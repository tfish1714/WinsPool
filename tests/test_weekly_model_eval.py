"""Tests for scripts/weekly_model_eval.py's --firestore / nn_weekly_accuracy wiring.

Model loading, feature-table construction, and _evaluate_weeks are mocked out --
these tests only cover what main() does with the resulting rows: CSV append,
local nn_weekly_accuracy store write, and the optional Firestore push.
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


FAKE_ROWS = [
    {"evaluated_at": "2026-01-01T00:00:00+00:00", "model_version": "nn_v14",
     "season": 2025, "week": 14, "games": 14, "correct": 9, "accuracy_pct": 64.3},
]


def _run_main(argv, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["weekly_model_eval.py", *argv])
    monkeypatch.setattr("scripts.weekly_model_eval.REPORTS_DIR", tmp_path)
    monkeypatch.setattr("scripts.weekly_model_eval.ACCURACY_CSV", tmp_path / "nn_weekly_accuracy.csv")
    monkeypatch.setattr("scripts.weekly_model_eval.NNPredictionService", MagicMock())
    monkeypatch.setattr("scripts.weekly_model_eval.XGBPredictionService", MagicMock())
    monkeypatch.setattr("scripts.weekly_model_eval.LRPredictionService", MagicMock())
    monkeypatch.setattr("scripts.weekly_model_eval.build_master_feature_table", MagicMock(return_value=[]))
    monkeypatch.setattr("scripts.weekly_model_eval._evaluate_weeks", MagicMock(return_value=FAKE_ROWS))

    from scripts.weekly_model_eval import main
    main()


class TestNnWeeklyAccuracyStoreWiring:
    def test_default_run_writes_local_store_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        with patch("services.cache_service.write_nn_weekly_accuracy_rows") as mock_write:
            _run_main(["--season", "2025", "--week", "14"], tmp_path, monkeypatch)

        mock_write.assert_called_once_with(2025, FAKE_ROWS, use_local=True)

    def test_firestore_flag_forces_use_local_data_false_and_pushes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        with patch("services.cache_service.write_nn_weekly_accuracy_rows") as mock_write, \
             patch("services.db_service.get_db", return_value=MagicMock()), \
             patch("services.db_service.signal_data_update") as mock_signal:
            _run_main(["--season", "2025", "--week", "14", "--firestore"], tmp_path, monkeypatch)

        assert os.environ["USE_LOCAL_DATA"] == "False"
        assert mock_write.call_count == 2
        mock_write.assert_any_call(2025, FAKE_ROWS, use_local=True)
        mock_write.assert_any_call(2025, FAKE_ROWS, use_local=False)

        # Task 7: a Firestore push must signal admin_analytics_updated, or
        # the admin ML Accuracy panel keeps serving a stale weekly snapshot.
        from services.cache_service import DOMAIN_ADMIN_ANALYTICS
        mock_signal.assert_called_once_with(DOMAIN_ADMIN_ANALYTICS)

    def test_no_save_skips_store_writes_entirely(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        with patch("services.cache_service.write_nn_weekly_accuracy_rows") as mock_write:
            _run_main(["--season", "2025", "--week", "14", "--no-save"], tmp_path, monkeypatch)

        mock_write.assert_not_called()
