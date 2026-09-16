"""Tests for scripts/betting_edge_alert_weekly.py -- the winspool-betting-alert
Cloud Run Job entrypoint. Mirrors the mocking style of test_sync_live_scores.py:
mock every service call, verify the orchestration (no real Firestore/network)."""
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from scripts.betting_edge_alert_weekly import main


def _games_df():
    return pd.DataFrame([
        {"season": 2026, "week": 3, "home_team": "KC", "away_team": "SF",
         "home_score": None, "away_score": None, "result": None},
    ])


class TestMain:
    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    @patch("scripts.betting_edge_alert_weekly.os.environ.get")
    def test_sends_when_summary_has_edges(
        self, mock_env_get, mock_init_firebase, mock_load_data, mock_find_week,
        mock_load_preds, mock_build_summary, mock_send,
    ):
        mock_env_get.side_effect = lambda key, default=None: {
            "BETTING_ALERT_EMAIL": "owner@x.com",
            "BETTING_EDGE_THRESHOLD": "3.0",
        }.get(key, default)
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [{"metric": "ats"}],
            "raw_edge_outliers": [],
        }
        mock_send.return_value = True

        main()

        mock_init_firebase.assert_called_once()
        mock_send.assert_called_once_with("owner@x.com", mock_build_summary.return_value)

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    def test_skips_send_when_both_tiers_empty(
        self, mock_init_firebase, mock_load_data, mock_find_week, mock_load_preds,
        mock_build_summary, mock_send,
    ):
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [], "raw_edge_outliers": [],
        }

        main()

        mock_send.assert_not_called()

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    def test_no_upcoming_week_skips_entirely(self, mock_init_firebase, mock_load_data, mock_send):
        mock_load_data.return_value = (None, None, pd.DataFrame(), None, None, None, None)
        main()
        mock_send.assert_not_called()

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    @patch("scripts.betting_edge_alert_weekly.os.environ.get")
    def test_missing_recipient_env_var_skips_send(
        self, mock_env_get, mock_init_firebase, mock_load_data, mock_find_week,
        mock_load_preds, mock_build_summary, mock_send,
    ):
        mock_env_get.side_effect = lambda key, default=None: {
            "BETTING_EDGE_THRESHOLD": "3.0",
        }.get(key, default)  # BETTING_ALERT_EMAIL deliberately absent
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [{"metric": "ats"}], "raw_edge_outliers": [],
        }

        main()

        mock_send.assert_not_called()
