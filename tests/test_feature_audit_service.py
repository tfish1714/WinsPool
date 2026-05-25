"""Unit tests for feature_audit_service.compute_feature_audit()."""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

from services.nn_feature_engine import FEATURE_COLUMNS


def _make_feature_table(n=3):
    """Return a minimal feature DataFrame with required columns."""
    rng = np.random.default_rng(42)
    data = {col: rng.normal(size=n) for col in FEATURE_COLUMNS}
    data["season"] = [2025] * n
    data["week"] = [8, 8, 9][:n]
    data["home_team"] = ["KC", "SF", "BUF"][:n]
    data["away_team"] = ["LAC", "DAL", "MIA"][:n]
    data["home_win"] = [1, 0, 1][:n]
    return pd.DataFrame(data)


def _make_mocks(n=3):
    """Return mocked NN/XGB/LR services with plausible predict outputs."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, len(FEATURE_COLUMNS))).astype(np.float32)

    nn_svc = MagicMock()
    nn_svc.scaler.transform.return_value = X
    nn_svc.model.predict.return_value = rng.uniform(0.3, 0.7, (n, 1))
    nn_svc.loaded_version = "v10"

    xgb_svc = MagicMock()
    xgb_svc.scaler.transform.return_value = X
    # predict_proba returns (n, 2): col 1 = home_win prob
    xgb_proba = np.stack([rng.uniform(0.3, 0.7, n), rng.uniform(0.3, 0.7, n)], axis=1)
    xgb_svc.model.predict_proba.return_value = xgb_proba
    xgb_svc.loaded_version = "v4"

    lr_svc = MagicMock()
    lr_svc.scaler.transform.return_value = X
    lr_proba = np.stack([rng.uniform(0.3, 0.7, n), rng.uniform(0.3, 0.7, n)], axis=1)
    lr_svc.model.predict_proba.return_value = lr_proba
    lr_svc.model.coef_ = rng.normal(size=(1, len(FEATURE_COLUMNS)))
    lr_svc.loaded_version = "v2"

    return nn_svc, xgb_svc, lr_svc


def _patch_nn_grad(n, value=0.0):
    """Context manager that patches tf.GradientTape to return zero gradients."""
    import tensorflow as tf
    fake_grads = np.full((n, len(FEATURE_COLUMNS)), value, dtype=np.float32)

    class _FakeTape:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def watch(self, t): pass
        def gradient(self, pred, inp): return tf.constant(fake_grads)

    return patch("services.feature_audit_service.tf.GradientTape", return_value=_FakeTape())


class TestComputeFeatureAudit:
    def test_returns_list_of_dicts(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(3)
        nn, xgb_svc, lr = _make_mocks(3)
        dummy_shap = np.zeros((3, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(3):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        assert isinstance(records, list)
        assert len(records) == 3

    def test_record_has_required_keys(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        r = records[0]
        for key in ("game_key", "season", "week", "away_team", "home_team",
                    "nn_prob", "xgb_prob", "lr_prob", "blended_prob",
                    "features", "scaled_features", "feature_importance"):
            assert key in r, f"Missing key: {key}"

    def test_game_key_format(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        # First row: week=8, home=KC, away=LAC → W08_KC_LAC
        assert records[0]["game_key"] == "W08_KC_LAC"

    def test_probabilities_in_range(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(3)
        nn, xgb_svc, lr = _make_mocks(3)
        dummy_shap = np.zeros((3, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(3):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        for r in records:
            assert 0.0 <= r["blended_prob"] <= 1.0
            assert 0.0 <= r["nn_prob"] <= 1.0
            assert 0.0 <= r["xgb_prob"] <= 1.0
            assert 0.0 <= r["lr_prob"] <= 1.0

    def test_feature_importance_sorted_by_abs_score(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(2)
        nn, xgb_svc, lr = _make_mocks(2)
        dummy_shap = np.zeros((2, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(2):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        importance = records[0]["feature_importance"]
        scores = [abs(item["score"]) for item in importance]
        assert scores == sorted(scores, reverse=True), "importance must be sorted by |score| desc"

    def test_feature_importance_direction(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)

        # Force a known pattern: first feature strongly positive across all models
        shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        shap[0, 0] = 5.0   # first feature → "home"
        shap[0, 1] = -3.0  # second feature → "away"
        xgb_svc.model.get_booster.return_value.predict.return_value = shap
        # Zero out LR coef so XGB+NN dominate direction
        lr.model.coef_ = np.zeros((1, len(FEATURE_COLUMNS)))

        # NN gradient: mock GradientTape to return matching pattern
        import tensorflow as tf
        fake_grads = np.zeros((1, len(FEATURE_COLUMNS)), dtype=np.float32)
        fake_grads[0, 0] = 2.0    # first feature positive → "home"
        fake_grads[0, 1] = -1.0   # second feature negative → "away"
        with patch("services.feature_audit_service.xgb.DMatrix"), \
             patch("services.feature_audit_service.tf.GradientTape") as mock_tape_cls:
            mock_tape = MagicMock()
            mock_tape.__enter__ = lambda s: s
            mock_tape.__exit__ = MagicMock(return_value=False)
            mock_tape.gradient.return_value = tf.constant(fake_grads)
            mock_tape_cls.return_value = mock_tape
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        fi = {item["feature"]: item for item in records[0]["feature_importance"]}
        assert fi[FEATURE_COLUMNS[0]]["direction"] == "home"
        assert fi[FEATURE_COLUMNS[1]]["direction"] == "away"

    def test_features_dict_has_all_feature_columns(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        assert set(records[0]["features"].keys()) == set(FEATURE_COLUMNS)
        assert set(records[0]["scaled_features"].keys()) == set(FEATURE_COLUMNS)

    def test_empty_table_returns_empty_list(self):
        from services.feature_audit_service import compute_feature_audit
        empty = pd.DataFrame(columns=FEATURE_COLUMNS + ["season", "week", "home_team", "away_team", "home_win"])
        nn, xgb_svc, lr = _make_mocks(0)
        records = compute_feature_audit(empty, nn, xgb_svc, lr)
        assert records == []

    def test_tf_unavailable_falls_back_to_zeros(self):
        """When TF_AVAILABLE is False, NN importance should be zero (falls back gracefully)."""
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(2)
        nn, xgb_svc, lr = _make_mocks(2)
        dummy_shap = np.zeros((2, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), \
             patch("services.feature_audit_service.TF_AVAILABLE", False):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        # Should not raise; records should be complete
        assert len(records) == 2
        for r in records:
            assert "feature_importance" in r
