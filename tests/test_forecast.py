"""tests/test_forecast.py — Unit tests for GET /api/admin/forecast."""
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app

client = TestClient(app)

_PRESEASON = {
    "KC":  {"projected_wins": 11.5, "std_dev": 2.1, "sources": {}},
    "DET": {"projected_wins": 11.0, "std_dev": 2.0, "sources": {}},
    "BUF": {"projected_wins": 10.5, "std_dev": 1.9, "sources": {}},
}

_GAME_PREDS = {
    "W01_KC_BUF":  {"pred_winner": "KC",  "locked": False},
    "W01_DET_NYG": {"pred_winner": "DET", "locked": False},
    "W02_KC_LV":   {"pred_winner": "KC",  "locked": False},
}

_FEAT_DOC = {"ensemble_version": "nn_v10+xgb_v6+lr_v2"}


class TestForecastEndpoint:
    def test_happy_path_shape(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value=_GAME_PREDS), \
             patch("routes.admin_routes.get_prediction_features", return_value=_FEAT_DOC):
            with patch("routes.admin_routes.get_preseason_predictions", return_value=_PRESEASON):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["season"] == 2026
        assert data["game_count"] == 3
        assert data["weeks"] == [1, 2]
        assert data["model_version"] == "nn_v10+xgb_v6+lr_v2"
        assert len(data["team_projections"]) == 3

    def test_team_projections_sorted_by_wins_desc(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value=_GAME_PREDS), \
             patch("routes.admin_routes.get_prediction_features", return_value=_FEAT_DOC):
            with patch("routes.admin_routes.get_preseason_predictions", return_value=_PRESEASON):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        teams = [t["team"] for t in resp.json()["team_projections"]]
        assert teams == ["KC", "DET", "BUF"]

    def test_team_projection_fields(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value=_GAME_PREDS), \
             patch("routes.admin_routes.get_prediction_features", return_value=_FEAT_DOC):
            with patch("routes.admin_routes.get_preseason_predictions", return_value=_PRESEASON):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        first = resp.json()["team_projections"][0]
        assert first == {"team": "KC", "projected_wins": 11.5, "std_dev": 2.1}

    def test_empty_preseason_returns_empty_list(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value=_GAME_PREDS), \
             patch("routes.admin_routes.get_prediction_features", return_value=None):
            with patch("routes.admin_routes.get_preseason_predictions", return_value={}):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        assert resp.json()["team_projections"] == []

    def test_empty_game_preds_returns_empty_weeks(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value={}), \
             patch("routes.admin_routes.get_prediction_features", return_value=None):
            with patch("routes.admin_routes.get_preseason_predictions", return_value=_PRESEASON):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        data = resp.json()
        assert data["weeks"] == []
        assert data["game_count"] == 0

    def test_model_version_none_when_no_features(self, admin_token):
        with patch("routes.admin_routes.get_game_predictions", return_value=_GAME_PREDS), \
             patch("routes.admin_routes.get_prediction_features", return_value=None):
            with patch("routes.admin_routes.get_preseason_predictions", return_value=_PRESEASON):
                resp = client.get("/api/admin/forecast", headers={"Authorization": admin_token})
        assert resp.json()["model_version"] is None

    def test_requires_admin_auth(self):
        resp = client.get("/api/admin/forecast")
        assert resp.status_code == 401
