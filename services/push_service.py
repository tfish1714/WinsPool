"""services/push_service.py — Web Push notifications via pywebpush + VAPID."""
import json
import logging
import os

logger = logging.getLogger(__name__)

_VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
_VAPID_EMAIL   = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")

# HTTP statuses a push service returns when a subscription is permanently
# invalid (unsubscribed, expired, app uninstalled). Retrying is pointless and
# keeping the token just adds a failure to every future broadcast.
_GONE_STATUS_CODES = (404, 410)


def is_configured() -> bool:
    return bool(_VAPID_PUBLIC and _VAPID_PRIVATE)


def _is_gone_error(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in _GONE_STATUS_CODES


def _prune_subscription(player_id, failed_sub: dict) -> bool:
    """Delete a dead push_subscription. Returns True only if a field was deleted.

    The failed subscription may have come from the warm players cache, which
    can lag Firestore: a browser that resubscribed already stored a newer
    subscription. So the delete only happens when the stored endpoint still
    equals the failed one (read-compare-update, not a transaction: the window
    is milliseconds and a lost race only costs one re-subscribe prompt).
    """
    from services.db_service import get_db
    db = get_db()
    if db is None:
        # Local-data mode: nothing to delete, and firebase_admin is not needed.
        return False
    ref = db.collection("players").document(str(player_id))
    snap = ref.get()
    stored = (snap.to_dict() or {}).get("push_subscription") if snap.exists else None
    if not isinstance(stored, dict) or stored.get("endpoint") != failed_sub.get("endpoint"):
        logger.info("push_service: not pruning player %s, stored subscription differs or is gone", player_id)
        return False
    from firebase_admin import firestore
    ref.update({"push_subscription": firestore.DELETE_FIELD})
    try:
        _invalidate_players_cache()
    except Exception:
        logger.warning(
            "push_service: players cache invalidation failed after pruning subscription for player %s",
            player_id, exc_info=True,
        )
    logger.info("push_service: pruned dead push subscription for player %s", player_id)
    return True


def _invalidate_players_cache() -> None:
    """Drop this process's static bucket and tell every other process to do so."""
    import services.cache_service as cs
    from services.db_service import signal_data_update
    cs.clear_data_cache(cs.DOMAIN_STATIC)
    signal_data_update("static")


def _deliver(player_id, sub: dict, title: str, body: str, url=None) -> str:
    """Send one notification. Returns "sent", "failed", or "pruned"."""
    from pywebpush import webpush
    payload = {"title": title, "body": body}
    if url:
        payload["url"] = url
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps(payload),
            vapid_private_key=_VAPID_PRIVATE,
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        logger.info("push_service: sent push to player %s", player_id)
        return "sent"
    except Exception as e:
        logger.warning("push_service: send failed for player %s: %s", player_id, e)
        if _is_gone_error(e):
            try:
                if _prune_subscription(player_id, sub):
                    return "pruned"
            except Exception:
                logger.exception("push_service: prune failed for player %s", player_id)
        return "failed"


def save_push_subscription(player_id: int, subscription: dict) -> bool:
    """Store the browser's push subscription object on the player's Firestore document."""
    try:
        from services.db_service import get_db
        get_db().collection("players").document(str(player_id)).update(
            {"push_subscription": subscription}
        )
    except Exception:
        logger.exception("push_service: failed to save subscription for player %s", player_id)
        return False
    try:
        _invalidate_players_cache()
    except Exception:
        logger.warning(
            "push_service: players cache invalidation failed after saving subscription for player %s",
            player_id, exc_info=True,
        )
    return True


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
    db = get_db()
    if db is None:
        return None
    doc = db.collection("players").document(str(player_id)).get()
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

        return _deliver(player_id, sub, title, body) == "sent"
    except Exception as e:
        logger.warning("push_service: send failed for player %s: %s", player_id, e)
        return False


def _player_doc_id_to_int(doc_id):
    try:
        return int(doc_id)
    except (TypeError, ValueError):
        return doc_id


def send_to_subscribers(build_message, *, pref=None, url=None) -> dict:
    """Send a per-player message to every player with a push_subscription.

    build_message(player_id: int) -> (title, body) | None (None skips the
    player). A player whose players.push_prefs[pref] is False is also skipped.
    Returns {"counts": {total, sent, failed, pruned, skipped},
    "messages": {player_id: {title, body, status}}}.
    """
    counts = {"total": 0, "sent": 0, "failed": 0, "pruned": 0, "skipped": 0}
    messages = {}
    if not is_configured():
        logger.warning("push_service: VAPID not configured, nothing sent")
        return {"counts": counts, "messages": messages}
    from services.db_service import get_db
    db = get_db()
    if db is None:
        logger.warning("push_service: no database (local data mode), nothing sent")
        return {"counts": counts, "messages": messages}
    for doc in db.collection("players").stream():
        data = doc.to_dict() or {}
        sub = data.get("push_subscription")
        if not isinstance(sub, dict):
            continue
        prefs = data.get("push_prefs")
        if pref and isinstance(prefs, dict) and prefs.get(pref) is False:
            counts["skipped"] += 1
            continue
        pid = _player_doc_id_to_int(doc.id)
        try:
            msg = build_message(pid)
            if not msg:
                counts["skipped"] += 1
                continue
            if not (isinstance(msg, (tuple, list)) and len(msg) == 2
                    and all(isinstance(x, str) for x in msg)):
                raise ValueError("build_message must return a (title, body) pair of strings")
            title, body = msg
            status = _deliver(doc.id, sub, title, body, url) if url else _deliver(doc.id, sub, title, body)
            if status not in ("sent", "failed", "pruned"):
                status = "failed"
            counts["total"] += 1
            counts[status] += 1
            messages[pid] = {"title": title, "body": body, "status": status}
        except Exception:
            logger.exception("push_service: send failed for player %s", pid)
            counts["total"] += 1
            counts["failed"] += 1
            messages[pid] = {"title": None, "body": None, "status": "failed"}
    logger.info(
        "push_service: broadcast complete pref=%s total=%d sent=%d failed=%d pruned=%d skipped=%d",
        pref, counts["total"], counts["sent"], counts["failed"], counts["pruned"], counts["skipped"],
    )
    return {"counts": counts, "messages": messages}


def broadcast_push_notification(title: str, body: str, *, pref=None, url=None) -> dict:
    """Send title/body to every player with a stored push_subscription.

    Returns {"total", "sent", "failed", "pruned"} where total counts only
    players that actually had a subscription. One bad subscription never
    stops the loop.
    """
    result = send_to_subscribers(lambda _pid: (title, body), pref=pref, url=url)
    c = result["counts"]
    return {"total": c["total"], "sent": c["sent"], "failed": c["failed"], "pruned": c["pruned"]}


def get_push_prefs(player_id: int) -> dict:
    from services.db_service import get_db
    db = get_db()
    prefs = {}
    if db is not None:
        snap = db.collection("players").document(str(player_id)).get()
        prefs = ((snap.to_dict() or {}).get("push_prefs") or {}) if snap.exists else {}
    return {"recap": prefs.get("recap") is not False, "standings": prefs.get("standings") is not False}


def set_push_prefs(player_id: int, recap: bool, standings: bool) -> bool:
    from services.db_service import get_db
    db = get_db()
    if db is None:
        return False
    try:
        db.collection("players").document(str(player_id)).update(
            {"push_prefs": {"recap": bool(recap), "standings": bool(standings)}})
    except Exception:
        logger.exception("push_service: failed to save push prefs for player %s", player_id)
        return False
    try:
        _invalidate_players_cache()
    except Exception:
        logger.warning("push_service: players cache invalidation failed after prefs save", exc_info=True)
    return True


def has_subscription(player_id: int) -> bool:
    try:
        return isinstance(_get_push_subscription(player_id), dict)
    except Exception:
        logger.warning("push_service: subscription lookup failed for %s", player_id, exc_info=True)
        return False
