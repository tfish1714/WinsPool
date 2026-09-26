"""Tests for simulate_pool_finish_odds and the top-2 fields on /api/profile/portfolio."""
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app
from services.analysis_service import simulate_pool_finish_odds
from services.session_service import require_auth

STRONG = {"projected_wins": 12.0, "std_dev": 1.5}
WEAK = {"projected_wins": 5.0, "std_dev": 1.5}
MID = {"projected_wins": 8.5, "std_dev": 2.0}


class TestSimulatePoolFinishOdds:
    def test_stronger_player_wins(self):
        r = simulate_pool_finish_odds(
            {1: ["A", "B"], 2: ["C", "D"]},
            {"A": STRONG, "B": STRONG, "C": WEAK, "D": WEAK}, {},
            top_n=1, n_sims=5000)
        assert r[1]["win_prob"] > 0.99
        assert r[2]["win_prob"] < 0.01
        assert r[1]["top_n_prob"] > 0.99 and r[2]["top_n_prob"] < 0.01

    def test_fair_tie_break_three_identical(self):
        teams = {1: ["A"], 2: ["B"], 3: ["C"]}
        proj = {"A": MID, "B": MID, "C": MID}
        r = simulate_pool_finish_odds(teams, proj, {}, n_sims=20000, seed=7)
        for pid in (1, 2, 3):
            assert r[pid]["win_prob"] == pytest.approx(1 / 3, abs=0.05)
            assert r[pid]["top_n_prob"] == pytest.approx(2 / 3, abs=0.05)

    def test_completed_season_deterministic(self):
        rec = {"A": {"wins": 12, "losses": 5, "ties": 0},
               "B": {"wins": 10, "losses": 7, "ties": 0},
               "C": {"wins": 4, "losses": 13, "ties": 0}}
        proj = {t: MID for t in rec}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"], 3: ["C"]}, proj, rec, n_sims=500)
        assert r[1] == {"top_n_prob": 1.0, "win_prob": 1.0, "expected_rank": 1.0}
        assert r[2] == {"top_n_prob": 1.0, "win_prob": 0.0, "expected_rank": 2.0}
        assert r[3] == {"top_n_prob": 0.0, "win_prob": 0.0, "expected_rank": 3.0}

    def test_reproducible(self):
        args = ({1: ["A"], 2: ["B"], 3: ["C"]}, {"A": MID, "B": MID, "C": WEAK}, {})
        assert simulate_pool_finish_odds(*args, seed=3) == simulate_pool_finish_odds(*args, seed=3)

    def test_zero_teams_player(self):
        r = simulate_pool_finish_odds({1: ["A"], 2: [], 3: ["B"]},
                                      {"A": STRONG, "B": MID}, {}, n_sims=1000)
        assert r[2]["win_prob"] == 0.0
        assert r[2]["top_n_prob"] == 0.0
        assert r[2]["expected_rank"] == 3.0

    def test_two_player_pool_top2_is_one(self):
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, {"A": STRONG, "B": WEAK}, {})
        assert r[1]["top_n_prob"] == 1.0 and r[2]["top_n_prob"] == 1.0

    def test_wins_capped_by_remaining_games(self):
        # A is 12-3 (2 left): can end at most 14. B is 13-4 complete with huge sd elsewhere.
        rec = {"A": {"wins": 12, "losses": 3, "ties": 0},
               "B": {"wins": 15, "losses": 2, "ties": 0}}
        proj = {"A": {"projected_wins": 17.0, "std_dev": 10.0},
                "B": {"projected_wins": 15.0, "std_dev": 0.0}}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, proj, rec, top_n=1, n_sims=2000)
        assert r[1]["win_prob"] == 0.0 and r[2]["win_prob"] == 1.0
        # and A can reach the cap: with B at 13 A can beat it only by winning both
        rec["B"] = {"wins": 13, "losses": 4, "ties": 0}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, proj, rec, top_n=1, n_sims=2000)
        assert r[1]["win_prob"] > 0.0

    def test_mid_season_projection_scaled_by_remaining_games(self):
        # A is 6-3 with 8 left and projected 10 wins: expected final is
        # 6 + 8/17 * 10 = 10.7 (not 10, and not 6 + 10 = 16 capped at 14).
        # B is a completed 10-7 season, so A's win odds pin down A's mean:
        # ~0.92 when scaled, ~0.5 if the projection were used unscaled,
        # ~1.0 if it were added on top of current wins.
        rec = {"A": {"wins": 6, "losses": 3, "ties": 0},
               "B": {"wins": 10, "losses": 7, "ties": 0}}
        proj = {"A": {"projected_wins": 10.0, "std_dev": 0.0},
                "B": {"projected_wins": 10.0, "std_dev": 0.0}}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, proj, rec, top_n=1, n_sims=20000)
        assert 0.85 < r[1]["win_prob"] < 0.99
        # never exceeds wins + remaining: A cannot pass a completed 14-win team
        rec["B"] = {"wins": 14, "losses": 3, "ties": 0}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, proj, rec, top_n=1, n_sims=20000)
        assert r[1]["win_prob"] < 0.001

    def test_sd_floor_when_std_dev_zero_and_games_remain(self):
        # A is 15-1 with one game left, std_dev 0: unfloored, A would end
        # at exactly 15 + 1/17 * 1 > 15 and beat a completed 15-win B every time.
        rec = {"A": {"wins": 15, "losses": 1, "ties": 0},
               "B": {"wins": 15, "losses": 2, "ties": 0}}
        proj = {"A": {"projected_wins": 1.0, "std_dev": 0.0},
                "B": {"projected_wins": 15.0, "std_dev": 0.0}}
        r = simulate_pool_finish_odds({1: ["A"], 2: ["B"]}, proj, rec, top_n=1, n_sims=20000)
        assert 0.3 < r[1]["win_prob"] < 0.99


