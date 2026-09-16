"""scripts/betting_edge_alert_weekly.py -- winspool-betting-alert Cloud Run
Job entrypoint. Runs weekly (Tuesdays, shortly after winspool-schedule-kickoffs'
10:00 UTC run -- see docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md),
in-season only (gated by the Cloud Scheduler trigger's own cron window, not
in-script -- matching every other scheduled job in this repo).

Personal alert only (project owner, one recipient via BETTING_ALERT_EMAIL) --
never player-facing. Read-only: composes the existing betting screener
(services.betting_screener_service) and pattern scanner
(services.pattern_scanner_service) via services.betting_edge_alert_service,
never touches the NN+XGB+LR ensemble, never writes anything.

No spam: if both tiers (validated angle matches, raw edge_vs_vegas outliers)
are empty for the week, this skips sending entirely and just logs -- a quiet
week is normal, not a failure.
"""
import os
import sys
import pathlib

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scripts.daily_nfl_sync import initialize_firebase
from services.data_service import load_data
from services.betting_screener_service import find_next_upcoming_week, load_predictions_by_season
from services.betting_edge_alert_service import build_week_summary
from services.email_service import send_betting_edge_email, send_alert_email


def main():
    initialize_firebase()

    _, _, all_games, _, _, _, _ = load_data()
    if all_games.empty:
        print("[betting_edge_alert] No schedule data available; nothing to do.")
        return

    target_season = int(all_games["season"].max())
    target_week = find_next_upcoming_week(all_games, target_season)
    if target_week is None:
        print(f"[betting_edge_alert] No upcoming week found for season {target_season}; nothing to do.")
        return

    predictions_by_season, _, _ = load_predictions_by_season(all_games)

    edge_threshold = float(os.environ.get("BETTING_EDGE_THRESHOLD", "3.0"))
    summary = build_week_summary(
        predictions_by_season, all_games,
        target_season=target_season, target_week=target_week,
        edge_threshold=edge_threshold,
    )

    if not summary["validated_angle_matches"] and not summary["raw_edge_outliers"]:
        print(f"[betting_edge_alert] No edges found for {target_season} week {target_week}. Skipping email.")
        return

    to_email = os.environ.get("BETTING_ALERT_EMAIL")
    if not to_email:
        print("[betting_edge_alert] BETTING_ALERT_EMAIL not set; skipping email.")
        return

    sent = send_betting_edge_email(to_email, summary)
    print(
        f"[betting_edge_alert] {target_season} week {target_week}: "
        f"{len(summary['validated_angle_matches'])} validated angle match(es), "
        f"{len(summary['raw_edge_outliers'])} raw edge outlier(s). Email sent: {sent}"
    )


def _run_with_alerting():
    try:
        main()
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-betting-alert' failed",
            f"betting_edge_alert_weekly.py raised an unhandled exception:\n\n{traceback.format_exc()}",
        )
        raise


if __name__ == "__main__":
    _run_with_alerting()
