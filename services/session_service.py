"""services/session_service.py — JWT session tokens and FastAPI auth dependencies."""
import logging
import os
import time

import jwt
from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
_TOKEN_EXPIRY_SECONDS = 86400 * 7  # 7 days

_secret: str | None = None


def _get_secret() -> str:
    global _secret
    if _secret is None:
        _secret = os.environ.get("JWT_SECRET", "dev-insecure-secret-change-in-production")
        if _secret == "dev-insecure-secret-change-in-production":
            logger.warning("JWT_SECRET is not set — using insecure development default.")
    return _secret


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
