import pytest
import re
import time
import hashlib
import os
import sys
from unittest.mock import patch
from fastapi.testclient import TestClient

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

client = TestClient(app)

def test_api_check_player_nonexistent():
    """Verify that a nonexistent player returns 200 with exists=False (anti-enumeration)."""
    response = client.get("/api/check_player?email=ghost_stability@example.com")
    assert response.status_code == 200
    assert response.json()["exists"] is False

def test_api_profile_requires_auth():
    """GET /api/profile with no token must return 401."""
    response = client.get("/api/profile")
    assert response.status_code == 401

def test_api_login_payload_consistency():
    """Verify login failure returns error JSON."""
    response = client.post("/api/login", json={"email": "wrong@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert "error" in response.json()

def test_api_admin_unauthorized():
    """Verify that admin endpoints reject requests without admin role."""
    response = client.get("/api/admin/players?playerId=non_admin_id")
    assert response.status_code == 401


@pytest.mark.parametrize("pw,should_match", [
    ("Short1!",                False),   # too short
    ("alllowercase1!longpwd",  False),   # no uppercase
    ("ALLUPPERCASE1!LONGPWD",  False),   # no lowercase
    ("NoSpecialChar12345678",  False),   # no special char
    ("NoNumbers!!LongEnough",  False),   # no digit
    ("Valid1!LongEnoughPwd",   True),
    ("Another$Valid1Password", True),
    ("A1!aaaaaaaaa",           True),    # exactly 12 chars
    ("A1!aaaaaaaaaaaaaaaaaa",  True),    # long valid
    ("A1!aaaaaaaa",            False),   # 11 chars, too short
])
def test_password_complexity(pw, should_match):
    """PASSWORD_COMPLEXITY_RE must accept/reject exactly the documented cases."""
    from services.constants import PASSWORD_COMPLEXITY_RE
    assert bool(re.match(PASSWORD_COMPLEXITY_RE, pw)) == should_match


def test_api_profile_returns_own_data(auth_token):
    """Authenticated player receives their own profile data."""
    from unittest.mock import patch

    fake_player = {
        "playerId": 1,
        "fullName": "Test Player",
        "nickName": "TP",
        "email": "test@example.com",
        "role": "user",
        "mfa_enabled": False,
        "password_hash": "should_not_appear",
    }
    with patch("services.db_service.get_player_by_id", return_value=fake_player):
        response = client.get(
            "/api/profile",
            headers={"Authorization": auth_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["fullName"] == "Test Player"
    assert data["playerId"] == "1"
    assert "password_hash" not in data


# ── Issue #44: 5-attempt rate-limiter lockout ──────────────────────────────

class TestLoginLockout:

    def _player(self, **extra):
        return {
            "playerId": 99,
            "email": "lockout@test.com",
            "password_hash": "somehash",
            "role": "user",
            "failed_login_attempts": 0,
            "lockout_until": None,
            **extra,
        }

    def test_login_lockout_triggers_on_5th_failure(self):
        """Fifth wrong password → 429 with lockout_until set."""
        player = self._player(failed_login_attempts=4)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.verify_password", return_value=False), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/login", json={"email": "lockout@test.com", "password": "wrong"})
        assert resp.status_code == 429
        call_data = mock_update.call_args[0][1]
        assert "lockout_until" in call_data

    def test_login_during_active_lockout_returns_429(self):
        """Password is never checked when the lockout window is active."""
        player = self._player(lockout_until=time.time() + 3600)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.verify_password") as mock_verify:
            resp = client.post("/api/login", json={"email": "lockout@test.com", "password": "correct"})
        assert resp.status_code == 429
        mock_verify.assert_not_called()

    def test_login_lockout_persists_via_update_profile(self):
        """update_player_profile is called with new count=5 and lockout_until set."""
        player = self._player(failed_login_attempts=4)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.verify_password", return_value=False), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            client.post("/api/login", json={"email": "lockout@test.com", "password": "wrong"})
        mock_update.assert_called_once()
        call_data = mock_update.call_args[0][1]
        assert call_data["failed_login_attempts"] == 5
        assert call_data.get("lockout_until") is not None

    def test_login_resets_counter_on_success(self):
        """Successful login writes failed_login_attempts=0 back to the DB."""
        player = self._player(failed_login_attempts=2)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as mock_update, \
             patch("routes.auth_routes.create_token", return_value="tok"):
            resp = client.post("/api/login", json={"email": "lockout@test.com", "password": "correct"})
        assert resp.status_code == 200
        call_data = mock_update.call_args[0][1]
        assert call_data["failed_login_attempts"] == 0


class TestSetPasswordLockout:

    def _unclaimed_player(self, **extra):
        return {
            "playerId": 99,
            "email": "unclaimed@test.com",
            "password_hash": None,
            "role": "user",
            "failed_setup_attempts": 0,
            "lockout_until": None,
            **extra,
        }

    def test_set_password_mismatch_triggers_lockout_on_5th(self):
        """Fifth mismatched-password attempt → 429, lockout_until written."""
        player = self._unclaimed_player(failed_setup_attempts=4)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.increment_failed_setup_attempts") as mock_inc:
            resp = client.post("/api/set_password", json={
                "email": "unclaimed@test.com",
                "password": "Valid1!LongEnough",
                "confirm_password": "Different1!LongEnough",
            })
        assert resp.status_code == 429
        args = mock_inc.call_args[0]
        assert args[1] == 5          # new_count
        assert args[2] is not None   # lockout_until

    def test_set_password_weak_password_triggers_lockout_on_5th(self):
        """Fifth weak-password attempt → 429."""
        player = self._unclaimed_player(failed_setup_attempts=4)
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.increment_failed_setup_attempts") as mock_inc:
            resp = client.post("/api/set_password", json={
                "email": "unclaimed@test.com",
                "password": "weak",
                "confirm_password": "weak",
            })
        assert resp.status_code == 429
        mock_inc.assert_called_once()

    def test_set_password_during_active_lockout_returns_429(self):
        """Locked-out player cannot set a password at all."""
        player = self._unclaimed_player(lockout_until=time.time() + 3600)
        with patch("routes.auth_routes.get_player_by_email", return_value=player):
            resp = client.post("/api/set_password", json={
                "email": "unclaimed@test.com",
                "password": "Valid1!LongEnough",
                "confirm_password": "Valid1!LongEnough",
            })
        assert resp.status_code == 429


# ── Issue #45: MFA verify — expired token, wrong code, missing fields ──────

class TestMfaVerify:

    _CODE = "123456"
    _HASH = hashlib.sha256(_CODE.encode()).hexdigest()

    def _player(self, expired=False):
        return {
            "playerId": 99,
            "email": "mfa@test.com",
            "role": "user",
            "mfa_token": self._HASH,
            "mfa_expiry": (time.time() - 1) if expired else (time.time() + 600),
        }

    def test_mfa_verify_expired_token_returns_401(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player(expired=True)):
            resp = client.post("/api/mfa/verify", json={"playerId": "99", "code": self._CODE})
        assert resp.status_code == 401
        assert "expired" in resp.json()["error"].lower()

    def test_mfa_verify_wrong_code_returns_401(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()):
            resp = client.post("/api/mfa/verify", json={"playerId": "99", "code": "000000"})
        assert resp.status_code == 401

    def test_mfa_verify_empty_fields_returns_400(self):
        """Empty playerId or code strings are caught by the handler (not Pydantic)."""
        resp = client.post("/api/mfa/verify", json={"playerId": "", "code": ""})
        assert resp.status_code == 400

    def test_mfa_verify_unknown_player_returns_404(self):
        with patch("services.db_service.get_player_by_id", return_value=None):
            resp = client.post("/api/mfa/verify", json={"playerId": "999", "code": self._CODE})
        assert resp.status_code == 404


# ── Issue #46: cookie-based auth fallback for require_auth ─────────────────

class TestCookieAuth:

    def _fake_player(self):
        return {
            "playerId": 1,
            "fullName": "Cookie Tester",
            "nickName": "CT",
            "email": "cookie@test.com",
            "role": "user",
            "mfa_enabled": False,
        }

    def test_require_auth_cookie_only_succeeds(self):
        """Valid session_token cookie (no Authorization header) authenticates the user."""
        from services.session_service import create_token
        token = create_token(1, "user")
        with patch("services.db_service.get_player_by_id", return_value=self._fake_player()):
            resp = client.get("/api/profile", cookies={"session_token": token})
        assert resp.status_code == 200
        assert resp.json()["playerId"] == "1"

    def test_require_auth_header_wins_over_bad_cookie(self):
        """Valid Bearer header succeeds even when session_token cookie is garbage."""
        from services.session_service import create_token
        token = create_token(1, "user")
        with patch("services.db_service.get_player_by_id", return_value=self._fake_player()):
            resp = client.get(
                "/api/profile",
                headers={"Authorization": f"Bearer {token}"},
                cookies={"session_token": "garbage-value"},
            )
        assert resp.status_code == 200

    def test_require_auth_no_header_no_cookie_returns_401(self):
        resp = client.get("/api/profile")
        assert resp.status_code == 401


# ── Issue #47: /api/profile/update coverage ───────────────────────────────

class TestProfileUpdate:

    _BASE_BODY = {
        "playerId": "99",
        "currentPassword": "OldPass1!LongPwd",
        "fullName": "New Name",
        "nickName": "nn",
        "email": "new@test.com",
        "mfaEnabled": False,
    }

    def _player(self):
        return {
            "playerId": 99,
            "email": "old@test.com",
            "password_hash": "hash",
            "role": "user",
        }

    def test_profile_update_missing_password_returns_400(self):
        """Empty currentPassword is rejected before any DB lookup."""
        resp = client.post("/api/profile/update", json={
            "playerId": "99",
            "currentPassword": "",
        })
        assert resp.status_code == 400

    def test_profile_update_wrong_password_returns_401(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=False):
            resp = client.post("/api/profile/update", json=self._BASE_BODY)
        assert resp.status_code == 401

    def test_profile_update_email_collision_returns_400(self):
        collision = {"playerId": 77, "email": "new@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.get_player_by_email", return_value=collision):
            resp = client.post("/api/profile/update", json=self._BASE_BODY)
        assert resp.status_code == 400
        assert "already in use" in resp.json()["error"].lower()

    def test_profile_update_weak_new_password_returns_400(self):
        body = {**self._BASE_BODY, "newPassword": "weak", "email": "old@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.get_player_by_email", return_value=None):
            resp = client.post("/api/profile/update", json=body)
        assert resp.status_code == 400

    def test_profile_update_happy_path_returns_200(self):
        """Valid update with same email (no collision check) succeeds and calls update_player_profile."""
        body = {**self._BASE_BODY, "email": "old@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/profile/update", json=body)
        assert resp.status_code == 200
        mock_update.assert_called_once()


# ── Last Login Tracking Tests ──────────────────────────────────────────────

class TestLastLoginTracking:

    def test_login_updates_last_login(self):
        """Successful login records last_login timestamp."""
        player = {
            "playerId": 10,
            "fullName": "Test Player",
            "email": "player@test.com",
            "password_hash": "$2b$12$eIX/mockhash",
            "role": "user",
            "lockout_until": None,
            "failed_login_attempts": 0,
            "mfa_enabled": False,
            "must_change_password": False,
        }
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/login", json={"email": "player@test.com", "password": "ValidPassword1!"})
        assert resp.status_code == 200
        assert mock_update.called
        update_args = mock_update.call_args[0]
        assert update_args[0] == "10"
        assert "last_login" in update_args[1]
        assert isinstance(update_args[1]["last_login"], float)

    def test_set_password_updates_last_login(self):
        """Claiming account via set_password records last_login timestamp."""
        player = {
            "playerId": 11,
            "fullName": "New Player",
            "email": "new@test.com",
            "password_hash": None,
            "role": "user",
            "lockout_until": None,
            "failed_setup_attempts": 0,
        }
        with patch("routes.auth_routes.get_player_by_email", return_value=player), \
             patch("routes.auth_routes.update_player_credentials") as mock_creds, \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/set_password", json={
                "email": "new@test.com",
                "password": "ValidPassword1!",
                "confirm_password": "ValidPassword1!",
            })
        assert resp.status_code == 200
        assert mock_creds.called
        assert mock_update.called
        update_args = mock_update.call_args[0]
        assert update_args[0] == "11"
        assert "last_login" in update_args[1]
        assert isinstance(update_args[1]["last_login"], float)

    def test_verify_mfa_updates_last_login(self):
        """MFA verification records last_login timestamp."""
        code = "123456"
        token_hash = hashlib.sha256(code.encode()).hexdigest()
        player = {
            "playerId": 12,
            "fullName": "MFA Player",
            "email": "mfa@test.com",
            "role": "user",
            "mfa_token": token_hash,
            "mfa_expiry": time.time() + 600,
        }
        with patch("services.db_service.get_player_by_id", return_value=player), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/mfa/verify", json={"playerId": "12", "code": code})
        assert resp.status_code == 200
        assert mock_update.called
        update_args = mock_update.call_args[0]
        assert update_args[0] == "12"
        assert "last_login" in update_args[1]
        assert isinstance(update_args[1]["last_login"], float)
