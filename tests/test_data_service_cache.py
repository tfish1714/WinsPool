"""services/data_service.py's dynamic remote-check interval: check_remote_signals()
re-checks Firestore's metadata/cache_control doc every _LIVE_REMOTE_CHECK_INTERVAL
(10s) while the cached DOMAIN_ACTIVE bucket's games say a live window is open,
and falls back to the normal _REMOTE_CHECK_INTERVAL (60s) otherwise -- including
on a cold cache that hasn't been populated yet.
"""
import time
from unittest.mock import MagicMock

import pandas as pd

import services.cache_service as cs
from services.data_service import check_remote_signals, _active_check_interval


def _stub_doc(exists=False, data=None):
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc


def test_active_check_interval_cold_cache_is_the_normal_interval():
    cs.clear_data_cache()
    assert _active_check_interval() == cs._REMOTE_CHECK_INTERVAL


def test_active_check_interval_is_tight_when_cached_games_are_live(monkeypatch):
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window", lambda games, now_et=None: True
    )

    assert _active_check_interval() == cs._LIVE_REMOTE_CHECK_INTERVAL

    cs.clear_data_cache()


def test_active_check_interval_is_normal_when_cached_games_are_not_live(monkeypatch):
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window", lambda games, now_et=None: False
    )

    assert _active_check_interval() == cs._REMOTE_CHECK_INTERVAL

    cs.clear_data_cache()


def test_check_remote_signals_rechecks_after_ten_seconds_when_live(mock_firestore, monkeypatch):
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window", lambda games, now_et=None: True
    )
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", time.time() - 15)
    mock_firestore.collection.return_value.document.return_value.get.return_value = _stub_doc()

    check_remote_signals(use_local=False)

    mock_firestore.collection.assert_called_with("metadata")
    cs.clear_data_cache()


def test_check_remote_signals_does_not_recheck_before_sixty_seconds_when_not_live(mock_firestore, monkeypatch):
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window", lambda games, now_et=None: False
    )
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", time.time() - 15)

    check_remote_signals(use_local=False)

    mock_firestore.collection.assert_not_called()
    cs.clear_data_cache()


def test_check_remote_signals_does_not_recheck_before_sixty_seconds_on_cold_cache(mock_firestore, monkeypatch):
    cs.clear_data_cache()
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", time.time() - 15)

    check_remote_signals(use_local=False)

    mock_firestore.collection.assert_not_called()
