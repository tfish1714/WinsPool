"""services/push_service.py: send_push_notification's failure paths used to
be a single swallowed `except: logger.debug(...)` with the actual exception
discarded -- zero observability into whether push ever actually lands. Each
distinct outcome must now be logged at a visible level.
"""
import logging
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

import services.push_service as push_service


@pytest.fixture(autouse=True)
def _empty_static_bucket():
    """_get_push_subscription (Task 5) checks the static cache bucket before
    falling back to a direct Firestore read. Force that bucket empty for
    every test in this file so the mocked services.db_service.get_db() below
    is always what actually resolves the subscription -- otherwise whatever
    real player data happens to be warm in the local dev cache (e.g. an
    actual player_id=1 subscription) would silently shadow the mock."""
    import services.cache_service as cs
    cs.set_domain(cs.DOMAIN_STATIC, {"players": pd.DataFrame()})
    yield
    cs.clear_data_cache()


def _mock_db_with_doc(exists=True, subscription=None):
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = {"push_subscription": subscription} if subscription else {}
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.get.return_value = doc
    return mock_db


def test_no_vapid_keys_logs_warning_and_returns_false(monkeypatch, caplog):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "")
    with caplog.at_level(logging.WARNING):
        result = push_service.send_push_notification(1, "title", "body")
    assert result is False
    assert any("VAPID keys not configured" in r.message for r in caplog.records)


def test_no_player_document_logs_info_and_returns_false(monkeypatch, caplog):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")
    with patch("services.db_service.get_db", return_value=_mock_db_with_doc(exists=False)):
        with caplog.at_level(logging.INFO):
            result = push_service.send_push_notification(1, "title", "body")
    assert result is False
    assert any("no player document" in r.message for r in caplog.records)


def test_no_subscription_logs_info_and_returns_false(monkeypatch, caplog):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")
    with patch("services.db_service.get_db", return_value=_mock_db_with_doc(exists=True, subscription=None)):
        with caplog.at_level(logging.INFO):
            result = push_service.send_push_notification(1, "title", "body")
    assert result is False
    assert any("no push subscription stored" in r.message for r in caplog.records)


def test_webpush_failure_logs_warning_with_exception_text(monkeypatch, caplog):
    """The old code discarded the exception entirely (logger.debug with no
    args). It must now appear in the log message."""
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    with patch("services.db_service.get_db", return_value=_mock_db_with_doc(exists=True, subscription=sub)), \
         patch("pywebpush.webpush", side_effect=Exception("410 Gone: subscription expired")):
        with caplog.at_level(logging.WARNING):
            result = push_service.send_push_notification(1, "title", "body")
    assert result is False
    assert any("410 Gone: subscription expired" in r.message for r in caplog.records)


def test_uses_static_bucket_subscription_without_hitting_firestore(monkeypatch, caplog):
    """Task 5 regression: when the static cache bucket already has this
    player's push_subscription, send_push_notification must use it directly
    rather than issuing a fresh Firestore .get()."""
    import services.cache_service as cs
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    players_df = pd.DataFrame([{"playerId": 1, "push_subscription": sub}])
    cs.set_domain(cs.DOMAIN_STATIC, {"players": players_df})

    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")
    with patch("services.db_service.get_db") as mock_get_db, \
         patch("pywebpush.webpush", return_value=None):
        with caplog.at_level(logging.INFO):
            result = push_service.send_push_notification(1, "title", "body")
    assert result is True
    mock_get_db.assert_not_called()


def test_success_logs_info_and_returns_true(monkeypatch, caplog):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    with patch("services.db_service.get_db", return_value=_mock_db_with_doc(exists=True, subscription=sub)), \
         patch("pywebpush.webpush", return_value=None):
        with caplog.at_level(logging.INFO):
            result = push_service.send_push_notification(1, "title", "body")
    assert result is True
    assert any("sent push to player 1" in r.message for r in caplog.records)
