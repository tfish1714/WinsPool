"""scripts/backfill_schedule_predictions.py -- Backfill ML ensemble predictions
into the game_predictions store (local JSON and/or Firestore).

For every game in every cached season, computes the blended NN+XGB+LR probability
using the actual game-level feature vector (Vegas lines, Elo, EPA, etc.) and writes
pred_winner, pred_su_conf, pred_prob, and pred_ats_pick to:
  - Local:     .local_db/game_predictions_{year}.json
  - Firestore: game_predictions/{year} document (one doc per season, ~27 KB each)

The /api/schedule route reads from game_predictions and merges predictions at
request time, so this backfill makes ML predictions visible for all historical seasons.

Usage:
    python scripts/backfill_schedule_predictions.py                    # local only
    python scripts/backfill_schedule_predictions.py --firestore        # local + Firestore
    python scripts/backfill_schedule_predictions.py --firestore-only   # Firestore only
    python scripts/backfill_schedule_predictions.py --seasons 2022 2025
    python scripts/backfill_schedule_predictions.py --dry-run
"""

import argparse
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

import numpy as np
import pandas as pd

from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS, _normalize_team
from services.nn_prediction_service import NNPredictionService
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService

NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35

LOCAL_PRED_DIR = pathlib.Path(".local_db")


# ---------------------------------------------------------------------------
# ML prediction lookup
# ---------------------------------------------------------------------------

def _build_prediction_lookup(feature_table: pd.DataFrame, nn_svc, xgb_svc, lr_svc) -> dict:
    """Run ensemble on every row. Returns {(season, week, home_team, away_team): pred_dict}."""
    X = feature_table[FEATURE_COLUMNS].values.astype(np.float32)

    nn_probs  = nn_svc.model.predict(nn_svc.scaler.transform(X), verbose=0).flatten()
    xgb_probs = xgb_svc.model.predict_proba(xgb_svc.scaler.transform(X))[:, 1]
    lr_probs  = lr_svc.model.predict_proba(lr_svc.scaler.transform(X))[:, 1]

    blended = np.clip(
        NN_WEIGHT * nn_probs + XGB_WEIGHT * xgb_probs + LR_WEIGHT * lr_probs,
        0.02, 0.98,
    )

    lookup = {}
    for i, row in enumerate(feature_table.itertuples(index=False)):
        home_prob = float(blended[i])
        ht = _normalize_team(row.home_team)
        at = _normalize_team(row.away_team)
        spread = getattr(row, "spread_line", None)

        winner     = ht if home_prob >= 0.5 else at
        confidence = home_prob if home_prob >= 0.5 else 1.0 - home_prob
        conf_pct   = round(min(99.0, max(50.0, confidence * 100)), 1)

        ats = winner
        if spread is not None and not (isinstance(spread, float) and np.isnan(spread)):
            try:
                sv = float(spread)
                ats = at if sv < -3 else (ht if sv > 3 else (at if sv < 0 else ht))
            except (ValueError, TypeError):
                pass

        lookup[(int(row.season), int(row.week), ht, at)] = {
            "pred_prob":     round(home_prob, 4),
            "pred_winner":   winner,
            "pred_su_conf":  conf_pct,
            "pred_ats_pick": ats,
        }

    return lookup


def _lookup_to_predictions_map(year: int, lookup: dict) -> dict:
    """Convert {(season,week,ht,at): pred} → {"W{wk:02d}_{ht}_{at}": pred} for one year."""
    out = {}
    for (s, wk, ht, at), pred in lookup.items():
        if s == year:
            out[f"W{wk:02d}_{ht}_{at}"] = pred
    return out


# ---------------------------------------------------------------------------
# Local write
# ---------------------------------------------------------------------------

def _write_local(year: int, predictions_map: dict, dry_run: bool) -> None:
    if dry_run:
        return
    LOCAL_PRED_DIR.mkdir(parents=True, exist_ok=True)
    p = LOCAL_PRED_DIR / f"game_predictions_{year}.json"
    with open(p, "w") as f:
        json.dump({
            "season":       year,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "predictions":  predictions_map,
        }, f, default=str)


# ---------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------

def _init_firestore():
    import firebase_admin
    from firebase_admin import credentials, firestore
    if not firebase_admin._apps:
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
                raise FileNotFoundError(
                    "No Firebase credentials found. Set FIREBASE_CREDENTIALS env var "
                    "or place firebase_credentials.json in the project root."
                )
            cred = credentials.Certificate(str(creds_path))
        firebase_admin.initialize_app(cred)
    import firebase_admin.firestore as fs
    return fs.client()


