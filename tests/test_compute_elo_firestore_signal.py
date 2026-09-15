"""Tests for scripts/compute_elo.py's --firestore cache-invalidation signal
(Task 7 of the cache mutability redesign) -- compute_elo() itself and the
local/Firestore row writes are mocked out; these tests only cover the new
signal_data_update(DOMAIN_ADMIN_ANALYTICS) wiring added after them.
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pandas as pd


def _fake_df():
    return pd.DataFrame([
        {"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF", "home_elo_post": 1520.0},
    ])


def _run_main(argv, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["compute_elo.py", *argv])
    from scripts import compute_elo
    with patch.object(compute_elo, "compute_elo", return_value=(_fake_df(), {"KC": 1520.0})), \
         patch.object(compute_elo, "_print_season_summary"), \
         patch.object(compute_elo, "save_metadata"), \
         patch("services.cache_service.write_elo_history_season"):
        monkeypatch.setattr(compute_elo, "DEFAULT_OUTPUT", tmp_path / "elo_computed.csv")
        compute_elo.main()


def test_firestore_flag_signals_admin_analytics(tmp_path, monkeypatch):
    from services.cache_service import DOMAIN_ADMIN_ANALYTICS
    with patch("services.db_service.get_db", return_value=MagicMock()), \
         patch("services.db_service.signal_data_update") as mock_signal:
        _run_main(["--firestore"], tmp_path, monkeypatch)

    mock_signal.assert_called_once_with(DOMAIN_ADMIN_ANALYTICS)


def test_default_run_does_not_signal(tmp_path, monkeypatch):
    with patch("services.db_service.signal_data_update") as mock_signal:
        _run_main([], tmp_path, monkeypatch)

    mock_signal.assert_not_called()


def test_firestore_flag_forces_use_local_data_false(tmp_path, monkeypatch):
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    with patch("services.db_service.get_db", return_value=MagicMock()), \
         patch("services.db_service.signal_data_update"):
        _run_main(["--firestore"], tmp_path, monkeypatch)

    assert os.environ["USE_LOCAL_DATA"] == "False"
