import os
import sys
import pathlib
import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore
import time

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
POOL_START_YEAR = 2013  # Earliest season in the pool


def initialize_firebase():
    """Initialize Firebase from FIREBASE_CREDENTIALS env var or local file."""
    if firebase_admin._apps:
        return firestore.client()

    creds_b64 = os.environ.get("FIREBASE_CREDENTIALS")
    if creds_b64:
        import base64, tempfile
        decoded = base64.b64decode(creds_b64).decode("utf-8")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(decoded)
        tmp.close()
        cred = credentials.Certificate(tmp.name)
    else:
        creds_path = pathlib.Path(__file__).parent.parent / "firebase_credentials.json"
        if not creds_path.exists():
            print("ERROR: No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
            sys.exit(1)
        cred = credentials.Certificate(str(creds_path))

    firebase_admin.initialize_app(cred)
    return firestore.client()


def batch_upload(db, collection_name, dataframe, id_col=None):
    """Upload a DataFrame to Firestore in batches of 400."""
    print(f"Uploading {len(dataframe)} records to {collection_name}...")
    collection_ref = db.collection(collection_name)
    batch = db.batch()
    count = 0
    total_committed = 0

    for _, row in dataframe.iterrows():
        doc_data = row.dropna().to_dict()

        if id_col and id_col in doc_data:
            doc_id = str(doc_data[id_col])
        elif "season" in doc_data and "team" in doc_data:
            doc_id = f"{doc_data['season']}_{doc_data['team']}"
        elif "game_id" in doc_data:
            doc_id = str(doc_data["game_id"])
        else:
            doc_id = None

        doc_ref = collection_ref.document(doc_id) if doc_id else collection_ref.document()
        batch.set(doc_ref, doc_data)
        count += 1

        if count == 400:
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


def load_games() -> pd.DataFrame:
    """Load nflverse schedule as the single source of truth for game data."""
    path = RAWDATA_DIR / "schedules" / "games.csv"
    if not path.exists():
        print(f"ERROR: {path} not found. Run sync_nflverse_data.py first.")
        sys.exit(1)
    df = pd.read_csv(path, low_memory=False)
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    return df[df["season"] >= POOL_START_YEAR].copy()


def compute_standings(games: pd.DataFrame) -> pd.DataFrame:
    """Compute per-team season standings from completed regular-season games.

    Produces: season, team, wins, losses, ties, scored, allowed, net, pct
    """
    reg = games[
        (games["game_type"] == "REG") &
        games["result"].notna() &
        games["home_score"].notna() &
        games["away_score"].notna()
    ].copy()

    records = []
    for (season, team), _ in (
        pd.concat([
            reg[["season", "home_team"]].rename(columns={"home_team": "team"}),
            reg[["season", "away_team"]].rename(columns={"away_team": "team"}),
        ])
        .drop_duplicates()
        .groupby(["season", "team"])
    ):
        home = reg[(reg["season"] == season) & (reg["home_team"] == team)]
        away = reg[(reg["season"] == season) & (reg["away_team"] == team)]

        wins   = int((home["result"] > 0).sum()  + (away["result"] < 0).sum())
        losses = int((home["result"] < 0).sum()  + (away["result"] > 0).sum())
        ties   = int((home["result"] == 0).sum() + (away["result"] == 0).sum())
        scored  = float(home["home_score"].sum() + away["away_score"].sum())
        allowed = float(home["away_score"].sum() + away["home_score"].sum())
        games_played = wins + losses + ties
        pct = round((wins + 0.5 * ties) / games_played, 6) if games_played else 0.0

        records.append({
            "season":  int(season),
            "team":    team,
            "wins":    wins,
            "losses":  losses,
            "ties":    ties,
            "scored":  scored,
            "allowed": allowed,
            "net":     scored - allowed,
            "pct":     pct,
        })

    return pd.DataFrame(records).sort_values(["season", "team"]).reset_index(drop=True)


def sync_nfl_data():
    print("Initializing Firebase...")
    db = initialize_firebase()

    print("Loading nflverse schedule from rawdata/schedules/games.csv...")
    df_games = load_games()
    print(f"  {len(df_games)} games loaded ({int(df_games['season'].min())}–{int(df_games['season'].max())})")

    print("Computing standings from game results...")
    df_standings = compute_standings(df_games)
    seasons_with_standings = sorted(df_standings["season"].unique())
    print(f"  {len(df_standings)} team-seasons computed ({seasons_with_standings[0]}–{seasons_with_standings[-1]})")

    batch_upload(db, "nfl_standings", df_standings)
    batch_upload(db, "nfl_games", df_games)

    print("Signaling cache invalidation...")
    db.collection("metadata").document("cache_control").set({"last_update": time.time()})

    print("Daily sync completed successfully!")


if __name__ == "__main__":
    sync_nfl_data()
