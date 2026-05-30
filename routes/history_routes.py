"""routes/history_routes.py — Overall history and head-to-head routes."""
import pandas as pd

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import load_data, get_available_years, get_active_season, get_preseason_predictions
from services.utils import abbreviate_player_name as _first_name, filter_season
import services.analysis_service as analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ─── Overall History ──────────────────────────────────────────────────────────

@router.get("/history")
async def overall_history(request: Request):
    standings_master, _, all_games, players, _, all_draft_results, _ = load_data()
    current_year = get_active_season(all_games)

    player_stats: dict = {}
    season_records: list = []

    # Undrafted best & drafted worst
    undrafted_best = {"team": "None", "year": "-", "wins": -1}
    drafted_worst = {"team": "None", "year": "-", "wins": 999, "player": "-", "pick": "-"}

    if not standings_master.empty and "season" in standings_master.columns:
        for yr in standings_master["season"].unique():
            yr_standings = filter_season(standings_master, yr)
            yr_draft = filter_season(all_draft_results, yr)
            if not yr_standings.empty and not yr_draft.empty:
                drafted = set(yr_draft["team"].dropna().unique())
                for _, u in yr_standings[~yr_standings["team"].isin(drafted)].iterrows():
                    w = int(u.get("wins", 0))
                    if w > undrafted_best["wins"]:
                        undrafted_best = {"team": u["team"], "year": int(yr), "wins": w}
                for _, d in yr_standings[yr_standings["team"].isin(drafted)].iterrows():
                    w = int(d.get("wins", 0))
                    if w < drafted_worst["wins"]:
                        pick_row = yr_draft[yr_draft["team"] == d["team"]]
                        if not pick_row.empty:
                            pid = pick_row.iloc[0]["playerId"]
                            pick = int(pick_row.iloc[0]["draftPick"])
                            p_row = players[players["playerId"] == pid] if not players.empty else pd.DataFrame()
                            player_name = p_row.iloc[0]["fullName"] if not p_row.empty else f"Player {pid}"
                            drafted_worst = {"team": d["team"], "year": int(yr), "wins": w, "player": player_name, "pick": pick}

    if undrafted_best["wins"] == -1:
        undrafted_best["wins"] = 0
    if drafted_worst["wins"] == 999:
        drafted_worst["wins"] = 0

    # available_years derived once
    available_years = get_available_years(all_draft_results, all_games)

    for yr in available_years:
        try:
            # Derived from master data in-memory
            standings = filter_season(standings_master, yr)
            games = filter_season(all_games, yr)
            draft_results = filter_season(all_draft_results, yr)

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
                        "playerId": int(row.get("playerId", 0)),
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

    return templates.TemplateResponse(request, "overall_history.html", {
        "stats": sorted_stats,
        "top_seasons": top_seasons,
        "bottom_seasons": bottom_seasons,
        "current_year": current_year,
        "undrafted_best": undrafted_best,
        "drafted_worst": drafted_worst,
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
    current_year = get_active_season(all_games)

    all_h2h = []
    all_schedules = []

    available_years = get_available_years(all_draft_results, all_games)

    for yr in available_years:
        try:
            # Deriving from master data in-memory
            standings = filter_season(standings_master, yr)
            games = filter_season(all_games, yr)
            draft_results = filter_season(all_draft_results, yr)

            sched = analysis.get_enriched_schedule(games, draft_results, players, yr)
            if not sched.empty:
                all_schedules.append(sched)
            m = analysis.player_winlossmatrix(sched)
            if not m.empty:
                all_h2h.append({"year": yr, "table": m.rename(columns=_first_name, index=_first_name).to_html(classes="wp-data-table", border=0)})
        except Exception:
            pass

    all_time_html = ""
    if all_schedules:
        try:
            combined = pd.concat(all_schedules, ignore_index=True)
            all_time_m = analysis.player_winlossmatrix(combined)
            if not all_time_m.empty:
                all_time_html = all_time_m.rename(columns=_first_name, index=_first_name).to_html(classes="wp-data-table", border=0)
        except Exception:
            pass

    return templates.TemplateResponse(request, "headtohead_history.html", {
        "all_time_table": all_time_html,
        "history": all_h2h,
        "current_year": current_year,
    })


@router.get("/headtohead/{year}")
async def headtohead_by_year(request: Request, year: int):
    # Load master data once
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    # Filter in-memory
    standings = filter_season(all_st, year)
    games = filter_season(all_games, year)
    draft_results = filter_season(all_draft_results, year)

    try:
        sched = analysis.get_enriched_schedule(games, draft_results, players, year)
        h2h_df = analysis.player_winlossmatrix(sched)
        h2h_html = h2h_df.rename(columns=_first_name, index=_first_name).to_html(classes="wp-data-table", border=0)
    except Exception:
        h2h_html = ""

    return templates.TemplateResponse(request, "headtohead.html", {
        "h2h_html": h2h_html,
        "year": year,
        "current_year": get_active_season(games),
        "available_years": get_available_years(all_draft_results, all_games),
    })


# ─── Player Profile ───────────────────────────────────────────────────────────

def _get_player_analytics_data(player_id: int) -> dict | None:
    """Load and compute analytics for one player. Returns None if player not found."""
    standings_master, _, _, players, _, all_draft_results, _ = load_data()
    player_row = players[players["playerId"] == player_id] if not players.empty else pd.DataFrame()
    if player_row.empty:
        return None
    player_seasons = (
        all_draft_results[all_draft_results["playerId"] == player_id]["season"].unique()
        if not all_draft_results.empty else []
    )
    preseason_preds = {int(s): get_preseason_predictions(int(s)) for s in player_seasons}
    return analysis.get_player_analytics(
        player_id, all_draft_results, standings_master, players, preseason_preds
    )


@router.get("/history/player/{player_id}")
async def player_profile(request: Request, player_id: int):
    analytics = _get_player_analytics_data(player_id)
    if analytics is None:
        raise HTTPException(status_code=404, detail="Player not found")
    return templates.TemplateResponse(request, "player_profile.html", {
        "analytics": analytics,
    })
