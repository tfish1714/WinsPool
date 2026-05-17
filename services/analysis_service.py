import logging
import os
import re
import time
import pandas as pd
import numpy as np
from typing import Dict, List, Any
from services.constants import UNDRAFTED_SENTINEL

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
    remaining_games = schedule[
        (schedule['result'].isna()) & 
        ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
    ].apply(lambda row: 2 if row['fullName_away'] == row['fullName_home'] else 1, axis=1).sum()
    return remaining_games

def player_winsbyWeek(schedule: pd.DataFrame, sorted_players: List[str] = None) -> pd.DataFrame:
    df = schedule[['week', 'fullName_away', 'fullName_home', 'result']].dropna(subset=['result'])
    all_players = pd.concat([df['fullName_away'], df['fullName_home']]).unique()
    all_weeks = df['week'].unique()

    record_by_week = pd.DataFrame('0-0', index=all_players, columns=[f'Week {week}' for week in all_weeks])

    win_loss_tracker = {
        player: {week: {'W': 0, 'L': 0} for week in all_weeks} for player in all_players
    }

    for index, row in df.iterrows():
        away_player = row['fullName_away']
        home_player = row['fullName_home']
        result = row['result']
        week = row['week']

        if result < 0:  # Away win
            win_loss_tracker[away_player][week]['W'] += 1
            win_loss_tracker[home_player][week]['L'] += 1
        elif result > 0:  # Home win
            win_loss_tracker[home_player][week]['W'] += 1
            win_loss_tracker[away_player][week]['L'] += 1

    total_wins_losses = []
    for player, weeks in win_loss_tracker.items():
        total_wins = total_losses = 0
        for week, record in weeks.items():
            wins = record['W']
            losses = record['L']
            total_wins += wins
            total_losses += losses
            record_by_week.at[player, f'Week {week}'] = f'{wins}-{losses} ({total_wins}-{total_losses})'
        total_wins_losses.append(f'{total_wins}-{total_losses}')

    record_by_week['Total'] = total_wins_losses
    result_df = record_by_week.T

    # Sort so latest week is at the top (descending), with Total first
    def _week_sort_key(label):
        if label == 'Total':
            return (0, 0)
        try:
            num = int(label.replace('Week ', ''))
            return (1, -num)
        except ValueError:
            return (2, 0)
    result_df = result_df.reindex(sorted(result_df.index, key=_week_sort_key))

    # Rename the undrafted sentinel index (−1000) to a human-readable label.
    result_df = result_df.rename(columns=lambda c: 'Undrafted' if str(c) in (str(UNDRAFTED_SENTINEL), f'{UNDRAFTED_SENTINEL}.0') else c)

    # Sort columns if sorted_players is provided
    if sorted_players:
        cols = []
        # Add players in ranked order if they exist in the dataframe
        for p in sorted_players:
            if p in result_df.columns:
                cols.append(p)
        
        # Add any remaining players not in the sorted list (e.g. 'Undrafted')
        for p in result_df.columns:
            if p not in cols:
                cols.append(p)
        
        result_df = result_df[cols]

    return result_df

