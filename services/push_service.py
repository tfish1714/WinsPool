"""services/push_service.py — Web Push notifications via pywebpush + VAPID."""
import json
import logging
import os

logger = logging.getLogger(__name__)

_VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
_VAPID_EMAIL   = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")


def save_push_subscription(player_id: int, subscription: dict) -> bool:
    """Store the browser's push subscription object on the player's Firestore document."""
    try:
        from services.db_service import get_db
        get_db().collection("players").document(str(player_id)).update(
            {"push_subscription": subscription}
        )
        return True
    except Exception:
        logger.exception("push_service: failed to save subscription for player %s", player_id)
        return False


def send_push_notification(player_id: int, title: str, body: str) -> bool:
    """Send a web push notification to player_id. Returns True on success.

    Returns False silently if VAPID keys are missing, no subscription is stored,
    or the push fails — callers must never block on this.
    """
    if not _VAPID_PUBLIC or not _VAPID_PRIVATE:
        return False
    try:
        from services.db_service import get_db
        doc = get_db().collection("players").document(str(player_id)).get()
        if not doc.exists:
            return False
        sub = doc.to_dict().get("push_subscription")
        if not sub:
            return False

        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=_VAPID_PRIVATE,
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        return True
    except Exception:
        logger.debug("push_service: push failed for player %s (subscription may be expired)", player_id)
        return False
