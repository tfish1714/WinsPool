import logging
import os
import pandas as pd
from typing import Dict, Any, List
from services.db_service import get_collection_df, add_draft_result, update_player_cell, delete_draft_pick
from services.data_service import get_season_projection, get_team_schedule, load_data
import time

logger = logging.getLogger(__name__)

_CACHED_DRAFT_STATE = None

def sanitize_state(obj):
    """Recursively replaces NaN/Inf with None to ensure JSON compliance."""
    if isinstance(obj, dict):
        return {k: sanitize_state(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_state(x) for x in obj]
    elif isinstance(obj, float):
        if obj != obj or obj == float('inf') or obj == float('-inf'): # NaN or Inf
            return None
    elif pd.isna(obj) and not isinstance(obj, (str, bytes)):
        return None
    return obj

def load_draft_state(connected_players: set, year: int = None) -> Dict[str, Any]:
    """
    Constructs the current draft board state, including analytics and Elo projections.
    Optimized to reuse cached game data and skip redundant historical Elo re-replays.
    """
    global _CACHED_DRAFT_STATE
    start_time = time.time()
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    
    # 1. Quick deepcopy from local Singleton if available
    if year is None and _CACHED_DRAFT_STATE is not None:
        import copy
        state = copy.deepcopy(_CACHED_DRAFT_STATE)
        state["draft_ready"] = True
        state["connected_players"] = list(connected_players)
        state["connected_count"] = len(connected_players) if connected_players else 0
        for p in state["all_players"]:
            p["connected"] = p["playerId"] in connected_players
        return state

    # 2. Comprehensive Data Fetch (Smart Caching via data_service)
    # Optimization: We check the season first to avoid fetching ALL historical games from Firestore (huge)
    # unless we actually need to initialize the PredictionService history.
    t_start_fetch = time.time()
    
    if year:
        season = int(year)
    else:
        # Quick check for current season (draft_order is small, no year filter needed)
        d_order_temp = get_collection_df('draft_order')
        season_val = d_order_temp['season'].max()
        if pd.isna(season_val):
            # Avoid reloading everything just to get the season if possible, but for first load it's fine
            d_order = load_data().draft_order
            season = int(d_order['season'].iloc[0]) if not d_order.empty else 2024
        else:
            season = int(season_val)
    
    logger.info("draft_state: processing season %s", season)

    # If after determining season, it's still None (e.g., no data at all), return empty state
    if season is None:
        return {
            "season": None, "draft_board": [], "active_pick": 1,
            "available_teams": [], "draft_ready": False,
            "connected_players": list(connected_players), "all_players": [],
        }

    from services.prediction_service import PredictionService
    
    # 2. Load Data (GRANULAR: Only fetches current season as we use cached analytics)
    if is_debug: 
        logger.debug("draft_state: fetching granular season data for %s...", season)
    _bundle = load_data(year=season)
    standings, games_master, players_df = _bundle.standings, _bundle.games, _bundle.players
    d_order, results, rules = _bundle.draft_order, _bundle.draft_results, _bundle.draft_order_rules

    if is_debug:
        logger.debug("draft_state: data fetch took %.3fs", time.time() - t_start_fetch)
    
    # Slice datasets for the target season (if not already filtered)
    d_order_season = d_order[d_order['season'] == season] if 'season' in d_order.columns else d_order
    rules_season = rules[rules['season'] == season] if 'season' in rules.columns else rules
    results_season = results[results['season'] == season] if 'season' in results.columns else results
    games_season = games_master[games_master['season'] == season] if 'season' in games_master.columns else games_master
    
    # 3. Build Draft Board
    merged_order = pd.merge(d_order_season, rules_season, on=['season', 'draftOrder'])
    melted = pd.melt(merged_order, id_vars=['playerId'], value_vars=['pickOne', 'pickTwo', 'pickThree'], value_name='draftPick')
    melted = melted.dropna(subset=['draftPick']).sort_values('draftPick').reset_index(drop=True)
    melted['draftPick'] = melted['draftPick'].astype(int)
    
    melted = pd.merge(melted, players_df, on='playerId', how='left')
    melted = pd.merge(melted, results_season[['draftPick', 'team']], on='draftPick', how='left')
    
    # Build time_taken map: pick → seconds
    time_taken_map: dict[int, float] = {}
    if 'time_taken_seconds' in results_season.columns:
        for _, r in results_season[['draftPick', 'time_taken_seconds']].dropna().iterrows():
            time_taken_map[int(r['draftPick'])] = float(r['time_taken_seconds'])

    draft_board = []
    active_pick = None
    picked_teams = set()
    for _, row in melted.iterrows():
        pid = int(row['playerId']) if pd.notna(row['playerId']) else None
        if pid is None: continue

        pname = row.get('nickName') or row.get('fullName', f"Player {pid}")
        team = row['team'] if pd.notna(row['team']) else None
        if team: picked_teams.add(team)

        draft_board.append({
            "pick": int(row['draftPick']),
            "playerId": pid,
            "playerName": pname,
            "team": team,
            "time_taken_seconds": time_taken_map.get(int(row['draftPick'])),
        })
        if team is None and active_pick is None:
            active_pick = int(row['draftPick'])
            
    if active_pick is None: active_pick = len(melted) + 1
    
    # 4. Available Teams (Fallback to standard list)
    nfl_standard = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"]
    all_nfl_teams = sorted(games_season['home_team'].unique().tolist()) if not games_season.empty else nfl_standard
    available_teams = sorted(list(set(all_nfl_teams) - picked_teams))
    
    # 5. Analytics & Predictions (STATIC for Draft)
    # get_season_projection() resolves model output vs. analyst consensus per
    # team; its {"wins", "source_type", "detail"} shape is adapted back to the
    # legacy {"projected_wins", "mean_wins", "std_dev", "sources"} shape here
    # because this dict is sent verbatim to the frontend (static/js/main.js,
    # ui_renderer.js) and Jinja templates, which read `.projected_wins` /
    # `.std_dev` directly — those aren't touched by this change.
    season_projection = get_season_projection(int(season))
    preseason_predictions = {}
    for team, proj in season_projection.items():
        detail = proj.get("detail") or {}
        wins = proj.get("wins")
        if proj.get("source_type") == "model":
            preseason_predictions[team] = {
                "projected_wins": wins,
                "mean_wins": detail.get("mean_wins", wins),
                "std_dev": detail.get("std_dev", 0),
                "sources": detail.get("sources", {}),
            }
        else:
            preseason_predictions[team] = {
                "projected_wins": wins,
                "mean_wins": detail.get("consensus_mean", wins),
                "std_dev": detail.get("consensus_std", 0),
                "sources": detail.get("sources", {}),
            }
    team_schedules = {t: get_team_schedule(t, games_season, int(season)) for t in all_nfl_teams}
    
    # 6. Player Info Metadata
    all_players_info = []
    valid_players = players_df.dropna(subset=['playerId'])
    for _, row in valid_players.iterrows():
        pid = int(row['playerId'])
        phone = str(row.get('cell', ''))
        has_phone = pd.notna(row.get('cell')) and phone.strip() != 'nan' and phone.strip() != ''
        masked_phone = f"***-***-{phone[-4:]}" if has_phone and len(phone) >= 4 else "Unknown"
        email = str(row.get('email', ''))
        has_email = pd.notna(row.get('email')) and email.strip() != 'nan' and '@' in email
        masked_email = f"{email[0]}***@{email.split('@')[-1]}" if has_email else "Unknown"
        
        all_players_info.append({
            "playerId": pid, "playerName": row.get('nickName') or row.get('fullName', f"P{pid}"),
            "connected": pid in connected_players, "role": row.get('role', 'user'),
            "has_phone": has_phone, "phone": masked_phone, "has_email": has_email, "email": masked_email,
            "has_password": 'password_hash' in row and pd.notna(row['password_hash']) and str(row['password_hash']).strip() != ''
        })
    
    available_seasons = sorted([int(s) for s in set(d_order['season'].dropna())], reverse=True)

    # 7. Timer Sync
    from services.db_service import get_metadata, save_metadata
    pick_start_time = int(time.time())
    if season and active_pick <= 30:
        meta_id = f"draft_timer_{season}"
        meta = get_metadata(meta_id) or {"picks": {}}
        picks = meta.get("picks", {})
        pick_key = str(active_pick)
        if pick_key in picks:
            pick_start_time = int(picks[pick_key])
        else:
            pick_start_time = int(time.time())
            picks[pick_key] = pick_start_time
            save_metadata(meta_id, {"picks": picks})

    state = {
        "season": int(season), "available_seasons": available_seasons,
        "draft_board": draft_board, "active_pick": active_pick,
        "available_teams": available_teams, "draft_ready": True,
        "connected_players": list(connected_players), "all_players": all_players_info,
        "pick_start_time": pick_start_time, "preseason_predictions": preseason_predictions,
        "team_schedules": team_schedules,
    }
    
    state["connected_count"] = len(connected_players) if connected_players else 0

    import copy
    state = sanitize_state(state)
    _CACHED_DRAFT_STATE = copy.deepcopy(state)
    
    if is_debug:
        logger.debug("load_draft_state total took %.3fs", time.time() - start_time)
    return state

def save_pick(season: int, draft_pick: int, player_id: int, team: str, executed_by: str = None):
    """Appends a new draft pick to the results."""
    global _CACHED_DRAFT_STATE
    time_taken = None
    if _CACHED_DRAFT_STATE and "pick_start_time" in _CACHED_DRAFT_STATE:
        time_taken = time.time() - _CACHED_DRAFT_STATE["pick_start_time"]
    add_draft_result(season, draft_pick, player_id, team, executed_by, time_taken)
    _CACHED_DRAFT_STATE = None

def undo_pick(season: int, draft_pick: int):
    """Deletes the most recent draft pick."""
    global _CACHED_DRAFT_STATE
    delete_draft_pick(season, draft_pick)
    _CACHED_DRAFT_STATE = None

def reset_pick(season: int, draft_pick: int):
    """
    Undoes a pick if it exists, and resets the timer for that pick slot.
    """
    global _CACHED_DRAFT_STATE
    # 1. Delete result if it exists
    delete_draft_pick(season, draft_pick)
    
    # 2. Reset timer for this specific pick in metadata
    from services.db_service import get_metadata, save_metadata
    meta_id = f"draft_timer_{season}"
    meta = get_metadata(meta_id) or {"picks": {}}
    picks = meta.get("picks", {})
    
    # Set to current time
    picks[str(draft_pick)] = int(time.time())
    save_metadata(meta_id, {"picks": picks})
    
    # 3. Clear cache to force reload
    _CACHED_DRAFT_STATE = None

def update_player_phone(player_id: int, phone: str):
    """Updates a player's phone number."""
    global _CACHED_DRAFT_STATE
    update_player_cell(player_id, phone)
    _CACHED_DRAFT_STATE = None

def wipe_draft_cache():
    """Manually evicts the Draft Singleton (used by Admin sandbox reset)."""
    global _CACHED_DRAFT_STATE
    _CACHED_DRAFT_STATE = None
