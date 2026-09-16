"""Tests for services/betting_edge_alert_service.py -- orchestrates the
existing betting screener/pattern scanner into a weekly alert-email summary.
Pure logic, no Firestore/network; scan_angles/screen_games are mocked since
their own behavior is already covered by test_pattern_scanner_service.py /
test_betting_screener_service.py."""
from unittest.mock import patch

import pandas as pd
import pytest

from services.betting_edge_alert_service import (
    find_raw_edge_outliers,
    find_validated_angle_matches,
    build_week_summary,
)
from services.email_service import send_betting_edge_email


class TestFindRawEdgeOutliers:
    def _predictions(self):
        return {
            2026: {
                # explanation has no model_spread -- KC_SF's model_spread must
                # come from the top-level fallback (Finding 2).
                "W03_KC_SF": {"pred_ats_pick": "KC", "model_spread": 4.0,
                               "explanation": {"edge_vs_vegas": 5.0, "vegas_line": -1.0}},
                "W03_BUF_MIA": {"pred_ats_pick": "MIA", "model_spread": -1.0,
                                 "explanation": {"edge_vs_vegas": 1.0, "vegas_line": -2.0}},
                "W02_DAL_PHI": {"pred_ats_pick": "DAL", "model_spread": 6.0,
                                 "explanation": {"edge_vs_vegas": 6.0, "vegas_line": 0.0}},
            },
        }

    def test_filters_to_target_week_and_threshold(self):
        result = find_raw_edge_outliers(
            self._predictions(), target_season=2026, target_week=3, edge_threshold=3.0,
        )
        # BUF_MIA's edge (1.0) is below threshold; DAL_PHI is week 2, not week 3
        assert len(result) == 1
        assert result[0]["home_team"] == "KC"
        assert result[0]["away_team"] == "SF"
        assert result[0]["edge_vs_vegas"] == 5.0
        # model_spread isn't in this entry's explanation -- confirms the
        # top-level fallback from Finding 2 actually fired.
        assert result[0]["model_spread"] == 4.0

    def test_model_spread_falls_back_to_top_level_when_explanation_has_edge_only(self):
        """explanation carries edge_vs_vegas but not model_spread -- the
        model_spread-specific top-level fallback must be used on its own,
        independent of whether edge_vs_vegas itself needed a fallback."""
        preds = {
            2026: {
                "W01_AAA_BBB": {
                    "pred_ats_pick": "AAA",
                    "model_spread": 7.5,
                    "edge_vs_vegas": 5.0,
                    "explanation": {"edge_vs_vegas": 5.0, "vegas_line": 2.5},
                },
            },
        }
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert len(result) == 1
        assert result[0]["edge_vs_vegas"] == 5.0
        assert result[0]["model_spread"] == 7.5
        assert result[0]["vegas_line"] == 2.5

    def test_falls_back_to_top_level_fields_when_explanation_missing(self):
        """explanation entirely absent (thin doc written by cache_builder.py's
        merge_thin_game_predictions, no manual backfill ever run for this
        season) -- edge_vs_vegas/model_spread must come from the top-level
        pred dict fields cache_builder.py actually refreshes daily."""
        preds = {
            2026: {
                "W01_AAA_BBB": {
                    "pred_ats_pick": "AAA",
                    "model_spread": 6.0,
                    "edge_vs_vegas": 4.0,
                },
            },
        }
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert len(result) == 1
        assert result[0]["edge_vs_vegas"] == 4.0
        assert result[0]["model_spread"] == 6.0
        # vegas_line has no top-level equivalent -- must stay None.
        assert result[0]["vegas_line"] is None

    def test_sorted_by_absolute_edge_descending(self):
        preds = {
            2026: {
                "W01_AAA_BBB": {"pred_ats_pick": "AAA", "model_spread": 3.0,
                                 "explanation": {"edge_vs_vegas": 3.5, "vegas_line": 0.0}},
                "W01_CCC_DDD": {"pred_ats_pick": "DDD", "model_spread": -8.0,
                                 "explanation": {"edge_vs_vegas": -8.0, "vegas_line": 0.0}},
            },
        }
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert [r["home_team"] for r in result] == ["CCC", "AAA"]

    def test_missing_edge_is_excluded(self):
        preds = {2026: {"W01_AAA_BBB": {"pred_ats_pick": None, "model_spread": None,
                                          "explanation": {"edge_vs_vegas": None, "vegas_line": 0.0}}}}
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert result == []

    def test_no_predictions_for_season_returns_empty(self):
        result = find_raw_edge_outliers({}, target_season=2026, target_week=1, edge_threshold=3.0)
        assert result == []


class TestFindValidatedAngleMatches:
    def _scan_result(self, held_up=True):
        return {
            "ats_leaderboard": [
                {"conditions": [{"feature": "elo_diff", "label": "Elo Diff", "min": 50.0}],
                 "train_rate": 0.62, "train_n": 120, "test_rate": 0.58, "test_n": 30,
                 "held_up": held_up},
            ],
            "su_leaderboard": [],
        }

    def _screen_result(self, with_candidate=True):
        candidates = [{
            "season": 2026, "week": 3, "home_team": "KC", "away_team": "SF",
            "matched_sides": ["home"], "already_played": False,
        }] if with_candidate else []
        return {"backtest": {}, "candidates": candidates, "filterable_features": {}}

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_matched_angle_with_upcoming_game_is_included(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        mock_screen.return_value = self._screen_result(with_candidate=True)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert len(result) == 1
        assert result[0]["metric"] == "ats"
        assert result[0]["games"][0]["home_team"] == "KC"

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_angle_that_did_not_hold_up_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=False)
        mock_screen.return_value = self._screen_result(with_candidate=True)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []
        mock_screen.assert_not_called()

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_held_up_angle_with_no_matching_game_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        mock_screen.return_value = self._screen_result(with_candidate=False)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_already_played_candidate_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        screen_result = self._screen_result(with_candidate=True)
        screen_result["candidates"][0]["already_played"] = True
        mock_screen.return_value = screen_result

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []


