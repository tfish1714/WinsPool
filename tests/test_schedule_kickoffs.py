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
