"""services/live_score_service.py -- Fetches live NFL game scores from the ESPN scoreboard API."""
import logging
import requests
import pandas as pd
from datetime import datetime
from services.utils import normalize_team_abbr

logger = logging.getLogger(__name__)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

# ESPN's status.type.name values that mean "game is underway, show the LIVE
# badge" -- STATUS_IN_PROGRESS alone misses halftime, which ESPN reports as
# its own distinct status rather than a flavor of in-progress.
LIVE_STATUSES = {"STATUS_IN_PROGRESS", "STATUS_HALFTIME"}


def is_live_status(status: str) -> bool:
    return status in LIVE_STATUSES

def fetch_espn_scores():
    """
    Fetches the current week's scores from ESPN.
    """
    try:
        resp = requests.get(ESPN_URL, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception as e:
        logger.error("Error fetching ESPN scores: %s", e)
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
        possession = None
        # comp['situation'] only exists while a game is live and ESPN has
        # attributed the current play to a team -- absent between plays
        # (e.g. right after a score), not just for non-live games.
        possession_team_id = comp.get('situation', {}).get('possession')

        for team_data in comp.get('competitors', []):
            abbr = team_data.get('team', {}).get('abbreviation')
            score = int(team_data.get('score', 0))
            home_away = team_data.get('homeAway')
            if home_away == 'home':
                home_team = abbr
                home_score = score
            else:
                away_team = abbr
                away_score = score
            if possession_team_id and team_data.get('team', {}).get('id') == possession_team_id:
                possession = home_away

        if home_team and away_team:
            updates[(home_team, away_team)] = {
                "home_score": home_score,
                "away_score": away_score,
                "status": status,
                "clock": event.get('status', {}).get('displayClock', ''),
                "period": event.get('status', {}).get('period', 0),
                "possession": possession
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

    # ESPN returns raw abbreviations (LAR/WSH/JAC) that differ from
    # nflverse's (LA/WAS/JAX) -- normalize the ESPN side before matching
    # against nflverse-normalized home_team/away_team keys, or Rams/
    # Commanders/Jaguars games silently never match (see
    # services/utils.py::normalize_team_abbr and the same pattern in
    # scripts/sync_live_scores.py::run_espn_overlay_safely()). The repo
    # side (row['home_team']/row['away_team']) is already canonical and
    # needs no normalization.
    normalized_live_data = {
        (normalize_team_abbr(h), normalize_team_abbr(a)): v
        for (h, a), v in live_data.items()
    }

    df = games_df.copy()
    cache_needs_rebuild = False

    for idx, row in df.iterrows():
        match_key = (row['home_team'], row['away_team'])

        if match_key in normalized_live_data:
            update = normalized_live_data[match_key]
            
            # If the game is currently live or just finished on ESPN
            # we prefer ESPN's score over the static CSV
            espn_status = update['status']
            
            # Update scores
            df.at[idx, 'home_score'] = update['home_score']
            df.at[idx, 'away_score'] = update['away_score']
            
            # result = home_score - away_score
            df.at[idx, 'result'] = update['home_score'] - update['away_score']
            
            # Add metadata for the UI to show 'LIVE'
            if is_live_status(espn_status):
                df.at[idx, 'is_live'] = True
                df.at[idx, 'clock'] = 'Halftime' if espn_status == 'STATUS_HALFTIME' else update['clock']
                df.at[idx, 'period'] = update['period']
            elif espn_status == 'STATUS_FINAL':
                # Check if it was previously NOT final in our local DF logic
                # (is_live usually tracks if the game has started but not ended)
                if df.at[idx, 'is_live'] == True or pd.isna(df.at[idx, 'is_live']):
                    cache_needs_rebuild = True
                    
                df.at[idx, 'is_live'] = False
                df.at[idx, 'is_final_live'] = True # Marked as final via live sync

    if cache_needs_rebuild:
        from services.db_service import signal_data_update
        signal_data_update()
        
    return df
