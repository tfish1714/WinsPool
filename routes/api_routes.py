"""routes/api_routes.py — JSON API endpoints."""
import os
import random
import sys
import subprocess
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.data_service import (
    load_data, get_latest_season_and_week, get_season_progress,
)
from services.db_service import (
    get_collection_df, add_draft_order, add_draft_rule, delete_draft_results_for_season,
    get_player_by_email, verify_password, get_password_hash, 
    update_player_credentials, increment_failed_setup_attempts,
    update_player_profile, add_player, delete_season_data, get_player_role
)
from services.draft_service import load_draft_state, wipe_draft_cache, sanitize_state
import services.analysis_service as analysis
import services.recap_service as recap_service
import services.ai_service as ai_service
import services.email_service as email_service
import time
import re

router = APIRouter(prefix="/api")

# ADMIN_CODE removed - using role-based auth viaplayerId


@router.get("/progress/{season}/{week}")
def fetch_progress(season: str, week: str):
    """Chart data: cumulative player wins by week for the given season."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        _, _, games, _, _, _, _ = load_data()

        if games.empty or "season" not in games.columns:
            return JSONResponse(content={"labels": [], "datasets": []})

        if season.lower() == "latest":
            target_season, _ = get_latest_season_and_week(games)
        else:
            target_season = int(season)

        if week.lower() == "latest":
            s_games = games[games["season"] == target_season]
            target_week = int(s_games["week"].max()) if not s_games.empty else 18
        else:
            target_week = int(week)

        res = get_season_progress(target_season, target_week)
        if is_debug:
            print(f"[DEBUG_PAGE_LOAD] /api/progress route total took {time.time() - start_route:.3f}s")
        return JSONResponse(content=res)
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/progress/draft_summary")
def fetch_draft_summary():
    """Best-picks summary for the current season (used by draft board tab)."""
    try:
        _, _, games, _, _, _, _ = load_data()
        s, w = get_latest_season_and_week(games)
        data = get_season_progress(s, w)
        return JSONResponse(content={
            "season": s, "week": w,
            "best_overall": data.get("best_overall"),
            "best_by_round": data.get("best_by_round"),
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/standings")
def get_standings(year: int):
    """Data for the standings table."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        standings, _, _, players, _, draft_results, _ = load_data(year=year)
        sorted_df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year)
        # Ensure we return expected keys for UiRenderer if it's being used
        data = sorted_df.to_dict(orient="records")
        for row in data:
            row['entrant'] = row.get('fullName')
            row['total_wins'] = row.get('TotalWins')
        if is_debug:
            print(f"[DEBUG_PAGE_LOAD] /api/standings route total took {time.time() - start_route:.3f}s")
        return JSONResponse(content=sanitize_state(data))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/schedule")
