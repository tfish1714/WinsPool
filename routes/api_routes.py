"""routes/api_routes.py — JSON API endpoints."""
import os
import random
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.data_service import (
    load_data, get_latest_season_and_week, get_season_progress,
)
from services.db_service import get_collection_df, add_draft_order, add_draft_rule
from services.draft_service import load_draft_state

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
