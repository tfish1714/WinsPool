"""Tests for consensus derived statistics and comparison math."""
import math

import pytest

from services import consensus_service as cs


def test_registry_loads_expected_keys():
    reg = cs.load_source_registry()
    assert "vegas_ou" in reg
    assert reg["vegas_ou"]["name"] == "Vegas O/U"
    assert reg["vegas_ou"]["type"] == "market"
    assert reg["fpi"]["type"] == "model"


def test_canonical_keys_match_registry():
    assert cs.CANONICAL_SOURCE_KEYS == set(cs.load_source_registry())


def test_numeric_sources_drops_non_numeric():
    """A model marker row is {'model': 'nn_xgb_lr_ensemble'} -- a string, not a projection."""
    assert cs.numeric_sources({"model": "nn_xgb_lr_ensemble"}) == {}


def test_numeric_sources_keeps_mixed_row_numerics():
    raw = {"br": 10, "note": "revised", "vegas_ou": 8.5}
    assert cs.numeric_sources(raw) == {"br": 10.0, "vegas_ou": 8.5}


def test_numeric_sources_excludes_bools():
    """bool is a subclass of int; a flag is not a projection."""
    assert cs.numeric_sources({"br": 10, "verified": True}) == {"br": 10.0}


def test_compute_derived_matches_real_2025_buffalo_row():
    """Worked example from the spec: BUF 2025."""
    sources = {
        "br": 12, "fpi": 10.6, "si": 12, "nfl_bhanpuri": 15, "nfl_rank": 12,
        "athletic": 11.2, "pff": 11.6, "usa_today": 13, "vegas_ou": 11.5, "clay": 11.9,
    }
    d = cs.compute_derived(sources)
    assert d["n_sources"] == 10
    assert d["consensus_mean"] == pytest.approx(12.08, abs=0.01)
    assert d["consensus_median"] == pytest.approx(11.95, abs=0.01)
    assert d["consensus_min"] == pytest.approx(10.6)
    assert d["consensus_max"] == pytest.approx(15.0)
    assert d["consensus_std"] == pytest.approx(1.14, abs=0.01)


def test_compute_derived_single_source_has_zero_std():
    d = cs.compute_derived({"vegas_ou": 9.5})
    assert d["n_sources"] == 1
    assert d["consensus_std"] == 0.0
    assert d["consensus_mean"] == 9.5


def test_compute_derived_empty_returns_zero_sources():
    d = cs.compute_derived({})
    assert d["n_sources"] == 0
    assert d["consensus_mean"] is None
    assert d["consensus_std"] is None
