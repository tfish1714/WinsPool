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


def test_get_config_settings_includes_mock_draft_active():
    """mock_draft_active defaults present alongside draft_active on the same doc."""
    response = client.get("/api/config/settings")
    assert response.status_code == 200
    data = response.json()
    assert "mock_draft_active" in data
    assert isinstance(data["mock_draft_active"], bool)


def test_set_config_settings_mock_draft_active_as_admin(admin_token, mock_firestore):
    """POST /api/admin/config/settings accepts mock_draft_active same as draft_active."""
    response = client.post(
        "/api/admin/config/settings",
        json={"mock_draft_active": True},
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("mock_draft_active") is True


def test_set_config_settings_merges_not_overwrites(admin_token, mock_firestore):
    """Toggling one flag must not overwrite the other on the same config doc.

    draft_active and mock_draft_active share config/settings; a plain
    Firestore .set(data) (no merge) would silently erase whichever flag
    wasn't included in this particular POST body.
    """
    client.post(
        "/api/admin/config/settings",
        json={"draft_active": True},
        headers={"Authorization": admin_token},
    )
    set_call = mock_firestore.collection.return_value.document.return_value.set
    set_call.assert_called_with({"draft_active": True}, merge=True)


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


def test_get_config_settings_includes_integer_latest_week():
    data = client.get("/api/config/settings").json()
    assert "latest_week" in data
    assert isinstance(data["latest_week"], int) and data["latest_week"] >= 0


def test_get_config_latest_week_fails_closed_to_zero(monkeypatch):
    import routes.api_routes as api_routes
    api_routes._reset_latest_week_cache()

    def boom():
        raise RuntimeError("data unavailable")

    monkeypatch.setattr(api_routes, "_active_season_latest_week", boom)
    # The endpoint must still return 200 with latest_week == 0.
    response = client.get("/api/config/settings")
    assert response.status_code == 200
    assert response.json()["latest_week"] == 0


def test_latest_week_cached_within_ttl(monkeypatch):
    import routes.api_routes as api_routes
    api_routes._reset_latest_week_cache()
    calls = []

    def fake():
        calls.append(1)
        return 7

    monkeypatch.setattr(api_routes, "_active_season_latest_week", fake)
    assert api_routes._safe_latest_week() == 7
    assert api_routes._safe_latest_week() == 7
    assert len(calls) == 1


def test_latest_week_recomputed_after_ttl(monkeypatch):
    import routes.api_routes as api_routes
    api_routes._reset_latest_week_cache()
    now = [1000.0]
    monkeypatch.setattr(api_routes.time, "monotonic", lambda: now[0])
    values = iter([7, 8])
    calls = []

    def fake():
        calls.append(1)
        return next(values)

    monkeypatch.setattr(api_routes, "_active_season_latest_week", fake)
    assert api_routes._safe_latest_week() == 7
    now[0] += api_routes._LATEST_WEEK_TTL_SECONDS + 1
    assert api_routes._safe_latest_week() == 8
    assert len(calls) == 2


def test_latest_week_failure_is_not_cached(monkeypatch):
    import routes.api_routes as api_routes
    api_routes._reset_latest_week_cache()
    outcomes = [RuntimeError("down"), 9]
    calls = []

    def flaky():
        calls.append(1)
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(api_routes, "_active_season_latest_week", flaky)
    assert api_routes._safe_latest_week() == 0
    assert api_routes._safe_latest_week() == 9
    assert len(calls) == 2
