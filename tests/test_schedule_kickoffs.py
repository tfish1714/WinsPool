import pandas as pd
from datetime import datetime, timezone
from scripts.schedule_kickoffs import compute_kickoff_clusters


def _week_games():
    return pd.DataFrame([
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-17", "gametime": "20:15"},  # Thu night
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "13:00"},  # Sun early
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "13:00"},  # Sun early, same cluster
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "16:25"},  # Sun late
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "20:20"},  # Sun night
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-21", "gametime": "20:15"},  # Mon night
    ])


class TestComputeKickoffClusters:
    def test_dedupes_same_day_same_time_games(self):
        clusters = compute_kickoff_clusters(_week_games(), season=2026, week=2)
        assert len(clusters) == 5  # Thu, Sun-early (deduped), Sun-late, Sun-night, Mon

    def test_filters_to_requested_season_and_week(self):
        games = pd.concat([
            _week_games(),
            pd.DataFrame([{"season": 2025, "week": 2, "game_type": "REG",
                            "gameday": "2025-09-18", "gametime": "20:15"}]),
        ], ignore_index=True)
        clusters = compute_kickoff_clusters(games, season=2026, week=2)
        assert all(c.year == 2026 for c in clusters)

    def test_ignores_non_reg_games(self):
        games = pd.concat([
            _week_games(),
            pd.DataFrame([{"season": 2026, "week": 2, "game_type": "POST",
                            "gameday": "2026-09-22", "gametime": "20:15"}]),
        ], ignore_index=True)
        clusters = compute_kickoff_clusters(games, season=2026, week=2)
        assert len(clusters) == 5  # POST game not counted

    def test_returns_timezone_aware_datetimes(self):
        clusters = compute_kickoff_clusters(_week_games(), season=2026, week=2)
        assert all(c.tzinfo is not None for c in clusters)


class TestCurrentSeasonWeek:
    def test_returns_earliest_upcoming_reg_game(self):
        from scripts.schedule_kickoffs import _current_season_week
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "result": 3.0},   # played
            {"season": 2026, "week": 2, "game_type": "REG", "result": None},  # upcoming
            {"season": 2026, "week": 3, "game_type": "REG", "result": None},  # further out
        ])
        assert _current_season_week(games) == (2026, 2)

    def test_ignores_non_reg_games(self):
        from scripts.schedule_kickoffs import _current_season_week
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "POST", "result": None},  # earlier but not REG
            {"season": 2026, "week": 2, "game_type": "REG", "result": None},
        ])
        assert _current_season_week(games) == (2026, 2)

    def test_raises_when_season_is_over(self):
        from scripts.schedule_kickoffs import _current_season_week
        import pytest
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "result": 3.0},
        ])
        with pytest.raises(ValueError):
            _current_season_week(games)


class TestEnqueueTask:
    """enqueue_task's target (:run) is a *.googleapis.com Google Cloud API
    endpoint, not a Cloud Run Service's own HTTPS endpoint -- it must use
    oauth_token (not oidc_token), matching Task 9's own Cloud Scheduler
    --oauth-service-account-email usage against the identical URL. And a
    named CreateTask raises AlreadyExists on a duplicate rather than
    silently deduping -- that must be caught, or a routine Cloud Scheduler
    retry aborts the whole week's enqueue loop and fires a false alert."""

    def _kickoff(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 9, 20, 13, 0, tzinfo=ZoneInfo("America/New_York"))

    def test_uses_oauth_token_not_oidc_token(self, monkeypatch):
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"

        enqueue_task(client, self._kickoff(), "winspool-sync-daily")

        client.create_task.assert_called_once()
        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "oauth_token" in task["http_request"]
        assert "oidc_token" not in task["http_request"]
        assert task["http_request"]["oauth_token"]["service_account_email"] == "sa@test.iam.gserviceaccount.com"

    def test_already_exists_is_caught_not_raised(self, monkeypatch, capsys):
        from unittest.mock import MagicMock
        from google.api_core.exceptions import AlreadyExists
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        client.create_task.side_effect = AlreadyExists("duplicate task")

        # Must not raise -- a Scheduler retry re-enqueuing the same cluster
        # must be able to continue on to the *next* cluster, not abort here.
        enqueue_task(client, self._kickoff(), "winspool-sync-daily")

        assert "already enqueued" in capsys.readouterr().out

    def test_other_errors_still_propagate(self, monkeypatch):
        from unittest.mock import MagicMock
        import pytest
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        client.create_task.side_effect = RuntimeError("network error")

        # Only AlreadyExists should be swallowed -- a genuine failure must
        # still surface (main()'s except (Exception, SystemExit) turns it
        # into an alert email).
        with pytest.raises(RuntimeError):
            enqueue_task(client, self._kickoff(), "winspool-sync-daily")