class TestBuildWeekSummary:
    @patch("services.betting_edge_alert_service.find_validated_angle_matches")
    @patch("services.betting_edge_alert_service.find_raw_edge_outliers")
    def test_combines_both_tiers(self, mock_outliers, mock_matches):
        mock_outliers.return_value = [{"home_team": "KC"}]
        mock_matches.return_value = [{"metric": "ats"}]

        result = build_week_summary({}, pd.DataFrame(), target_season=2026, target_week=3)

        assert result["season"] == 2026
        assert result["week"] == 3
        assert result["raw_edge_outliers"] == [{"home_team": "KC"}]
        assert result["validated_angle_matches"] == [{"metric": "ats"}]

    @patch("services.betting_edge_alert_service.find_validated_angle_matches")
    @patch("services.betting_edge_alert_service.find_raw_edge_outliers")
    def test_both_empty_is_a_valid_quiet_week(self, mock_outliers, mock_matches):
        mock_outliers.return_value = []
        mock_matches.return_value = []

        result = build_week_summary({}, pd.DataFrame(), target_season=2026, target_week=3)

        assert result["raw_edge_outliers"] == []
        assert result["validated_angle_matches"] == []


class TestRawEdgeOutlierToEmailPipeline:
    """End-to-end regression test (Finding 4 of the 2026-09-15 final review):
    every other test in this file/test_email_service.py mocks at least one
    seam in the raw-edge-outlier -> email chain. This test runs a realistic
    prediction dict -- shaped exactly like services.nn_prediction_service.py's
    build_ensemble_lookup() actually produces (both explanation.* AND
    top-level model_spread/edge_vs_vegas/pred_ats_pick populated) -- through
    the REAL find_raw_edge_outliers, then the REAL send_betting_edge_email
    (only resend.Emails.send and os.getenv mocked, per test_email_service.py's
    existing pattern), and asserts on the final rendered HTML. This is what
    would have caught the pre-Finding-2 explanation-only read: a shape
    mismatch anywhere in the chain now surfaces here."""

    def _realistic_predictions(self):
        # Matches build_ensemble_lookup's real per-game shape: pred_prob,
        # pred_winner, pred_su_conf, pred_ats_pick, model_spread,
        # edge_vs_vegas all at the top level, PLUS the fuller `explanation`
        # sub-dict (elo_diff, roster deltas, vegas_line, etc.).
        return {
            2026: {
                "W05_KC_SF": {
                    "pred_prob": 0.71,
                    "pred_winner": "KC",
                    "pred_su_conf": 71.0,
                    "pred_ats_pick": "KC",
                    "model_spread": 6.2,
                    "edge_vs_vegas": 4.7,
                    "explanation": {
                        "vegas_line": 1.5,
                        "vegas_home_prob": 0.55,
                        "model_spread": 6.2,
                        "edge_vs_vegas": 4.7,
                        "elo_diff": 45.0,
                        "elo_confidence": 0.6,
                        "pass_epa_matchup": 0.12,
                        "rush_epa_matchup": -0.05,
                        "early_down_matchup": 0.08,
                        "roster_delta": 0.3,
                        "turnover_margin": 0.5,
                        "point_diff_advantage": 3.2,
                        "home_qb_out": 0.0,
                        "away_qb_out": 0.0,
                        "rest_advantage": 0.0,
                        "travel_disadvantage": 1.1,
                        "trench_dominance": 0.4,
                        "off_roster_value": 0.2,
                        "def_roster_value": 0.1,
                    },
                },
            },
        }

    @patch("services.email_service.resend.Emails.send")
    @patch("services.email_service.os.getenv", return_value="re_test_key")
    def test_realistic_prediction_flows_through_to_rendered_email(self, mock_getenv, mock_send):
        mock_send.return_value = {"id": "pipeline-test"}

        outliers = find_raw_edge_outliers(
            self._realistic_predictions(), target_season=2026, target_week=5, edge_threshold=3.0,
        )
        assert len(outliers) == 1
        assert outliers[0]["edge_vs_vegas"] == 4.7
        assert outliers[0]["model_spread"] == 6.2
        assert outliers[0]["vegas_line"] == 1.5
        assert outliers[0]["ats_pick"] == "KC"

        summary = {
            "season": 2026, "week": 5,
            "validated_angle_matches": [],
            "raw_edge_outliers": outliers,
        }
        result = send_betting_edge_email("owner@x.com", summary)

        assert result is True
        call_params = mock_send.call_args[0][0]
        html_output = call_params["html"]
        assert "KC" in html_output and "SF" in html_output
        assert "+4.7" in html_output
        assert "6.2" in html_output
        assert "1.5" in html_output
