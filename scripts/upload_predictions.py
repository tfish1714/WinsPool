import os
import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import pathlib

# Initialize Firebase
cred_path = os.environ.get("FIREBASE_CREDENTIALS", "firebase_credentials.json")
if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

def upload_predictions(excel_path: str):
    print(f"Reading {excel_path}...")
    df = pd.read_excel(excel_path)
    
    # We saw these columns: ['Team', 'BR', 'FPI', 'SI', 'ESPN', 'CBS', 'Athle', 'PFF', 'NFL', 'O/U', 'Clay', 'Unnamed: 11']
    # 'Unnamed: 11' is season.
    # 'Team' is full name like 'Dallas Cowboys'.
    
    if 'Unnamed: 11' in df.columns:
        df = df.rename(columns={'Unnamed: 11': 'season'})
    
    # Filter out rows without a season
    df = df.dropna(subset=['season'])
    df['season'] = df['season'].astype(int)
    
    # Load team mapping from current standings or teams collection
    # For now, let's use a robust heuristic or fetch from Firestore
    print("Fetching team mapping...")
    teams_ref = db.collection("nfl_teams").stream()
    # Map name -> abbr
    team_map = {}
    for doc in teams_ref:
        t = doc.to_dict()
        # nfl_teams usually has 'team' (abbr) and 'team_name' (full)
        team_map[t.get('team_name', '').lower()] = t.get('team')
        # Also map city names and common variations
        # (e.g. 'Dallas Cowboys' -> 'DAL', 'Cowboys' -> 'DAL')
        full_name = t.get('team_name', '').lower()
        if ' ' in full_name:
            city = full_name.split(' ')[0]
            nickname = full_name.split(' ')[-1]
            team_map[city] = t.get('team')
            team_map[nickname] = t.get('team')

    # Hardcoded overrides for common edge cases
    overrides = {
        'dallas cowboys': 'DAL',
        'philadelphia eagles': 'PHI',
        'new york giants': 'NYG',
        'washington commanders': 'WAS',
        'washington redskins': 'WAS',
        'washington football team': 'WAS',
        'san francisco 49ers': 'SF',
        'los angeles rams': 'LA',
        'seattle seahawks': 'SEA',
        'arizona cardinals': 'ARI',
        'green bay packers': 'GB',
        'detroit lions': 'DET',
        'minnesota vikings': 'MIN',
        'chicago bears': 'CHI',
        'tampa bay buccaneers': 'TB',
        'new orleans saints': 'NO',
        'atlanta falcons': 'ATL',
        'carolina panthers': 'CAR',
        'kansas city chiefs': 'KC',
        'las vegas raiders': 'LV',
        'oakland raiders': 'LV',
        'denver broncos': 'DEN',
        'los angeles chargers': 'LAC',
        'san diego chargers': 'LAC',
        'buffalo bills': 'BUF',
        'miami dolphins': 'MIA',
        'new york jets': 'NYJ',
        'new england patriots': 'NE',
        'cincinnati bengals': 'CIN',
        'baltimore ravens': 'BAL',
        'pittburgh steelers': 'PIT', # Common typo
        'pittsburgh steelers': 'PIT',
        'cleveland browns': 'CLE',
        'houston texans': 'HOU',
        'jacksonville jaguars': 'JAX',
        'indianapolis colts': 'IND',
        'tennessee titans': 'TEN'
    }
    team_map.update(overrides)

    source_cols = ['BR', 'FPI', 'SI', 'ESPN', 'CBS', 'Athle', 'PFF', 'NFL', 'O/U', 'Clay']
    
    count = 0
    for _, row in df.iterrows():
        name = str(row['Team']).lower().strip()
        abbr = team_map.get(name)
        
        if not abbr:
            # Try partial match
            for k, v in team_map.items():
                if k in name or name in k:
                    abbr = v
                    break
        
        if not abbr:
            print(f"⚠️ Could not find abbreviation for team: {row['Team']}")
            continue
            
        season = int(row['season'])
        
        # Extract sources that have values
        sources = {}
        values = []
        for src in source_cols:
            if src in row and pd.notna(row[src]):
                val = float(row[src])
                sources[src] = val
                values.append(val)
        
        if not values:
            continue
            
        avg = np.mean(values)
        std = np.std(values) if len(values) > 1 else 0.0
        
        doc_id = f"{season}_{abbr}"
        data = {
            "season": season,
            "team": abbr,
            "sources": sources,
            "projected_wins": round(avg, 2), # Maintain compatibility
            "std_dev": round(std, 2)
        }
        
        db.collection("preseason_predictions").document(doc_id).set(data)
        count += 1
        
    print(f"✅ Uploaded {count} predictions.")

if __name__ == "__main__":
    path = "debug/predictions.xlsx"
    if os.path.exists(path):
        upload_predictions(path)
    else:
        print(f"❌ File not found: {path}")
