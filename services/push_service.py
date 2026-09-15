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


def _get_push_subscription(player_id: int):
    """Look up player_id's stored push_subscription, preferring the warm
    static cache bucket (players/draft_order/draft_results/draft_order_rules
    -- see docs/superpowers/specs/2026-09-14-cache-mutability-redesign-design.md)
    over a fresh Firestore read. Falls back to a direct read only if the
    player isn't found there (e.g. added after the bucket last warmed) or
    the cached row has no push_subscription value at all -- pandas turns a
    missing dict field into NaN, not a real dict, so that case must fall
    through to Firestore rather than be treated as "no subscription".
    """
    from services.data_service import _get_static_bucket
    players_df = _get_static_bucket()["players"]
    if not players_df.empty and "playerId" in players_df.columns:
        match = players_df[players_df["playerId"] == int(player_id)]
        if not match.empty and "push_subscription" in match.columns:
            sub = match.iloc[0]["push_subscription"]
            if isinstance(sub, dict):
                return sub

    from services.db_service import get_db
    doc = get_db().collection("players").document(str(player_id)).get()
    if not doc.exists:
        logger.info("push_service: no player document for %s", player_id)
        return None
    return doc.to_dict().get("push_subscription")


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
        sub = _get_push_subscription(player_id)
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
