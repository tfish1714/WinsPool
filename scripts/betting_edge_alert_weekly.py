"""scripts/betting_edge_alert_weekly.py -- weekly betting-edge alert email.

Not its own Cloud Run Job: run as a plain subprocess step of
winspool-schedule-kickoffs, right after that job enqueues the week's kickoff
Cloud Tasks (see scripts/schedule_kickoffs.py::_run_betting_alert() and
docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md). In-season
gating and the weekly Tuesday cadence both come from that job's own Cloud
Scheduler trigger -- nothing in this script re-checks either.

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
        # Distinguish a genuinely quiet week (expected, fine) from a broken
        # pipeline (no prediction docs for this week, or no game has an
        # edge_vs_vegas value at all -- expected, silently, forever, with no
        # alerting otherwise). Cheap: just iterates the already-loaded
        # predictions_by_season[target_season] dict for this week's games,
        # same game_key parsing pattern as betting_screener_service.screen_games.
        week_preds = predictions_by_season.get(target_season, {})
        game_count = 0
        explanation_count = 0
        edge_count = 0
        for game_key, pred in week_preds.items():
            parts = game_key.split("_")
            if len(parts) != 3:
                continue
            wk_str, _ht, _at = parts
            try:
                wk = int(wk_str.lstrip("W"))
            except ValueError:
                continue
            if wk != target_week:
                continue
            game_count += 1
            ex = pred.get("explanation") or {}
            if ex:
                explanation_count += 1
            edge = ex.get("edge_vs_vegas")
            if edge is None:
                edge = pred.get("edge_vs_vegas")
            if edge is not None:
                edge_count += 1
        print(
            f"[betting_edge_alert] No edges found for {target_season} week {target_week} "
            f"({game_count} games, {explanation_count} with explanation, "
            f"{edge_count} with edge_vs_vegas). Skipping email."
        )
        return

    to_email = os.environ.get("BETTING_ALERT_EMAIL")
    if not to_email:
        print("[betting_edge_alert] BETTING_ALERT_EMAIL not set; skipping email.")
        return

    sent = send_betting_edge_email(to_email, summary)
    if not sent:
        # send_betting_edge_email (via email_service._send) returns False
        # rather than raising when RESEND_API_KEY is unset or the Resend API
        # call itself fails. Without raising here, _run_with_alerting() never
        # sees an exception, so a misconfigured env (missing secret, bad key)
        # would fail this job silently, every week, forever -- see Finding 5
        # of the 2026-09-15 final review.
        raise RuntimeError(
            f"send_betting_edge_email returned False for {target_season} week "
            f"{target_week} -- check RESEND_API_KEY/BETTING_ALERT_EMAIL config"
        )
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
