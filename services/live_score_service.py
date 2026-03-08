import requests
import pandas as pd
from datetime import datetime
from services.utils import normalize_team_abbr

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

def fetch_espn_scores():
    """
    Fetches the current week's scores from ESPN.
    """
    try:
        resp = requests.get(ESPN_URL, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"Error fetching ESPN scores: {e}")
    return None

def get_live_updates():
    """
    Parses ESPN JSON into a simplified dict: {(home, away): {home_score, away_score, status}}
    """
    data = fetch_espn_scores()
    if not data:
        return {}
    
    updates = {}
    for event in data.get('events', []):
        comp = event.get('competitions', [{}])[0]
        status = event.get('status', {}).get('type', {}).get('name', '')
        
        home_team = ""
        away_team = ""
        home_score = 0
        away_score = 0
        
        for team_data in comp.get('competitors', []):
            abbr = team_data.get('team', {}).get('abbreviation')
            score = int(team_data.get('score', 0))
            if team_data.get('homeAway') == 'home':
                home_team = abbr
                home_score = score
            else:
                away_team = abbr
                away_score = score
        
        if home_team and away_team:
            updates[(home_team, away_team)] = {
                "home_score": home_score,
                "away_score": away_score,
                "status": status,
                "clock": event.get('status', {}).get('displayClock', ''),
                "period": event.get('status', {}).get('period', 0)
            }
            
    return updates

def sync_live_scores_to_df(games_df: pd.DataFrame) -> pd.DataFrame:
    """
    Overlays live ESPN data onto the provided games DataFrame.
    Only updates games that are NOT yet final in the source DF, 
    or games that ESPN says are currently active.
    """
    if games_df.empty:
        return games_df
        
    live_data = get_live_updates()
    if not live_data:
        return games_df
        
    df = games_df.copy()
    
    # We need to map ESPN abbreviations to repo abbreviations if they differ.
    # Usually they match (KC, SF, etc.), but sometimes (LAR vs LA, etc.)
    # For now assume mostly matching or add a small map.
    repo_to_espn = {
        "LA": "LAR",
        "ARI": "ARI", # ... and so on
    }
    
    for idx, row in df.iterrows():
        # Only check games for the current/active week (or all for simplicity in V1)
        # Try to find match
        
        # Normalize repo teams to check against ESPN (usually they match, but be safe)
        home_norm = normalize_team_abbr(row['home_team'])
        away_norm = normalize_team_abbr(row['away_team'])
        match_key = (home_norm, away_norm)
            
        if match_key in live_data:
            update = live_data[match_key]
            
            # If the game is currently live or just finished on ESPN
            # we prefer ESPN's score over the static CSV
            espn_status = update['status']
            
            # Update scores
            df.at[idx, 'home_score'] = update['home_score']
            df.at[idx, 'away_score'] = update['away_score']
            
            # result = home_score - away_score
            df.at[idx, 'result'] = update['home_score'] - update['away_score']
            
            # Add metadata for the UI to show 'LIVE'
            if espn_status == 'STATUS_IN_PROGRESS':
                df.at[idx, 'is_live'] = True
                df.at[idx, 'clock'] = update['clock']
                df.at[idx, 'period'] = update['period']
            elif espn_status == 'STATUS_FINAL':
                df.at[idx, 'is_live'] = False
                df.at[idx, 'is_final_live'] = True # Marked as final via live sync
                
    return df
