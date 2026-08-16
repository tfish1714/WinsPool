"""tests/test_mock_draft.py — /api/mock-draft/* route tests."""
import pandas as pd
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app

client = TestClient(app)

RULES_DF = pd.DataFrame([
    {"season": 2026, "draftOrder": i, "pickOne": i, "pickTwo": 21 - i, "pickThree": 20 + i}
    for i in range(1, 11)
])
ORDER_DF = pd.DataFrame([{"season": 2026, "playerId": i, "draftOrder": i} for i in range(1, 11)])
PROJECTIONS = {"KC": {"projected_wins": 11.2, "std_dev": 1.1}, "DAL": {"projected_wins": 9.0, "std_dev": 1.4}}


def _mock_collection_df(name, filters=None):
    if name == "draft_order_rules":
        return RULES_DF
    if name == "draft_order":
        return ORDER_DF
    return pd.DataFrame()


class TestMockDraftSetup:

    def test_non_admin_setup_has_no_projections_key(self):
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df), \
             patch("routes.mock_draft_routes.get_season_projection_legacy_shape", return_value=PROJECTIONS):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 200
        data = resp.json()
        assert "projections" not in data
        assert len(data["pickSequence"]) == 30
        assert data["season"] == 2026
        assert len(data["teams"]) == 32

    def test_admin_setup_includes_projections(self, admin_token):
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df), \
             patch("routes.mock_draft_routes.get_season_projection_legacy_shape", return_value=PROJECTIONS):
            resp = client.get("/api/mock-draft/setup", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        assert resp.json()["projections"] == PROJECTIONS

    def test_setup_returns_400_when_no_rules_configured(self):
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 400


class TestMockDraftPick:

    def test_pick_returns_team_from_available_teams(self):
        with patch("routes.mock_draft_routes.bot_pick", return_value=("KC", False)):
            resp = client.post("/api/mock-draft/pick", json={
                "season": 2026, "availableTeams": ["KC", "DAL"],
                "wildcardsSoFar": 0, "botPicksRemaining": 10,
            })
        assert resp.status_code == 200
        assert resp.json() == {"team": "KC", "wasWildcard": False}

    def test_pick_rejects_empty_available_teams(self):
        resp = client.post("/api/mock-draft/pick", json={
            "season": 2026, "availableTeams": [], "wildcardsSoFar": 0, "botPicksRemaining": 10,
        })
        assert resp.status_code == 400


class TestMockDraftResults:

    def test_non_admin_results_have_rank_only(self):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1},
            {"slot": 2, "totalProjectedWins": 7.0, "rank": 2},
        ]):
            resp = client.post("/api/mock-draft/results", json={
                "season": 2026, "rosters": {"1": ["KC", "DAL"], "2": ["NE", "LV"]},
            })
        assert resp.status_code == 200
        rankings = resp.json()["rankings"]
        assert all("totalProjectedWins" not in r for r in rankings)
        assert {r["rank"] for r in rankings} == {1, 2}

    def test_admin_results_include_totals(self, admin_token):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1},
        ]):
            resp = client.post(
                "/api/mock-draft/results",
                json={"season": 2026, "rosters": {"1": ["KC", "DAL"]}},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json()["rankings"][0]["totalProjectedWins"] == 20.2
