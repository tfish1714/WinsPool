"""routes/auth_routes.py — Authentication and player profile endpoints."""
import hashlib
import logging
import os
import re
import secrets
import time

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from routes.models import (
    SetPasswordRequest, LoginRequest, UpdateProfileRequest, MfaVerifyRequest,
)
from services.db_service import (
    get_player_by_email, verify_password, get_password_hash, _is_legacy_sha256,
    update_player_credentials, increment_failed_setup_attempts, update_player_profile,
)
from services.constants import PASSWORD_COMPLEXITY_RE
from services.session_service import create_token, _TOKEN_EXPIRY_SECONDS
import services.email_service as email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_IS_PROD = os.environ.get("ENVIRONMENT", "development").lower() == "production"


def _set_session_cookie(response: JSONResponse, token: str) -> JSONResponse:
    """Attach the session_token httpOnly cookie to a JSONResponse."""
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=_IS_PROD,          # https-only in production, http-ok in dev
        max_age=_TOKEN_EXPIRY_SECONDS,
        path="/",
    )
    return response


@router.get("/check_player")
async def check_player(email: str):
    """Checks if a player exists and if they already have a password set.

    Always returns HTTP 200 to prevent email enumeration. If the email is
    not found, returns exists=False with no additional metadata.
    """
    if not email:
        return JSONResponse(status_code=400, content={"error": "Email is required."})

    email = email.strip().lower()
    player = get_player_by_email(email)

    if not player:
        return JSONResponse(content={"exists": False})

    return JSONResponse(content={
        "exists": True,
        "has_password": bool(player.get("password_hash")),
        "playerName": player.get("fullName")
    })


