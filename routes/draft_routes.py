"""routes/draft_routes.py — Draft history, draft results, draft board, and WebSocket routes."""
import asyncio
import json
import logging
import random
import pandas as pd
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import (
    load_data, get_available_years, get_draft_years, get_active_season,
)
from services.draft_service import load_draft_state, save_pick, undo_pick, reset_pick
from services.db_service import get_collection_df, add_draft_order, add_draft_rule
from services.chat_service import post_system_message, post_chat_message, get_recent_messages
import services.analysis_service as analysis
from services.constants import DRAFT_ROUNDS

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_authenticated_admin(socket_player_id: int | None, all_players: list[dict]) -> dict | None:
    """Return the player dict if the authenticated socket user is an admin, else None.

    Always uses socket_player_id (the server-validated identity from the verify/
    reauthenticate handshake) — never the playerId field from a client message,
    which is attacker-controlled.  Callers must pass socket_player_id directly.
    """
    if socket_player_id is None:
        return None
    player = next((p for p in all_players if p["playerId"] == socket_player_id), None)
    if not player or player.get("role") != "admin":
        return None
    return player


def _format_pick_time(seconds: int) -> str:
    """Convert raw seconds to a human-readable duration string."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}h {m}m {s}s"
    if seconds >= 60:
        m = seconds // 60
        s = seconds % 60
        return f"{m}m {s}s"
    return f"{seconds}s"


# ─── Draft board (live WebSocket) ─────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, message: dict):
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()
connected_players: set = set()


@router.get("/draft")
async def serve_draft_board(request: Request):
    import os
    return templates.TemplateResponse(request, "index.html", {
        "vapid_public_key": os.environ.get("VAPID_PUBLIC_KEY", ""),
    })


@router.get("/draft-results")
async def draft_results_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    return RedirectResponse(f"/draft/{get_active_season(games)}")


@router.get("/admin")
async def serve_admin(request: Request):
    return templates.TemplateResponse(request, "admin.html")


# ─── Draft History ────────────────────────────────────────────────────────────

@router.get("/draft/history")
async def route_draft_history(request: Request):
    standings, _, games, players, _, draft_results, _ = load_data()

    draft_df = pd.merge(draft_results, players, on="playerId", how="inner")
    merged = pd.merge(draft_df, standings[["team", "season", "wins"]], on=["team", "season"], how="left")
    merged = merged.dropna(subset=["wins"])

    # #1 overall pick count per PLAYER — deduplicate by season so multi-team drafts don't inflate count
    first_pick_rows = merged[merged["draftPick"].fillna(0).astype(int) == 1]
    player_first_picks = (
        first_pick_rows
        .drop_duplicates(subset=["fullName", "season"])
        .groupby("fullName")
        .size()
        .to_dict()
    )
    player_first_picks = {k: int(v) for k, v in player_first_picks.items()}

    # #1 overall pick count per TEAM — how many times this team was the #1 overall pick
    team_first_picks = (
        first_pick_rows
        .groupby("team")
        .size()
        .to_dict()
    )
    team_first_picks = {k: int(v) for k, v in team_first_picks.items()}

    summary_data = []

    for (player, team), group in merged.groupby(["fullName", "team"]):
        times_picked = len(group)
        total_wins = int(group["wins"].sum())
        best_row = group.loc[group["wins"].idxmax()]
        worst_row = group.loc[group["wins"].idxmin()]
        picks_list = sorted(
            [
                {
                    "season": int(r["season"]),
                    "draftPick": int(r["draftPick"]) if pd.notna(r.get("draftPick")) else None,
                    "wins": int(r["wins"]),
                }
                for _, r in group.iterrows()
            ],
            key=lambda x: x["season"],
        )
        summary_data.append({
            "fullName": player,
            "team": team,
            "times_picked": times_picked,
            "total_wins": total_wins,
            "best_year": int(best_row["season"]),
            "best_wins": int(best_row["wins"]),
            "worst_year": int(worst_row["season"]),
            "worst_wins": int(worst_row["wins"]),
            "picks_list": picks_list,
        })

    # Undrafted teams
    for yr in standings["season"].unique():
        yr_st = standings[standings["season"] == yr]
        yr_dr = draft_results[draft_results["season"] == yr]
        if yr_st.empty or yr_dr.empty:
            continue
        drafted = set(yr_dr["team"].dropna().unique())
        for _, u in yr_st[~yr_st["team"].isin(drafted)].iterrows():
            summary_data.append({
                "fullName": "Undrafted Teams",
                "team": u["team"],
                "times_picked": 1,
                "total_wins": int(u.get("wins", 0)),
                "best_year": int(yr),
                "best_wins": int(u.get("wins", 0)),
                "worst_year": int(yr),
                "worst_wins": int(u.get("wins", 0)),
                "picks_list": [{"season": int(yr), "draftPick": None, "wins": int(u.get("wins", 0))}],
            })

    # Merge undrafted rows per team (aggregate wins across years)
    from itertools import groupby as igrpby
    merged_undrafted = {}
    final_data = []
    for item in summary_data:
        if item["fullName"] != "Undrafted Teams":
            final_data.append(item)
        else:
            key = item["team"]
            if key not in merged_undrafted:
                merged_undrafted[key] = {
                    "fullName": "Undrafted Teams", "team": key,
                    "times_picked": 0, "total_wins": 0,
                    "best_year": None, "best_wins": -1,
                    "worst_year": None, "worst_wins": 999,
                    "picks_list": [],
                }
            m = merged_undrafted[key]
            m["times_picked"] += item["times_picked"]
            m["total_wins"] += item["total_wins"]
            for pl in item["picks_list"]:
                m["picks_list"].append(pl)
                if pl["wins"] > m["best_wins"]:
                    m["best_wins"] = pl["wins"]; m["best_year"] = pl["season"]
                if pl["wins"] < m["worst_wins"]:
                    m["worst_wins"] = pl["wins"]; m["worst_year"] = pl["season"]

    final_data.extend(sorted(merged_undrafted.values(), key=lambda x: -x["total_wins"]))

    # Compute player total wins and sort players by wins desc
    player_total_wins = {}
    for item in final_data:
        if item["fullName"] != "Undrafted Teams":
            player_total_wins[item["fullName"]] = player_total_wins.get(item["fullName"], 0) + item["total_wins"]
    sorted_players = sorted(player_total_wins.keys(), key=lambda p: -player_total_wins[p])
    # Add Undrafted Teams last
    if any(d["fullName"] == "Undrafted Teams" for d in final_data):
        sorted_players.append("Undrafted Teams")

    # Compute team total wins and sort teams by wins desc
    team_total_wins = {}
    for item in final_data:
        t = item["team"]
        team_total_wins[t] = team_total_wins.get(t, 0) + item["total_wins"]
    sorted_teams = sorted(team_total_wins.keys(), key=lambda t: -team_total_wins[t])

    # Sort final_data so grouped_players/teams iteration is stable — by player wins desc, then team wins desc
    final_data.sort(key=lambda x: (-player_total_wins.get(x["fullName"], 0), -x["total_wins"]))

    return templates.TemplateResponse(request, "draft_history.html", {
        "data": final_data,
        "sorted_players": sorted_players,
        "sorted_teams": sorted_teams,
        "player_first_picks": player_first_picks,
        "team_first_picks": team_first_picks,
        "current_year": get_active_season(games),
    })


# ─── Draft Results by Year ────────────────────────────────────────────────────

@router.get("/draft/{year}")
async def route_draft_results_by_year(request: Request, year: int):
    # Load Master Data once
    all_st, teams, all_games, players, draft_order, all_draft_results, rules = load_data()

    # Filter for the year in-memory
    standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
    games = all_games[all_games['season'] == year] if not all_games.empty else all_games
    draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results

    dr_year = draft_results[draft_results["season"] == year].copy()
    merged = pd.merge(dr_year, players, on="playerId", how="inner")

    st_year = standings[standings["season"] == year]
    if not st_year.empty and "wins" in st_year.columns:
        merged = pd.merge(merged, st_year[["team", "wins"]], on="team", how="left")
        merged["TotalWinsBySeason"] = merged["wins"].fillna(0).astype(int)
    else:
        merged["TotalWinsBySeason"] = 0

    if "draftPick" in merged.columns:
        merged = merged.sort_values("draftPick")

    # Compute League-Wide Win Rank: rank ALL 32 teams by wins, 1 = most wins.
    # Pick Value = draftPick - leagueWinRank. Positive = overperformed for draft slot.
    nfl_standard_teams = {"ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"}
    if not games.empty:
        season_teams = set(games["home_team"].unique()) | set(games["away_team"].unique())
    else:
        season_teams = nfl_standard_teams
    drafted_teams_set = set(dr_year["team"].dropna().tolist()) if not dr_year.empty else set()

    # Build full league standings for ranking
    # Tiebreakers mirror NFL procedures (using available data):
    # 1. wins  2. net points  3. strength of victory  4. strength of schedule  5. points scored  6. team (stable fallback)
    _tb_cols = {"net": 0.0, "sov": 0.0, "sos": 0.0, "scored": 0}
    league_rows = []
    for team in season_teams:
        team_st = st_year[st_year["team"] == team]
        row = {"team": team, "wins": 0}
        if not team_st.empty:
            row["wins"] = int(team_st["wins"].iloc[0]) if "wins" in team_st.columns else 0
            for col, default in _tb_cols.items():
                row[col] = float(team_st[col].iloc[0]) if col in team_st.columns else default
        else:
            row.update(_tb_cols)
        league_rows.append(row)
    league_df = (
        pd.DataFrame(league_rows)
        .sort_values(
            ["wins", "net", "sov", "sos", "scored", "team"],
            ascending=[False, False, False, False, False, True],
        )
        .reset_index(drop=True)
    )
    league_df["win_rank"] = range(1, len(league_df) + 1)
    rank_map = dict(zip(league_df["team"], league_df["win_rank"]))

    # Apply league-wide win rank to drafted teams
    if not merged.empty:
        merged["win_rank"] = merged["team"].map(rank_map)
        merged["pick_value"] = merged["draftPick"].astype(int) - merged["win_rank"].fillna(0).astype(int)
        merged = merged.sort_values("draftPick")
    else:
        merged["win_rank"] = None
        merged["pick_value"] = None

    best_overall = None
    worst_overall = None
    best_by_round = {}
    if not merged.empty:
        # Best Overall: highest pick_value (best value relative to draft slot)
        best_value_sorted = merged.sort_values("pick_value", ascending=False)
        bv = best_value_sorted.iloc[0]
        best_overall = {
            "player": bv.get("fullName", ""), "team": bv.get("team", ""),
            "pick": int(bv.get("draftPick", 0)), "wins": int(bv.get("TotalWinsBySeason", 0)),
            "pick_value": int(bv.get("pick_value", 0)),
            "win_rank": int(bv.get("win_rank", 0)),
        }
        # Worst Overall: lowest pick_value (worst value relative to draft slot)
        wv = best_value_sorted.iloc[-1]
        worst_overall = {
            "player": wv.get("fullName", ""), "team": wv.get("team", ""),
            "pick": int(wv.get("draftPick", 0)), "wins": int(wv.get("TotalWinsBySeason", 0)),
            "pick_value": int(wv.get("pick_value", 0)),
            "win_rank": int(wv.get("win_rank", 0)),
        }
        # Best by round: same pick_value metric as best overall (draft slot vs. league win rank)
        sorted_awards = merged.sort_values(["pick_value", "TotalWinsBySeason"], ascending=[False, False])
        for rnum, (lo, hi) in DRAFT_ROUNDS.items():
            label = f"Round {rnum} (Picks {lo}-{hi})"
            rd = sorted_awards[(sorted_awards["draftPick"] >= lo) & (sorted_awards["draftPick"] <= hi)]
            if not rd.empty:
                rb = rd.iloc[0]
                best_by_round[rnum] = {
                    "label": label, "player": rb.get("fullName", ""), "team": rb.get("team", ""),
                    "pick": int(rb.get("draftPick", 0)), "wins": int(rb.get("TotalWinsBySeason", 0)),
                    "pick_value": int(rb.get("pick_value", 0)) if rb.get("pick_value") is not None else None,
                    "win_rank": int(rb.get("win_rank", 0)) if rb.get("win_rank") is not None else None,
                }

    quickest = None
    slowest = None
    if "time_taken_seconds" in merged.columns:
        valid_times = merged[merged["time_taken_seconds"] > 0]
        if not valid_times.empty:
            sorted_times = valid_times.sort_values("time_taken_seconds")
            q_row = sorted_times.iloc[0]
            s_row = sorted_times.iloc[-1]
            quickest = {
                "player": q_row.get("fullName", ""), "team": q_row.get("team", ""),
                "time": _format_pick_time(int(q_row.get("time_taken_seconds", 0)))
            }
            slowest = {
                "player": s_row.get("fullName", ""), "team": s_row.get("team", ""),
                "time": _format_pick_time(int(s_row.get("time_taken_seconds", 0)))
            }

    # Draft Value Calculus
    from services.data_service import get_preseason_predictions
    preds = get_preseason_predictions(year)
    def calculate_draft_value(row):
        team = row.get("team")
        actual = float(row.get("TotalWinsBySeason", 0))
        p_obj = preds.get(team)
        if p_obj and isinstance(p_obj, dict):
            proj = p_obj.get("projected_wins")
            if proj is not None and proj > 0:
                return round(actual - proj, 1)
        return None

    def get_proj(row):
        team = row.get("team")
        p_obj = preds.get(team)
        if p_obj and isinstance(p_obj, dict):
            return p_obj.get("projected_wins")
        return None
        
    merged["projected_wins"] = merged.apply(get_proj, axis=1)
    merged["draft_value"] = merged.apply(calculate_draft_value, axis=1)

    # Season winner: player with Rank == 1 in final pool standings
    winner_player_id = None
    pool = analysis.calculate_wins_pool_standings(standings, draft_results, players, year)
    if not pool.empty and "Rank" in pool.columns:
        winner_rows = pool[pool["Rank"] == 1]
        if not winner_rows.empty:
            winner_player_id = int(winner_rows.iloc[0]["playerId"])

    # Slot averages: league-wide avg wins per draft slot across all seasons
    if not all_draft_results.empty and not all_st.empty and "wins" in all_st.columns:
        dr_with_wins = pd.merge(
            all_draft_results[["draftPick", "team", "season"]],
            all_st[["team", "season", "wins"]],
            on=["team", "season"],
            how="left",
        )
        slot_avgs = dr_with_wins.groupby("draftPick")["wins"].mean().round(1).to_dict()
    else:
        slot_avgs = {}
    merged["slot_avg_wins"] = merged["draftPick"].map(lambda p: slot_avgs.get(int(p)))

    # Undrafted teams with league-wide win rank
    undrafted_team_abbrs = sorted(season_teams - drafted_teams_set)
    undrafted_teams = []
    for team in undrafted_team_abbrs:
        team_standings = st_year[st_year["team"] == team]
        actual_wins = int(team_standings["wins"].iloc[0]) if not team_standings.empty and "wins" in team_standings.columns else 0
        p_obj = preds.get(team)
        proj = p_obj.get("projected_wins") if p_obj and isinstance(p_obj, dict) else None
        undrafted_teams.append({
            "team": team,
            "actual_wins": actual_wins,
            "projected_wins": proj,
            "win_rank": rank_map.get(team),
        })
    undrafted_teams.sort(key=lambda x: x.get("win_rank") or 99)

    return templates.TemplateResponse(request, "draft_results.html", {
        "data": merged.to_dict(orient="records"),
        "year": year,
        "current_year": get_active_season(games),
        "available_years": get_draft_years(all_draft_results),
        "best_overall": best_overall,
        "worst_overall": worst_overall,
        "best_by_round": best_by_round,
        "quickest": quickest,
        "slowest": slowest,
        "undrafted_teams": undrafted_teams,
        "winner_player_id": winner_player_id,
    })


# ─── WebSocket ────────────────────────────────────────────────────────────────

import os
ROOM_CODE = os.environ.get("ROOM_CODE", "test").strip().lower()


async def _broadcast_pick_messages(manager, old_state: dict, new_state: dict, team: str) -> None:
    """Post pick + on-the-clock system messages to chat and broadcast them."""
    season = old_state["season"]
    active_pick = old_state["active_pick"]
    picker_pid = next(
        (x["playerId"] for x in old_state["draft_board"] if x["pick"] == active_pick), None
    )
    picker = next(
        (p for p in old_state.get("all_players", []) if p["playerId"] == picker_pid),
        None,
    )
    picker_name = picker["playerName"] if picker else "Unknown"
    pick_msg = post_system_message(season, f"🏈 {picker_name} picks {team} — Pick #{active_pick}")
    if pick_msg:
        await manager.broadcast({
            "type": "chat_message",
            "msgType": "system",
            "playerName": "System",
            "text": pick_msg["text"],
            "timestamp": pick_msg["timestamp"],
        })
    new_active = new_state.get("active_pick", active_pick + 1)
    if new_active <= 30:
        next_entry = next(
            (x for x in new_state["draft_board"] if x["pick"] == new_active), None
        )
        next_pid = next_entry["playerId"] if next_entry else None
        next_player = next(
            (p for p in new_state.get("all_players", []) if p["playerId"] == next_pid),
            None,
        )
        if next_player:
            clock_msg = post_system_message(
                season, f"⏰ {next_player['playerName']}, you're on the clock! Pick #{new_active}"
            )
            if clock_msg:
                await manager.broadcast({
                    "type": "chat_message",
                    "msgType": "system",
                    "playerName": "System",
                    "text": clock_msg["text"],
                    "timestamp": clock_msg["timestamp"],
                })
            # Fire-and-forget push notification — never block the WebSocket flow
            if next_pid is not None:
                asyncio.get_event_loop().run_in_executor(
                    None,
                    _send_push_sync,
                    next_pid,
                    "⏰ You're on the clock!",
                    f"Pick #{new_active} — open WinsPool to make your pick.",
                )


def _send_push_sync(player_id: int, title: str, body: str) -> None:
    """Synchronous wrapper for push send — runs in thread pool executor."""
    try:
        from services.push_service import send_push_notification
        send_push_notification(player_id, title, body)
    except Exception:
        pass


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    current_view_year = None # Default to latest
    socket_player_id: int | None = None  # track which player authenticated on this socket
    try:
        initial_state = load_draft_state(connected_players)
        await websocket.send_json({"type": "state", "payload": initial_state})
        history = get_recent_messages(initial_state.get("season", 2026))
        await websocket.send_json({"type": "chat_history", "messages": history})
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")

            # Determine target season for this action
            # If not provided in message, use current_view_year, else fallback to latest in load_draft_state
            target_year = msg.get("year") or current_view_year

            if action == "switch_season":
                yr = msg.get("year")
                if yr:
                    current_view_year = int(yr)
                    await websocket.send_json({"type": "state", "payload": load_draft_state(connected_players, year=current_view_year)})
                continue

            elif action == "request_signin":
                pid = msg.get("playerId")
                if pid and str(pid).strip().lower() != "null":
                    await websocket.send_json({
                        "type": "verification_sent",
                        "playerId": int(pid),
                        "method": "room_code",
                    })

            elif action == "verify_code":
                pid, code = msg.get("playerId"), msg.get("code")
                if pid and code:
                    pid = int(pid)
                    if str(code).strip().lower() == ROOM_CODE:
                        connected_players.add(pid)
                        socket_player_id = pid
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
                    else:
                        await websocket.send_json({"type": "error", "message": "Invalid Room Code."})

            elif action == "reauthenticate":
                pid = msg.get("playerId")
                if pid and str(pid).strip().lower() != "null":
                    try:
                        pid = int(pid)
                        # SEC-H4: Validate the playerId against the database
                        # before trusting the client-supplied identity.
                        from services.db_service import get_player_by_id
                        player = get_player_by_id(str(pid))
                        if not player or not player.get("password_hash"):
                            await websocket.send_json({"type": "error", "message": "Session expired. Please log in again."})
                            continue
                        connected_players.add(pid)
                        socket_player_id = pid
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
                    except ValueError:
                        pass

            elif action == "undo_pick":
                state = load_draft_state(connected_players, year=target_year)
                player = _get_authenticated_admin(socket_player_id, state["all_players"])
                if not player:
                    await websocket.send_json({"type": "error", "message": "Unauthorized: Admin access required."})
                    continue
                
                active_pick = state["active_pick"]
                last_pick_num = active_pick - 1
                if last_pick_num < 1:
                    await websocket.send_json({"type": "error", "message": "No picks to undo."})
                    continue
                
                undone_entry = next((x for x in state.get("draft_board", []) if x["pick"] == last_pick_num), None)
                undone_team = undone_entry["team"] if undone_entry else "?"
                undone_player = undone_entry["playerName"] if undone_entry else "?"
                undo_pick(state["season"], last_pick_num)
                new_state = load_draft_state(connected_players, year=target_year)
                await manager.broadcast({"type": "state", "payload": new_state})
                undo_msg = post_system_message(
                    state["season"],
                    f"↩️ Pick #{last_pick_num} ({undone_player} → {undone_team}) has been undone by {player.get('playerName', 'Admin')}"
                )
                if undo_msg:
                    await manager.broadcast({
                        "type": "chat_message", "msgType": "system",
                        "playerName": "System", "text": undo_msg["text"], "timestamp": undo_msg["timestamp"],
                    })

            elif action == "reset_pick":
                state = load_draft_state(connected_players, year=target_year)
                player = _get_authenticated_admin(socket_player_id, state["all_players"])
                if not player:
                    await websocket.send_json({"type": "error", "message": "Unauthorized: Admin access required."})
                    continue
                
                active_pick = state["active_pick"]
                # Reset can happen on current active pick (just reset timer)
                # or previous pick (undo + reset timer)
                target_pick = msg.get("pick") or active_pick
                
                # If target_pick > active_pick, it's invalid
                if target_pick > active_pick:
                    await websocket.send_json({"type": "error", "message": "Cannot reset a future pick."})
                    continue
                
                reset_pick(state["season"], target_pick)
                new_state = load_draft_state(connected_players, year=target_year)
                await manager.broadcast({"type": "state", "payload": new_state})
                reset_msg = post_system_message(
                    state["season"],
                    f"⏱️ Pick #{target_pick} clock has been reset by {player.get('playerName', 'Admin')}"
                )
                if reset_msg:
                    await manager.broadcast({
                        "type": "chat_message", "msgType": "system",
                        "playerName": "System", "text": reset_msg["text"], "timestamp": reset_msg["timestamp"],
                    })

            elif action == "force_pick":
                team = msg.get("team")
                state = load_draft_state(connected_players, year=target_year)
                player = _get_authenticated_admin(socket_player_id, state["all_players"])
                if not player:
                    await websocket.send_json({"type": "error", "message": "Unauthorized: Admin access required."})
                    continue
                
                if not state["draft_ready"]:
                    await websocket.send_json({"type": "error", "message": "Draft cannot start until everyone is signed in!"})
                    continue
                if state["active_pick"] > 30:
                    await websocket.send_json({"type": "error", "message": "Draft is already complete!"})
                    continue
                if team not in state["available_teams"]:
                    await websocket.send_json({"type": "error", "message": "Team is not available!"})
                    continue

                active_pick = state["active_pick"]
                target_pid = next((x["playerId"] for x in state["draft_board"] if x["pick"] == active_pick), None)
                if target_pid is not None:
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=player.get("playerName", "Admin"))
                    new_state = load_draft_state(connected_players, year=target_year)
                    await manager.broadcast({"type": "state", "payload": new_state})
                    await _broadcast_pick_messages(manager, state, new_state, team)

            elif action == "pick":
                team = msg.get("team")
                state = load_draft_state(connected_players, year=target_year)
                if not state["draft_ready"]:
                    await websocket.send_json({"type": "error", "message": "Draft cannot start until everyone is signed in!"})
                    continue
                if state["active_pick"] > 30:
                    await websocket.send_json({"type": "error", "message": "Draft is already complete!"})
                    continue
                if team not in state["available_teams"]:
                    await websocket.send_json({"type": "error", "message": "Team is not available!"})
                    continue

                # Check permissions using socket_player_id (server-validated identity),
                # never the client-supplied playerId from the message.
                active_pick = state["active_pick"]
                target_pid = next((x["playerId"] for x in state["draft_board"] if x["pick"] == active_pick), None)
                try:
                    target_pid_int = int(target_pid) if target_pid is not None else None
                except (ValueError, TypeError):
                    await websocket.send_json({"type": "error", "message": "Invalid draft board state."})
                    continue

                # Resolve admin status from the authenticated socket identity only
                admin_player = _get_authenticated_admin(socket_player_id, state["all_players"])
                is_admin = admin_player is not None

                if socket_player_id != target_pid_int and not is_admin:
                    await websocket.send_json({"type": "error", "message": "It is not your turn to pick!"})
                    continue

                if target_pid is not None:
                    executed_by = admin_player.get("playerName") if is_admin and socket_player_id != target_pid_int else None
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=executed_by)
                    new_state = load_draft_state(connected_players, year=target_year)
                    await manager.broadcast({"type": "state", "payload": new_state})
                    await _broadcast_pick_messages(manager, state, new_state, team)

            elif action == "chat":
                if socket_player_id is None:
                    await websocket.send_json({"type": "error", "message": "Sign in before chatting."})
                    continue
                state = load_draft_state(connected_players, year=target_year)
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                if text.lower().startswith("/teams"):
                    teams_str = ", ".join(sorted(state.get("available_teams", [])))
                    msg_doc = post_system_message(
                        state["season"], f"📋 Available teams: {teams_str}"
                    )
                else:
                    player_info = next(
                        (p for p in state.get("all_players", []) if p["playerId"] == socket_player_id),
                        None,
                    )
                    player_name = player_info["playerName"] if player_info else f"Player {socket_player_id}"
                    msg_doc = post_chat_message(state["season"], player_name, text)
                if msg_doc:
                    await manager.broadcast({
                        "type": "chat_message",
                        "msgType": msg_doc["type"],
                        "playerName": msg_doc["playerName"],
                        "text": msg_doc["text"],
                        "timestamp": msg_doc["timestamp"],
                    })

            elif action == "teams_list":
                state = load_draft_state(connected_players, year=target_year)
                teams_str = ", ".join(sorted(state.get("available_teams", [])))
                msg_doc = post_system_message(
                    state["season"], f"📋 Available teams: {teams_str}"
                )
                if msg_doc:
                    await manager.broadcast({
                        "type": "chat_message",
                        "msgType": "system",
                        "playerName": "System",
                        "text": msg_doc["text"],
                        "timestamp": msg_doc["timestamp"],
                    })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        if socket_player_id is not None:
            connected_players.discard(socket_player_id)
    except Exception as e:
        manager.disconnect(websocket)
        if socket_player_id is not None:
            connected_players.discard(socket_player_id)
        logger.exception("WebSocket error: %s", e)
