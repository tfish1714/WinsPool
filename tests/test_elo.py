"""tests/test_elo.py -- Unit tests for Elo rating mechanics (23 tests).

Covers: Haversine travel distance, win probability, margin-of-victory multiplier,
preseason reversion, and PredictionService initialization.
"""

import pytest
from services.prediction_service import (
    _haversine_miles,
    _get_travel_distance,
    win_probability,
    margin_of_victory_multiplier,
    apply_preseason_reversion,
    PredictionService,
    STADIUM_COORDS,
)


# ---------------------------------------------------------------------------
# Haversine / Travel Distance
# ---------------------------------------------------------------------------

class TestHaversine:
    """Validates the Haversine great-circle distance calculation."""

    def test_same_point_returns_zero(self):
        assert _haversine_miles(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_nyc_to_la(self):
        """NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) is ~2,451 miles."""
        dist = _haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        assert 2400 < dist < 2500

    def test_shared_stadium_zero_travel(self):
        """NYG and NYJ share MetLife Stadium; travel distance should be 0."""
        dist = _get_travel_distance("NYG", "NYJ")
        assert dist == 0.0
        dist = _get_travel_distance("LA", "LAC")
        assert dist == 0.0

    def test_cross_country_travel(self):
        """SEA to MIA should be > 2,500 miles."""
        dist = _get_travel_distance("SEA", "MIA")
        assert dist > 2500

    def test_unknown_team_returns_zero(self):
        """Graceful degradation for unknown team codes."""
        assert _get_travel_distance("FAKE", "KC") == 0.0

    def test_all_teams_have_coords(self):
        """Every current NFL team abbreviation should have stadium coordinates."""
        current_teams = [
            "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
            "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
            "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
            "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
        ]
        for team in current_teams:
            assert team in STADIUM_COORDS, f"Missing coordinates for {team}"


# ---------------------------------------------------------------------------
# Elo Win Probability
# ---------------------------------------------------------------------------

class TestWinProbability:
    """Validates the FiveThirtyEight logistic win probability formula.

    Formula: Pr(A) = 1 / (10^(-EloDiff / 400) + 1)
    """

    def test_equal_teams_fifty_fifty(self):
        """Two teams with identical Elo should have ~50% win probability."""
        prob = win_probability(1500, 1500)
        assert abs(prob - 0.5) < 0.001

    def test_200_point_advantage(self):
        """A 200-point Elo advantage should yield ~76% win probability."""
        prob = win_probability(1600, 1400)
        assert 0.75 < prob < 0.77

    def test_400_point_advantage(self):
        """A 400-point advantage should yield ~91% win probability."""
        prob = win_probability(1700, 1300)
        assert 0.90 < prob < 0.92

    def test_adjustments_shift_probability(self):
        """Home-field adjustment (48 pts) should meaningfully shift probability."""
        neutral = win_probability(1500, 1500, adjustments=0.0)
        home = win_probability(1500, 1500, adjustments=48.0)
        assert home > neutral
        assert home > 0.55  # ~57% with home-field alone

    def test_symmetry(self):
        """Pr(A wins) + Pr(B wins) should equal 1.0."""
        prob_a = win_probability(1550, 1450)
        prob_b = win_probability(1450, 1550)
        assert abs((prob_a + prob_b) - 1.0) < 0.001

    def test_probabilities_bounded(self):
        """Win probability should always be in (0, 1)."""
        assert 0 < win_probability(2000, 1000) < 1
        assert 0 < win_probability(1000, 2000) < 1


# ---------------------------------------------------------------------------
# Margin of Victory Multiplier
# ---------------------------------------------------------------------------

class TestMoVMultiplier:
    """Validates the log-scaled MoV multiplier with autocorrelation correction.

    Formula: MoV = ln(|PtDiff| + 1) * 2.2 / (WinnerEloDiff * 0.001 + 2.2)
    """

    def test_close_game_low_multiplier(self):
        """A 1-point win should produce a small multiplier."""
        mult = margin_of_victory_multiplier(1.0, 0.0)
        assert 0.5 < mult < 1.5

    def test_blowout_higher_multiplier(self):
        """A 28-point blowout should have a higher multiplier than a 3-point win."""
        close = margin_of_victory_multiplier(3.0, 0.0)
        blowout = margin_of_victory_multiplier(28.0, 0.0)
        assert blowout > close

    def test_diminishing_returns(self):
        """The marginal increase should diminish for larger margins."""
        m14 = margin_of_victory_multiplier(14.0, 0.0)
        m28 = margin_of_victory_multiplier(28.0, 0.0)
        m42 = margin_of_victory_multiplier(42.0, 0.0)
        increase_14_to_28 = m28 - m14
        increase_28_to_42 = m42 - m28
        assert increase_28_to_42 < increase_14_to_28

    def test_autocorrelation_correction(self):
        """Favorites (positive EloDiff) should get a smaller multiplier
        than underdogs (negative EloDiff) for the same point differential."""
        fav_mult = margin_of_victory_multiplier(14.0, 200.0)
        dog_mult = margin_of_victory_multiplier(14.0, -200.0)
        assert dog_mult > fav_mult


# ---------------------------------------------------------------------------
# Preseason Reversion
# ---------------------------------------------------------------------------

class TestPreseasonReversion:
    """Validates the 1/3 mean-reversion to 1505.

    Formula: Preseason_Elo = EndOfSeason * 2/3 + 1505 * 1/3
    """

    def test_reversion_formula(self):
        """A 1700-rated team should revert to ~1635."""
        reverted = apply_preseason_reversion(1700.0)
        expected = (1700.0 * 2 / 3) + (1505 * 1 / 3)
        assert abs(reverted - expected) < 0.01
        assert abs(reverted - 1635.0) < 1.0

    def test_mean_team_stays(self):
        """A team at exactly 1505 should stay at 1505 after reversion."""
        reverted = apply_preseason_reversion(1505.0)
        assert abs(reverted - 1505.0) < 0.01

    def test_bad_team_improves(self):
        """A 1300-rated team should revert upward toward 1505."""
        reverted = apply_preseason_reversion(1300.0)
        assert reverted > 1300.0
        assert reverted < 1505.0


# ---------------------------------------------------------------------------
# PredictionService Initialization
# ---------------------------------------------------------------------------

class TestPredictionServiceInit:
    """Tests for Elo initialization and rating computation."""

    def test_initialize_produces_ratings(self, prediction_games_df):
        """After initialization, every team in the data should have an Elo rating."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        ratings = svc.get_all_ratings()
        assert "KC" in ratings
        assert "BUF" in ratings
        assert "DET" in ratings
        assert "NYJ" in ratings

    def test_winners_gain_elo(self, prediction_games_df):
        """Teams that win more should have higher Elo than persistent losers."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        # KC won 2 of 3, BUF won 2 of 3, DET won 1 of 2, NYJ lost all
        assert svc._elo_ratings["KC"] > svc._elo_ratings["NYJ"]
        assert svc._elo_ratings["BUF"] > svc._elo_ratings["NYJ"]

    def test_preseason_reversion_applied(self, prediction_games_df):
        """Ratings should be closer to 1505 than raw accumulated shifts."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        # After reversion, no team should be wildly far from 1505
        for elo in svc._elo_ratings.values():
            assert 1300 < elo < 1700

    def test_scoring_aggregates_computed(self, prediction_games_df):
        """Team scoring data should be populated for the target season."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        kc_scoring = svc._team_scoring.get("KC", {})
        assert kc_scoring["points_for"] == 27  # Only 1 completed game in 2024
        assert kc_scoring["games_played"] == 1
