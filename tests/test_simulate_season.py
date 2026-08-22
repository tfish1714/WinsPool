"""tests/test_simulate_season.py -- Tests for NNProjectionEngine.simulate_season()."""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def test_mc_constants_exist():
    from services.constants import MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT
    assert MC_MARGIN_STD == 13.0
    assert MC_EPA_SCALE == 0.004
    assert MC_EPA_RUSH_WEIGHT == 0.5


# ── Fixtures ──────────────────────────────────────────────────────────────────

from services.nn_feature_engine import FEATURE_COLUMNS as NN_FEATURE_COLUMNS


@pytest.fixture
def mock_engine():
    """NNProjectionEngine with mocked model services and synthetic 2-team profiles."""
    with patch("services.nn_projection_engine.NNPredictionService"), \
         patch("services.nn_projection_engine.XGBPredictionService"), \
         patch("services.nn_projection_engine.LRPredictionService"):
        from services.nn_projection_engine import NNProjectionEngine
        engine = NNProjectionEngine()

    # Override profiles: STRONG (Elo 1600, good EPA) vs WEAK (Elo 1400, bad EPA)
    engine._team_profiles = pd.DataFrame([
        {
            "team": "STRONG", "elo_pre": 1600.0,
            "off_pass_epa_roll": 0.15,  "off_rush_epa_roll": 0.05,
            "def_pass_epa_roll": 0.10,  "def_rush_epa_roll": 0.03,
            "off_early_roll": 0.10,     "def_early_roll": 0.08,
            "margin_roll": 8.0,         "trench_score": 0.3,
            "qb_pressure_roll": 0.2,    "def_pressures_roll": 0.25,
            "market_implied_team_total": 24.0,
            **{c: 0.0 for c in NN_FEATURE_COLUMNS},
        },
        {
            "team": "WEAK", "elo_pre": 1400.0,
            "off_pass_epa_roll": -0.10, "off_rush_epa_roll": -0.05,
            "def_pass_epa_roll": -0.08, "def_rush_epa_roll": -0.03,
            "off_early_roll": -0.05,    "def_early_roll": -0.04,
            "margin_roll": -6.0,        "trench_score": -0.3,
            "qb_pressure_roll": -0.2,   "def_pressures_roll": -0.15,
            "market_implied_team_total": 20.0,
            **{c: 0.0 for c in NN_FEATURE_COLUMNS},
        },
    ])
    engine._preseason_roster = {}
    engine._preseason_norm = None
    return engine


# ── _batch_predict tests ───────────────────────────────────────────────────────

class TestBatchPredict:
    def test_returns_correct_shape(self, mock_engine):
        X = np.random.rand(50, len(NN_FEATURE_COLUMNS)).astype(np.float32)
        mock_engine.svc.scaler = MagicMock()
        mock_engine.svc.scaler.transform = lambda x: x
        mock_engine.svc.model = MagicMock()
        mock_engine.svc.model.predict = MagicMock(
            return_value=np.full((50, 1), 0.6, dtype=np.float32)
        )
        mock_engine.xgb_svc.scaler = MagicMock()
        mock_engine.xgb_svc.scaler.transform = lambda x: x
        mock_engine.xgb_svc.model = MagicMock()
        mock_engine.xgb_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(50, 0.4), np.full(50, 0.6)])
        )
        mock_engine.lr_svc.scaler = MagicMock()
        mock_engine.lr_svc.scaler.transform = lambda x: x
        mock_engine.lr_svc.model = MagicMock()
        mock_engine.lr_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(50, 0.4), np.full(50, 0.6)])
        )

        result = mock_engine._batch_predict(X)
        assert result.shape == (50,)
        assert np.all(result >= 0.02) and np.all(result <= 0.98)

    def test_blends_models_correctly(self, mock_engine):
        """Verify the weighted blend NN*0.45 + XGB*0.20 + LR*0.35 is applied correctly."""
        from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT
        X = np.ones((10, len(NN_FEATURE_COLUMNS)), dtype=np.float32)
        mock_engine.svc.scaler = MagicMock()
        mock_engine.svc.scaler.transform = lambda x: x
        mock_engine.svc.model = MagicMock()
        mock_engine.svc.model.predict = MagicMock(return_value=np.full((10, 1), 0.8))
        mock_engine.xgb_svc.scaler = MagicMock()
        mock_engine.xgb_svc.scaler.transform = lambda x: x
        mock_engine.xgb_svc.model = MagicMock()
        mock_engine.xgb_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(10, 0.4), np.full(10, 0.6)])
        )
        mock_engine.lr_svc.scaler = MagicMock()
        mock_engine.lr_svc.scaler.transform = lambda x: x
        mock_engine.lr_svc.model = MagicMock()
        mock_engine.lr_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(10, 0.5), np.full(10, 0.5)])
        )
        result = mock_engine._batch_predict(X)
        expected = NN_WEIGHT * 0.8 + XGB_WEIGHT * 0.6 + LR_WEIGHT * 0.5
        assert np.allclose(result, expected, atol=0.001)


