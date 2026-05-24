"""
Tests that MFA codes are stored as SHA-256 hashes, never as plaintext.

The login flow in auth_routes.py generates a 6-digit code, stores it
in Firestore under mfa_token, and the verify endpoint checks it.
After this fix, mfa_token in storage must be the hash, not the raw digit string.
"""
import hashlib
import pytest
from unittest.mock import patch, MagicMock


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_stored_mfa_token_is_not_plaintext():
    """
    When a player logs in with MFA enabled, the value written to the DB
    must not equal the raw 6-digit code.
    """
    captured_updates = {}

    def fake_update(player_id, updates):
        captured_updates.update(updates)

    fake_player = {
        "playerId": "42",
        "email": "test@example.com",
        "password_hash": "some_hash",
        "mfa_enabled": True,
        "role": "user",
        "fullName": "Test User",
        "must_change_password": False,
        "lockout_until": None,
        "failed_login_attempts": 0,
    }

    with patch("routes.auth_routes.get_player_by_email", return_value=fake_player), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes._is_legacy_sha256", return_value=False), \
         patch("routes.auth_routes.update_player_profile", side_effect=fake_update), \
         patch("routes.auth_routes.email_service") as mock_email:

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)

        resp = client.post("/api/login", json={"email": "test@example.com", "password": "Test1234!"})
        assert resp.status_code == 200, f"Login failed: {resp.json()}"
        assert resp.json().get("status") == "mfa_required"

    stored = captured_updates.get("mfa_token", "")
    # Must be a SHA-256 hex digest (64 hex chars), not a 6-digit string
    assert len(stored) == 64, f"Expected 64-char hash, got: {stored!r}"
    assert stored.isalnum(), "Hash should be hex alphanumeric"


def test_correct_mfa_code_still_verifies():
    """
    After hashing, submitting the correct plaintext code must still succeed.
    The verify endpoint hashes the submitted code before comparing.
    """
    import time
    raw_code = "123456"
    hashed = _sha256(raw_code)

    fake_player = {
        "playerId": "42",
        "mfa_token": hashed,
        "mfa_expiry": time.time() + 600,
        "role": "user",
        "fullName": "Test User",
        "email": "test@example.com",
    }

    with patch("services.db_service.get_player_by_id", return_value=fake_player), \
         patch("routes.auth_routes.update_player_profile"), \
         patch("routes.auth_routes.create_token", return_value="fake-jwt"):

        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)

        resp = client.post("/api/mfa/verify", json={"playerId": "42", "code": raw_code})
        assert resp.status_code == 200, f"MFA verify failed: {resp.json()}"
        assert resp.json().get("status") == "success"