def get_schedule(year: int):
    """Data for the week-by-week schedule grid."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        _, _, games, players, _, draft_results, _ = load_data(year=year)
        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        if is_debug:
            print(f"[DEBUG_PAGE_LOAD] /api/schedule route total took {time.time() - start_route:.3f}s")
        return JSONResponse(content=sanitize_state(schedule_enriched.to_dict(orient="records")))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/check_player")
async def check_player(email: str):
    """Checks if a player exists and if they already have a password set."""
    if not email:
        return JSONResponse(status_code=400, content={"error": "Email is required."})
    
    email = email.strip().lower()
    player = get_player_by_email(email)
    
    if not player:
        return JSONResponse(status_code=404, content={"exists": False})
    
    return {
        "exists": True,
        "has_password": bool(player.get("password_hash")),
        "playerName": player.get("fullName")
    }

@router.post("/set_password")
async def set_password(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        password = data.get("password")
        confirm_password = data.get("confirm_password")

        if not email or not password or not confirm_password:
            return JSONResponse(status_code=400, content={"error": "Missing required fields."})

        if password != confirm_password:
            return JSONResponse(status_code=400, content={"error": "Passwords do not match."})

        pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
        if not re.match(pw_regex, password):
            return JSONResponse(status_code=400, content={"error": "Password does not meet the complexity requirements (12+ characters, uppercase, lowercase, number, symbol)."})

        player = get_player_by_email(email)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Email not found in player database."})

        # Check Active Lockout
        lockout = player.get("lockout_until")
        if lockout and time.time() < lockout:
            rem = int((lockout - time.time()) // 60)
            return JSONResponse(status_code=429, content={"error": f"Account locked remotely. Try again in {rem} minutes."})

        if player.get("password_hash"):
            return JSONResponse(status_code=400, content={"error": "Account already claimed. Please log in."})

        # Formal Password Assignment
        hashed = get_password_hash(password)
        update_player_credentials(str(player["playerId"]), hashed)

        role = player.get("role", "user")

        return JSONResponse(content={
            "message": "Password setup securely! Redirecting...",
            "playerId": str(player["playerId"]),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": role
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/login")
async def login(request: Request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        password = data.get("password")

        player = get_player_by_email(email)
        print(f"DEBUG LOGIN: Searching for email '{email}'. Found player: {player is not None}")
        
        if not player:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        if not player.get("password_hash"):
            print(f"DEBUG LOGIN: Player {email} has no password set.")
            return JSONResponse(status_code=400, content={"error": "Account not claimed yet. Please set a password."})

        is_valid = verify_password(password, player.get("password_hash"))
        print(f"DEBUG LOGIN: Password check for {email} resolved to: {is_valid}")
        if not is_valid:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        # Check for MFA
        if player.get("mfa_enabled"):
            import random
            mfa_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
            update_player_profile(str(player["playerId"]), {
                "mfa_token": mfa_code,
                "mfa_expiry": time.time() + 600
            })
            print(f"DEBUG: MFA Code for {email} is {mfa_code}")
            return JSONResponse(content={
                "status": "mfa_required",
                "playerId": str(player["playerId"]),
                "message": "A 6-digit verification code has been sent to your email."
            })

        role = player.get("role", "user")

        return JSONResponse(content={
            "status": "success",
            "playerId": str(player["playerId"]),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": role
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


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
async def update_profile(request: Request):
    try:
        data = await request.json()
        pid = data.get("playerId")
        full_name = data.get("fullName", "").strip()
        nickname = data.get("nickName", "").strip()
        new_email = data.get("email", "").strip().lower()
        curr_password = data.get("currentPassword")
        new_password = data.get("newPassword")
        mfa_enabled = data.get("mfaEnabled", False)

        if not pid or not curr_password:
            return JSONResponse(status_code=400, content={"error": "Missing player ID or current password."})

        # 1. Fetch current player (not by email, but by ID to be safe during email change)
        from services.db_service import get_player_by_id
        player = get_player_by_id(pid)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Player not found."})
        
        # 2. Verify current password
        if not verify_password(curr_password, player.get("password_hash")):
            return JSONResponse(status_code=401, content={"error": "Incorrect current password."})

        # 3. Check for email collision if changing
        if new_email and new_email != player.get("email"):
            collision = get_player_by_email(new_email)
            if collision:
                return JSONResponse(status_code=400, content={"error": "Email is already in use by another account."})

        # 4. Prepare updates
        updates = {}
        if full_name:
            updates["fullName"] = full_name
        if nickname:
            updates["nickName"] = nickname
        if new_email:
            updates["email"] = new_email
        if new_password:
            # Complexity check
            pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
            if not re.match(pw_regex, new_password):
                 return JSONResponse(status_code=400, content={"error": "New password too weak."})
            updates["password_hash"] = get_password_hash(new_password)
        
        # MFA placeholder logic
        updates["mfa_enabled"] = bool(mfa_enabled)

        if updates:
            update_player_profile(str(pid), updates)

        return JSONResponse(content={"message": "Profile updated successfully!"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/mfa/verify")
async def verify_mfa(request: Request):
    try:
        data = await request.json()
        pid = data.get("playerId")
        code = data.get("code")

        if not pid or not code:
             return JSONResponse(status_code=400, content={"error": "Missing player ID or verification code."})

        from services.db_service import get_player_by_id
        player = get_player_by_id(pid)
        if not player:
             return JSONResponse(status_code=404, content={"error": "Player not found."})
        stored_code = player.get("mfa_token")
        expiry = player.get("mfa_expiry", 0)

        if not stored_code or time.time() > expiry:
            return JSONResponse(status_code=401, content={"error": "MFA code expired or invalid."})

        if code != stored_code:
            return JSONResponse(status_code=401, content={"error": "Incorrect verification code."})

        # Clear code on success
        update_player_profile(pid, {
            "mfa_token": None,
            "mfa_expiry": 0
        })

        return JSONResponse(content={
            "status": "success",
            "playerId": str(pid),
            "playerName": player.get("fullName"),
            "email": player.get("email"),
            "role": player.get("role", "user")
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/record_failed_setup")
async def record_failed_setup(request: Request):
    """Fires mechanically from Javascript when Passwords mismatch, enforcing robust Rate-Limites"""
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        
        player = get_player_by_email(email)
        if not player:
            return JSONResponse(status_code=404, content={"error": "User not found"})
        
        fails = int(player.get("failed_setup_attempts", 0)) + 1
        lockout = None
        if fails >= 5:
            lockout = time.time() + 1800 # 30 mins
        
        increment_failed_setup_attempts(str(player["playerId"]), fails, lockout)

        if lockout:
            return JSONResponse(status_code=429, content={"error": "Too many failed setup attempts. Account locked out for 30 minutes.", "locked": True})
        
        return JSONResponse(content={"message": "Failed attempt recorded.", "attempts": fails})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/new_season")
async def create_new_season(request: Request):
    """Generate a randomized draft order for a new season."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})

        season = int(data.get("season"))
        player_ids = data.get("playerIds", [])

        if not player_ids:
            return JSONResponse(status_code=400, content={"error": "At least one player must be selected."})

        order_df = get_collection_df("draft_order")
        existing = order_df[order_df["season"] == season] if not order_df.empty and "season" in order_df.columns else []
        if len(existing):
            return JSONResponse(status_code=400, content={"error": f"Season {season} already exists."})

        # Shuffle the selected players
        random.shuffle(player_ids)

        for idx, pid in enumerate(player_ids):
            add_draft_order(season, idx + 1, int(pid))

        rules_df = get_collection_df("draft_order_rules")
        if not rules_df.empty and "season" in rules_df.columns:
            prev_s = int(rules_df["season"].max())
            for _, r in rules_df[rules_df["season"] == prev_s].iterrows():
                # Only copy rules if the draftOrder exists in our new selection
                if int(r["draftOrder"]) <= len(player_ids):
                    add_draft_rule(season, int(r["draftOrder"]), int(r["pickOne"]), int(r["pickTwo"]), int(r["pickThree"]))

        wipe_draft_cache()
        return JSONResponse(content={"message": f"Draft order for {season} created successfully with {len(player_ids)} players."})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/admin/players")
