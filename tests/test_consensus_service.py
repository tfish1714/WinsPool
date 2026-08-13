"""Tests for consensus derived statistics and comparison math."""
import math

import numpy as np
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


def _model(**kw):
    return {t: {"mean_wins": w} for t, w in kw.items()}


def _consensus(**kw):
    """kw maps team -> dict of source values."""
    return {
        t: {"sources": s, **cs.compute_derived(s)}
        for t, s in kw.items()
    }


def test_build_comparison_computes_delta_and_ranks():
    model = _model(BUF=12.0, KC=9.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 10.0},
        KC={"br": 11.0, "vegas_ou": 11.0},
    )
    out = cs.build_comparison(model, consensus)
    assert out["available"] is True
    teams = {t["team"]: t for t in out["teams"]}

    assert teams["BUF"]["delta"] == pytest.approx(2.0)
    assert teams["KC"]["delta"] == pytest.approx(-2.0)
    assert teams["BUF"]["model_rank"] == 1
    assert teams["BUF"]["consensus_rank"] == 2
    assert teams["BUF"]["rank_delta"] == 1


def test_in_range_boundaries_are_inclusive():
    consensus = _consensus(BUF={"br": 9.0, "vegas_ou": 11.0})
    at_min = cs.build_comparison(_model(BUF=9.0), consensus)["teams"][0]
    at_max = cs.build_comparison(_model(BUF=11.0), consensus)["teams"][0]
    outside = cs.build_comparison(_model(BUF=11.5), consensus)["teams"][0]
    assert at_min["in_range"] is True
    assert at_max["in_range"] is True
    assert outside["in_range"] is False


def test_outlier_z_is_none_when_std_is_zero():
    """A single source gives zero spread -- no division by zero."""
    out = cs.build_comparison(_model(BUF=12.0), _consensus(BUF={"vegas_ou": 9.5}))
    assert out["teams"][0]["outlier_z"] is None


def test_outlier_z_scales_by_analyst_disagreement():
    """Same raw gap counts for more where analysts agree than where they are split.

    Both sets have mean 9.0, so the model's raw delta is identical; only the
    spread differs. The tight set must not have zero spread or outlier_z would
    be None and the comparison would raise.
    """
    tight = _consensus(BUF={"br": 8.8, "si": 9.0, "vegas_ou": 9.2, "pff": 9.0})
    split = _consensus(BUF={"br": 5.0, "si": 10.0, "vegas_ou": 9.5, "pff": 11.5})
    z_tight = cs.build_comparison(_model(BUF=11.0), tight)["teams"][0]["outlier_z"]
    z_split = cs.build_comparison(_model(BUF=11.0), split)["teams"][0]["outlier_z"]
    assert z_tight > z_split
    # Exact value, pinned to standard deviation (not variance) so a
    # transcription slip in the divisor would fail here even though the
    # ordering assertion above would still pass.
    assert z_tight == pytest.approx((11 - 9) / np.std([8.8, 9.0, 9.2, 9.0]))


def test_team_missing_from_consensus_is_excluded_from_summary():
    model = _model(BUF=12.0, KC=9.0)
    consensus = _consensus(BUF={"br": 10.0, "vegas_ou": 10.0})
    out = cs.build_comparison(model, consensus)
    teams = {t["team"]: t for t in out["teams"]}
    assert teams["KC"]["consensus_mean"] is None
    assert teams["KC"]["delta"] is None
    assert out["summary"]["n_compared"] == 1


def test_team_missing_from_model_is_excluded_from_summary_but_still_listed():
    """The mirror image of test_team_missing_from_consensus_is_excluded_from_summary.

    Every season 2017-2025 now hits exactly this direction -- preseason_predictions
    holds current-season model output only, so historical consensus teams have no
    matching model row. A consensus-only team must still appear in `teams` with
    model_wins/delta as None rather than being dropped, and must be excluded from
    n_compared.
    """
    model = _model(BUF=12.0)  # KC has no model row at all
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 10.0},
        KC={"br": 9.0, "vegas_ou": 9.0},
    )
    out = cs.build_comparison(model, consensus)
    teams = {t["team"]: t for t in out["teams"]}
    assert teams["KC"]["model_wins"] is None
    assert teams["KC"]["delta"] is None
    assert teams["KC"]["consensus_median"] == pytest.approx(9.0)
    assert out["summary"]["n_compared"] == 1  # only BUF has both sides


def test_no_consensus_returns_unavailable():
    out = cs.build_comparison(_model(BUF=12.0), {})
    assert out["available"] is False
    assert out["teams"] == []


def test_summary_mae_and_bias():
    model = _model(BUF=12.0, KC=8.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 10.0},
        KC={"br": 10.0, "vegas_ou": 10.0},
    )
    s = cs.build_comparison(model, consensus)["summary"]
    assert s["mae"] == pytest.approx(2.0)
    assert s["bias"] == pytest.approx(0.0)


def test_accuracy_columns_present_only_with_actuals():
    model = _model(BUF=12.0)
    consensus = _consensus(BUF={"br": 10.0, "vegas_ou": 10.0})

    without = cs.build_comparison(model, consensus)
    assert without["teams"][0].get("actual_wins") is None
    assert without["source_scores"] == []

    with_actuals = cs.build_comparison(model, consensus, actuals={"BUF": 11})
    t = with_actuals["teams"][0]
    assert t["actual_wins"] == 11
    assert t["model_error"] == pytest.approx(1.0)
    assert t["consensus_error"] == pytest.approx(-1.0)


def test_source_scores_report_mae_with_n():
    model = _model(BUF=12.0, KC=8.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 12.0},
        KC={"br": 6.0},
    )
    out = cs.build_comparison(model, consensus, actuals={"BUF": 12, "KC": 8})
    scores = {s["source"]: s for s in out["source_scores"]}

    # br: |10-12| = 2, |6-8| = 2 -> MAE 2.0 over n=2
    assert scores["br"]["mae"] == pytest.approx(2.0)
    assert scores["br"]["n"] == 2
    # vegas_ou only covers BUF -> MAE 0.0 over n=1
    assert scores["vegas_ou"]["n"] == 1
    assert "consensus_avg" in scores


def test_spearman_perfect_and_inverse():
    assert cs.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert cs.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert cs.spearman([1, 1, 1], [1, 2, 3]) is None  # zero variance
