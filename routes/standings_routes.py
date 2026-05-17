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


def _first_name(name: str) -> str:
    if not name or name == 'Undrafted':
        return name or 'Undrafted'
    if name == 'Overall Record':
        return 'Overall'
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return parts[0] if parts else name


@router.get("/profile")
async def user_profile(request: Request):
    return templates.TemplateResponse(request, "profile.html")


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
        current_year = get_active_season(games)
        available_years = get_available_years(all_draft_results, all_games)

        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)
        latest_week = get_latest_week_for_year(games, year)
        unique_weeks = (
            sorted(schedule_enriched["week"].dropna().astype(int).unique().tolist())
            if not schedule_enriched.empty and "week" in schedule_enriched.columns else []
        )

        h2h_df = analysis.player_winlossmatrix(schedule_enriched)

        recap = db.get_weekly_recap(year, latest_week)

        return templates.TemplateResponse(request, "wins_pool.html", {
            "data": sorted_df.to_dict(orient="records"),
            "refreshTime": sorted_df["refreshTime"].iloc[0] if not sorted_df.empty and "refreshTime" in sorted_df.columns else "",
            "current_year": current_year,
            "year": year,
            "available_years": available_years,
            "recap": recap["summary"] if recap else None,
            "h2h_html": (h2h_df.rename(columns=_first_name, index=_first_name)
                         .to_html(classes="wp-data-table", border=0)) if not h2h_df.empty else "",
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

    return templates.TemplateResponse(request, "weekbyweek.html", {
        "table": record_by_week.rename(columns=_first_name).to_html(classes="wp-data-table", index=True, border=0),
        "current_year": get_active_season(games),
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

    return templates.TemplateResponse(request, "playoff_race.html", {
        "race": race_data,
        "year": year,
        "current_year": get_active_season(games),
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

    available_years = get_available_years(all_draft_results, all_games)
    # Always include the requested year in the picker so future seasons are navigable
    if year not in available_years:
        available_years = sorted(available_years + [year])

    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    if games.empty:
        from services.sandbox_service import get_future_schedule
        schedule_enriched = get_future_schedule(year)
    else:
        schedule_enriched = analysis.get_enriched_schedule(games, draft_results, players, year)

    from services.cache_service import merge_game_predictions
    schedule_enriched = merge_game_predictions(schedule_enriched, year)

    latest_week = get_latest_week_for_year(games, year) if not games.empty else 1
    unique_weeks = (
        sorted(schedule_enriched["week"].dropna().astype(int).unique().tolist())
        if not schedule_enriched.empty and "week" in schedule_enriched.columns else []
    )

    return templates.TemplateResponse(request, "schedule.html", {
        "schedule": schedule_enriched.to_dict(orient="records") if not schedule_enriched.empty else [],
        "unique_weeks": unique_weeks,
        "current_week": latest_week,
        "year": year,
        "available_years": available_years,
        "current_year": get_active_season(all_games),
    })
