"""routes/draft_routes.py — Draft history, draft results, draft board, and WebSocket routes."""
import json
import random
import pandas as pd
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from services.data_service import (
    load_data, get_available_years, get_draft_years, get_active_season,
)
from services.draft_service import load_draft_state, save_pick, undo_pick
from services.db_service import get_collection_df, add_draft_order, add_draft_rule
import services.analysis_service as analysis

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _current_year(games, draft_results=None) -> int:
    return get_active_season(games, draft_results)


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
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/draft-results")
async def draft_results_redirect():
    _, _, games, _, _, draft_results, _ = load_data()
    return RedirectResponse(f"/draft/{_current_year(games)}")


@router.get("/admin")
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


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

    return templates.TemplateResponse("draft_history.html", {
        "request": request,
        "data": final_data,
        "sorted_players": sorted_players,
        "sorted_teams": sorted_teams,
        "player_first_picks": player_first_picks,
        "team_first_picks": team_first_picks,
        "current_year": _current_year(games),
    })


# ─── Draft Results by Year ────────────────────────────────────────────────────

@router.get("/draft/{year}")
async def route_draft_results_by_year(request: Request, year: int):
    standings, _, games, players, _, draft_results, _ = load_data(year=year)
    _, _, _, _, _, all_draft_results, _ = load_data()

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

    best_overall = None
    best_by_round = {}
    if not merged.empty:
        sorted_awards = merged.sort_values(["TotalWinsBySeason", "draftPick"], ascending=[False, False])
        br = sorted_awards.iloc[0]
        best_overall = {
            "player": br.get("fullName", ""), "team": br.get("team", ""),
            "pick": int(br.get("draftPick", 0)), "wins": int(br.get("TotalWinsBySeason", 0)),
        }
        for rnum, label, lo, hi in [(1, "Round 1 (Picks 1-10)", 1, 10),
                                     (2, "Round 2 (Picks 11-20)", 11, 20),
                                     (3, "Round 3 (Picks 21-30)", 21, 30)]:
            rd = sorted_awards[(sorted_awards["draftPick"] >= lo) & (sorted_awards["draftPick"] <= hi)]
            if not rd.empty:
                rb = rd.iloc[0]
                best_by_round[rnum] = {
                    "label": label, "player": rb.get("fullName", ""), "team": rb.get("team", ""),
                    "pick": int(rb.get("draftPick", 0)), "wins": int(rb.get("TotalWinsBySeason", 0)),
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
                "time": int(q_row.get("time_taken_seconds", 0))
            }
            slowest = {
                "player": s_row.get("fullName", ""), "team": s_row.get("team", ""),
                "time": int(s_row.get("time_taken_seconds", 0))
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

    return templates.TemplateResponse("draft_results.html", {
        "request": request,
        "data": merged.to_dict(orient="records"),
        "year": year,
        "current_year": _current_year(games),
        "available_years": get_draft_years(all_draft_results),
        "best_overall": best_overall,
        "best_by_round": best_by_round,
        "quickest": quickest,
        "slowest": slowest
    })


# ─── WebSocket ────────────────────────────────────────────────────────────────

import os
ROOM_CODE = os.environ.get("ROOM_CODE", "test").strip().lower()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    current_view_year = None # Default to latest
    try:
        await websocket.send_json({"type": "state", "payload": load_draft_state(connected_players)})
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
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
                    else:
                        await websocket.send_json({"type": "error", "message": "Invalid Room Code."})

            elif action == "reauthenticate":
                pid = msg.get("playerId")
                if pid and str(pid).strip().lower() != "null":
                    try:
                        pid = int(pid)
                        connected_players.add(pid)
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
                    except ValueError:
                        pass

            elif action == "undo_pick":
                pid = msg.get("playerId")
                state = load_draft_state(connected_players, year=target_year)
                
                player = next((p for p in state["all_players"] if p["playerId"] == int(pid)), None)
                if not player or player.get("role") != "admin":
                    await websocket.send_json({"type": "error", "message": "Unauthorized: Admin access required."})
                    continue
                
                active_pick = state["active_pick"]
                last_pick_num = active_pick - 1
                if last_pick_num < 1:
                    await websocket.send_json({"type": "error", "message": "No picks to undo."})
                    continue
                
                undo_pick(state["season"], last_pick_num)
                await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})

            elif action == "force_pick":
                pid = msg.get("playerId")
                team = msg.get("team")
                state = load_draft_state(connected_players, year=target_year)
                
                player = next((p for p in state["all_players"] if p["playerId"] == int(pid)), None)
                if not player or player.get("role") != "admin":
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
                    await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})

            elif action == "pick":
                team = msg.get("team")
                pid = msg.get("playerId")
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

                # Check permissions: Is it their turn OR are they an admin?
                active_pick = state["active_pick"]
                target_pid = next((x["playerId"] for x in state["draft_board"] if x["pick"] == active_pick), None)
                try:
                    target_pid_int = int(target_pid) if target_pid is not None else None
                    pid_int = int(pid) if pid is not None else None
                except (ValueError, TypeError):
                    await websocket.send_json({"type": "error", "message": "Invalid Player ID."})
                    continue

                player = next((p for p in state["all_players"] if p["playerId"] == pid_int), None)
                is_admin = player and player.get("role") == "admin"

                if pid_int != target_pid_int and not is_admin:
                    await websocket.send_json({"type": "error", "message": "It is not your turn to pick!"})
                    continue

                if target_pid is not None:
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=player.get("playerName") if is_admin else None)
                    await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        manager.disconnect(websocket)
        import traceback
        print(f"WS error: {e}")
        traceback.print_exc()
