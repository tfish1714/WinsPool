"""services/session_service.py — JWT session tokens and FastAPI auth dependencies."""
import logging
import os
import time

import jwt
from fastapi import Depends, Header, HTTPException

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
    now = int(time.time())
    payload = {
        "sub": str(player_id),
        "role": role,
        "iat": now,
        "exp": now + _TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, _get_secret(), algorithms=[JWT_ALGORITHM])


def require_auth(authorization: str = Header(default=None)) -> dict:
    """FastAPI dependency: validates a Bearer JWT (any role).

    Returns the decoded payload dict on success.
    Raises HTTP 401 for missing, malformed, or expired tokens.
    Use this on endpoints that any logged-in pool member may access.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    return payload


def require_admin(authorization: str = Header(default=None)) -> dict:
    """FastAPI dependency: validates a Bearer JWT and asserts admin role."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    if payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    return payload
