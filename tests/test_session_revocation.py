"""Session revocation via a per-player token_version (JWT claim `tv`).

A password change (own change, admin reset, admin temp password) bumps the
player's stored `token_version`; a token minted earlier carries the old `tv`
and is rejected everywhere a token is trusted.
"""
import time

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from services import session_service
from services.session_service import create_token, decode_token, require_admin, require_auth, get_is_admin

STRONG_PW = "Correct-Horse-Battery-9!"


@pytest.fixture
def real_lookup(monkeypatch):
    """Undo conftest's permissive player-lookup stub so the real check runs."""
    monkeypatch.setattr(session_service, "_lookup_player", session_service._load_player_from_db)


@pytest.fixture
def players(monkeypatch, tmp_path, real_lookup):
    """A real local players pkl (in tmp_path, never the developer's .local_db)."""
    from services import cache_service, db_service
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    (tmp_path / ".local_db").mkdir()
    df = pd.DataFrame([
        {"playerId": 1, "fullName": "Admin", "nickName": "A", "email": "admin@example.com",
         "role": "admin", "password_hash": db_service.get_password_hash(STRONG_PW),
         "mfa_enabled": False, "must_change_password": False},
        {"playerId": 2, "fullName": "User", "nickName": "U", "email": "user@example.com",
         "role": "user", "password_hash": db_service.get_password_hash(STRONG_PW),
         "mfa_enabled": False, "must_change_password": False},
    ])
    df.to_pickle(tmp_path / ".local_db" / "players.pkl")
    cache_service.clear_data_cache()
    yield
    cache_service.clear_data_cache()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _stored_version(pid):
    from services import db_service
    v = db_service.get_player_by_id(pid).get("token_version")
    return 0 if v is None else int(v)


# --- create_token / helper -------------------------------------------------

def test_create_token_embeds_tv():
    assert decode_token(create_token(3, "user", 4))["tv"] == 4
    assert decode_token(create_token(3, "user"))["tv"] == 0


def test_token_is_current_semantics():
    f = session_service.token_is_current
    assert f({"tv": 2}, {"token_version": 2}) is True
    assert f({"tv": 1}, {"token_version": 2}) is False
    assert f({}, {"token_version": 0}) is True             # legacy token, unbumped player
    assert f({}, {}) is True                               # neither has a version
    assert f({}, {"token_version": float("nan")}) is True  # NaN = unset
    assert f({}, {"token_version": 1}) is False            # legacy token after a bump
    assert f({"tv": 0}, None) is False                     # deleted player


# --- every trust point -----------------------------------------------------

def test_old_token_rejected_on_all_trust_points_after_bump(players):
    from services import db_service
    old = create_token(1, "admin", 0)
    assert require_auth(authorization=f"Bearer {old}")["sub"] == "1"
    assert require_admin(authorization=f"Bearer {old}")["sub"] == "1"
    assert get_is_admin(authorization=f"Bearer {old}") is True

    db_service.update_player_credentials("1", db_service.get_password_hash(STRONG_PW + "x"))

    for fn in (require_auth, require_admin):
        with pytest.raises(HTTPException) as e:
            fn(authorization=f"Bearer {old}")
        assert e.value.status_code == 401
    assert get_is_admin(authorization=f"Bearer {old}") is False
    assert get_is_admin(authorization=None, session_token=old) is False

    fresh = create_token(1, "admin", _stored_version(1))
    assert require_admin(authorization=f"Bearer {fresh}")["sub"] == "1"


def test_legacy_token_without_tv_works_until_password_changes(players):
    import jwt
    legacy = jwt.encode({"sub": "2", "role": "user", "iat": int(time.time()),
                         "exp": int(time.time()) + 600},
                        session_service._get_secret(), algorithm="HS256")
    assert require_auth(authorization=f"Bearer {legacy}")["sub"] == "2"
    from services import db_service
    db_service.update_player_credentials("2", db_service.get_password_hash(STRONG_PW + "y"))
    with pytest.raises(HTTPException):
        require_auth(authorization=f"Bearer {legacy}")


def test_token_for_deleted_player_rejected(players):
    tok = create_token(999, "user", 0)
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=f"Bearer {tok}")
    assert e.value.status_code == 401
    assert get_is_admin(authorization=f"Bearer {create_token(999, 'admin')}") is False


def test_lookup_failure_fails_closed(monkeypatch):
    def boom(pid):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(session_service, "_lookup_player", boom)
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=f"Bearer {create_token(1, 'user')}")
    assert e.value.status_code == 503
    assert get_is_admin(authorization=f"Bearer {create_token(1, 'admin')}") is False


