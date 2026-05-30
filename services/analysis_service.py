import logging
import os
import re
import time
import pandas as pd
import numpy as np

try:
    pd.set_option('future.no_silent_downcasting', True)
except Exception:
    pass  # Option added in pandas 2.2; silently skip on older versions
from typing import Dict, List, Any
from services.constants import UNDRAFTED_SENTINEL, TIEBREAKER_SORT_COLS, DRAFT_ROUNDS
from services.utils import filter_season

logger = logging.getLogger(__name__)


def compute_team_records(games: pd.DataFrame, season: int) -> Dict[str, Dict[str, int]]:
    """Build W-L-T records for every team using vectorized pandas operations.

    Args:
        games: Full games DataFrame (must contain season, result, home_team,
               away_team, and optionally game_type columns).
        season: The target season year.

    Returns:
        Dict mapping team abbreviation to {'W': int, 'L': int, 'T': int}.
    """
    played = games[
        (games['season'] == season)
        & (games['result'].notna())
        & (games['result'] != UNDRAFTED_SENTINEL)
    ]
    if 'game_type' in played.columns:
        played = played[played['game_type'] == 'REG']
    if played.empty:
        return {}

    result = played['result']

    # Home team perspective
    home_wins = (result > 0).astype(int)
    home_losses = (result < 0).astype(int)
    home_ties = (result == 0).astype(int)

    home_records = pd.DataFrame({
        'team': played['home_team'],
        'W': home_wins, 'L': home_losses, 'T': home_ties
    }).groupby('team').sum()

    # Away team perspective
    away_wins = (result < 0).astype(int)
    away_losses = (result > 0).astype(int)
    away_ties = (result == 0).astype(int)

    away_records = pd.DataFrame({
        'team': played['away_team'],
        'W': away_wins, 'L': away_losses, 'T': away_ties
    }).groupby('team').sum()

    # Combine home + away records
    combined = home_records.add(away_records, fill_value=0).fillna(0).astype(int)
    return combined.to_dict(orient='index')


def format_team_record(team: str, records: Dict[str, Dict[str, int]]) -> str:
    """Format a team's W-L(-T) record as a display string."""
    if team not in records:
        return "0-0"
    r = records[team]
    if r.get('T', 0) > 0:
        return f"{r['W']}-{r['L']}-{r['T']}"
    return f"{r['W']}-{r['L']}"

def get_remaining_games(player: str, schedule: pd.DataFrame) -> int:
    """Return the count of unplayed games remaining for a player's drafted teams."""
    filtered = schedule[
        (schedule['result'].isna()) &
        ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
    ]
    if filtered.empty:
        return 0
    return int(np.where(
        filtered['fullName_away'] == filtered['fullName_home'], 2, 1
    ).sum())

def player_winsbyWeek(schedule: pd.DataFrame, sorted_players: List[str] = None) -> pd.DataFrame:
    """Return a DataFrame of cumulative wins per player broken down by week."""
    df = schedule[['week', 'fullName_away', 'fullName_home', 'result']].dropna(subset=['result'])
    if df.empty:
        return pd.DataFrame()

    all_players = pd.concat([df['fullName_away'], df['fullName_home']]).unique()
    all_weeks = sorted(df['week'].astype(int).unique())

    # Away-team perspective: wins when result < 0
    away = df[['week', 'fullName_away', 'result']].rename(columns={'fullName_away': 'player'})
    away['W'] = (away['result'] < 0).astype(int)
    away['L'] = (away['result'] > 0).astype(int)

    # Home-team perspective: wins when result > 0
    home = df[['week', 'fullName_home', 'result']].rename(columns={'fullName_home': 'player'})
    home['W'] = (home['result'] > 0).astype(int)
    home['L'] = (home['result'] < 0).astype(int)

    combined = pd.concat(
        [away[['week', 'player', 'W', 'L']], home[['week', 'player', 'W', 'L']]],
        ignore_index=True,
    )
    weekly = combined.groupby(['player', 'week'])[['W', 'L']].sum()

    # Reindex to all player×week pairs so cumsum is continuous even for empty weeks
    full_idx = pd.MultiIndex.from_product([all_players, all_weeks], names=['player', 'week'])
    weekly = weekly.reindex(full_idx, fill_value=0).reset_index()
    weekly = weekly.sort_values(['player', 'week'])

    weekly['cum_W'] = weekly.groupby('player')['W'].cumsum()
    weekly['cum_L'] = weekly.groupby('player')['L'].cumsum()
    weekly['cell'] = (
        weekly['W'].astype(str) + '-' + weekly['L'].astype(str)
        + ' (' + weekly['cum_W'].astype(str) + '-' + weekly['cum_L'].astype(str) + ')'
    )

    pivot = weekly.pivot(index='player', columns='week', values='cell')
    pivot.columns = [f'Week {w}' for w in pivot.columns]

    totals = weekly.groupby('player')[['W', 'L']].sum()
    pivot['Total'] = totals['W'].astype(str) + '-' + totals['L'].astype(str)

    result_df = pivot.T

    def _week_sort_key(label):
        if label == 'Total':
            return (0, 0)
        try:
            return (1, -int(str(label).replace('Week ', '')))
        except ValueError:
            return (2, 0)

    result_df = result_df.reindex(sorted(result_df.index, key=_week_sort_key))
    result_df = result_df.rename(
        columns=lambda c: 'Undrafted' if str(c) in (str(UNDRAFTED_SENTINEL), f'{UNDRAFTED_SENTINEL}.0') else c
    )

    if sorted_players:
        cols = [p for p in sorted_players if p in result_df.columns]
        cols += [p for p in result_df.columns if p not in cols]
        result_df = result_df[cols]

    return result_df

