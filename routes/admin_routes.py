"""routes/admin_routes.py — Admin-only API endpoints (/api/admin/*)."""
import html as html_module
import logging
import os
import random
import re
import subprocess
import sys

from typing import Annotated

from fastapi import APIRouter, Depends, Path as FPath, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from routes.models import (
    NewSeasonRequest, MemberPaidRequest, PreviewDraftOrderRequest,
    CreatePlayerRequest, UpdatePlayerRequest, TargetPlayerRequest,
    SetTempPasswordRequest, SeasonRequest, RecapWeekRequest,
    RecapYearRequest, GenerateRecapRequest, SaveBroadcastRecapRequest,
)
from services.cache_service import get_prediction_features
from services.data_service import load_data
from services.response_helpers import server_error
from services.db_service import (
    add_draft_order, add_draft_rule, add_player, delete_draft_results_for_season,
    delete_season_data, get_collection_df, get_password_hash, save_weekly_recap,
    set_member_paid, update_player_credentials, update_player_profile,
)
from services.draft_service import sanitize_state, wipe_draft_cache
from services.session_service import require_admin
import services.ai_service as ai_service
import services.email_service as email_service
import services.recap_service as recap_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")
_templates = Jinja2Templates(directory="templates")
_page_router = APIRouter()  # No prefix — for HTML page routes