@router.post("/set_password")
async def set_password(body: SetPasswordRequest):
    try:
        email = body.email.strip().lower()
        password = body.password
        confirm_password = body.confirm_password

        if not email or not password or not confirm_password:
            return JSONResponse(status_code=400, content={"error": "Missing required fields."})

        player = get_player_by_email(email)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Email not found in player database."})

        lockout = player.get("lockout_until")
        if lockout and time.time() < lockout:
            rem = int((lockout - time.time()) // 60)
            return JSONResponse(status_code=429, content={"error": f"Account locked. Try again in {rem} minutes."})

        if player.get("password_hash"):
            return JSONResponse(status_code=400, content={"error": "Account already claimed. Please log in."})

        def _record_setup_failure():
            fails = int(player.get("failed_setup_attempts", 0)) + 1
            lockout_ts = time.time() + 1800 if fails >= 5 else None
            increment_failed_setup_attempts(str(player["playerId"]), fails, lockout_ts)
            return fails >= 5

        if password != confirm_password:
            locked = _record_setup_failure()
            if locked:
                return JSONResponse(status_code=429, content={"error": "Too many failed attempts. Account locked for 30 minutes."})
            return JSONResponse(status_code=400, content={"error": "Passwords do not match."})

        if not re.match(PASSWORD_COMPLEXITY_RE, password):
            locked = _record_setup_failure()
            if locked:
                return JSONResponse(status_code=429, content={"error": "Too many failed attempts. Account locked for 30 minutes."})
            return JSONResponse(status_code=400, content={"error": "Password does not meet the complexity requirements (12+ characters, uppercase, lowercase, number, symbol)."})

        hashed = get_password_hash(password)
        update_player_credentials(str(player["playerId"]), hashed)

        role = player.get("role", "user")
        token = create_token(int(player["playerId"]), role)
        resp = JSONResponse(content={
            "status": "success",
            "message": "Password setup securely! Redirecting...",
            "playerId": str(player["playerId"]),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": role,
            "token": token,
        })
        return _set_session_cookie(resp, token)
    except Exception as e:
        logger.exception("Unhandled error in set_password")
        return JSONResponse(status_code=500, content={"error": "An internal error occurred."})


@router.post("/login")
async def login(body: LoginRequest):
    try:
        email = body.email.strip().lower()
        password = body.password

        player = get_player_by_email(email)
        if not player:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        if not player.get("password_hash"):
            return JSONResponse(status_code=400, content={"error": "Account not claimed yet. Please set a password."})

        lockout = player.get("lockout_until")
        if lockout and time.time() < lockout:
            rem = int((lockout - time.time()) // 60)
            return JSONResponse(status_code=429, content={"error": f"Account locked. Try again in {rem} minutes."})

        is_valid = verify_password(password, player.get("password_hash"))
        if not is_valid:
            fails = int(player.get("failed_login_attempts", 0)) + 1
            lockout_ts = time.time() + 1800 if fails >= 5 else None
            update_player_profile(str(player["playerId"]), {
                "failed_login_attempts": fails,
                **(({"lockout_until": lockout_ts}) if lockout_ts else {})
            })
            if lockout_ts:
                return JSONResponse(status_code=429, content={"error": "Too many failed login attempts. Account locked for 30 minutes."})
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        reset_fields = {"failed_login_attempts": 0, "lockout_until": None}

        stored_hash = player.get("password_hash", "")
        if _is_legacy_sha256(stored_hash):
            reset_fields["password_hash"] = get_password_hash(password)

        update_player_profile(str(player["playerId"]), reset_fields)

        if player.get("must_change_password"):
            return JSONResponse(content={
                "status": "must_change_password",
                "playerId": str(player["playerId"]),
                "message": "You must change your temporary password before continuing."
            })

        if player.get("mfa_enabled"):
            mfa_code = "".join([str(secrets.randbelow(10)) for _ in range(6)])
            mfa_token_hash = hashlib.sha256(mfa_code.encode()).hexdigest()
            update_player_profile(str(player["playerId"]), {
                "mfa_token": mfa_token_hash,
                "mfa_expiry": time.time() + 600
            })
            player_email = player.get("email")
            if player_email:
                email_service.send_mfa_code_email(player_email, mfa_code)
            return JSONResponse(content={
                "status": "mfa_required",
                "playerId": str(player["playerId"]),
                "message": "A 6-digit verification code has been sent to your email."
            })

        role = player.get("role", "user")
        token = create_token(int(player["playerId"]), role)
        resp = JSONResponse(content={
            "status": "success",
            "playerId": str(player["playerId"]),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": role,
            "token": token,
        })
        return _set_session_cookie(resp, token)
    except Exception as e:
        logger.exception("Unhandled error in login")
        return JSONResponse(status_code=500, content={"error": "An internal error occurred."})


@router.post("/logout")
async def logout():
    """Clear the server-side session cookie."""
    resp = JSONResponse(content={"status": "ok"})
    resp.delete_cookie(key="session_token", path="/")
    return resp


@router.get("/profile")
async def get_profile(playerId: str):
    """Fetch current player profile data for pre-filling the form."""
    from services.db_service import get_player_by_id
    player = get_player_by_id(playerId)
    if not player:
        return JSONResponse(status_code=404, content={"error": "Player not found."})

    return {
        "playerId": str(playerId),
        "fullName": player.get("fullName"),
        "nickName": player.get("nickName"),
        "email": player.get("email"),
        "role": player.get("role", "user"),
        "mfa_enabled": bool(player.get("mfa_enabled"))
    }


@router.post("/profile/update")
async def update_profile(body: UpdateProfileRequest):
    try:
        pid = body.playerId
        full_name = body.fullName.strip()
        nickname = body.nickName.strip()
        new_email = body.email.strip().lower()
        curr_password = body.currentPassword
        new_password = body.newPassword
        mfa_enabled = body.mfaEnabled

        if not pid or not curr_password:
            return JSONResponse(status_code=400, content={"error": "Missing player ID or current password."})

        from services.db_service import get_player_by_id
        player = get_player_by_id(pid)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Player not found."})

        if not verify_password(curr_password, player.get("password_hash")):
            return JSONResponse(status_code=401, content={"error": "Incorrect current password."})

        if new_email and new_email != player.get("email"):
            collision = get_player_by_email(new_email)
            if collision:
                return JSONResponse(status_code=400, content={"error": "Email is already in use by another account."})

        updates = {}
        if full_name:
            updates["fullName"] = full_name
        if nickname:
            updates["nickName"] = nickname
        if new_email:
            updates["email"] = new_email
        if new_password:
            if not re.match(PASSWORD_COMPLEXITY_RE, new_password):
                return JSONResponse(status_code=400, content={"error": "New password too weak."})
            updates["password_hash"] = get_password_hash(new_password)

        updates["mfa_enabled"] = bool(mfa_enabled)

        if updates:
            update_player_profile(str(pid), updates)

        return JSONResponse(content={"message": "Profile updated successfully!"})
    except Exception as e:
        logger.exception("Unhandled error updating profile")
        return JSONResponse(status_code=500, content={"error": "An internal error occurred."})


@router.post("/mfa/verify")
async def verify_mfa(body: MfaVerifyRequest):
    try:
        pid = body.playerId
        code = body.code

        if not pid or not code:
            return JSONResponse(status_code=400, content={"error": "Missing player ID or verification code."})

        from services.db_service import get_player_by_id
        player = get_player_by_id(pid)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Player not found."})
        stored_hash = player.get("mfa_token")
        expiry = player.get("mfa_expiry", 0)

        if not stored_hash or time.time() > expiry:
            return JSONResponse(status_code=401, content={"error": "MFA code expired or invalid."})

        submitted_hash = hashlib.sha256(str(code).encode()).hexdigest()
        if submitted_hash != stored_hash:
            return JSONResponse(status_code=401, content={"error": "Incorrect verification code."})

        update_player_profile(pid, {"mfa_token": None, "mfa_expiry": 0})

        role = player.get("role", "user")
        token = create_token(int(pid), role)
        resp = JSONResponse(content={
            "status": "success",
            "playerId": str(pid),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": role,
            "token": token,
        })
        return _set_session_cookie(resp, token)
    except Exception as e:
        logger.exception("Unhandled error in MFA flow")
        return JSONResponse(status_code=500, content={"error": "An internal error occurred."})