def _upload_to_firestore(db, year: int, predictions_map: dict, dry_run: bool) -> None:
    """Write one season's predictions to Firestore game_predictions/{year}."""
    payload = {
        "season":       year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "predictions":  predictions_map,
    }
    if not dry_run:
        db.collection("game_predictions").document(str(year)).set(payload)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backfill ML predictions into game_predictions store")
    parser.add_argument("--seasons", type=int, nargs=2, metavar=("MIN", "MAX"),
                        help="Season range, e.g. --seasons 2020 2025")
    parser.add_argument("--firestore", action="store_true",
                        help="Write to Firestore game_predictions in addition to local files")
    parser.add_argument("--firestore-only", action="store_true",
                        help="Write to Firestore only (skip local file updates)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute predictions but do not write anywhere")
    args = parser.parse_args()

    write_local     = not args.firestore_only and not args.dry_run
    write_firestore = (args.firestore or args.firestore_only) and not args.dry_run

    print("=" * 64)
    print("  Backfill Schedule Predictions (NN+XGB+LR Ensemble)")
    print(f"  Weights: NN={NN_WEIGHT:.0%} / XGB={XGB_WEIGHT:.0%} / LR={LR_WEIGHT:.0%}")
    dest = []
    if args.dry_run:       dest.append("dry-run")
    if write_local:        dest.append("local (.local_db/game_predictions_*.json)")
    if write_firestore:    dest.append("Firestore (game_predictions/{season})")
    print(f"  Output: {' + '.join(dest) or 'dry-run'}")
    print("=" * 64)

    # Init Firestore early so we fail fast on bad credentials
    db = None
    if write_firestore or (args.firestore and args.dry_run):
        print("\nConnecting to Firestore...")
        db = _init_firestore()
        print("  Connected.")

    # Determine season range from feature table availability
    if args.seasons:
        lo, hi = args.seasons
        years = list(range(lo, hi + 1))
    else:
        # Default: all seasons with available feature data (2006-current)
        years = list(range(2006, 2026))

    print(f"\nSeasons to process: {years}")

    # Load models
    print("\n[1/3] Loading models...")
    t0 = time.time()
    nn_svc  = NNPredictionService();  nn_svc.load_model()
    xgb_svc = XGBPredictionService(); xgb_svc.load_model()
    lr_svc  = LRPredictionService();  lr_svc.load_model()
    print(f"  Done in {time.time()-t0:.1f}s")

    # Build feature table
    min_season = max(2006, min(years))
    max_season = max(years)
    print(f"\n[2/3] Building feature table ({min_season}-{max_season})...")
    t0 = time.time()
    ft = build_master_feature_table(min_season=min_season, max_season=max_season)
    ft["home_team"] = ft["home_team"].apply(_normalize_team)
    ft["away_team"] = ft["away_team"].apply(_normalize_team)
    print(f"  {len(ft)} games in {time.time()-t0:.1f}s")

    # Generate lookup
    print("\n[3/3] Running ensemble and writing predictions...")
    t0 = time.time()
    lookup = _build_prediction_lookup(ft, nn_svc, xgb_svc, lr_svc)
    print(f"  {len(lookup)} predictions computed in {time.time()-t0:.1f}s\n")

    total_written = 0
    seasons_done  = 0

    for year in years:
        predictions_map = _lookup_to_predictions_map(year, lookup)
        if not predictions_map:
            print(f"  {year}  no predictions (no feature data) — skipped")
            continue

        n = len(predictions_map)
        actions = []

        if write_local:
            _write_local(year, predictions_map, dry_run=False)
            actions.append("local")

        if write_firestore:
            _upload_to_firestore(db, year, predictions_map, dry_run=False)
            actions.append("Firestore")

        status = f"[{', '.join(actions)}]" if actions else "[dry-run]"
        print(f"  {year}  {n:>4} games  {status}")
        total_written += n
        seasons_done  += 1

    if write_firestore and db:
        # Invalidate server-side in-memory cache
        db.collection("metadata").document("cache_control").set({"last_update": time.time()})
        print("\n  Cache invalidation signal sent to Firestore.")

    print(f"\n{'='*64}")
    print(f"  Total: {total_written} predictions across {seasons_done} seasons")
    if args.dry_run:
        print("  [dry-run] Nothing written.")
    print("Done.")


if __name__ == "__main__":
    main()