class TestBuildInitialState:
    def test_returns_correct_shapes(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        assert state_template.shape == (2, 6)   # 2 teams, 6 state dims
        assert len(team_list) == 2
        assert set(team_idx.keys()) == {"STRONG", "WEAK"}

    def test_elo_extracted_correctly(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        assert state_template[team_idx["STRONG"], 0] == pytest.approx(1600.0)
        assert state_template[team_idx["WEAK"],   0] == pytest.approx(1400.0)

    def test_epa_extracted_correctly(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        # off_pass_epa (dim 1)
        assert state_template[team_idx["STRONG"], 1] == pytest.approx(0.15)
        assert state_template[team_idx["WEAK"],   1] == pytest.approx(-0.10)


class TestPrecomputeStaticFeatures:
    def _make_schedule(self):
        return pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 1, "game_type": "REG",
             "spread_line": -7.0, "div_game": 0, "surface_type": 0},
        ])

    def test_returns_entry_for_each_game(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        assert "W01_STRONG_WEAK" in feats

    def test_static_array_correct_length(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        assert feats["W01_STRONG_WEAK"].shape == (len(NN_FEATURE_COLUMNS),)

    def test_dynamic_features_zeroed(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        arr = feats["W01_STRONG_WEAK"]
        col_idx = {c: i for i, c in enumerate(NN_FEATURE_COLUMNS)}
        for dyn in ("elo_diff", "elo_confidence", "pass_epa_matchup",
                    "rush_epa_matchup", "point_diff_advantage"):
            assert arr[col_idx[dyn]] == 0.0, f"{dyn} should be 0 in static features"

    def test_home_field_is_one(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        col_idx = {c: i for i, c in enumerate(NN_FEATURE_COLUMNS)}
        assert feats["W01_STRONG_WEAK"][col_idx["home_field_advantage"]] == 1.0


class TestVectorizedEloUpdate:
    def _make_state(self, n_sims, n_teams=2, strong_elo=1600.0, weak_elo=1400.0):
        """state[n_sims, n_teams, 6]. Team 0 = strong, team 1 = weak."""
        state = np.zeros((n_sims, n_teams, 6), dtype=np.float32)
        state[:, 0, 0] = strong_elo
        state[:, 1, 0] = weak_elo
        return state

    def test_elo_increases_for_winner(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_strong_elo = state[:, 0, 0].copy()
        # Home team (idx 0 = STRONG) wins all trials
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 0, 0] > initial_strong_elo), "Winner Elo should increase"

    def test_elo_decreases_for_loser(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_weak_elo = state[:, 1, 0].copy()
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 1, 0] < initial_weak_elo), "Loser Elo should decrease"

    def test_elo_zero_sum(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        total_before = state[:, 0, 0] + state[:, 1, 0]
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        total_after = state[:, 0, 0] + state[:, 1, 0]
        np.testing.assert_allclose(total_before, total_after, rtol=1e-4)

    def test_away_team_wins_when_negative_margin(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_weak_elo = state[:, 1, 0].copy()
        # Negative margin → away team (WEAK, idx 1) wins
        margins = np.full(n_sims, -7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 1, 0] > initial_weak_elo), "Away winner's Elo should increase"


class TestVectorizedEpaUpdate:
    def test_winner_epa_increases(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 14.0, dtype=np.float32)  # home wins by 14
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        # Home team (winner) off_pass_epa (dim 1) should increase
        assert np.all(state[:, 0, 1] > 0.0)

    def test_loser_epa_decreases(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 14.0, dtype=np.float32)
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        # Away team (loser) off_pass_epa (dim 1) should decrease
        assert np.all(state[:, 1, 1] < 0.0)

    def test_rush_weight_is_half_passing(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 10.0, dtype=np.float32)
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        pass_delta = state[0, 0, 1]   # off_pass_epa
        rush_delta = state[0, 0, 2]   # off_rush_epa
        assert rush_delta == pytest.approx(pass_delta * 0.5, rel=0.01)

    def test_def_epa_moves_correctly(self, mock_engine):
        """Winner's defensive EPA decreases (allowed less); loser's increases (allowed more)."""
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 14.0, dtype=np.float32)  # home wins by 14
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        # Home team (winner) def_pass_epa (dim 3) should DECREASE (they allowed less)
        assert np.all(state[:, 0, 3] < 0.0), "Winner def EPA should decrease"
        # Away team (loser) def_pass_epa (dim 3) should INCREASE (they allowed more)
        assert np.all(state[:, 1, 3] > 0.0), "Loser def EPA should increase"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_schedule(n_weeks: int = 4) -> pd.DataFrame:
    """Alternating home/away schedule between STRONG and WEAK over n_weeks."""
    rows = []
    for wk in range(1, n_weeks + 1):
        if wk % 2 == 1:
            rows.append({"home_team": "STRONG", "away_team": "WEAK",
                         "week": wk, "game_type": "REG",
                         "spread_line": -7.0, "div_game": 0, "surface_type": 0})
        else:
            rows.append({"home_team": "WEAK", "away_team": "STRONG",
                         "week": wk, "game_type": "REG",
                         "spread_line": 7.0, "div_game": 0, "surface_type": 0})
    return pd.DataFrame(rows)


def _elo_only_predict(X: np.ndarray) -> np.ndarray:
    """Logistic win probability from elo_diff alone — no model loading needed."""
    elo_diff_idx = list(NN_FEATURE_COLUMNS).index("elo_diff")
    elo_diff = X[:, elo_diff_idx]
    return np.clip(1.0 / (1.0 + np.power(10.0, -elo_diff / 400.0)), 0.02, 0.98)


# ── simulate_season tests ──────────────────────────────────────────────────────

class TestSimulateSeason:
    def test_returns_correct_top_level_keys(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        sched = _make_schedule(2)
        result = mock_engine.simulate_season(sched, n_sims=200)
        assert "team_stats" in result
        assert "game_probs" in result

    def test_team_stats_has_both_teams(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200)
        assert "STRONG" in result["team_stats"]
        assert "WEAK"   in result["team_stats"]

    def test_team_stats_has_all_percentile_keys(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200)
        for key in ("median_wins", "mean_wins", "std_dev", "p5", "p25", "p75", "p95"):
            assert key in result["team_stats"]["STRONG"]

    def test_wins_sum_to_total_games(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        n_weeks = 4
        result = mock_engine.simulate_season(_make_schedule(n_weeks), n_sims=500)
        total = (result["team_stats"]["STRONG"]["mean_wins"]
                 + result["team_stats"]["WEAK"]["mean_wins"])
        assert total == pytest.approx(n_weeks, abs=0.5)

    def test_strong_team_projects_more_wins(self, mock_engine):
        """200 Elo point gap → STRONG should project more wins than WEAK."""
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(18), n_sims=1000)
        strong_wins = result["team_stats"]["STRONG"]["mean_wins"]
        weak_wins   = result["team_stats"]["WEAK"]["mean_wins"]
        assert strong_wins > 10.0, f"Expected >10 but got {strong_wins:.1f}"
        assert weak_wins   <  8.0, f"Expected <8 but got {weak_wins:.1f}"

    def test_game_probs_populated_for_future_games(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(3), n_sims=200)
        assert len(result["game_probs"]) == 3

    def test_completed_results_applied_deterministically(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        # Mark week 1 as STRONG winning by 7 (home wins, positive margin)
        completed = {"W01_STRONG_WEAK": 7.0}
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200,
                                              completed_results=completed)
        # Week 1 should not appear in game_probs (it was completed)
        assert "W01_STRONG_WEAK" not in result["game_probs"]
        # Week 2 should appear (future)
        assert "W02_WEAK_STRONG" in result["game_probs"]

    def test_completed_games_credited_to_winner(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        # STRONG wins week 1 by 7 (STRONG is home, positive margin = home wins)
        # WEAK wins week 2 by 3  (WEAK is home in week 2, positive margin = home WEAK wins)
        completed = {"W01_STRONG_WEAK": 7.0, "W02_WEAK_STRONG": 3.0}
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200,
                                              completed_results=completed)
        # All wins should be 1 each (1 game each, all completed deterministically)
        assert result["team_stats"]["STRONG"]["mean_wins"] == pytest.approx(1.0, abs=0.01)
        assert result["team_stats"]["WEAK"]["mean_wins"]   == pytest.approx(1.0, abs=0.01)


# ── game_win_probability / game_win_probabilities_batch tests ─────────────────
#
# Perf fix context: cache_builder.py's schedule fallback and
# NNProjectionEngine.project_portfolio_wins both used to call
# game_win_probability() once per game in a Python loop, each call issuing 3
# single-row model.predict() calls. game_win_probabilities_batch() collapses
# that to exactly 3 calls total regardless of how many pairs are requested;
# the call-count assertions below are the regression guard for that.

def _mock_models(engine, nn_val: float, xgb_val: float, lr_val: float, n: int):
    """Wire up mocked scaler+model objects returning constant probabilities."""
    engine.svc.scaler = MagicMock()
    engine.svc.scaler.transform = lambda x: x
    engine.svc.model = MagicMock()
    engine.svc.model.predict = MagicMock(return_value=np.full((n, 1), nn_val, dtype=np.float32))
    engine.xgb_svc.scaler = MagicMock()
    engine.xgb_svc.scaler.transform = lambda x: x
    engine.xgb_svc.model = MagicMock()
    engine.xgb_svc.model.predict_proba = MagicMock(
        return_value=np.column_stack([np.full(n, 1 - xgb_val), np.full(n, xgb_val)])
    )
    engine.lr_svc.scaler = MagicMock()
    engine.lr_svc.scaler.transform = lambda x: x
    engine.lr_svc.model = MagicMock()
    engine.lr_svc.model.predict_proba = MagicMock(
        return_value=np.column_stack([np.full(n, 1 - lr_val), np.full(n, lr_val)])
    )


class TestGameWinProbabilityBatch:
    def test_single_call_returns_expected_keys(self, mock_engine):
        _mock_models(mock_engine, nn_val=0.7, xgb_val=0.6, lr_val=0.5, n=1)
        result = mock_engine.game_win_probability("STRONG", "WEAK")
        assert set(result) == {
            "home_team", "away_team", "home_win_prob", "away_win_prob",
            "nn_home_prob", "xgb_home_prob", "lr_home_prob",
        }
        assert result["home_team"] == "STRONG"
        assert result["away_team"] == "WEAK"

    def test_probabilities_sum_to_one(self, mock_engine):
        _mock_models(mock_engine, nn_val=0.7, xgb_val=0.6, lr_val=0.5, n=1)
        result = mock_engine.game_win_probability("STRONG", "WEAK")
        assert result["home_win_prob"] + result["away_win_prob"] == pytest.approx(1.0)

    def test_blend_matches_weighted_average(self, mock_engine):
        from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT
        _mock_models(mock_engine, nn_val=0.8, xgb_val=0.6, lr_val=0.5, n=1)
        result = mock_engine.game_win_probability("STRONG", "WEAK")
        expected = NN_WEIGHT * 0.8 + XGB_WEIGHT * 0.6 + LR_WEIGHT * 0.5
        assert result["home_win_prob"] == pytest.approx(expected, abs=1e-4)
        assert result["nn_home_prob"]  == pytest.approx(0.8, abs=1e-4)
        assert result["xgb_home_prob"] == pytest.approx(0.6, abs=1e-4)
        assert result["lr_home_prob"]  == pytest.approx(0.5, abs=1e-4)

    def test_batch_makes_exactly_one_model_call_for_many_pairs(self, mock_engine):
        """Regression guard: N pairs must cost 1 predict() call each, not N."""
        n = 5
        _mock_models(mock_engine, nn_val=0.6, xgb_val=0.55, lr_val=0.5, n=n)
        pairs = [("STRONG", "WEAK")] * n
        results = mock_engine.game_win_probabilities_batch(pairs)
        assert len(results) == n
        assert mock_engine.svc.model.predict.call_count == 1
        assert mock_engine.xgb_svc.model.predict_proba.call_count == 1
        assert mock_engine.lr_svc.model.predict_proba.call_count == 1

    def test_batch_result_matches_single_call(self, mock_engine):
        """Batching must not change the math -- same inputs, same output as one at a time."""
        _mock_models(mock_engine, nn_val=0.65, xgb_val=0.55, lr_val=0.45, n=2)
        batch = mock_engine.game_win_probabilities_batch(
            [("STRONG", "WEAK"), ("WEAK", "STRONG")]
        )
        _mock_models(mock_engine, nn_val=0.65, xgb_val=0.55, lr_val=0.45, n=1)
        single = mock_engine.game_win_probability("STRONG", "WEAK")
        assert batch[0]["home_win_prob"] == single["home_win_prob"]
        assert batch[0]["home_team"] == "STRONG" and batch[0]["away_team"] == "WEAK"
        assert batch[1]["home_team"] == "WEAK" and batch[1]["away_team"] == "STRONG"

    def test_empty_pairs_returns_empty_without_calling_models(self, mock_engine):
        _mock_models(mock_engine, nn_val=0.5, xgb_val=0.5, lr_val=0.5, n=1)
        assert mock_engine.game_win_probabilities_batch([]) == []
        mock_engine.svc.model.predict.assert_not_called()


class TestProjectPortfolioWinsBatching:
    """NNProjectionEngine.project_portfolio_wins runs once per drafted player;
    unbatched per-game model calls there multiply across the whole draft pool."""

    def _make_schedule(self, n_games: int, first_game_result: float = None):
        rows = []
        for wk in range(1, n_games + 1):
            row = {
                "home_team": "STRONG" if wk % 2 else "WEAK",
                "away_team": "WEAK" if wk % 2 else "STRONG",
                "week": wk, "game_type": "REG",
            }
            if first_game_result is not None and wk == 1:
                row["result"] = first_game_result
            rows.append(row)
        return pd.DataFrame(rows)

    def test_unplayed_games_batched_into_one_model_call(self, mock_engine):
        n = 6
        _mock_models(mock_engine, nn_val=0.6, xgb_val=0.55, lr_val=0.5, n=n)
        sched = self._make_schedule(n)
        mock_engine.project_portfolio_wins(["STRONG"], sched, n_sims=50)
        assert mock_engine.svc.model.predict.call_count == 1

    def test_completed_game_skips_the_model_entirely(self, mock_engine):
        """A fully-completed schedule needs no fallback prediction at all."""
        sched = self._make_schedule(1, first_game_result=7.0)  # home team won
        mock_engine.project_portfolio_wins(["STRONG"], sched, n_sims=50)
        mock_engine.svc.model.predict.assert_not_called()

    def test_mixed_completed_and_pending_batches_only_the_pending(self, mock_engine):
        n = 4
        _mock_models(mock_engine, nn_val=0.6, xgb_val=0.55, lr_val=0.5, n=n - 1)
        sched = self._make_schedule(n, first_game_result=7.0)  # week 1 completed, 2-4 pending
        mock_engine.project_portfolio_wins(["STRONG"], sched, n_sims=50)
        # 3 pending games batched into exactly 1 call
        assert mock_engine.svc.model.predict.call_count == 1
        call_args = mock_engine.svc.model.predict.call_args
        X_passed = call_args[0][0]
        assert X_passed.shape[0] == n - 1


class TestWeekAwareRosterValue:
    def test_precompute_static_features_uses_week_specific_cache(self, mock_engine):
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {
            (2025, 3, "STRONG"): {"off_roster_value": 2.0, "def_roster_value": 1.0,
                                   "st_value": 0.5, "qb_resilience": 0.9},
            (2025, 3, "WEAK"):   {"off_roster_value": -2.0, "def_roster_value": -1.0,
                                   "st_value": -0.5, "qb_resilience": 0.2},
        }
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        feat = static_feats["W03_STRONG_WEAK"]
        col_idx = {c: i for i, c in enumerate(NN_FC)}

        assert feat[col_idx["off_roster_value_delta"]] == pytest.approx(4.0)
        assert feat[col_idx["def_roster_value_delta"]] == pytest.approx(2.0)
        assert feat[col_idx["st_value_delta"]]         == pytest.approx(1.0)
        assert feat[col_idx["qb_resilience_delta"]]    == pytest.approx(0.7)

    def test_precompute_static_features_ignores_other_weeks(self, mock_engine):
        """A cache entry for a LATER week than the game being featured must not
        leak in -- carry-forward only looks at weeks <= the target week, never
        later weeks, so this stays a miss (falls back to the team-profile
        default of 0.0 here since _team_profiles has no override), not a
        future value applied retroactively."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {
            (2025, 9, "STRONG"): {"off_roster_value": 99.0},  # week 9, not week 3
        }
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["off_roster_value_delta"]] == pytest.approx(0.0)

    def test_precompute_static_features_defaults_to_zero_when_cache_empty(self, mock_engine):
        """Graceful degradation: an empty/missing roster-value cache (e.g.
        compute_roster_value() failed) AND no _team_profiles fallback value
        must not crash -- deltas fall back to 0.0, same as every other
        hp.get(col, 0.0) default in this method."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {}
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["off_roster_value_delta"]] == pytest.approx(0.0)

    def test_precompute_static_features_falls_back_to_team_profiles_when_cache_empty(self, mock_engine):
        """When _roster_value_cache has NO entry at all for a team (not just no
        entry for this week), the OLD prior-season _team_profiles average
        (off_roster_value_delta etc.) must be used instead of silently
        zeroing the feature out."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {}
        mock_engine._team_profiles.loc[
            mock_engine._team_profiles["team"] == "STRONG", "off_roster_value_delta"
        ] = 3.0
        mock_engine._team_profiles.loc[
            mock_engine._team_profiles["team"] == "WEAK", "off_roster_value_delta"
        ] = -1.0
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["off_roster_value_delta"]] == pytest.approx(4.0)

    def test_precompute_static_features_carries_forward_latest_week(self, mock_engine):
        """The cache only has an entry through the current week (nflverse's
        weekly_rosters never has future weeks) -- predicting a LATER week for
        that team must carry forward the latest week <= the target week
        rather than treat it as a miss and zero the feature out."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {
            (2025, 3, "STRONG"): {"off_roster_value": 2.0, "def_roster_value": 1.0,
                                   "st_value": 0.5, "qb_resilience": 0.9},
            (2025, 3, "WEAK"):   {"off_roster_value": -2.0, "def_roster_value": -1.0,
                                   "st_value": -0.5, "qb_resilience": 0.2},
        }
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 9, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        feat = static_feats["W09_STRONG_WEAK"]
        col_idx = {c: i for i, c in enumerate(NN_FC)}

        assert feat[col_idx["off_roster_value_delta"]] == pytest.approx(4.0)
        assert feat[col_idx["def_roster_value_delta"]] == pytest.approx(2.0)
        assert feat[col_idx["st_value_delta"]]         == pytest.approx(1.0)
        assert feat[col_idx["qb_resilience_delta"]]    == pytest.approx(0.7)

    def test_roster_talent_delta_still_uses_team_profiles_not_roster_value_cache(self, mock_engine):
        """roster_talent_delta is a separate, performance-grade-based feature
        computed in build_master_feature_table() -- NOT part of
        compute_roster_value()'s output. It must keep reading from
        _team_profiles, unaffected by this task."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {}
        mock_engine._team_profiles.loc[
            mock_engine._team_profiles["team"] == "STRONG", "roster_talent_delta"
        ] = 5.0
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["roster_talent_delta"]] == pytest.approx(5.0)


class TestInitializeBuildsRosterValueCache:
    def test_initialize_computes_and_threads_espn_overrides(self):
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine, RAWDATA_DIR

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        captured = {}

        def fake_compute_rv(season, rawdata_dir, espn_overrides=None):
            captured["args"] = (season, rawdata_dir, espn_overrides)
            return {(2025, 1, "KC"): {"off_roster_value": 1.0}}

        overrides = {(1, "QB1"): 0.0}
        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=fake_compute_rv):
            engine.initialize(2025, espn_overrides=overrides)

        assert captured["args"] == (2025, RAWDATA_DIR, overrides)
        assert engine._roster_value_cache == {(2025, 1, "KC"): {"off_roster_value": 1.0}}
        assert engine._season == 2025

    def test_initialize_defaults_espn_overrides_to_none(self):
        """Existing callers that don't pass espn_overrides must be unaffected."""
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        captured = {}

        def fake_compute_rv(season, rawdata_dir, espn_overrides=None):
            captured["espn_overrides"] = espn_overrides
            return {}

        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=fake_compute_rv):
            engine.initialize(2025)

        assert captured["espn_overrides"] is None

    def test_initialize_degrades_gracefully_when_compute_roster_value_fails(self):
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=Exception("rawdata unavailable")):
            engine.initialize(2025)  # must not raise

        assert engine._roster_value_cache == {}
