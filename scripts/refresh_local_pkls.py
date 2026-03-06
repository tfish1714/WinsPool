"""
refresh_local_pkls.py — Rebuild local .pkl cache from Firestore.

Run this after any Firebase migration to get an up-to-date local cache
that the web server reads via USE_LOCAL_DATA=True.

Usage:
    python scripts/refresh_local_pkls.py
"""
import os
import sys
import pathlib
import logging

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Force Firestore reads (not local cache)
os.environ["USE_LOCAL_DATA"] = "False"

import pandas as pd
from services.db_service import get_collection_df

LOCAL_DB = pathlib.Path(".local_db")
LOCAL_DB.mkdir(parents=True, exist_ok=True)

# Collections and their optional season filter column
COLLECTIONS = [
    ("players",            None),
    ("draft_results",      None),
    ("draft_order",        None),
    ("draft_order_rules",  None),
    ("nfl_teams",          None),
    ("nfl_standings",      None),
    ("nfl_games",          None),
]


def dump_collection(collection: str, season_col: str | None):
    log.info(f"  Fetching '{collection}' from Firestore...")
    try:
        df = get_collection_df(collection)
        if df.empty:
            log.warning(f"    '{collection}' returned empty — skipping")
            return

        # Write full unfiltered pkl
        out_path = LOCAL_DB / f"{collection}.pkl"
        df.to_pickle(out_path)
        log.info(f"    ✓ {len(df)} rows → {out_path.name}")

        # Write per-year slices for fast year-specific loads
        if season_col and season_col in df.columns:
            for year in sorted(df[season_col].dropna().unique()):
                year_df = df[df[season_col] == year]
                year_path = LOCAL_DB / f"{collection}_{int(year)}.pkl"
                year_df.to_pickle(year_path)
                log.info(f"    ✓ {len(year_df)} rows → {year_path.name}")
    except Exception as e:
        log.error(f"    ✗ Failed '{collection}': {e}")


def main():
    log.info("=" * 60)
    log.info("Refreshing local .pkl cache from Firestore")
    log.info("=" * 60)

    for collection, season_col in COLLECTIONS:
        dump_collection(collection, season_col)

    log.info("\nLocal cache refresh complete.")
    log.info(f"Files written to: {LOCAL_DB.resolve()}")


if __name__ == "__main__":
    main()
