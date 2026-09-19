# tests/test_promotion_gate_xgb.py
import json
import pickle
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.xgb_prediction_service import XGBPredictionService
    svc = XGBPredictionService()
    svc._is_trained = True
    svc._eval_metrics = metrics
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    return svc


class TestXGBPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v4")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            version = svc.save_versioned(version="v4", force_promote=True)
        assert version == "v4"

        saved_registry = json.loads(registry_path.read_text())
        assert saved_registry["v4"]["force_promoted"] is True

    def test_force_promoted_not_stamped_when_gate_actually_passes(self, tmp_path, monkeypatch):
        """force_promote=True with a passing gate must not leave a false
        audit trail -- the flag means the override actually fired."""
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.70, "test_auc": 0.70})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            svc.save_versioned(version="v4", force_promote=True)

        saved_registry = json.loads(registry_path.read_text())
        assert "force_promoted" not in saved_registry["v4"]

    def test_first_model_of_new_schema_never_blocked(self, tmp_path, monkeypatch):
        """A different feature_columns list means nothing to gate against --
        must save without raising."""
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": ["spread_line", "elo_diff"],  # old, leaky schema
                "metrics": {"test_accuracy": 0.90, "test_auc": 0.90},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            version = svc.save_versioned(version="v4")
        assert version == "v4"
