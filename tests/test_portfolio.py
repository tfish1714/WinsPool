"""tests/test_portfolio.py -- Unit tests for portfolio projection, draft confidence,
team summaries, season team helpers, defunct team filtering, projected wins,
and schedule enrichment in PredictionService.
"""

import pytest
import pandas as pd
from services.prediction_service import (
    PredictionService,
    _get_season_teams,
    enrich_schedule_with_predictions,
)


# ---------------------------------------------------------------------------
# Portfolio Projection
# ---------------------------------------------------------------------------

class TestPortfolioProjection:
    """Tests for Monte Carlo portfolio simulation."""

    def test_portfolio_projection_structure(self, prediction_games_df):
        """Result should contain expected keys."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=50)
        expected_keys = {"mean_wins", "std_wins", "min_wins", "max_wins",
                         "actual_wins", "projected_additional", "simulations"}
        assert expected_keys.issubset(set(result.keys()))

    def test_actual_wins_counted(self, prediction_games_df):
        """Banked wins from completed games should be reflected."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=50)
        # KC won Week 1, DET won Week 1 => 2 actual wins
        assert result["actual_wins"]["KC"] == 1
        assert result["actual_wins"]["DET"] == 1

    def test_completed_season_no_simulation(self, prediction_games_df):
        """A fully completed season should return actual wins with 0 simulations."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2023)
        result = svc.project_portfolio_wins(["KC", "BUF"], 2023, prediction_games_df)
        assert result["simulations"] == 0
        assert result["season_complete"] is True

    def test_mean_wins_reasonable(self, prediction_games_df):
        """Projected wins should be within plausible bounds."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=100)
        # With 2 banked wins and 1 remaining game for KC (+ potentially others),
        # mean should be between 2 and 4
        assert result["mean_wins"] >= 2.0
        assert result["mean_wins"] <= 10.0  # Very generous upper bound for test data


# ---------------------------------------------------------------------------
# Draft Confidence
# ---------------------------------------------------------------------------

class TestDraftConfidence:
    """Tests for draft room confidence scoring."""

    def test_confidence_scores_sorted(self, prediction_games_df):
        """Scores should be returned sorted by confidence descending."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        confidences = [s["confidence"] for s in scores]
        assert confidences == sorted(confidences, reverse=True)

    def test_drafted_teams_excluded(self, prediction_games_df):
        """Drafted teams should not appear in the confidence list."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df, drafted_teams=["KC"])
        team_list = [s["team"] for s in scores]
        assert "KC" not in team_list

    def test_ranks_sequential(self, prediction_games_df):
        """Rank values should start at 1 and be sequential."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        ranks = [s["rank"] for s in scores]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_confidence_bounded(self, prediction_games_df):
        """Confidence scores should be in [0, 1]."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        for s in scores:
            assert 0.0 <= s["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Team Summary
# ---------------------------------------------------------------------------

class TestTeamSummary:
    """Tests for individual team summary output."""

    def test_summary_keys(self, prediction_games_df):
        """Summary should contain all expected keys."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        summary = svc.get_team_summary("KC")
        expected = {"team", "elo", "elo_rank", "points_for", "points_against",
                    "games_played", "pythagorean_win_pct", "pythagorean_projected_wins",
                    "bye_weeks"}
        assert expected.issubset(set(summary.keys()))

    def test_summary_values_plausible(self, prediction_games_df):
        """Summary values should reflect real game data."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        summary = svc.get_team_summary("KC")
        assert summary["points_for"] == 27
        assert summary["games_played"] == 1


# ---------------------------------------------------------------------------
# Season Teams
# ---------------------------------------------------------------------------

class TestSeasonTeams:
    """Tests for _get_season_teams helper."""

    def test_returns_teams_from_season(self, prediction_games_df):
        teams = _get_season_teams(prediction_games_df, 2024)
        assert "KC" in teams
        assert "BUF" in teams
        assert "DET" in teams
        assert "NYJ" in teams

    def test_no_extra_seasons(self, prediction_games_df):
        teams_2024 = _get_season_teams(prediction_games_df, 2024)
        # Only teams from 2024 games should be returned
        assert len(teams_2024) == 4

    def test_empty_season(self, prediction_games_df):
        teams = _get_season_teams(prediction_games_df, 2030)
        assert len(teams) == 0


# ---------------------------------------------------------------------------
# Defunct Team Filtering
# ---------------------------------------------------------------------------

class TestDefunctTeamFiltering:
    """Tests that defunct teams (SD, STL, OAK) are excluded from modern season outputs."""

    def test_confidence_excludes_defunct_teams(self, prediction_games_df):
        """Defunct teams in Elo history should not appear in confidence scores."""
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        # Manually inject a defunct team into ratings
        svc._elo_ratings["SD"] = 1500.0
        svc._elo_ratings["STL"] = 1500.0
        svc._elo_ratings["OAK"] = 1500.0
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        team_list = [s["team"] for s in scores]
        assert "SD" not in team_list
        assert "STL" not in team_list
        assert "OAK" not in team_list

    def test_only_season_teams_in_confidence(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        team_list = [s["team"] for s in scores]
        # Only 4 teams in the test data for 2024
        assert len(team_list) == 4


# ---------------------------------------------------------------------------
# Team Projected Wins
# ---------------------------------------------------------------------------

class TestTeamProjectedWins:
    """Tests for get_team_projected_wins."""

    def test_returns_only_season_teams(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        projections = svc.get_team_projected_wins(prediction_games_df)
        assert "KC" in projections
        assert "BUF" in projections
        assert len(projections) == 4

    def test_projections_are_floats(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        projections = svc.get_team_projected_wins(prediction_games_df)
        for team, wins in projections.items():
            assert isinstance(wins, float)


# ---------------------------------------------------------------------------
# Schedule Enrichment
# ---------------------------------------------------------------------------

class TestScheduleEnrichment:
    """Tests for enrich_schedule_with_predictions."""

    def test_unplayed_games_get_predictions(self, prediction_games_df):
        schedule = pd.DataFrame([
            {"home_team": "BUF", "away_team": "KC", "result": -1000,
             "spread_line": -3.0, "week": 2},
        ])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert enriched.iloc[0]["pred_winner"] is not None
        assert enriched.iloc[0]["pred_su_conf"] >= 50.0
        assert enriched.iloc[0]["pred_su_conf"] <= 99.0

    def test_completed_games_skipped(self, prediction_games_df):
        schedule = pd.DataFrame([
            {"home_team": "KC", "away_team": "BUF", "result": 7,
             "home_score": 27, "away_score": 20, "spread_line": -3.0, "week": 1},
        ])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert enriched.iloc[0]["pred_winner"] is None

    def test_columns_added(self, prediction_games_df):
        schedule = pd.DataFrame([
            {"home_team": "DET", "away_team": "NYJ", "result": -1000,
             "spread_line": 7.0, "week": 3},
        ])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert "pred_winner" in enriched.columns
        assert "pred_su_conf" in enriched.columns
        assert "pred_ats_pick" in enriched.columns
