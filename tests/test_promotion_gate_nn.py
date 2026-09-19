import json
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.nn_prediction_service import NNPredictionService
    svc = NNPredictionService()
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    svc._eval_metrics = metrics
    return svc


class TestNNPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "models": [{
                "version": "v13",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60},
            }],
            "latest": "v13",
            "best_by": {},
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50})
        with patch("joblib.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v14")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "models": [{
                "version": "v13",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60},
            }],
            "latest": "v13",
            "best_by": {},
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50})
        with patch("joblib.dump"):
            version = svc.save_versioned(version="v14", force_promote=True)
        assert version == "v14"

        saved_registry = json.loads(registry_path.read_text())
        entry = next(m for m in saved_registry["models"] if m["version"] == "v14")
        assert entry["force_promoted"] is True

    def test_force_promoted_not_stamped_when_gate_actually_passes(self, tmp_path, monkeypatch):
        """force_promote=True with a passing gate must not leave a false
        audit trail -- the flag means the override actually fired."""
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "models": [{
                "version": "v13",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60},
            }],
            "latest": "v13",
            "best_by": {},
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.70})
        with patch("joblib.dump"):
            svc.save_versioned(version="v14", force_promote=True)

        saved_registry = json.loads(registry_path.read_text())
        entry = next(m for m in saved_registry["models"] if m["version"] == "v14")
        assert "force_promoted" not in entry

    def test_new_entry_stores_feature_columns(self, tmp_path, monkeypatch):
        """Regression guard for the gap this task fixes: every NN registry
        entry must carry feature_columns so future gate checks can use it."""
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps({"models": [], "latest": None, "best_by": {}}))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.60})
        with patch("joblib.dump"):
            svc.save_versioned(version="v1")

        saved = json.loads(registry_path.read_text())
        assert saved["models"][0]["feature_columns"] == FEATURE_COLUMNS