class TestEnqueueTaskWithOverrides:
    def _kickoff(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 9, 20, 13, 0, tzinfo=ZoneInfo("America/New_York"))

    def test_job_args_produce_container_override_body(self, monkeypatch):
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"

        enqueue_task(client, self._kickoff(), "winspool-predict-daily",
                      job_args=["--resimulate", "2026_03_KC_WAS"])

        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "body" in task["http_request"]
        import json
        body = json.loads(task["http_request"]["body"])
        assert body["overrides"]["containerOverrides"][0]["args"] == ["--resimulate", "2026_03_KC_WAS"]

    def test_no_job_args_omits_body(self, monkeypatch):
        """Existing sync/predict calls (no job_args) must be unaffected -- no
        body means the job runs with its normal configured command."""
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"

        enqueue_task(client, self._kickoff(), "winspool-sync-daily")

        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "body" not in task["http_request"]

    def test_job_args_produce_distinct_task_name_from_routine_task(self, monkeypatch):
        """Both the routine predict task and the resimulate task target
        job_name="winspool-predict-daily" -- two different kickoff clusters
        exactly 40 minutes apart could otherwise land on the same
        job_name/timestamp task_id and collide (AlreadyExists silently
        skipping the second one). job_args must disambiguate the name."""
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        kickoff = self._kickoff()

        client_routine = MagicMock()
        client_routine.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        enqueue_task(client_routine, kickoff, "winspool-predict-daily")
        routine_task = client_routine.create_task.call_args.kwargs["request"]["task"]

        client_resim = MagicMock()
        client_resim.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        enqueue_task(client_resim, kickoff, "winspool-predict-daily",
                     job_args=["--resimulate", "2026_03_KC_WAS"])
        resim_task = client_resim.create_task.call_args.kwargs["request"]["task"]

        assert routine_task["name"] != resim_task["name"]


class TestRunBettingAlert:
    """_run_betting_alert() piggybacks the weekly betting-edge alert on this
    job instead of a separate Cloud Run Job -- see
    docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md. It must
    never be able to fail this job's actual purpose (enqueuing the week's
    kickoff Cloud Tasks)."""

    def test_invokes_betting_edge_alert_weekly_script(self):
        from unittest.mock import MagicMock, patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            sk._run_betting_alert()
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        assert "betting_edge_alert_weekly.py" in called_args[-1]

    def test_timeout_is_non_fatal(self):
        from unittest.mock import patch
        import subprocess as sp
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run",
                          side_effect=sp.TimeoutExpired(cmd="betting_edge_alert_weekly", timeout=300)):
            sk._run_betting_alert()  # must not raise

    def test_unexpected_subprocess_error_is_non_fatal(self):
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run", side_effect=OSError("no interpreter")):
            sk._run_betting_alert()  # must not raise

    def test_nonzero_returncode_is_non_fatal(self):
        """The inner script already sends its own '[WinsPool Alert]
        winspool-betting-alert failed' email via its own _run_with_alerting()
        -- this wrapper just needs to not propagate the failure itself."""
        from unittest.mock import MagicMock, patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            sk._run_betting_alert()  # must not raise

    def test_main_runs_betting_alert_after_successful_enqueue(self, monkeypatch):
        """A screener bug must never fail the kickoff-task enqueue this job
        actually exists for -- _run_betting_alert() must be called only
        after that enqueue loop has already succeeded, not folded into the
        same try block that would misreport it as a kickoff-scheduling
        failure."""
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        order = []
        monkeypatch.setattr(sk, "_sync_schedule_data", lambda: None)
        monkeypatch.setattr(sk, "load_games", lambda: pd.DataFrame())
        monkeypatch.setattr(sk, "_current_season_week", lambda games: (2026, 3))
        monkeypatch.setattr(sk, "compute_kickoff_clusters_with_games", lambda games, s, w: [])
        monkeypatch.setattr(sk, "enqueue_live_ticks", lambda client, games, s, w: 0)
        monkeypatch.setattr(
            sk, "_run_quarter_scores_scrape",
            lambda season, week: order.append("quarter_scores"),
        )
        monkeypatch.setattr(
            sk, "_run_betting_alert",
            lambda: order.append("betting_alert"),
        )
        with patch("google.cloud.tasks_v2.CloudTasksClient"):
            sk.main()
        assert order == ["quarter_scores", "betting_alert"]

    def test_betting_alert_failure_does_not_trigger_kickoff_failure_alert(self, monkeypatch):
        """Even if _run_betting_alert() somehow raised, main()'s except
        block would misreport it as 'Dynamic kickoff scheduling failed' --
        assert the real _run_betting_alert() (not a mock) can't do that,
        since it must swallow everything internally."""
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        monkeypatch.setattr(sk, "_sync_schedule_data", lambda: None)
        monkeypatch.setattr(sk, "load_games", lambda: pd.DataFrame())
        monkeypatch.setattr(sk, "_current_season_week", lambda games: (2026, 3))
        monkeypatch.setattr(sk, "compute_kickoff_clusters_with_games", lambda games, s, w: [])
        monkeypatch.setattr(sk, "enqueue_live_ticks", lambda client, games, s, w: 0)
        with patch.object(sk.subprocess, "run", side_effect=OSError("no interpreter")), \
             patch("google.cloud.tasks_v2.CloudTasksClient"), \
             patch.object(sk, "send_alert_email") as mock_alert:
            sk.main()
        mock_alert.assert_not_called()


