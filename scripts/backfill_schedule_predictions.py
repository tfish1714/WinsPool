"""scripts/backfill_schedule_predictions.py -- Backfill / refresh ML predictions.

Writes per-game ML predictions to the game_predictions store (local JSON and/or
Firestore), which /api/schedule reads to display predictions for every season.

Prediction sources
------------------
- Completed games  : NN+XGB+LR ensemble via build_ensemble_lookup() on actual
                     game-day feature vectors (Vegas lines, Elo, EPA from prior
                     weeks — all pre-game data).
- Future games     : NNProjectionEngine team-profile fallback using the prior
                     season's averages.

Locking
-------
Every completed game (any season) is stored with locked=True.  Future re-runs
(including after model retraining) skip locked entries by default so the
accuracy running-total stays valid across the whole history.

  --force : overwrite locked predictions too. Use after retraining to reset
            the accuracy baseline with the new model.

Usage:
    python scripts/backfill_schedule_predictions.py                    # local only
    python scripts/backfill_schedule_predictions.py --firestore        # local + Firestore
    python scripts/backfill_schedule_predictions.py --firestore-only   # Firestore only
    python scripts/backfill_schedule_predictions.py --seasons 2022 2026
    python scripts/backfill_schedule_predictions.py --force
    python scripts/backfill_schedule_predictions.py --dry-run
"""

import argparse
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

from services.nn_feature_engine import build_master_feature_table, _normalize_team
from services.nn_prediction_service import NNPredictionService, build_ensemble_lookup
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService
from services.nn_projection_engine import NNProjectionEngine
from services.cache_service import get_game_predictions, write_game_predictions
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT


# ---------------------------------------------------------------------------
# Profile predictions (future / unplayed games)
# ---------------------------------------------------------------------------

def _profile_predictions_for_year(year: int, schedule_df: pd.DataFrame,
                                   played_keys: set) -> dict:
    """Generate team-profile predictions for unplayed games in a season.

    Uses the NNProjectionEngine (prior-season team averages) for games not
    already covered by the feature table.  schedule_df must contain at least
    week, home_team, away_team columns for the target year.
    """
    if schedule_df.empty:
        return {}

    engine = NNProjectionEngine()
    engine.initialize(year)

    out = {}
    for _, game in schedule_df.iterrows():
        ht = _normalize_team(str(game.get("home_team", "") or ""))
        at = _normalize_team(str(game.get("away_team", "") or ""))
        wk = game.get("week")
        if not ht or not at or wk is None:
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        if key in played_keys:
            continue
        try:
            prob = engine.game_win_probability(ht, at)
            hp   = prob["home_win_prob"]
            winner = ht if hp >= 0.5 else at
            conf   = round(min(99.0, max(50.0, (hp if hp >= 0.5 else 1.0 - hp) * 100)), 1)
            spread = game.get("spread_line")
            ats = winner
            if pd.notna(spread):
                try:
                    sv = float(spread)
                    hp_clip = min(0.98, max(0.02, hp))
                    implied = -7.5 * np.log(hp_clip / (1.0 - hp_clip))
                    ats = ht if implied < sv else at
                except (ValueError, TypeError):
                    pass
            sl = game.get("spread_line")
            sl_val = float(sl) if pd.notna(sl) else None
            vhp = round(1 / (1 + np.exp(sl_val / 7.5)), 4) if sl_val is not None else None
            elo_diff = round(prob.get("nn_home_prob", 0.5) * 100 - 50, 1)  # proxy
            out[key] = {
                "pred_prob":     round(float(hp), 4),
                "pred_winner":   winner,
                "pred_su_conf":  conf,
                "pred_ats_pick": ats,
                "explanation": {
                    "vegas_line":      round(sl_val, 1) if sl_val is not None else None,
                    "vegas_home_prob": vhp,
                    "elo_diff":        None,
                    "roster_delta":    None,
                    "off_pass_epa":    None,
                    "def_pass_epa":    None,
                    "off_rush_epa":    None,
                    "def_rush_epa":    None,
                    "turnover_margin": None,
                    "qb_injury":       None,
                    "rest_disadvantage": None,
                    "trench_dominance":  None,
                    "off_roster_value":  None,
                    "def_roster_value":  None,
                    "source": "profile",
                },
            }
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# Build full predictions map for one year
# ---------------------------------------------------------------------------

def _build_predictions_map(year: int, ft_lookup: dict,
                            schedule_df: pd.DataFrame,
                            force: bool) -> dict:
    """Assemble the final predictions map for one season.

    1. Feature-table predictions for completed games → locked=True.
    2. Team-profile predictions for future games → locked=False.
    3. Without --force: read existing store, preserve any already-locked entries
       (protects accuracy tracking if a game just became "completed" but the
       feature table hasn't caught up yet — or if a prior run locked predictions
       we want to keep).
    """
    # Feature-table hits for this year
    played_keys = {}
    for (s, wk, ht, at), pred in ft_lookup.items():
        if s == year:
            played_keys[f"W{wk:02d}_{ht}_{at}"] = pred

    # Build final map: played = locked, future = unlocked
    result = {k: {**v, "locked": True}  for k, v in played_keys.items()}

    # Profile predictions for unplayed games
    profile = _profile_predictions_for_year(year, schedule_df, set(played_keys))
    for k, v in profile.items():
        result[k] = {**v, "locked": False}

    if force:
        return result

    # Preserve any already-locked entries from a prior run (including ones
    # locked by a previous run that aren't yet in the feature table this run).
    existing = get_game_predictions(year)
    for k, v in existing.items():
        if v.get("locked") and k not in played_keys:
            result[k] = v  # keep the frozen pre-game prediction

    return result


