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


class _Resp:
    def __init__(self, status):
        self.status_code = status


class _PushError(Exception):
    def __init__(self, status):
        super().__init__(f"push failed {status}")
        self.response = _Resp(status)


def _player_doc(pid, sub):
    doc = MagicMock()
    doc.id = str(pid)
    doc.to_dict.return_value = {"push_subscription": sub} if sub is not None else {}
    return doc


def _configure(monkeypatch):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")


def test_410_on_single_send_prunes_subscription(monkeypatch):
    _configure(monkeypatch)
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    db = _mock_db_with_doc(exists=True, subscription=sub)
    with patch("services.db_service.get_db", return_value=db), \
         patch("services.db_service.signal_data_update") as signal, \
         patch("pywebpush.webpush", side_effect=_PushError(410)):
        assert push_service.send_push_notification(7, "t", "b") is False
    db.collection.return_value.document.return_value.update.assert_called_once()
    signal.assert_called_once_with("static")


def test_non_gone_failure_never_prunes(monkeypatch):
    _configure(monkeypatch)
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    db = _mock_db_with_doc(exists=True, subscription=sub)
    with patch("services.db_service.get_db", return_value=db), \
         patch("pywebpush.webpush", side_effect=_PushError(500)):
        assert push_service.send_push_notification(7, "t", "b") is False
    db.collection.return_value.document.return_value.update.assert_not_called()


def test_prune_in_local_mode_without_db_does_not_crash(monkeypatch):
    _configure(monkeypatch)
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    with patch("services.db_service.get_db", return_value=None), \
         patch("pywebpush.webpush", side_effect=_PushError(410)):
        assert push_service._deliver(7, sub, "t", "b") == "pruned"


def test_broadcast_counts_sent_failed_and_pruned(monkeypatch):
    _configure(monkeypatch)
    sub = lambda n: {"endpoint": f"https://push.example.com/{n}", "keys": {"auth": "a", "p256dh": "b"}}
    docs = [_player_doc(1, sub(1)), _player_doc(2, sub(2)), _player_doc(3, sub(3)),
            _player_doc(4, None), _player_doc(5, float("nan"))]
    db = MagicMock()
    db.collection.return_value.stream.return_value = docs
    outcomes = {"https://push.example.com/1": None,
                "https://push.example.com/2": _PushError(404),
                "https://push.example.com/3": _PushError(500)}

    def fake_webpush(subscription_info, **kwargs):
        err = outcomes[subscription_info["endpoint"]]
        if err:
            raise err

    with patch("services.db_service.get_db", return_value=db), \
         patch("services.db_service.signal_data_update"), \
         patch("pywebpush.webpush", side_effect=fake_webpush):
        result = push_service.broadcast_push_notification("Hello", "World")
    assert result == {"total": 3, "sent": 1, "failed": 1, "pruned": 1}


def test_broadcast_prunes_only_the_gone_subscription(monkeypatch):
    _configure(monkeypatch)
    docs = [_player_doc(1, {"endpoint": "e1"}), _player_doc(2, {"endpoint": "e2"})]
    db = MagicMock()
    db.collection.return_value.stream.return_value = docs
    errors = {"e1": _PushError(500), "e2": _PushError(410)}

    def fake_webpush(subscription_info, **kwargs):
        raise errors[subscription_info["endpoint"]]

    with patch("services.db_service.get_db", return_value=db), \
         patch("services.db_service.signal_data_update"), \
         patch("pywebpush.webpush", side_effect=fake_webpush):
        result = push_service.broadcast_push_notification("t", "b")
    assert result == {"total": 2, "sent": 0, "failed": 1, "pruned": 1}
    updates = db.collection.return_value.document
    updates.assert_called_once_with("2")
    updates.return_value.update.assert_called_once()


def test_broadcast_with_no_subscriptions_returns_zeros(monkeypatch):
    _configure(monkeypatch)
    db = MagicMock()
    db.collection.return_value.stream.return_value = [_player_doc(1, None)]
    with patch("services.db_service.get_db", return_value=db):
        assert push_service.broadcast_push_notification("t", "b") == {
            "total": 0, "sent": 0, "failed": 0, "pruned": 0}


def test_broadcast_in_local_mode_without_db_does_not_crash(monkeypatch):
    _configure(monkeypatch)
    with patch("services.db_service.get_db", return_value=None):
        assert push_service.broadcast_push_notification("t", "b") == {
            "total": 0, "sent": 0, "failed": 0, "pruned": 0}


def test_broadcast_logs_a_summary_line(monkeypatch, caplog):
    _configure(monkeypatch)
    db = MagicMock()
    db.collection.return_value.stream.return_value = []
    with patch("services.db_service.get_db", return_value=db), caplog.at_level(logging.INFO):
        push_service.broadcast_push_notification("t", "b")
    assert any("broadcast complete" in r.message for r in caplog.records)