def create_what_if_scenario_matrix(schedule: pd.DataFrame, record_by_week: pd.DataFrame, step: float = 0.166666666666) -> pd.DataFrame:
    """Build a matrix of hypothetical final-standings outcomes across remaining games.

    Iterates over win-probability steps to estimate how often each player
    finishes in each rank given uncertain remaining results.
    """
    transpose_record_by_week = record_by_week.T
    all_players = transpose_record_by_week.index

    step = float(step)
    scenarios = np.arange(0, 1 + step, step)
    scenario_matrix = pd.DataFrame(index=all_players, columns=[f'{int(100 * scenario)}% Wins' for scenario in scenarios])

    for player in all_players:
        if player not in transpose_record_by_week.index:
            continue
        current_record = transpose_record_by_week.loc[player, 'Total']
        current_wins, current_losses = map(int, current_record.split('-'))

        remaining_games = schedule[
            (schedule['result'].isna()) &
            ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
        ].apply(lambda row: 2 if row['fullName_away'] == row['fullName_home'] else 1, axis=1).sum()

        for scenario in scenarios:
            projected_wins = int(scenario * remaining_games)
            projected_losses = remaining_games - projected_wins

            total_wins = current_wins + projected_wins
            total_losses = current_losses + projected_losses

            scenario_matrix.at[player, f'{int(100 * scenario)}% Wins'] = f'{total_wins}-{total_losses}'

    columns = scenario_matrix.columns.tolist()
    sorted_columns = sorted(columns, key=lambda x: int(x.split('%')[0]), reverse=True)
    scenario_matrix = scenario_matrix[sorted_columns]
    scenario_matrix.index = [f'{player} ({get_remaining_games(player, schedule)})' for player in all_players]

    return scenario_matrix


