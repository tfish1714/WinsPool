"""routes/standings_routes.py — Standings, week-by-week, and playoff race routes."""
import pandas as pd
import os
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import (
    load_data, get_latest_week_for_year, get_dropdown_config,
    is_season_bundled, get_bundled_analysis
)
import services.db_service as db
import services.analysis_service as analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _current_year() -> int:
    config = get_dropdown_config()
    return config.get("latest_season", 2024) if config else 2024


@router.get("/profile")
async def user_profile(request: Request):
    return templates.TemplateResponse("profile.html", {"request": request})


@router.get("/")
async def index_redirect():
    config = get_dropdown_config()
    s = config.get("latest_season", 2024) if config else 2024
    return RedirectResponse(f"/wins-pool/{s}")


@router.get("/wins-pool")
async def wins_pool_redirect():
    config = get_dropdown_config()
    s = config.get("latest_season", 2024) if config else 2024
    return RedirectResponse(f"/wins-pool/{s}")


@router.get("/wins-pool/{year}")
async def wins_pool_by_year(request: Request, year: int):
    if is_season_bundled(year):
        bundled = get_bundled_analysis(year)
        if bundled:
            config = get_dropdown_config()
            return templates.TemplateResponse("wins_pool.html", {
                "request": request,
                "data": bundled["standings_progress"]["standings"],
                "refreshTime": bundled["standings_progress"].get("refreshTime", "Cached"),
                "current_year": config.get("latest_season", 2024) if config else 2024,
                "year": year,
                "available_years": config.get("available_seasons", []) if config else [2024],
                "current_week": bundled["standings_progress"]["week"],
                "recap": "Historical recap loaded from bundle.",
                "h2h_html": bundled.get("h2h_html", "")
            })

    try:
        # Load ALL data once — data_service will cache this master set.
        # Sub-calls for available_years etc. will now be instant memory hits.
        all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

        # Filter for the specific year in-memory
        standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
        games = all_games[all_games['season'] == year] if not all_games.empty else all_games
        draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

        config = get_dropdown_config()
        current_year = config.get("latest_season", 2024) if config else 2024
        available_years = config.get("available_seasons", []) if config else [2024]
        
        # Use metadata for latest week, fallback to data_service logic if missing
        metadata_wk = config.get("latest_week_by_season", {}).get(str(year)) if config else None
        latest_week = int(metadata_wk) if metadata_wk is not None else get_latest_week_for_year(games, year)
        
        unique_weeks = config.get("weeks_by_season", {}).get(str(year), []) if config else []

        sorted_df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year, games)
        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        h2h_df = analysis.player_winlossmatrix(schedule_enriched)

        recap_data = db.get_weekly_recap(year, latest_week)

        return templates.TemplateResponse("wins_pool.html", {
            "request": request,
            "data": sorted_df.to_dict(orient="records"),
            "refreshTime": sorted_df["refreshTime"].iloc[0] if not sorted_df.empty and "refreshTime" in sorted_df.columns else "",
            "current_year": current_year,
            "year": year,
            "available_years": available_years,
            "current_week": latest_week,
            "recap": recap_data["summary"] if recap_data else None,
            "h2h_html": h2h_df.to_html(classes="table table-striped", border=0) if not h2h_df.empty else "",
        })
    except Exception as e:
        import traceback
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(traceback.format_exc())


@router.get("/wins-pool/{year}/weekbyweek")
async def wins_pool_weekbyweek(request: Request, year: int):
    if is_season_bundled(year):
        bundled = get_bundled_analysis(year)
        if bundled:
            config = get_dropdown_config()
            return templates.TemplateResponse("weekbyweek.html", {
                "request": request,
                "table": bundled.get("week_by_week_html", ""),
                "current_year": config.get("latest_season", 2024) if config else 2024,
                "year": year,
                "available_years": config.get("available_seasons", [2024]) if config else [2024],
            })

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

    config = get_dropdown_config()
    
    return templates.TemplateResponse("weekbyweek.html", {
        "request": request,
        "table": record_by_week.to_html(classes="table table-striped", index=True, border=0),
        "current_year": config.get("latest_season", 2024) if config else 2024,
        "year": year,
        "available_years": config.get("available_seasons", [2024]) if config else [2024],
    })


@router.get("/playoff-race")
async def playoff_race_redirect():
    config = get_dropdown_config()
    s = config.get("latest_season", 2024) if config else 2024
    return RedirectResponse(f"/playoff-race/{s}")


@router.get("/playoff-race/{year}")
async def playoff_race_by_year(request: Request, year: int):
    if is_season_bundled(year):
        bundled = get_bundled_analysis(year)
        if bundled:
            config = get_dropdown_config()
            return templates.TemplateResponse("playoff_race.html", {
                "request": request,
                "race": bundled.get("playoff_race", []),
                "year": year,
                "current_year": config.get("latest_season", 2024) if config else 2024,
                "available_years": config.get("available_seasons", [2024]) if config else [2024],
            })

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

    config = get_dropdown_config()

    return templates.TemplateResponse("playoff_race.html", {
        "request": request,
        "race": race_data,
        "year": year,
        "current_year": config.get("latest_season", 2024) if config else 2024,
        "available_years": config.get("available_seasons", [2024]) if config else [2024],
    })
@router.get("/schedule")
async def schedule_redirect():
    config = get_dropdown_config()
    s = config.get("latest_season", 2024) if config else 2024
    return RedirectResponse(f"/schedule/{s}")


@router.get("/schedule/{year}")
async def schedule_by_year(request: Request, year: int):
    if is_season_bundled(year):
        bundled = get_bundled_analysis(year)
        if bundled:
            config = get_dropdown_config()
            return templates.TemplateResponse("schedule.html", {
                "request": request,
                "schedule": bundled["full_schedule"],
                "unique_weeks": config.get("weeks_by_season", {}).get(str(year), []) if config else [],
                "current_week": bundled["standings_progress"]["week"],
                "year": year,
                "available_years": config.get("available_seasons", [2024]) if config else [2024],
                "current_year": config.get("latest_season", 2024) if config else 2024,
            })

    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    config = get_dropdown_config()
    
    # Use metadata for latest week, fallback to data_service logic if missing
    metadata_wk = config.get("latest_week_by_season", {}).get(str(year)) if config else None
    latest_week = int(metadata_wk) if metadata_wk is not None else get_latest_week_for_year(games, year)

    unique_weeks = config.get("weeks_by_season", {}).get(str(year), []) if config else []
    
    # CRITICAL: Restore missing calculation
    schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)

    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedule": schedule_enriched.to_dict(orient="records") if not schedule_enriched.empty else [],
        "unique_weeks": unique_weeks,
        "current_week": latest_week,
        "year": year,
        "available_years": config.get("available_seasons", [2024]) if config else [2024],
        "current_year": config.get("latest_season", 2024) if config else 2024,
    })
