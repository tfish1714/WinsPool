"""services/session_service.py — JWT session tokens and FastAPI auth dependencies."""
import logging
import os
import threading
import time

import jwt
from fastapi import Cookie, Depends, Header, HTTPException

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
_TOKEN_EXPIRY_SECONDS = 86400 * 7  # 7 days

_INSECURE_DEFAULT = "dev-insecure-secret-change-in-production"


def _get_secret() -> str:
    """Return the JWT signing secret.

    Reads JWT_SECRET from the environment on every call (no caching) so that
    monkeypatch in tests and runtime env-var changes take effect immediately.

    Raises RuntimeError if the secret is absent or is the well-known insecure
    development placeholder — a misconfigured production deploy fails loudly
    rather than silently accepting forged tokens.
    """
    secret = os.environ.get("JWT_SECRET", "")
    if not secret or secret == _INSECURE_DEFAULT:
        raise RuntimeError(
            "JWT_SECRET environment variable is not configured or is using the "
            "insecure development default. Set a strong secret before running "
            "(e.g. export JWT_SECRET=$(openssl rand -hex 32))."
        )
    return secret


def create_token(player_id: int, role: str, token_version: int = 0) -> str:
    """Create a signed JWT containing player_id, role, issued-at, expiry (7 days)
    and `tv`, the player's token_version at issue time.

    A password change bumps the stored token_version, which makes every token
    minted with an older `tv` fail `token_is_current` (session revocation).
    """
    now = int(time.time())
    payload = {
        "sub": str(player_id),
        "role": role,
        "tv": int(token_version or 0),
        "iat": now,
        "exp": now + _TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT, returning the payload dict.

    Raises jwt.ExpiredSignatureError if the token is expired, and
    jwt.InvalidTokenError for any other verification failure.
    """
    return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])


def _as_version(value) -> int:
    """Coerce a stored/claimed token version to int; missing, None or NaN (an
    unset Firestore field arriving through pandas) count as 0."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def token_is_current(payload: dict, player: dict | None) -> bool:
    """True iff the token's `tv` claim matches the player's stored token_version.

    A token without `tv` is treated as 0 and a player without a stored version
    as 0, so tokens issued before revocation existed keep working until that
    player's password next changes. A missing player (deleted) is never current.
    """
    if player is None:
        return False
    return _as_version(payload.get("tv")) == _as_version(player.get("token_version"))


class SessionCheckUnavailable(Exception):
    """The players data needed to validate a session could not be read
    (lookup exception, or no players data loaded). This is an infrastructure
    failure, distinct from a real revocation (data loaded, player missing or
    token_version mismatch)."""


def _load_player_from_db(player_id: int):
    from services.db_service import _get_players_df
    players_df = _get_players_df()
    if players_df is None or players_df.empty or "playerId" not in players_df.columns:
        raise SessionCheckUnavailable("players data unavailable")
    from services.db_service import get_player_by_id
    return get_player_by_id(player_id)


def _lookup_player(player_id: int):
    """Indirection over the players lookup (tests stub this)."""
    return _load_player_from_db(player_id)


def _payload_is_current(payload: dict) -> bool:
    """Look up the token's player and apply token_is_current.

    Returns False for a real revocation (player row missing, tv mismatch) or a
    non-numeric `sub`. Raises SessionCheckUnavailable when the players data
    cannot be read at all, so callers can answer 503 instead of treating a
    backend blip as a dead session."""
    try:
        player_id = int(payload["sub"])
    except Exception:
        return False
    try:
        player = _lookup_player(player_id)
    except Exception:
        logger.warning("Token version check unavailable", exc_info=True)
        raise SessionCheckUnavailable("players lookup failed")
    return token_is_current(payload, player)


_UNAVAILABLE_DETAIL = "Session check temporarily unavailable."


def _require_current(payload: dict) -> None:
    """401 revoked if the token was revoked; 503 (no X-Session-State header, so
    the client guard leaves the user signed in) if the check cannot be made."""
    try:
        current = _payload_is_current(payload)
    except SessionCheckUnavailable:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
    if not current:
        raise _dead_session(_REVOKED_DETAIL, "revoked")


def decode_current_token(token: str) -> dict | None:
    """Decode a token and confirm it has not been revoked. Never raises;
    returns None for any invalid, expired or revoked token. For the
    cookie-only trust sites (page routes) that don't use the dependencies."""
    try:
        payload = decode_token(token)
    except Exception:
        return None
    try:
        return payload if _payload_is_current(payload) else None
    except SessionCheckUnavailable:
        return None


_REVOKED_DETAIL = "Session is no longer valid. Please log in again."


def _dead_session(detail: str, state: str) -> HTTPException:
    """401 for a rejected session, with a stable X-Session-State header
    (missing|invalid|expired|revoked) the client auth guard keys off instead of
    matching the human-readable detail text."""
    return HTTPException(status_code=401, detail=detail, headers={"X-Session-State": state})


def _resolve_token(authorization: str | None, session_token: str | None) -> str:
    """Extract a raw JWT from either the Authorization header or the session cookie.

    Priority: Bearer header > session cookie.
    Raises HTTP 401 if neither is present or the header is malformed.
    """
    if authorization:
        if not authorization.startswith("Bearer "):
            raise _dead_session("Missing or invalid Authorization header.", "missing")
        return authorization.removeprefix("Bearer ")
    if session_token:
        return session_token
    raise _dead_session("Missing or invalid Authorization header.", "missing")


_ACTIVITY_THROTTLE_SECONDS = 900  # persist last_active at most once per player per 15 minutes
_LAST_ACTIVE_CACHE: dict[int, float] = {}  # player_id -> when it was last persisted (this process only)


def _run_in_background(fn, *args) -> None:
    """Run `fn(*args)` on a daemon thread so a Firestore round trip never
    sits in the request path. Tests replace this with a synchronous call."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def _persist_activity(player_id: int, ts: float) -> None:
    try:
        from services.db_service import record_player_activity
        record_player_activity(player_id, ts)
    except Exception:
        logger.warning("Failed to record activity for player %s", player_id, exc_info=True)


def record_user_activity(player_id: int) -> None:
    """Stamp `last_active` for a player, at most once per throttle window.

    The throttle cache is stamped before the write is handed off, so a burst
    of concurrent requests from one player produces a single write. A failed
    write is logged and not retried until the next window -- this is a coarse
    "last seen" indicator, not a record that must never be missed.
    """
    now = time.time()
    if now - _LAST_ACTIVE_CACHE.get(player_id, 0.0) < _ACTIVITY_THROTTLE_SECONDS:
        return
    _LAST_ACTIVE_CACHE[player_id] = now
    _run_in_background(_persist_activity, player_id, now)


def _track_activity(payload: dict) -> None:
    """Record activity for a successfully authenticated payload. Never raises:
    tracking must not be able to fail the request it is observing."""
    try:
        record_user_activity(int(payload["sub"]))
    except Exception:
        logger.debug("Skipping activity tracking for payload without a numeric sub", exc_info=True)


def require_auth(
    authorization: str = Header(default=None),
    session_token: str = Cookie(default=None),
) -> dict:
    """FastAPI dependency: validates a Bearer JWT or session cookie (any role).

    Checks the Authorization: Bearer header first; falls back to the
    session_token httpOnly cookie set by the login endpoint.  Returns the
    decoded payload dict on success, and records the player's last_active
    (throttled -- see record_user_activity).
    Raises HTTP 401 for missing, malformed, or expired tokens.
    """
    token = _resolve_token(authorization, session_token)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise _dead_session("Session expired. Please log in again.", "expired")
    except Exception:
        raise _dead_session("Invalid session token.", "invalid")
    _require_current(payload)
    _track_activity(payload)
    return payload


def require_admin(
    authorization: str = Header(default=None),
    session_token: str = Cookie(default=None),
) -> dict:
    """FastAPI dependency: validates a Bearer JWT or session cookie, asserts admin role."""
    token = _resolve_token(authorization, session_token)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise _dead_session("Session expired. Please log in again.", "expired")
    except Exception:
        raise _dead_session("Invalid session token.", "invalid")
    _require_current(payload)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    _track_activity(payload)
    return payload


def get_is_admin(
    authorization: str = Header(default=None),
    session_token: str = Cookie(default=None),
) -> bool:
    """FastAPI dependency: True if the request carries a valid, non-expired
    admin JWT (Bearer header or session cookie). Never raises — for
    endpoints that must work for anonymous callers and only conditionally
    include admin-only data (e.g. the mock draft).
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    elif session_token:
        token = session_token
    if not token:
        return False
    try:
        payload = decode_token(token)
    except Exception:
        return False
    if payload.get("role") != "admin":
        return False
    try:
        return _payload_is_current(payload)
    except SessionCheckUnavailable:
        return False
