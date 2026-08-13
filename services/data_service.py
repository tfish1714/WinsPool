import logging
import os
import pathlib
import pandas as pd

logger = logging.getLogger(__name__)
import numpy as np
import time
from typing import Tuple, Dict, Any, List, NamedTuple
from services.db_service import get_collection_df
from services.utils import get_team_logo_url
from services.constants import UNDRAFTED_SENTINEL


class DataBundle(NamedTuple):
    """Named 7-tuple returned by load_data(). All fields are pandas DataFrames."""
    standings:          "pd.DataFrame"
    teams:              "pd.DataFrame"
    games:              "pd.DataFrame"
    players:            "pd.DataFrame"
    draft_order:        "pd.DataFrame"
    draft_results:      "pd.DataFrame"
    draft_order_rules:  "pd.DataFrame"


def get_team_logo(team_code: str) -> str:
    """Returns the official high-resolution logo URL for an NFL team."""
    return get_team_logo_url(team_code)

from services.cache_service import (
    _DATA_CACHE, _CACHE_TIMESTAMPS, _CACHE_TTL_SECONDS,
    _LAST_REMOTE_CHECK, _REMOTE_CHECK_INTERVAL,
    clear_data_cache, _cache_key
)

def load_data(year: int = None):
    """
    Loads data for the given season year with smart caching.
    
    Caching Hierarchy:
    1. Memory Cache (_DATA_CACHE): Instant return if within TTL.
    2. Local Pickle Cache (.local_db): Returns from disk if memory cache is cold.
    3. Firestore (get_collection_df): Remote fetch if all else fails.
    """
    start_total = time.time()
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    use_local = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'
    
    if is_debug:
        logger.debug("load_data(year=%s) called. USE_LOCAL_DATA=%s", year, use_local)

    # 1. Check for remote invalidation signals if it's been a while
    current_time = time.time()
    
    # We need to access and potentially update the module-level globals in cache_service
    import services.cache_service as cs
    
    # Only check remote cache control if we are NOT using local data.
    # Checking remote Firestore when using local data forces SDK initialization, causing a 12s hang.
    if not use_local and (current_time - cs._LAST_REMOTE_CHECK) > cs._REMOTE_CHECK_INTERVAL:
        cs._LAST_REMOTE_CHECK = current_time
        if is_debug:
            logger.debug("Triggering remote Firestore cache_control check...")
        try:
            # We use a raw Firestore fetch here to avoid circular dependencies
            from services.db_service import get_db
            db = get_db()
            if db:
                # Add a timeout to prevent hanging the whole request if Firestore is slow
                ctrl = db.collection("metadata").document("cache_control").get(timeout=5)
                if ctrl.exists:
                    remote_ts = ctrl.to_dict().get("last_update", 0)
                    # If remote signal is newer than our local cache creation for this key
                    key = _cache_key(year)
                    local_ts = _CACHE_TIMESTAMPS.get(key, 0)
                    if remote_ts > local_ts:
                        logger.info("Remote invalidation detected (remote=%s, local=%s). Clearing cache.", remote_ts, local_ts)
                        clear_data_cache()
                elif is_debug:
                    logger.debug("Remote cache_control document not found.")
        except Exception as e:
            logger.warning("Failed to check remote cache control: %s", e)
    elif is_debug and use_local:
        logger.debug("Skipped remote Firestore cache_control check because USE_LOCAL_DATA=True.")

    key = _cache_key(year)
    master_key = _cache_key(None)

    # 2. Check if we already have the memory cache for this specific key
    if key in _DATA_CACHE and (current_time - _CACHE_TIMESTAMPS.get(key, 0) < _CACHE_TTL_SECONDS):
        if is_debug:
            logger.debug("Returning load_data(year=%s) from memory cache.", year)
        return _DATA_CACHE[key]



    local_dir = pathlib.Path('.local_db')

    if use_local and not local_dir.exists():
        local_dir.mkdir(parents=True, exist_ok=True)

    def fetch_or_load(collection_name, filters=None, pkl_suffix=''):
        """Helper to fetch from Disk (Pickle) or Remote (Firestore) or Local-Slice."""
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

        start_io = time.time()
        df = get_collection_df(collection_name, filters)
        if is_debug:
            logger.debug("Firestore read '%s' took %.3fs", collection_name, time.time() - start_io)

        if use_local and not df.empty:
            df.to_pickle(pkl_path)
        return df


    # Build season filter for NFL game data
    season_filter = [('season', '==', year)] if year is not None else None
    yr_suffix = f'_{year}' if year is not None else ''

    from concurrent.futures import ThreadPoolExecutor

    # Define the datasets we need to fetch
    fetch_tasks = [
        ('nfl_standings',    season_filter, yr_suffix),
        ('nfl_teams',        None,          ''),
        ('nfl_games',        season_filter, yr_suffix),
        ('players',          None,          ''),
        ('draft_order',      None,          ''),
        ('draft_results',    None,          ''),
        ('draft_order_rules',None,          '')
    ]

    if is_debug:
        logger.debug("Starting parallel fetch of %d collections...", len(fetch_tasks))

    with ThreadPoolExecutor(max_workers=len(fetch_tasks)) as executor:
        # Map task tuples to fetch_or_load
        futures = {executor.submit(fetch_or_load, *task): task[0] for task in fetch_tasks}
        
        # Collect results into a dictionary for easy access
        results = {}
        for future in futures:
            collection_name = futures[future]
            try:
                results[collection_name] = future.result()
            except Exception as e:
                logger.error("Parallel fetch failed for %s: %s", collection_name, e)
                results[collection_name] = pd.DataFrame()

    # Unpack results in the correct order
    standings         = results['nfl_standings']
    teams             = results['nfl_teams']
    games             = results['nfl_games']
    players           = results['players']
    draft_order       = results['draft_order']
    draft_results     = results['draft_results']
    draft_order_rules = results['draft_order_rules']

    # Schema Healing — Ensure MixedCase column names for downstream logic
    RENAME_MAP = {
        'playerid': 'playerId',
        'fullname': 'fullName',
        'draftpick': 'draftPick',
        'draftorder': 'draftOrder',
        'teamid': 'teamId',
        'nickname': 'nickName',
        'score': 'TotalWinsBySeason' # Handle specific draft_results differences if any
    }

    # Ensure numeric columns are correctly typed
    for df in [standings, teams, games, players, draft_order, draft_results, draft_order_rules]:
        if not df.empty:
            # Heal columns first
            df.rename(columns={k: v for k, v in RENAME_MAP.items() if k in df.columns}, inplace=True)
            
            if 'season' in df.columns:
                df['season'] = pd.to_numeric(df['season'], errors='coerce').fillna(0).astype(int)
            if 'week' in df.columns:
                df['week'] = pd.to_numeric(df['week'], errors='coerce').fillna(0).astype(int)
            if 'playerId' in df.columns:
                df['playerId'] = pd.to_numeric(df['playerId'], errors='coerce').fillna(0).astype(int)
            if 'draftPick' in df.columns:
                df['draftPick'] = pd.to_numeric(df['draftPick'], errors='coerce').fillna(0).astype(int)
            if 'draftOrder' in df.columns:
                df['draftOrder'] = pd.to_numeric(df['draftOrder'], errors='coerce').fillna(0).astype(int)

    # Deduplicate players — old Firestore auto-ID records can cause duplicates
    if not players.empty and 'playerId' in players.columns:
        players = players.dropna(subset=['playerId'])
        players = players.sort_values('playerId').drop_duplicates(subset=['playerId'], keep='last').reset_index(drop=True)

    res = DataBundle(standings, teams, games, players, draft_order, draft_results, draft_order_rules)
    _DATA_CACHE[key] = res
    _CACHE_TIMESTAMPS[key] = current_time
    
    if is_debug:
        logger.debug("load_data(year=%s) total execution took %.3fs", year, time.time() - start_total)
        
    return res