def calculate_playoff_race(schedule: pd.DataFrame, standings_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """
    For each player, compute:
      - current_wins: total wins so far
      - remaining_games: how many games they still have
      - max_wins: current_wins + remaining_games (best possible outcome)
      - can_reach: dict of {target_player: True/False} — whether this player
        can mathematically end up with more wins than target_player's CURRENT total
    
    Returns a list of dicts sorted by current_wins descending.
    """
    import time
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_op = time.time()
    # Build per-player current wins & remaining from the schedule
    all_players = pd.concat([
        schedule['fullName_away'],
        schedule['fullName_home']
    ]).dropna().unique()

    records = []
    for player in all_players:
        # Skip -1000 sentinel (undrafted teams) and any non-player entries
        try:
            if float(player) == UNDRAFTED_SENTINEL:
                continue
        except (ValueError, TypeError):
            pass
        if not isinstance(player, str) or not player.strip():
            continue

        # Vectorized win counting: avoid row-by-row iteration
        played = schedule[
            ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player)) &
            (schedule['result'].notna()) &
            (schedule['result'] != -1000)
        ]
        home_wins = ((played['fullName_home'] == player) & (played['result'] > 0)).sum()
        away_wins = ((played['fullName_away'] == player) & (played['result'] < 0)).sum()
        wins = int(home_wins + away_wins)

        remaining = get_remaining_games(player, schedule)
        max_wins = wins + int(remaining)
        records.append({
            'player': player,
            'current_wins': wins,
            'remaining_games': int(remaining),
            'max_wins': int(max_wins)
        })

    # Sort by current wins descending
    records.sort(key=lambda x: -x['current_wins'])

    # For each player, determine which positions they can still mathematically reach
    for i, rec in enumerate(records):
        race_info = []
        for j, target in enumerate(records):
            if j == i:
                continue
            position = j + 1
            target_wins = target['current_wins']
            # Can reach this player's position: max_wins must exceed target's CURRENT wins
            can_pass = rec['max_wins'] > target_wins
            # Gap: wins needed beyond what they have
            gap = target_wins - rec['current_wins']
            race_info.append({
                'position': position,
                'target_player': target['player'],
                'target_wins': target_wins,
                'gap': gap,
                'can_pass': can_pass
            })
        rec['race'] = race_info
        rec['rank'] = i + 1

    if is_debug:
        logger.debug("calculate_playoff_race processing took %.3fs", time.time() - start_op)
    return records


