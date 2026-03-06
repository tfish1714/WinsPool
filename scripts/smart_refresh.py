"""
scripts/smart_refresh.py — Differential heartbeat refresh for WinsPool.

Monitors in-progress NFL games and triggers cache_builder.py only
when actual game results change since the last check.

Strategy:
  1. Load current week's game schedule from local .pkl.
  2. During active game windows (computed from gametime + 3.5h),
     poll every 15 minutes.
  3. For each expected-completed game_id, fetch ONLY those docs
     from Firestore and compare `result` field against a local snapshot.
  4. If any result changed → run cache_builder.py.
  5. Mid-week (no games expected) → sleep until next game window.
  6. Off-season → sleep until the first preseason game window.

Usage:
    python scripts/smart_refresh.py

Runs as a background always-on process. Safe to restart anytime.
"""
import os
import sys
import time
import pathlib
import subprocess
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")
POLL_INTERVAL_SECONDS = 15 * 60   # 15 minutes
GAME_WINDOW_BUFFER = timedelta(hours=3, minutes=30)
CACHE_BUILDER = pathlib.Path(__file__).parent / "cache_builder.py"

# Local snapshot: { game_id: result_value }
_result_snapshot: dict = {}


def _load_games() -> pd.DataFrame:
    """Load nfl_games from local PKL (no Firestore read)."""
    pkl = pathlib.Path(".local_db") / "nfl_games.pkl"
    if not pkl.exists():
        return pd.DataFrame()
    try:
        df = pd.read_pickle(pkl)
        if "game_type" in df.columns:
            df = df[df["game_type"] == "REG"]
        return df
    except Exception as e:
        log.error(f"Could not read nfl_games.pkl: {e}")
        return pd.DataFrame()


def _games_ending_before(games: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Return games whose expected end time (gametime + 3.5h) is before `as_of`."""
    if games.empty or "gameday" not in games.columns:
        return pd.DataFrame()

    rows = []
    for _, row in games.iterrows():
        gd = str(row.get("gameday", ""))
        gt = str(row.get("gametime", "16:00"))  # default 4pm if missing
        try:
            start = ET.localize(datetime.strptime(f"{gd} {gt}", "%Y-%m-%d %H:%M"))
            end = start + GAME_WINDOW_BUFFER
            if end <= as_of:
                rows.append(row)
        except Exception:
            pass
    return pd.DataFrame(rows)


def _next_game_window(games: pd.DataFrame, after: datetime) -> datetime | None:
    """Return the earliest future game-start time, or None if no games remain."""
    if games.empty:
        return None

    times = []
    for _, row in games.iterrows():
        gd = str(row.get("gameday", ""))
        gt = str(row.get("gametime", "16:00"))
        try:
            start = ET.localize(datetime.strptime(f"{gd} {gt}", "%Y-%m-%d %H:%M"))
            if start > after:
                times.append(start)
        except Exception:
            pass
    return min(times) if times else None


def _fetch_results_from_firestore(game_ids: list) -> dict:
    """Fetch { game_id: result } for specific game IDs from Firestore."""
    os.environ["USE_LOCAL_DATA"] = "False"
    from services.db_service import get_db

    db = get_db()
    results = {}
    col = db.collection("nfl_games")
    for gid in game_ids:
        try:
            doc = col.document(str(gid)).get()
            if doc.exists:
                results[gid] = doc.to_dict().get("result")
        except Exception as e:
            log.warning(f"Could not fetch game_id={gid}: {e}")
    return results


def _run_cache_builder():
    log.info("📊 Changes detected — running cache_builder.py ...")
    try:
        result = subprocess.run(
            [sys.executable, str(CACHE_BUILDER)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            log.info("✅ cache_builder.py completed successfully.")
        else:
            log.error(f"❌ cache_builder.py returned {result.returncode}:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        log.error("❌ cache_builder.py timed out after 5 minutes.")
    except Exception as e:
        log.error(f"❌ Failed to run cache_builder.py: {e}")


def run_check(games: pd.DataFrame):
    """Perform one differential heartbeat check."""
    now_et = datetime.now(tz=ET)
    completed = _games_ending_before(games, now_et)

    if completed.empty:
        log.debug("No games expected to have ended yet — skipping Firestore check.")
        return

    game_ids = completed["game_id"].dropna().tolist() if "game_id" in completed.columns else []
    if not game_ids:
        return

    log.info(f"Checking {len(game_ids)} expected-complete game(s) for result changes...")
    current_results = _fetch_results_from_firestore(game_ids)

    changed = any(
        current_results.get(gid) != _result_snapshot.get(gid)
        for gid in game_ids
    )

    if changed:
        _result_snapshot.update(current_results)
        _run_cache_builder()
    else:
        log.info("No result changes — nothing to rebuild.")


def main():
    log.info("🏈 WinsPool Smart Refresh started.")

    games = _load_games()
    if games.empty:
        log.error("No game data found in .local_db/nfl_games.pkl. Run refresh_local_pkls.py first.")
        sys.exit(1)

    # Filter to current season
    current_season = int(games["season"].max())
    games = games[games["season"] == current_season].copy()
    log.info(f"Loaded {len(games)} regular season games for {current_season}.")

    while True:
        now_et = datetime.now(tz=ET)
        next_window = _next_game_window(games, now_et)

        if next_window is None:
            log.info("No future games found. Sleeping for 24 hours then re-checking.")
            time.sleep(86400)
            games = _load_games()
            games = games[games["season"] == int(games["season"].max())].copy()
            continue

        # Sleep until just before the next game window if we're in dead time
        # (i.e., more than POLL_INTERVAL ahead of next game)
        time_to_next = (next_window - now_et).total_seconds()
        if time_to_next > POLL_INTERVAL_SECONDS:
            sleep_until = next_window - timedelta(minutes=15)
            seconds_to_sleep = max(0, (sleep_until - now_et).total_seconds())
            log.info(f"Next game window: {next_window.strftime('%a %b %d %I:%M%p ET')}. "
                     f"Sleeping {int(seconds_to_sleep / 3600)}h {int((seconds_to_sleep % 3600) / 60)}m.")
            time.sleep(seconds_to_sleep)
            continue

        # We're in an active game window — run the heartbeat check
        run_check(games)
        log.info(f"Next check in {POLL_INTERVAL_SECONDS // 60} minutes.")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
