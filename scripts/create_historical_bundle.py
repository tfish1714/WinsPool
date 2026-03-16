# scripts/create_historical_bundle.py
import os
import sys
import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_bundle import FirestoreBundle

from google.cloud.firestore_bundle import FirestoreBundle
import json

# Add root directory to path for service imports if needed
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.data_service import get_season_progress, get_latest_week_for_year
from services.analysis_service import (
    get_enriched_schedule, player_winsbyWeek, 
    calculate_playoff_race, player_winlossmatrix,
    calculate_wins_pool_standings
)

def create_season_bundle(year: int):
    """
    Queries Firestore for all data related to a specific season and packages it into a Data Bundle.
    """
    print(f"[*] Initializing Firestore for season {year} bundle...")
    
    # Initialize Firebase Admin if not already
    if not firebase_admin._apps:
        # Use credentials from file or environment
        cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_credentials.json")
        if not os.path.exists(cred_path):
             print(f"[!] Error: Credentials not found at {cred_path}")
             return
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    
    db = firestore.client()
    
    # Define queries for the bundle
    # Note: Using collection names from database.md
    # Using FieldFilter to avoid "Detected filter using positional arguments" warning
    queries = {
        "nfl_games": db.collection("nfl_games").where(filter=FieldFilter("season", "==", year)),
        "draft_results": db.collection("draft_results").where(filter=FieldFilter("season", "==", year)),
        "nfl_standings": db.collection("nfl_standings").where(filter=FieldFilter("season", "==", year)),
        "draft_order": db.collection("draft_order").where(filter=FieldFilter("season", "==", year)),
        "draft_order_rules": db.collection("draft_order_rules").where(filter=FieldFilter("season", "==", year)),
        "preseason_predictions": db.collection("preseason_predictions").where(filter=FieldFilter("season", "==", year)),
        "weekly_recaps": db.collection("weekly_recaps").where(filter=FieldFilter("year", "==", year)),
        "nfl_teams": db.collection("nfl_teams").where(filter=FieldFilter("season", "==", year))
    }
    
    # Create the bundle using the direct FirestoreBundle class
    bundle_name = f"season_{year}_bundle"
    print(f"[*] Creating bundle: {bundle_name}")
    builder = FirestoreBundle(bundle_name)
    
    count = 0
    for name, query in queries.items():
        print(f"[*] Adding query: {name} for year {year}")
        # Add the query to the bundle with a name/ID for the frontend to reference
        builder.add_named_query(name, query)
        
        # We also need to add the actual documents to the bundle so they are cached
        docs = query.get()
        for doc in docs:
            builder.add_document(doc)
            count += 1
            
    print(f"[*] Bundled {count} documents.")
    
    # Build the bundle string
    bundle_content = builder.build()
    
    # Ensure output directory exists
    os.makedirs("static/bundles", exist_ok=True)
    output_path = f"static/bundles/{year}_season_bundle.txt"
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(bundle_content)
        
    print(f"[+] Success! Firestore Bundle saved to {output_path}")

    # --- Step 2: Pre-compute Analysis Data for zero-cost server rendering ---
    print(f"[*] Pre-computing analysis data for {year}...")
    try:
        # We need to ensure load_data has hit the DB for this run
        os.environ["USE_LOCAL_DATA"] = "False"
        
        # Determine latest week for the season
        _, _, games, players, _, draft_results, _ = firestore_admin_load_data(year)
        lw = get_latest_week_for_year(games, year)
        
        # 1. Standings & Progress Data
        progress = get_season_progress(year, lw)
        
        # 2. Enriched Schedule (Full Season)
        schedule = get_enriched_schedule(games, draft_results, players, year)
        
        # 3. Week by Week Record
        # Need ranked players from standings for column sorting
        standings_ranked = calculate_wins_pool_standings(None, draft_results, players, year, games)
        ranked_names = standings_ranked["fullName"].tolist() if not standings_ranked.empty else None
        wbw = player_winsbyWeek(schedule, sorted_players=ranked_names)
        
        # 4. Playoff Race
        race = calculate_playoff_race(schedule, None) # Standings DF not strictly needed by race logic
        
        # 5. H2H Matrix
        h2h = player_winlossmatrix(schedule)
        
        analysis_payload = {
            "standings_progress": progress,
            "full_schedule": schedule.to_dict(orient="records") if not schedule.empty else [],
            "week_by_week_html": wbw.to_html(classes="table table-striped", index=True, border=0) if not wbw.empty else "",
            "playoff_race": race,
            "h2h_html": h2h.to_html(classes="table table-striped", border=0) if not h2h.empty else ""
        }
        
        analysis_path = f"static/bundles/{year}_analysis.json"
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis_payload, f, default=str)
        print(f"[+] Success! Pre-computed analysis saved to {analysis_path}")
        
    except Exception as e:
        print(f"[!] Warning: Failed to pre-compute analysis: {e}")

def firestore_admin_load_data(year: int):
    """Internal helper to load data using the admin SDK for pre-computation."""
    from services.db_service import get_collection_df
    import pandas as pd
    standings = get_collection_df("nfl_standings", [("season", "==", year)])
    games = get_collection_df("nfl_games", [("season", "==", year)])
    players = get_collection_df("players")
    draft_results = get_collection_df("draft_results", [("season", "==", year)])
    return (
        standings if standings is not None else pd.DataFrame(),
        pd.DataFrame(), # teams
        games if games is not None else pd.DataFrame(),
        players if players is not None else pd.DataFrame(),
        pd.DataFrame(), # draft_order
        draft_results if draft_results is not None else pd.DataFrame(),
        pd.DataFrame()  # draft_order_rules
    )

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python scripts/create_historical_bundle.py 2024          (Single Year)")
        print("  python scripts/create_historical_bundle.py 2013-2025     (Range)")
        print("  python scripts/create_historical_bundle.py 2020,2021     (List)")
        sys.exit(1)
        
    arg = sys.argv[1]
    target_years = []
    
    if "-" in arg:
        try:
            start, end = map(int, arg.split("-"))
            target_years = list(range(start, end + 1))
        except ValueError:
            print("Error: Invalid range format. Use YYYY-YYYY (e.g. 2013-2025).")
            sys.exit(1)
    elif "," in arg:
        try:
            target_years = [int(y) for y in arg.split(",")]
        except ValueError:
            print("Error: Invalid list format. Use YYYY,YYYY.")
            sys.exit(1)
    else:
        try:
            target_years = [int(arg)]
        except ValueError:
            print("Error: Year must be an integer.")
            sys.exit(1)

    print(f"[*] Starting bulk bundle generation for: {target_years}")
    
    success_count = 0
    fail_count = 0
    
    for y in target_years:
        try:
            create_season_bundle(y)
            success_count += 1
        except Exception as e:
            print(f"[!] Critical Error for season {y}: {e}")
            fail_count += 1
            
    print(f"\n[***] Bulk Operation Complete!")
    print(f"[***] Success: {success_count}")
    print(f"[***] Failed:  {fail_count}")
    
    if success_count > 0:
        print("\n[*] Don't forget to run metadata update:")
        print("    python scripts/update_metadata_dropdowns.py")

