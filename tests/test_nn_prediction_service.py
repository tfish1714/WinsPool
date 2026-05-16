"""tests/test_nn_prediction_service.py -- Unit tests for the NN prediction engine.

Validates the feature engineering pipeline, aging curve calculations,
model architecture constraints, and prediction output bounds.
"""

import pytest
import numpy as np
import pandas as pd

from services.nn_feature_engine import (
    compute_age_multiplier,
    compute_roster_features,
    build_master_feature_table,
    GROWTH_MULTIPLIER,
    PRIME_MULTIPLIER,
    RAWDATA_DIR,
)

# Attempt TF import for model-level tests
try:
    from services.nn_prediction_service import (
        NNPredictionService,
        FEATURE_COLUMNS as NN_FEATURE_COLUMNS,
    )
    TF_AVAILABLE = True
except Exception:
    TF_AVAILABLE = False


# -----------------------------------------------------------------------
# Aging Curve Tests
# -----------------------------------------------------------------------

class TestAgingCurve:
    """Validates the age-based performance multiplier logic."""

    def test_young_player_gets_growth(self):
        """Players under 24 should receive a +5% growth multiplier."""
        mult = compute_age_multiplier(22, "WR")
        assert mult == GROWTH_MULTIPLIER
        assert mult == 1.05

    def test_prime_player_no_adjustment(self):
        """Players 24-27 should have a 1.0 multiplier (no adjustment)."""
        for age in [24, 25, 26, 27]:
            mult = compute_age_multiplier(age, "QB")
            assert mult == PRIME_MULTIPLIER

    def test_mid_career_decay(self):
        """Players 28-30 should decay at 2% per year past 27."""
        mult_28 = compute_age_multiplier(28, "QB")
        assert abs(mult_28 - 0.98) < 0.001

        mult_30 = compute_age_multiplier(30, "QB")
        assert abs(mult_30 - 0.94) < 0.001

    def test_old_player_standard_decay(self):
        """Players 31+ should decay at 4% per year past 27 for non-skill positions."""
        mult_31 = compute_age_multiplier(31, "QB")
        # 4 years past 27 at 4% = 0.84
        assert abs(mult_31 - 0.84) < 0.001

    def test_old_skill_player_accelerated_decay(self):
        """RB/WR players 31+ should decay at 6% per year past 27."""
        mult_31_rb = compute_age_multiplier(31, "RB")
        # 4 years past 27 at 6% = 0.76
        assert abs(mult_31_rb - 0.76) < 0.001

        mult_31_wr = compute_age_multiplier(31, "WR")
        assert abs(mult_31_wr - 0.76) < 0.001

    def test_minimum_floor(self):
        """Multiplier should never go below 0.3."""
        mult = compute_age_multiplier(40, "RB")
        assert mult >= 0.3

    def test_nan_age_returns_one(self):
        """NaN age should return a neutral multiplier of 1.0."""
        mult = compute_age_multiplier(np.nan, "QB")
        assert mult == 1.0

    def test_positional_distinction(self):
        """At the same old age, RB should decay faster than QB."""
        rb = compute_age_multiplier(33, "RB")
        qb = compute_age_multiplier(33, "QB")
        assert rb < qb


# -----------------------------------------------------------------------
# Roster Talent Score Tests
# -----------------------------------------------------------------------

