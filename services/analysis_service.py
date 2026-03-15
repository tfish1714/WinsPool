import pandas as pd
import numpy as np
import os
import re

def get_remaining_games(player, schedule):
    remaining_games = schedule[
        (schedule['result'].isna()) & 
        ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
    ].apply(lambda row: 2 if row['fullName_away'] == row['fullName_home'] else 1, axis=1).sum()
    return remaining_games

def player_winsbyWeek(schedule, sorted_players=None):
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
    result_df = result_df.rename(columns=lambda c: 'Undrafted' if str(c) in ('-1000', '-1000.0') else c)

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

def create_what_if_scenario_matrix(schedule, record_by_week, step=0.166666666666):
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


def calculate_playoff_race(schedule, standings_df):
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
            if float(player) == -1000:
                continue
        except (ValueError, TypeError):
            pass
        if not isinstance(player, str) or not player.strip():
            continue

        # Games already played (result is not NaN and not -1000 sentinel)
        played = schedule[
            ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player)) &
            (schedule['result'].notna()) &
            (schedule['result'] != -1000)
        ]
        wins = 0
        for _, row in played.iterrows():
            result = row['result']
            if result > 0 and row['fullName_home'] == player:
                wins += 1
            elif result < 0 and row['fullName_away'] == player:
                wins += 1

        remaining = get_remaining_games(player, schedule)
        max_wins = int(wins) + int(remaining)
        records.append({
            'player': player,
            'current_wins': int(wins),
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
        print(f"[DEBUG_PAGE_LOAD] calculate_playoff_race processing took {time.time() - start_op:.3f}s")
    return records


def player_winlossmatrix(schedule):
    if schedule.empty or not all(c in schedule.columns for c in ['fullName_away', 'fullName_home', 'result']):
        return pd.DataFrame()
    df = schedule[['fullName_away', 'fullName_home', 'result']].copy()
    # Replace the -1000 sentinel (from get_enriched_schedule fillna) with 'Undrafted'
    df['fullName_away'] = df['fullName_away'].replace(-1000, 'Undrafted').replace('-1000', 'Undrafted')
    df['fullName_home'] = df['fullName_home'].replace(-1000, 'Undrafted').replace('-1000', 'Undrafted')
    # Drop rows where result has the sentinel (game not yet played)
    df = df[df['result'] != -1000]
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
        if pd.isna(result) or result == -1000:
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

def reshape_wins_pool_standings(df):
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

def apply_tiebreakers(reshaped_df):
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
    if games.empty or draft_results.empty or 'season' not in draft_results.columns:
        return pd.DataFrame()
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
    
    # Calculate Global Team Records (Wins-Losses-Ties) for the season
    team_records = {}
    played_games = games[(games['season'] == season) & (games['result'].notna()) & (games['result'] != -1000) & (games['game_type'].eq('REG'))]
    
    for _, row in played_games.iterrows():
        away = row['away_team']
        home = row['home_team']
        res = row['result']
        
        if away not in team_records: team_records[away] = {'W': 0, 'L': 0, 'T': 0}
        if home not in team_records: team_records[home] = {'W': 0, 'L': 0, 'T': 0}
        
        if res < 0: # Away Win
            team_records[away]['W'] += 1
            team_records[home]['L'] += 1
        elif res > 0: # Home Win
            team_records[home]['W'] += 1
            team_records[away]['L'] += 1
        elif res == 0: # Tie
            team_records[home]['T'] += 1
            team_records[away]['T'] += 1
            
    # Helper to stringify record
    def fmt_rec(t):
        if t not in team_records: return "0-0"
        r = team_records[t]
        if r['T'] > 0: return f"{r['W']}-{r['L']}-{r['T']}"
        return f"{r['W']}-{r['L']}"
        
    final_merged['away_record'] = final_merged['away_team'].apply(fmt_rec)
    final_merged['home_record'] = final_merged['home_team'].apply(fmt_rec)
    
    final_merged = final_merged.where(pd.notnull(final_merged), None)
    final_merged = final_merged.fillna(-1000)
    
    if is_debug:
        print(f"[DEBUG_PAGE_LOAD] get_enriched_schedule processing took {time.time() - start_op:.3f}s")
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
            print(f"[ERROR] wins_pool_standings missing merge keys: {wins_pool_standings.columns.tolist()}")
        return pd.DataFrame()

    if 'playerId' not in wins_pool_standings.columns:
        if is_debug:
            print(f"[ERROR] wins_pool_standings missing 'playerId'. Cols: {wins_pool_standings.columns.tolist()}")
        return pd.DataFrame()

    if 'playerId' not in players.columns:
        if is_debug:
            print(f"[ERROR] 'players' DF missing 'playerId'. Cols: {players.columns.tolist()}")
        return pd.DataFrame()

    wins_pool_standings = pd.merge(wins_pool_standings, players, on='playerId', how='inner')
    
    # Optional: Attach global team records if games DF is passed
    if games is not None and not games.empty:
        team_records = {}
        played_games = games[(games['season'] == season) & (games['result'].notna()) & (games['result'] != -1000)]
        for _, row in played_games.iterrows():
            away, home, res = row['away_team'], row['home_team'], row['result']
            if away not in team_records: team_records[away] = {'W': 0, 'L': 0, 'T': 0}
            if home not in team_records: team_records[home] = {'W': 0, 'L': 0, 'T': 0}
            if res < 0: team_records[away]['W'] += 1; team_records[home]['L'] += 1
            elif res > 0: team_records[home]['W'] += 1; team_records[away]['L'] += 1
            elif res == 0: team_records[home]['T'] += 1; team_records[away]['T'] += 1
            
        def fmt_rec(t):
            if t not in team_records: return "0-0"
            r = team_records[t]
            if r['T'] > 0: return f"{r['W']}-{r['L']}-{r['T']}"
            return f"{r['W']}-{r['L']}"
            
        wins_pool_standings['global_record'] = wins_pool_standings['team'].apply(fmt_rec)
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
