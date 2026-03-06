import os
import pathlib

# Default to local data cache for the web server (avoids Firestore reads on every page load).
# Override by setting USE_LOCAL_DATA=False before running (e.g. from cache_builder.py).
if 'USE_LOCAL_DATA' not in os.environ:
    os.environ['USE_LOCAL_DATA'] = 'True'

import pandas as pd
import numpy as np
import time
from typing import Tuple, Dict, Any, List
from services.db_service import get_collection_df

# Constants
NFL_DATA_GITHUB = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"

# In-memory cache keyed by year (or 'all' for unfiltered load)
_DATA_CACHE: dict = {}
_CACHE_TIMESTAMPS: dict = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes

def _cache_key(year):
    return str(year) if year is not None else 'all'

def load_data(year: int = None):
    """
    Loads data for the given season year.
    - nfl_games and nfl_standings are filtered to `year` if provided.
    - Players/draft collections are loaded in full (no season filter) and deduplicated.
    - Results are cached to .local_db/<collection>_<year>.pkl for year-specific loads.
    """
    global _DATA_CACHE, _CACHE_TIMESTAMPS

    key = _cache_key(year)
    current_time = time.time()
    if key in _DATA_CACHE and (current_time - _CACHE_TIMESTAMPS.get(key, 0) < _CACHE_TTL_SECONDS):
        return _DATA_CACHE[key]

    use_local = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'
    local_dir = pathlib.Path('.local_db')

    if use_local and not local_dir.exists():
        local_dir.mkdir(parents=True, exist_ok=True)

    def fetch_or_load(collection_name, filters=None, pkl_suffix=''):
        pkl_name = f"{collection_name}{pkl_suffix}.pkl"
        pkl_path = local_dir / pkl_name
        if use_local and pkl_path.exists():
            try:
                return pd.read_pickle(pkl_path)
            except Exception:
                pass  # Fallback if pickle is corrupted

        if use_local and pkl_suffix:
            # Year-specific pkl missing — fall back to filtering the base (unfiltered) pkl.
            # This prevents the web server from calling Firestore when a year hasn't been cached yet.
            base_pkl = local_dir / f"{collection_name}.pkl"
            if base_pkl.exists():
                try:
                    base_df = pd.read_pickle(base_pkl)
                    if filters and not base_df.empty:
                        for f in filters:
                            col, op, val = f
                            if col in base_df.columns:
                                if op == '==':
                                    base_df = base_df[base_df[col] == val]
                    # Cache the year slice so the next request is fast
                    if not base_df.empty:
                        base_df.to_pickle(pkl_path)
                    return base_df
                except Exception:
                    pass

        if use_local:
            # In local mode never call Firestore — return empty if no pkl found
            return pd.DataFrame()

        df = get_collection_df(collection_name, filters)
        if use_local and not df.empty:
            df.to_pickle(pkl_path)
        return df


    # Build season filter for NFL game data
    season_filter = [('season', '==', year)] if year is not None else None
    yr_suffix = f'_{year}' if year is not None else ''

    # NFL game data — filtered by year for web routes, unfiltered for cache_builder
    standings = fetch_or_load('nfl_standings', season_filter, yr_suffix)
    teams     = fetch_or_load('nfl_teams', None, '')      # No season filter — teams rarely change
    games     = fetch_or_load('nfl_games', season_filter, yr_suffix)


    # User-managed data — lives in Firestore (seeded via scripts/upload_configfiles.py)
    # These span all seasons, so no year filter needed
    players           = fetch_or_load('players')           # shared across seasons
    draft_order       = fetch_or_load('draft_order')
    draft_results     = fetch_or_load('draft_results')
    draft_order_rules = fetch_or_load('draft_order_rules')

    # Standardize column casing for downstream joins (Removed per user request)

    # Ensure numeric columns are correctly typed
    for df in [standings, teams, games, players, draft_order, draft_results, draft_order_rules]:
        if not df.empty and 'season' in df.columns:
            df['season'] = pd.to_numeric(df['season'], errors='coerce')
        if not df.empty and 'week' in df.columns:
            df['week'] = pd.to_numeric(df['week'], errors='coerce')
        if not df.empty and 'playerId' in df.columns:
            df['playerId'] = pd.to_numeric(df['playerId'], errors='coerce')

    # Deduplicate players — old Firestore auto-ID records can cause duplicates
    if not players.empty and 'playerId' in players.columns:
        players = players.dropna(subset=['playerId'])
        players = players.sort_values('playerId').drop_duplicates(subset=['playerId'], keep='last').reset_index(drop=True)

    res = (standings, teams, games, players, draft_order, draft_results, draft_order_rules)
    _DATA_CACHE[key] = res
    _CACHE_TIMESTAMPS[key] = current_time
    return res

