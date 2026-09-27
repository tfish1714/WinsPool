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


class TestSimulateStats:
    def test_certain_wins_are_deterministic(self):
        # A at 2 wins with 3 certain remaining wins -> exactly 5 every sim.
        games = [("A", "X", 1.0), ("A", "Y", 1.0), ("Z", "A", 0.0)]
        r = sim({1: ["A"]}, {"A": 2}, games, n_sims=500)[1]
        assert r["expected_wins"] == 5.0
        assert r["p5"] == r["p95"] == 5.0
        assert r["std_dev"] == 0.0
        assert r["teams"]["A"] == {"wins_to_date": 2.0, "expected_wins": 5.0,
                                 "std_dev": 0.0, "playoff_prob": 0.0}

    def test_team_with_no_remaining_games_keeps_base(self):
        r = sim({1: ["A", "B"]}, {"A": 4}, [("B", "X", 1.0)], n_sims=100)[1]
        assert r["teams"]["A"]["expected_wins"] == 4.0
        assert r["teams"]["A"]["wins_to_date"] == 4.0
        assert r["teams"]["B"]["wins_to_date"] == 0.0  # missing from base_wins
        assert r["teams"]["B"]["expected_wins"] == 1.0
        assert r["expected_wins"] == 5.0

    def test_playoff_prob_extremes(self):
        r = sim({1: ["A", "B"]}, {"A": 10, "B": 1}, [("B", "X", 1.0)], n_sims=100)[1]
        assert r["teams"]["A"]["playoff_prob"] == 1.0
        assert r["teams"]["B"]["playoff_prob"] == 0.0

    def test_playoff_threshold_parameter(self):
        r = sim({1: ["A"]}, {"A": 3}, [], n_sims=50, playoff_wins_threshold=3)[1]
        assert r["teams"]["A"]["playoff_prob"] == 1.0

    def test_any_and_expected_playoff_teams(self):
        # A is at 10 (always in); B needs its coin flip to reach 10.
        r = sim({1: ["A", "B"]}, {"A": 10, "B": 9.0}, [("B", "X", 0.5)],
                n_sims=20000, seed=4, playoff_wins_threshold=10)[1]
        assert r["playoff_prob_any"] == 1.0
        assert abs(r["expected_playoff_teams"] - 1.5) < 0.02
        assert abs(r["teams"]["B"]["playoff_prob"] - 0.5) < 0.02

    def test_none_when_no_team_can_qualify(self):
        r = sim({1: ["A", "B"]}, {}, [], n_sims=10)[1]
        assert r["playoff_prob_any"] == 0.0 and r["expected_playoff_teams"] == 0.0

    def test_stats_reproducible_and_existing_keys_intact(self):
        args = ({1: ["A"], 2: ["B"]}, {}, [("A", "B", 0.6)])
        a, b = sim(*args, n_sims=500, seed=5), sim(*args, n_sims=500, seed=5)
        assert a == b
        assert {"top_n_prob", "win_prob", "expected_rank"} <= set(a[1])
        assert abs(a[1]["win_prob"] - 0.6) < 0.08


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
         patch("routes.api_routes.get_frozen_preseason_projection", return_value=PROJ), \
         patch("routes.api_routes.is_draft_active_fail_closed", return_value=False), \
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

    def test_pred_prob_is_home_win_probability_end_to_end(self):
        # A holds the home team (KC), B the away team (BUF); the only remaining game
        # has pred_prob 0.99 (home win). Inverting the probability would flip these.
        dr = pd.DataFrame([{"season": 2026, "team": "KC", "playerId": 3},
                           {"season": 2026, "team": "BUF", "playerId": 4}])
        games = _games([(2, "REG", "KC", "BUF", None)])
        preds = {"W02_KC_BUF": {"pred_prob": 0.99}}
        load = (None, None, games, None, pd.DataFrame(), dr, pd.DataFrame())

        def fetch(pid):
            app.dependency_overrides[require_auth] = lambda: {"sub": str(pid), "role": "player"}
            try:
                with patch("routes.api_routes.load_data", return_value=load), \
                     patch("services.data_service.get_active_season", return_value=2026), \
                     patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), \
                     patch("routes.api_routes.get_frozen_preseason_projection", return_value=PROJ), \
                     patch("routes.api_routes.is_draft_active_fail_closed", return_value=False), \
                     patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}), \
                     patch("services.cache_service.get_game_predictions", return_value=preds):
                    return TestClient(app).get("/api/profile/portfolio").json()
            finally:
                app.dependency_overrides.pop(require_auth, None)

        a, b = fetch(3), fetch(4)
        assert a["odds_basis"] == "per_game"
        assert a["win_prob"] > 0.9
        assert b["win_prob"] < 0.1
        assert a["win_prob"] > b["win_prob"] + 0.5

    def test_unavailable_has_null_basis(self, auth_player):
        load = (None, None, pd.DataFrame(), None, pd.DataFrame(), _dr().assign(playerId=99), pd.DataFrame())
        with patch("routes.api_routes.load_data", return_value=load), \
             patch("services.data_service.get_active_season", return_value=2026), \
             patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}):
            b = TestClient(app).get("/api/profile/portfolio").json()
        assert b["odds_basis"] is None and b["games_remaining"] is None


