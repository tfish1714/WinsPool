import pytest
import re
from fastapi.testclient import TestClient
import os
import sys

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
