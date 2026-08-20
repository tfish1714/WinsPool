"""scripts/sync_live_scores.py -- winspool-live-scores Cloud Run Job entrypoint.

Runs every 5 minutes, in-season (Sept 1 - Feb 10) only. Two parts:

1. Authoritative (must not fail silently -- this is what actually moves
   player win totals): re-pull rawdata/schedules/games.csv from nflverse
   (schedules only, priority 1 -- lightweight single file), re-run the same
   compute_standings() daily_nfl_sync.py uses, push nfl_standings + nfl_games
   via the same batch_upload() (full overwrite, same semantics as the daily
   sync). Intentionally does not depend on ESPN.

2. Best-effort (cosmetic only, must never affect wins or crash part 1):
   fetch ESPN's live scoreboard and merge-write only is_live/clock/period
   onto nfl_games documents that are NOT yet final per nflverse's own data --
   this is the "don't clobber a final score" guard the old
   sync_live_scores_to_df() docstring claimed but never actually
   implemented. Wrapped so any ESPN failure is silent-safe.

See docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md.
"""
import subprocess
import sys
import pathlib
import os
import traceback

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import compute_standings, batch_upload, initialize_firebase, load_games
from services.live_score_service import get_live_updates
from services.email_service import send_alert_email

SCRIPTS_DIR = pathlib.Path(__file__).parent


def sync_authoritative(db) -> pd.DataFrame:
    """Part 1: re-pull schedules, recompute standings, push nfl_games + nfl_standings.
    Returns the freshly-loaded games DataFrame for part 2 to reuse."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py"), "--priority", "1"],
        capture_output=True, text=True, timeout=120,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        raise RuntimeError(f"sync_nflverse_data.py --priority 1 failed: {result.stderr.strip()[:500]}")

    games = load_games()
    standings = compute_standings(games)
    batch_upload(db, "nfl_standings", standings)
    batch_upload(db, "nfl_games", games)
    return games


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

        db.collection("nfl_games").document(str(row["game_id"])).set(
            {
                "is_live": update["status"] == "STATUS_IN_PROGRESS",
                "clock": update.get("clock"),
                "period": update.get("period"),
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
        return overlay_espn_live_fields(db, games, live_data)
    except Exception as e:
        print(f"[warn] ESPN live overlay failed (non-fatal): {e}")
        return 0


def main():
    db = initialize_firebase()
    try:
        games = sync_authoritative(db)
    except Exception:
        send_alert_email(
            "WinsPool job 'winspool-live-scores' failed",
            f"Authoritative sync failed:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)

    written = run_espn_overlay_safely(db, games)
    print(f"Live sync complete. ESPN overlay wrote {written} game(s).")


if __name__ == "__main__":
    main()
