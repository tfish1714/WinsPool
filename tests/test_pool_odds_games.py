"""Tests for simulate_pool_finish_odds_from_games and per-game odds on /api/profile/portfolio."""
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app
from services.analysis_service import simulate_pool_finish_odds_from_games as sim
from services.session_service import require_auth


class TestSimulateFromGames:
    def test_single_game_probability(self):
        r = sim({1: ["A"], 2: ["B"]}, {}, [("A", "B", 0.7)], n_sims=20000, seed=1, top_n=1)
        assert abs(r[1]["win_prob"] - 0.7) < 0.02
        assert abs(r[2]["win_prob"] - 0.3) < 0.02

    def test_game_sampled_once_for_both_sides(self):
        # Player 1 holds both sides: exactly 1 win from the game every sim (total 1).
        # Player 2 sits at 1.5 from base wins, so player 2 must always win.
        # An independent double sample would give player 1 totals of 0 or 2.
        r = sim({1: ["A", "B"], 2: ["C"]}, {"C": 1.5}, [("A", "B", 0.5)],
                n_sims=5000, seed=2, top_n=1)
        assert r[1]["win_prob"] == 0.0
        assert r[2]["win_prob"] == 1.0

    def test_base_wins_shift_odds(self):
        r = sim({1: ["A"], 2: ["B"]}, {"A": 2}, [("B", "X", 0.5)], n_sims=2000, top_n=1)
        assert r[1]["win_prob"] == 1.0 and r[2]["win_prob"] == 0.0

    def test_no_remaining_games_exact(self):
        r = sim({1: ["A"], 2: ["B"], 3: ["C"]}, {"A": 10, "B": 8, "C": 5}, [], n_sims=200, top_n=1)
        assert (r[1]["win_prob"], r[2]["win_prob"], r[3]["win_prob"]) == (1.0, 0.0, 0.0)
        assert r[1]["top_n_prob"] == 1.0 and r[3]["top_n_prob"] == 0.0

    def test_same_seed_same_output(self):
        args = ({1: ["A"], 2: ["B"]}, {}, [("A", "B", 0.6)])
        assert sim(*args, n_sims=500, seed=5) == sim(*args, n_sims=500, seed=5)

    def test_tie_break_is_fair(self):
        r = sim({1: ["A"], 2: ["B"], 3: ["C"]}, {"A": 5, "B": 5, "C": 5}, [],
                n_sims=30000, seed=3, top_n=1)
        for pid in (1, 2, 3):
            assert abs(r[pid]["win_prob"] - 1 / 3) < 0.02

    def test_player_without_teams_zero_totals(self):
        r = sim({1: ["A"], 2: []}, {"A": 3}, [], n_sims=100, top_n=1)
        assert r[1]["win_prob"] == 1.0 and r[2]["win_prob"] == 0.0

    def test_few_players_top_n_is_one(self):
        r = sim({1: ["A"], 2: ["B"]}, {"A": 0, "B": 9}, [], n_sims=100, top_n=2)
        assert r[1]["top_n_prob"] == 1.0 and r[2]["top_n_prob"] == 1.0

    def test_empty_pool(self):
        assert sim({}, {}, []) == {}


@pytest.fixture
def auth_player():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


PROJ = {t: {"projected_wins": 8.0 + i * 0.3, "std_dev": 2.0}
        for i, t in enumerate(["KC", "NYJ", "CAR", "BUF", "DAL", "MIA", "DEN", "LAR", "SEA"])}


def _dr():
    rows = []
    for pid, teams in {3: ["KC", "NYJ", "CAR"], 4: ["BUF", "DAL", "MIA"], 5: ["DEN", "LAR", "SEA"]}.items():
        rows += [{"season": 2026, "team": t, "playerId": pid} for t in teams]
    return pd.DataFrame(rows)


def _games(rows):
    return pd.DataFrame([
        {"season": 2026, "week": w, "game_type": gt, "home_team": h, "away_team": a, "result": res}
        for (w, gt, h, a, res) in rows])


def _get(games, preds):
    load = (None, None, games, None, pd.DataFrame(), _dr(), pd.DataFrame())
    with patch("routes.api_routes.load_data", return_value=load), \
         patch("services.data_service.get_active_season", return_value=2026), \
         patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), \
         patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}), \
         patch("services.cache_service.get_game_predictions", return_value=preds):
        return TestClient(app).get("/api/profile/portfolio").json()


class TestRoutePerGame:
    def test_per_game_when_predictions_exist(self, auth_player):
        games = _games([(2, "REG", "KC", "BUF", None), (3, "REG", "NYJ", "DAL", None),
                        (1, "REG", "CAR", "MIA", 3.0)])  # played: never counted
        preds = {"W02_KC_BUF": {"pred_prob": 0.6}, "W03_NYJ_DAL": {"pred_prob": 0.4},
                 "W01_CAR_MIA": {"pred_prob": 0.9}}
        b = _get(games, preds)
        assert b["odds_basis"] == "per_game"
        assert b["games_remaining"] == 2
        assert 0 <= b["top2_prob"] <= 1

    def test_falls_back_without_predictions(self, auth_player):
        games = _games([(2, "REG", "KC", "BUF", None)])
        b = _get(games, {})
        assert b["odds_basis"] == "team_projection"
        assert b["games_remaining"] is None
        assert b["top2_prob"] is not None and b["pool_size"] == 3

    def test_missing_prediction_uses_half_and_is_counted(self, auth_player):
        games = _games([(2, "REG", "KC", "BUF", None), (3, "REG", "NYJ", "DAL", None),
                        (4, "PRE", "CAR", "MIA", None)])  # non-REG ignored
        b = _get(games, {"W02_KC_BUF": {"pred_prob": 0.6}})
        assert b["odds_basis"] == "per_game"
        assert b["games_remaining"] == 2

    def test_unavailable_has_null_basis(self, auth_player):
        load = (None, None, pd.DataFrame(), None, pd.DataFrame(), _dr().assign(playerId=99), pd.DataFrame())
        with patch("routes.api_routes.load_data", return_value=load), \
             patch("services.data_service.get_active_season", return_value=2026), \
             patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}):
            b = TestClient(app).get("/api/profile/portfolio").json()
        assert b["odds_basis"] is None and b["games_remaining"] is None
