"""Tests for services/session_service.py — JWT creation, validation, and auth dependencies."""
import time
import pytest
from fastapi import HTTPException


# ── #13: _get_secret() must raise on missing / insecure JWT_SECRET ─────────

def test_get_secret_raises_when_jwt_secret_not_set(monkeypatch):
    """_get_secret() raises RuntimeError when JWT_SECRET env var is absent."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    from services import session_service
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        session_service._get_secret()


def test_get_secret_raises_when_jwt_secret_is_insecure_default(monkeypatch):
    """_get_secret() raises RuntimeError when the well-known insecure default is used."""
    monkeypatch.setenv("JWT_SECRET", "dev-insecure-secret-change-in-production")
    from services import session_service
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        session_service._get_secret()


def test_get_secret_returns_value_when_configured(monkeypatch):
    """_get_secret() returns the secret when JWT_SECRET is properly set."""
    monkeypatch.setenv("JWT_SECRET", "a-real-32-char-secret-for-testing!")
    from services import session_service
    assert session_service._get_secret() == "a-real-32-char-secret-for-testing!"


# ── Token round-trip ────────────────────────────────────────────────────────

def test_create_and_decode_token_round_trip(monkeypatch):
    """A token created for a player can be decoded to recover player_id and role."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-round-trip")
    from services import session_service
    token = session_service.create_token(player_id=42, role="user")
    payload = session_service.decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "user"


def test_create_token_expires_in_7_days(monkeypatch):
    """Token expiry is set to approximately 7 days from now."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-expiry-check")
    from services import session_service
    before = int(time.time())
    token = session_service.create_token(player_id=1, role="admin")
    payload = session_service.decode_token(token)
    seven_days = 86400 * 7
    assert payload["exp"] - before >= seven_days - 5   # allow a few seconds of clock drift
    assert payload["exp"] - before <= seven_days + 5


def test_decode_token_rejects_tampered_signature(monkeypatch):
    """decode_token() raises when the token signature has been altered."""
    import jwt as pyjwt
    monkeypatch.setenv("JWT_SECRET", "test-secret-tamper")
    from services import session_service
    token = session_service.create_token(player_id=1, role="user")
    tampered = token[:-4] + "XXXX"
    with pytest.raises(Exception):
        session_service.decode_token(tampered)


# ── #12: require_auth dependency ───────────────────────────────────────────

def test_require_auth_raises_401_when_header_missing(monkeypatch):
    """require_auth() raises HTTP 401 when Authorization header is absent."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-require-auth")
    from services.session_service import require_auth
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization=None)
    assert exc_info.value.status_code == 401


def test_require_auth_raises_401_when_header_not_bearer(monkeypatch):
    """require_auth() raises HTTP 401 when header doesn't start with 'Bearer '."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-require-auth")
    from services.session_service import require_auth
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization="Basic abc123")
    assert exc_info.value.status_code == 401


def test_require_auth_raises_401_for_invalid_token(monkeypatch):
    """require_auth() raises HTTP 401 for a malformed or forged token."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-require-auth")
    from services.session_service import require_auth
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization="Bearer not.a.valid.jwt")
    assert exc_info.value.status_code == 401


def test_require_auth_accepts_valid_user_token(monkeypatch):
    """require_auth() returns payload for a valid JWT with role='user'."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-require-auth")
    from services import session_service
    token = session_service.create_token(player_id=7, role="user")
    payload = session_service.require_auth(authorization=f"Bearer {token}")
    assert payload["sub"] == "7"
    assert payload["role"] == "user"


def test_require_auth_accepts_valid_admin_token(monkeypatch):
    """require_auth() returns payload for a valid JWT with role='admin'."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-require-auth")
    from services import session_service
    token = session_service.create_token(player_id=1, role="admin")
    payload = session_service.require_auth(authorization=f"Bearer {token}")
    assert payload["sub"] == "1"
    assert payload["role"] == "admin"


def test_require_auth_raises_401_for_expired_token(monkeypatch):
    """require_auth() raises HTTP 401 with a descriptive message for expired tokens."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-expired")
    import jwt as pyjwt
    from services.session_service import require_auth, JWT_ALGORITHM, _get_secret
    expired_payload = {"sub": "1", "role": "user", "iat": 1000000, "exp": 1000001}
    expired_token = pyjwt.encode(expired_payload, _get_secret(), algorithm=JWT_ALGORITHM)
    with pytest.raises(HTTPException) as exc_info:
        require_auth(authorization=f"Bearer {expired_token}")
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


# ── get_is_admin() — non-raising admin check for anonymous-friendly endpoints ─

def test_get_is_admin_true_for_valid_admin_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=1, role="admin")
    assert session_service.get_is_admin(authorization=f"Bearer {token}", session_token=None) is True


def test_get_is_admin_false_for_valid_non_admin_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=2, role="user")
    assert session_service.get_is_admin(authorization=f"Bearer {token}", session_token=None) is False


def test_get_is_admin_false_when_no_token_present(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    assert session_service.get_is_admin(authorization=None, session_token=None) is False


def test_get_is_admin_false_for_malformed_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    assert session_service.get_is_admin(authorization="Bearer not-a-real-jwt", session_token=None) is False


def test_get_is_admin_reads_session_cookie_when_no_header(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=1, role="admin")
    assert session_service.get_is_admin(authorization=None, session_token=token) is True


def test_get_is_admin_prefers_bearer_header_over_cookie_when_both_present(monkeypatch):
    """Verify Bearer header takes priority over session cookie when both are present."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    admin_token = session_service.create_token(player_id=1, role="admin")
    user_token = session_service.create_token(player_id=2, role="user")
    # Bearer header carries the admin token, cookie carries a non-admin token — Bearer must win.
    assert session_service.get_is_admin(authorization=f"Bearer {admin_token}", session_token=user_token) is True


def test_get_is_admin_false_for_non_bearer_authorization_header(monkeypatch):
    """Verify non-Bearer Authorization headers (e.g. 'Basic xyz') are ignored and fall through to cookie check."""
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    admin_token = session_service.create_token(player_id=1, role="admin")
    # Authorization header is 'Basic xyz' (not Bearer), so it's ignored; cookie has admin token.
    assert session_service.get_is_admin(authorization="Basic xyz", session_token=admin_token) is True
