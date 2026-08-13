"""
refresh_local_pkls.py — Rebuild local .pkl cache from Firestore.

Run this after any Firebase migration to get an up-to-date local cache
that the web server reads via USE_LOCAL_DATA=True.

Usage:
    python scripts/refresh_local_pkls.py
    python scripts/refresh_local_pkls.py --skip-analytics   # skip analytics_cache step
"""
import os
import sys
import json
import argparse
import pathlib
import logging

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Force Firestore reads (not local cache)
os.environ["USE_LOCAL_DATA"] = "False"

import pandas as pd
from services.db_service import get_collection_df, get_db

LOCAL_DB = pathlib.Path(".local_db")
LOCAL_DB.mkdir(parents=True, exist_ok=True)
ANALYTICS_DIR = LOCAL_DB / "analytics"
ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

# Collections and their optional season filter column
COLLECTIONS = [
    ("players",               None),
    ("draft_results",         None),
    ("draft_order",           None),
    ("draft_order_rules",     None),
    ("nfl_teams",             None),
    ("nfl_standings",         "season"),
    ("nfl_games",             "season"),
    ("weekly_recaps",         "year"),
    ("preseason_predictions", "season"),
    ("consensus_projections", "season"),
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


def dump_analytics_cache():
    """Pull all analytics_cache docs from Firestore → .local_db/analytics/*.json."""
    log.info("  Fetching 'analytics_cache' from Firestore...")
    try:
        db = get_db()
        docs = list(db.collection("analytics_cache").stream())
        if not docs:
            log.warning("    'analytics_cache' returned no documents — skipping")
            return

        written = 0
        for doc in docs:
            d = doc.to_dict()
            analytic = d.get("analytic")
            year = d.get("year")
            week = d.get("week")
            raw_data = d.get("data")
            if not analytic or year is None or week is None or raw_data is None:
                continue
            try:
                data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
            except json.JSONDecodeError:
                log.warning(f"    skipping {doc.id} — data is not valid JSON")
                continue

            out_path = ANALYTICS_DIR / f"{analytic}_{year}_{week}.json"
            with open(out_path, "w") as f:
                json.dump({
                    "analytic": analytic,
                    "year": year,
                    "week": week,
                    "is_final": d.get("is_final", False),
                    "data": data,
                }, f)
            written += 1

        log.info(f"    ✓ {written} docs → {ANALYTICS_DIR.name}/")
    except Exception as e:
        log.error(f"    ✗ Failed 'analytics_cache': {e}")


def dump_game_predictions():
    """Pull all game_predictions docs from Firestore → .local_db/game_predictions_{year}.json."""
    log.info("  Fetching 'game_predictions' from Firestore...")
    try:
        db = get_db()
        docs = list(db.collection("game_predictions").stream())
        if not docs:
            log.warning("    'game_predictions' returned no documents — skipping")
            return

        written = 0
        for doc in docs:
            d = doc.to_dict()
            season = d.get("season")
            predictions = d.get("predictions")
            if season is None or not predictions:
                continue
            out_path = LOCAL_DB / f"game_predictions_{int(season)}.json"
            with open(out_path, "w") as f:
                json.dump(d, f, default=str)
            written += 1
            log.info(f"    ✓ {len(predictions)} predictions → game_predictions_{int(season)}.json")

        log.info(f"    ✓ {written} seasons written")
    except Exception as e:
        log.error(f"    ✗ Failed 'game_predictions': {e}")


def dump_prediction_features():
    """Pull all prediction_features docs from Firestore → .local_db/prediction_features_*.json."""
    log.info("  Fetching 'prediction_features' from Firestore...")
    try:
        db = get_db()
        docs = list(db.collection("prediction_features").stream())
        if not docs:
            log.warning("    'prediction_features' returned no documents — skipping")
            return

        written = 0
        for doc in docs:
            d = doc.to_dict()
            season = d.get("season")
            ensemble_version = d.get("ensemble_version")
            if season is None or not ensemble_version:
                log.warning(f"    skipping {doc.id} — missing season or ensemble_version")
                continue
            out_path = LOCAL_DB / f"prediction_features_{int(season)}_{ensemble_version}.json"
            with open(out_path, "w") as f:
                json.dump(d, f, default=str)
            written += 1
            log.info(f"    ✓ {len(d.get('games', {}))} games → {out_path.name}")

        log.info(f"    ✓ {written} docs written")
    except Exception as e:
        log.error(f"    ✗ Failed 'prediction_features': {e}")


def dump_config_settings():
    """Pull config/settings doc → .local_db/config_settings.json."""
    log.info("  Fetching 'config/settings' from Firestore...")
    try:
        db = get_db()
        doc = db.collection("config").document("settings").get()
        data = doc.to_dict() if doc.exists else {"draft_active": False}
        out_path = LOCAL_DB / "config_settings.json"
        with open(out_path, "w") as f:
            json.dump(data, f)
        log.info(f"    ✓ → {out_path.name}")
    except Exception as e:
        log.error(f"    ✗ Failed 'config/settings': {e}")


def main():
    parser = argparse.ArgumentParser(description="Rebuild local .pkl cache from Firestore")
    parser.add_argument("--skip-analytics", action="store_true",
                        help="Skip the analytics_cache download step")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Refreshing local .pkl cache from Firestore")
    log.info("=" * 60)

    log.info("\n-- Raw collections --")
    for collection, season_col in COLLECTIONS:
        dump_collection(collection, season_col)

    log.info("\n-- ML game predictions --")
    dump_game_predictions()

    log.info("\n-- ML feature audit --")
    dump_prediction_features()

    log.info("\n-- App config --")
    dump_config_settings()

    if not args.skip_analytics:
        log.info("\n-- Analytics cache (NN projections, standings, etc.) --")
        dump_analytics_cache()

    log.info("\nLocal cache refresh complete.")
    log.info(f"Files written to: {LOCAL_DB.resolve()}")


if __name__ == "__main__":
    main()
