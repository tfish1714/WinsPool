"""Tests for GET /api/admin/betting/scan."""
from unittest.mock import patch
import pandas as pd

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _games_df():
    rows = []
    for season in (2020, 2021, 2022, 2023):
        for wk in range(1, 21):
            ht, at = f"H{wk}", f"A{wk}"
            if wk % 2 == 0:
                home_score, away_score = 30, 3
            else:
                home_score, away_score = 3, 30
            rows.append({
                "season": season, "week": wk, "home_team": ht, "away_team": at,
                "home_score": home_score, "away_score": away_score, "result": home_score - away_score,
            })
    return pd.DataFrame(rows)


def _predictions_for(season):
    preds = {}
    for wk in range(1, 21):
        ht, at = f"H{wk}", f"A{wk}"
        if wk % 2 == 0:
            elo_diff, spread_line = 80.0, 7.0
        else:
            elo_diff, spread_line = -80.0, -7.0
        preds[f"W{wk:02d}_{ht}_{at}"] = {"explanation": {"elo_diff": elo_diff, "vegas_line": spread_line}}
    return preds


def test_requires_admin():
    response = client.get("/api/admin/betting/scan")
    assert response.status_code == 401


def test_rejects_out_of_range_top_n(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/scan?top_n=0",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_rejects_negative_min_sample(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/scan?min_sample=0",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_rejects_negative_test_seasons(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/scan?test_seasons=-1",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_scan_returns_leaderboards(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get(
            "/api/admin/betting/scan?test_seasons=1&min_sample=10&min_test_sample=5&include_pairs=false",
            headers={"Authorization": admin_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ats_leaderboard"]
    top = data["ats_leaderboard"][0]
    assert top["train_rate"] == 1.0
    assert top["held_up"] is True
    assert "favorite_ats_cover_pct" in data["baseline"]
    assert data["seasons_covered"] == [2020, 2023]
