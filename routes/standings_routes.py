"""routes/standings_routes.py — Standings, week-by-week, and playoff race routes."""
import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import (
    load_data, get_available_years, get_latest_season_and_week,
    get_latest_week_for_year, get_active_season,
)
import services.db_service as db
import services.analysis_service as analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _current_year(games, draft_results=None) -> int:
    return get_active_season(games, draft_results)


@router.get("/profile")
async def user_profile(request: Request):
    return templates.TemplateResponse("profile.html", {"request": request})


@router.get("/wins-pool")
async def wins_pool_redirect():
    _, _, games, _, _, _, _ = load_data()
    s, _ = get_latest_season_and_week(games)
    return RedirectResponse(f"/wins-pool/{s}")


@router.get("/wins-pool/{year}")
async def wins_pool_by_year(request: Request, year: int):
    try:
        # Load ALL data once — data_service will cache this master set.
        # Sub-calls for available_years etc. will now be instant memory hits.
        all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

        # Filter for the specific year in-memory
        standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
        games = all_games[all_games['season'] == year] if not all_games.empty else all_games
        draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

        sorted_df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year, games)
        current_year = _current_year(games)
        available_years = get_available_years(all_draft_results, all_games)

        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        latest_week = get_latest_week_for_year(games, year)
        unique_weeks = (
            sorted(schedule_enriched["week"].dropna().astype(int).unique().tolist())
            if not schedule_enriched.empty and "week" in schedule_enriched.columns else []
        )

        h2h_df = analysis.player_winlossmatrix(schedule_enriched)

        recap = db.get_weekly_recap(year, latest_week)

        return templates.TemplateResponse("wins_pool.html", {
            "request": request,
            "data": sorted_df.to_dict(orient="records"),
            "refreshTime": sorted_df["refreshTime"].iloc[0] if not sorted_df.empty and "refreshTime" in sorted_df.columns else "",
            "current_year": current_year,
            "year": year,
            "available_years": available_years,
            "recap": recap["summary"] if recap else None,
            "h2h_html": h2h_df.to_html(classes="table table-striped", border=0) if not h2h_df.empty else "",
        })
    except Exception as e:
        import traceback
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(traceback.format_exc())


@router.get("/wins-pool/{year}/weekbyweek")
async def wins_pool_weekbyweek(request: Request, year: int):
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    # Filter for the specific year in-memory
    standings_df = all_st[all_st['season'] == year] if not all_st.empty else all_st
    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    # Calculate standings to get the ranked player order
    standings_ranked = analysis.calculate_wins_pool_standings(standings_df, draft_results, players, year)
    sorted_player_names = standings_ranked["fullName"].tolist() if not standings_ranked.empty else None

    schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
    record_by_week = analysis.player_winsbyWeek(schedule_enriched, sorted_players=sorted_player_names)

    return templates.TemplateResponse("weekbyweek.html", {
        "request": request,
        "table": record_by_week.to_html(classes="table table-striped", index=True, border=0),
        "current_year": _current_year(games),
        "year": year,
        "available_years": get_available_years(all_draft_results, all_games),
    })


@router.get("/playoff-race")
async def playoff_race_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    return RedirectResponse(f"/playoff-race/{get_active_season(games, draft_results)}")


@router.get("/playoff-race/{year}")
async def playoff_race_by_year(request: Request, year: int):
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    # Filter for the specific year in-memory
    standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    try:
        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        race_data = analysis.calculate_playoff_race(schedule_enriched, standings)
    except Exception:
        race_data = []

    return templates.TemplateResponse("playoff_race.html", {
        "request": request,
        "race": race_data,
        "year": year,
        "current_year": _current_year(games),
        "available_years": get_available_years(all_draft_results, all_games),
    })
@router.get("/schedule")
async def schedule_redirect():
    _, _, games, _, _, _, _ = load_data()
    s, _ = get_latest_season_and_week(games)
    return RedirectResponse(f"/schedule/{s}")


@router.get("/schedule/{year}")
async def schedule_by_year(request: Request, year: int):
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
    latest_week = get_latest_week_for_year(games, year)
    unique_weeks = (
        sorted(schedule_enriched["week"].dropna().astype(int).unique().tolist())
        if not schedule_enriched.empty and "week" in schedule_enriched.columns else []
    )

    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule_enriched.to_dict(orient="records") if not schedule_enriched.empty else [],
        "unique_weeks": unique_weeks,
        "current_week": latest_week,
        "year": year,
        "available_years": get_available_years(all_draft_results, all_games),
        "current_year": _current_year(all_games),
    })