def load_data_season(year: int):
    """
    Returns data sliced to a single season year.
    Uses the same 3-tier cache as load_data().

    Returns the same 7-tuple as load_data() but with DataFrames filtered to
    just the requested year. Use this in route handlers that only need one season
    to avoid processing multi-year master datasets.

    Returns: (standings, teams, games, players, draft_order, draft_results, rules)
    """
    bundle = load_data()

    def _filter(df, col='season'):
        if df.empty or col not in df.columns:
            return df
        return df[df[col] == year].copy()

    return DataBundle(
        _filter(bundle.standings),
        bundle.teams,
        _filter(bundle.games),
        bundle.players,
        bundle.draft_order,
        _filter(bundle.draft_results),
        bundle.draft_order_rules,
    )


def get_active_season(games: pd.DataFrame, draft_results: pd.DataFrame = None) -> int:
    """
    Returns the latest season that has completed game results.
    If draft_results is provided, only considers seasons that also have draft picks.
    This prevents future/post-season data (e.g. 2025 playoffs) from overriding a
    season where draft data hasn't been loaded yet.
    """
    if games.empty or 'season' not in games.columns:
        return 2024
    if 'result' not in games.columns:
        return int(games['season'].max())
    has_results = games[games['result'].notna() & (games['result'] != UNDRAFTED_SENTINEL)]
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
        return r.notna() & (r != UNDRAFTED_SENTINEL)

    week_stats = (
        reg_games.groupby('week')
        .apply(lambda g: pd.Series({
            'total': len(g),
            'done': completed(g['result']).sum()
        }), include_groups=False)
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

def get_preseason_predictions(season: int) -> Dict[str, dict]:
    """Retrieves Win Totals (including avg, std_dev, and sources) from the database."""
    preds_df = get_collection_df("preseason_predictions", filters=[("season", "==", season)])
    if preds_df.empty:
        return {}
    
    # Return a map of team -> {projected_wins, std_dev, sources}
    res = {}
    for _, row in preds_df.iterrows():
        # row.get("mean_wins", ...) only falls back when the key is absent, not
        # when the column exists but is NaN for this row (e.g. a season mixed
        # into a DataFrame with other seasons that do populate mean_wins) --
        # so the NaN case must be checked explicitly with pd.notna.
        mean_wins = row.get("mean_wins")
        res[row["team"]] = {
            "projected_wins": float(row.get("projected_wins", 0)),
            "mean_wins": float(mean_wins) if pd.notna(mean_wins) else float(row.get("projected_wins", 0)),
            "std_dev": float(row.get("std_dev", 0)),
            "sources": row.get("sources", {})
        }
    return res

def get_consensus_projections(season: int) -> Dict[str, dict]:
    """Retrieve analyst consensus projections for a season, keyed by team."""
    df = get_collection_df("consensus_projections", filters=[("season", "==", season)])
    if df.empty:
        return {}

    res = {}
    for _, row in df.iterrows():
        res[row["team"]] = {
            "sources":          row.get("sources", {}),
            "n_sources":        int(row.get("n_sources", 0) or 0),
            "consensus_mean":   row.get("consensus_mean"),
            "consensus_median": row.get("consensus_median"),
            "consensus_min":    row.get("consensus_min"),
            "consensus_max":    row.get("consensus_max"),
            "consensus_std":    row.get("consensus_std"),
        }
    return res

def get_team_schedule(team: str, games_df: pd.DataFrame, season: int) -> List[str]:
    """Extracts a team's sequential 17-game schedule from the NFL Games dataframe."""
    schedule = []
    if games_df.empty or "season" not in games_df.columns:
        return schedule
        
    season_games = games_df[(games_df["season"] == season)]
    team_games = season_games[(season_games["home_team"] == team) | (season_games["away_team"] == team)].copy()

    if "week" in team_games.columns:
        team_games = team_games.sort_values(by="week")
        
    for _, row in team_games.iterrows():
        opp = row["away_team"] if row["home_team"] == team else row["home_team"]
        home_away = "vs" if row["home_team"] == team else "@"
        schedule.append(f"Wk{row.get('week', '?')} {home_away} {opp}")
        
    return schedule

if __name__ == "__main__":
    import json
    from services.analysis_service import get_season_progress
    st, tm, gm, pl, do, dr, drr = load_data()
    s, w = get_latest_season_and_week(gm)
    print(f"Latest: Season {s} Week {w}")
    res = get_season_progress(s, w)
    print(json.dumps(res)[:500])
