"""Tests for GET /api/admin/betting/screen."""
from unittest.mock import patch
import pandas as pd

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _games_df():
    return pd.DataFrame([
        {"season": 2026, "week": 1, "home_team": "KC", "away_team": "SF",
         "home_score": None, "away_score": None, "result": None},
        {"season": 2026, "week": 2, "home_team": "BUF", "away_team": "MIA",
         "home_score": None, "away_score": None, "result": None},
    ])


def _predictions_for(season):
    if season != 2026:
        return {}
    return {
        "W01_KC_SF": {"explanation": {"elo_diff": 40.0, "vegas_line": 3.0}},
        "W02_BUF_MIA": {"explanation": {"elo_diff": -10.0, "vegas_line": -2.0}},
    }


def test_requires_admin():
    response = client.get("/api/admin/betting/screen")
    assert response.status_code == 401


def test_rejects_invalid_side(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/screen?side=sideways",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_rejects_invalid_favorite_or_dog(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/screen?favorite_or_dog=maybe",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_default_season_and_week_resolve_from_schedule(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get("/api/admin/betting/screen", headers={"Authorization": admin_token})

    assert response.status_code == 200
    data = response.json()
    assert data["target_season"] == 2026
    assert data["target_week"] == 1  # earliest unplayed week


def test_explicit_week_used_over_default(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get(
            "/api/admin/betting/screen?season=2026&week=2",
            headers={"Authorization": admin_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["target_week"] == 2
    weeks = {c["week"] for c in data["candidates"]}
    assert weeks == {2}


def test_filter_narrows_candidates(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get(
            "/api/admin/betting/screen?season=2026&week=1&elo_diff_min=100",
            headers={"Authorization": admin_token},
        )

    assert response.status_code == 200
    assert response.json()["candidates"] == []
