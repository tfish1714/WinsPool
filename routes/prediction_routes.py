"""routes/prediction_routes.py — Win prediction and Elo endpoints."""
import logging
import pathlib

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from routes.models import PredictionConfigRequest
from services.data_service import load_data
from services.draft_service import sanitize_state
from services.response_helpers import server_error
from services.session_service import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/predictions/game")
def get_game_prediction(home_team: str, away_team: str, season: int = 2025):
    """Public: Blended win probability for a single matchup."""
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, _, _ = load_data()
        svc = PredictionService.get_initialized(games, season)
        return JSONResponse(content=sanitize_state(svc.game_win_probability(home_team, away_team)))
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.get("/predictions/portfolio")
def get_portfolio_projection(season: int, playerId: int):
    """Public: Monte Carlo projected wins for a player's 3-team portfolio."""
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
                content={"error": f"No teams drafted by player {playerId} in {season}."},
            )

        svc = PredictionService.get_initialized(games, season)
        return JSONResponse(content=sanitize_state(svc.project_portfolio_wins(player_teams, season, games)))
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.get("/predictions/ratings")
def get_elo_ratings(season: int = 2025):
    """Public: Current Elo power ratings for all NFL teams."""
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, _, _ = load_data()
        svc = PredictionService.get_initialized(games, season)
        return JSONResponse(content=sanitize_state(svc.get_all_ratings()))
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.get("/admin/predictions/confidence")
def get_draft_confidence(season: int, _: dict = Depends(require_admin)):
    """Admin-only: Confidence scores ranking all NFL teams for the draft room."""
    try:
        from services.prediction_service import PredictionService
        _, _, games, _, _, draft_results, _ = load_data()
        season_drafts = draft_results[draft_results["season"] == season]
        drafted_teams = season_drafts["team"].tolist() if not season_drafts.empty and "team" in season_drafts.columns else []
        svc = PredictionService.get_initialized(games, season)
        scores = svc.generate_draft_confidence_scores(season, games, drafted_teams)
        return JSONResponse(content=sanitize_state(scores))
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.get("/admin/predictions/config")
def get_prediction_config(_: dict = Depends(require_admin)):
    """Admin-only: Read the current model configuration (blend weights)."""
    try:
        from services.prediction_service import PredictionService
        svc = PredictionService()
        return JSONResponse(content=sanitize_state(svc.load_config()))
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.post("/admin/predictions/config")
async def update_prediction_config(body: PredictionConfigRequest, _: dict = Depends(require_admin)):
    """Admin-only: Update the model blend weights."""
    try:
        elo_weight = body.elo_weight
        simulations = body.simulations

        if not (0.0 <= elo_weight <= 1.0):
            return JSONResponse(status_code=400, content={"error": "elo_weight must be between 0.0 and 1.0."})
        if simulations < 100 or simulations > 10000:
            return JSONResponse(status_code=400, content={"error": "simulations must be between 100 and 10,000."})

        from services.prediction_service import PredictionService
        PredictionService.save_config(elo_weight, simulations)
        return JSONResponse(content={
            "message": "Prediction model configuration updated.",
            "elo_weight": elo_weight,
            "pythagorean_weight": round(1.0 - elo_weight, 2),
            "simulations": simulations,
        })
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


@router.get("/admin/elo_history")
async def get_elo_history(_: dict = Depends(require_admin)):
    """Admin-only: Elo ratings over time for all teams, structured for Chart.js."""
    try:
        import json

        import pandas as pd

        from services.cache_service import get_all_elo_history

        rows = get_all_elo_history()
        if not rows:
            return JSONResponse(status_code=404, content={"error": "No Elo history found. Run scripts/compute_elo.py --firestore first."})

        df = pd.DataFrame(rows)

        records = []
        for _, row in df.iterrows():
            records.append({"season": int(row["season"]), "week": int(row["week"]),
                            "team": row["home_team"], "elo": round(float(row["home_elo_post"]), 1)})
            records.append({"season": int(row["season"]), "week": int(row["week"]),
                            "team": row["away_team"], "elo": round(float(row["away_elo_post"]), 1)})

        elo_df = pd.DataFrame(records)
        elo_df = elo_df.sort_values(["season", "week"]).drop_duplicates(
            subset=["season", "week", "team"], keep="last"
        )

        seasons = sorted(elo_df["season"].unique().tolist())
        teams_data: dict = {}
        for (season, week, team), group in elo_df.groupby(["season", "week", "team"]):
            if team not in teams_data:
                teams_data[team] = {}
            season_key = str(season)
            if season_key not in teams_data[team]:
                teams_data[team][season_key] = []
            teams_data[team][season_key].append({"week": int(week), "elo": group["elo"].iloc[0]})

        team_meta_path = pathlib.Path(__file__).parent.parent / "static" / "data" / "team_meta.json"
        divisions: dict = {}
        conferences: dict = {}
        colors: dict = {}
        if team_meta_path.exists():
            with open(team_meta_path) as f:
                team_meta = json.load(f)
            for abbr, meta in team_meta.items():
                divisions[abbr] = meta["division"]
                conferences[abbr] = meta["conference"]
                colors[abbr] = meta["color"]

        return JSONResponse(content={
            "seasons": seasons,
            "teams": teams_data,
            "divisions": divisions,
            "conferences": conferences,
            "colors": colors,
        })
    except Exception as e:
        logger.exception("Unhandled error in prediction endpoint")
        return server_error()


