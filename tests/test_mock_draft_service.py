"""Tests for services/mock_draft_service.py — pick sequence, bot picks, rankings."""
import pandas as pd
import pytest
from unittest.mock import patch


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
