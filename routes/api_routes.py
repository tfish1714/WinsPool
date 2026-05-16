"""routes/api_routes.py — Public data API endpoints (/api/*)."""
import logging
import os
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.data_service import load_data, get_latest_season_and_week
from services.draft_service import sanitize_state
import services.analysis_service as analysis
from services.analysis_service import get_season_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


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
            logger.debug("/api/progress route total took %.3fs", time.time() - start_route)
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
        data = sorted_df.to_dict(orient="records")
        for row in data:
            row['entrant'] = row.get('fullName')
            row['total_wins'] = row.get('TotalWins')
        if is_debug:
            logger.debug("/api/standings route total took %.3fs", time.time() - start_route)
        return JSONResponse(content=sanitize_state(data))
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


def _merge_game_predictions(df, year: int):
    """Return df with ML predictions from game_predictions cache merged in."""
    from services.cache_service import get_game_predictions
    from services.nn_feature_engine import _normalize_team
    preds = get_game_predictions(year)
    if not preds:
        return df
    df = df.copy()
    for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob'):
        if col not in df.columns:
            df[col] = None

    def _key(row):
        wk = row.get('week')
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        if wk is None or not ht or not at:
            return None
        return f"W{int(wk):02d}_{ht}_{at}"

    keys = df.apply(_key, axis=1)
    for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob'):
        df[col] = keys.map(lambda k, c=col: preds.get(k, {}).get(c) if k else None)
    return df


@router.get("/schedule")
def get_schedule(year: int):
    """Data for the week-by-week schedule grid."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        if year == 2026:
            from services.sandbox_service import get_sandbox_2026_schedule
            import pandas as pd
            schedule_enriched = get_sandbox_2026_schedule()
            if not schedule_enriched.empty and pd.api.types.is_datetime64_any_dtype(schedule_enriched['gameday']):
                schedule_enriched['gameday'] = schedule_enriched['gameday'].dt.strftime('%Y-%m-%d')
        else:
            _, _, games, players, _, draft_results, _ = load_data(year=year)
            schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
            schedule_enriched = _merge_game_predictions(schedule_enriched, year)

        if is_debug:
            logger.debug("/api/schedule route total took %.3fs", time.time() - start_route)
        return JSONResponse(content=sanitize_state(schedule_enriched.to_dict(orient="records")))
    except Exception as e:
        import traceback; traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": str(e)})
