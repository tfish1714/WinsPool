"""scripts/sync_live_scores.py -- winspool-live-scores Cloud Run Job entrypoint.

Scheduled every few minutes, in-season (Sept 1 - Feb 10) only, but fast-exits
(before any subprocess, Firestore, or ESPN work) unless the current ET time
is inside an NFL game window -- see is_live_score_window_active(). The only
I/O outside the window is one small schedule read (local rawdata copy, else a
single nflverse GET). `--force` skips the check. Two parts:

1. Authoritative (must not fail silently -- this is what actually moves
   player win totals): re-pull rawdata/ from nflverse at priority 1, which
   is `schedules` (games.csv) AND `stats_team` (stats_team_reg_{year}.csv +
   stats_team_week_{year}.csv) -- NOT schedules alone. The stats_team files
   routinely 404 preseason / on transient nflverse CDN blips, so a non-zero
   exit from that subprocess is logged as a warning, not raised -- games.csv
   is written independently of whether stats_team succeeds, so it's still
   very likely fresh. We then re-run the same compute_standings()
   daily_nfl_sync.py uses, filtered to the current + prior season only (the
   5-minute cadence makes a full historical rewrite needlessly expensive),
   and push nfl_standings via the same batch_upload() (full overwrite, same
   semantics as the daily sync). The nfl_games push is narrowed further, to
   just the last ~7 days by gameday -- standings need the full current
   season to be correct, but only games that could plausibly be live or
   recently finished need to be rewritten every 5 minutes. Intentionally
   does not depend on ESPN.

2. Best-effort (cosmetic only, must never affect wins or crash part 1):
   fetch ESPN's live scoreboard and merge-write only is_live/clock/period/
   live_home_score/live_away_score onto nfl_games documents that are NOT yet
   final per nflverse's own data -- live_home_score/live_away_score are
   deliberately separate fields from home_score/away_score (which come from
   nflverse and drive win totals), so an in-game score shown on the schedule
   page can never itself affect standings. This is the "don't clobber a
   final score" guard the old
   sync_live_scores_to_df() docstring claimed but never actually
   implemented. Wrapped so any ESPN failure is silent-safe. ESPN's team
   abbreviations are normalized to nflverse's before matching (see
   services/utils.py::normalize_team_abbr) since e.g. ESPN's "LAR"/"WSH"/
   "JAC" differ from nflverse's "LA"/"WAS"/"JAX".

See docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md.
"""
import argparse
import io
import subprocess
import sys
import pathlib
import os
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd
import requests

from scripts.daily_nfl_sync import (
    compute_standings, batch_upload, initialize_firebase, load_games, RAWDATA_DIR,
)
from services.live_score_service import get_live_updates, is_live_status
from services.email_service import send_alert_email
from services.utils import normalize_team_abbr

SCRIPTS_DIR = pathlib.Path(__file__).parent

ET = ZoneInfo("America/New_York")
WINDOW_LEAD = timedelta(minutes=20)         # open this long before the earliest kickoff
WINDOW_FINAL_TAIL = timedelta(minutes=30)   # close after the last kickoff once every game is final
WINDOW_LIVE_TAIL = timedelta(hours=4, minutes=30)  # 3h game + 1.5h overtime/delay buffer
CARRYOVER = timedelta(hours=5)              # yesterday's kickoffs still in play past midnight


SCHEDULE_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"


def _load_schedule_for_window() -> pd.DataFrame:
    """Schedule for the window check. Prefers the local rawdata copy; the Cloud
    Run Job container has none at start (rawdata/ is gitignored and not baked
    into the image -- the authoritative step below is what downloads it), so
    fall back to one small nflverse GET rather than a full sync."""
    local = RAWDATA_DIR / "schedules" / "games.csv"
    if local.exists():
        return pd.read_csv(local, low_memory=False)
    resp = requests.get(SCHEDULE_URL, timeout=15)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text), low_memory=False)


