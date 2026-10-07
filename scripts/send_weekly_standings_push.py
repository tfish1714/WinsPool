#!/usr/bin/env python3
"""Send each opted-in player one personal standings push after a week completes.

Runs as a non-required step of winspool-sync-daily (scripts/run_cron.py), after
daily_nfl_sync.py. Safe to rerun: an event record per (season, week) prevents
duplicate sends. Best-effort: never exits non-zero for a skip or a send failure.

--dry-run prints the messages and neither sends nor records. It does not call
require_db(), so it reads whatever data mode the environment selects
(USE_LOCAL_DATA=True reads .local_db pickles; no Firestore credentials needed).
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
log = logging.getLogger("send_weekly_standings_push")


def _require_db():
    # Forces USE_LOCAL_DATA=False before any db-using service import.
    from services.db_service import require_db
    return require_db()


def _load(season_arg=None):
    """Return (season, games, draft_results, players, draft_complete)."""
    from services.data_service import load_data, get_active_season
    from services.draft_state import draft_is_complete
    from services.utils import filter_season
    _st, _teams, all_games, players, draft_order, all_dr, rules = load_data()
    season = season_arg or get_active_season(all_games, all_dr, rules)
    games = filter_season(all_games, season)
    draft_results = filter_season(all_dr, season)
    complete = draft_is_complete(season, all_dr, draft_order, rules)
    return season, games, draft_results, players, complete


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-week", type=int)
    ap.add_argument("--season", type=int)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if not args.dry_run:
        _require_db()
    from services import push_service, push_events, standings_push_service as sp

    season, games, draft_results, players, draft_complete = _load(args.season)
    if not draft_complete:
        log.info("Draft for %s is not complete; skipping standings push.", season)
        return 0
    week = args.force_week or sp.latest_complete_week(games, season)
    if week is None:
        log.info("No complete week in %s; skipping standings push.", season)
        return 0
    event_id = f"{season}_w{week:02d}_standings"
    if not args.dry_run:
        if not args.force and push_events.push_event_exists(event_id):
            log.info("%s already sent; skipping (use --force to resend).", event_id)
            return 0
        if not push_service.is_configured():
            log.warning("Web push is not configured (VAPID); skipping standings push.")
            return 0

    current = sp.pool_ranking(sp.standings_as_of(games, season, week),
                              draft_results, players, season, games)
    previous = (sp.pool_ranking(sp.standings_as_of(games, season, week - 1),
                                draft_results, players, season, games)
                if week > 1 else None)
    messages = sp.build_messages(week, current, previous)

    if args.dry_run:
        for pid, (title, body) in sorted(messages.items()):
            print(f"{pid}: {title} - {body}")
        return 0

    try:
        result = push_service.send_to_subscribers(
            lambda pid: messages.get(pid), pref="standings", url=f"/wins-pool/{season}")
        push_events.record_push_event(event_id, "standings", result["counts"],
                                      result["messages"], {"season": season, "week": week})
    except Exception:
        log.exception("Weekly standings push failed for %s; not recording the event.", event_id)
        return 0
    log.info("Sent %s: %s", event_id, result["counts"])
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.exit(main())
