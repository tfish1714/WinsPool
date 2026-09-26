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

def test_login_succeeds_when_role_field_is_missing():
    """Reproduces a prod 500: get_player_by_email() returns match.iloc[0].to_dict(),
    which includes every DataFrame column for every row -- a player whose Firestore
    doc never had a `role` field comes back with role=nan (a float), not a missing
    key. player.get("role", "user") only substitutes the default for a MISSING key,
    not a present-but-nan one, so `role` stayed nan and blew up json.dumps
    (Starlette's JSONResponse sets allow_nan=False) with
    "ValueError: Out of range float values are not JSON compliant".
    """
    fake_player = {
        "playerId": 11,
        "email": "test@example.com",
        "password_hash": "some_hash",
        "fullName": "Test User",
        "nickName": "Testy",
        "mfa_enabled": False,
        "must_change_password": False,
        "lockout_until": None,
        "failed_login_attempts": 0,
        "role": float("nan"),  # matches match.iloc[0].to_dict() for a role-less row
    }

    with patch("routes.auth_routes.get_player_by_email", return_value=fake_player), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes._is_legacy_sha256", return_value=False), \
         patch("routes.auth_routes.update_player_profile"):

        # Isolated client, not the shared module-level `client` -- a successful
        # login sets a session_token cookie, which would otherwise persist on
        # the shared client's cookie jar and leak into later tests in this file.
        resp = TestClient(app).post("/api/login", json={"email": "test@example.com", "password": "Test1234!"})

    assert resp.status_code == 200, f"Login failed: {resp.text}"
    body = resp.json()
    assert body["status"] == "success"
    assert body["role"] == "user"


def test_login_succeeds_when_must_change_password_and_mfa_enabled_are_nan():
    """Reproduces a real prod incident: the admin's own account had
    must_change_password=nan (same to_dict() root cause as the role bug
    above). `if player.get("must_change_password"):` is a bare truthiness
    check, and bool(float('nan')) is True in Python -- so a normal login
    was incorrectly routed into the forced-password-change branch, which
    returns {"status": "must_change_password", ...} with no `error` field.
    The frontend's else-branch then showed the literal fallback string
    'Login failed', matching exactly what was reported.
    """
    fake_player = {
        "playerId": 1,
        "email": "test@example.com",
        "password_hash": "some_hash",
        "fullName": "Test User",
        "nickName": "Testy",
        "role": "admin",
        "must_change_password": float("nan"),
        "mfa_enabled": float("nan"),
        "lockout_until": None,
        "failed_login_attempts": 0,
    }

    with patch("routes.auth_routes.get_player_by_email", return_value=fake_player), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes._is_legacy_sha256", return_value=False), \
         patch("routes.auth_routes.update_player_profile"):

        resp = TestClient(app).post("/api/login", json={"email": "test@example.com", "password": "Test1234!"})

    assert resp.status_code == 200, f"Login failed: {resp.text}"
    body = resp.json()
    assert body["status"] == "success", f"Expected clean success, got: {body}"


def test_wrong_password_returns_401_when_failed_login_attempts_is_nan():
    """Reproduces a real prod incident: a wrong-password attempt for a player
    with failed_login_attempts=nan (same to_dict() root cause as the other
    nan bugs above) crashed with "ValueError: cannot convert float NaN to
    integer" at `int(player.get("failed_login_attempts", 0)) + 1` -- the
    dict.get default never applies because the key IS present, just nan.
    A normal wrong password must return a clean 401, not a 500.
    """
    fake_player = {
        "playerId": 7,
        "email": "test@example.com",
        "password_hash": "some_hash",
        "role": "user",
        "failed_login_attempts": float("nan"),
        "lockout_until": None,
    }

    with patch("routes.auth_routes.get_player_by_email", return_value=fake_player), \
         patch("routes.auth_routes.verify_password", return_value=False), \
         patch("routes.auth_routes.update_player_profile") as mock_update:

        resp = TestClient(app).post("/api/login", json={"email": "test@example.com", "password": "WrongPassword1!"})

    assert resp.status_code == 401, f"Expected clean 401, got: {resp.status_code} {resp.text}"
    assert resp.json()["error"] == "Invalid email or password."
    # Confirms it actually reached the increment logic (not skipped) and
    # recovered to 1, not crashed
    mock_update.assert_called_once_with("7", {"failed_login_attempts": 1})


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
        }, headers=_bearer(99))
        assert resp.status_code == 400

    def test_profile_update_wrong_password_returns_401(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=False):
            resp = client.post("/api/profile/update", json=self._BASE_BODY, headers=_bearer(99))
        assert resp.status_code == 401

    def test_profile_update_email_collision_returns_400(self):
        collision = {"playerId": 77, "email": "new@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.get_player_by_email", return_value=collision):
            resp = client.post("/api/profile/update", json=self._BASE_BODY, headers=_bearer(99))
        assert resp.status_code == 400
        assert "already in use" in resp.json()["error"].lower()

    def test_profile_update_weak_new_password_returns_400(self):
        body = {**self._BASE_BODY, "newPassword": "weak", "email": "old@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.get_player_by_email", return_value=None):
            resp = client.post("/api/profile/update", json=body, headers=_bearer(99))
        assert resp.status_code == 400

    def test_profile_update_happy_path_returns_200(self):
        """Valid update with same email (no collision check) succeeds and calls update_player_profile."""
        body = {**self._BASE_BODY, "email": "old@test.com"}
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as mock_update:
            resp = client.post("/api/profile/update", json=body, headers=_bearer(99))
        assert resp.status_code == 200
        mock_update.assert_called_once()