def test_decode_current_token_helper(players):
    from services import db_service
    old = create_token(2, "user", 0)
    assert session_service.decode_current_token(old)["sub"] == "2"
    db_service.update_player_credentials("2", db_service.get_password_hash(STRONG_PW + "z"))
    assert session_service.decode_current_token(old) is None
    assert session_service.decode_current_token("garbage") is None


def test_cookie_only_trust_sites_reject_old_token(players, monkeypatch):
    """history_routes._viewer and standings /profile also decode tokens."""
    from services import db_service
    from routes.history_routes import _viewer
    from starlette.requests import Request

    def req(tok):
        return Request({"type": "http", "headers": [(b"cookie", f"session_token={tok}".encode())]})

    old = create_token(1, "admin", 0)
    assert _viewer(req(old)) == (1, True)
    db_service.update_player_credentials("1", db_service.get_password_hash(STRONG_PW + "q"))
    assert _viewer(req(old)) == (None, False)

    c = TestClient(app, follow_redirects=False)
    c.cookies.set("session_token", create_token(2, "user", 0))
    r = c.get("/profile")
    assert r.status_code in (302, 307) and "/player/2" in r.headers["location"]
    db_service.update_player_credentials("2", db_service.get_password_hash(STRONG_PW + "r"))
    from starlette.responses import PlainTextResponse
    monkeypatch.setattr("routes.standings_routes.templates.TemplateResponse",
                        lambda request, name, *a, **k: PlainTextResponse(f"fallthrough:{name}"))
    r = c.get("/profile")
    assert r.status_code == 200 and r.text == "fallthrough:profile_redirect.html"


# --- password writers bump -------------------------------------------------

def test_update_player_credentials_bumps_by_one_each_time(players):
    from services import db_service
    assert _stored_version(2) == 0
    db_service.update_player_credentials("2", db_service.get_password_hash("a" * 12 + "A1!"))
    assert _stored_version(2) == 1
    db_service.update_player_credentials("2", db_service.get_password_hash("b" * 12 + "A1!"))
    assert _stored_version(2) == 2
    assert _stored_version(1) == 0        # other players untouched


def test_non_password_profile_update_does_not_bump(players):
    from services import db_service
    db_service.update_player_profile("2", {"nickName": "Newname"})
    assert _stored_version(2) == 0


def test_bump_applies_to_firestore_update_as_increment(monkeypatch, real_lookup):
    """Remote mode: the bump must be a server-side Increment inside the same
    update() call as the password write, not a read-modify-write."""
    from unittest.mock import MagicMock
    from services import db_service
    db = MagicMock()
    monkeypatch.setenv("USE_LOCAL_DATA", "false")
    monkeypatch.setattr(db_service, "get_db", lambda: db)
    monkeypatch.setattr(db_service, "get_collection_df", lambda name, filters=None: pd.DataFrame())
    db_service.update_player_credentials("5", "hash")
    calls = db.collection.return_value.document.return_value.update.call_args_list
    assert len(calls) == 1
    payload = calls[0].args[0]
    assert payload["password_hash"] == "hash"
    assert type(payload["token_version"]).__name__ == "Increment"


# --- routes ----------------------------------------------------------------

def test_login_token_carries_current_version(players):
    from services import db_service
    db_service.update_player_credentials("2", db_service.get_password_hash(STRONG_PW))
    c = TestClient(app)
    r = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW})
    assert r.status_code == 200
    assert decode_token(r.json()["token"])["tv"] == 1
    assert c.get("/api/profile", headers=_bearer(r.json()["token"])).status_code == 200


def test_profile_password_change_revokes_old_and_returns_fresh_token(players):
    c = TestClient(app)
    old = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW}).json()["token"]
    new_pw = "Another-Strong-Pass-7#"
    r = c.post("/api/profile/update", headers=_bearer(old), json={
        "playerId": "2", "fullName": "User", "nickName": "U", "email": "user@example.com",
        "currentPassword": STRONG_PW, "newPassword": new_pw, "mfaEnabled": False})
    assert r.status_code == 200, r.text
    fresh = r.json()["token"]
    assert "session_token=" in r.headers.get("set-cookie", "")
    assert c.get("/api/profile", headers=_bearer(old)).status_code == 401
    assert c.get("/api/profile", headers=_bearer(fresh)).status_code == 200


def test_profile_update_without_password_change_keeps_old_token_valid(players):
    c = TestClient(app)
    old = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW}).json()["token"]
    r = c.post("/api/profile/update", headers=_bearer(old), json={
        "playerId": "2", "fullName": "User Two", "nickName": "U", "email": "user@example.com",
        "currentPassword": STRONG_PW, "newPassword": "", "mfaEnabled": False})
    assert r.status_code == 200
    assert c.get("/api/profile", headers=_bearer(old)).status_code == 200


