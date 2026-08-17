"""tests/test_mock_draft.py — /api/mock-draft/* route tests."""
import pandas as pd
import pytest
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


@pytest.fixture(autouse=True)
def _mock_draft_active_by_default():
    """Every test in this file assumes the feature is toggled on unless it
    explicitly overrides this patch — only TestMockDraftActiveGate cares
    about the off state.
    """
    with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": True}):
        yield


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """The limiter's bucket dict is module-level state — clear it between
    tests so one test's call volume can't affect another's.
    """
    from routes import mock_draft_routes
    mock_draft_routes._rate_limit_buckets.clear()
    yield


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

    def test_setup_includes_team_schedules_for_non_admin(self):
        """teamSchedules is opponent/week text, not projection data -- sent to everyone."""
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df), \
             patch("routes.mock_draft_routes.get_team_schedules", return_value={"KC": ["Wk1 vs DAL"]}):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 200
        assert resp.json()["teamSchedules"] == {"KC": ["Wk1 vs DAL"]}


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
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1, "graded": True},
            {"slot": 2, "totalProjectedWins": 7.0, "rank": 2, "graded": True},
        ]):
            resp = client.post("/api/mock-draft/results", json={
                "season": 2026, "rosters": {"1": ["KC", "DAL"], "2": ["NE", "LV"]},
            })
        assert resp.status_code == 200
        rankings = resp.json()["rankings"]
        assert all("totalProjectedWins" not in r for r in rankings)
        assert {r["rank"] for r in rankings} == {1, 2}
        assert all(r["graded"] is True for r in rankings)

    def test_admin_results_include_totals(self, admin_token):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1, "graded": True},
        ]):
            resp = client.post(
                "/api/mock-draft/results",
                json={"season": 2026, "rosters": {"1": ["KC", "DAL"]}},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json()["rankings"][0]["totalProjectedWins"] == 20.2

    def test_ungraded_signal_survives_for_non_admin(self):
        """When rank_rosters marks entries ungraded (no projection data for the season),
        the non-admin response variant must still carry that signal through so the
        frontend can show an honest message instead of a fabricated rank."""
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 0.0, "rank": 1, "graded": False},
            {"slot": 2, "totalProjectedWins": 0.0, "rank": 2, "graded": False},
        ]):
            resp = client.post("/api/mock-draft/results", json={
                "season": 2026, "rosters": {"1": ["KC", "DAL"], "2": ["NE", "LV"]},
            })
        assert resp.status_code == 200
        rankings = resp.json()["rankings"]
        assert all(r["graded"] is False for r in rankings)

    def test_ungraded_signal_survives_for_admin(self, admin_token):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 0.0, "rank": 1, "graded": False},
        ]):
            resp = client.post(
                "/api/mock-draft/results",
                json={"season": 2026, "rosters": {"1": ["KC", "DAL"]}},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json()["rankings"][0]["graded"] is False


class TestMockDraftActiveGate:

    def test_page_shows_unavailable_when_inactive(self):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}):
            resp = client.get("/mock-draft")
        assert resp.status_code == 200
        assert "not currently available" in resp.text.lower()
        assert "mock_draft.js" not in resp.text

    def test_page_shows_draft_ui_when_active(self):
        resp = client.get("/mock-draft")
        assert resp.status_code == 200
        assert "mock_draft.js" in resp.text

    def test_page_bypasses_gate_for_admin(self, admin_token):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}):
            resp = client.get("/mock-draft", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        assert "mock_draft.js" in resp.text

    def test_setup_blocked_for_non_admin_when_inactive(self):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 403

    def test_setup_allowed_for_admin_when_inactive(self, admin_token):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}), \
             patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df):
            resp = client.get("/api/mock-draft/setup", headers={"Authorization": admin_token})
        assert resp.status_code == 200

    def test_pick_blocked_for_non_admin_when_inactive(self):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}):
            resp = client.post("/api/mock-draft/pick", json={
                "season": 2026, "availableTeams": ["KC"], "wildcardsSoFar": 0, "botPicksRemaining": 10,
            })
        assert resp.status_code == 403

    def test_results_blocked_for_non_admin_when_inactive(self):
        with patch("routes.mock_draft_routes.get_config_settings", return_value={"mock_draft_active": False}):
            resp = client.post("/api/mock-draft/results", json={
                "season": 2026, "rosters": {"1": ["KC"]},
            })
        assert resp.status_code == 403


class TestMockDraftRateLimit:

    def test_setup_rate_limited_after_threshold(self):
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df):
            statuses = [client.get("/api/mock-draft/setup").status_code for _ in range(41)]
        assert statuses[:40] == [200] * 40
        assert statuses[40] == 429

    def test_pick_rate_limited_after_threshold(self):
        body = {"season": 2026, "availableTeams": ["KC"], "wildcardsSoFar": 0, "botPicksRemaining": 10}
        with patch("routes.mock_draft_routes.bot_pick", return_value=("KC", False)):
            statuses = [client.post("/api/mock-draft/pick", json=body).status_code for _ in range(41)]
        assert statuses[:40] == [200] * 40
        assert statuses[40] == 429

    def test_rate_limit_is_per_endpoint_bucket_shared_by_ip(self):
        """setup + pick + results share one bucket per client — hammering one
        endpoint exhausts the allowance for the others too."""
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df):
            for _ in range(40):
                client.get("/api/mock-draft/setup")
        resp = client.post("/api/mock-draft/results", json={"season": 2026, "rosters": {"1": ["KC"]}})
        assert resp.status_code == 429