def is_live_score_window_active(now_et=None, games=None) -> bool:
    """True when `now_et` (default: now, America/New_York) is inside an NFL game
    window, so the every-few-minutes Cloud Run Job can fast-exit the rest of
    the day.

    Candidate games are today's (ET) plus yesterday's whose kickoff is within
    the last 5 hours (a late game still running past midnight). The window
    opens 20 minutes before the earliest candidate kickoff and closes 30
    minutes after the latest kickoff once every candidate has a result, else
    4.5 hours after it. Fails open (True) on any error, so a broken schedule
    read can never suppress live updates."""
    try:
        now = now_et or datetime.now(ET)
        if games is None:
            games = _load_schedule_for_window()
        df = games.dropna(subset=["gameday", "gametime"]).copy()
        if df.empty:
            return False
        parsed = pd.to_datetime(
            df["gameday"].astype(str) + " " + df["gametime"].astype(str), errors="coerce"
        )
        df["kickoff"] = [
            k.to_pydatetime().replace(tzinfo=ET) if pd.notna(k) else None for k in parsed
        ]
        df = df[df["kickoff"].notna()]
        today = now.date()
        keep = [
            k.date() == today
            or (k.date() == today - timedelta(days=1) and now - k <= CARRYOVER)
            for k in df["kickoff"]
        ]
        df = df[keep]
        if df.empty:
            return False
        start = min(df["kickoff"]) - WINDOW_LEAD
        all_final = bool(df["result"].notna().all())
        end = max(df["kickoff"]) + (WINDOW_FINAL_TAIL if all_final else WINDOW_LIVE_TAIL)
        return start <= now <= end
    except Exception as e:
        print(f"[warn] live-score window check failed, failing open: {e}")
        return True


