"""scripts/schedule_kickoffs.py -- winspool-schedule-kickoffs Cloud Run Job entrypoint.

Runs weekly (Tue ~10am UTC), in-season only (Sept 1 - Feb 10). Reads the
upcoming week's actual gameday/gametime, computes distinct kickoff-time
clusters, and enqueues 2 Cloud Tasks per cluster:
  - winspool-sync-daily    at (kickoff - 75 min)
  - winspool-predict-daily at (kickoff - 60 min)

Cloud Tasks (not Cloud Scheduler) is used because it supports a specific
one-off future execution timestamp per task, whereas Cloud Scheduler is
built for recurring cron patterns. Each task's HTTP target hits the Cloud
Run Jobs Admin API's :run endpoint, authenticated via an OIDC token from a
service account with run.invoker on the target job.

See docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md.
"""
import os
import sys
import pathlib
from datetime import datetime, timedelta, timezone

os.environ["USE_LOCAL_DATA"] = "False"

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import load_games
from services.email_service import send_alert_email

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"

GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_REGION = os.environ.get("GCP_REGION", "us-east1")
GCP_TASKS_QUEUE = os.environ.get("GCP_TASKS_QUEUE", "winspool-kickoff-triggers")
GCP_SCHEDULER_SERVICE_ACCOUNT = os.environ.get("GCP_SCHEDULER_SERVICE_ACCOUNT")

SYNC_LEAD_MINUTES = 75
PREDICT_LEAD_MINUTES = 60

# NFL gametime is published in US/Eastern per nflverse convention.
_EASTERN = timezone(timedelta(hours=-4))  # EDT; DST-naive for the September-January season window


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
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2

    parent = tasks_client.queue_path(GCP_PROJECT, GCP_REGION, GCP_TASKS_QUEUE)
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(run_at.astimezone(timezone.utc))

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": _run_url(job_name),
            "oidc_token": {"service_account_email": GCP_SCHEDULER_SERVICE_ACCOUNT},
        },
        "schedule_time": ts,
    }
    tasks_client.create_task(request={"parent": parent, "task": task})


def main():
    from google.cloud import tasks_v2

    try:
        games = load_games()
        season, week = _current_season_week(games)
        clusters = compute_kickoff_clusters(games, season, week)

        client = tasks_v2.CloudTasksClient()
        for kickoff in clusters:
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")

        print(f"Enqueued {len(clusters)} kickoff cluster(s) x 2 tasks for {season} week {week}.")
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-schedule-kickoffs' failed",
            f"Dynamic kickoff scheduling failed -- this week falls back to the "
            f"fixed daily baseline only:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
