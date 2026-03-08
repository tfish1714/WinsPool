import os
import sys
import argparse
import pandas as pd
from datetime import datetime

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.data_service import load_data
from services.analysis_service import get_enriched_schedule
from services.ai_service import generate_weekly_summary, get_recap_prompt
from services.email_service import send_weekly_recap_email
from services.db_service import save_weekly_recap

def extract_weekly_data(year, week):
    """
    Fetches results for the given week and identifies winners/bad beats, 
    while also including overall season-to-date records.
    """
    standings, _, games, players, _, draft_results, _ = load_data(year=year)
    schedule = get_enriched_schedule(games, draft_results, players, year)
    
    if schedule.empty:
        print(f"No schedule data found for {year}.")
        return None

    # 1. Calculate Overall Season Wins (up to and including this week)
    # We use all completed games up to the target week
    season_to_date = schedule[schedule['week'] <= week]
    played_to_date = season_to_date[(season_to_date['result'] != -1000) & (season_to_date['result'].notna())]
    
    overall_wins = {}
    for _, row in played_to_date.iterrows():
        # result > 0: Home Wins, result < 0: Away Wins
        win_pid = row['playerId_home_draft'] if row['result'] > 0 else row['playerId']
        if pd.notna(win_pid) and win_pid != -1000:
            overall_wins[win_pid] = overall_wins.get(win_pid, 0) + 1

    # 2. Extract specific week stats (for bad beats and weekly roast)
    weekly_games = schedule[schedule['week'] == week]
    if weekly_games.empty:
        print(f"No games found for week {week} in {year}.")
        return None

    player_stats = {}
    
    # Map player IDs for the summary
    pid_to_name = dict(zip(players['playerId'], players['fullName']))
    
    for _, row in weekly_games.iterrows():
        # Skip unplayed games
        if row['result'] == -1000 or row['result'] is None:
            continue
            
        a_pid = row['playerId']
        h_pid = row['playerId_home_draft']
        
        # Skip games where one side is undrafted or unknown
        if a_pid == -1000 or h_pid == -1000 or pd.isna(a_pid) or pd.isna(h_pid):
            continue
            
        if h_pid not in player_stats:
            player_stats[h_pid] = {'wins': 0, 'losses': 0, 'bad_beats': []}
        if a_pid not in player_stats:
            player_stats[a_pid] = {'wins': 0, 'losses': 0, 'bad_beats': []}

        home_score = row['home_score']
        away_score = row['away_score']
        margin = abs(home_score - away_score)
        
        # Home Win
        if row['result'] > 0:
            player_stats[h_pid]['wins'] += 1
            player_stats[a_pid]['losses'] += 1
            if margin <= 3:
                player_stats[a_pid]['bad_beats'].append(f"Lost by {margin} ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
            elif away_score >= 30:
                player_stats[a_pid]['bad_beats'].append(f"Scored {away_score} and still lost ({row['away_team']} {away_score}-{home_score} {row['home_team']})")
        # Away Win
        else:
            player_stats[a_pid]['wins'] += 1
            player_stats[h_pid]['losses'] += 1
            if margin <= 3:
                player_stats[h_pid]['bad_beats'].append(f"Lost by {margin} ({row['home_team']} {home_score}-{away_score} {row['away_team']})")
            elif home_score >= 30:
                player_stats[h_pid]['bad_beats'].append(f"Scored {home_score} and still lost ({row['home_team']} {home_score}-{away_score} {row['away_team']})")

    # 3. Build text for Gemini
    data_summary = f"NFL WEEK {week} RESULTS ({year})\n"
    data_summary += "---------------------------------\n\n"
    
    for pid, name in pid_to_name.items():
        # Filter to only players who had games this week OR have wins on record
        if pid not in player_stats and pid not in overall_wins:
            continue
            
        stats = player_stats.get(pid, {'wins': 0, 'losses': 0, 'bad_beats': []})
        total_wins = overall_wins.get(pid, 0)
        
        data_summary += f"PLAYER: {name}\n"
        data_summary += f"WEEKLY RESULT: {stats['wins']}-{stats['losses']}\n"
        data_summary += f"CUMULATIVE SEASON WINS (UP TO WEEK {week}): {total_wins}\n"
        
        if stats['bad_beats']:
            data_summary += "BAD BEATS THIS WEEK:\n"
            for beat in stats['bad_beats']:
                data_summary += f" - {beat}\n"
        data_summary += "\n"
        
    return data_summary, list(players['email'].dropna().unique())

def main():
    parser = argparse.ArgumentParser(description="Generate AI Weekly Summary")
    parser.add_argument("--year", type=int, required=True, help="Season year")
    parser.add_argument("--week", type=int, required=True, help="NFL Week number")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without saving/emailing")
    parser.add_argument("--show-prompt", action="store_true", help="Show the full prompt sent to AI for testing")
    args = parser.parse_args()

    print(f"--- Generating Recap for {args.year} Week {args.week} ---")
    
    data_summary, emails = extract_weekly_data(args.year, args.week)
    if not data_summary:
        return

    if args.show_prompt:
        full_prompt = get_recap_prompt(data_summary)
        print("\n" + "!"*50)
        print("FULL AI PROMPT (FOR MANUAL TESTING):")
        print("!"*50)
        print(full_prompt)
        print("!"*50 + "\n")
        
        confirm_prompt = input("Proceed to call Gemini AI with this data? (y/n): ")
        if confirm_prompt.lower() != 'y':
            print("AI call aborted.")
            return

    print("Extracting data and calling Gemini...")
    summary_text = generate_weekly_summary(data_summary)
    
    print("\n" + "="*50)
    print("PROPOSED SUMMARY:")
    print("="*50)
    print(summary_text)
    print("="*50 + "\n")

    if args.dry_run:
        print("Dry run complete. No actions taken.")
        return

    confirm = input("Approve this summary and broadcast? (y/n): ")
    if confirm.lower() != 'y':
        print("Broadcast cancelled.")
        return

    print("Saving to Firestore...")
    save_weekly_recap(args.year, args.week, summary_text)
    
    if emails:
        print(f"Sending emails to {len(emails)} players...")
        # Create a simple HTML wrapper
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
                <div style="background-color: #ffffff; padding: 20px; border-radius: 10px; box-shadow: 0 0 10px rgba(0,0,0,0.1);">
                    <h2 style="color: #333;">🏈 Weekly Recap: NFL Week {args.week}</h2>
                    <div style="white-space: pre-wrap; color: #555; line-height: 1.6;">
                        {summary_text}
                    </div>
                    <p style="margin-top: 20px; font-size: 0.8em; color: #888;">
                        This recap was generated by Gemini AI for the Wins Pool. 
                        View the full standings at <a href="https://winspool.web.app">winspool.web.app</a>
                    </p>
                </div>
            </body>
        </html>
        """
        success = send_weekly_recap_email(emails, f"Week {args.week} Recap - Wins Pool", html_body)
        if success:
            print("Emails sent successfully!")
        else:
            print("Failed to send some or all emails.")
    else:
        print("No valid player emails found to notify.")

    print("Done!")

if __name__ == "__main__":
    main()
