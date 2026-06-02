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
