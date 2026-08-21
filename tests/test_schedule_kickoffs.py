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
