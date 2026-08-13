"""scripts/predict_season.py -- Generate NFL season win projections (Monte Carlo).

Replaces the hardcoded predict_2026.py. Dynamically resolves the target
season, prior-season feature baseline, and schedule. Saves results to
Firestore (preseason_predictions collection) unless --dry-run is set.

Uses a hybrid approach:
  1. NN model provides per-matchup base probabilities
  2. Team power ratings (point differential) amplify differentiation
  3. Monte Carlo simulation (10,000 trials) produces win distributions

Usage:
    python scripts/predict_season.py                  # upcoming season
    python scripts/predict_season.py --season 2026
    python scripts/predict_season.py --season 2026 --simulations 50000
    python scripts/predict_season.py --season 2026 --dry-run   # print only, no upload
"""

import argparse
import datetime
import logging
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

import numpy as np
import pandas as pd

from services.nn_feature_engine import (
    _read_csv_safe,
    _normalize_team,
    RAWDATA_DIR,
)
from services.nn_projection_engine import NNProjectionEngine

from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT

N_SIMULATIONS = 10_000


def _default_season() -> int:
    """Return the upcoming/current prediction target season.

    In the off-season (Jan–Aug), this is the current calendar year.
    Once the season starts (Sep+), this is next year.
    """
    today = datetime.date.today()
    return today.year + 1 if today.month >= 9 else today.year


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_schedule(rawdata_dir: pathlib.Path, season: int, prior_season: int) -> pd.DataFrame:
    """Load the target season's regular-season schedule.

    Prefers the actual schedule for `season` if it exists in games.csv.
    Falls back to `prior_season` as a structural proxy when the target
    season's schedule hasn't been released yet.
    """
    path = rawdata_dir / "schedules" / "games.csv"
    if not path.exists():
        return pd.DataFrame()

    df = _read_csv_safe(str(path))
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)

    reg = df[(df["season"] == season) & (df["game_type"] == "REG")]
    if not reg.empty:
        print(f"  Using actual {season} schedule ({len(reg)} games).")
        return reg[["season", "week", "home_team", "away_team"]].copy()

    # Fall back to prior season as a structural proxy
    reg = df[(df["season"] == prior_season) & (df["game_type"] == "REG")]
    print(f"  {season} schedule not found — using {prior_season} as proxy ({len(reg)} games).")
    return reg[["season", "week", "home_team", "away_team"]].copy()


# ---------------------------------------------------------------------------
# Firestore upload
# ---------------------------------------------------------------------------

def _init_firebase():
    import firebase_admin
    from firebase_admin import credentials, firestore
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
        os.unlink(tmp.name)
    else:
        creds_path = pathlib.Path(__file__).parent.parent / "firebase_credentials.json"
        if not creds_path.exists():
            print("ERROR: No Firebase credentials found. Use --dry-run to skip upload.")
            sys.exit(1)
        cred = credentials.Certificate(str(creds_path))

    firebase_admin.initialize_app(cred)
    return firestore.client()


def _model_version_string() -> str:
    """Concrete ensemble versions, e.g. 'nn_v14+xgb_v8+lr_v6'.

    Replaces the old sources={'model': ...} marker, which gave the field two
    different types depending on the season.
    """
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent / "models"
    parts = []
    for fname, prefix, key in (
        ("model_registry.json", "nn", "latest"),
        ("xgb_registry.json", "xgb", "latest"),
        ("lr_registry.json", "lr", "latest"),
    ):
        try:
            with open(root / fname) as f:
                reg = json.load(f)
            ver = reg.get(key)
            if ver:
                parts.append(f"{prefix}_{ver}")
        except Exception:
            continue
    return "+".join(parts) if parts else "unknown"


