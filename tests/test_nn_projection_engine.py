"""Tests for NNProjectionEngine internals that do not require TensorFlow."""
import inspect
import re
from unittest.mock import patch, MagicMock

from services.nn_projection_engine import NNProjectionEngine
from services.prediction_service import ELO_HOME_ADVANTAGE, ELO_K


def test_elo_update_uses_calibrated_home_advantage():
    """The simulation must not carry its own copy of the HFA constant."""
    src = inspect.getsource(NNProjectionEngine._vectorized_elo_update)
    assert "48.0" not in src, "hardcoded home advantage still present"
    assert "ELO_HOME_ADVANTAGE" in src


def test_elo_update_uses_calibrated_k_factor():
    src = inspect.getsource(NNProjectionEngine._vectorized_elo_update)
    assert not re.search(r"\b20\.0\s*\*", src), "hardcoded K factor still present"
    assert "ELO_K" in src


def test_calibrated_constants_have_expected_values():
    """Guards against a silent revert of the June 2026 calibration."""
    assert ELO_HOME_ADVANTAGE == 41.5
    assert ELO_K == 20.6


class TestConstructorInjection:
    def test_default_construction_loads_from_registry(self):
        """No args: today's behavior — each service is constructed and load_model() is called."""
        with patch("services.nn_projection_engine.NNPredictionService") as MockNN, \
             patch("services.nn_projection_engine.XGBPredictionService") as MockXGB, \
             patch("services.nn_projection_engine.LRPredictionService") as MockLR:
            engine = NNProjectionEngine()

        MockNN.return_value.load_model.assert_called_once()
        MockXGB.return_value.load_model.assert_called_once()
        MockLR.return_value.load_model.assert_called_once()
        assert engine.svc is MockNN.return_value
        assert engine.xgb_svc is MockXGB.return_value
        assert engine.lr_svc is MockLR.return_value

    def test_injected_services_bypass_load_model(self):
        """Passing pre-built services skips construction and load_model() entirely."""
        fake_nn = MagicMock()
        fake_xgb = MagicMock()
        fake_lr = MagicMock()

        with patch("services.nn_projection_engine.NNPredictionService") as MockNN, \
             patch("services.nn_projection_engine.XGBPredictionService") as MockXGB, \
             patch("services.nn_projection_engine.LRPredictionService") as MockLR:
            engine = NNProjectionEngine(nn_svc=fake_nn, xgb_svc=fake_xgb, lr_svc=fake_lr)

        MockNN.assert_not_called()
        MockXGB.assert_not_called()
        MockLR.assert_not_called()
        fake_nn.load_model.assert_not_called()
        fake_xgb.load_model.assert_not_called()
        fake_lr.load_model.assert_not_called()
        assert engine.svc is fake_nn
        assert engine.xgb_svc is fake_xgb
        assert engine.lr_svc is fake_lr
