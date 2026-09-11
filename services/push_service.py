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

    Returns False if VAPID keys are missing, no subscription is stored, or the
    push fails — callers must never block on this. Every outcome is logged
    (previously the failure path was `logger.debug`, invisible by default,
    with the actual exception discarded) so push reliability is observable
    instead of guessed at.
    """
    if not _VAPID_PUBLIC or not _VAPID_PRIVATE:
        logger.warning("push_service: VAPID keys not configured, cannot send push to player %s", player_id)
        return False
    try:
        from services.db_service import get_db
        doc = get_db().collection("players").document(str(player_id)).get()
        if not doc.exists:
            logger.info("push_service: no player document for %s", player_id)
            return False
        sub = doc.to_dict().get("push_subscription")
        if not sub:
            logger.info("push_service: no push subscription stored for player %s", player_id)
            return False

        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=_VAPID_PRIVATE,
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        logger.info("push_service: sent push to player %s", player_id)
        return True
    except Exception as e:
        logger.warning("push_service: send failed for player %s: %s", player_id, e)
        return False