def _upload_predictions(season: int, projections: list):
    """Write projections to preseason_predictions collection (upsert by season+team)."""
    db = _init_firebase()
    from firebase_admin import firestore as fs

    batch = db.batch()
    count = 0
    for proj in projections:
        doc_id = f"{season}_{proj['team']}"
        ref = db.collection("preseason_predictions").document(doc_id)
        batch.set(ref, {
            "season": season,
            "team": proj["team"],
            "projected_wins": proj["proj_wins"],
            "mean_wins": proj["mean_wins"],
            "std_dev": proj["std_dev"],
            "floor": proj["floor"],
            "p25": proj["p25"],
            "p75": proj["p75"],
            "ceiling": proj["ceiling"],
            "model_version": _model_version_string(),
            "generated_at": time.time(),
        })
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()

    if count % 400 != 0:
        batch.commit()

    # Signal cache invalidation
    db.collection("metadata").document("cache_control").set({"last_update": time.time()})
    print(f"  Uploaded {count} team projections to preseason_predictions.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate NFL season win projections")
    parser.add_argument("--season", type=int, default=_default_season())
    parser.add_argument("--simulations", type=int, default=N_SIMULATIONS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    season       = args.season
    prior_season = season - 1

    print("=" * 72)
    print(f"  NFL ML Ensemble -- {season} Season Projections (Dynamic MC)")
    print(f"  Feature baseline: {prior_season}  |  Simulations: {args.simulations:,}")
    print(f"  Blend: NN={NN_WEIGHT:.0%} / XGB={XGB_WEIGHT:.0%} / LR={LR_WEIGHT:.0%}")
    print("=" * 72)

    print("\n[1/4] Loading models and building team profiles...")
    engine = NNProjectionEngine()
    engine.initialize(season)
    print(f"  {len(engine._team_profiles)} team profiles built from {prior_season} data.")

    print(f"[2/4] Loading {season} schedule...")
    schedule = _load_schedule(RAWDATA_DIR, season, prior_season)
    if schedule.empty:
        print("ERROR: No schedule data found.")
        sys.exit(1)
    print(f"  {len(schedule)} regular season games.")

    print(f"[3/4] Running dynamic Monte Carlo ({args.simulations:,} trials)...")
    results = engine.simulate_season(schedule, n_sims=args.simulations)
    team_stats = results["team_stats"]

    projections = sorted([
        {
            "team":      team,
            "proj_wins": round(stats["median_wins"], 1),
            "mean_wins": round(stats["mean_wins"],   1),
            "std_dev":   round(stats["std_dev"],     2),
            "floor":     round(stats["p5"],          1),
            "p25":       round(stats["p25"],         1),
            "p75":       round(stats["p75"],         1),
            "ceiling":   round(stats["p95"],         1),
        }
        for team, stats in team_stats.items()
    ], key=lambda x: x["proj_wins"], reverse=True)

    # Print table
    print(f"\n{'Rk':<4}{'Team':<6}{'Proj':<6}{'Mean':<6}{'StdDev':<8}"
          f"{'Floor':<7}{'25th':<6}{'75th':<6}{'Ceil':<6}")
    print("-" * 55)
    for i, p in enumerate(projections, 1):
        print(f"{i:<4}{p['team']:<6}{p['proj_wins']:<6.1f}{p['mean_wins']:<6.1f}"
              f"{p['std_dev']:<8.2f}{p['floor']:<7.1f}{p['p25']:<6.1f}"
              f"{p['p75']:<6.1f}{p['ceiling']:<6.1f}")

    total = sum(p["proj_wins"] for p in projections)
    print(f"\n{'='*55}")
    print(f"  {len(projections)} teams | {args.simulations:,} sims | total wins: {total:.0f}")
    print(f"  Range: {min(p['proj_wins'] for p in projections):.1f}"
          f"–{max(p['proj_wins'] for p in projections):.1f} wins")
    print(f"{'='*55}")

    if args.dry_run:
        print("\n[dry-run] Skipping Firestore upload.")
    else:
        print("\n[4/4] Saving to Firestore preseason_predictions...")
        _upload_predictions(season, projections)
        print("Done.")


if __name__ == "__main__":
    main()