@router.post("/admin/new_season")
async def create_new_season(body: NewSeasonRequest, _: dict = Depends(require_admin)):
    """Generate a randomized draft order for a new season."""
    try:
        season = body.season
        player_ids = body.playerIds

        if not player_ids:
            return JSONResponse(status_code=400, content={"error": "At least one player must be selected."})

        order_df = get_collection_df("draft_order")
        existing = order_df[order_df["season"] == season] if not order_df.empty and "season" in order_df.columns else []
        if len(existing):
            return JSONResponse(status_code=400, content={"error": f"Season {season} already exists."})

        random.shuffle(player_ids)
        for idx, pid in enumerate(player_ids):
            add_draft_order(season, idx + 1, int(pid))

        rules_df = get_collection_df("draft_order_rules")
        if not rules_df.empty and "season" in rules_df.columns:
            prev_s = int(rules_df["season"].max())
            for _, r in rules_df[rules_df["season"] == prev_s].iterrows():
                if int(r["draftOrder"]) <= len(player_ids):
                    add_draft_rule(season, int(r["draftOrder"]), int(r["pickOne"]), int(r["pickTwo"]), int(r["pickThree"]))

        wipe_draft_cache()
        return JSONResponse(content={"message": f"Draft order for {season} created successfully with {len(player_ids)} players."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.get("/admin/players")
async def fetch_admin_players(_: dict = Depends(require_admin)):
    """Retrieve all players for the admin selection grid."""
    try:
        _, _, _, players_df, _, _, _ = load_data()
        return JSONResponse(content=sanitize_state(players_df.to_dict(orient="records")))
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.get("/admin/members/{season}")
async def get_season_members(season: int, _: dict = Depends(require_admin)):
    """Return all players enrolled in a season with their draft order and paid status."""
    try:
        _, _, _, players_df, _, _, _ = load_data()
        order_df = get_collection_df("draft_order")
        if order_df.empty or "season" not in order_df.columns:
            return JSONResponse(content=[])
        season_order = order_df[order_df["season"].astype(int) == season]
        members = []
        for _, row in season_order.iterrows():
            pid = int(row["playerId"])
            player = players_df[players_df["playerId"].astype(int) == pid]
            if player.empty:
                continue
            p = player.iloc[0]
            members.append({
                "playerId": pid,
                "fullName": str(p.get("fullName", "")),
                "email": str(p.get("email", "")),
                "role": str(p.get("role", "member")),
                "draftOrder": int(row.get("draftOrder", 0)),
                "paid": bool(row.get("paid", False)),
            })
        members.sort(key=lambda x: x["draftOrder"])
        return JSONResponse(content={"members": members})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/members/paid")
async def update_member_paid(body: MemberPaidRequest, _: dict = Depends(require_admin)):
    """Toggle paid status for a player in a given season."""
    try:
        ok = set_member_paid(body.season, body.targetPlayerId, body.paid)
        return JSONResponse(content={"ok": ok})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.get("/admin/seasons")
async def fetch_admin_seasons(_: dict = Depends(require_admin)):
    """Retrieve all years that have at least some draft data."""
    try:
        _, _, _, _, draft_order_df, draft_results_df, _ = load_data()
        seasons = set()
        if not draft_order_df.empty and "season" in draft_order_df.columns:
            seasons.update(draft_order_df["season"].unique().tolist())
        if not draft_results_df.empty and "season" in draft_results_df.columns:
            seasons.update(draft_results_df["season"].unique().tolist())
        return JSONResponse(content={"seasons": sorted([int(s) for s in seasons], reverse=True)})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/preview_draft_order")
async def preview_draft_order(body: PreviewDraftOrderRequest, _: dict = Depends(require_admin)):
    """Randomize selected players and return the order for review without saving."""
    try:
        player_ids = body.playerIds
        if not player_ids:
            return JSONResponse(status_code=400, content={"error": "No players selected."})

        random.shuffle(player_ids)
        _, _, _, players_df, _, _, _ = load_data()
        preview = []
        for idx, pid in enumerate(player_ids):
            p_row = players_df[players_df["playerId"] == int(pid)]
            p_name = p_row["fullName"].iloc[0] if not p_row.empty else f"Unknown ({pid})"
            preview.append({"order": idx + 1, "playerName": p_name, "playerId": int(pid)})
        return JSONResponse(content={"preview": preview})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/create_player")
async def create_player(body: CreatePlayerRequest, _: dict = Depends(require_admin)):
    """Admin-only: Create a new player."""
    try:
        if not body.fullName or not body.nickName or not body.email:
            return JSONResponse(status_code=400, content={"error": "Name, Nickname, and Email are required."})
        new_id = add_player(body.fullName, body.nickName, body.email, phone=body.phone)
        return JSONResponse(content={"message": f"Player created with ID {new_id}", "playerId": new_id})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/update_player")
async def admin_update_player(body: UpdatePlayerRequest, _: dict = Depends(require_admin)):
    """Admin-only: Update an existing player's profile fields."""
    try:
        if not body.targetPlayerId:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId is required."})

        allowed_fields = {"fullName", "nickName", "email", "cell"}
        updates = {k: v for k, v in body.fields.items() if k in allowed_fields}
        if not updates:
            return JSONResponse(status_code=400, content={"error": "No valid fields to update."})

        if "email" in updates:
            updates["email"] = updates["email"].strip().lower()

        update_player_profile(str(body.targetPlayerId), updates)
        return JSONResponse(content={"message": "Player updated successfully."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/reset_password")
async def admin_reset_password(body: TargetPlayerRequest, _: dict = Depends(require_admin)):
    """Admin-only: Clear a player's password hash, forcing them to set a new one."""
    try:
        if not body.targetPlayerId:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId is required."})
        update_player_profile(str(body.targetPlayerId), {
            "password_hash": None,
            "failed_setup_attempts": 0,
            "lockout_until": None,
            "mfa_secret": None,
            "mfa_enabled": False,
        })
        return JSONResponse(content={"message": "Password reset. Player will be prompted to set a new password on next login."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/set_temp_password")
async def admin_set_temp_password(body: SetTempPasswordRequest, _: dict = Depends(require_admin)):
    """Admin-only: Set a temporary password; player must change it on next login."""
    try:
        if not body.targetPlayerId or not body.tempPassword:
            return JSONResponse(status_code=400, content={"error": "targetPlayerId and tempPassword are required."})

        pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
        if not re.match(pw_regex, body.tempPassword):
            return JSONResponse(status_code=400, content={
                "error": "Temporary password must be 12+ characters with uppercase, lowercase, number, and symbol."
            })

        hashed = get_password_hash(body.tempPassword)
        update_player_credentials(str(body.targetPlayerId), hashed)
        update_player_profile(str(body.targetPlayerId), {"must_change_password": True})
        return JSONResponse(content={"message": "Temporary password set. Player will be required to change it on next login."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/delete_season")
async def delete_season(body: SeasonRequest, _: dict = Depends(require_admin)):
    """Wipe all draft data for a specific season."""
    try:
        delete_season_data(body.season)
        wipe_draft_cache()
        return JSONResponse(content={"message": f"Season {body.season} data wiped successfully."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/reset_draft")
async def reset_draft(body: SeasonRequest, _: dict = Depends(require_admin)):
    """Admin: Delete all draft results for a given season (sandbox reset)."""
    try:
        delete_draft_results_for_season(body.season)
        wipe_draft_cache()
        return JSONResponse(content={"message": f"Draft Results for {body.season} securely wiped! Mock draft reset successful."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/scrape_predictions")
async def scrape_predictions(_: dict = Depends(require_admin)):
    """Execute the Vegas odds scraper and push results to the database."""
    try:
        script_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "upload_predictions.py"))
        result = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
        if result.returncode != 0:
            return JSONResponse(status_code=500, content={"error": result.stderr or "Script failed silently."})
        return JSONResponse(content={"message": "Vegas Odds successfully scraped and injected into Firestore!"})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/recap/preview_prompt")
async def preview_recap_prompt(body: RecapWeekRequest, _: dict = Depends(require_admin)):
    """Admin: Generate the data prompt for an AI weekly recap."""
    try:
        data_summary, _ = recap_service.extract_weekly_data(body.year, body.week)
        if not data_summary:
            return JSONResponse(status_code=404, content={"error": f"No game results found for {body.year} Week {body.week}."})
        return JSONResponse(content={"prompt": ai_service.get_recap_prompt(data_summary)})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/recap/preview_draft_prompt")
async def preview_draft_recap_prompt(body: RecapYearRequest, _: dict = Depends(require_admin)):
    """Admin: Generate the data prompt for an AI draft recap."""
    try:
        data_summary, _ = recap_service.extract_draft_data(body.year)
        if not data_summary:
            return JSONResponse(status_code=404, content={"error": f"No draft data found for {body.year}."})
        return JSONResponse(content={"prompt": ai_service.get_draft_recap_prompt(data_summary)})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/recap/generate")
async def generate_admin_recap(body: GenerateRecapRequest, _: dict = Depends(require_admin)):
    """Admin: Send the prompt to AI and return the generated summary."""
    try:
        if not body.prompt_data:
            return JSONResponse(status_code=400, content={"error": "Prompt data is required."})
        summary = ai_service.generate_generic_content(body.prompt_data)
        return JSONResponse(content={"summary": summary})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.post("/admin/recap/save_and_broadcast")
async def save_and_broadcast_recap(body: SaveBroadcastRecapRequest, _: dict = Depends(require_admin)):
    """Admin: Save a recap to the DB and email it to all enrolled players."""
    try:
        if not body.summary:
            return JSONResponse(status_code=400, content={"error": "Summary text is required."})

        save_weekly_recap(body.year, body.week, body.summary)

        _, emails = recap_service.extract_weekly_data(body.year, body.week)
        if emails:
            html_body = f"""
            <html>
                <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                    <div style="background-color: #ffffff; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                        <h2 style="color: #333;">🏈 Weekly Recap: NFL Week {body.week}</h2>
                        <div style="white-space: pre-wrap; color: #555; line-height: 1.6;">
                            {html_module.escape(body.summary)}
                        </div>
                        <p style="margin-top: 20px; font-size: 0.8em; color: #888;">
                            This recap was generated by Gemini AI for the Wins Pool.
                        </p>
                    </div>
                </body>
            </html>
            """
            email_service.send_weekly_recap_email(emails, f"Week {body.week} Recap - Wins Pool", html_body)

        return JSONResponse(content={"message": f"Week {body.week} recap saved and broadcast to {len(emails)} players."})
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.get("/admin/predictions/vs-vegas")
async def get_predictions_vs_vegas(season: int, week: int, _: dict = Depends(require_admin)):
    """All predictions for a given week sorted by |edge vs Vegas| (admin only)."""
    try:
        from services.cache_service import get_game_predictions
        preds = get_game_predictions(season)
        week_prefix = f"W{week:02d}_"
        games = []
        for key, pred in preds.items():
            if not key.startswith(week_prefix):
                continue
            parts = key.split("_")
            ht = parts[1] if len(parts) > 1 else "?"
            at = parts[2] if len(parts) > 2 else "?"
            ev = pred.get("edge_vs_vegas")
            ex = pred.get("explanation") or {}
            games.append({
                "key":           key,
                "home_team":     ht,
                "away_team":     at,
                "pred_winner":   pred.get("pred_winner"),
                "pred_su_conf":  pred.get("pred_su_conf"),
                "home_win_prob": pred.get("home_win_prob"),
                "model_spread":  pred.get("model_spread"),
                "vegas_line":    ex.get("vegas_line"),
                "edge_vs_vegas": ev,
                "pred_ats_pick": pred.get("pred_ats_pick"),
            })
        games.sort(key=lambda g: abs(g.get("edge_vs_vegas") or 0), reverse=True)
        return JSONResponse(content={"season": season, "week": week, "games": games})
    except Exception:
        logger.exception("Unhandled error in get_predictions_vs_vegas")
        return server_error()


@_page_router.get("/admin/predictions", response_class=HTMLResponse)
async def admin_predictions_page(
    request: Request,
    _: dict = Depends(require_admin),
):
    """Admin prediction debug page — requires admin auth."""
    return _templates.TemplateResponse(request, "admin_predictions.html")


@router.get("/admin/prediction_features/{season}")
async def get_admin_prediction_features(
    season: Annotated[int, FPath(ge=2000, le=2030)],
    _: dict = Depends(require_admin),
):
    """Full feature audit for a season (admin only — used by debug page).

    Returns the latest ensemble version doc for the season.
    """
    try:
        doc = get_prediction_features(season)
        if doc is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"No feature data for season {season}."},
            )
        return JSONResponse(content=doc)
    except Exception:
        logger.exception("Unhandled error in get_admin_prediction_features")
        return server_error()
