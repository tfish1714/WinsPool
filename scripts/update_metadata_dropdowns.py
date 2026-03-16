import os
import sys
import pandas as pd
from firebase_admin import firestore
import numpy as np

# Add project root to sys.path for services imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.db_service import get_db, save_metadata

def update_dropdown_metadata():
    print("Initializing Firebase optimization utility...")
    db = get_db()
    if not db:
        print("Error: Could not initialize Firestore database.")
        return

    print("Scanning 'draft_results' for unique seasons...")
    results_docs = db.collection('draft_results').select(['season']).get()
    seasons = sorted(list(set([doc.to_dict().get('season') for doc in results_docs if doc.to_dict().get('season')])))
    
    print(f"Found seasons: {seasons}")

    print("Scanning 'nfl_games' for unique seasons, weeks, and progress...")
    # Fetch season, week, result, and game_type to calculate latest_week per season
    games_docs = db.collection('nfl_games').select(['season', 'week', 'result', 'game_type']).get()
    
    games_list = []
    for doc in games_docs:
        games_list.append(doc.to_dict())
    
    games_df = pd.DataFrame(games_list)
    
    weeks_by_season = {}
    latest_week_by_season = {}

    def is_completed(r):
        return pd.notna(r) and (r != -1000)

    for s in seasons:
        s_games = games_df[games_df['season'] == s]
        if s_games.empty:
            weeks_by_season[str(s)] = []
            latest_week_by_season[str(s)] = 1
            continue
            
        # Regular Season weeks only
        reg_games = s_games[s_games['game_type'] == 'REG'] if 'game_type' in s_games.columns else s_games
        weeks = sorted(reg_games['week'].dropna().astype(int).unique().tolist())
        weeks_by_season[str(s)] = weeks
        
        # Calculate latest_week (Logic from data_service.get_latest_week_for_year)
        if reg_games.empty:
            latest_week_by_season[str(s)] = 1
            continue

        week_stats = (
            reg_games.groupby('week')
            .apply(lambda g: pd.Series({
                'total': len(g),
                'done': g['result'].apply(is_completed).sum() if 'result' in g.columns else 0
            }))
            .reset_index()
        )
        
        week_stats = week_stats[week_stats['done'] > 0]
        if week_stats.empty:
            latest_week_by_season[str(s)] = 1
        else:
            # Find highest week where done < total (in-progress)
            in_progress = week_stats[week_stats['done'] < week_stats['total']]
            if not in_progress.empty:
                latest_week_by_season[str(s)] = int(in_progress['week'].max())
            else:
                # All weeks done, pick highest completed
                latest_week_by_season[str(s)] = int(week_stats['week'].max())

    formatted_weeks = {str(s): [int(w) for w in sorted(list(wks))] for s, wks in weeks_by_season.items()}
    latest_week_by_season = {str(s): int(w) for s, w in latest_week_by_season.items()}
    latest_season = int(max(seasons)) if seasons else None
    
    # Identify finalized seasons that have a physical bundle file on disk
    bundled_seasons = []
    bundle_dir = os.path.join("static", "bundles")
    for s in seasons:
        if int(s) < latest_season:
            bundle_path = os.path.join(bundle_dir, f"{s}_season_bundle.txt")
            if os.path.exists(bundle_path):
                bundled_seasons.append(int(s))
    
    metadata_payload = {
        "available_seasons": sorted([int(s) for s in seasons], reverse=True),
        "latest_season": latest_season,
        "bundled_seasons": bundled_seasons,
        "weeks_by_season": formatted_weeks,
        "latest_week_by_season": latest_week_by_season,
        "last_updated": firestore.SERVER_TIMESTAMP
    }

    print("Writing consolidated metadata to 'metadata/dropdown_config'...")
    save_metadata("dropdown_config", metadata_payload)
    
    # FORCE local save even if USE_LOCAL_DATA wasn't set to True during the script run
    # This ensures the local dev environment gets the data.
    if os.environ.get("USE_LOCAL_DATA", "False").lower() != "true":
        print("Mirroring metadata to local .local_db/metadata.pkl for dev mode...")
        os.environ["USE_LOCAL_DATA"] = "True"
        save_metadata("dropdown_config", metadata_payload)
        os.environ["USE_LOCAL_DATA"] = "False"

    print("Successfully updated dropdown metadata with latest season/week info!")

if __name__ == "__main__":
    update_dropdown_metadata()
