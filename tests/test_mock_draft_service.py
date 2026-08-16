"""Tests for services/mock_draft_service.py — pick sequence, bot picks, rankings."""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch


class TestGetPickSequence:

    def test_returns_30_entries_sorted_by_pick(self):
        from services.mock_draft_service import get_pick_sequence
        rules_df = pd.DataFrame([
            {"season": 2025, "draftOrder": 1, "pickOne": 1, "pickTwo": 20, "pickThree": 26},
            {"season": 2025, "draftOrder": 2, "pickOne": 2, "pickTwo": 19, "pickThree": 27},
            {"season": 2026, "draftOrder": 1, "pickOne": 3, "pickTwo": 18, "pickThree": 28},
        ])
        with patch("services.mock_draft_service.get_collection_df", return_value=rules_df):
            seq = get_pick_sequence()
        # Uses the most recent season present (2026) — only 1 slot there, 3 picks.
        assert len(seq) == 3
        assert [e["pick"] for e in seq] == sorted(e["pick"] for e in seq)
        assert all(e["slot"] == 1 for e in seq)

    def test_uses_most_recent_season_with_rules(self):
        from services.mock_draft_service import get_pick_sequence
        rules_df = pd.DataFrame([
            {"season": 2024, "draftOrder": 1, "pickOne": 99, "pickTwo": 98, "pickThree": 97},
            {"season": 2025, "draftOrder": 1, "pickOne": 1, "pickTwo": 20, "pickThree": 26},
        ])
        with patch("services.mock_draft_service.get_collection_df", return_value=rules_df):
            seq = get_pick_sequence()
        assert {e["pick"] for e in seq} == {1, 20, 26}

    def test_raises_value_error_when_no_rules_configured(self):
        from services.mock_draft_service import get_pick_sequence
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            with pytest.raises(ValueError):
                get_pick_sequence()


class TestGetProjectionSeason:

    def test_returns_max_season_in_draft_order(self):
        from services.mock_draft_service import get_projection_season
        order_df = pd.DataFrame([{"season": 2024, "playerId": 1}, {"season": 2026, "playerId": 2}])
        with patch("services.mock_draft_service.get_collection_df", return_value=order_df):
            assert get_projection_season() == 2026

    def test_raises_value_error_when_no_draft_order_configured(self):
        from services.mock_draft_service import get_projection_season
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            with pytest.raises(ValueError):
                get_projection_season()


class TestNflTeams:

    def test_has_32_unique_teams(self):
        from services.mock_draft_service import NFL_TEAMS
        assert len(NFL_TEAMS) == 32
        assert len(set(NFL_TEAMS)) == 32


