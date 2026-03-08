import os
import sys
import pathlib
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
import time

NFL_DATA_GITHUB = "https://raw.githubusercontent.com/leesharpe/nfldata/master/data/"
POOL_START_YEAR = 2013  # Earliest season in the pool


def initialize_firebase():
    """Initialize Firebase from FIREBASE_CREDENTIALS env var or local file."""
    if firebase_admin._apps:
        return firestore.client()

    creds_b64 = os.environ.get("FIREBASE_CREDENTIALS")
    if creds_b64:
        import base64, json, tempfile
        decoded = base64.b64decode(creds_b64).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(decoded)
        tmp.close()
        cred = credentials.Certificate(tmp.name)
    else:
        project_root = pathlib.Path(__file__).parent.parent
        creds_path = project_root / "firebase_credentials.json"
        if not creds_path.exists():
            print(f"ERROR: No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
            sys.exit(1)
        cred = credentials.Certificate(str(creds_path))

    firebase_admin.initialize_app(cred)
    return firestore.client()

def batch_upload(db, collection_name, dataframe, id_col=None):
    """Uploads a pandas DataFrame to Firestore in batches to avoid timeout limitations."""
    print(f"Uploading {len(dataframe)} records to {collection_name}...")
    collection_ref = db.collection(collection_name)
    
    # Firestore has a 500 document limit per batch commit
    batch = db.batch()
    count = 0
    total_committed = 0
    
    for _, row in dataframe.iterrows():
        doc_data = row.dropna().to_dict()
        
        # Build deterministic ID if possible, otherwise let Firestore auto-generate
        if id_col and id_col in doc_data:
            doc_id = str(doc_data[id_col])
            doc_ref = collection_ref.document(doc_id)
        else:
            # e.g standings/teams don't inherently have a globally unique integer ID in a single column 
            # so we'll just hash the season/team or use auto-id
            if 'season' in doc_data and 'team' in doc_data:
                doc_id = f"{doc_data['season']}_{doc_data['team']}"
                doc_ref = collection_ref.document(doc_id)
            elif 'game_id' in doc_data:
                doc_id = str(doc_data['game_id'])
                doc_ref = collection_ref.document(doc_id)
            else:
                doc_ref = collection_ref.document()
                
        batch.set(doc_ref, doc_data)
        count += 1
        
        if count == 400: # commit in batches of 400
            batch.commit()
            total_committed += count
            print(f"  ...committed {total_committed} records")
            batch = db.batch()
            count = 0
            
    if count > 0:
        batch.commit()
        total_committed += count
        print(f"  ...committed {total_committed} records")
        
    print(f"Successfully uploaded {collection_name}!")


def sync_nfl_data():
    print("Initializing Firebase...")
    db = initialize_firebase()
    
    print("Downloading CSVs from GitHub (LeeSharpe/nfldata)...")
    
    # We only care about games, teams, and standings. 
    # Fetching them into pandas DataFrames first normalizes datatypes.
    df_standings = pd.read_csv(f"{NFL_DATA_GITHUB}standings.csv")
    df_teams = pd.read_csv(f"{NFL_DATA_GITHUB}teams.csv")
    df_games = pd.read_csv(f"{NFL_DATA_GITHUB}games.csv")
    
    # Filter to pool start year — gets all seasons the pool has run
    df_games_filtered = df_games[df_games['season'] >= POOL_START_YEAR]

    # Upload to Firestore (upserts based on doc_id)
    # Note: nfl_teams is excluded — team rosters don't change during the season.
    #       Run upload_configfiles.py manually if teams need updating.
    batch_upload(db, 'nfl_standings', df_standings)
    batch_upload(db, 'nfl_games', df_games_filtered)

    # Signal web server to invalidate cache
    print("Signaling cache invalidation...")
    db.collection("metadata").document("cache_control").set({
        "last_update": time.time()
    })

    print("Daily sync completed successfully!")


if __name__ == "__main__":
    sync_nfl_data()