def test_admin_reset_password_revokes_target_tokens(players):
    c = TestClient(app)
    admin = c.post("/api/login", json={"email": "admin@example.com", "password": STRONG_PW}).json()["token"]
    victim = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW}).json()["token"]
    r = c.post("/api/admin/reset_password", headers=_bearer(admin), json={"targetPlayerId": "2"})
    assert r.status_code == 200, r.text
    assert c.get("/api/profile", headers=_bearer(victim)).status_code == 401
    assert c.get("/api/profile", headers=_bearer(admin)).status_code == 200   # admin unaffected


def test_admin_set_temp_password_revokes_target_tokens(players):
    c = TestClient(app)
    admin = c.post("/api/login", json={"email": "admin@example.com", "password": STRONG_PW}).json()["token"]
    victim = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW}).json()["token"]
    r = c.post("/api/admin/set_temp_password", headers=_bearer(admin),
               json={"targetPlayerId": "2", "tempPassword": "Temp-Password-42#x"})
    assert r.status_code == 200, r.text
    assert c.get("/api/profile", headers=_bearer(victim)).status_code == 401


def test_set_password_token_is_valid_after_claim(players):
    """First-time claim writes the hash (bumping the version) and must mint its
    token against the post-write version, or the new user is instantly logged out."""
    from services import cache_service
    df = pd.read_pickle(".local_db/players.pkl")
    df["password_hash"] = df["password_hash"].astype(object)
    df.loc[df.playerId == 2, "password_hash"] = None
    df.to_pickle(".local_db/players.pkl")
    cache_service.clear_data_cache()
    c = TestClient(app)
    r = c.post("/api/set_password", json={"email": "user@example.com",
                                          "password": STRONG_PW, "confirm_password": STRONG_PW})
    assert r.status_code == 200, r.text
    assert c.get("/api/profile", headers=_bearer(r.json()["token"])).status_code == 200


# --- profile form source-level checks (no JS test suite) --------------------

def _profile_template():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / "templates" / "player_profile.html").read_text(encoding="utf-8")


def test_profile_form_surfaces_detail_and_handles_401_and_stores_fresh_token():
    src = _profile_template()
    assert "result.detail" in src
    assert "result.error || result.detail" in src and '"Update failed"' in src
    assert "resp.status === 401" in src
    assert "/wins-pool" in src
    # fresh token from a password change must replace the stored Bearer token
    assert "localStorage.setItem('nfl_wins_token', result.token)" in src
    # all form element ids kept
    for eid in ("full-name", "nickname", "email", "current-password", "new-password",
                "confirm-new-password", "mfa-enabled"):
        assert f"getElementById('{eid}')" in src


# --- review follow-ups: MFA challenge revocation, overflow ------------------

def _give_pending_mfa(pid):
    from services import db_service
    db_service.update_player_profile(str(pid), {"mfa_token": "somehash", "mfa_expiry": time.time() + 600})


def _assert_no_pending_mfa(pid):
    from services import db_service
    p = db_service.get_player_by_id(pid)
    assert p.get("mfa_token") in (None, "")
    assert not p.get("mfa_expiry")


def test_update_player_credentials_clears_pending_mfa(players):
    from services import db_service
    _give_pending_mfa(2)
    db_service.update_player_credentials("2", db_service.get_password_hash("a" * 12 + "A1!"))
    _assert_no_pending_mfa(2)


def test_admin_reset_password_clears_pending_mfa(players):
    c = TestClient(app)
    admin = c.post("/api/login", json={"email": "admin@example.com", "password": STRONG_PW}).json()["token"]
    _give_pending_mfa(2)
    r = c.post("/api/admin/reset_password", headers=_bearer(admin), json={"targetPlayerId": "2"})
    assert r.status_code == 200, r.text
    _assert_no_pending_mfa(2)


def test_profile_password_change_clears_pending_mfa(players):
    c = TestClient(app)
    tok = c.post("/api/login", json={"email": "user@example.com", "password": STRONG_PW}).json()["token"]
    _give_pending_mfa(2)
    r = c.post("/api/profile/update", headers=_bearer(tok), json={
        "currentPassword": STRONG_PW, "newPassword": "Another-Strong-Pw-7#",
        "playerId": "2", "fullName": "User", "nickName": "U", "email": "user@example.com", "mfaEnabled": False})
    assert r.status_code == 200, r.text
    _assert_no_pending_mfa(2)


def test_non_password_update_keeps_pending_mfa(players):
    from services import db_service
    _give_pending_mfa(2)
    db_service.update_player_profile("2", {"nickName": "Newname"})
    assert db_service.get_player_by_id(2).get("mfa_token") == "somehash"


def test_as_version_overflow_is_zero_not_error():
    assert session_service._as_version(float("inf")) == 0
    assert session_service._as_version(float("-inf")) == 0
    assert session_service.token_is_current({"tv": 0}, {"token_version": float("inf")}) is True