def get_active_season(games: pd.DataFrame, draft_results: pd.DataFrame = None) -> int:
    """
    Returns the latest season that has completed game results.
    If draft_results is provided, only considers seasons that also have draft picks.
    This prevents future/post-season data (e.g. 2025 playoffs) from overriding a
    season where draft data hasn't been loaded yet.
    """
    if games.empty or 'season' not in games.columns:
        return 2024
    has_results = games[games['result'].notna() & (games['result'] != -1000)]
    if has_results.empty:
        return 2024
    active = int(has_results['season'].max())

    # If draft_results provided, cap to the latest season that has draft picks
    if draft_results is not None and not draft_results.empty and 'season' in draft_results.columns:
        draft_seasons = set(draft_results['season'].dropna().astype(int).unique())
        # Walk back from active until we find a year with draft picks
        while active > 2013 and active not in draft_seasons:
            active -= 1

    return active

def get_available_years(draft_results: pd.DataFrame, games: pd.DataFrame = None) -> list:
    """Returns seasons with draft data, capped at the active (game-result) season.
    Use this for standings/schedule/race pages."""
    if draft_results.empty or 'season' not in draft_results.columns:
        return [2024]
    years = sorted(draft_results['season'].dropna().astype(int).unique().tolist())
    if games is not None and not games.empty:
        active = get_active_season(games)
        years = [y for y in years if y <= active]
    return years

def get_draft_years(draft_results: pd.DataFrame) -> list:
    """Returns ALL seasons with draft data, including future seasons (e.g. 2025 pre-draft).
    Use this for draft-specific pages (draft results, draft history)."""
    if draft_results.empty or 'season' not in draft_results.columns:
        return [2024]
    return sorted(draft_results['season'].dropna().astype(int).unique().tolist())

def get_latest_week_for_year(games: pd.DataFrame, year: int) -> int:
    """
    Returns the 'current' week for the schedule view:
    - Finds the highest week where at least one game is complete but NOT all games are done
      (i.e., the week is still in progress / just finished but within the season).
    - Falls back to the highest week with any completed game if all weeks are fully done.
    - Returns 1 if no completed games exist.
    """
    if games.empty or 'week' not in games.columns:
        return 1
    reg_games = games[(games['season'] == year) & (games.get('game_type', 'REG') == 'REG')] if 'game_type' in games.columns else games[games['season'] == year]
    if reg_games.empty:
        return 1

    # Group by week: count total games and completed games
    def completed(r):
        return r.notna() & (r != -1000)

    week_stats = (
        reg_games.groupby('week')
        .apply(lambda g: pd.Series({
            'total': len(g),
            'done': completed(g['result']).sum()
        }))
        .reset_index()
    )
    week_stats = week_stats[week_stats['done'] > 0]  # Only weeks with at least one completed game
    if week_stats.empty:
        return 1

    # Find highest week where done < total (in-progress / live week)
    in_progress = week_stats[week_stats['done'] < week_stats['total']]
    if not in_progress.empty:
        return int(in_progress['week'].max())

    # All weeks are fully complete — return highest completed week
    return int(week_stats['week'].max())

