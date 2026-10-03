"""Validation contract for the Pydantic request models and the 422 they produce."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from main import app
from routes.models import (
    LoginRequest, MockDraftPickRequest, NewSeasonRequest, SetPasswordRequest, UpdateProfileRequest,
)

client = TestClient(app)
GOOD_PW = "Abcdefgh1!xy"


def test_valid_models_pass_untouched():
    assert LoginRequest(email="a@b.com", password="x").email == "a@b.com"
    assert LoginRequest(email=" A@B.com ", password="x").email == " A@B.com "  # routes strip/lower
    r = SetPasswordRequest(email="a@b.com", password=GOOD_PW, confirm_password=GOOD_PW)
    assert r.password == GOOD_PW
    assert UpdateProfileRequest(playerId="1", currentPassword="x").email == ""
    assert UpdateProfileRequest(playerId="1", currentPassword="x", email="a@b.co", newPassword=GOOD_PW)
    assert NewSeasonRequest(season=2026).playerIds == []


@pytest.mark.parametrize("email", ["", "nope", "a@b", "a b@c.com", "@c.com"])
def test_bad_email_rejected(email):
    with pytest.raises(ValidationError):
        LoginRequest(email=email, password="x")
    with pytest.raises(ValidationError):
        SetPasswordRequest(email=email, password=GOOD_PW, confirm_password=GOOD_PW)


def test_update_profile_email_optional_but_validated():
    assert UpdateProfileRequest(playerId="1", currentPassword="x", email="").email == ""
    with pytest.raises(ValidationError):
        UpdateProfileRequest(playerId="1", currentPassword="x", email="bad")


def test_password_length_is_left_to_routes_but_capped():
    # Routes own length/complexity so lockout accounting is not bypassed by a 422.
    assert LoginRequest(email="a@b.com", password="short").password == "short"
    assert SetPasswordRequest(email="a@b.com", password="weak", confirm_password="weak").password == "weak"
    assert UpdateProfileRequest(playerId="1", currentPassword="x", newPassword="").newPassword == ""
    with pytest.raises(ValidationError):
        LoginRequest(email="a@b.com", password="x" * 257)


@pytest.mark.parametrize("season", [1999, 3001, -1])
def test_season_bounds(season):
    with pytest.raises(ValidationError):
        NewSeasonRequest(season=season)
    with pytest.raises(ValidationError):
        MockDraftPickRequest(season=season, availableTeams=["KC"])


def test_mock_draft_counters_non_negative():
    with pytest.raises(ValidationError):
        MockDraftPickRequest(season=2026, availableTeams=["KC"], wildcardsSoFar=-1)


def test_login_endpoint_returns_422_for_malformed_email():
    r = client.post("/api/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422


def test_set_password_endpoint_returns_422_for_malformed_email():
    r = client.post("/api/set_password",
                    json={"email": "bad", "password": GOOD_PW, "confirm_password": GOOD_PW})
    assert r.status_code == 422
