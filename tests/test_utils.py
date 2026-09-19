import pytest
from services.utils import derive_prediction_scalars, get_team_logo_url, normalize_team_abbr

def test_normalize_team_abbr():
    """
    Verify that heterogeneous data abbreviations (e.g. from GitHub CSVs)
    are strictly mapped to the single source of truth application identifiers.
    """
    assert normalize_team_abbr("LAR") == "LA"
    assert normalize_team_abbr("WSH") == "WAS"
    assert normalize_team_abbr("JAC") == "JAX"
    # Fallback to pure uppercase if already standard
    assert normalize_team_abbr("buf") == "BUF"

def test_get_team_logo_url():
    """
    Verify the application leverages the ESPN HD vector API reliably, passing 
    through normalized string references to prevent dead images on the UI.
    """
    assert "LV.png" in get_team_logo_url("LV")
    # Native mapping fallback for ESPN anomalies
    assert "LAR.png" in get_team_logo_url("LA")
    
    # Empty string should yield empty safely
    assert get_team_logo_url("") == ""
    assert get_team_logo_url(None) == ""


class TestDerivePredictionScalars:
    """derive_prediction_scalars() is the single source of truth for
    winner/confidence/ATS-pick/edge-vs-vegas, shared by
    services/nn_prediction_service.py's build_ensemble_lookup and
    services/nn_projection_engine.py's build_mc_prediction_entry."""

    def test_confidence_is_clamped_to_99_near_certain_win(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.999, model_spread=14.0)
        assert result["pred_su_conf"] == 99.0

    def test_confidence_is_clamped_to_99_near_certain_loss(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.001, model_spread=-14.0)
        assert result["pred_su_conf"] == 99.0
        assert result["pred_winner"] == "SF"

    def test_confidence_floors_at_50_for_a_coin_flip(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.5, model_spread=0.0)
        assert result["pred_su_conf"] == 50.0

    def test_ats_pick_and_edge_vs_vegas_from_spread_line(self):
        # Model likes home more than Vegas (model_spread 7.0 > vegas 3.5) -> home has the edge.
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.65, model_spread=7.0, spread_line=3.5)
        assert result["pred_ats_pick"] == "KC"
        assert result["edge_vs_vegas"] == 3.5
        assert result["vegas_line"] == 3.5

    def test_no_spread_line_leaves_ats_and_edge_none(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.65, model_spread=7.0, spread_line=None)
        assert result["edge_vs_vegas"] is None
        assert result["vegas_line"] is None
        # ATS pick falls back to the straight-up winner when there's no Vegas line to compare against.
        assert result["pred_ats_pick"] == result["pred_winner"] == "KC"
