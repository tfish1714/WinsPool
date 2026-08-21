"""scripts/schedule_kickoffs.py -- winspool-schedule-kickoffs Cloud Run Job entrypoint.

Runs weekly (Tue ~10am UTC), in-season only (Sept 1 - Feb 10). Reads the
upcoming week's actual gameday/gametime, computes distinct kickoff-time
clusters, and enqueues 2 Cloud Tasks per cluster:
  - winspool-sync-daily    at (kickoff - 75 min)
  - winspool-predict-daily at (kickoff - 60 min)

Cloud Tasks (not Cloud Scheduler) is used because it supports a specific
one-off future execution timestamp per task, whereas Cloud Scheduler is
built for recurring cron patterns. Each task's HTTP target hits the Cloud
Run Jobs Admin API's :run endpoint -- a *.googleapis.com Google Cloud API,
not a Cloud Run Service's own HTTPS endpoint -- so it's authenticated via
an OAuth access token (oauth_token), not an OIDC identity token: OIDC is
for a target that validates the ID token itself (e.g. a Cloud Run Service
with IAM invoker checks); OAuth is for calling a Google API on the caller's
behalf, which is what :run is. This matches Task 9's own Cloud Scheduler
setup, which uses --oauth-service-account-email against the identical URL.
The service account still needs run.invoker on the target job either way.

See docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md.
"""
import os
import subprocess
import sys
import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ["USE_LOCAL_DATA"] = "False"

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import load_games
from services.email_service import send_alert_email

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
SCRIPTS_DIR = pathlib.Path(__file__).parent

GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_REGION = os.environ.get("GCP_REGION", "us-east1")
GCP_TASKS_QUEUE = os.environ.get("GCP_TASKS_QUEUE", "winspool-kickoff-triggers")
GCP_SCHEDULER_SERVICE_ACCOUNT = os.environ.get("GCP_SCHEDULER_SERVICE_ACCOUNT")

SYNC_LEAD_MINUTES = 75
PREDICT_LEAD_MINUTES = 60

# NFL gametime is published in US/Eastern per nflverse convention. Use a
# proper DST-aware zone -- clocks fall back to EST (UTC-5) the first Sunday
# of November, which is squarely inside this job's Sept 1 - Feb 10 window
# (Thanksgiving primetime, most of the playoffs, the Super Bowl).
_EASTERN = ZoneInfo("America/New_York")


def compute_kickoff_clusters(games: pd.DataFrame, season: int, week: int) -> list[datetime]:
    """Distinct REG-season kickoff datetimes for (season, week), deduplicated."""
    wk = games[
        (games["season"] == season)
        & (games["week"] == week)
        & (games["game_type"] == "REG")
    ]
    seen: set[str] = set()
    clusters: list[datetime] = []
    for _, row in wk.iterrows():
        key = f"{row['gameday']} {row['gametime']}"
        if key in seen:
            continue
        seen.add(key)
        dt = datetime.strptime(key, "%Y-%m-%d %H:%M").replace(tzinfo=_EASTERN)
        clusters.append(dt)
    return sorted(clusters)


def _current_season_week(games: pd.DataFrame) -> tuple[int, int]:
    """The next upcoming REG-season week: the (season, week) of the earliest
    not-yet-played REG game (result is null). Deliberately NOT
    services.data_service.get_active_season()/get_latest_week_for_year() --
    those find the latest *completed* week (for the standings page,
    retrospective), which is the opposite of what pre-kickoff scheduling
    needs (the next upcoming week, prospective)."""
    upcoming = games[
        (games["game_type"] == "REG") & games["result"].isna()
    ].sort_values(["season", "week"])
    if upcoming.empty:
        raise ValueError("No upcoming REG games found — season may be over")
    row = upcoming.iloc[0]
    return int(row["season"]), int(row["week"])


def _run_url(job_name: str) -> str:
    return (
        f"https://{GCP_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
        f"namespaces/{GCP_PROJECT}/jobs/{job_name}:run"
    )


def enqueue_task(tasks_client, run_at: datetime, job_name: str) -> None:
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2

    parent = tasks_client.queue_path(GCP_PROJECT, GCP_REGION, GCP_TASKS_QUEUE)
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(run_at.astimezone(timezone.utc))

    # Deterministic task name so Cloud Tasks' built-in dedup applies -- a
    # Cloud Scheduler retry of the weekly winspool-schedule-kickoffs job (or
    # a manual re-run) would otherwise enqueue duplicate tasks for the same
    # kickoff cluster. A named CreateTask does NOT silently no-op on a
    # duplicate name -- it raises AlreadyExists, which we must catch here:
    # letting it propagate would abort the whole enqueue loop mid-week (the
    # remaining clusters never get scheduled) and fire a false failure
    # alert on every ordinary retry -- worse than having no dedup at all.
    task_id = f"{job_name}-{run_at.strftime('%Y%m%dT%H%M')}"
    task_name = (
        f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/"
        f"queues/{GCP_TASKS_QUEUE}/tasks/{task_id}"
    )

    task = {
        "name": task_name,
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": _run_url(job_name),
            # :run is a Google Cloud API endpoint (*.googleapis.com), not a
            # Cloud Run Service's own HTTPS endpoint -- OAuth, not OIDC. See
            # the module docstring.
            "oauth_token": {"service_account_email": GCP_SCHEDULER_SERVICE_ACCOUNT},
        },
        "schedule_time": ts,
    }
    try:
        tasks_client.create_task(request={"parent": parent, "task": task})
    except AlreadyExists:
        print(f"[skip] task already enqueued: {task_id}")


def _sync_schedule_data() -> None:
    """Re-pull rawdata/schedules/games.csv before reading it. In the actual
    Cloud Run Job container (ephemeral, no baked-in rawdata), games.csv won't
    exist otherwise, so load_games() would sys.exit(1) on every single run --
    zero Cloud Tasks ever get enqueued, every week. Mirrors
    sync_live_scores.py::sync_authoritative()'s subprocess pattern: non-fatal
    on failure -- a fresh container has no stale-but-present games.csv to
    fall back on anyway, so any failure here is worth surfacing (via
    load_games()'s own sys.exit(1) if the file genuinely isn't there
    afterward) but shouldn't be treated differently than that established
    pattern."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py"), "--priority", "1"],
        capture_output=True, text=True, timeout=120,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"[warn] sync_nflverse_data.py --priority 1 exited non-zero (non-fatal): "
              f"{result.stderr.strip()[:500]}")


def main():
    try:
        from google.cloud import tasks_v2

        _sync_schedule_data()
        games = load_games()
        season, week = _current_season_week(games)
        clusters = compute_kickoff_clusters(games, season, week)

        client = tasks_v2.CloudTasksClient()
        for kickoff in clusters:
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")

        print(f"Enqueued {len(clusters)} kickoff cluster(s) x 2 tasks for {season} week {week}.")
    except (Exception, SystemExit):
        # `except Exception` alone would let `load_games()`'s `sys.exit(1)`
        # (raised as SystemExit, which does not subclass Exception) escape
        # uncaught and bypass the alert email below.
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-schedule-kickoffs' failed",
            f"Dynamic kickoff scheduling failed -- this week falls back to the "
            f"fixed daily baseline only:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
