"""services/chat_service.py — Firestore-backed draft room chat.

Falls back to an in-memory store when Firestore is unavailable (local dev).
Messages in the in-memory store are lost on server restart but chat works
for the current session without needing Firestore credentials.
"""
import logging
from datetime import datetime, timezone
from services.db_service import get_db

logger = logging.getLogger(__name__)

# In-memory fallback: {season: [msg_dict, ...]}
_local_chat: dict[int, list[dict]] = {}


def _messages_ref(season: int):
    try:
        db = get_db()
    except Exception:
        return None
    if db is None:
        return None
    return db.collection("draft_chat").document(str(season)).collection("messages")


def _local_append(season: int, data: dict) -> dict:
    _local_chat.setdefault(season, []).append(data)
    if len(_local_chat[season]) > 200:
        _local_chat[season] = _local_chat[season][-200:]
    return data


def post_system_message(season: int, text: str) -> dict | None:
    """Write a system event to draft_chat/{season}/messages. Returns the written doc or None."""
    data = {
        "type": "system",
        "playerName": "System",
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ref = _messages_ref(season)
    if ref is None:
        return _local_append(season, data)
    try:
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post system message")
        return _local_append(season, data)


def post_chat_message(season: int, player_name: str, text: str) -> dict | None:
    """Write a player chat message to draft_chat/{season}/messages. Returns written doc or None."""
    data = {
        "type": "chat",
        "playerName": player_name,
        "text": text[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    ref = _messages_ref(season)
    if ref is None:
        return _local_append(season, data)
    try:
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post chat message")
        return _local_append(season, data)


def get_recent_messages(season: int, limit: int = 50) -> list[dict]:
    """Return the most recent messages ordered by timestamp ascending."""
    ref = _messages_ref(season)
    if ref is None:
        return list(_local_chat.get(season, []))[-limit:]
    try:
        from google.cloud.firestore_v1 import Query
        docs = (
            ref
            .order_by("timestamp", direction=Query.ASCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception:
        logger.exception("chat_service: failed to get messages")
        return list(_local_chat.get(season, []))[-limit:]
