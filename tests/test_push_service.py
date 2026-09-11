"""services/push_service.py: send_push_notification's failure paths used to
be a single swallowed `except: logger.debug(...)` with the actual exception
discarded -- zero observability into whether push ever actually lands. Each
distinct outcome must now be logged at a visible level.
"""
import logging
from unittest.mock import patch, MagicMock

import services.push_service as push_service


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