class TestRunQuarterScoresScrape:
    """_run_quarter_scores_scrape() scrapes the just-finished week's quarter
    scores for the weekly recap's comeback-win detection -- see
    docs/superpowers/specs/2026-09-15-comeback-win-recap-design.md, Design §4.
    Must never be able to fail this job's actual purpose (enqueuing the
    week's kickoff Cloud Tasks)."""

    def test_skips_when_no_prior_week(self):
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run") as mock_run:
            sk._run_quarter_scores_scrape(2026, 1)  # week 1 -> prior_week 0
        mock_run.assert_not_called()

    def test_invokes_scraper_for_prior_week(self):
        from unittest.mock import MagicMock, patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            sk._run_quarter_scores_scrape(2026, 3)
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        assert "scrape_quarter_scores.py" in called_args[1]
        assert "--season" in called_args and "2026" in called_args
        assert "--week" in called_args and "2" in called_args
        assert "--firestore" in called_args

    def test_timeout_is_non_fatal(self):
        from unittest.mock import patch
        import subprocess as sp
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run",
                          side_effect=sp.TimeoutExpired(cmd="scrape_quarter_scores", timeout=120)):
            sk._run_quarter_scores_scrape(2026, 3)  # must not raise

    def test_unexpected_subprocess_error_is_non_fatal(self):
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run", side_effect=OSError("no interpreter")):
            sk._run_quarter_scores_scrape(2026, 3)  # must not raise

    def test_nonzero_returncode_is_non_fatal(self):
        from unittest.mock import MagicMock, patch
        import scripts.schedule_kickoffs as sk
        with patch.object(sk.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="blocked by site")
            sk._run_quarter_scores_scrape(2026, 3)  # must not raise

    def test_main_runs_quarter_scores_scrape_after_successful_enqueue(self, monkeypatch):
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        order = []
        monkeypatch.setattr(sk, "_sync_schedule_data", lambda: None)
        monkeypatch.setattr(sk, "load_games", lambda: pd.DataFrame())
        monkeypatch.setattr(sk, "_current_season_week", lambda games: (2026, 3))
        monkeypatch.setattr(sk, "compute_kickoff_clusters_with_games", lambda games, s, w: [])
        monkeypatch.setattr(sk, "enqueue_live_ticks", lambda client, games, s, w: 0)
        monkeypatch.setattr(
            sk, "_run_quarter_scores_scrape",
            lambda season, week: order.append(("quarter_scores", season, week)),
        )
        monkeypatch.setattr(sk, "_run_betting_alert", lambda: order.append("betting_alert"))
        with patch("google.cloud.tasks_v2.CloudTasksClient"):
            sk.main()
        assert order == [("quarter_scores", 2026, 3), "betting_alert"]

    def test_quarter_scores_failure_does_not_trigger_kickoff_failure_alert(self, monkeypatch):
        from unittest.mock import patch
        import scripts.schedule_kickoffs as sk
        monkeypatch.setattr(sk, "_sync_schedule_data", lambda: None)
        monkeypatch.setattr(sk, "load_games", lambda: pd.DataFrame())
        monkeypatch.setattr(sk, "_current_season_week", lambda games: (2026, 3))
        monkeypatch.setattr(sk, "compute_kickoff_clusters_with_games", lambda games, s, w: [])
        monkeypatch.setattr(sk, "enqueue_live_ticks", lambda client, games, s, w: 0)
        with patch.object(sk.subprocess, "run", side_effect=OSError("no interpreter")), \
             patch("google.cloud.tasks_v2.CloudTasksClient"), \
             patch.object(sk, "send_alert_email") as mock_alert:
            sk.main()
        mock_alert.assert_not_called()


