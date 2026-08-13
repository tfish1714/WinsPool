import pandas as pd
from services.constants import UNDRAFTED_SENTINEL
from services.data_service import load_data, get_season_projection
from services.analysis_service import get_enriched_schedule
from services.ai_service import generate_weekly_summary, get_recap_prompt
from services.db_service import save_weekly_recap

def extract_weekly_data(year, week):
    """
    Fetches results for the given week and identifies winners/bad beats, 
    while also including overall season-to-date records.
    
    Beat Definitions:
      Bad Beat: Lost by <= 3 points OR scored >= 30 and still lost.
      Good Beat: Won by >= 17 points OR scored < 14 and still won.
    """
    standings, _, games, players, _, draft_results, _ = load_data(year=year)
    schedule = get_enriched_schedule(games, draft_results, players, year)
    
    if schedule.empty:
        return None, []

    # 1. Calculate Overall Season Wins (up to and including this week)
    season_to_date = schedule[schedule['week'] <= week]
    played_to_date = season_to_date[(season_to_date['result'] != UNDRAFTED_SENTINEL) & (season_to_date['result'].notna())]
    
    overall_wins = {}
    for _, row in played_to_date.iterrows():
        win_pid = row['playerId_home_draft'] if row['result'] > 0 else row['playerId']
        if pd.notna(win_pid) and win_pid != UNDRAFTED_SENTINEL:
            overall_wins[win_pid] = overall_wins.get(win_pid, 0) + 1

    # 2. Extract specific week stats
    weekly_games = schedule[schedule['week'] == week]
    if weekly_games.empty:
        return None, []

    player_stats = {}
    pid_to_name = dict(zip(players['playerId'], players['fullName']))
    
    for _, row in weekly_games.iterrows():
        if row['result'] == UNDRAFTED_SENTINEL or row['result'] is None:
            continue
            
        a_pid = row['playerId']
        h_pid = row['playerId_home_draft']
        
        if a_pid == UNDRAFTED_SENTINEL or h_pid == UNDRAFTED_SENTINEL or pd.isna(a_pid) or pd.isna(h_pid):
            continue
            
        if h_pid not in player_stats:
            player_stats[h_pid] = {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []}
        if a_pid not in player_stats:
            player_stats[a_pid] = {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []}

        home_score = row['home_score']
        away_score = row['away_score']
        margin = abs(home_score - away_score)
        
        if row['result'] > 0: # Home Win
            player_stats[h_pid]['wins'] += 1
            player_stats[a_pid]['losses'] += 1
            # Bad Beats for Away
            if margin <= 3:
                player_stats[a_pid]['bad_beats'].append(f"Lost a nail-biter by {margin} ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
                player_stats[h_pid]['notable_wins'].append(f"Survived a close one by {margin} ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
            elif away_score >= 30:
                player_stats[a_pid]['bad_beats'].append(f"Scored {away_score} and still lost ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
            
            # Notable Wins for Home
            if margin >= 17:
                player_stats[h_pid]['notable_wins'].append(f"Absolute blowout! Won by {margin} ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
            elif home_score < 14 and row['result'] > 0:
                player_stats[h_pid]['notable_wins'].append(f"Ugly win but counts! Scored only {home_score} and escaped ({row['home_team']} {home_score}-{away_score} {row['away_team']})")

        else: # Away Win
            player_stats[a_pid]['wins'] += 1
            player_stats[h_pid]['losses'] += 1
            # Bad Beats for Home
            if margin <= 3:
                player_stats[h_pid]['bad_beats'].append(f"Heartbreaker! Lost by {margin} ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
                player_stats[a_pid]['notable_wins'].append(f"Stole a win by {margin} ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
            elif home_score >= 30:
                player_stats[h_pid]['bad_beats'].append(f"Scored {home_score} and still lost ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
            
            # Notable Wins for Away
            if margin >= 17:
                player_stats[a_pid]['notable_wins'].append(f"Dominant performance! Won by {margin} ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
            elif away_score < 14 and row['result'] < 0:
                player_stats[a_pid]['notable_wins'].append(f"Scrappy win! Scored only {away_score} and still won ({row['away_team']} {away_score}-{home_score} {row['home_team']})")

    # 3. Build text for Gemini
    data_summary = f"NFL WEEK {week} RESULTS ({year})\n"
    data_summary += "---------------------------------\n\n"
    
    for pid, name in pid_to_name.items():
        if pid not in player_stats and pid not in overall_wins:
            continue
            
        stats = player_stats.get(pid, {'wins': 0, 'losses': 0, 'bad_beats': [], 'notable_wins': []})
        total_wins = overall_wins.get(pid, 0)
        
        data_summary += f"PLAYER: {name}\n"
        data_summary += f"WEEKLY RESULT: {stats['wins']}-{stats['losses']}\n"
        data_summary += f"CUMULATIVE SEASON WINS (UP TO WEEK {week}): {total_wins}\n"
        
        if stats['notable_wins']:
            data_summary += "NOTABLE WINS THIS WEEK (GOOD BEATS):\n"
            for win in stats['notable_wins']:
                data_summary += f" + {win}\n"
        
        if stats['bad_beats']:
            data_summary += "BAD BEATS THIS WEEK:\n"
            for beat in stats['bad_beats']:
                data_summary += f" - {beat}\n"
        data_summary += "\n"
        
    return data_summary, list(players['email'].dropna().unique())

def extract_draft_data(year):
    """
    Builds the context string for the preseason Draft AI recap.
    It includes drafted teams per player, consensus Win Totals (if available), 
    and general season context.
    """
    
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data(year=year)
    preds = get_season_projection(year)
    
    if draft_results.empty:
        return None, []
    
    # Filter draft results for the requested season
    draft_results = draft_results[draft_results['season'] == year]
    if draft_results.empty:
        return None, []

    pid_to_name = dict(zip(players['playerId'], players['fullName']))
    
    data_summary = f"NFL {year} PRESEASON DRAFT RECAP\n"
    data_summary += "---------------------------------\n\n"
    
    # Group drafted teams by Player
    player_rosters = {}
    for _, row in draft_results.iterrows():
        pid = row['playerId']
        team = row['team']
        if pid not in player_rosters:
            player_rosters[pid] = []
        player_rosters[pid].append(team)
        
    for pid, name in pid_to_name.items():
        if pid not in player_rosters:
            continue
            
        roster = player_rosters[pid]
        data_summary += f"PLAYER: {name}\n"
        
        # Build roster string with projections and calculate total
        formatted_roster = []
        proj_wins = 0.0
        for team in roster:
            if team in preds and preds[team].get('wins') is not None:
                val = float(preds[team]['wins'])
                formatted_roster.append(f"{team} ({val})")
                proj_wins += val
            else:
                formatted_roster.append(team)

        data_summary += f"DRAFTED TEAMS: {', '.join(formatted_roster)}\n"
                
        if proj_wins > 0:
            data_summary += f"ROSTER PROJECTED WINS (Average): {proj_wins:.1f}\n"
            
        data_summary += "\n"
        
    return data_summary, list(players['email'].dropna().unique())
