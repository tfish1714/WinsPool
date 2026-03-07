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
    update_player_credentials, increment_failed_setup_attempts
)
from services.draft_service import load_draft_state, wipe_draft_cache
import time
import re

router = APIRouter(prefix="/api")

ADMIN_CODE = os.environ.get("ADMIN_CODE", "admin_test")


@router.get("/progress/{season}/{week}")
def fetch_progress(season: str, week: str):
    """Chart data: cumulative player wins by week for the given season."""
    try:
        _, _, games, _, _, _, _ = load_data()

        if season.lower() == "latest":
            target_season, _ = get_latest_season_and_week(games)
        else:
            target_season = int(season)

        if week.lower() == "latest":
            s_games = games[games["season"] == target_season]
            target_week = int(s_games["week"].max()) if not s_games.empty else 18
        else:
            target_week = int(week)

        return JSONResponse(content=get_season_progress(target_season, target_week))
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
        if not player:
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        if not player.get("password_hash"):
            return JSONResponse(status_code=400, content={"error": "Account not claimed yet. Please set a password."})

        if not verify_password(password, player.get("password_hash")):
            return JSONResponse(status_code=401, content={"error": "Invalid email or password."})

        role = player.get("role", "user")

        return JSONResponse(content={
            "message": "Login successful!",
            "playerId": str(player["playerId"]),
            "playerName": player.get("fullName"),
            "role": role
        })
    except Exception as e:
        import traceback; traceback.print_exc()
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
        if data.get("admin_code") != ADMIN_CODE:
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Invalid admin code."})

        season = int(data.get("season"))

        order_df = get_collection_df("draft_order")
        existing = order_df[order_df["season"] == season] if not order_df.empty and "season" in order_df.columns else []
        if len(existing):
            return JSONResponse(status_code=400, content={"error": f"Season {season} already exists."})

        players = get_collection_df("players")
        player_ids = players["playerId"].tolist()
        random.shuffle(player_ids)

        for idx, pid in enumerate(player_ids):
            add_draft_order(season, idx + 1, pid)

        rules_df = get_collection_df("draft_order_rules")
        if not rules_df.empty and "season" in rules_df.columns:
            prev_s = int(rules_df["season"].max())
            for _, r in rules_df[rules_df["season"] == prev_s].iterrows():
                add_draft_rule(season, int(r["draftOrder"]), int(r["pickOne"]), int(r["pickTwo"]), int(r["pickThree"]))

        return JSONResponse(content={"message": f"Draft order for {season} created successfully."})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.post("/admin/reset_draft")
async def reset_draft(request: Request):
    """Admin sandbox feature to delete all draft results for a given season."""
    try:
        data = await request.json()
        if data.get("admin_code") != ADMIN_CODE:
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Invalid admin code."})

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
        if data.get("admin_code") != ADMIN_CODE:
            return JSONResponse(status_code=401, content={"error": "Unauthorized: Invalid admin code."})

        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "upload_predictions.py"))
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"error": result.stderr or "Script failed silently."})
        
        return JSONResponse(content={"message": "Vegas Odds successfully scraped and injected into Firestore!"})
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
