"""scripts/schedule_kickoffs.py -- winspool-schedule-kickoffs Cloud Run Job entrypoint.

Runs weekly (Tue ~10am UTC), in-season only (Sept 1 - Feb 10). Reads the
upcoming week's actual gameday/gametime, computes distinct kickoff-time
clusters, and enqueues 3 Cloud Tasks per cluster:
  - winspool-sync-daily    at (kickoff - 75 min)
  - winspool-predict-daily at (kickoff - 60 min)
  - winspool-predict-daily at (kickoff - 20 min), with its container args
    overridden to `--resimulate <game_ids>` (Task 6) -- a scoped ESPN
    injury check + re-simulate for just that cluster's games, reusing the
    existing winspool-predict-daily job/image rather than a new one. Runs
    AFTER the routine predict run above so it is the last word before
    kickoff, not overwritten by it.

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

After enqueuing this week's kickoff tasks, also runs the weekly betting-edge
alert email (scripts/betting_edge_alert_weekly.py) as a plain subprocess step
-- see _run_betting_alert() below for why this piggybacks here instead of
getting its own Cloud Run Job + Cloud Scheduler triggers.

See docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md and
docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md.
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
# How close to kickoff the ESPN check + re-simulate runs. Must fire AFTER the
# routine PREDICT_LEAD_MINUTES=60 predict run, not before it -- the whole
# point of this task is to be the last, freshest word before kickoff, using
# a narrower/later ESPN check than the routine run could. If this fired
# earlier than -60min (e.g. anchored to the -90min inactive-list deadline
# instead), the routine predict run would simply overwrite its published
# predictions 10 minutes later with no ESPN overrides, defeating the whole
# point.
#
# This is NOT a cheap operation, despite --resimulate sounding like a narrow
# per-game repredict. Real cost drivers, in order incurred:
#   1. Cold Cloud Run Job container start.
#   2. A full rawdata resync (_sync_rawdata(), up to a 300s subprocess
#      timeout) -- the enqueued Cloud Task's containerOverrides.args
#      REPLACES the job's configured args entirely, so --skip-sync is never
#      passed here (unlike a local/manual invocation).
#   3. engine.initialize(year), which runs build_master_feature_table() across
#      SIX seasons (min_season=2020..year-1) plus compute_roster_value() --
#      simulate_season() being scoped to one season does NOT make
#      initialize() itself cheap; it runs before simulate_season() either way.
#   4. The Monte Carlo simulate_season() call itself (RESIMULATE_N_SIMS).
# Measured (docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md,
# Stage 3 finding 2, and independently reconfirmed during planning): a real
# invocation of engine.initialize() takes roughly 440-640s depending on
# environment, and build_master_feature_table() alone is ~96% of that --
# compute_qb_availability_flags()/compute_roster_value() are single-digit
# seconds each. That's up to ~53% of the original 20-minute budget BEFORE any
# Cloud Run cold-start penalty (commonly 30-90s+ for a TensorFlow-loading
# image). 30 keeps real margin against that measured range while staying
# comfortably less than PREDICT_LEAD_MINUTES (60), so this step still fires
# after the routine predict run, not before it.
RESIMULATE_LEAD_MINUTES = 30

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


def compute_kickoff_clusters_with_games(games: pd.DataFrame, season: int, week: int) -> list[tuple[datetime, list]]:
    """Same clustering as compute_kickoff_clusters(), but paired with each
    cluster's game_ids so the caller knows what to pass to --resimulate."""
    wk = games[
        (games["season"] == season)
        & (games["week"] == week)
        & (games["game_type"] == "REG")
    ]
    clusters: dict = {}
    for _, row in wk.iterrows():
        key_dt = datetime.strptime(
            f"{row['gameday']} {row['gametime']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=_EASTERN)
        clusters.setdefault(key_dt, []).append(row["game_id"])
    return sorted(clusters.items())


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


def enqueue_task(tasks_client, run_at: datetime, job_name: str, job_args: list = None) -> None:
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2
    import json

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
    if job_args:
        # Disambiguate from the routine (no-args) task at the same
        # job_name/timestamp -- both the routine predict task and the
        # resimulate task target job_name="winspool-predict-daily", so two
        # DIFFERENT kickoff clusters exactly 40 minutes apart could
        # otherwise collide (cluster A's routine-predict slot at
        # kickoff_A-60 landing on the same minute as cluster B's resimulate
        # slot at kickoff_B-20, when kickoff_B = kickoff_A + 40min). Without
        # this, the AlreadyExists handler below would silently treat the
        # second as a duplicate and skip it.
        task_id += "-resim"
    task_name = (
        f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/"
        f"queues/{GCP_TASKS_QUEUE}/tasks/{task_id}"
    )

    http_request = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": _run_url(job_name),
        # :run is a Google Cloud API endpoint (*.googleapis.com), not a
        # Cloud Run Service's own HTTPS endpoint -- OAuth, not OIDC. See
        # the module docstring.
        "oauth_token": {"service_account_email": GCP_SCHEDULER_SERVICE_ACCOUNT},
    }
    if job_args:
        # Cloud Run Jobs Admin API's :run RunJobRequest body -- overrides the
        # container's configured args for just this execution, so the
        # re-simulate can reuse winspool-predict-daily's existing job instead
        # of provisioning a new one.
        body = {"overrides": {"containerOverrides": [{"args": job_args}]}}
        http_request["body"] = json.dumps(body).encode("utf-8")

    task = {
        "name": task_name,
        "http_request": http_request,
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


def _run_betting_alert() -> None:
    """Run the weekly betting-edge alert email as a step of this same job,
    right after this week's kickoff Cloud Tasks are enqueued.

    Piggybacks on winspool-schedule-kickoffs (Tuesdays ~10:00 UTC) rather
    than getting its own Cloud Run Job + Cloud Scheduler triggers: both jobs
    already run on the same Dockerfile.sync image (the screener/scanner
    behind the alert -- services.betting_screener_service /
    services.pattern_scanner_service -- are read-only and never touch the
    NN+XGB+LR ensemble, so there's no ML-dependency mismatch to avoid), and
    by the time this job runs each Tuesday the week's schedule is confirmed
    and that week's Vegas lines are already posted, so there's no need for
    a separate, independently-tuned offset/delay either. See
    docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md.

    Wholly non-fatal, by design: a screener bug must never fail this job's
    actual purpose (enqueuing the week's kickoff Cloud Tasks), which is why
    this is called after that enqueue loop already succeeded, not folded
    into the same try block. betting_edge_alert_weekly.py has its own
    _run_with_alerting() wrapper that emails
    '[WinsPool Alert] winspool-betting-alert failed' on an unhandled
    exception -- this wrapper only needs to keep that subprocess call
    (timeout, unexpected crash) from propagating into main()'s own
    except (Exception, SystemExit), which would otherwise misreport a
    betting-alert problem as "Dynamic kickoff scheduling failed."
    """
    print("[schedule_kickoffs] Running weekly betting-edge alert...")
    cmd = [sys.executable, str(SCRIPTS_DIR / "betting_edge_alert_weekly.py")]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            cwd=str(SCRIPTS_DIR.parent),
        )
    except subprocess.TimeoutExpired:
        print("[warn] betting_edge_alert_weekly.py timed out after 300s "
              "(non-fatal) -- this week's kickoff tasks are already enqueued")
        return
    except Exception as e:
        print(f"[warn] betting_edge_alert_weekly.py could not be run "
              f"(non-fatal): {e}")
        return
    stdout_tail = (result.stdout or "").strip().splitlines()[-20:]
    print("[schedule_kickoffs] betting-edge alert summary:")
    for line in stdout_tail:
        print(f"  {line}")
    if result.returncode != 0:
        print(f"[warn] betting_edge_alert_weekly.py exited non-zero (non-fatal, "
              f"already self-alerted): {(result.stderr or '').strip()[:500]}")


