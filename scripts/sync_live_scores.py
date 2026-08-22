"""scripts/sync_live_scores.py -- winspool-live-scores Cloud Run Job entrypoint.

Runs every 5 minutes, in-season (Sept 1 - Feb 10) only. Two parts:

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
   fetch ESPN's live scoreboard and merge-write only is_live/clock/period
   onto nfl_games documents that are NOT yet final per nflverse's own data --
   this is the "don't clobber a final score" guard the old
   sync_live_scores_to_df() docstring claimed but never actually
   implemented. Wrapped so any ESPN failure is silent-safe. ESPN's team
   abbreviations are normalized to nflverse's before matching (see
   services/utils.py::normalize_team_abbr) since e.g. ESPN's "LAR"/"WSH"/
   "JAC" differ from nflverse's "LA"/"WAS"/"JAX".

See docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md.
"""
import subprocess
import sys
import pathlib
import os
import time
import traceback

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import compute_standings, batch_upload, initialize_firebase, load_games
from services.live_score_service import get_live_updates, is_live_status
from services.email_service import send_alert_email
from services.utils import normalize_team_abbr

SCRIPTS_DIR = pathlib.Path(__file__).parent


def sync_authoritative(db) -> pd.DataFrame:
    """Part 1: re-pull rawdata, recompute standings for the current + prior
    season, push nfl_games + nfl_standings. Returns the freshly-loaded,
    season-filtered games DataFrame for part 2 to reuse."""
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
    recent = games[games["season"] >= current_season - 1].copy()

    standings = compute_standings(recent)
    batch_upload(db, "nfl_standings", standings)

    # Narrow the nfl_games push (but NOT the standings input above) to games
    # with a gameday in roughly the last week -- these are the only games
    # that could plausibly be live or have just finished. Pushing all of
    # `recent` (current + prior season, ~620 docs) every 5 minutes measured
    # at ~179k Firestore writes/day, ~9x the free tier. compute_standings()
    # still needs the full current season to produce correct win/loss
    # totals, so only the nfl_games push is narrowed here.
    gameday = pd.to_datetime(recent["gameday"], errors="coerce")
    today = pd.Timestamp.now().normalize()
    window = recent[(gameday >= today - pd.Timedelta(days=7)) & (gameday <= today)].copy()

    batch_upload(db, "nfl_games", window)
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


def main():
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

    # Signal cache invalidation so the app's caches pick up the fresh
    # standings/games + ESPN overlay fields just written, matching
    # daily_nfl_sync.py's pattern. Deliberately placed *after* the overlay
    # step, not right after sync_authoritative() -- a page request landing
    # in that earlier window would rebuild the cache from nfl_games docs
    # that were just fully overwritten without is_live/clock/period (those
    # are added by the overlay step above), systematically caching away the
    # LIVE badge for a full 5-minute cycle during actual game traffic.
    db.collection("metadata").document("cache_control").set({"last_update": time.time()})

    print(f"Live sync complete. ESPN overlay wrote {written} game(s).")


if __name__ == "__main__":
    main()
