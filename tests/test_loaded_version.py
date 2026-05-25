"""Test that all three prediction services expose loaded_version after load_model()."""
import pytest
from unittest.mock import patch, MagicMock, mock_open
import pickle
import json
from pathlib import Path
from io import BytesIO


class TestNNLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        from services.nn_prediction_service import NNPredictionService

        # Create fake registry
        registry = {
            "models": [{"version": "v10"}],
            "latest": "v10",
            "best_by": {}
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))

        # Create fake model files
        model_path = tmp_path / "nn_v10.keras"
        scaler_path = tmp_path / "nn_v10_scaler.pkl"
        model_path.touch()
        scaler_path.touch()

        # Patch paths
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        fake_model = MagicMock()
        fake_scaler = MagicMock()

        # Patch keras module at the service level (it was imported as "from tensorflow import keras")
        import services.nn_prediction_service
        with patch.object(services.nn_prediction_service.keras.models, "load_model", return_value=fake_model), \
             patch("joblib.load", return_value=fake_scaler):
            svc = NNPredictionService()
            svc.load_model(path="v10")

            assert hasattr(svc, "loaded_version"), "NNPredictionService should have loaded_version attribute"
            assert svc.loaded_version == "v10", f"Expected loaded_version='v10', got {svc.loaded_version}"


class TestXGBLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        from services.xgb_prediction_service import XGBPredictionService
        import xgboost as xgb

        # Create fake files
        model_path = tmp_path / "xgb_v4.json"
        scaler_path = tmp_path / "xgb_v4_scaler.pkl"
        model_path.touch()
        scaler_path.touch()

        # Create fake registry
        registry = {
            "v4": {
                "model_path": str(model_path),
                "scaler_path": str(scaler_path),
            },
            "best_by": {}
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))

        # Patch paths
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)

        fake_model = MagicMock()
        fake_scaler = MagicMock()

        with patch.object(xgb.XGBClassifier, "load_model"), \
             patch("pickle.load", return_value=fake_scaler):
            svc = XGBPredictionService()
            svc.load_model(version="v4")

            assert hasattr(svc, "loaded_version"), "XGBPredictionService should have loaded_version attribute"
            assert svc.loaded_version == "v4", f"Expected loaded_version='v4', got {svc.loaded_version}"


class TestLRLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        from services.lr_prediction_service import LRPredictionService

        # Create fake files
        model_path = tmp_path / "lr_v2.pkl"
        scaler_path = tmp_path / "lr_v2_scaler.pkl"
        model_path.touch()
        scaler_path.touch()

        # Create fake registry
        registry = {
            "v2": {
                "model_path": str(model_path),
                "scaler_path": str(scaler_path),
            },
            "best_by": {}
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))

        # Patch paths
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)

        fake_model = MagicMock()
        fake_scaler = MagicMock()

        with patch("pickle.load", side_effect=[fake_model, fake_scaler]):
            svc = LRPredictionService()
            svc.load_model(version="v2")

            assert hasattr(svc, "loaded_version"), "LRPredictionService should have loaded_version attribute"
            assert svc.loaded_version == "v2", f"Expected loaded_version='v2', got {svc.loaded_version}"
