import os
import traceback
import pandas as pd
from typing import Dict, Any, List
from services.db_service import get_collection_df, add_draft_result, update_player_cell, delete_draft_pick
from services.data_service import get_preseason_predictions, get_team_schedule, load_data
import time

NFL_DATA_GITHUB = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"

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
    Loads all players, draft order, rules, and existing results to construct the current draft board state.
    """
    global _CACHED_DRAFT_STATE
    import copy
    
    # If a specific year is requested, bypass cache
    if year is None and _CACHED_DRAFT_STATE is not None:
        state = copy.deepcopy(_CACHED_DRAFT_STATE)
        state["draft_ready"] = True # No longer requires all players connected
        state["connected_players"] = list(connected_players)
        for p in state["all_players"]:
            p["connected"] = p["playerId"] in connected_players
        return state

    # Load players
    players_df = get_collection_df('players')
    valid_players = players_df.dropna(subset=['playerId'])
    total_players = len(valid_players)
    
    # Draft is always ready if order exists
    draft_ready = True
    # Load order and rules
    d_order = get_collection_df('draft_order')
    rules = get_collection_df('draft_order_rules')
    
    # Load results
    results = get_collection_df('draft_results')
    
    # Determine the season
    if year:
        season = int(year)
    else:
        season_val = d_order['season'].max()
        if pd.isna(season_val):
            # No draft order exists yet — return a safe empty state
            return {
                "season": None,
                "draft_board": [],
                "active_pick": 1,
                "available_teams": [],
                "draft_ready": False,
                "connected_players": list(connected_players),
                "all_players": [],
            }
        season = int(season_val)
    
    d_order_season = d_order[d_order['season'] == season]
    rules_season = rules[rules['season'] == season]
    results_season = results[results['season'] == season]
    
    # Join order and rules
    merged_order = pd.merge(d_order_season, rules_season, on=['season', 'draftOrder'])
    melted = pd.melt(merged_order, id_vars=['playerId'], value_vars=['pickOne', 'pickTwo', 'pickThree'], value_name='draftPick')
    # Drop rows where draftPick is NaN (player has fewer than 3 picks scheduled)
    melted = melted.dropna(subset=['draftPick'])
    melted['draftPick'] = melted['draftPick'].astype(int)
    melted = melted.sort_values('draftPick').reset_index(drop=True)
    
    # Join with player names
    melted = pd.merge(melted, players_df, on='playerId', how='left')
    
    # Join with existing results
    melted = pd.merge(melted, results_season[['draftPick', 'team']], on='draftPick', how='left')
    
    # Build list of dicts for the board
    draft_board = []
    active_pick = None
    picked_teams = set()
    
    for _, row in melted.iterrows():
        try:
            pid = int(row['playerId'])
        except (ValueError, TypeError):
            continue  # skip any leftover NaN rows
        
        # Fallback logic for nickname
        pname = row.get('nickName')
        if pd.isna(pname) or not str(pname).strip():
            pname = row.get('fullName', f"Player {pid}")
            
        team = row['team'] if pd.notna(row['team']) else None
        
        if team:
            picked_teams.add(team)
            
        draft_board.append({
            "pick": int(row['draftPick']),
            "playerId": pid,
            "playerName": pname,
            "team": team
        })
        
        if team is None and active_pick is None:
            active_pick = int(row['draftPick'])
            
    # If all picks are taken
    if active_pick is None:
        active_pick = len(melted) + 1 # Draft is over
        
    # Fetch real team list for that season
    nfl_standard_teams = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"]
    all_nfl_teams = []
    url = f"{NFL_DATA_GITHUB}teams.csv"
    try:
        teams_df = pd.read_csv(url)
        all_nfl_teams = sorted(teams_df[teams_df['season'] == season]['team'].tolist())
    except Exception:
        pass
    
    # CRITICAL FALLBACK: If GitHub is empty for this season, use standard list
    if not all_nfl_teams:
        all_nfl_teams = sorted(nfl_standard_teams)

    # List of available teams
    available_teams = sorted(list(set(all_nfl_teams) - picked_teams))
    
    # Analytics Projections & 17-Game Opponent Trees
    preseason_predictions = get_preseason_predictions(int(season))
    _, _, games_df, _, _, _, _ = load_data(year=int(season))
    team_schedules = {t: get_team_schedule(t, games_df, int(season)) for t in all_nfl_teams}
    
    # List of all players with connection status (skip rows with NaN playerId)
    all_players_info = []
    valid_players = players_df.dropna(subset=['playerId'])
    for _, row in valid_players.iterrows():
        try:
            pid = int(row['playerId'])
        except (ValueError, TypeError):
            continue

        phone = str(row.get('cell', ''))
        is_nan = pd.isna(row.get('cell')) or phone.strip() == 'nan' or phone.strip() == ''
        has_phone = not is_nan
        masked_phone = f"***-***-{phone[-4:]}" if has_phone and len(phone) >= 4 else "Unknown"

        email = str(row.get('email', ''))
        is_email_nan = pd.isna(row.get('email')) or email.strip() == 'nan' or email.strip() == ''
        has_email = not is_email_nan
        masked_email = f"{email[0]}***@{email.split('@')[-1]}" if has_email and '@' in email else "Unknown"
        
        has_password = 'password_hash' in row and not pd.isna(row['password_hash']) and str(row['password_hash']).strip() != ''

        # Robust name mapping
        pname = row.get('nickName')
        if pd.isna(pname) or not str(pname).strip():
            pname = row.get('fullName', f"Player {pid}")

        all_players_info.append({
            "playerId": pid,
            "playerName": pname,
            "connected": pid in connected_players,
            "role": row.get('role', 'user'),
            "has_phone": has_phone,
            "phone": masked_phone,
            "has_email": has_email,
            "email": masked_email,
            "has_password": has_password
        })
    
    # Seasons with any draft data
    seasons = set()
    if not d_order.empty and 'season' in d_order.columns:
        seasons.update(d_order['season'].dropna().unique().tolist())
    if not results.empty and 'season' in results.columns:
        seasons.update(results['season'].dropna().unique().tolist())
    available_seasons = sorted([int(s) for s in seasons], reverse=True)

    # Persistent Pick Start Time Map (survives restarts and supports undo)
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
            # First time seeing this pick
            pick_start_time = int(time.time())
            picks[pick_key] = pick_start_time
            save_metadata(meta_id, {"picks": picks})

    state = {
        "season": int(season),
        "available_seasons": available_seasons,
        "draft_board": draft_board,
        "active_pick": active_pick,
        "available_teams": available_teams,
        "draft_ready": draft_ready,
        "connected_players": list(connected_players),
        "all_players": all_players_info,
        "pick_start_time": pick_start_time,
        "preseason_predictions": preseason_predictions,
        "team_schedules": team_schedules,
    }
    
    import copy
    state = sanitize_state(state)
    _CACHED_DRAFT_STATE = copy.deepcopy(state)
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
