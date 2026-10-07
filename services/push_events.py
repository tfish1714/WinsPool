"""services/push_events.py -- durable record of every notification batch sent."""
import logging
import time

logger = logging.getLogger(__name__)
_COLLECTION = "push_events"


def _db():
    from services.db_service import get_db
    return get_db()


def record_push_event(event_id, kind, counts, messages=None, extra=None) -> bool:
    db = _db()
    if db is None:
        logger.warning("push_events: no database, event %s not recorded", event_id)
        return False
    doc = {"kind": kind, "sent_at": time.time(), "counts": dict(counts)}
    if messages:
        doc["messages"] = {str(k): v for k, v in messages.items()}
    if extra:
        doc.update(extra)
    try:
        db.collection(_COLLECTION).document(event_id).set(doc)
        return True
    except Exception:
        logger.exception("push_events: failed to record %s", event_id)
        return False


def get_push_event(event_id):
    db = _db()
    if db is None:
        return None
    snap = db.collection(_COLLECTION).document(event_id).get()
    return {"id": event_id, **snap.to_dict()} if snap.exists else None


def push_event_exists(event_id) -> bool:
    db = _db()
    return bool(db is not None and db.collection(_COLLECTION).document(event_id).get().exists)


def list_push_events(limit: int = 50) -> list:
    db = _db()
    if db is None:
        return []
    rows = []
    for snap in db.collection(_COLLECTION).stream():
        d = snap.to_dict() or {}
        d.pop("messages", None)
        rows.append({"id": snap.id, **d})
    rows.sort(key=lambda r: r.get("sent_at", 0), reverse=True)
    return rows[:limit]