class TestComputeKickoffClustersWithGames:
    def test_pairs_each_cluster_with_its_game_ids(self):
        from scripts.schedule_kickoffs import compute_kickoff_clusters_with_games
        games = pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g1",
             "gameday": "2026-09-20", "gametime": "13:00"},
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g2",
             "gameday": "2026-09-20", "gametime": "13:00"},
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g3",
             "gameday": "2026-09-20", "gametime": "16:25"},
        ])
        result = compute_kickoff_clusters_with_games(games, 2026, 3)
        assert len(result) == 2
        early = next(g for dt, g in result if dt.hour == 13)
        late = next(g for dt, g in result if dt.hour == 16)
        assert sorted(early) == ["g1", "g2"]
        assert late == ["g3"]


def test_resimulate_job_args_include_the_script_path():
    """Regression: Cloud Run Jobs' containerOverrides.args REPLACES the
    job's entire configured args list (it does not append), and
    winspool-predict-daily's configured command/args is `python
    scripts/cache_builder.py`. main() used to build job_args as just
    ["--resimulate", "<game_ids>"], which drops the script path entirely --
    the container then runs literally `python --resimulate <game_ids>`,
    which Python rejects as an unknown interpreter option (exit code 2).
    This was masked for weeks by an unrelated IAM permission error that
    made the same task fail earlier (PERMISSION_DENIED), and only surfaced
    once that was fixed and the task actually reached the container.
    resimulate_job_args() must always prefix the real script path."""
    from scripts.schedule_kickoffs import resimulate_job_args
    args = resimulate_job_args(["2026_03_KC_WAS", "2026_03_LAC_BUF"])
    assert args[0] == "scripts/cache_builder.py"
    assert args[1] == "--resimulate"
    assert args[2] == "2026_03_KC_WAS,2026_03_LAC_BUF"


def test_resimulate_lead_minutes_fires_after_routine_predict():
    """RESIMULATE_LEAD_MINUTES must stay strictly less than PREDICT_LEAD_MINUTES
    (smaller lead = closer to kickoff = fires later in absolute time), or the
    resimulate step could run before the routine predict step and get
    overwritten by it -- see the ordering comment in schedule_kickoffs.py.
    Also pins the specific value chosen after profiling engine.initialize()."""
    from scripts.schedule_kickoffs import RESIMULATE_LEAD_MINUTES, PREDICT_LEAD_MINUTES
    assert RESIMULATE_LEAD_MINUTES == 30
    assert RESIMULATE_LEAD_MINUTES < PREDICT_LEAD_MINUTES