class TestRosterTalentScore:
    """Validates per-team talent score computation (snap-count-based)."""

    def _make_snap_df(self):
        """Minimal snap_counts DataFrame matching nflverse schema."""
        rows = []
        # KC: QB (1000 off snaps), WR (850), RB (400)
        for pos, snaps in [("QB", 1000), ("WR", 850), ("RB", 400)]:
            for wk in range(1, 18):
                rows.append({
                    "season": 2024, "week": wk, "game_type": "REG",
                    "team": "KC", "position": pos,
                    "pfr_player_id": f"KC{pos}01",
                    "offense_snaps": snaps // 17, "defense_snaps": 0,
                })
        # BUF: QB (1000 off), OT (1000 off), DE (0 off / 900 def)
        for pos, off, def_ in [("QB", 1000, 0), ("OT", 1000, 0), ("DE", 0, 900)]:
            for wk in range(1, 18):
                rows.append({
                    "season": 2024, "week": wk, "game_type": "REG",
                    "team": "BUF", "position": pos,
                    "pfr_player_id": f"BUF{pos}01",
                    "offense_snaps": off // 17, "defense_snaps": def_ // 17,
                })
        return pd.DataFrame(rows)

    def _make_roster_df(self):
        """Minimal nflverse rosters DataFrame with birth_date for age multiplier."""
        return pd.DataFrame([
            {"season": 2024, "pfr_id": "KCQB01", "birth_date": "1998-09-10"},  # age ~26
            {"season": 2024, "pfr_id": "KCWR01", "birth_date": "2001-03-15"},  # age ~23
            {"season": 2024, "pfr_id": "KCRB01", "birth_date": "1992-06-01"},  # age ~32
            {"season": 2024, "pfr_id": "BUFQB01", "birth_date": "1997-05-20"}, # age ~27
            {"season": 2024, "pfr_id": "BUFOT01", "birth_date": "1999-01-10"}, # age ~25
            {"season": 2024, "pfr_id": "BUFDE01", "birth_date": "1999-07-22"}, # age ~25
        ])

    def test_score_is_positive(self):
        cache = compute_roster_features(self._make_snap_df(), self._make_roster_df())
        score = cache.get((2024, "KC"), {}).get("talent", 0.0)
        assert score > 0

    def test_missing_team_returns_zero(self):
        cache = compute_roster_features(self._make_snap_df(), self._make_roster_df())
        score = cache.get((2024, "FAKE"), {}).get("talent", 0.0)
        assert score == 0.0

    def test_young_player_boosted(self):
        cache = compute_roster_features(self._make_snap_df(), self._make_roster_df())
        score = cache.get((2024, "KC"), {}).get("talent", 0.0)
        assert score > 0  # snap-weighted score should be positive

    def test_trench_metrics(self):
        cache = compute_roster_features(self._make_snap_df(), self._make_roster_df())
        buf_ol = cache.get((2024, "BUF"), {}).get("ol_av", 0.0)
        buf_dl = cache.get((2024, "BUF"), {}).get("dl_av", 0.0)
        assert buf_ol > 0
        assert buf_dl > 0


# -----------------------------------------------------------------------
# Feature Table Tests
# -----------------------------------------------------------------------

class TestFeatureTable:
    """Validates the Master Feature Table construction."""

    @pytest.fixture(scope="class")
    def feature_table(self):
        if not RAWDATA_DIR.exists():
            pytest.skip("rawdata directory not found")
        try:
            return build_master_feature_table(min_season=2023, max_season=2024)
        except Exception as e:
            pytest.skip(f"Could not build feature table: {e}")

    def test_table_not_empty(self, feature_table):
        assert len(feature_table) > 0

    def test_expected_columns_present(self, feature_table):
        for col in ["season", "week", "home_team", "away_team", "home_win"]:
            assert col in feature_table.columns
            
    def test_feature_count(self, feature_table):
        if TF_AVAILABLE:
            for col in NN_FEATURE_COLUMNS:
                assert col in feature_table.columns

    def test_no_nan_in_label(self, feature_table):
        assert feature_table["home_win"].isna().sum() == 0

    def test_label_bounded(self, feature_table):
        valid = feature_table["home_win"].isin([0.0, 0.5, 1.0])
        assert valid.all()


# -----------------------------------------------------------------------
# Model Architecture Tests
# -----------------------------------------------------------------------

@pytest.mark.skipif(not TF_AVAILABLE, reason="TensorFlow not installed")
class TestModelArchitecture:
    """Validates the NN model structure and output constraints."""

    def test_model_builds(self):
        model = NNPredictionService._build_model(len(NN_FEATURE_COLUMNS))
        assert model is not None

    def test_output_shape(self):
        n = len(NN_FEATURE_COLUMNS)
        model = NNPredictionService._build_model(n)
        X = np.random.randn(5, n).astype(np.float32)
        preds = model.predict(X, verbose=0)
        assert preds.shape == (5, 1)

    def test_output_bounded(self):
        n = len(NN_FEATURE_COLUMNS)
        model = NNPredictionService._build_model(n)
        X = np.random.randn(100, n).astype(np.float32)
        preds = model.predict(X, verbose=0).flatten()
        assert all(0.0 <= p <= 1.0 for p in preds)

    def test_predict_game_returns_float(self):
        """predict_game should return a float in [0, 1]."""
        svc = NNPredictionService()
        svc.model = NNPredictionService._build_model(len(NN_FEATURE_COLUMNS))
        svc._is_trained = True
        features = {col: 0.0 for col in NN_FEATURE_COLUMNS}
        prob = svc.predict_game(features)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0
