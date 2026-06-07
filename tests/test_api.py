from unittest.mock import patch
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _bearer(monkeypatch):
    """Helper: returns an Authorization header with a valid test JWT."""
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id=1, role='user')}"}


def test_read_main():
    """Test that the standings page renders correctly (HTML route, no auth needed)."""
    response = client.get("/wins-pool/2024")
    assert response.status_code == 200


def test_api_progress(monkeypatch):
    """GET /api/progress returns valid JSON with a valid auth token."""
    headers = _bearer(monkeypatch)
    response = client.get("/api/progress/2023/5", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["season"] == 2023
    assert data["week"] == 5
    assert "player_chart" in data


def test_api_draft_summary(monkeypatch):
    """GET /api/progress/draft_summary returns best-picks data with a valid auth token."""
    headers = _bearer(monkeypatch)
    response = client.get("/api/progress/draft_summary", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "best_overall" in data
    assert "best_by_round" in data


# ── Issue #89: push_subscribe endpoint ───────────────────────────────────────

FAKE_SUBSCRIPTION = {"endpoint": "https://push.example.com/sub", "keys": {"auth": "x", "p256dh": "y"}}


def test_push_subscribe_no_auth_returns_401():
    """POST /api/draft/push-subscribe with no auth token must return 401."""
    response = client.post("/api/draft/push-subscribe", json={"playerId": 1, "subscription": FAKE_SUBSCRIPTION})
    assert response.status_code == 401


def test_push_subscribe_missing_player_id_returns_400(monkeypatch):
    """Missing playerId in body returns 400."""
    headers = _bearer(monkeypatch)
    response = client.post("/api/draft/push-subscribe", json={"subscription": FAKE_SUBSCRIPTION}, headers=headers)
    assert response.status_code == 400


def test_push_subscribe_missing_subscription_returns_400(monkeypatch):
    """Missing subscription in body returns 400."""
    headers = _bearer(monkeypatch)
    response = client.post("/api/draft/push-subscribe", json={"playerId": 1}, headers=headers)
    assert response.status_code == 400


def test_push_subscribe_valid_returns_ok(monkeypatch):
    """Valid auth + full body → save_push_subscription called → {"ok": true}."""
    headers = _bearer(monkeypatch)
    with patch("services.push_service.save_push_subscription", return_value=True):
        response = client.post(
            "/api/draft/push-subscribe",
            json={"playerId": 1, "subscription": FAKE_SUBSCRIPTION},
            headers=headers,
        )
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_push_subscribe_service_failure_returns_500(monkeypatch):
    """save_push_subscription returning False triggers a 500."""
    headers = _bearer(monkeypatch)
    with patch("services.push_service.save_push_subscription", return_value=False):
        response = client.post(
            "/api/draft/push-subscribe",
            json={"playerId": 1, "subscription": FAKE_SUBSCRIPTION},
            headers=headers,
        )
    assert response.status_code == 500
