"""Tests for NNProjectionEngine internals that do not require TensorFlow."""
import inspect
import re

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
