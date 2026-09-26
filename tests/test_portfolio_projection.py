"""Tests for the player portfolio projection widget (analysis_service + /api/profile/portfolio)."""
import math
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app
from services.analysis_service import compute_portfolio_projection
from services.session_service import require_auth

PROJ = {
    "KC": {"projected_wins": 10.5, "std_dev": 2.0},
    "NYJ": {"projected_wins": 8.0, "std_dev": 2.0},
    "CAR": {"projected_wins": 6.0, "std_dev": 2.0},
}


class TestComputePortfolioProjection:
    def test_three_teams(self):
        r = compute_portfolio_projection(PROJ, ["KC", "NYJ", "CAR"])
        assert r["team_count"] == 3
        assert r["expected_wins"] == pytest.approx(24.5)
        assert r["std_dev"] == pytest.approx(math.sqrt(12), abs=0.01)
        assert r["floor"] < r["expected_wins"] < r["ceiling"]
        probs = [t["playoff_prob"] for t in r["teams"]]
        assert all(0 < p < 1 for p in probs)
        assert probs == sorted(probs, reverse=True)
        assert max(probs) < r["playoff_prob_any"] < 1
        assert r["expected_playoff_teams"] == pytest.approx(sum(probs), abs=0.01)

    def test_zero_teams(self):
        r = compute_portfolio_projection(PROJ, [])
        assert r["teams"] == [] and r["team_count"] == 0
        assert r["expected_wins"] == 0 and r["std_dev"] == 0
        assert r["floor"] == 0 and r["ceiling"] == 0
        assert r["playoff_prob_any"] == 0 and r["expected_playoff_teams"] == 0

    def test_missing_team_skipped(self):
        r = compute_portfolio_projection(PROJ, ["KC", "ZZZ"])
        assert r["team_count"] == 1
        assert [t["team"] for t in r["teams"]] == ["KC"]

    def test_zero_sd_does_not_crash(self):
        r = compute_portfolio_projection({"KC": {"projected_wins": 11, "std_dev": 0}}, ["KC"])
        assert 0.9 < r["teams"][0]["playoff_prob"] <= 1

    def test_floor_clamped_at_zero(self):
        r = compute_portfolio_projection({"KC": {"projected_wins": 1, "std_dev": 6}}, ["KC"])
        assert r["floor"] == 0

    def test_ceiling_capped(self):
        r = compute_portfolio_projection({"KC": {"projected_wins": 17, "std_dev": 5}}, ["KC"])
        assert r["ceiling"] <= 17


@pytest.fixture
def auth_player():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture
def auth_admin():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_auth, None)


def _dr(teams=("KC", "NYJ", "CAR"), pid=3):
    return pd.DataFrame([{"season": 2026, "team": t, "playerId": pid} for t in teams])


def _load(dr):
    return (None, None, pd.DataFrame(), None, pd.DataFrame(), dr, pd.DataFrame())


def _get(dr, proj, config):
    with patch("routes.api_routes.load_data", return_value=_load(dr)), \
         patch("services.data_service.get_active_season", return_value=2026), \
         patch("routes.api_routes.get_season_projection_legacy_shape", return_value=proj), \
         patch("routes.api_routes.is_draft_active_fail_closed",
               return_value=bool(config.get("draft_active"))):
        return TestClient(app).get("/api/profile/portfolio")


class TestPortfolioRoute:
    def test_requires_auth(self):
        assert TestClient(app).get("/api/profile/portfolio").status_code in (401, 403)

    def test_draft_in_progress_non_admin(self, auth_player):
        r = _get(_dr(), PROJ, {"draft_active": True})
        assert r.status_code == 200
        b = r.json()
        assert b["available"] is False and b["reason"] == "draft_in_progress"
        assert "KC" not in r.text

    def test_draft_in_progress_admin_allowed(self, auth_admin):
        assert _get(_dr(), PROJ, {"draft_active": True}).json()["available"] is True

    def test_no_teams(self, auth_player):
        b = _get(_dr(pid=99), PROJ, {"draft_active": False}).json()
        assert b["available"] is False and b["reason"] == "no_teams"

    def test_no_projections(self, auth_player):
        b = _get(_dr(), {}, {"draft_active": False}).json()
        assert b["available"] is False and b["reason"] == "no_projections"

    def test_normal(self, auth_player):
        r = _get(_dr(), PROJ, {"draft_active": False})
        assert r.status_code == 200
        b = r.json()
        assert b["available"] is True and b["reason"] is None
        assert b["season"] == 2026 and len(b["teams"]) == 3
