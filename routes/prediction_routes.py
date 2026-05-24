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
        import pandas as pd

        elo_path = pathlib.Path(__file__).parent.parent / "rawdata" / "elo_computed.csv"
        if not elo_path.exists():
            return JSONResponse(status_code=404, content={"error": "elo_computed.csv not found. Run scripts/compute_elo.py first."})

        df = pd.read_csv(elo_path)

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

        teams_csv = pathlib.Path(__file__).parent.parent / "rawdata" / "teams_colors_logos.csv"
        divisions: dict = {}
        conferences: dict = {}
        colors: dict = {}
        if teams_csv.exists():
            tdf = pd.read_csv(teams_csv)
            for _, tr in tdf.iterrows():
                abbr = str(tr["team_abbr"])
                divisions[abbr] = str(tr["team_division"])
                conferences[abbr] = str(tr["team_conf"])
                colors[abbr] = str(tr["team_color"])

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
