"""Tests for GET /api/admin/elo_history — must read via cache_service, never rawdata/ directly."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_requires_admin():
    response = client.get("/api/admin/elo_history")
    assert response.status_code == 401


def test_returns_404_when_no_elo_history(admin_token):
    with patch("services.cache_service.get_all_elo_history", return_value=[]):
        response = client.get("/api/admin/elo_history", headers={"Authorization": admin_token})
    assert response.status_code == 404


def test_returns_structured_history(admin_token):
    rows = [
        {"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF",
         "home_elo_post": 1520.5, "away_elo_post": 1490.3},
        {"season": 2025, "week": 2, "home_team": "SF", "away_team": "KC",
         "home_elo_post": 1495.1, "away_elo_post": 1515.7},
    ]
    with patch("services.cache_service.get_all_elo_history", return_value=rows):
        response = client.get("/api/admin/elo_history", headers={"Authorization": admin_token})

    assert response.status_code == 200
    data = response.json()
    assert data["seasons"] == [2025]
    assert "KC" in data["teams"]
    assert "SF" in data["teams"]
    assert data["teams"]["KC"]["2025"] == [
        {"week": 1, "elo": 1520.5},
        {"week": 2, "elo": 1515.7},
    ]
    # Team metadata comes from the bundled static/data/team_meta.json, not rawdata/
    assert data["divisions"].get("KC") == "AFC West"
    assert data["conferences"].get("KC") == "AFC"
    assert data["colors"].get("KC")
