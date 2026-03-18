"""tests/test_prediction_service.py -- Unit tests for the advanced prediction engine.

Validates the core mathematical formulas from FiveThirtyEight Elo and the
Frontiers Pythagorean expectation model, plus integration tests for the
PredictionService class methods.
"""

import pytest
import math
import pandas as pd
import numpy as np
from services.prediction_service import (
    _haversine_miles,
    _get_travel_distance,
    win_probability,
    margin_of_victory_multiplier,
    compute_elo_shift,
    apply_preseason_reversion,
    pythagorean_win_pct,
    pythagorean_projected_wins,
    PredictionService,
    ELO_MEAN,
    STADIUM_COORDS,
    _get_season_teams,
    enrich_schedule_with_predictions,
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
# PredictionService Integration Tests
# ---------------------------------------------------------------------------

def _make_games_df():
    """Create a minimal games DataFrame for integration testing."""
    games = []
    # 2023 Season: 4 games
    games.append({
        "game_id": "2023_01_KC_DET", "season": 2023, "week": 1,
        "game_type": "REG", "home_team": "KC", "away_team": "DET",
        "home_score": 21, "away_score": 20, "result": 1,
    })
    games.append({
        "game_id": "2023_01_BUF_NYJ", "season": 2023, "week": 1,
        "game_type": "REG", "home_team": "BUF", "away_team": "NYJ",
        "home_score": 22, "away_score": 16, "result": 6,
    })
    games.append({
        "game_id": "2023_02_DET_KC", "season": 2023, "week": 2,
        "game_type": "REG", "home_team": "DET", "away_team": "KC",
        "home_score": 31, "away_score": 17, "result": 14,
    })
    games.append({
        "game_id": "2023_02_NYJ_BUF", "season": 2023, "week": 2,
        "game_type": "REG", "home_team": "NYJ", "away_team": "BUF",
        "home_score": 10, "away_score": 24, "result": -14,
    })
    # 2024 Season: 2 played games + 1 unplayed
    games.append({
        "game_id": "2024_01_KC_BUF", "season": 2024, "week": 1,
        "game_type": "REG", "home_team": "KC", "away_team": "BUF",
        "home_score": 27, "away_score": 20, "result": 7,
    })
    games.append({
        "game_id": "2024_01_DET_NYJ", "season": 2024, "week": 1,
        "game_type": "REG", "home_team": "DET", "away_team": "NYJ",
        "home_score": 35, "away_score": 14, "result": 21,
    })
    games.append({
        "game_id": "2024_02_BUF_KC", "season": 2024, "week": 2,
        "game_type": "REG", "home_team": "BUF", "away_team": "KC",
        "home_score": None, "away_score": None, "result": None,
    })
    return pd.DataFrame(games)


class TestPredictionServiceInit:
    """Tests for Elo initialization and rating computation."""

    def test_initialize_produces_ratings(self):
        """After initialization, every team in the data should have an Elo rating."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        ratings = svc.get_all_ratings()
        assert "KC" in ratings
        assert "BUF" in ratings
        assert "DET" in ratings
        assert "NYJ" in ratings

    def test_winners_gain_elo(self):
        """Teams that win more should have higher Elo than persistent losers."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        # KC won 2 of 3, BUF won 2 of 3, DET won 1 of 2, NYJ lost all
        assert svc._elo_ratings["KC"] > svc._elo_ratings["NYJ"]
        assert svc._elo_ratings["BUF"] > svc._elo_ratings["NYJ"]

    def test_preseason_reversion_applied(self):
        """Ratings should be closer to 1505 than raw accumulated shifts."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        # After reversion, no team should be wildly far from 1505
        for elo in svc._elo_ratings.values():
            assert 1300 < elo < 1700

    def test_scoring_aggregates_computed(self):
        """Team scoring data should be populated for the target season."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        kc_scoring = svc._team_scoring.get("KC", {})
        assert kc_scoring["points_for"] == 27  # Only 1 completed game in 2024
        assert kc_scoring["games_played"] == 1


class TestGamePrediction:
    """Tests for single-game win probability output."""

    def test_game_prediction_keys(self):
        """Result dict should contain all expected keys."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.game_win_probability("KC", "BUF")
        expected_keys = {
            "home_team", "away_team", "home_win_prob", "away_win_prob",
            "elo_home_prob", "pyth_home_prob", "home_elo", "away_elo",
            "adjustments", "travel_miles", "elo_weight", "predicted_spread",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_probabilities_sum_to_one(self):
        """Home + away win probabilities should sum to 1.0."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert abs(result["home_win_prob"] + result["away_win_prob"] - 1.0) < 0.001

    def test_home_advantage_reflected(self):
        """Home team should get a positive adjustment."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert result["adjustments"] > 0


class TestPortfolioProjection:
    """Tests for Monte Carlo portfolio simulation."""

    def test_portfolio_projection_structure(self):
        """Result should contain expected keys."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, games, n_simulations=50)
        expected_keys = {"mean_wins", "std_wins", "min_wins", "max_wins",
                         "actual_wins", "projected_additional", "simulations"}
        assert expected_keys.issubset(set(result.keys()))

    def test_actual_wins_counted(self):
        """Banked wins from completed games should be reflected."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, games, n_simulations=50)
        # KC won Week 1, DET won Week 1 => 2 actual wins
        assert result["actual_wins"]["KC"] == 1
        assert result["actual_wins"]["DET"] == 1

    def test_completed_season_no_simulation(self):
        """A fully completed season should return actual wins with 0 simulations."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2023)
        result = svc.project_portfolio_wins(["KC", "BUF"], 2023, games)
        assert result["simulations"] == 0
        assert result["season_complete"] is True

    def test_mean_wins_reasonable(self):
        """Projected wins should be within plausible bounds."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, games, n_simulations=100)
        # With 2 banked wins and 1 remaining game for KC (+ potentially others),
        # mean should be between 2 and 4
        assert result["mean_wins"] >= 2.0
        assert result["mean_wins"] <= 10.0  # Very generous upper bound for test data


class TestDraftConfidence:
    """Tests for draft room confidence scoring."""

    def test_confidence_scores_sorted(self):
        """Scores should be returned sorted by confidence descending."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        scores = svc.generate_draft_confidence_scores(2024, games)
        confidences = [s["confidence"] for s in scores]
        assert confidences == sorted(confidences, reverse=True)

    def test_drafted_teams_excluded(self):
        """Drafted teams should not appear in the confidence list."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        scores = svc.generate_draft_confidence_scores(2024, games, drafted_teams=["KC"])
        team_list = [s["team"] for s in scores]
        assert "KC" not in team_list

    def test_ranks_sequential(self):
        """Rank values should start at 1 and be sequential."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        scores = svc.generate_draft_confidence_scores(2024, games)
        ranks = [s["rank"] for s in scores]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_confidence_bounded(self):
        """Confidence scores should be in [0, 1]."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        scores = svc.generate_draft_confidence_scores(2024, games)
        for s in scores:
            assert 0.0 <= s["confidence"] <= 1.0


class TestTeamSummary:
    """Tests for individual team summary output."""

    def test_summary_keys(self):
        """Summary should contain all expected keys."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        summary = svc.get_team_summary("KC")
        expected = {"team", "elo", "elo_rank", "points_for", "points_against",
                    "games_played", "pythagorean_win_pct", "pythagorean_projected_wins",
                    "bye_weeks"}
        assert expected.issubset(set(summary.keys()))

    def test_summary_values_plausible(self):
        """Summary values should reflect real game data."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        summary = svc.get_team_summary("KC")
        assert summary["points_for"] == 27
        assert summary["games_played"] == 1


# ---------------------------------------------------------------------------
# Expansion Tests: Defunct Teams, Schedule Enrichment, Season Teams
# ---------------------------------------------------------------------------

class TestSeasonTeams:
    """Tests for _get_season_teams helper."""

    def test_returns_teams_from_season(self):
        games = _make_games_df()
        teams = _get_season_teams(games, 2024)
        assert "KC" in teams
        assert "BUF" in teams
        assert "DET" in teams
        assert "NYJ" in teams

    def test_no_extra_seasons(self):
        games = _make_games_df()
        teams_2024 = _get_season_teams(games, 2024)
        # Only teams from 2024 games should be returned
        assert len(teams_2024) == 4

    def test_empty_season(self):
        games = _make_games_df()
        teams = _get_season_teams(games, 2030)
        assert len(teams) == 0


class TestDefunctTeamFiltering:
    """Tests that defunct teams (SD, STL, OAK) are excluded from modern season outputs."""

    def test_confidence_excludes_defunct_teams(self):
        """Defunct teams in Elo history should not appear in confidence scores."""
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        # Manually inject a defunct team into ratings
        svc._elo_ratings["SD"] = 1500.0
        svc._elo_ratings["STL"] = 1500.0
        svc._elo_ratings["OAK"] = 1500.0
        scores = svc.generate_draft_confidence_scores(2024, games)
        team_list = [s["team"] for s in scores]
        assert "SD" not in team_list
        assert "STL" not in team_list
        assert "OAK" not in team_list

    def test_only_season_teams_in_confidence(self):
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        scores = svc.generate_draft_confidence_scores(2024, games)
        team_list = [s["team"] for s in scores]
        # Only 4 teams in the test data for 2024
        assert len(team_list) == 4


class TestTeamProjectedWins:
    """Tests for get_team_projected_wins."""

    def test_returns_only_season_teams(self):
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        projections = svc.get_team_projected_wins(games)
        assert "KC" in projections
        assert "BUF" in projections
        assert len(projections) == 4

    def test_projections_are_floats(self):
        svc = PredictionService()
        games = _make_games_df()
        svc.initialize(games, 2024)
        projections = svc.get_team_projected_wins(games)
        for team, wins in projections.items():
            assert isinstance(wins, float)


class TestScheduleEnrichment:
    """Tests for enrich_schedule_with_predictions."""

    def test_unplayed_games_get_predictions(self):
        games = _make_games_df()
        schedule = pd.DataFrame([
            {"home_team": "BUF", "away_team": "KC", "result": -1000,
             "spread_line": -3.0, "week": 2},
        ])
        enriched = enrich_schedule_with_predictions(schedule, games, 2024)
        assert enriched.iloc[0]["pred_winner"] is not None
        assert enriched.iloc[0]["pred_su_conf"] >= 50.0
        assert enriched.iloc[0]["pred_su_conf"] <= 99.0

    def test_completed_games_skipped(self):
        games = _make_games_df()
        schedule = pd.DataFrame([
            {"home_team": "KC", "away_team": "BUF", "result": 7,
             "home_score": 27, "away_score": 20, "spread_line": -3.0, "week": 1},
        ])
        enriched = enrich_schedule_with_predictions(schedule, games, 2024)
        assert enriched.iloc[0]["pred_winner"] is None

    def test_columns_added(self):
        games = _make_games_df()
        schedule = pd.DataFrame([
            {"home_team": "DET", "away_team": "NYJ", "result": -1000,
             "spread_line": 7.0, "week": 3},
        ])
        enriched = enrich_schedule_with_predictions(schedule, games, 2024)
        assert "pred_winner" in enriched.columns
        assert "pred_su_conf" in enriched.columns
        assert "pred_ats_pick" in enriched.columns
