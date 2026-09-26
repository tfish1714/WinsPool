import pytest
from services.utils import (
    derive_prediction_scalars, edge_vs_vegas, get_team_logo_url, normalize_team_abbr,
    pick_ats_team, prob_to_model_spread,
)

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

    def test_nan_spread_line_is_treated_as_missing(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.4, model_spread=-3.0,
                                           spread_line=float("nan"))
        assert result["edge_vs_vegas"] is None
        assert result["vegas_line"] is None
        assert result["pred_ats_pick"] == "SF"

    def test_model_spread_equal_to_line_goes_to_away(self):
        result = derive_prediction_scalars("KC", "SF", mean_prob=0.6, model_spread=3.0, spread_line=3.0)
        assert result["pred_ats_pick"] == "SF"
        assert result["edge_vs_vegas"] == 0.0


class TestEdgeVsVegas:
    """The one formula for model-vs-Vegas edge; every caller shares it."""

    def test_positive_when_model_likes_home_more_than_vegas(self):
        assert edge_vs_vegas(7.0, 3.5) == 3.5

    def test_negative_when_model_likes_home_less_than_vegas(self):
        assert edge_vs_vegas(-1.0, 3.0) == -4.0

    def test_rounds_to_one_decimal(self):
        assert edge_vs_vegas(3.14, 1.0) == 2.1

    @pytest.mark.parametrize("model_spread, line", [
        (None, 3.0), (3.0, None), (None, None),
        (float("nan"), 3.0), (3.0, float("nan")), (3.0, "n/a"),
    ])
    def test_missing_or_invalid_inputs_return_none(self, model_spread, line):
        assert edge_vs_vegas(model_spread, line) is None

    def test_numeric_strings_are_accepted(self):
        assert edge_vs_vegas(7.0, "3.5") == 3.5


class TestPickAtsTeam:
    def test_home_when_model_spread_exceeds_line(self):
        assert pick_ats_team("KC", "SF", "SF", model_spread=4.0, vegas_line=3.0) == "KC"

    def test_away_when_model_spread_below_line(self):
        assert pick_ats_team("KC", "SF", "KC", model_spread=2.0, vegas_line=3.0) == "SF"

    def test_tie_goes_to_away(self):
        assert pick_ats_team("KC", "SF", "KC", model_spread=3.0, vegas_line=3.0) == "SF"

    @pytest.mark.parametrize("line", [None, float("nan"), "n/a"])
    def test_no_usable_line_falls_back_to_the_straight_up_winner(self, line):
        assert pick_ats_team("KC", "SF", "SF", model_spread=9.0, vegas_line=line) == "SF"

    def test_home_favored_convention_positive_line_means_home_favored(self):
        # Vegas: KC -7 is stored as +7. Model says KC by 10 -> KC covers the pick.
        assert pick_ats_team("KC", "SF", "KC", model_spread=10.0, vegas_line=7.0) == "KC"
        # Model says KC by only 4 -> backs SF +7.
        assert pick_ats_team("KC", "SF", "KC", model_spread=4.0, vegas_line=7.0) == "SF"


class TestProbToModelSpread:
    def test_even_game_is_zero(self):
        assert prob_to_model_spread(0.5) == 0.0

    def test_symmetric_around_even(self):
        assert prob_to_model_spread(0.7) == -prob_to_model_spread(0.3)

    def test_matches_the_logistic_scale(self):
        import math
        from services.constants import SPREAD_TO_PROB_SCALE
        assert prob_to_model_spread(0.75) == round(SPREAD_TO_PROB_SCALE * math.log(0.75 / 0.25), 1)

    def test_extreme_probabilities_are_clipped(self):
        from services.constants import PROB_CLIP_MAX, PROB_CLIP_MIN
        assert prob_to_model_spread(1.0) == prob_to_model_spread(PROB_CLIP_MAX)
        assert prob_to_model_spread(0.0) == prob_to_model_spread(PROB_CLIP_MIN)


def test_normalize_team_abbr_handles_relocated_franchises():
    assert normalize_team_abbr("OAK") == "LV"
    assert normalize_team_abbr("SD") == "LAC"
    assert normalize_team_abbr("STL") == "LA"
    assert normalize_team_abbr(" wsh ") == "WAS"
