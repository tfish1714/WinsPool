"""Push notification preferences: service helpers and /api/profile/push-* routes."""
import pytest
from fastapi.testclient import TestClient

from fake_firestore import FakeFirestore
import services.push_service as push_service
from main import app

client = TestClient(app)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setattr("services.db_service.get_db", lambda *a, **k: db)
    monkeypatch.setattr(push_service, "_invalidate_players_cache", lambda: None)
    # Force the direct-Firestore branch of _get_push_subscription.
    monkeypatch.setattr(
        "services.data_service._get_static_bucket",
        lambda: {"players": __import__("pandas").DataFrame()},
    )
    return db


def _bearer(player_id=1):
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id=player_id, role='user')}"}


def test_set_then_get_round_trip(fake_db):
    fake_db.collection("players").document("1").set({"playerId": 1})
    assert push_service.set_push_prefs(1, True, False) is True
    assert push_service.get_push_prefs(1) == {"recap": True, "standings": False}


def test_missing_prefs_default_true(fake_db):
    fake_db.collection("players").document("1").set({"playerId": 1})
    assert push_service.get_push_prefs(1) == {"recap": True, "standings": True}
    assert push_service.get_push_prefs(999) == {"recap": True, "standings": True}


def test_set_without_db_returns_false(monkeypatch):
    monkeypatch.setattr("services.db_service.get_db", lambda *a, **k: None)
    assert push_service.set_push_prefs(1, True, True) is False


def test_has_subscription_only_for_dict(fake_db):
    fake_db.collection("players").document("1").set({"push_subscription": {"endpoint": "e"}})
    fake_db.collection("players").document("2").set({"push_subscription": "junk"})
    fake_db.collection("players").document("3").set({"playerId": 3})
    assert push_service.has_subscription(1) is True
    assert push_service.has_subscription(2) is False
    assert push_service.has_subscription(3) is False


def test_status_route_shape(fake_db):
    fake_db.collection("players").document("1").set(
        {"push_subscription": {"endpoint": "e"}, "push_prefs": {"recap": False}})
    r = client.get("/api/profile/push-status", headers=_bearer(1))
    assert r.status_code == 200
    body = r.json()
    assert body["subscribed"] is True
    assert body["prefs"] == {"recap": False, "standings": True}
    assert isinstance(body["configured"], bool)


def test_prefs_route_persists_for_caller_only(fake_db):
    fake_db.collection("players").document("1").set({"playerId": 1})
    fake_db.collection("players").document("2").set({"playerId": 2})
    r = client.post("/api/profile/push-prefs", json={"recap": False, "standings": True},
                    headers=_bearer(1))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "prefs": {"recap": False, "standings": True}}
    assert push_service.get_push_prefs(1) == {"recap": False, "standings": True}
    assert push_service.get_push_prefs(2) == {"recap": True, "standings": True}


def test_prefs_route_rejects_non_boolean(fake_db):
    r = client.post("/api/profile/push-prefs", json={"recap": "maybe", "standings": True},
                    headers=_bearer(1))
    assert r.status_code == 422


def test_routes_require_auth():
    assert client.get("/api/profile/push-status").status_code == 401
    assert client.post("/api/profile/push-prefs",
                       json={"recap": True, "standings": True}).status_code == 401


def test_set_prefs_missing_player_returns_false_without_invalidating(fake_db, monkeypatch):
    calls = []
    monkeypatch.setattr(push_service, "_invalidate_players_cache", lambda: calls.append(1))
    assert push_service.set_push_prefs(404, True, True) is False
    assert calls == []


def test_set_prefs_success_returns_true_and_invalidates(fake_db, monkeypatch):
    calls = []
    monkeypatch.setattr(push_service, "_invalidate_players_cache", lambda: calls.append(1))
    fake_db.collection("players").document("1").set({"playerId": 1})
    assert push_service.set_push_prefs(1, False, True) is True
    assert calls == [1]
    assert push_service.get_push_prefs(1) == {"recap": False, "standings": True}


def test_prefs_route_500_when_save_fails(fake_db):
    r = client.post("/api/profile/push-prefs", json={"recap": True, "standings": True},
                    headers=_bearer(404))
    assert r.status_code == 500
    assert r.json() == {"error": "An internal error occurred."}
