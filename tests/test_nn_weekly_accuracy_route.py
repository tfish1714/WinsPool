"""Tests for GET /api/admin/nn_weekly_accuracy — reads via cache_service, never the CSV directly."""
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_requires_admin():
    response = client.get("/api/admin/nn_weekly_accuracy")
    assert response.status_code == 401


def test_returns_404_when_no_history(admin_token):
    with patch("services.cache_service.get_all_nn_weekly_accuracy", return_value=[]):
        response = client.get("/api/admin/nn_weekly_accuracy", headers={"Authorization": admin_token})
    assert response.status_code == 404


def test_returns_rows_grouped_and_sorted_by_season(admin_token):
    rows = [
        {"season": 2025, "week": 2, "accuracy_pct": 70.0},
        {"season": 2025, "week": 1, "accuracy_pct": 60.0},
        {"season": 2006, "week": 1, "accuracy_pct": 50.0},
    ]
    with patch("services.cache_service.get_all_nn_weekly_accuracy", return_value=rows):
        response = client.get("/api/admin/nn_weekly_accuracy", headers={"Authorization": admin_token})

    assert response.status_code == 200
    data = response.json()["seasons"]
    assert set(data.keys()) == {"2025", "2006"}
    assert [r["week"] for r in data["2025"]] == [1, 2]
    assert [r["week"] for r in data["2006"]] == [1]