def player_winlossmatrix(schedule: pd.DataFrame) -> pd.DataFrame:
    """Return a head-to-head win/loss record matrix between every player pair."""
    if schedule.empty or not all(c in schedule.columns for c in ['fullName_away', 'fullName_home', 'result']):
        return pd.DataFrame()

    df = schedule[['fullName_away', 'fullName_home', 'result']].copy()
    df['fullName_away'] = df['fullName_away'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
    df['fullName_home'] = df['fullName_home'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
    df = df[df['result'] != UNDRAFTED_SENTINEL].dropna(subset=['result'])
    if df.empty:
        return pd.DataFrame()

    all_players = pd.concat([df['fullName_away'], df['fullName_home']]).dropna().unique()
    all_players = [p for p in all_players if p not in (None, '', 'nan')]
    if not all_players:
        return pd.DataFrame()

    # -- Overall record (vectorized) -----------------------------------------
    away_w = df[df['result'] < 0].groupby('fullName_away').size().rename('W')
    away_l = df[df['result'] > 0].groupby('fullName_away').size().rename('L')
    away_t = df[df['result'] == 0].groupby('fullName_away').size().rename('T')
    home_w = df[df['result'] > 0].groupby('fullName_home').size().rename('W')
    home_l = df[df['result'] < 0].groupby('fullName_home').size().rename('L')
    home_t = df[df['result'] == 0].groupby('fullName_home').size().rename('T')

    idx = pd.Index(all_players)
    W = away_w.reindex(idx, fill_value=0) + home_w.reindex(idx, fill_value=0)
    L = away_l.reindex(idx, fill_value=0) + home_l.reindex(idx, fill_value=0)
    T = away_t.reindex(idx, fill_value=0) + home_t.reindex(idx, fill_value=0)

    overall = pd.DataFrame({'W': W, 'L': L, 'T': T}, index=idx)
    overall['Overall Record'] = np.where(
        overall['T'] > 0,
        overall['W'].astype(str) + '-' + overall['L'].astype(str) + '-' + overall['T'].astype(str),
        overall['W'].astype(str) + '-' + overall['L'].astype(str),
    )

    # -- H2H matrix (vectorized) ---------------------------------------------
    away_wins = df[df['result'] < 0][['fullName_away', 'fullName_home']].rename(
        columns={'fullName_away': 'winner', 'fullName_home': 'loser'}
    )
    home_wins = df[df['result'] > 0][['fullName_home', 'fullName_away']].rename(
        columns={'fullName_home': 'winner', 'fullName_away': 'loser'}
    )
    ties_a = df[df['result'] == 0][['fullName_away', 'fullName_home']].rename(
        columns={'fullName_away': 'p1', 'fullName_home': 'p2'}
    )
    ties_b = ties_a.rename(columns={'p1': 'p2', 'p2': 'p1'})

    win_counts = pd.concat([away_wins, home_wins]).groupby(['winner', 'loser']).size()
    tie_counts = pd.concat([ties_a, ties_b]).groupby(['p1', 'p2']).size()

    record_matrix = pd.DataFrame('0-0', index=all_players, columns=all_players)

    # Pass 1: fill cells where winner has wins (include tie component)
    for (winner, loser), w_count in win_counts.items():
        if winner in record_matrix.index and loser in record_matrix.columns:
            l_count = int(win_counts.get((loser, winner), 0))
            t_count = int(tie_counts.get((winner, loser), 0))
            if t_count > 0:
                record_matrix.loc[winner, loser] = f"{w_count}-{l_count}-{t_count}"
            else:
                record_matrix.loc[winner, loser] = f"{w_count}-{l_count}"

    # Pass 2: fill cells where player only lost (never won vs this opponent)
    for (winner, loser), w_count in win_counts.items():
        if loser in record_matrix.index and winner in record_matrix.columns:
            if record_matrix.loc[loser, winner] == '0-0':
                t_count = int(tie_counts.get((loser, winner), 0))
                if t_count > 0:
                    record_matrix.loc[loser, winner] = f"0-{w_count}-{t_count}"
                else:
                    record_matrix.loc[loser, winner] = f"0-{w_count}"

    # Pass 3: fill tie-only pairs (neither player won, both cells still '0-0')
    for (p1, p2), t_count in tie_counts.items():
        if p1 in record_matrix.index and p2 in record_matrix.columns:
            if record_matrix.loc[p1, p2] == '0-0':
                record_matrix.loc[p1, p2] = f"0-0-{t_count}"

    record_matrix['Overall Record'] = overall['Overall Record']
    return record_matrix

# Pivot from long format (one row per player-team pair) to wide format
# (one row per player). Each player has 3 drafted teams, so the function
# groups by player and flattens (team, wins, ptDiff, global_record) × 3
# into columns wins1/wins2/wins3, ptDiff1/2/3, global_record1/2/3.
# Teams are ordered by draft pick number because the DataFrame is already
# sorted that way by the caller. TotalWins is derived here as the sum.
def reshape_wins_pool_standings(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot per-team win rows into a wide-format standings DataFrame (one row per player).

    Each player row gets wins1/wins2/wins3, ptDiff1/2/3, and global_record1/2/3
    columns corresponding to their three drafted teams.
    """
    grouped = df.groupby(['playerId', 'fullName', 'season']).apply(
        lambda x: x[['team', 'wins', 'ptDiff', 'global_record']].values.flatten(),
        include_groups=False,
    )
    if grouped.empty: return pd.DataFrame()
    num_teams = len(grouped.iloc[0]) // 4
    reshaped_df = pd.DataFrame(grouped.tolist(), index=grouped.index)
    
    reshaped_df.columns = [
        f'{label}{i//4+1}' for i, label in enumerate(['team', 'wins', 'ptDiff', 'global_record'] * num_teams)
    ]
    reshaped_df['TotalWins'] = reshaped_df[[f'wins{i+1}' for i in range(num_teams)]].sum(axis=1)
    reshaped_df = reshaped_df.reset_index()
    reshaped_df.fillna(0, inplace=True)
    # Cast all numeric score/diff columns to int so they never display as floats
    for col in reshaped_df.columns:
        if col.startswith(('wins', 'ptDiff', 'TotalWins', 'Rank', 'Tiebreaker')):
            try:
                reshaped_df[col] = reshaped_df[col].astype(float).astype(int)
            except (ValueError, TypeError):
                pass
    return reshaped_df

# Build six derived tiebreaker columns from the three per-team win and
# point-differential columns, then sort by all seven columns descending.
# Point-differential columns also sort descending: a higher (less negative)
# differential is better, so descending puts the best differential first.
# Column names and sort order are defined in TIEBREAKER_SORT_COLS
# (services/constants.py) so the cascade can be audited in one place.
def apply_tiebreakers(reshaped_df: pd.DataFrame) -> pd.DataFrame:
    """Sort standings using the 6-tier tiebreaker cascade.

    Tiers (highest to lowest priority): total wins, worst-team wins,
    best-team wins, total point differential, head-to-head record,
    preseason projected wins. All tiers sort descending.
    """
    if reshaped_df.empty: return reshaped_df
    reshaped_df['Tiebreaker1_WorstTeamWins'] = reshaped_df[['wins1', 'wins2', 'wins3']].min(axis=1)
    reshaped_df['Tiebreaker2_2ndWorstTeamWins'] = reshaped_df[['wins1', 'wins2', 'wins3']].apply(
        lambda x: sorted(x)[1] if len(x) > 1 else 0, axis=1
    )
    reshaped_df['Tiebreaker3_BestTeamWins'] = reshaped_df[['wins1', 'wins2', 'wins3']].max(axis=1)

    reshaped_df['Tiebreaker4_WorstTeamPtDiff'] = reshaped_df[['ptDiff1', 'ptDiff2', 'ptDiff3']].min(axis=1)
    reshaped_df['Tiebreaker5_2ndWorstTeamPtDiff'] = reshaped_df[['ptDiff1', 'ptDiff2', 'ptDiff3']].apply(
        lambda x: sorted(x)[1] if len(x) > 1 else 0, axis=1
    )
    reshaped_df['Tiebreaker6_BestTeamPtDiff'] = reshaped_df[['ptDiff1', 'ptDiff2', 'ptDiff3']].max(axis=1)

    sorted_df = reshaped_df.sort_values(
        TIEBREAKER_SORT_COLS,
        ascending=[False] * len(TIEBREAKER_SORT_COLS),
    )

    sorted_df = sorted_df.reset_index(drop=True)
    sorted_df['Rank'] = sorted_df.index + 1
    cols = ['Rank'] + [col for col in sorted_df.columns if col != 'Rank']
    return sorted_df[cols]

# Six-way merge sequence:
#   1. today_games         — current-season REG games from nfl_games
#   2. away draft_results  — maps away_team → playerId (who owns that team)
#   3. players             — maps playerId → fullName_away
#   4. home draft_results  — maps home_team → playerId_home_draft
#   5. players again       — maps playerId_home_draft → fullName_home
#   6. winner draft_results + players — maps winning_team → fullName (winner's owner)
# Empty-string suffixes on merges 4-6 avoid column-name collisions with the
# columns already added in earlier merges. The final frame has one row per
# game with both owners' names, the winner's owner, and ML predictions.
def get_enriched_schedule(games, draft_results, players, season):
    """Join games with draft ownership, player metadata, standings, and predictions.

    Performs a 6-way merge so each game row carries the owning player's name,
    their team's season record, and ML win-probability for display in the
    schedule tab.
    """
    import time
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_op = time.time()
    if games.empty:
        return pd.DataFrame()
    # If no draft results yet (pre-draft season), use an empty placeholder so
    # the schedule still renders without player name columns filled in
    if draft_results.empty or 'season' not in draft_results.columns:
        draft_results = pd.DataFrame(columns=['season', 'team', 'playerId'])
    today_games = games[(games['season'] == season) & (games.get('game_type', pd.Series(['REG']*len(games))).eq('REG'))].copy() if 'game_type' in games.columns else games[games['season'] == season].copy()
    today_draft_results = draft_results[draft_results['season'] == season].copy()
    
    # Add away team player logic
    away_merged = pd.merge(today_games, today_draft_results, left_on=['away_team','season'], right_on=['team','season'], how='left')
    away_merged = pd.merge(away_merged, players, on='playerId', how='left')
    away_merged.rename(columns={'fullName': 'fullName_away'}, inplace=True)
    
    # Add home team player logic
    final_merged = pd.merge(away_merged, today_draft_results, left_on=['home_team','season'], right_on=['team','season'], how='left', suffixes=('', '_home_draft'))
    final_merged = pd.merge(final_merged, players, left_on='playerId_home_draft', right_on='playerId', how='left', suffixes=('', '_home_player'))
    final_merged.rename(columns={'fullName': 'fullName_home'}, inplace=True)
    
    # Calculate winner mapping
    final_merged['winning_team'] = np.where(final_merged['result'] > 0, final_merged['home_team'], 
                                   np.where(final_merged['result'] < 0, final_merged['away_team'], np.nan))
                                   
    final_merged = pd.merge(final_merged, today_draft_results, left_on=['winning_team','season'],right_on=['team','season'], how='left', suffixes=('', '_winner'))
    final_merged = pd.merge(final_merged, players, left_on='playerId_winner', right_on='playerId', how='left', suffixes=('', '_winner_player'))
    final_merged.rename(columns={'fullName': 'fullName'}, inplace=True)
    
    # UI Formatting requirements from the user's legacy Flask code
    final_merged['gameday'] = pd.to_datetime(final_merged['gameday'], format='%Y-%m-%d', errors='coerce')
    final_merged['home_score'] = final_merged['home_score'].astype('Int64')
    final_merged['away_score'] = final_merged['away_score'].astype('Int64')
    
    # Sort chronologically by week and gameday
    final_merged = final_merged.sort_values(['week', 'gameday'], ascending=[True, True])
    
    # Calculate Global Team Records using the shared vectorized utility
    team_records = compute_team_records(games, season)
    final_merged['away_record'] = final_merged['away_team'].apply(lambda t: format_team_record(t, team_records))
    final_merged['home_record'] = final_merged['home_team'].apply(lambda t: format_team_record(t, team_records))
    
    final_merged = final_merged.fillna(UNDRAFTED_SENTINEL)
    
    if is_debug:
        logger.debug("get_enriched_schedule processing took %.3fs", time.time() - start_op)
    return final_merged

def calculate_wins_pool_standings(standings, draft_results, players, season, games=None):
    """Compute per-player cumulative win totals from game results and draft assignments.

    Merges standings with draft_results and players to produce a DataFrame
    with one row per (player, team) pair and columns for wins, point
    differential, and global record.
    """
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    if draft_results.empty or 'season' not in draft_results.columns:
        return pd.DataFrame()
    today_standings = standings[standings['season'] == season].copy() if not standings.empty and 'season' in standings.columns else pd.DataFrame()
    today_draft_results = draft_results[draft_results['season'] == season].copy()
    
    wins_pool_standings = pd.merge(today_standings, today_draft_results, on=['team', 'season'])
    
    if 'scored' in wins_pool_standings.columns and 'allowed' in wins_pool_standings.columns:
        wins_pool_standings['ptDiff'] = wins_pool_standings['scored'] - wins_pool_standings['allowed']
    else:
        wins_pool_standings['ptDiff'] = 0
        
    if 'team' not in wins_pool_standings.columns or 'season' not in wins_pool_standings.columns:
        if is_debug:
            logger.error("wins_pool_standings missing merge keys: %s", wins_pool_standings.columns.tolist())
        return pd.DataFrame()

    if 'playerId' not in wins_pool_standings.columns:
        if is_debug:
            logger.error("wins_pool_standings missing 'playerId'. Cols: %s", wins_pool_standings.columns.tolist())
        return pd.DataFrame()

    if 'playerId' not in players.columns:
        if is_debug:
            logger.error("'players' DF missing 'playerId'. Cols: %s", players.columns.tolist())
        return pd.DataFrame()

    wins_pool_standings = pd.merge(wins_pool_standings, players, on='playerId', how='inner')
    
    # Optional: Attach global team records if games DF is passed
    if games is not None and not games.empty:
        team_records = compute_team_records(games, season)
        wins_pool_standings['global_record'] = wins_pool_standings['team'].apply(
            lambda t: format_team_record(t, team_records)
        )
    else:
        wins_pool_standings['global_record'] = "0-0"

    # Sort by draftPick to ensure team1, team2, etc. are in draft order
    if 'draftPick' in wins_pool_standings.columns:
        wins_pool_standings = wins_pool_standings.sort_values(by=['playerId', 'draftPick'])
    
    reshaped_df = reshape_wins_pool_standings(wins_pool_standings)
    sorted_df = apply_tiebreakers(reshaped_df)
    
    from datetime import datetime
    refreshTime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sorted_df["refreshTime"] = refreshTime
    return sorted_df


def get_player_analytics(
    player_id: int,
    all_draft_results: pd.DataFrame,
    standings_master: pd.DataFrame,
    players: pd.DataFrame,
    preseason_preds: dict,
) -> dict | None:
    """Multi-season analytics for one player.

    preseason_preds: {season_int: {team_abbr: {"projected_wins": float}}}
    Returns None if the player has no draft history.
    """
    if all_draft_results.empty:
        return None
    player_draft = all_draft_results[all_draft_results["playerId"] == player_id]
    if player_draft.empty:
        return None

    # League-wide avg wins per draft slot across all players/seasons
    slot_avgs: dict[int, float | None] = {}
    max_pick = max(hi for _, hi in DRAFT_ROUNDS.values())
    for pick_num in range(1, max_pick + 1):
        slot_picks = all_draft_results[all_draft_results["draftPick"] == pick_num]
        wins_list = []
        for _, row in slot_picks.iterrows():
            yr_st = filter_season(standings_master, row["season"])
            team_row = yr_st[yr_st["team"] == row["team"]]
            if not team_row.empty:
                wins_list.append(int(team_row.iloc[0].get("wins", 0)))
        slot_avgs[pick_num] = round(sum(wins_list) / len(wins_list), 1) if wins_list else None

    available_seasons = sorted(int(s) for s in player_draft["season"].unique())
    seasons_data = []

    for season in available_seasons:
        season_picks = filter_season(player_draft, season).sort_values("draftPick")
        yr_standings = filter_season(standings_master, season)
        all_season_draft = filter_season(all_draft_results, season)
        season_preds = preseason_preds.get(season, {})

        picks = []
        total_wins = 0
        for _, pick_row in season_picks.iterrows():
            team = pick_row["team"]
            pick_num = int(pick_row["draftPick"])
            team_st = yr_standings[yr_standings["team"] == team]
            actual_wins = int(team_st.iloc[0].get("wins", 0)) if not team_st.empty else 0
            total_wins += actual_wins
            projected = float(season_preds.get(team, {}).get("projected_wins", 0))
            slot_avg = slot_avgs.get(pick_num)  # may be None if no historical data for this slot
            picks.append({
                "pickNum": pick_num,
                "team": team,
                "actualWins": actual_wins,
                "projectedWins": round(projected, 1),
                "slotAvgWins": slot_avg,
                "vsProjected": round(actual_wins - projected, 1),
                "vsSlot": round(actual_wins - slot_avg, 1) if slot_avg is not None else None,
            })

        pool = calculate_wins_pool_standings(yr_standings, all_season_draft, players, season)
        pool_row = pool[pool["playerId"] == player_id] if not pool.empty else pd.DataFrame()
        rank = int(pool_row.iloc[0]["Rank"]) if not pool_row.empty else 0

        seasons_data.append({"year": season, "rank": rank, "totalWins": total_wins, "picks": picks})

    if not seasons_data:
        return None

    all_wins = [s["totalWins"] for s in seasons_data]
    ranked = [s for s in seasons_data if s["rank"] > 0]
    best_finish  = min(ranked, key=lambda s: s["rank"]) if ranked else None
    worst_finish = max(ranked, key=lambda s: s["rank"]) if ranked else None

    p_row = players[players["playerId"] == player_id]
    p = p_row.iloc[0] if not p_row.empty else pd.Series(dtype=object)

    return {
        "player": {
            "playerId": player_id,
            "fullName": str(p.get("fullName", "")),
            "nickName": str(p.get("nickName", "")),
        },
        "career": {
            "seasons": len(seasons_data),
            "totalWins": sum(all_wins),
            "avgWins": round(sum(all_wins) / len(all_wins), 1),
            "bestFinish":  {"rank": best_finish["rank"],  "year": best_finish["year"]}  if best_finish  else None,
            "worstFinish": {"rank": worst_finish["rank"], "year": worst_finish["year"]} if worst_finish else None,
        },
        "seasons": seasons_data,
        "slotAverages": {str(k): v for k, v in slot_avgs.items()},
    }


# ---------------------------------------------------------------------------
# Season progress computation (moved from data_service.py)
# ---------------------------------------------------------------------------

def process_games_data(games: pd.DataFrame) -> pd.DataFrame:
    """Compute cumulative wins for each team per week from raw games data."""
    games = games.copy()
    conditions = [games['result'] > 0, games['result'] < 0]
    choices = [games['home_team'], games['away_team']]
    games['winner'] = np.select(conditions, choices, default=np.nan)
    games['rec'] = 1
    games.sort_values(['season', 'week'], inplace=True)
    games['TotalWinsBySeason'] = games.groupby(['season', 'winner', 'game_type'])['rec'].cumsum()
    games.drop('rec', axis=1, inplace=True)
    games.rename(columns={'winner': 'team'}, inplace=True)
    return games


def get_season_progress(season: int, week: int) -> Dict[str, Any]:
    """Compute player and team wins for a season up to a given week.

    Returns a dict suitable for JSON serialization and Chart.js consumption.
    """
    from services.data_service import load_data  # local import avoids circular dep
    is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
    start_op = time.time()

    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data(year=season)
    games = process_games_data(games)

    today_teams = teams[teams['season'] == season].copy()
    today_standings = standings[standings['season'] == season].copy()
    today_draft_results = draft_results[draft_results['season'] == season].copy()

    if 'game_type' in games.columns:
        today_games = games[(games['season'] == season) & (games['week'] <= week) & (games['game_type'] == 'REG')].copy()
    else:
        today_games = games[(games['season'] == season) & (games['week'] <= week)].copy()

    games_player_added = pd.merge(today_games, today_draft_results, on=['team', 'season'], how='inner')
    games_player_added['rec'] = 1
    games_player_added.sort_values(['season', 'week'], inplace=True)
    games_player_added['TotalPlayerWinsBySeason'] = games_player_added.groupby('playerId')['rec'].cumsum()
    games_player_added = pd.merge(games_player_added, players, on='playerId', how='inner')

    wins_by_week_player = games_player_added.groupby(['season', 'week', 'nickName'])['TotalPlayerWinsBySeason'].max().reset_index()
    wins_by_week_player = wins_by_week_player.pivot_table(index=['season', 'week'], columns='nickName', values='TotalPlayerWinsBySeason').ffill().fillna(0).reset_index()

    nick_to_pid = (
        games_player_added[['nickName', 'playerId']]
        .drop_duplicates('nickName')
        .set_index('nickName')['playerId']
        .to_dict()
    )

    player_data: Dict[str, Any] = {
        "labels": wins_by_week_player["week"].tolist(),
        "datasets": []
    }
    for player in [col for col in wins_by_week_player.columns if col not in ['season', 'week']]:
        player_data["datasets"].append({
            "label": player,
            "playerId": int(nick_to_pid.get(player, -1)),
            "data": wins_by_week_player[player].tolist()
        })

    teams_with_wins = today_games.dropna(subset=['team'])
    team_data: Dict[str, Any] = {
        "labels": sorted(teams_with_wins["week"].unique().tolist()),
        "datasets": []
    }
    for team in teams_with_wins['team'].unique():
        t_data = teams_with_wins[teams_with_wins['team'] == team].sort_values('week')
        merged = pd.DataFrame({"week": team_data["labels"]})
        merged = pd.merge(merged, t_data[['week', 'TotalWinsBySeason']], on='week', how='left')
        merged['TotalWinsBySeason'] = merged['TotalWinsBySeason'].ffill().fillna(0)
        team_data["datasets"].append({"label": str(team), "data": merged["TotalWinsBySeason"].tolist()})

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
    wins_pool_standings = wins_pool_standings.replace({np.nan: None})

    if 'wins' in wins_pool_standings.columns and 'ptDiff' in wins_pool_standings.columns and 'draftPick' in wins_pool_standings.columns:
        picks_ranked = wins_pool_standings.sort_values(by=['wins', 'draftPick', 'ptDiff'], ascending=[False, False, False])
    elif 'wins' in wins_pool_standings.columns and 'ptDiff' in wins_pool_standings.columns:
        picks_ranked = wins_pool_standings.sort_values(by=['wins', 'ptDiff'], ascending=[False, False])
    else:
        picks_ranked = wins_pool_standings.copy()

    best_overall_team = picks_ranked.iloc[0]['team'] if not picks_ranked.empty else None

    total_players = len(players)
    if not picks_ranked.empty and 'draftPick' in picks_ranked.columns:
        picks_ranked['round'] = np.ceil(picks_ranked['draftPick'].astype(float) / total_players)
        ranks_valid = picks_ranked[picks_ranked['wins'].notnull()] if 'wins' in picks_ranked.columns else pd.DataFrame()
        if not ranks_valid.empty:
            best_by_round_teams = {}
            for r, grp in picks_ranked.groupby('round'):
                best_by_round_teams[r] = grp.iloc[0]['team'] if not grp.empty else None
            best_by_round_teams = {str(int(k)): (v if pd.notnull(v) else None) for k, v in best_by_round_teams.items() if pd.notnull(k)}
        else:
            best_by_round_teams = {}
    else:
        best_by_round_teams = {}

    if is_debug:
        logger.debug("get_season_progress(week=%s) processing took %.3fs", week, time.time() - start_op)

    return {
        "season": season,
        "week": week,
        "player_chart": player_data,
        "team_chart": team_data,
        "best_overall": best_overall_team,
        "best_by_round": best_by_round_teams,
        "standings": wins_pool_standings.to_dict(orient="records")
    }