def create_what_if_scenario_matrix(schedule: pd.DataFrame, record_by_week: pd.DataFrame, step: float = 0.166666666666) -> pd.DataFrame:
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
    if schedule.empty or not all(c in schedule.columns for c in ['fullName_away', 'fullName_home', 'result']):
        return pd.DataFrame()
    df = schedule[['fullName_away', 'fullName_home', 'result']].copy()
    # Replace the -1000 sentinel (from get_enriched_schedule fillna) with 'Undrafted'
    df['fullName_away'] = df['fullName_away'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
    df['fullName_home'] = df['fullName_home'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
    # Drop rows where result has the sentinel (game not yet played)
    df = df[df['result'] != UNDRAFTED_SENTINEL]
    df = df.dropna(subset=['result'])
    all_players = pd.concat([df['fullName_away'], df['fullName_home']]).dropna().unique()
    all_players = [p for p in all_players if p not in (None, '', 'nan')]
    if not all_players:
        return pd.DataFrame()
    record_matrix = pd.DataFrame('0-0', index=all_players, columns=all_players)

    win_loss_tracker = {player: {opponent: {'W': 0, 'L': 0, 'T': 0} for opponent in all_players} for player in all_players}
    overall_tracker = {player: {'W': 0, 'L': 0, 'T': 0} for player in all_players}

    for index, row in df.iterrows():
        away_player = row.get('fullName_away')
        home_player = row.get('fullName_home')
        result = row.get('result')

        # Ignore unplayed games
        if pd.isna(result) or result == UNDRAFTED_SENTINEL:
            continue

        # 1. Update true overall record (against anyone, including Undrafted)
        if pd.notna(away_player) and away_player in overall_tracker:
            if result < 0: overall_tracker[away_player]['W'] += 1
            elif result > 0: overall_tracker[away_player]['L'] += 1
            elif result == 0: overall_tracker[away_player]['T'] += 1
            
        if pd.notna(home_player) and home_player in overall_tracker:
            if result > 0: overall_tracker[home_player]['W'] += 1
            elif result < 0: overall_tracker[home_player]['L'] += 1
            elif result == 0: overall_tracker[home_player]['T'] += 1

        # 2. Update H2H matrix (only for head-to-head drafted matchups)
        if pd.isna(away_player) or pd.isna(home_player): continue
        away_player = str(away_player)
        home_player = str(home_player)
        if away_player not in win_loss_tracker or home_player not in win_loss_tracker: continue

        if result < 0:
            win_loss_tracker[away_player][home_player]['W'] += 1
            win_loss_tracker[home_player][away_player]['L'] += 1
        elif result > 0:
            win_loss_tracker[home_player][away_player]['W'] += 1
            win_loss_tracker[away_player][home_player]['L'] += 1
        elif result == 0:
            win_loss_tracker[home_player][away_player]['T'] += 1
            win_loss_tracker[away_player][home_player]['T'] += 1

    row_totals = []
    
    for player in all_players:
        for opponent in all_players:
            record = win_loss_tracker[player][opponent]
            if record['T'] > 0:
                record_matrix.at[player, opponent] = f"{record['W']}-{record['L']}-{record['T']}"
            else:
                record_matrix.at[player, opponent] = f"{record['W']}-{record['L']}"
                
        # Use true overall record for the Total column
        ovr = overall_tracker[player]
        if ovr['T'] > 0:
            row_totals.append(f"{ovr['W']}-{ovr['L']}-{ovr['T']}")
        else:
            row_totals.append(f"{ovr['W']}-{ovr['L']}")

    record_matrix['Overall Record'] = row_totals
    return record_matrix

def reshape_wins_pool_standings(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby(['playerId', 'fullName', 'season']).apply(
        lambda x: x[['team', 'wins', 'ptDiff', 'global_record']].values.flatten()
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

def apply_tiebreakers(reshaped_df: pd.DataFrame) -> pd.DataFrame:
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
        ['TotalWins','Tiebreaker1_WorstTeamWins', 'Tiebreaker2_2ndWorstTeamWins', 'Tiebreaker3_BestTeamWins',
         'Tiebreaker4_WorstTeamPtDiff', 'Tiebreaker5_2ndWorstTeamPtDiff', 'Tiebreaker6_BestTeamPtDiff'],
        ascending=[False,False, False, False, False, False, False]
    )

    sorted_df = sorted_df.reset_index(drop=True)
    sorted_df['Rank'] = sorted_df.index + 1
    cols = ['Rank'] + [col for col in sorted_df.columns if col != 'Rank']
    return sorted_df[cols]

def get_enriched_schedule(games, draft_results, players, season):
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
    
    final_merged = final_merged.where(pd.notnull(final_merged), None)
    final_merged = final_merged.fillna(UNDRAFTED_SENTINEL)
    
    if is_debug:
        logger.debug("get_enriched_schedule processing took %.3fs", time.time() - start_op)
    return final_merged

def calculate_wins_pool_standings(standings, draft_results, players, season, games=None):
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
