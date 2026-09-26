"""Tests for the unified /player/{id} page, its redirects, and /api/players/{id}/portfolio."""
import pandas as pd
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from services.session_service import create_token, require_auth

client = TestClient(app, follow_redirects=False)


def _load():
    standings = pd.DataFrame([{"season": 2022, "team": "KC", "wins": 14, "losses": 3, "ties": 0}])
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        {"playerId": 2, "fullName": "Bob Jones", "nickName": "Bob"},
    ])
    draft = pd.DataFrame([{"playerId": 1, "season": 2022, "draftPick": 1, "team": "KC"}])
    games = pd.DataFrame([{"season": 2022}])
    return standings, pd.DataFrame(), games, players, pd.DataFrame(), draft, pd.DataFrame()


def _page_patches():
    return (
        patch("routes.history_routes.load_data", return_value=_load()),
        patch("services.analysis_service.load_data", return_value=_load()),
        patch("services.analysis_service.get_season_projection_legacy_shape",
              return_value={"KC": {"projected_wins": 12.0}}),
    )


def _get(path):
    p1, p2, p3 = _page_patches()
    with p1, p2, p3:
        return client.get(path)


class TestPlayerPage:
    def test_player_with_history_renders(self):
        r = _get("/player/1")
        assert r.status_code == 200
        assert "Alice Smith" in r.text

    def test_player_without_history_renders_zeroed_career(self):
        r = _get("/player/2")
        assert r.status_code == 200
        assert "Bob Jones" in r.text

    def test_unknown_player_404(self):
        assert _get("/player/999").status_code == 404

    def test_own_page_block_hidden_by_default_with_markers(self):
        r = _get("/player/2")
        html = r.text
        assert 'id="own-page-only"' in html
        idx = html.index('id="own-page-only"')
        tag_start = html.rfind("<", 0, idx)
        tag_end = html.index(">", idx)
        assert "hidden" in html[tag_start:tag_end]
        assert 'data-player-id="2"' in html
        # security form and pool placeholder live inside the hidden block
        assert html.index('id="profile-form"') > idx
        assert html.index('id="pool-status-card"') > idx

    def test_page_keeps_profile_form_ids(self):
        html = _get("/player/1").text
        for i in ("profile-form", "full-name", "nickname", "email", "mfa-enabled",
                  "current-password", "new-password", "confirm-new-password"):
            assert f'id="{i}"' in html

    def test_history_url_redirects_301(self):
        r = client.get("/history/player/5")
        assert r.status_code == 301
        assert r.headers["location"] == "/player/5"


class TestProfileRedirect:
    def test_with_cookie_redirects_to_player_page(self):
        token = create_token(player_id=7, role="user")
        c = TestClient(app, follow_redirects=False)
        c.cookies.set("session_token", token)
        r = c.get("/profile")
        assert r.status_code in (302, 307)
        assert r.headers["location"] == "/player/7"

    def test_without_cookie_serves_fallback_script(self):
        r = TestClient(app, follow_redirects=False).get("/profile")
        assert r.status_code == 200
        assert "nfl_wins_my_player_id" in r.text
        assert "/wins-pool" in r.text

    def test_invalid_cookie_serves_fallback(self):
        c = TestClient(app, follow_redirects=False)
        c.cookies.set("session_token", "garbage")
        r = c.get("/profile")
        assert r.status_code == 200
        assert "nfl_wins_my_player_id" in r.text


# ---- /api/players/{id}/portfolio ------------------------------------------------

PROJ = {
    "KC": {"projected_wins": 10.5, "std_dev": 2.0},
    "NYJ": {"projected_wins": 8.0, "std_dev": 2.0},
    "CAR": {"projected_wins": 6.0, "std_dev": 2.0},
}


def _api_load():
    standings = pd.DataFrame([
        {"season": 2026, "team": t, "wins": 0, "losses": 0, "ties": 0} for t in PROJ])
    draft = pd.DataFrame([
        {"playerId": 3, "season": 2026, "draftPick": 1, "team": "KC"},
        {"playerId": 3, "season": 2026, "draftPick": 2, "team": "NYJ"},
        {"playerId": 4, "season": 2026, "draftPick": 3, "team": "CAR"},
    ])
    games = pd.DataFrame([{"season": 2026, "game_type": "REG", "week": 1, "home_team": "KC",
                           "away_team": "NYJ", "result": None}])
    return standings, pd.DataFrame(), games, pd.DataFrame(), pd.DataFrame(), draft, pd.DataFrame()


def _portfolio(path, caller_role="player", draft_active=False, caller_id="4"):
    app.dependency_overrides[require_auth] = lambda: {"sub": caller_id, "role": caller_role}
    try:
        with patch("routes.api_routes.load_data", return_value=_api_load()), \
             patch("services.data_service.get_active_season", return_value=2026), \
             patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), \
             patch("routes.api_routes.get_config_settings", return_value={"draft_active": draft_active}), \
             patch("services.cache_service.get_game_predictions", return_value={}):
            return TestClient(app).get(path)
    finally:
        app.dependency_overrides.pop(require_auth, None)


class TestPlayerPortfolioApi:
    def test_other_player_outlook_available_and_same_shape_as_own(self):
        other = _portfolio("/api/players/3/portfolio").json()
        own = _portfolio("/api/profile/portfolio", caller_id="3").json()
        assert other["available"] is True
        assert other["teams"] and len(other["teams"]) == 2
        assert set(other.keys()) == set(own.keys())
        assert other["expected_wins"] == own["expected_wins"]

    def test_no_paid_keys(self):
        body = _portfolio("/api/players/3/portfolio").json()
        assert not [k for k in body if "paid" in k.lower()]

    def test_unknown_player_no_teams(self):
        r = _portfolio("/api/players/999/portfolio")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False and body["reason"] == "no_teams"

    def test_draft_gate_for_non_admin(self):
        body = _portfolio("/api/players/3/portfolio", draft_active=True).json()
        assert body["available"] is False and body["reason"] == "draft_in_progress"

    def test_draft_gate_bypassed_for_admin(self):
        body = _portfolio("/api/players/3/portfolio", caller_role="admin", draft_active=True).json()
        assert body["available"] is True

    def test_requires_auth(self):
        assert TestClient(app).get("/api/players/3/portfolio").status_code == 401