def _load_predictions_by_season(all_games):
    """{season: {game_key: pred_dict}} for every season from BACKTEST_MIN_SEASON
    (or the games data's own floor, if later) through the latest season present
    in `all_games`. Shared by the betting screener and pattern scanner endpoints.

    BACKTEST_MIN_SEASON=2006 is when Elo data starts, but games_df (used to
    grade ATS/SU outcomes) only goes back to 2013 -- looping earlier seasons
    would fetch prediction docs that can never be graded (no matching game
    result), wasting Firestore reads and silently misrepresenting how much
    history actually backs the reported n.
    """
    from services.betting_screener_service import BACKTEST_MIN_SEASON
    from services.cache_service import get_game_predictions

    max_season = int(all_games["season"].max())
    min_season = max(BACKTEST_MIN_SEASON, int(all_games["season"].min()))
    predictions_by_season = {}
    for yr in range(min_season, max_season + 1):
        preds = get_game_predictions(yr)
        if preds:
            predictions_by_season[yr] = preds
    return predictions_by_season, min_season, max_season


@router.get("/admin/betting/screen")
async def get_betting_screen(
    season: int | None = None,
    week: int | None = None,
    side: str = "any",
    favorite_or_dog: str = "any",
    filters: str | None = None,
    _: dict = Depends(require_admin),
):
    """Admin-only: backtest an Elo/spread/model angle and list a week's matching
    games (one row per game).

    Never touches the NN+XGB+LR ensemble -- reads only the `explanation` dict
    already stored per game (services.cache_service.get_game_predictions) plus
    actual results (services.data_service.load_data) for grading.

    `filters` is a JSON-encoded array of {"feature": <key in
    betting_screener_service.FILTERABLE_FEATURES>, "min": float|None, "max": float|None}.
    """
    try:
        import json as _json
        from services.betting_screener_service import (
            screen_games, find_next_upcoming_week, FILTERABLE_FEATURES,
        )

        if side not in ("home", "away", "any"):
            return JSONResponse(status_code=400, content={"error": "side must be home, away, or any"})
        if favorite_or_dog not in ("favorite", "dog", "any"):
            return JSONResponse(status_code=400, content={"error": "favorite_or_dog must be favorite, dog, or any"})

        parsed_filters = []
        if filters:
            try:
                parsed_filters = _json.loads(filters)
            except _json.JSONDecodeError:
                return JSONResponse(status_code=400, content={"error": "filters must be valid JSON"})
            if not isinstance(parsed_filters, list):
                return JSONResponse(status_code=400, content={"error": "filters must be a JSON array"})
            for f in parsed_filters:
                if not isinstance(f, dict) or f.get("feature") not in FILTERABLE_FEATURES:
                    bad = f.get("feature") if isinstance(f, dict) else f
                    return JSONResponse(status_code=400, content={"error": f"unknown filter feature: {bad!r}"})

        _, _, all_games, _, _, _, _ = load_data()
        if all_games.empty:
            return JSONResponse(status_code=404, content={"error": "No schedule data available."})

        target_season = season if season is not None else int(all_games["season"].max())

        target_week = week
        if target_week is None:
            target_week = find_next_upcoming_week(all_games, target_season)
            if target_week is None:
                target_week = 1

        predictions_by_season, min_season, max_season = _load_predictions_by_season(all_games)

        result = screen_games(
            predictions_by_season, all_games,
            target_season=target_season, target_week=target_week,
            side=side, favorite_or_dog=favorite_or_dog,
            filters=parsed_filters,
        )
        result["target_season"] = target_season
        result["target_week"] = target_week
        result["seasons_covered"] = [min_season, max_season]
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Unhandled error in get_betting_screen")
        return server_error()


@router.get("/admin/betting/scan")
async def get_betting_pattern_scan(
    test_seasons: int = 5,
    include_pairs: bool = True,
    min_sample: int = 50,
    min_test_sample: int = 15,
    top_n: int = 20,
    _: dict = Depends(require_admin),
):
    """Admin-only: automated angle miner. Sweeps single- and 2-feature threshold
    combinations across services.betting_screener_service.FILTERABLE_FEATURES
    against history, ranked by distance from the 50% breakeven line.

    Walk-forward validated: the sweep only searches the training seasons
    (everything before the most recent `test_seasons`), then each surfaced
    combo is evaluated once -- no re-tuning -- against the held-out seasons.
    Results are in-sample-derived leads to investigate, not proven edges.
    """
    try:
        from services.pattern_scanner_service import scan_angles

        if top_n < 1 or top_n > 100:
            return JSONResponse(status_code=400, content={"error": "top_n must be between 1 and 100"})
        if min_sample < 1 or min_test_sample < 1:
            return JSONResponse(status_code=400, content={"error": "min_sample and min_test_sample must be positive"})
        if test_seasons < 0:
            return JSONResponse(status_code=400, content={"error": "test_seasons must be non-negative"})

        _, _, all_games, _, _, _, _ = load_data()
        if all_games.empty:
            return JSONResponse(status_code=404, content={"error": "No schedule data available."})

        predictions_by_season, min_season, max_season = _load_predictions_by_season(all_games)

        result = scan_angles(
            predictions_by_season, all_games,
            test_seasons=test_seasons, include_pairs=include_pairs,
            min_sample=min_sample, min_test_sample=min_test_sample, top_n=top_n,
        )
        result["seasons_covered"] = [min_season, max_season]
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Unhandled error in get_betting_pattern_scan")
        return server_error()
