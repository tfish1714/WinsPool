# tests/test_promotion_gate_lr.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.lr_prediction_service import LRPredictionService
    svc = LRPredictionService()
    svc._is_trained = True
    svc._eval_metrics = metrics
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    return svc


class TestLRPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v1": {
                "model_path": "models/lr_v1.pkl",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v1"},
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.lr_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch("pickle.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v2")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v1": {
                "model_path": "models/lr_v1.pkl",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v1"},
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.lr_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch("pickle.dump"):
            version = svc.save_versioned(version="v2", force_promote=True)
        assert version == "v2"

        saved_registry = json.loads(registry_path.read_text())
        assert saved_registry["v2"]["force_promoted"] is True

    def test_force_promoted_not_stamped_when_gate_actually_passes(self, tmp_path, monkeypatch):
        """force_promote=True with a passing gate must not leave a false
        audit trail -- the flag means the override actually fired."""
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v1": {
                "model_path": "models/lr_v1.pkl",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v1"},
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.lr_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.70, "test_auc": 0.70})
        with patch("pickle.dump"):
            svc.save_versioned(version="v2", force_promote=True)

        saved_registry = json.loads(registry_path.read_text())
        assert "force_promoted" not in saved_registry["v2"]