# ---------------------------------------------------------------------------
# Firestore
# ---------------------------------------------------------------------------

def _init_firestore():
    import firebase_admin
    from firebase_admin import credentials
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backfill / refresh ML predictions into game_predictions store")
    parser.add_argument("--seasons", type=int, nargs=2, metavar=("MIN", "MAX"),
                        help="Season range, e.g. --seasons 2020 2026")
    parser.add_argument("--firestore", action="store_true",
                        help="Write to Firestore in addition to local files")
    parser.add_argument("--firestore-only", action="store_true",
                        help="Write to Firestore only (skip local file updates)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite locked predictions (all seasons). "
                             "Use after retraining to reset the accuracy baseline.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but do not write anywhere")
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
    print(f"  Output : {' + '.join(dest) or 'dry-run'}")
    print(f"  Force  : {args.force}")
    print("=" * 64)

    db = None
    if write_firestore or (args.firestore and args.dry_run):
        print("\nConnecting to Firestore...")
        db = _init_firestore()
        print("  Connected.")

    # Year range
    current_year = datetime.now(timezone.utc).year
    if args.seasons:
        lo, hi = args.seasons
        years = list(range(lo, hi + 1))
    else:
        years = list(range(2006, current_year + 1))

    print(f"\nSeasons: {years}")

    # Load models
    print("\n[1/3] Loading models...")
    t0 = time.time()
    nn_svc  = NNPredictionService();  nn_svc.load_model()
    xgb_svc = XGBPredictionService(); xgb_svc.load_model()
    lr_svc  = LRPredictionService();  lr_svc.load_model()
    print(f"  Done in {time.time()-t0:.1f}s")

    # Build feature table for all years (covers completed games only)
    min_s = max(2006, min(years))
    max_s = max(years)
    print(f"\n[2/3] Building feature table ({min_s}–{max_s})...")
    t0 = time.time()
    ft = build_master_feature_table(min_season=min_s, max_season=max_s)
    ft["home_team"] = ft["home_team"].apply(_normalize_team)
    ft["away_team"] = ft["away_team"].apply(_normalize_team)
    print(f"  {len(ft)} completed games in {time.time()-t0:.1f}s")

    ft_lookup = build_ensemble_lookup(ft, nn_svc, xgb_svc, lr_svc)
    print(f"  {len(ft_lookup)} feature-table predictions computed")

    # Load the schedule from Firestore/pkl for the profile fallback
    # (used only for years that have unplayed games not in the feature table)
    print("\n  Loading schedule data for profile fallback...")
    try:
        from services.data_service import load_data
        _, _, all_games, _, _, _, _ = load_data()
        has_schedule = True
    except Exception as e:
        print(f"  [warn] Could not load schedule data: {e}")
        all_games = pd.DataFrame()
        has_schedule = False

    print(f"\n[3/3] Writing predictions...")
    total_written = 0
    seasons_done  = 0

    for year in years:
        # Schedule rows for this year (used by profile fallback for unplayed games)
        if has_schedule and not all_games.empty and "season" in all_games.columns:
            yr_schedule = all_games[all_games["season"] == year].copy()
        else:
            yr_schedule = pd.DataFrame()

        predictions_map = _build_predictions_map(year, ft_lookup, yr_schedule, args.force)

        if not predictions_map:
            print(f"  {year}  no predictions available — skipped")
            continue

        n      = len(predictions_map)
        locked = sum(1 for v in predictions_map.values() if v.get("locked"))
        unlocked = n - locked
        actions = []

        if write_local:
            # Write directly to local JSON (bypasses USE_LOCAL_DATA routing in cache_service)
            _local_dir = pathlib.Path(".local_db")
            _local_dir.mkdir(parents=True, exist_ok=True)
            _local_path = _local_dir / f"game_predictions_{year}.json"
            import json as _json
            with open(_local_path, "w") as _f:
                _json.dump({
                    "season": year,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "predictions": predictions_map,
                }, _f, default=str)
            actions.append("local")

        if write_firestore:
            payload = {
                "season":       year,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "predictions":  predictions_map,
            }
            db.collection("game_predictions").document(str(year)).set(payload)
            actions.append("Firestore")

        status = f"[{', '.join(actions)}]" if actions else "[dry-run]"
        lock_info = f"  {locked} locked / {unlocked} open" if unlocked > 0 else f"  {locked} locked"
        print(f"  {year}  {n:>4} games  {status}{lock_info}")
        total_written += n
        seasons_done  += 1

    if write_firestore and db:
        db.collection("metadata").document("cache_control").set({"last_update": time.time()})
        print("\n  Cache invalidation signal sent.")

    print(f"\n{'='*64}")
    print(f"  Total: {total_written} predictions across {seasons_done} seasons")
    if args.dry_run:
        print("  [dry-run] Nothing written.")
    print("Done.")


if __name__ == "__main__":
    main()
