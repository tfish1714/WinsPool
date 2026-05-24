"""routes/api_routes.py — Public data API endpoints (/api/*)."""
import logging
import os
import time

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse

from services.data_service import load_data, get_latest_season_and_week
from services.response_helpers import error_response, server_error, not_found, unauthorized
from services.draft_service import sanitize_state
from services.session_service import require_auth
import services.analysis_service as analysis
from services.analysis_service import get_season_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/progress/{season}/{week}")
def fetch_progress(
    season: Annotated[int, Path(ge=2000, le=2030)],
    week: Annotated[int, Path(ge=1, le=22)],
    _auth: dict = Depends(require_auth),
):
    """Chart data: cumulative player wins by week for the given season."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        _, _, games, _, _, _, _ = load_data()
        if games.empty or "season" not in games.columns:
            return JSONResponse(content={"labels": [], "datasets": []})
        res = get_season_progress(season, week)
        if is_debug:
            logger.debug("/api/progress route total took %.3fs", time.time() - start_route)
        return JSONResponse(content=res)
    except Exception as e:
        logger.exception("Unhandled error in /api/progress")
        return server_error()


@router.get("/progress/draft_summary")
def fetch_draft_summary(_auth: dict = Depends(require_auth)):
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
        logger.exception("Unhandled error in fetch_draft_summary")
        return server_error()


@router.get("/standings")
def get_standings(year: int, _auth: dict = Depends(require_auth)):
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
        logger.exception("Unhandled error in get_standings")
        return server_error()


from services.cache_service import merge_game_predictions as _merge_game_predictions


@router.get("/predictions/accuracy")
def get_prediction_accuracy(_auth: dict = Depends(require_auth)):
    """ML prediction accuracy vs actual game results, by season and week."""
    try:
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team
        import pathlib, json, numpy as np

        _, _, all_games, _, _, _, _ = load_data()

        # Build a result lookup: normalized_key -> actual_winner (or None for ties/unplayed)
        result_lookup = {}
        if not all_games.empty:
            played = all_games[all_games['result'].notna() & (all_games['result'] != -1000)]
            for _, row in played.iterrows():
                wk = row.get('week')
                ht = _normalize_team(str(row.get('home_team', '') or ''))
                at = _normalize_team(str(row.get('away_team', '') or ''))
                res = row.get('result', 0)
                if not wk or not ht or not at:
                    continue
                key = f"W{int(wk):02d}_{ht}_{at}"
                if res > 0:
                    result_lookup[key] = ht
                elif res < 0:
                    result_lookup[key] = at
                # ties: key not stored → skipped in accuracy

        # Scan all game_predictions_*.json files for locked predictions
        local_db = pathlib.Path('.local_db')
        seasons_data = {}
        overall_correct = overall_total = 0

        pred_files = sorted(local_db.glob('game_predictions_*.json')) if local_db.exists() else []
        for pfile in pred_files:
            try:
                season = int(pfile.stem.split('_')[-1])
            except ValueError:
                continue
            preds = get_game_predictions(season)
            if not preds:
                continue

            by_week = {}
            s_correct = s_total = 0
            for key, pred in preds.items():
                if not pred.get('locked'):
                    continue
                actual = result_lookup.get(key)
                if actual is None:
                    continue
                pw = pred.get('pred_winner')
                if pw is None:
                    continue
                correct = int(_normalize_team(str(pw)) == actual)
                wk = int(key[1:3])
                if wk not in by_week:
                    by_week[wk] = {'week': wk, 'total': 0, 'correct': 0}
                by_week[wk]['total'] += 1
                by_week[wk]['correct'] += correct
                s_correct += correct
                s_total += 1

            if s_total == 0:
                continue

            week_rows = sorted(by_week.values(), key=lambda r: r['week'])
            for r in week_rows:
                r['accuracy'] = round(r['correct'] / r['total'] * 100, 1)

            seasons_data[season] = {
                'season': season,
                'total': s_total,
                'correct': s_correct,
                'accuracy': round(s_correct / s_total * 100, 1),
                'by_week': week_rows,
            }
            overall_correct += s_correct
            overall_total += s_total

        seasons_list = sorted(seasons_data.values(), key=lambda r: r['season'], reverse=True)
        overall = {
            'total': overall_total,
            'correct': overall_correct,
            'accuracy': round(overall_correct / overall_total * 100, 1) if overall_total else 0,
        }
        return JSONResponse(content={'seasons': seasons_list, 'overall': overall})
    except Exception as e:
        logger.exception("Unhandled error in /api/predictions/accuracy")
        return server_error()


@router.get("/predictions/explain")
def get_prediction_explain(season: int, week: int, home: str, away: str, _auth: dict = Depends(require_auth)):
    """Return the stored explanation (feature values) for a single game prediction."""
    try:
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team
        ht = _normalize_team(home)
        at = _normalize_team(away)
        key = f"W{week:02d}_{ht}_{at}"
        preds = get_game_predictions(season)
        pred = preds.get(key)
        if not pred:
            return not_found("No prediction found for this game.")
        return JSONResponse(content={
            "key": key,
            "home_team": ht,
            "away_team": at,
            "season": season,
            "week": week,
            **{k: v for k, v in pred.items() if k != "locked"},
        })
    except Exception as e:
        logger.exception("Unhandled error in get_prediction_explain")
        return server_error()


@router.get("/schedule")
def get_schedule(year: int, _auth: dict = Depends(require_auth)):
    """Data for the week-by-week schedule grid."""
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_route = time.time()
    try:
        _, _, games, players, _, draft_results, _ = load_data(year=year)
        if games.empty:
            from services.sandbox_service import get_future_schedule
            schedule_enriched = get_future_schedule(year)
        else:
            schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        schedule_enriched = _merge_game_predictions(schedule_enriched, year)

        if is_debug:
            logger.debug("/api/schedule route total took %.3fs", time.time() - start_route)
        return JSONResponse(content=sanitize_state(schedule_enriched.to_dict(orient="records")))
    except Exception as e:
        logger.exception("Unhandled error in /api/schedule")
        return server_error()