async def fetch_admin_players(playerId: str):
    """Retrieve all players for the admin selection grid."""
    if get_player_role(playerId) != "admin":
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    try:
        _, _, _, players_df, _, _, _ = load_data()
        players = players_df.to_dict(orient="records")
        # Ensure standard JSON
        return JSONResponse(content=sanitize_state(players))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/admin/seasons")
async def fetch_admin_seasons(playerId: str):
    """Retrieve all years that have at least some draft data (order or results)."""
    if get_player_role(playerId) != "admin":
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    try:
        standings_df, _, games_df, _, draft_order_df, draft_results_df, _ = load_data()
        
        seasons = set()
        if not draft_order_df.empty and "season" in draft_order_df.columns:
            seasons.update(draft_order_df["season"].unique().tolist())
        if not draft_results_df.empty and "season" in draft_results_df.columns:
            seasons.update(draft_results_df["season"].unique().tolist())
            
        return JSONResponse(content={"seasons": sorted([int(s) for s in seasons], reverse=True)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/preview_draft_order")
async def preview_draft_order(request: Request):
    """Randomize the selected players and return the order for review without saving."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        player_ids = data.get("playerIds", [])
        if not player_ids:
            return JSONResponse(status_code=400, content={"error": "No players selected."})

        # Shuffle
        random.shuffle(player_ids)
        
        # Get player names for display
        _, _, _, players_df, _, _, _ = load_data()
        preview = []
        for idx, pid in enumerate(player_ids):
            p_row = players_df[players_df["playerId"] == int(pid)]
            p_name = p_row["fullName"].iloc[0] if not p_row.empty else f"Unknown ({pid})"
            preview.append({"order": idx + 1, "playerName": p_name, "playerId": int(pid)})
            
        return JSONResponse(content={"preview": preview})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/create_player")
async def create_player(request: Request):
    """Admin-only: Create a new player."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        full_name = data.get("fullName")
        nick_name = data.get("nickName")
        email = data.get("email")
        phone = data.get("phone", "")

        if not full_name or not nick_name or not email:
            return JSONResponse(status_code=400, content={"error": "Name, Nickname, and Email are required."})

        new_id = add_player(full_name, nick_name, email, phone=phone)
        return JSONResponse(content={"message": f"Player created with ID {new_id}", "playerId": new_id})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/update_player")
async def admin_update_player(request: Request):
    """Admin-only: Update an existing player's profile fields."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        target_id = data.get("targetPlayerId")
        if not target_id:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId is required."})

        allowed_fields = {"fullName", "nickName", "email", "cell"}
        updates = {k: v for k, v in data.get("fields", {}).items() if k in allowed_fields}
        if not updates:
            return JSONResponse(status_code=400, content={"error": "No valid fields to update."})

        if "email" in updates:
            updates["email"] = updates["email"].strip().lower()

        update_player_profile(str(target_id), updates)
        return JSONResponse(content={"message": "Player updated successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/reset_password")
async def admin_reset_password(request: Request):
    """Admin-only: Trigger a password reset by clearing the stored hash."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        target_id = data.get("targetPlayerId")
        if not target_id:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId is required."})

        # Clear the password hash so the player is forced to set up again on next login
        update_player_profile(str(target_id), {
            "password_hash": None,
            "failed_setup_attempts": 0,
            "lockout_until": None,
            "mfa_secret": None,
            "mfa_enabled": False
        })
        return JSONResponse(content={"message": "Password reset. Player will be prompted to set a new password on next login."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/set_temp_password")
async def admin_set_temp_password(request: Request):
    """Admin-only: Set a temporary password for a player."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})

        target_id = data.get("targetPlayerId")
        temp_password = data.get("tempPassword")

        if not target_id or not temp_password:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId and tempPassword are required."})

        if len(temp_password) < 8:
            return JSONResponse(status_code=400, content={"error": "Temporary password must be at least 8 characters."})

        hashed = get_password_hash(temp_password)
        update_player_credentials(str(target_id), hashed)
        return JSONResponse(content={"message": "Temporary password set. Player should change it after logging in."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/delete_season")
async def delete_season(request: Request):
    """Wipes all draft data for a specific season."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        season = int(data.get("season"))
        delete_season_data(season)
        wipe_draft_cache()
        return JSONResponse(content={"message": f"Season {season} data wiped successfully."})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/reset_draft")
async def reset_draft(request: Request):
    """Admin sandbox feature to delete all draft results for a given season."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})

        season = int(data.get("season"))
        
        # 1. Sweep Firestore
        delete_draft_results_for_season(season)
        
        # 2. Evict Python Singleton cache mechanically
        wipe_draft_cache()
        
        return JSONResponse(content={"message": f"Draft Results for {season} securely wiped! Mock draft reset successful."})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/scrape_predictions")
async def scrape_predictions(request: Request):
    """Executes the Agentic Web Scraper to bind Preseason Vegas Odds to the database."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})

        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "upload_predictions.py"))
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"error": result.stderr or "Script failed silently."})
        
        return JSONResponse(content={"message": "Vegas Odds successfully scraped and injected into Firestore!"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/recap/preview_prompt")
async def preview_recap_prompt(request: Request):
    """Admin: Generate the data-only prompt for AI recap review."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        year = int(data.get("year"))
        week = int(data.get("week"))
        
        data_summary, _ = recap_service.extract_weekly_data(year, week)
        if not data_summary:
            return JSONResponse(status_code=404, content={"error": f"No game results found for {year} Week {week}."})
        
        full_prompt = ai_service.get_recap_prompt(data_summary)
        return JSONResponse(content={"prompt": full_prompt})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/recap/preview_draft_prompt")
async def preview_draft_recap_prompt(request: Request):
    """Admin: Generate the data-only prompt for AI draft recap review."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        year = int(data.get("year"))
        
        data_summary, _ = recap_service.extract_draft_data(year)
        if not data_summary:
            return JSONResponse(status_code=404, content={"error": f"No draft data found for {year}."})
        
        full_prompt = ai_service.get_draft_recap_prompt(data_summary)
        return JSONResponse(content={"prompt": full_prompt})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/recap/generate")
async def generate_admin_recap(request: Request):
    """Admin: Send the prompt to AI and return the generated summary."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        prompt_data = data.get("prompt_data")
        if not prompt_data:
            return JSONResponse(status_code=400, content={"error": "Prompt data is required."})
            
        summary = ai_service.generate_generic_content(prompt_data)
        return JSONResponse(content={"summary": summary})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/recap/save_and_broadcast")
async def save_and_broadcast_recap(request: Request):
    """Admin: Save the summary to DB and email all players."""
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized"})
        
        year = int(data.get("year"))
        week = int(data.get("week"))
        summary = data.get("summary")
        
        if not summary:
            return JSONResponse(status_code=400, content={"error": "Summary text is required."})

        # 1. Save to DB
        from services.db_service import save_weekly_recap
        save_weekly_recap(year, week, summary)
        
        # 2. Broadcast via Email
        _, emails = recap_service.extract_weekly_data(year, week)
        if emails:
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                    <div style="background-color: #ffffff; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                        <h2 style="color: #333;">🏈 Weekly Recap: NFL Week {week}</h2>
                        <div style="white-space: pre-wrap; color: #555; line-height: 1.6;">
                            {summary}
                        </div>
                        <p style="margin-top: 20px; font-size: 0.8em; color: #888;">
                            This recap was generated by Gemini AI for the Wins Pool. 
                        </p>
                    </div>
                </body>
            </html>
            """
            email_service.send_weekly_recap_email(emails, f"Week {week} Recap - Wins Pool", html_body)

        return JSONResponse(content={"message": f"Week {week} recap saved and broadcast to {len(emails)} players."})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# Prediction Engine Endpoints
# ---------------------------------------------------------------------------

@router.get("/predictions/game")
def get_game_prediction(home_team: str, away_team: str, season: int = 2025):
    """Public: Blended win probability for a single matchup.

    Query Params:
        home_team: Home team abbreviation (e.g. "KC").
        away_team: Away team abbreviation (e.g. "BUF").
        season: NFL season year for Elo context (default 2025).
    """
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, _, _ = load_data()
        svc = PredictionService()
        svc.initialize(games, season)
        result = svc.game_win_probability(home_team, away_team)
        return JSONResponse(content=sanitize_state(result))
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/predictions/portfolio")
def get_portfolio_projection(season: int, playerId: int):
    """Public: Monte Carlo projected wins for a player's 3-team portfolio.

    Query Params:
        season: NFL season year.
        playerId: The player whose drafted teams to project.
    """
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, draft_results, _ = load_data()

        player_teams = draft_results[
            (draft_results["season"] == season) &
            (draft_results["playerId"] == playerId)
        ]["team"].tolist()

        if not player_teams:
            return JSONResponse(
                status_code=404,
                content={"error": f"No teams drafted by player {playerId} in {season}."}
            )

        svc = PredictionService()
        svc.initialize(games, season)
        result = svc.project_portfolio_wins(player_teams, season, games)
        return JSONResponse(content=sanitize_state(result))
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/predictions/ratings")
def get_elo_ratings(season: int = 2025):
    """Public: Current Elo power ratings for all NFL teams.

    Query Params:
        season: NFL season year for Elo context (default 2025).
    """
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, _, _ = load_data()
        svc = PredictionService()
        svc.initialize(games, season)
        ratings = svc.get_all_ratings()
        return JSONResponse(content=sanitize_state(ratings))
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/admin/predictions/confidence")
def get_draft_confidence(season: int, playerId: str):
    """Admin-only: Confidence scores ranking all NFL teams for the draft room.

    Query Params:
        season: NFL season year.
        playerId: Admin player ID (for auth).
    """
    if get_player_role(playerId) != "admin":
        return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, draft_results, _ = load_data()

        # Identify teams already drafted this season
        season_drafts = draft_results[draft_results["season"] == season]
        drafted_teams = season_drafts["team"].tolist() if not season_drafts.empty and "team" in season_drafts.columns else []

        svc = PredictionService()
        svc.initialize(games, season)
        scores = svc.generate_draft_confidence_scores(season, games, drafted_teams)
        return JSONResponse(content=sanitize_state(scores))
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/admin/predictions/config")
def get_prediction_config(playerId: str):
    """Admin-only: Read the current model configuration (blend weights)."""
    if get_player_role(playerId) != "admin":
        return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})
    try:
        from services.prediction_service import PredictionService
        svc = PredictionService()
        config = svc.load_config()
        return JSONResponse(content=sanitize_state(config))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/admin/predictions/config")
async def update_prediction_config(request: Request):
    """Admin-only: Update the model blend weights.

    Request Body:
        { "playerId": "string", "elo_weight": float, "simulations": int }
    """
    try:
        data = await request.json()
        pid = data.get("playerId")
        if get_player_role(pid) != "admin":
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Admin role required."})

        elo_weight = float(data.get("elo_weight", 0.7))
        simulations = int(data.get("simulations", 1000))

        if not (0.0 <= elo_weight <= 1.0):
            return JSONResponse(
                status_code=400,
                content={"error": "elo_weight must be between 0.0 and 1.0."}
            )
        if simulations < 100 or simulations > 10000:
            return JSONResponse(
                status_code=400,
                content={"error": "simulations must be between 100 and 10,000."}
            )

        from services.prediction_service import PredictionService
        PredictionService.save_config(elo_weight, simulations)

        return JSONResponse(content={
            "message": "Prediction model configuration updated.",
            "elo_weight": elo_weight,
            "pythagorean_weight": round(1.0 - elo_weight, 2),
            "simulations": simulations,
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
