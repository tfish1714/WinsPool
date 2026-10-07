"""services/push_events.py -- durable record of notification batches."""
import time

from fake_firestore import FakeFirestore

import services.push_events as pe


def _patch(monkeypatch, fs):
    monkeypatch.setattr("services.db_service.get_db", lambda: fs)


def test_record_then_get_roundtrip(monkeypatch):
    fs = FakeFirestore()
    _patch(monkeypatch, fs)
    counts = {"total": 1, "sent": 1, "failed": 0, "pruned": 0, "skipped": 0}
    assert pe.record_push_event("e1", "recap", counts, {1: {"title": "T", "body": "B", "status": "sent"}},
                                extra={"season": 2026}) is True
    ev = pe.get_push_event("e1")
    assert ev["kind"] == "recap" and ev["counts"] == counts and ev["season"] == 2026
    assert ev["messages"]["1"]["title"] == "T"
    assert pe.push_event_exists("e1") and not pe.push_event_exists("nope")
    assert pe.get_push_event("nope") is None


def test_list_omits_messages_and_orders_newest_first(monkeypatch):
    fs = FakeFirestore()
    _patch(monkeypatch, fs)
    pe.record_push_event("old", "recap", {}, {1: {"title": "x"}})
    time.sleep(0.01)
    pe.record_push_event("new", "recap", {}, {1: {"title": "x"}})
    rows = pe.list_push_events()
    assert [r["id"] for r in rows] == ["new", "old"]
    assert all("messages" not in r for r in rows)
    assert len(pe.list_push_events(limit=1)) == 1


def test_no_database_is_falsy_and_does_not_raise(monkeypatch):
    _patch(monkeypatch, None)
    assert pe.record_push_event("e", "recap", {}) is False
    assert pe.get_push_event("e") is None
    assert pe.push_event_exists("e") is False
    assert pe.list_push_events() == []
