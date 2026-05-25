"""tests/test_xgb_prediction.py -- Unit tests for XGBPredictionService.

Uses in-memory training only. No rawdata/ files. No real model files.
"""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(scope="module")
def feature_table():
    """
    40-row feature table across 2 seasons for fast in-memory XGB training.

    - Season 2023: training data (20 rows)
    - Season 2024: test/val data (20 rows) — _split_data uses max season as test
    - Wins and losses interleaved so both classes appear in every split segment.

    home_team / away_team columns are required by _evaluate_season_level.

    Split details (VALIDATION_SPLIT_WEEK=14, TEST_SPLIT_WEEK=15):
      - train: season < 2024 → 20 rows
      - val:   season==2024, week <= 14 → rows with weeks 1-14 (14 rows)
      - test:  season==2024, week > 15  → rows with weeks 16-20 (5 rows)
    Interleaving [1,0]*20 ensures both classes appear in val (weeks 1-14) and
    test (weeks 16-20) so XGB early stopping and _evaluate don't see single-class sets.
    """
    from services.nn_feature_engine import FEATURE_COLUMNS

    rng = np.random.default_rng(42)
    n = len(FEATURE_COLUMNS)
    df = pd.DataFrame(rng.standard_normal((40, n)), columns=FEATURE_COLUMNS)
    # Interleave wins/losses so both classes appear in every split
    df["home_win"] = [1.0 if i % 2 == 0 else 0.0 for i in range(40)]
    df["season"] = [2023] * 20 + [2024] * 20
    df["week"] = list(range(1, 21)) * 2
    # Required by _evaluate_season_level groupby
    nfl_teams = ["NE", "BUF", "MIA", "NYJ", "BAL", "CLE", "PIT", "CIN",
                 "HOU", "IND", "TEN", "JAX", "KC", "LV", "LAC", "DEN"]
    df["home_team"] = [nfl_teams[i % len(nfl_teams)] for i in range(40)]
    df["away_team"] = [nfl_teams[(i + 1) % len(nfl_teams)] for i in range(40)]
    return df


@pytest.fixture(scope="module")
def trained_service(feature_table):
    """XGBPredictionService trained on the fixture — shared across tests."""
    from services.xgb_prediction_service import XGBPredictionService

    svc = XGBPredictionService()
    svc.train(feature_table)
    return svc


class TestXGBPredictGame:
    def test_predict_game_returns_float_in_unit_interval(self, trained_service):
        from services.nn_feature_engine import FEATURE_COLUMNS

        features = {col: 0.0 for col in FEATURE_COLUMNS}
        result = trained_service.predict_game(features)
        assert isinstance(result, float), f"Expected float, got {type(result)}"
        assert 0.0 <= result <= 1.0, f"Prediction {result} out of [0,1]"

    def test_predict_game_handles_missing_features(self, trained_service):
        """Passing a dict with only 3 keys must not raise — missing features fill to 0."""
        partial = {"elo_diff": 1.5, "home_advantage": 1, "trench_dominance_delta": 0.2}
        result = trained_service.predict_game(partial)
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_predict_game_untrained_raises_runtime_error(self):
        """Calling predict_game on a fresh (untrained) instance raises RuntimeError."""
        from services.xgb_prediction_service import XGBPredictionService
        from services.nn_feature_engine import FEATURE_COLUMNS

        fresh = XGBPredictionService()
        features = {col: 0.0 for col in FEATURE_COLUMNS}
        with pytest.raises((RuntimeError, ValueError)):
            fresh.predict_game(features)

    def test_output_bounded_on_random_inputs(self, trained_service):
        """50 random feature dicts must all produce predictions in [0, 1]."""
        from services.nn_feature_engine import FEATURE_COLUMNS

        rng = np.random.default_rng(7)
        for _ in range(50):
            features = {col: float(rng.standard_normal(1)[0]) for col in FEATURE_COLUMNS}
            p = trained_service.predict_game(features)
            assert 0.0 <= p <= 1.0, f"Prediction {p} out of [0,1]"


class TestXGBFeatureImportance:
    def test_feature_importance_returns_dataframe(self, trained_service):
        df = trained_service.feature_importance(top_n=5)
        assert isinstance(df, pd.DataFrame), f"Expected DataFrame, got {type(df)}"
        assert len(df) == 5
        assert "feature" in df.columns
        assert "importance" in df.columns