def _standings(wins):
    return pd.DataFrame([{"season": 2026, "team": t, "wins": w, "losses": 0, "ties": 0}
                         for t, w in wins.items()])


class TestRouteLiveOutlook:
    """per_game basis: outlook numbers come from wins to date + per-game probabilities."""

    def _fetch(self, games, preds, standings):
        load = (standings, None, games, None, pd.DataFrame(), _dr(), pd.DataFrame())
        with patch("routes.api_routes.load_data", return_value=load),              patch("services.data_service.get_active_season", return_value=2026),              patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), patch("routes.api_routes.get_frozen_preseason_projection", return_value=PROJ), patch("routes.api_routes.is_draft_active_fail_closed", return_value=False),              patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}),              patch("services.cache_service.get_game_predictions", return_value=preds):
            return TestClient(app).get("/api/profile/portfolio").json()

    def test_per_game_uses_wins_to_date_not_preseason(self, auth_player):
        # Player 3 owns KC (2 wins, 1 certain win left), NYJ (1 win, 1 certain win left), CAR (0, none left).
        games = _games([(5, "REG", "KC", "BUF", None), (5, "REG", "NYJ", "DAL", None)])
        preds = {"W05_KC_BUF": {"pred_prob": 1.0}, "W05_NYJ_DAL": {"pred_prob": 1.0}}
        b = self._fetch(games, preds, _standings({"KC": 2, "NYJ": 1, "CAR": 0}))
        preseason_sum = sum(PROJ[t]["projected_wins"] for t in ("KC", "NYJ", "CAR"))
        assert b["odds_basis"] == "per_game"
        assert b["expected_wins"] == 5.0
        assert b["expected_wins"] != round(preseason_sum, 2)
        assert b["floor"] == 5.0 and b["ceiling"] == 5.0 and b["std_dev"] == 0.0
        assert b["team_count"] == 3
        by_team = {t["team"]: t for t in b["teams"]}
        assert by_team["KC"]["wins_to_date"] == 2
        assert by_team["KC"]["projected_wins"] == 3.0
        assert by_team["KC"]["preseason_projected_wins"] == PROJ["KC"]["projected_wins"]
        assert by_team["CAR"]["projected_wins"] == 0.0
        assert set(by_team["KC"]) >= {"team", "projected_wins", "std_dev", "playoff_prob",
                                      "wins_to_date", "preseason_projected_wins"}
        assert b["playoff_prob_any"] == 0.0 and b["expected_playoff_teams"] == 0.0
        # existing top-level keys are still present
        assert b["games_remaining"] == 2 and b["pool_size"] == 3 and "top2_prob" in b

    def test_fallback_keeps_preseason_numbers(self, auth_player):
        games = _games([(5, "REG", "KC", "BUF", None)])
        b = self._fetch(games, {}, _standings({"KC": 2}))
        assert b["odds_basis"] == "team_projection"
        assert b["expected_wins"] == round(sum(PROJ[t]["projected_wins"] for t in ("KC", "NYJ", "CAR")), 2)
        assert all("wins_to_date" not in t for t in b["teams"])

    def test_simulation_failure_falls_back_to_preseason(self, auth_player):
        games = _games([(5, "REG", "KC", "BUF", None)])
        preds = {"W05_KC_BUF": {"pred_prob": 1.0}}
        with patch("routes.api_routes.analysis.simulate_pool_finish_odds_from_games",
                   side_effect=RuntimeError("boom")):
            b = self._fetch(games, preds, _standings({"KC": 2}))
        assert b["available"] is True
        assert b["expected_wins"] == round(sum(PROJ[t]["projected_wins"] for t in ("KC", "NYJ", "CAR")), 2)


