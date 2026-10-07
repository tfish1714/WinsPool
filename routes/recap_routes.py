"""routes/recap_routes.py -- weekly recap pages (/recap, /recap/{year}, /recap/{year}/{week})."""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import services.db_service as db
from services.data_service import load_data, get_active_season, get_available_years
from routes.standings_routes import templates   # shared env: filters/globals already registered

router = APIRouter()


def _active_season() -> int:
    _, _, games, _, _, draft_results, rules = load_data()
    return get_active_season(games, draft_results, rules)


def _available_years(season: int) -> list:
    try:
        _, _, games, _, _, draft_results, rules = load_data()
        years = get_available_years(draft_results, games, rules)
    except Exception:
        years = []
    return sorted(set(list(years) + [season]), reverse=True)


def _empty_state(request: Request, season: int):
    return templates.TemplateResponse(request, "recap.html", {
        "year": season, "week": None, "weeks": [], "recap": None,
        "available_years": _available_years(season), "current_year": season,
    })


@router.get("/recap")
async def recap_latest(request: Request):
    season = _active_season()
    for year in (season, season - 1):
        weeks = db.list_recap_weeks(year)
        if weeks:
            return RedirectResponse(f"/recap/{year}/{max(weeks)}")
    return _empty_state(request, season)


@router.get("/recap/{year}")
async def recap_year(request: Request, year: int):
    weeks = db.list_recap_weeks(year)
    if weeks:
        return RedirectResponse(f"/recap/{year}/{max(weeks)}")
    return _empty_state(request, year)


@router.get("/recap/{year}/{week}")
async def recap_page(request: Request, year: int, week: int):
    recap = db.get_weekly_recap(year, week)
    return templates.TemplateResponse(request, "recap.html", {
        "year": year, "week": week, "weeks": db.list_recap_weeks(year),
        "recap": recap, "available_years": _available_years(year), "current_year": year,
    })
