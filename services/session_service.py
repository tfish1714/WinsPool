"""services/session_service.py — JWT session tokens and FastAPI auth dependencies."""
import logging
import os
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


def create_token(player_id: int, role: str) -> str:
    """Create a signed JWT containing player_id, role, issued-at, and expiry (7 days)."""
    now = int(time.time())
    payload = {
        "sub": str(player_id),
        "role": role,
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


def _resolve_token(authorization: str | None, session_token: str | None) -> str:
    """Extract a raw JWT from either the Authorization header or the session cookie.

    Priority: Bearer header > session cookie.
    Raises HTTP 401 if neither is present or the header is malformed.
    """
    if authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
        return authorization.removeprefix("Bearer ")
    if session_token:
        return session_token
    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")


def require_auth(
    authorization: str = Header(default=None),
    session_token: str = Cookie(default=None),
) -> dict:
    """FastAPI dependency: validates a Bearer JWT or session cookie (any role).

    Checks the Authorization: Bearer header first; falls back to the
    session_token httpOnly cookie set by the login endpoint.  Returns the
    decoded payload dict on success.
    Raises HTTP 401 for missing, malformed, or expired tokens.
    """
    token = _resolve_token(authorization, session_token)
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token.")
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
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return payload