def process_games_data(games: pd.DataFrame) -> pd.DataFrame:
    """Processes raw games data to compute cumulative wins for each team per week."""
    games = games.copy()
    conditions = [
        games['result'] > 0,
        games['result'] < 0
    ]
    choices = [games['home_team'], games['away_team']]
    games['winner'] = np.select(conditions, choices, default=np.nan)
    
    games['rec'] = 1
    games.sort_values(['season', 'week'], inplace=True)
    games['TotalWinsBySeason'] = games.groupby(['season', 'winner', 'game_type'])['rec'].cumsum()
    games.drop('rec', axis=1, inplace=True)
    
    games.rename(columns={'winner': 'team'}, inplace=True)
    return games

def get_latest_season_and_week(games: pd.DataFrame) -> Tuple[int, int]:
    """Determines the latest regular season week available in the data."""
    if games.empty:
        return 2024, 1
        
    if 'game_type' in games.columns:
        reg_games = games[games['game_type'] == 'REG']
        if reg_games.empty: # Fallback
            reg_games = games
    else:
        reg_games = games
        
    latest_season = reg_games['season'].max()
    latest_week = reg_games[reg_games['season'] == latest_season]['week'].max()
    
    # Handle NaN cases
    if pd.isna(latest_season):
        latest_season = 2024
    if pd.isna(latest_week):
        latest_week = 1
        
    return int(latest_season), int(latest_week)

