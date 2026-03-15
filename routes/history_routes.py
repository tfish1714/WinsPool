"""routes/history_routes.py — Overall history and head-to-head routes."""
import pandas as pd
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import load_data, get_available_years, get_active_season
import services.analysis_service as analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _current_year(games, draft_results=None) -> int:
    return get_active_season(games, draft_results)


# ─── Overall History ──────────────────────────────────────────────────────────

@router.get("/history")
async def overall_history(request: Request):
    standings_master, _, all_games, players, _, all_draft_results, _ = load_data()
    current_year = _current_year(all_games)

    player_stats: dict = {}
    season_records: list = []

    # Undrafted best & worst
    undrafted_best = {"team": "None", "year": "-", "wins": -1}
    undrafted_worst = {"team": "None", "year": "-", "wins": 999}

    if not standings_master.empty and "season" in standings_master.columns:
        for yr in standings_master["season"].unique():
            yr_standings = standings_master[standings_master["season"] == yr]
            yr_draft = (
                all_draft_results[all_draft_results["season"] == yr]
                if not all_draft_results.empty else pd.DataFrame()
            )
            if not yr_standings.empty and not yr_draft.empty:
                drafted = set(yr_draft["team"].dropna().unique())
                for _, u in yr_standings[~yr_standings["team"].isin(drafted)].iterrows():
                    w = int(u.get("wins", 0))
                    if w > undrafted_best["wins"]:
                        undrafted_best = {"team": u["team"], "year": int(yr), "wins": w}
                    if 0 <= w < undrafted_worst["wins"]:
                        undrafted_worst = {"team": u["team"], "year": int(yr), "wins": w}

    if undrafted_best["wins"] == -1:
        undrafted_best["wins"] = 0
    if undrafted_worst["wins"] == 999:
        undrafted_worst["wins"] = 0

    # available_years derived once
    available_years = get_available_years(all_draft_results, all_games)

    for yr in available_years:
        try:
            # Derived from master data in-memory
            standings = standings_master[standings_master["season"] == yr] if not standings_master.empty else standings_master
            games = all_games[all_games["season"] == yr] if not all_games.empty else all_games
            draft_results = all_draft_results[all_draft_results["season"] == yr] if not all_draft_results.empty else all_draft_results
            
            # players, teams, draft_order, and rules are already "all" data
            yr_standings = analysis.calculate_wins_pool_standings(standings, draft_results, players, yr)
            if yr_standings.empty:
                continue
            for _, row in yr_standings.iterrows():
                p_name = row["fullName"]
                wins = int(row["TotalWins"])
                rank = int(row["Rank"])

                season_records.append({"player": p_name, "season": yr, "wins": wins, "rank": rank})

                if p_name not in player_stats:
                    player_stats[p_name] = {
                        "name": p_name, "total_wins": 0, "seasons_played": 0,
                        "1st": 0, "2nd": 0, "3rd": 0, "10th": 0,
                        "best": {"year": None, "wins": -1, "rank": 999},
                        "worst": {"year": None, "wins": 999, "rank": -1},
                    }
                ps = player_stats[p_name]
                ps["total_wins"] += wins
                ps["seasons_played"] += 1
                if rank == 1: ps["1st"] += 1
                elif rank == 2: ps["2nd"] += 1
                elif rank == 3: ps["3rd"] += 1
                elif rank == 10: ps["10th"] += 1

                if wins > ps["best"]["wins"] or (wins == ps["best"]["wins"] and rank < ps["best"]["rank"]):
                    ps["best"] = {"year": yr, "wins": wins, "rank": rank}
                if wins < ps["worst"]["wins"] or (wins == ps["worst"]["wins"] and rank > ps["worst"]["rank"]):
                    ps["worst"] = {"year": yr, "wins": wins, "rank": rank}
        except Exception:
            pass

    sorted_stats = sorted(player_stats.values(), key=lambda x: (x["total_wins"], x["1st"], x["2nd"]), reverse=True)
    top_seasons = sorted(season_records, key=lambda x: (x["wins"], -x["rank"]), reverse=True)[:10]
    bottom_seasons = sorted(season_records, key=lambda x: (x["wins"], -x["rank"]))[:10]

    return templates.TemplateResponse("overall_history.html", {
        "request": request,
        "stats": sorted_stats,
        "top_seasons": top_seasons,
        "bottom_seasons": bottom_seasons,
        "current_year": current_year,
        "undrafted_best": undrafted_best,
        "undrafted_worst": undrafted_worst,
    })


# ─── Head-to-Head ─────────────────────────────────────────────────────────────

@router.get("/headtohead")
async def headtohead_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    return RedirectResponse(f"/headtohead/{get_active_season(games, draft_results)}")


@router.get("/headtohead/history")
async def headtohead_history(request: Request):
    # Load Master Data once
    standings_master, teams, all_games, players, draft_order, all_draft_results, rules = load_data()
    current_year = _current_year(all_games)

    all_h2h = []
    all_schedules = []

    available_years = get_available_years(all_draft_results, all_games)

    for yr in available_years:
        try:
            # Deriving from master data in-memory
            standings = standings_master[standings_master["season"] == yr] if not standings_master.empty else standings_master
            games = all_games[all_games["season"] == yr] if not all_games.empty else all_games
            draft_results = all_draft_results[all_draft_results["season"] == yr] if not all_draft_results.empty else all_draft_results
            
            sched = analysis.get_enriched_schedule(games, draft_results, players, yr)
            if not sched.empty:
                all_schedules.append(sched)
            m = analysis.player_winlossmatrix(sched)
            if not m.empty:
                all_h2h.append({"year": yr, "table": m.to_html(classes="h2h-table", border=0)})
        except Exception:
            pass

    all_time_html = ""
    if all_schedules:
        try:
            combined = pd.concat(all_schedules, ignore_index=True)
            all_time_m = analysis.player_winlossmatrix(combined)
            if not all_time_m.empty:
                all_time_html = all_time_m.to_html(classes="h2h-table", border=0)
        except Exception:
            pass

    return templates.TemplateResponse("headtohead_history.html", {
        "request": request,
        "all_time_table": all_time_html,
        "history": all_h2h,
        "current_year": current_year,
    })


@router.get("/headtohead/{year}")
async def headtohead_by_year(request: Request, year: int):
    # Load master data once
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    # Filter in-memory
    standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    try:
        sched = analysis.get_enriched_schedule(games, draft_results, players, year)
        h2h_html = analysis.player_winlossmatrix(sched).to_html(classes="h2h-table", border=0)
    except Exception:
        h2h_html = ""

    return templates.TemplateResponse("headtohead.html", {
        "request": request,
        "h2h_html": h2h_html,
        "year": year,
        "current_year": _current_year(games),
        "available_years": get_available_years(all_draft_results, all_games),
    })