def sync_authoritative(db) -> pd.DataFrame:
    """Part 1: re-pull rawdata, recompute standings for the active season,
    push nfl_games + nfl_standings, diffing against what's already stored.

    Scoped to the active season only -- compute_standings() groups by
    (season, team) independently, with no cross-season dependency, so
    there's no correctness reason to also rewrite the prior season every
    5 minutes (matches daily_nfl_sync.py's Task 8 change; the previous
    current + prior season scoping here was only ever a cadence-cost
    concession, not a data-correctness requirement)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py"), "--priority", "1"],
        capture_output=True, text=True, timeout=120,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        # Non-fatal: priority 1 also includes stats_team, which routinely
        # fails to download preseason or on transient CDN blips. games.csv
        # is written independently, so it's very likely still fresh even
        # when this subprocess's aggregate exit code is non-zero -- matches
        # run_cron.py's "nflverse Raw Data Sync" step, which treats the
        # equivalent failure as required: False for the same reason.
        print(f"[warn] sync_nflverse_data.py --priority 1 exited non-zero (non-fatal): "
              f"{result.stderr.strip()[:500]}")

    games = load_games()
    current_season = games["season"].max()
    active_season_games = games[games["season"] == current_season].copy()

    standings = compute_standings(active_season_games)
    standings_written = batch_upload(db, "nfl_standings", standings, diff_before_write=True)

    # Narrow the nfl_games push to the trailing ~7 days by gameday -- these
    # are the only games that could plausibly be live or have just finished.
    gameday = pd.to_datetime(active_season_games["gameday"], errors="coerce")
    today = pd.Timestamp.now().normalize()
    window = active_season_games[(gameday >= today - pd.Timedelta(days=7)) & (gameday <= today)].copy()

    games_written = batch_upload(db, "nfl_games", window, diff_before_write=True)
    return window


def overlay_espn_live_fields(db, games: pd.DataFrame, live_data: dict) -> int:
    """Merge-write is_live/clock/period onto not-yet-final nfl_games docs
    that ESPN reports on. Returns count of docs written."""
    if not live_data:
        return 0

    written = 0
    for _, row in games.iterrows():
        key = (row["home_team"], row["away_team"])
        update = live_data.get(key)
        if update is None:
            continue

        # Guard: nflverse's own data (result notna) already says this game is
        # final -- don't let ESPN's cosmetic fields touch it.
        if pd.notna(row.get("result")):
            continue

        espn_status = update["status"]
        db.collection("nfl_games").document(str(row["game_id"])).set(
            {
                "is_live": is_live_status(espn_status),
                "clock": "Halftime" if espn_status == "STATUS_HALFTIME" else update.get("clock"),
                "period": update.get("period"),
                "possession": update.get("possession"),
                # Display-only in-game score, kept separate from home_score/away_score
                # (which come from nflverse and drive win totals) -- see module docstring
                # part 2: ESPN data must never affect wins, only cosmetics.
                "live_home_score": update.get("home_score"),
                "live_away_score": update.get("away_score"),
            },
            merge=True,
        )
        written += 1

    return written


def run_espn_overlay_safely(db, games: pd.DataFrame) -> int:
    """Wraps the ESPN fetch + overlay so any failure here never propagates --
    this step is cosmetic-only, per the spec's must-not-affect-wins rule."""
    try:
        live_data = get_live_updates()
        # ESPN returns raw abbreviations (LAR/WSH/JAC) that differ from
        # nflverse's (LA/WAS/JAX) -- normalize before matching against
        # nflverse-normalized home_team/away_team keys, or those games
        # silently never match (see services/utils.py::normalize_team_abbr).
        normalized_live_data = {
            (normalize_team_abbr(h), normalize_team_abbr(a)): v
            for (h, a), v in live_data.items()
        }
        return overlay_espn_live_fields(db, games, normalized_live_data)
    except Exception as e:
        print(f"[warn] ESPN live overlay failed (non-fatal): {e}")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="Run even outside the active NFL game window")
    args = parser.parse_args(argv)

    # Fast exit before any subprocess, network, or Firestore work: the job is
    # scheduled around the clock, but NFL games only run a few hours a week.
    if not args.force and not is_live_score_window_active():
        print("Outside active NFL game window. Exiting immediately.")
        sys.exit(0)

    try:
        # initialize_firebase() itself calls sys.exit(1) (SystemExit) when no
        # credentials are configured at all -- kept inside this try/except so
        # that failure reaches the alert handler below instead of escaping
        # uncaught, same class of bug as sync_authoritative()'s SystemExit
        # case (see comment below).
        db = initialize_firebase()
        games = sync_authoritative(db)
    except (Exception, SystemExit):
        # SystemExit is caught too: load_games() (called from
        # sync_authoritative()) calls sys.exit(1) directly when
        # rawdata/schedules/games.csv is genuinely missing (e.g. the
        # schedules download itself failed, not just the benign stats_team
        # 404 case above), and SystemExit does not subclass Exception -- an
        # `except Exception` alone would let that specific failure escape
        # this alert handler silently. traceback.format_exc() still works
        # correctly for a caught SystemExit (it reads sys.exc_info(), not
        # exception type).
        send_alert_email(
            "WinsPool job 'winspool-live-scores' failed",
            f"Authoritative sync failed:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)

    written = run_espn_overlay_safely(db, games)

    # Signal DOMAIN_ACTIVE so the app's caches pick up the fresh
    # standings/games + ESPN overlay fields just written, matching
    # daily_nfl_sync.py's pattern -- but unconditionally, unlike that
    # script's written-count gate: the ESPN overlay step above always
    # writes is_live/clock/period on live games via its own separate
    # merge=True .set() calls, which sync_authoritative()'s own
    # standings/games write counts don't capture, so gating this signal on
    # those counts alone would under-signal during an actual live game.
    # Deliberately placed *after* the overlay step, not right after
    # sync_authoritative() -- a page request landing in that earlier window
    # would rebuild the cache from nfl_games docs that were just fully
    # overwritten without is_live/clock/period, systematically caching away
    # the LIVE badge for a full 5-minute cycle during actual game traffic.
    from services.cache_service import DOMAIN_ACTIVE
    from services.db_service import signal_data_update
    signal_data_update(DOMAIN_ACTIVE)

    print(f"Live sync complete. ESPN overlay wrote {written} game(s).")


if __name__ == "__main__":
    main()