def get_season_progress(season: int, week: int) -> Dict[str, Any]:
    """
    Computes player and team wins for a specific season up to a specific week.
    Returns a dictionary suitable for JSON serialization.
    """
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()
    games = process_games_data(games)
    
    today_teams = teams[teams['season'] == season].copy()
    today_standings = standings[standings['season'] == season].copy()
    today_draft_results = draft_results[draft_results['season'] == season].copy()
    
    # Filter games up to the requested week for this season, enforcing Regular Season only
    if 'game_type' in games.columns:
        today_games = games[(games['season'] == season) & (games['week'] <= week) & (games['game_type'] == 'REG')].copy()
    else:
        today_games = games[(games['season'] == season) & (games['week'] <= week)].copy()
    
    # Join games with players and calculate player wins by season
    games_player_added = pd.merge(today_games, today_draft_results, on=['team', 'season'], how='inner')
    games_player_added['rec'] = 1
    games_player_added.sort_values(['season', 'week'], inplace=True)
    games_player_added['TotalPlayerWinsBySeason'] = games_player_added.groupby('playerId')['rec'].cumsum()
    games_player_added = pd.merge(games_player_added, players, on='playerId', how='inner')
    
    # Wins by week for players
    wins_by_week_player = games_player_added.groupby(['season', 'week', 'nickName'])['TotalPlayerWinsBySeason'].max().reset_index()
    wins_by_week_player = wins_by_week_player.pivot_table(index=['season', 'week'], columns='nickName', values='TotalPlayerWinsBySeason').ffill().fillna(0).reset_index()
    
    # Build a nickName → playerId lookup map (stable numeric key)
    nick_to_pid = (
        games_player_added[['nickName', 'playerId']]
        .drop_duplicates('nickName')
        .set_index('nickName')['playerId']
        .to_dict()
    )

    # Build JSON structure for Chart.js
    player_data = {
        "labels": wins_by_week_player["week"].tolist(),
        "datasets": []
    }
    for player in [col for col in wins_by_week_player.columns if col not in ['season', 'week']]:
        player_data["datasets"].append({
            "label": player,
            "playerId": int(nick_to_pid.get(player, -1)),
            "data": wins_by_week_player[player].tolist()
        })
        
    # Team wins by week
    teams_with_wins = today_games.dropna(subset=['team'])
    team_data = {
        "labels": sorted(teams_with_wins["week"].unique().tolist()),
        "datasets": []
    }
    for team in teams_with_wins['team'].unique():
        t_data = teams_with_wins[teams_with_wins['team'] == team].sort_values('week')
        # Fill missing weeks with previous max wins for smooth lines
        merged = pd.DataFrame({"week": team_data["labels"]})
        merged = pd.merge(merged, t_data[['week', 'TotalWinsBySeason']], on='week', how='left')
        merged['TotalWinsBySeason'] = merged['TotalWinsBySeason'].ffill().fillna(0)
        
        team_data["datasets"].append({
            "label": str(team),
            "data": merged["TotalWinsBySeason"].tolist()
        })
        
    # Current Standings & Best Picks
    wins_pool_standings = pd.merge(today_standings, today_draft_results, on=['team', 'season'])
    
    if not wins_pool_standings.empty and 'scored' in wins_pool_standings.columns and 'allowed' in wins_pool_standings.columns:
        wins_pool_standings['ptDiff'] = wins_pool_standings['scored'] - wins_pool_standings['allowed']
    else:
        wins_pool_standings['ptDiff'] = 0
        
    if not wins_pool_standings.empty and 'wins' in wins_pool_standings.columns:
        wins_pool_standings['my_ranks'] = wins_pool_standings.groupby(['season', 'playerId'])['wins'].rank(ascending=False)
    else:
        wins_pool_standings['my_ranks'] = 1
        wins_pool_standings['wins'] = 0
        
    wins_pool_standings = pd.merge(wins_pool_standings, players, on='playerId', how='inner')
    
    # Replace any potential NaNs in the standings frame
    wins_pool_standings = wins_pool_standings.replace({np.nan: None})
    
    # Calculate best picks (tiebreaker: later draft pick wins)
    if 'wins' in wins_pool_standings.columns and 'ptDiff' in wins_pool_standings.columns and 'draftPick' in wins_pool_standings.columns:
        picks_ranked = wins_pool_standings.sort_values(by=['wins', 'draftPick', 'ptDiff'], ascending=[False, False, False])
    elif 'wins' in wins_pool_standings.columns and 'ptDiff' in wins_pool_standings.columns:
        picks_ranked = wins_pool_standings.sort_values(by=['wins', 'ptDiff'], ascending=[False, False])
    else:
        picks_ranked = wins_pool_standings.copy()
        
    best_overall_team = picks_ranked.iloc[0]['team'] if not picks_ranked.empty else None
    
    # Best pick per round
    total_players = len(players)
    if not picks_ranked.empty and 'draftPick' in picks_ranked.columns:
        picks_ranked['round'] = np.ceil(picks_ranked['draftPick'].astype(float) / total_players)
        ranks_valid = picks_ranked[picks_ranked['wins'].notnull()] if 'wins' in picks_ranked.columns else pd.DataFrame()
        
        if not ranks_valid.empty:
            # For each round, find the rows with max wins.
            # To apply tiebreaker we can just take the first from picks_ranked which is already sorted
            best_by_round_teams = {}
            for r, grp in picks_ranked.groupby('round'):
                best_by_round_teams[r] = grp.iloc[0]['team'] if not grp.empty else None
            best_by_round_teams = {str(int(k)): (v if pd.notnull(v) else None) for k, v in best_by_round_teams.items() if pd.notnull(k)}
        else:
            best_by_round_teams = {}
    else:
        best_by_round_teams = {}
    
    return {
        "season": season,
        "week": week,
        "player_chart": player_data,
        "team_chart": team_data,
        "best_overall": best_overall_team,
        "best_by_round": best_by_round_teams,
        "standings": wins_pool_standings.to_dict(orient="records")
    }

if __name__ == "__main__":
    # Quick test
    import json
    st, tm, gm, pl, do, dr, drr = load_data()
    s, w = get_latest_season_and_week(gm)
    print(f"Latest: Season {s} Week {w}")
    res = get_season_progress(s, w)
    print(json.dumps(res)[:500])
