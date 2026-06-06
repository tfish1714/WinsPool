"""services/chat_service.py — Firestore-backed draft room chat."""
import logging
from datetime import datetime, timezone
from services.db_service import get_db

logger = logging.getLogger(__name__)


def _messages_ref(season: int):
    return get_db().collection("draft_chat").document(str(season)).collection("messages")


def post_system_message(season: int, text: str) -> dict | None:
    """Write a system event to draft_chat/{season}/messages. Returns the written doc or None."""
    try:
        ref = _messages_ref(season)
        data = {
            "type": "system",
            "playerName": "System",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post system message")
        return None


def post_chat_message(season: int, player_name: str, text: str) -> dict | None:
    """Write a player chat message to draft_chat/{season}/messages. Returns written doc or None."""
    try:
        ref = _messages_ref(season)
        data = {
            "type": "chat",
            "playerName": player_name,
            "text": text[:500],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post chat message")
        return None


def get_recent_messages(season: int, limit: int = 50) -> list[dict]:
    """Return the most recent messages ordered by timestamp ascending."""
    try:
        from google.cloud.firestore_v1 import Query
        docs = (
            _messages_ref(season)
            .order_by("timestamp", direction=Query.ASCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception:
        logger.exception("chat_service: failed to get messages")
        return []
