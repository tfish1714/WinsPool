import os
import traceback
import pandas as pd
from typing import Dict, Any, List
from services.db_service import get_collection_df, add_draft_result, update_player_cell

NFL_DATA_GITHUB = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"

def load_draft_state(connected_players: set) -> Dict[str, Any]:
    """
    Loads all players, draft order, rules, and existing results to construct the current draft board state.
    """
    # Load players
    players_df = get_collection_df('players')
    total_players = len(players_df)
    
    # Check if draft is ready to start
    draft_ready = len(connected_players) >= total_players
    # Load order and rules
    d_order = get_collection_df('draft_order')
    rules = get_collection_df('draft_order_rules')
    
    # Load results
    results = get_collection_df('draft_results')
    
    # Determine the latest season (or hardcode to 2024 for testing)
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
            pick_num = int(row['draftPick'])
            pid = int(row['playerId'])
        except (ValueError, TypeError):
            continue  # skip any leftover NaN rows
        pname = row['nickName']
        team = row['team'] if pd.notna(row['team']) else None
        
        if team:
            picked_teams.add(team)
            
        draft_board.append({
            "pick": pick_num,
            "playerId": pid,
            "playerName": pname,
            "team": team
        })
        
        if team is None and active_pick is None:
            active_pick = pick_num
            
    # If all picks are taken
    if active_pick is None:
        active_pick = len(melted) + 1 # Draft is over
        
    # Fetch real team list for that season
    url = f"{NFL_DATA_GITHUB}teams.csv"
    try:
        teams_df = pd.read_csv(url)
        all_nfl_teams = sorted(teams_df[teams_df['season'] == season]['team'].tolist())
    except Exception:
        # Fallback if request fails
        all_nfl_teams = ["ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS"]

    # List of available teams
    available_teams = sorted(list(set(all_nfl_teams) - picked_teams))
    
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

        all_players_info.append({
            "playerId": pid,
            "playerName": row.get('nickName', ''),
            "connected": pid in connected_players,
            "has_phone": has_phone,
            "phone": masked_phone,
            "has_email": has_email,
            "email": masked_email
        })
    
    state = {
        "season": int(season),
        "draft_board": draft_board,
        "active_pick": active_pick,
        "available_teams": available_teams,
        "draft_ready": draft_ready,
        "connected_players": list(connected_players),
        "all_players": all_players_info
    }
    
    return state

def save_pick(season: int, draft_pick: int, player_id: int, team: str):
    """Appends a new draft pick to the results."""
    add_draft_result(season, draft_pick, player_id, team)

def update_player_phone(player_id: int, phone: str):
    """Updates a player's phone number."""
    update_player_cell(player_id, phone)