def _bearer(player_id):
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id, 'user')}"}


class TestProfileUpdateAuth:
    """/api/profile/update requires a session and only edits the caller's own profile."""

    _BODY = {
        "playerId": "99",
        "currentPassword": "OldPass1!LongPwd",
        "fullName": "New Name",
        "nickName": "nn",
        "email": "old@test.com",
        "mfaEnabled": False,
    }

    def _player(self, **extra):
        return {
            "playerId": 99, "email": "old@test.com", "password_hash": "hash",
            "role": "user", "failed_login_attempts": 0, "lockout_until": None,
            **extra,
        }

    def test_no_token_returns_401(self):
        resp = client.post("/api/profile/update", json=self._BODY)
        assert resp.status_code == 401

    def test_garbage_cookie_returns_401_not_500(self):
        resp = client.post("/api/profile/update", json=self._BODY,
                           cookies={"session_token": "garbage"})
        assert resp.status_code == 401

    def test_token_for_other_player_returns_403(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()) as get_p,              patch("routes.auth_routes.verify_password", return_value=True) as vp,              patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(7))
        assert resp.status_code == 403
        assert resp.json() == {"error": "Forbidden."}
        get_p.assert_not_called()
        vp.assert_not_called()
        upd.assert_not_called()

    def test_matching_token_and_correct_password_returns_200(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()),              patch("routes.auth_routes.verify_password", return_value=True),              patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 200
        assert resp.json() == {"message": "Profile updated successfully!"}
        upd.assert_called_once()

    def test_cookie_only_auth_works(self):
        from services.session_service import create_token
        with patch("services.db_service.get_player_by_id", return_value=self._player()),              patch("routes.auth_routes.verify_password", return_value=True),              patch("routes.auth_routes.update_player_profile"):
            resp = client.post("/api/profile/update", json=self._BODY,
                               cookies={"session_token": create_token(99, "user")})
        assert resp.status_code == 200

    def test_success_resets_failure_counters_in_same_write(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=3)),              patch("routes.auth_routes.verify_password", return_value=True),              patch("routes.auth_routes.update_player_profile") as upd:
            client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        upd.assert_called_once()
        sent = upd.call_args[0][1]
        assert sent["failed_login_attempts"] == 0
        assert sent["lockout_until"] is None

    def test_wrong_password_increments_failed_attempts(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=1)),              patch("routes.auth_routes.verify_password", return_value=False),              patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 401
        assert resp.json()["error"] == "Incorrect current password."
        upd.assert_called_once_with("99", {"failed_login_attempts": 2})

    def test_nan_failed_attempts_treated_as_zero(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=float("nan"))),              patch("routes.auth_routes.verify_password", return_value=False),              patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 401
        upd.assert_called_once_with("99", {"failed_login_attempts": 1})

    def test_fifth_wrong_password_locks_account_and_blocks_correct_password_and_login(self):
        store = self._player(failed_login_attempts=4)

        def fake_update(pid, updates):
            store.update(updates)

        with patch("services.db_service.get_player_by_id", side_effect=lambda pid: dict(store)),              patch("routes.auth_routes.get_player_by_email", side_effect=lambda e: dict(store)),              patch("routes.auth_routes.verify_password", side_effect=lambda p, h: p == "OldPass1!LongPwd"),              patch("routes.auth_routes.update_player_profile", side_effect=fake_update):
            bad = client.post("/api/profile/update",
                              json={**self._BODY, "currentPassword": "wrong"}, headers=_bearer(99))
            assert bad.status_code == 429
            assert "locked" in bad.json()["error"].lower()
            assert store["lockout_until"] > time.time()

            good = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
            assert good.status_code == 429
            assert "locked" in good.json()["error"].lower()

            login = client.post("/api/login",
                                json={"email": "old@test.com", "password": "OldPass1!LongPwd"})
            assert login.status_code == 429
            assert "locked" in login.json()["error"].lower()


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


