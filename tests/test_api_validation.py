"""
Tests that /api/progress/{season}/{week} rejects out-of-range path params
with HTTP 422 (FastAPI validation error) rather than 500 or silent bad data.
"""
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _auth_headers():
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id=1, role='user')}"}


def test_season_too_old_returns_422():
    """Season 1800 is before 2000 minimum — must return 422."""
    response = client.get("/api/progress/1800/5", headers=_auth_headers())
    assert response.status_code == 422


def test_season_too_future_returns_422():
    """Season 2099 is after 2030 maximum — must return 422."""
    response = client.get("/api/progress/2099/5", headers=_auth_headers())
    assert response.status_code == 422


def test_week_zero_returns_422():
    """Week 0 is below 1 minimum — must return 422."""
    response = client.get("/api/progress/2024/0", headers=_auth_headers())
    assert response.status_code == 422


def test_week_99_returns_422():
    """Week 99 exceeds 22 maximum — must return 422."""
    response = client.get("/api/progress/2024/99", headers=_auth_headers())
    assert response.status_code == 422


def test_valid_season_and_week_does_not_return_422():
    """A valid season/week combination must not return 422."""
    response = client.get("/api/progress/2024/5", headers=_auth_headers())
    assert response.status_code != 422
