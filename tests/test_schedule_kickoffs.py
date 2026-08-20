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
