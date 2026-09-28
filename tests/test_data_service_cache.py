"""services/data_service.py's dynamic remote-check interval: check_remote_signals()
re-checks Firestore's metadata/cache_control doc every _LIVE_REMOTE_CHECK_INTERVAL
(10s) while the cached DOMAIN_ACTIVE bucket's games say a live window is open,
and falls back to the normal _REMOTE_CHECK_INTERVAL (60s) otherwise -- including
on a cold cache that hasn't been populated yet.

_active_check_interval() itself is memoized (see test_active_check_interval_*)
so the (non-trivial) is_within_live_window() DataFrame check does not run on
every single load_data() call -- only when the cheap elapsed-time bounds in
check_remote_signals() can't already decide the answer, and even then at most
once per _LIVE_REMOTE_CHECK_INTERVAL regardless of request volume.
"""
import time
from unittest.mock import MagicMock

import pandas as pd
import pytest

import services.cache_service as cs
import services.data_service as data_service
from services.data_service import check_remote_signals, _active_check_interval


@pytest.fixture(autouse=True)
def _reset_interval_cache():
    """Every test gets a cold _active_check_interval() memo -- otherwise a
    prior test's cached value (same DOMAIN_ACTIVE generation, well within the
    10s memo window in real wall-clock time) would leak into the next test."""
    data_service._INTERVAL_CACHE = {"value": None, "computed_at": 0.0, "generation": None}
    yield
    data_service._INTERVAL_CACHE = {"value": None, "computed_at": 0.0, "generation": None}


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


def test_active_check_interval_memoizes_within_the_live_interval_window(monkeypatch):
    """Two calls in quick succession against an unchanged DOMAIN_ACTIVE bucket
    must compute the (non-trivial) live-window check only once -- this is
    what keeps a page's several load_data() calls per request, and every
    concurrent request during a live game, from each re-running it."""
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    calls = []
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window",
        lambda games, now_et=None: calls.append(1) or True,
    )

    first = _active_check_interval()
    second = _active_check_interval()

    assert (first, second) == (cs._LIVE_REMOTE_CHECK_INTERVAL, cs._LIVE_REMOTE_CHECK_INTERVAL)
    assert len(calls) == 1

    cs.clear_data_cache()


def test_active_check_interval_recomputes_after_domain_cleared(monkeypatch):
    """A real cache invalidation (clear_domain -> new generation) must bust the
    memo even within the same instant, or a stale interval could survive a
    genuine data refresh."""
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window", lambda games, now_et=None: True
    )
    assert _active_check_interval() == cs._LIVE_REMOTE_CHECK_INTERVAL

    cs.clear_domain(cs.DOMAIN_ACTIVE)
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


def test_check_remote_signals_skips_the_live_window_check_within_ten_seconds(mock_firestore, monkeypatch):
    """The cheapest possible bound (elapsed <= the tightest interval) must
    short-circuit before ever touching the live-window DataFrame check --
    this is the common case for every request during a live game, once the
    live interval is actually in effect."""
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    calls = []
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window",
        lambda games, now_et=None: calls.append(1) or True,
    )
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", time.time() - 5)

    check_remote_signals(use_local=False)

    assert calls == []
    mock_firestore.collection.assert_not_called()
    cs.clear_data_cache()


def test_check_remote_signals_skips_the_live_window_check_past_sixty_seconds(mock_firestore, monkeypatch):
    """The other cheap bound (elapsed already past the loose interval) must
    also short-circuit straight to polling, without needing to know whether
    a live window is open -- it's time to poll either way."""
    cs.set_domain(cs.DOMAIN_ACTIVE, {"games": pd.DataFrame({"x": [1]})})
    calls = []
    monkeypatch.setattr(
        "services.live_window_service.is_within_live_window",
        lambda games, now_et=None: calls.append(1) or False,
    )
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", time.time() - 61)
    mock_firestore.collection.return_value.document.return_value.get.return_value = _stub_doc()

    check_remote_signals(use_local=False)

    assert calls == []
    mock_firestore.collection.assert_called_with("metadata")
    cs.clear_data_cache()
