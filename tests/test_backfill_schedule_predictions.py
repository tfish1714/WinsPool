"""Tests for scripts/backfill_schedule_predictions.py's cache-invalidation
signal routing (Task 6 of the cache mutability redesign) -- everything else
(model loading, feature table, per-game prediction building) is mocked out
so these tests exercise only the domain-routing decision at the end of main().
"""
import sys
from unittest.mock import patch, MagicMock

import pandas as pd


def _run_main_with_seasons(monkeypatch, seasons):
    from scripts import backfill_schedule_predictions as bsp

    argv = ["backfill_schedule_predictions.py", "--firestore",
            "--seasons", str(seasons[0]), str(seasons[1])]
    monkeypatch.setattr(sys, "argv", argv)

    with patch.object(bsp, "_init_firestore", return_value=MagicMock()), \
         patch.object(bsp, "NNPredictionService"), \
         patch.object(bsp, "XGBPredictionService"), \
         patch.object(bsp, "LRPredictionService"), \
         patch.object(bsp, "build_master_feature_table", return_value=pd.DataFrame(
             columns=["home_team", "away_team", "season"])), \
         patch.object(bsp, "build_ensemble_lookup", return_value={}), \
         patch.object(bsp, "_build_predictions_map", return_value={}), \
         patch("services.data_service._get_active_bucket", return_value={"season": 2026}), \
         patch("services.db_service.signal_data_update") as mock_signal:
        bsp.main()
    return mock_signal


def test_backfill_historical_only_range_signals_historical_domain(monkeypatch):
    from services.cache_service import DOMAIN_PREDICTIONS_HISTORICAL
    mock_signal = _run_main_with_seasons(monkeypatch, (2020, 2022))
    mock_signal.assert_called_once_with(DOMAIN_PREDICTIONS_HISTORICAL)


def test_backfill_active_only_range_signals_active_domain(monkeypatch):
    from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE
    mock_signal = _run_main_with_seasons(monkeypatch, (2026, 2026))
    mock_signal.assert_called_once_with(DOMAIN_PREDICTIONS_ACTIVE)


def test_backfill_range_spanning_active_season_signals_both_domains(monkeypatch):
    from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL
    mock_signal = _run_main_with_seasons(monkeypatch, (2024, 2026))
    assert mock_signal.call_count == 2
    called_domains = {c.args[0] for c in mock_signal.call_args_list}
    assert called_domains == {DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL}


def test_backfill_forces_use_local_data_false(monkeypatch):
    """See CLAUDE.md's 'any script that writes to Firestore must force
    USE_LOCAL_DATA=False' gotcha -- signal_data_update()'s get_db() call
    silently no-ops otherwise."""
    import os
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    _run_main_with_seasons(monkeypatch, (2026, 2026))
    assert os.environ["USE_LOCAL_DATA"] == "False"
