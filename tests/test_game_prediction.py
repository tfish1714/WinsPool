"""tests/test_game_prediction.py -- Unit tests for Pythagorean expectation and
single-game win probability output from PredictionService.
"""

from services.prediction_service import (
    pythagorean_win_pct,
    pythagorean_projected_wins,
    PredictionService,
)


# ---------------------------------------------------------------------------
# Pythagorean Expectation
# ---------------------------------------------------------------------------

class TestPythagoreanExpectation:
    """Validates the Frontiers NFL Pythagorean model (exponent 2.37).

    Formula: WinPct = PF^2.37 / (PF^2.37 + PA^2.37)
    """

    def test_equal_scoring_fifty_percent(self):
        """Equal points for and against yields 50% win expectation."""
        pct = pythagorean_win_pct(350, 350)
        assert abs(pct - 0.5) < 0.001

    def test_dominant_offense(self):
        """400 PF vs 300 PA should project well above .500."""
        pct = pythagorean_win_pct(400, 300)
        assert pct > 0.6

    def test_weak_team(self):
        """250 PF vs 400 PA should project well below .500."""
        pct = pythagorean_win_pct(250, 400)
        assert pct < 0.4

    def test_projected_wins_reasonable(self):
        """A team scoring 400 and allowing 300 should project ~11 wins over 17 games."""
        wins = pythagorean_projected_wins(400, 300, total_games=17)
        assert 10.0 < wins < 13.0

    def test_zero_scoring_fallback(self):
        """Both zero should return 0.5."""
        assert pythagorean_win_pct(0, 0) == 0.5


# ---------------------------------------------------------------------------
# Game Prediction
# ---------------------------------------------------------------------------

class TestGamePrediction:
    """Tests for single-game win probability output."""

    def test_game_prediction_keys(self, prediction_games_df):
        """Result dict should contain all expected keys."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        expected_keys = {
            "home_team", "away_team", "home_win_prob", "away_win_prob",
            "elo_home_prob", "pyth_home_prob", "home_elo", "away_elo",
            "adjustments", "travel_miles", "elo_weight", "predicted_spread",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_probabilities_sum_to_one(self, prediction_games_df):
        """Home + away win probabilities should sum to 1.0."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert abs(result["home_win_prob"] + result["away_win_prob"] - 1.0) < 0.001

    def test_home_advantage_reflected(self, prediction_games_df):
        """Home team should get a positive adjustment."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert result["adjustments"] > 0