class TestOutlookPreseasonFromFrozenSnapshot:
    """Task 2: preseason_projected_wins is the frozen number, not the daily-rewritten doc."""

    def _fetch(self, frozen):
        games = _games([(5, "REG", "KC", "BUF", None)])
        preds = {"W05_KC_BUF": {"pred_prob": 1.0}}
        load = (_standings({"KC": 2}), None, games, None, pd.DataFrame(), _dr(), pd.DataFrame())
        with patch("routes.api_routes.load_data", return_value=load), \
             patch("services.data_service.get_active_season", return_value=2026), \
             patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), \
             patch("routes.api_routes.get_frozen_preseason_projection", return_value=frozen), \
             patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}), \
             patch("services.cache_service.get_game_predictions", return_value=preds):
            return TestClient(app).get("/api/profile/portfolio").json()

    def test_preseason_projected_wins_from_snapshot(self, auth_player):
        b = self._fetch({"KC": {"projected_wins": 7.0}})
        by_team = {t["team"]: t for t in b["teams"]}
        assert by_team["KC"]["preseason_projected_wins"] == 7.0
        assert by_team["KC"]["preseason_projected_wins"] != PROJ["KC"]["projected_wins"]
        # simulation-based numbers unchanged
        assert by_team["KC"]["wins_to_date"] == 2 and by_team["KC"]["projected_wins"] == 3.0

    def test_team_missing_from_snapshot_falls_back_to_legacy_projection(self, auth_player):
        b = self._fetch({})
        by_team = {t["team"]: t for t in b["teams"]}
        assert by_team["KC"]["preseason_projected_wins"] == PROJ["KC"]["projected_wins"]


def test_player_profile_team_line_shows_preseason_projection():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "templates" / "player_profile.html").read_text(encoding="utf-8")
    assert "preseason_projected_wins" in src
    assert "(preseason " in src


class TestFrozenPreseasonHelper:
    def test_snapshot_first_then_preseason_predictions(self):
        from services import data_service as ds
        with patch.object(ds, "get_draft_snapshot_predictions", return_value={"KC": {"projected_wins": 1.0}}), \
             patch.object(ds, "get_preseason_predictions", return_value={"KC": {"projected_wins": 2.0}}):
            assert ds.get_frozen_preseason_projection(2026)["KC"]["projected_wins"] == 1.0
        with patch.object(ds, "get_draft_snapshot_predictions", return_value={}), \
             patch.object(ds, "get_preseason_predictions", return_value={"KC": {"projected_wins": 2.0}}):
            assert ds.get_frozen_preseason_projection(2026)["KC"]["projected_wins"] == 2.0


def test_frozen_preseason_read_failure_keeps_per_game_numbers(auth_player):
    games = _games([(5, "REG", "KC", "BUF", None)])
    preds = {"W05_KC_BUF": {"pred_prob": 1.0}}
    load = (_standings({"KC": 2}), None, games, None, pd.DataFrame(), _dr(), pd.DataFrame())
    with patch("routes.api_routes.load_data", return_value=load), \
         patch("services.data_service.get_active_season", return_value=2026), \
         patch("routes.api_routes.get_season_projection_legacy_shape", return_value=PROJ), \
         patch("routes.api_routes.get_frozen_preseason_projection", side_effect=RuntimeError("boom")), \
         patch("routes.api_routes.get_config_settings", return_value={"draft_active": False}), \
         patch("services.cache_service.get_game_predictions", return_value=preds):
        b = TestClient(app).get("/api/profile/portfolio").json()
    assert b["odds_basis"] == "per_game"
    by_team = {t["team"]: t for t in b["teams"]}
    assert by_team["KC"]["wins_to_date"] == 2 and by_team["KC"]["projected_wins"] == 3.0
    assert by_team["KC"]["preseason_projected_wins"] is None