def _run_quarter_scores_scrape(season: int, week: int) -> None:
    """Scrape the just-finished week's quarter-by-quarter scores for the
    weekly recap's comeback-win detection, as an independent step.

    `week` here is this job's own *upcoming* week (from
    _current_season_week()); quarter scores only exist for games that have
    already been played, i.e. week - 1. Skipped entirely when that's < 1
    (nothing has completed yet this season).

    Wholly non-fatal, mirroring _run_betting_alert(): this scrapes a single
    third-party site (jt-sw.com) with no fallback, so a flaky/blocked scrape
    is expected occasionally. It only affects recap flavor text (not core
    standings/predictions) and must never cause this job to report failure
    for -- or skip -- the kickoff-task enqueuing that's its actual purpose.
    See docs/superpowers/specs/2026-09-15-comeback-win-recap-design.md, Design §4.
    """
    prior_week = week - 1
    if prior_week < 1:
        print(f"[schedule_kickoffs] Season {season} week {week}: no prior week yet, "
              f"skipping quarter-score scrape.")
        return

    print(f"[schedule_kickoffs] Scraping quarter scores for {season} week {prior_week}...")
    cmd = [
        sys.executable, str(SCRIPTS_DIR / "scrape_quarter_scores.py"),
        "--season", str(season), "--week", str(prior_week), "--firestore",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            cwd=str(SCRIPTS_DIR.parent),
        )
    except subprocess.TimeoutExpired:
        print("[warn] scrape_quarter_scores.py timed out after 120s (non-fatal)")
        return
    except Exception as e:
        print(f"[warn] scrape_quarter_scores.py could not be run (non-fatal): {e}")
        return
    if result.returncode != 0:
        print(f"[warn] scrape_quarter_scores.py exited non-zero (non-fatal): "
              f"{(result.stderr or '').strip()[:500]}")


def main():
    try:
        from google.cloud import tasks_v2

        _sync_schedule_data()
        games = load_games()
        season, week = _current_season_week(games)

        client = tasks_v2.CloudTasksClient()
        clusters_with_games = compute_kickoff_clusters_with_games(games, season, week)
        for kickoff, game_ids in clusters_with_games:
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")
            enqueue_task(
                client, kickoff - timedelta(minutes=RESIMULATE_LEAD_MINUTES), "winspool-predict-daily",
                job_args=["--resimulate", ",".join(str(g) for g in game_ids)],
            )

        print(f"Enqueued {len(clusters_with_games)} kickoff cluster(s) x 3 tasks for {season} week {week}.")

        _run_quarter_scores_scrape(season, week)
        _run_betting_alert()
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