class TestBotPick:

    def test_returns_team_from_available_teams(self):
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            for _ in range(50):
                team, _ = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=5, bot_picks_remaining=10)
                assert team in ["KC", "DAL"]

    def test_falls_back_to_uniform_random_when_no_projections(self):
        from services.mock_draft_service import bot_pick
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={}):
            team, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=5, bot_picks_remaining=10)
        assert team in ["KC", "DAL"]
        assert was_wildcard is False

    def test_forces_wildcard_when_shortfall_equals_remaining_picks(self):
        """wildcardsSoFar=0, botPicksRemaining=1 with MIN=2 -> needed(2) >= remaining(1) -> forced."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=0, bot_picks_remaining=1)
        assert was_wildcard is True

    def test_forces_wildcard_at_exact_boundary(self):
        """wildcardsSoFar=1, botPicksRemaining=1 -> needed(1) >= remaining(1) -> forced."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=1, bot_picks_remaining=1)
        assert was_wildcard is True

    def test_does_not_force_wildcard_once_minimum_already_met(self):
        """wildcardsSoFar=2 (minimum already hit) -> needed=0 -> never forced; disable the random roll to prove it."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections), \
             patch("services.mock_draft_service.random.random", return_value=0.99):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=2, bot_picks_remaining=1)
        assert was_wildcard is False

    def test_full_draft_simulation_hits_minimum_wildcards(self):
        """Simulate 27 bot picks (a full mock draft's bot slots) many times; every run has >= 2 wildcards."""
        from services.mock_draft_service import bot_pick, MIN_WILDCARDS_PER_DRAFT
        projections = {t: {"projected_wins": 32 - i} for i, t in enumerate(
            ["KC", "DAL", "SF", "BUF", "PHI", "BAL", "DET", "MIA", "GB", "LA",
             "CIN", "HOU", "MIN", "NYJ", "LAC", "PIT", "SEA", "TB", "IND", "DEN",
             "NO", "ATL", "CHI", "ARI", "WAS", "CLE", "NYG", "TEN", "JAX", "CAR",
             "NE", "LV"]
        )}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            for _ in range(20):  # repeat to cover the probabilistic (non-forced) path too
                available = list(projections.keys())
                wildcards_so_far = 0
                total_bot_picks = 27
                for i in range(total_bot_picks):
                    remaining = total_bot_picks - i
                    team, was_wildcard = bot_pick(2026, available, wildcards_so_far, remaining)
                    available.remove(team)
                    if was_wildcard:
                        wildcards_so_far += 1
                assert wildcards_so_far >= MIN_WILDCARDS_PER_DRAFT


class TestRankRosters:

    def test_ranks_highest_total_first(self):
        from services.mock_draft_service import rank_rosters
        projections = {
            "KC": {"projected_wins": 11.0}, "DAL": {"projected_wins": 9.0},
            "NE": {"projected_wins": 4.0}, "LV": {"projected_wins": 3.0},
        }
        rosters = {"1": ["KC", "DAL"], "2": ["NE", "LV"]}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            result = rank_rosters(2026, rosters)
        by_slot = {r["slot"]: r for r in result}
        assert by_slot[1]["rank"] == 1
        assert by_slot[1]["totalProjectedWins"] == 20.0
        assert by_slot[2]["rank"] == 2
        assert by_slot[2]["totalProjectedWins"] == 7.0
        assert by_slot[1]["graded"] is True
        assert by_slot[2]["graded"] is True

    def test_missing_projection_counts_as_zero(self):
        from services.mock_draft_service import rank_rosters
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={}):
            result = rank_rosters(2026, {"1": ["KC", "DAL"]})
        assert result[0]["totalProjectedWins"] == 0.0
        assert result[0]["rank"] == 1

    def test_empty_projections_marks_all_entries_ungraded(self):
        """When a season has zero projection data at all, every roster totals 0.0 and
        rank_rosters must not present the resulting order as a meaningful ranking —
        mirrors bot_pick()'s honest fallback to uniform-random in the same situation."""
        from services.mock_draft_service import rank_rosters
        rosters = {"1": ["KC", "DAL"], "2": ["NE", "LV"], "3": ["SF", "BUF"]}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={}):
            result = rank_rosters(2026, rosters)
        assert len(result) == 3
        assert all(r["graded"] is False for r in result)
        assert all(r["totalProjectedWins"] == 0.0 for r in result)
        # Ranks are still assigned (response shape unchanged) even though they're not meaningful.
        assert {r["rank"] for r in result} == {1, 2, 3}

    def test_nonempty_projections_marks_all_entries_graded(self):
        from services.mock_draft_service import rank_rosters
        projections = {"KC": {"projected_wins": 11.0}, "DAL": {"projected_wins": 9.0}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            result = rank_rosters(2026, {"1": ["KC"], "2": ["DAL"]})
        assert all(r["graded"] is True for r in result)

    def test_result_length_matches_roster_count(self):
        from services.mock_draft_service import rank_rosters
        rosters = {str(i): ["KC"] for i in range(1, 11)}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={"KC": {"projected_wins": 5.0}}):
            result = rank_rosters(2026, rosters)
        assert len(result) == 10
        assert {r["rank"] for r in result} == set(range(1, 11))


class TestGetTeamSchedules:

    def test_returns_schedule_list_per_team(self):
        from services.mock_draft_service import get_team_schedules, NFL_TEAMS
        games_df = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "DAL"},
            {"season": 2026, "week": 2, "home_team": "SF", "away_team": "KC"},
        ])
        fake_bundle = MagicMock(games=games_df)
        with patch("services.mock_draft_service.load_data_season", return_value=fake_bundle):
            result = get_team_schedules(2026)
        assert set(result.keys()) == set(NFL_TEAMS)
        assert result["KC"] == ["Wk1 vs DAL", "Wk2 @ SF"]
        assert result["DAL"] == ["Wk1 @ KC"]
        assert result["ARI"] == []  # team with no games in the fixture

    def test_empty_games_returns_empty_schedules_for_every_team(self):
        from services.mock_draft_service import get_team_schedules, NFL_TEAMS
        fake_bundle = MagicMock(games=pd.DataFrame())
        with patch("services.mock_draft_service.load_data_season", return_value=fake_bundle):
            result = get_team_schedules(2026)
        assert set(result.keys()) == set(NFL_TEAMS)
        assert all(v == [] for v in result.values())