# ---------------------------------------------------------------------------
# IP-based rate limiting on login / set_password / check_player
# ---------------------------------------------------------------------------

def _login_unknown(c, email="nobody@example.com", headers=None):
    return c.post("/api/login", json={"email": email, "password": "x"}, headers=headers or {})


def test_login_rate_limited_after_five_attempts_per_ip(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    codes = [_login_unknown(c).status_code for _ in range(5)]
    assert codes == [401] * 5
    resp = _login_unknown(c)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    assert "error" in resp.json()


def test_login_429_body_and_retry_after_are_consistent(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c)
    resp = _login_unknown(c)
    retry = resp.headers["Retry-After"]
    assert retry.isdigit() and int(retry) >= 1
    assert resp.json()["error"].startswith("Too many requests")
    assert retry in resp.json()["error"]


def test_valid_logins_succeed_for_attempts_one_to_five():
    player = {
        "playerId": 1, "email": "ok@example.com", "fullName": "Ok",
        "password_hash": "hash", "role": "user",
    }
    c = TestClient(app)
    with patch("routes.auth_routes.get_player_by_email", return_value=player), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes._is_legacy_sha256", return_value=False), \
         patch("routes.auth_routes.update_player_profile"):
        codes = [c.post("/api/login", json={"email": "ok@example.com", "password": "x"}).status_code
                 for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_login_allowed_again_after_window_passes(monkeypatch):
    from routes import auth_routes
    now = [1000.0]
    monkeypatch.setattr(auth_routes._login_limiter, "_clock", lambda: now[0])
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    assert [_login_unknown(c).status_code for _ in range(5)] == [401] * 5
    assert _login_unknown(c).status_code == 429
    now[0] += auth_routes._login_limiter.window_seconds + 1
    assert _login_unknown(c).status_code == 401


def test_set_password_shares_the_login_bucket(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c)
    resp = c.post("/api/set_password", json={
        "email": "a@b.com", "password": "Aa1!aaaaaaaaaa", "confirm_password": "Aa1!aaaaaaaaaa"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_spoofed_left_forwarded_for_entries_do_not_evade_limit(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    codes = []
    for i in range(6):
        codes.append(_login_unknown(c, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"}).status_code)
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_different_client_ips_have_separate_buckets(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c, headers={"X-Forwarded-For": "203.0.113.7"})
    assert _login_unknown(c, headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 401


def test_check_player_has_its_own_looser_bucket(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c)                        # exhaust the login bucket
    assert c.get("/api/check_player", params={"email": "a@b.com"}).status_code == 200
    codes = [c.get("/api/check_player", params={"email": "a@b.com"}).status_code for _ in range(40)]
    assert 429 in codes
    assert codes[:20] == [200] * 20              # lookup allowance (30) is well above login's (5)


def test_limit_follows_limiter_max_requests(monkeypatch):
    from routes import auth_routes
    monkeypatch.setattr(auth_routes._login_limiter, "max_requests", 2)
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    c = TestClient(app)
    assert [_login_unknown(c).status_code for _ in range(3)] == [401, 401, 429]


def test_env_int_parses_valid_value(monkeypatch):
    from routes.auth_routes import _env_int
    monkeypatch.setenv("X_TEST_LIMIT", "7")
    assert _env_int("X_TEST_LIMIT", 5) == 7


def test_env_int_invalid_string_falls_back_to_default(monkeypatch):
    from routes.auth_routes import _env_int
    monkeypatch.setenv("X_TEST_LIMIT", "lots")
    assert _env_int("X_TEST_LIMIT", 5) == 5


def test_env_int_zero_and_negative_clamp_to_one(monkeypatch):
    from routes.auth_routes import _env_int
    monkeypatch.setenv("X_TEST_LIMIT", "0")
    assert _env_int("X_TEST_LIMIT", 5) == 1
    monkeypatch.setenv("X_TEST_LIMIT", "-3")
    assert _env_int("X_TEST_LIMIT", 5) == 1


def _profile_update(c):
    return c.post("/api/profile/update",
                  json={"playerId": "1", "currentPassword": "wrong"},
                  headers=_bearer(1))


def test_profile_update_rate_limited_after_five_attempts_sharing_login_bucket(monkeypatch):
    monkeypatch.setattr("services.db_service.get_player_by_id", lambda pid: None)
    c = TestClient(app)
    assert [_profile_update(c).status_code for _ in range(5)] == [404] * 5
    resp = _profile_update(c)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    assert "error" in resp.json()
    # shared bucket: login is now blocked too
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    assert _login_unknown(c).status_code == 429