class TestComputeLiveWindows:
    def _game(self, day, time, week=2, season=2026, game_type="REG"):
        return {"season": season, "week": week, "game_type": game_type,
                "gameday": day, "gametime": time}

    def test_single_game_window_is_lead_to_tail_in_utc(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-17", "20:15")])  # Thu 20:15 EDT = 00:15Z Fri
        windows = compute_live_windows(games, 2026, 2)
        assert windows == [(
            datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 18, 4, 15, tzinfo=timezone.utc),
        )]

    def test_week_merges_overlapping_sunday_games_but_keeps_thu_and_mon_separate(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([
            self._game("2026-09-17", "20:15"),   # Thu night
            self._game("2026-09-20", "13:00"),   # Sun early
            self._game("2026-09-20", "13:00"),   # same cluster
            self._game("2026-09-20", "16:25"),   # Sun late
            self._game("2026-09-20", "20:20"),   # Sun night
            self._game("2026-09-21", "20:15"),   # Mon night
        ])
        windows = compute_live_windows(games, 2026, 2)
        assert windows == [
            (datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc), datetime(2026, 9, 18, 4, 15, tzinfo=timezone.utc)),
            (datetime(2026, 9, 20, 16, 55, tzinfo=timezone.utc), datetime(2026, 9, 21, 4, 20, tzinfo=timezone.utc)),
            (datetime(2026, 9, 22, 0, 10, tzinfo=timezone.utc), datetime(2026, 9, 22, 4, 15, tzinfo=timezone.utc)),
        ]

    def test_windows_that_exactly_touch_are_merged(self):
        from scripts.schedule_kickoffs import compute_live_windows
        # Kickoff A ends at A+4h; kickoff B starts at B-5min. B = A+4h+5min makes them touch.
        games = pd.DataFrame([self._game("2026-09-20", "13:00"), self._game("2026-09-20", "17:05")])
        assert len(compute_live_windows(games, 2026, 2)) == 1

    def test_clock_change_night_uses_utc_arithmetic(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        # Sat 2026-10-31 22:00 EDT = 02:00Z 11-01; +4h = 06:00Z. Wall-clock ET math
        # would cross the 2 AM fall-back and be off by an hour.
        games = pd.DataFrame([self._game("2026-10-31", "22:00", week=9)])
        windows = compute_live_windows(games, 2026, 9)
        assert windows == [(
            datetime(2026, 11, 1, 1, 55, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
        )]

    def test_week_with_no_reg_games_returns_empty(self):
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-20", "13:00")])
        assert compute_live_windows(games, 2026, 3) == []

    def test_ignores_non_reg_games(self):
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-20", "13:00", game_type="POST")])
        assert compute_live_windows(games, 2026, 2) == []


class TestLiveTicks:
    def _week_windows(self):
        from scripts.schedule_kickoffs import compute_live_windows
        rows = [("2026-09-17", "20:15"), ("2026-09-20", "13:00"), ("2026-09-20", "16:25"),
                ("2026-09-20", "20:20"), ("2026-09-21", "20:15")]
        games = pd.DataFrame([
            {"season": 2026, "week": 2, "game_type": "REG", "gameday": d, "gametime": t}
            for d, t in rows
        ])
        return compute_live_windows(games, 2026, 2)

    def test_full_week_tick_count_and_bounds(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        ticks = live_ticks(self._week_windows(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        # Thu 00:10-04:10 = 25, Sun 17:00-04:20 = 69, Mon 00:10-04:10 = 25
        assert len(ticks) == 119
        assert ticks[0] == datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc)
        assert ticks[-1] == datetime(2026, 9, 22, 4, 10, tzinfo=timezone.utc)
        assert ticks == sorted(ticks)
        assert len(set(ticks)) == len(ticks)

    def test_ticks_fall_on_interval_marks(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks, LIVE_TICK_MINUTES
        ticks = live_ticks(self._week_windows(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert all(t.minute % LIVE_TICK_MINUTES == 0 and t.second == 0 for t in ticks)

    def test_first_tick_is_first_mark_at_or_after_window_start(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        # Window 16:55-21:00Z: first mark at/after 16:55 is 17:00.
        window = [(datetime(2026, 9, 20, 16, 55, tzinfo=timezone.utc),
                   datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc))]
        ticks = live_ticks(window, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert ticks[0] == datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
        assert ticks[-1] == datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)  # end is inclusive

    def test_skips_ticks_not_strictly_after_now(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        now = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)  # mid Sunday window
        ticks = live_ticks(self._week_windows(), now=now)
        assert all(t > now for t in ticks)
        assert datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc) not in ticks
        assert len(ticks) == 57

    def test_no_windows_means_no_ticks(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        assert live_ticks([], now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == []


class TestEnqueueLiveTicks:
    def _games(self):
        rows = [("2026-09-20", "13:00"), ("2026-09-20", "16:25")]
        return pd.DataFrame([
            {"season": 2026, "week": 2, "game_type": "REG", "gameday": d, "gametime": t}
            for d, t in rows
        ])

    def _gcp_env(self, monkeypatch):
        import scripts.schedule_kickoffs as sk
        monkeypatch.setattr(sk, "GCP_PROJECT", "test-project")
        monkeypatch.setattr(sk, "GCP_REGION", "us-east1")
        monkeypatch.setattr(sk, "GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr(sk, "GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

    def test_enqueues_one_task_per_tick_for_live_scores_job(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        calls = []
        monkeypatch.setattr(sk, "enqueue_task",
                            lambda client, run_at, job_name, job_args=None: calls.append((run_at, job_name, job_args)))
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        count = sk.enqueue_live_ticks(MagicMock(), self._games(), 2026, 2, now=now)

        assert count == len(calls) > 0
        assert all(job == "winspool-live-scores" and args is None for _, job, args in calls)
        assert [c[0] for c in calls] == sorted(c[0] for c in calls)

    def test_mid_week_run_enqueues_no_past_ticks(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        calls = []
        monkeypatch.setattr(sk, "enqueue_task",
                            lambda client, run_at, job_name, job_args=None: calls.append(run_at))
        now = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)  # Sunday, mid-window

        sk.enqueue_live_ticks(MagicMock(), self._games(), 2026, 2, now=now)

        assert calls and all(t > now for t in calls)

    def test_no_games_enqueues_nothing(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        monkeypatch.setattr(sk, "enqueue_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not enqueue")))
        count = sk.enqueue_live_ticks(MagicMock(), self._games(), 2026, 3,
                                      now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert count == 0

    def test_rerun_hits_already_exists_without_raising(self, monkeypatch, capsys):
        """Same week enqueued twice (Scheduler retry / manual rerun): the real
        enqueue_task swallows AlreadyExists, so the helper must complete."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        from google.api_core.exceptions import AlreadyExists
        import scripts.schedule_kickoffs as sk

        self._gcp_env(monkeypatch)
        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        client.create_task.side_effect = AlreadyExists("duplicate task")

        count = sk.enqueue_live_ticks(client, self._games(), 2026, 2,
                                      now=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert count > 0
        assert "already enqueued" in capsys.readouterr().out

    def test_task_ids_are_deterministic_per_tick(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        self._gcp_env(monkeypatch)
        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        sk.enqueue_live_ticks(client, self._games(), 2026, 2, now=now)
        first_names = [c.kwargs["request"]["task"]["name"] for c in client.create_task.call_args_list]
        client.create_task.reset_mock()
        sk.enqueue_live_ticks(client, self._games(), 2026, 2, now=now)
        second_names = [c.kwargs["request"]["task"]["name"] for c in client.create_task.call_args_list]

        assert first_names == second_names
        assert first_names[0].endswith("/tasks/winspool-live-scores-20260920T1700")
        assert len(set(first_names)) == len(first_names)


def test_main_enqueues_live_ticks_before_the_non_fatal_steps(monkeypatch):
    from unittest.mock import patch
    import scripts.schedule_kickoffs as sk
    order = []
    monkeypatch.setattr(sk, "_sync_schedule_data", lambda: None)
    monkeypatch.setattr(sk, "load_games", lambda: pd.DataFrame())
    monkeypatch.setattr(sk, "_current_season_week", lambda games: (2026, 3))
    monkeypatch.setattr(sk, "compute_kickoff_clusters_with_games", lambda games, s, w: [])
    monkeypatch.setattr(sk, "enqueue_live_ticks",
                        lambda client, games, s, w: order.append(("live_ticks", s, w)) or 7)
    monkeypatch.setattr(sk, "_run_quarter_scores_scrape", lambda season, week: order.append("quarter_scores"))
    monkeypatch.setattr(sk, "_run_betting_alert", lambda: order.append("betting_alert"))
    with patch("google.cloud.tasks_v2.CloudTasksClient"):
        sk.main()
    assert order == [("live_ticks", 2026, 3), "quarter_scores", "betting_alert"]
