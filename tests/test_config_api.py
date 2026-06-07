import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_get_config_settings_public():
    """GET /api/config/settings is public — no auth required."""
    response = client.get("/api/config/settings")
    assert response.status_code == 200
    data = response.json()
    assert "draft_active" in data
    assert isinstance(data["draft_active"], bool)


def test_set_config_settings_requires_admin(auth_token):
    """POST /api/admin/config/settings rejects non-admin tokens."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": True},
        headers={"Authorization": auth_token},
    )
    assert response.status_code == 403


def test_set_config_settings_as_admin(admin_token, mock_firestore):
    """POST /api/admin/config/settings succeeds for admin and returns updated value."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": True},
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("draft_active") is True


def test_set_config_settings_ignores_unknown_keys(admin_token, mock_firestore):
    """POST /api/admin/config/settings strips unknown keys."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": False, "secret_flag": True},
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "secret_flag" not in data


def test_set_config_settings_no_auth_returns_401():
    """POST /api/admin/config/settings with no auth token returns 401."""
    response = client.post("/api/admin/config/settings", json={"draft_active": True})
    assert response.status_code == 401