@pytest.fixture
def auth_player():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


PROJ = {
    "KC": {"projected_wins": 10.5, "std_dev": 2.0},
    "NYJ": {"projected_wins": 8.0, "std_dev": 2.0},
    "CAR": {"projected_wins": 6.0, "std_dev": 2.0},
    "BUF": {"projected_wins": 11.0, "std_dev": 2.0},
    "DAL": {"projected_wins": 9.0, "std_dev": 2.0},
    "MIA": {"projected_wins": 7.0, "std_dev": 2.0},
    "DEN": {"projected_wins": 9.5, "std_dev": 2.0},
    "LAR": {"projected_wins": 8.5, "std_dev": 2.0},
    "SEA": {"projected_wins": 7.5, "std_dev": 2.0},
}


def _dr():
    rows = []
    for pid, teams in {3: ["KC", "NYJ", "CAR"], 4: ["BUF", "DAL", "MIA"], 5: ["DEN", "LAR", "SEA"]}.items():
        rows += [{"season": 2026, "team": t, "playerId": pid} for t in teams]
    return pd.DataFrame(rows)


def _get(dr, proj, config, standings=None):
    load = (standings, None, pd.DataFrame(), None, pd.DataFrame(), dr, pd.DataFrame())
    with patch("routes.api_routes.load_data", return_value=load), \
         patch("services.data_service.get_active_season", return_value=2026), \
         patch("routes.api_routes.get_season_projection_legacy_shape", return_value=proj), \
         patch("routes.api_routes.get_config_settings", return_value=config):
        return TestClient(app).get("/api/profile/portfolio")


class TestPortfolioRouteOdds:
    def test_normal_has_odds(self, auth_player):
        b = _get(_dr(), PROJ, {"draft_active": False}).json()
        assert b["available"] is True
        assert 0 <= b["top2_prob"] <= 1 and 0 <= b["win_prob"] <= 1
        assert b["pool_size"] == 3
        assert 1 <= b["expected_rank"] <= 3

    def test_uses_standings_records(self, auth_player):
        st = pd.DataFrame([{"season": 2026, "team": t, "wins": 17 if t in ("KC", "NYJ", "CAR") else 0,
                            "losses": 0, "ties": 0} for t in PROJ])
        b = _get(_dr(), PROJ, {"draft_active": False}, standings=st).json()
        assert b["top2_prob"] == 1.0 and b["win_prob"] == 1.0

    def test_nan_standings_row_treated_as_zero(self, auth_player):
        st = pd.DataFrame([{"season": 2026, "team": "KC", "wins": float("nan"),
                            "losses": float("nan"), "ties": float("nan")}])
        b = _get(_dr(), PROJ, {"draft_active": False}, standings=st).json()
        assert b["available"] is True
        assert b["top2_prob"] is not None and 0 <= b["top2_prob"] <= 1
        # player 3 (24.5 projected) sits between players 4 (27.5) and 5 (25.5 -> lower)
        # rather than being ranked last as a NaN total would be
        assert 1.5 < b["expected_rank"] < 2.6

    @pytest.mark.parametrize("dr,proj,cfg,reason", [
        (_dr(), PROJ, {"draft_active": True}, "draft_in_progress"),
        (_dr().assign(playerId=99), PROJ, {"draft_active": False}, "no_teams"),
        (_dr(), {}, {"draft_active": False}, "no_projections"),
    ])
    def test_unavailable(self, auth_player, dr, proj, cfg, reason):
        b = _get(dr, proj, cfg).json()
        assert b["available"] is False and b["reason"] == reason
        assert b.get("top2_prob") is None and b.get("win_prob") is None
