"""routes/history_routes.py — Overall history and head-to-head routes."""
import pandas as pd

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import load_data, get_available_years, get_active_season, get_season_projection_legacy_shape
from services.utils import abbreviate_player_name as _first_name, filter_season, normalize_team_abbr
import services.analysis_service as analysis
from services.cache_service import get_game_predictions
from services.db_service import get_config_settings, is_draft_active_fail_closed
from services.session_service import decode_current_token
import services.team_page_service as team_page_service

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ─── Overall History ──────────────────────────────────────────────────────────

@router.get("/history")
async def overall_history(request: Request):
    standings_master, _, all_games, players, _, all_draft_results, rules = load_data()
    current_year = get_active_season(all_games, all_draft_results, rules)

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
    available_years = get_available_years(all_draft_results, all_games, rules)

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
    _, _, games, _, _, draft_results, rules = load_data()
    return RedirectResponse(f"/headtohead/{get_active_season(games, draft_results, rules)}")


@router.get("/headtohead/history")
async def headtohead_history(request: Request):
    # Load Master Data once
    standings_master, teams, all_games, players, draft_order, all_draft_results, rules = load_data()
    current_year = get_active_season(all_games, all_draft_results, rules)

    all_h2h = []
    all_schedules = []

    available_years = get_available_years(all_draft_results, all_games, rules)

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
        "current_year": get_active_season(all_games, all_draft_results, rules),
        "available_years": get_available_years(all_draft_results, all_games, rules),
    })


# ─── Player Profile ───────────────────────────────────────────────────────────

@router.get("/history/player/{player_id}")
async def player_profile_legacy(player_id: int):
    """Old bookmark URL; the canonical page is /player/{player_id}."""
    return RedirectResponse(f"/player/{player_id}", status_code=301)


@router.get("/player/{player_id}")
async def player_page(request: Request, player_id: int):
    analytics = analysis.get_player_analytics_data(player_id)
    if analytics is None:
        # No draft history is not "not found": render a zeroed career for a known player.
        _, _, _, players, _, _, _ = load_data()
        row = players[players["playerId"] == player_id] if players is not None and not players.empty else pd.DataFrame()
        if row.empty:
            raise HTTPException(status_code=404, detail="Player not found")
        p = row.iloc[0]
        analytics = {
            "player": {"playerId": player_id, "fullName": str(p.get("fullName", "")),
                       "nickName": str(p.get("nickName", ""))},
            "career": {"seasons": 0, "totalWins": 0, "avgWins": 0, "championships": 0,
                       "bestFinish": None, "worstFinish": None},
            "seasons": [],
            "slotAverages": {},
        }
    return templates.TemplateResponse(request, "player_profile.html", {
        "analytics": analytics,
        "player_id": player_id,
    })


def _viewer(request: Request):
    """(player_id | None, is_admin) from the session cookie / Bearer header; never raises."""
    token = request.cookies.get("session_token")
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ")
    if not token:
        return None, False
    payload = decode_current_token(token)
    if payload is None:
        return None, False
    try:
        return int(payload.get("sub")), payload.get("role") == "admin"
    except Exception:
        return None, False


@router.get("/teams")
async def teams_redirect(request: Request):
    """Teams nav target: the viewer's first drafted team this season, else the first team alphabetically."""
    player_id, _ = _viewer(request)
    target = team_page_service.team_list()[0]["abbr"]
    if player_id is not None:
        _, _, games, _, _, draft_results, rules = load_data()
        season = int(get_active_season(games, draft_results, rules))
        if draft_results is not None and not draft_results.empty and "season" in draft_results.columns:
            mine = draft_results[(draft_results["season"] == season)
                                 & (draft_results["playerId"] == player_id)].sort_values("draftPick")
            teams = [normalize_team_abbr(str(t)) for t in mine["team"].dropna().tolist()]
            teams = [t for t in teams if t in team_page_service.TEAM_NAMES]
            if teams:
                target = teams[0]
    return RedirectResponse(f"/team/{target}", status_code=302)


@router.get("/team/{abbr}")
async def team_page(request: Request, abbr: str):
    standings, _, games, players, _, draft_results, rules = load_data()
    season = int(get_active_season(games, draft_results, rules))
    _, is_admin = _viewer(request)
    include_projections = is_admin or not is_draft_active_fail_closed()
    data = {
        "standings": standings, "games": games, "players": players, "draft_results": draft_results,
        "predictions": get_game_predictions(season) if include_projections else {},
        "projections": get_season_projection_legacy_shape(season) if include_projections else {},
    }
    payload = team_page_service.build_team_page(abbr, season, data, include_projections)
    if payload is None:
        raise HTTPException(status_code=404, detail="Team not found")
    return templates.TemplateResponse(request, "team.html", {"payload": payload})
