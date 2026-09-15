import os
import sys
import pathlib
import pandas as pd
import numpy as np
import firebase_admin
from firebase_admin import credentials, firestore

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


def batch_upload(db, collection_name, dataframe, id_col=None, diff_before_write=False) -> int:
    """Upload a DataFrame to Firestore in batches of 400.

    When diff_before_write=True, reads the current stored value for every
    row that has a derivable, stable doc_id and skips the .set() call for
    any row whose value is already identical -- trades N writes for N reads
    when most rows are unchanged, which is the common case for a job that
    reruns the same mostly-final data on a fixed schedule.

    Returns the number of documents actually written.
    """
    print(f"Uploading {len(dataframe)} records to {collection_name}...")
    collection_ref = db.collection(collection_name)

    rows_with_ids = []
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
        rows_with_ids.append((doc_id, doc_data))

    if diff_before_write:
        diffable = [(doc_id, doc_data) for doc_id, doc_data in rows_with_ids if doc_id]
        existing = {}
        if diffable:
            refs = [collection_ref.document(doc_id) for doc_id, _ in diffable]
            for snap in db.get_all(refs):
                if snap.exists:
                    existing[snap.id] = snap.to_dict()
        filtered = []
        for doc_id, doc_data in rows_with_ids:
            if doc_id and existing.get(doc_id) == doc_data:
                continue  # unchanged -- skip the write
            filtered.append((doc_id, doc_data))
        rows_with_ids = filtered

    batch = db.batch()
    count = 0
    total_committed = 0
    for doc_id, doc_data in rows_with_ids:
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

    print(f"Successfully uploaded {collection_name}! ({total_committed} of {len(dataframe)} records written)")
    return total_committed


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

    columns = ["season", "team", "wins", "losses", "ties", "scored", "allowed", "net", "pct"]
    if not records:
        # No completed REG games in the input at all (e.g. sync_live_scores.py
        # scoped to the active season alone, run during the preseason window
        # before that season's Week 1 has finished) -- pd.DataFrame([]) has no
        # columns, so sort_values(["season", "team"]) below would raise
        # KeyError. An empty-but-correctly-shaped frame lets every caller's
        # existing "if df.empty" handling work unchanged.
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(records)[columns].sort_values(["season", "team"]).reset_index(drop=True)


def sync_nfl_data(seasons: tuple = None):
    """Sync nfl_games/nfl_standings to Firestore.

    seasons=None (the normal daily run): scopes to the active season only
    -- historical seasons don't change absent a manual correction, so the
    daily job has no routine reason to rewrite them. Pass an explicit
    (min, max) tuple to force a full-range backfill/correction.
    """
    # services.db_service.get_db() (used below by signal_data_update())
    # returns None whenever USE_LOCAL_DATA is true in the environment,
    # regardless of this script's own separate initialize_firebase()
    # connection -- see CLAUDE.md's "any script that writes to Firestore
    # must force USE_LOCAL_DATA=False" gotcha.
    os.environ["USE_LOCAL_DATA"] = "False"

    print("Initializing Firebase...")
    db = initialize_firebase()

    print("Loading nflverse schedule from rawdata/schedules/games.csv...")
    df_games = load_games()
    print(f"  {len(df_games)} games loaded ({int(df_games['season'].min())}–{int(df_games['season'].max())})")

    active_season = int(df_games["season"].max())
    if seasons is not None:
        lo, hi = seasons
        scoped_games = df_games[(df_games["season"] >= lo) & (df_games["season"] <= hi)].copy()
        print(f"  Scoped to explicit season range {lo}-{hi} ({len(scoped_games)} games)")
    else:
        lo, hi = active_season, active_season
        scoped_games = df_games[df_games["season"] == active_season].copy()
        print(f"  Scoped to active season {active_season} ({len(scoped_games)} games)")

    print("Computing standings from game results...")
    df_standings = compute_standings(scoped_games)

    standings_written = batch_upload(db, "nfl_standings", df_standings, diff_before_write=True)
    games_written = batch_upload(db, "nfl_games", scoped_games, diff_before_write=True)

    if standings_written or games_written:
        print("Signaling cache invalidation...")
        from services.db_service import signal_data_update
        from services.cache_service import DOMAIN_ACTIVE, DOMAIN_HISTORICAL
        # DOMAIN_ACTIVE/DOMAIN_HISTORICAL route by season == active_season vs.
        # not (see data_service.py's _bootstrap_games_standings()), so a range
        # spanning both must signal both -- an explicit --seasons range that
        # touches the active season but also extends past it (e.g. a backfill
        # covering 2013-2026 while 2026 is active) must not under-signal
        # historical the way a single either/or domain choice would.
        if lo <= active_season <= hi:
            signal_data_update(DOMAIN_ACTIVE)
            if not (lo == hi == active_season):
                signal_data_update(DOMAIN_HISTORICAL)
        else:
            signal_data_update(DOMAIN_HISTORICAL)
    else:
        print("No changes -- skipping cache invalidation signal.")

    print("Daily sync completed successfully!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, nargs=2, metavar=("MIN", "MAX"),
                         help="Force a full-range sync (manual backfill/correction) instead of active-season-only")
    args = parser.parse_args()
    sync_nfl_data(seasons=tuple(args.seasons) if args.seasons else None)
