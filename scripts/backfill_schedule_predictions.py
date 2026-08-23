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
from services.cache_service import get_game_predictions, write_game_predictions, write_prediction_features
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX, SPREAD_TO_PROB_SCALE
from services.feature_audit_service import compute_feature_audit


# ---------------------------------------------------------------------------
# Build full predictions map for one year
# ---------------------------------------------------------------------------

def _build_predictions_map(year: int, ft_lookup: dict,
                            schedule_df: pd.DataFrame,
                            games_df: pd.DataFrame,
                            force: bool) -> dict:
    """Assemble the final predictions map for one season.

    1. Feature-table predictions for completed games → locked=True.
    2. MC simulation via NNProjectionEngine.simulate_season() for future games → locked=False.
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
    result = {k: {**v, "locked": True} for k, v in played_keys.items()}

    # Build completed_results from actual nfl_games scores
    completed_results = {}
    if not games_df.empty and "result" in games_df.columns:
        yr_games = games_df[games_df["season"] == year] if "season" in games_df.columns else games_df
        for _, row in yr_games.iterrows():
            if pd.notna(row.get("result")) and row.get("game_type") == "REG":
                ht = _normalize_team(str(row.get("home_team", "") or ""))
                at = _normalize_team(str(row.get("away_team", "") or ""))
                wk = row.get("week")
                if ht and at and wk is not None:
                    key = f"W{int(wk):02d}_{ht}_{at}"
                    completed_results[key] = float(row["result"])

    # Skip the MC simulation entirely when every scheduled game for this year
    # already has a locked feature-table prediction -- its output would be
    # thrown away anyway (see the `if key in played_keys: continue` below).
    # This matters because NNProjectionEngine.initialize() always calls
    # build_master_feature_table(min_season=2020, max_season=year-1), which is
    # an invalid empty range for any year <= 2020 -- for fully-completed
    # historical seasons that just burns time and logs noisy
    # "roster_value_service unavailable" / "Preseason player profile build
    # failed" warnings for a simulation whose result is discarded regardless.
    needs_simulation = False
    if not schedule_df.empty:
        for _, row in schedule_df.iterrows():
            wk = row.get("week")
            if wk is None or pd.isna(wk):
                continue
            ht = _normalize_team(str(row.get("home_team", "") or ""))
            at = _normalize_team(str(row.get("away_team", "") or ""))
            if not ht or not at:
                continue
            if f"W{int(wk):02d}_{ht}_{at}" not in played_keys:
                needs_simulation = True
                break

    # Run dynamic MC simulation for all games (completed apply deterministically,
    # future games simulate forward from rebuilt team state)
    if needs_simulation:
        engine = NNProjectionEngine()  # already imported at top of file
        engine.initialize(year)
        profile_dict = {row["team"]: row.to_dict() for _, row in engine._team_profiles.iterrows()}
        sim = engine.simulate_season(schedule_df, n_sims=10_000,
                                     completed_results=completed_results)

        def _pf(d, col, default=0.0):
            v = d.get(col, default)
            try:
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else default
            except (TypeError, ValueError):
                return default

        for key, gp in sim["game_probs"].items():
            if key in played_keys:
                continue  # never overwrite a locked feature-table prediction
            ht   = gp["home_team"]
            at   = gp["away_team"]
            wk   = gp["week"]
            hp   = gp["mean_prob"]
            winner = ht if hp >= 0.5 else at
            conf   = round(max(hp, 1.0 - hp) * 100, 1)
            ms     = gp["model_spread"]

            # Vegas line from schedule if available
            sched_row = schedule_df[
                (schedule_df["home_team"].apply(_normalize_team) == ht)
                & (schedule_df["week"] == wk)
            ]
            sl_val = None
            if not sched_row.empty and pd.notna(sched_row.iloc[0].get("spread_line")):
                sl_val = float(sched_row.iloc[0]["spread_line"])

            edge   = round(ms - sl_val, 1) if sl_val is not None else None
            ats    = ht if ms > (sl_val or 0) else at
            vhp    = (round(1.0 / (1.0 + np.exp(-sl_val / SPREAD_TO_PROB_SCALE)), 4)
                      if sl_val is not None else None)

            h_prof = profile_dict.get(ht, {})
            a_prof = profile_dict.get(at, {})

            elo_diff_val     = round(_pf(h_prof, "elo_pre", 1500) - _pf(a_prof, "elo_pre", 1500), 1)
            roster_delta_val = round(_pf(h_prof, "roster_talent_delta") - _pf(a_prof, "roster_talent_delta"), 3)
            pass_epa_val     = round(
                (_pf(h_prof, "off_pass_epa_roll") - _pf(a_prof, "def_pass_epa_roll"))
                - (_pf(a_prof, "off_pass_epa_roll") - _pf(h_prof, "def_pass_epa_roll")), 3
            )
            rush_epa_val     = round(
                (_pf(h_prof, "off_rush_epa_roll") - _pf(a_prof, "def_rush_epa_roll"))
                - (_pf(a_prof, "off_rush_epa_roll") - _pf(h_prof, "def_rush_epa_roll")), 3
            )
            trench_val       = round(_pf(h_prof, "trench_score") - _pf(a_prof, "trench_score"), 1)
            point_diff_val   = round(_pf(h_prof, "margin_roll") - _pf(a_prof, "margin_roll"), 2)

            # Week-aware roster value (same source simulate_season() actually
            # fed the model -- see NNProjectionEngine.lookup_roster_value())
            # instead of the flat prior-season profile average used above.
            h_rv = engine.lookup_roster_value(ht, int(wk))
            a_rv = engine.lookup_roster_value(at, int(wk))
            off_roster_val = round(
                _pf(h_rv, "off_roster_value") - _pf(a_rv, "off_roster_value"), 3
            )
            def_roster_val = round(
                _pf(h_rv, "def_roster_value") - _pf(a_rv, "def_roster_value"), 3
            )

            result[key] = {
                "pred_prob":     round(hp, 4),
                "pred_winner":   winner,
                "pred_su_conf":  conf,
                "pred_ats_pick": ats,
                "model_spread":  ms,
                "edge_vs_vegas": edge,
                "locked": False,
                "explanation": {
                    "vegas_line":           sl_val,
                    "vegas_home_prob":      vhp,
                    "model_spread":         ms,
                    "edge_vs_vegas":        edge,
                    "elo_diff":             elo_diff_val,
                    "roster_delta":         roster_delta_val,
                    "pass_epa_matchup":     pass_epa_val,
                    "rush_epa_matchup":     rush_epa_val,
                    "early_down_matchup":   0.0,
                    "turnover_margin":      0.0,
                    "point_diff_advantage": point_diff_val,
                    "home_qb_out":          0.0,
                    "away_qb_out":          0.0,
                    "rest_advantage":       0.0,
                    "travel_disadvantage":  0.0,
                    "trench_dominance":     trench_val,
                    "off_roster_value":     off_roster_val,
                    "def_roster_value":     def_roster_val,
                    "source": "mc_simulation (10000 trials)",
                },
            }

    if not force:
        existing = get_game_predictions(year)
        for k, v in existing.items():
            if v.get("locked") and k not in played_keys:
                result[k] = v

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
    parser.add_argument("--features", action="store_true",
                        help="Also compute and store per-game feature audit "
                             "(SHAP/LR contributions). Adds ~5-10s per season.")
    args = parser.parse_args()

    write_local     = not args.firestore_only and not args.dry_run
    write_firestore = (args.firestore or args.firestore_only) and not args.dry_run
    write_features  = args.features and not args.dry_run

    print("=" * 64)
    print("  Backfill Schedule Predictions (NN+XGB+LR Ensemble)")
    print(f"  Weights: NN={NN_WEIGHT:.0%} / XGB={XGB_WEIGHT:.0%} / LR={LR_WEIGHT:.0%}")
    dest = []
    if args.dry_run:       dest.append("dry-run")
    if write_local:        dest.append("local (.local_db/game_predictions_*.json)")
    if write_firestore:    dest.append("Firestore (game_predictions/{season})")
    print(f"  Output : {' + '.join(dest) or 'dry-run'}")
    print(f"  Force  : {args.force}")
    print(f"  Features: {write_features}")
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

    # Load the schedule for the profile fallback.
    # Primary: rawdata games.csv (always has latest spread_line/total_line from nflverse).
    # Fallback: Firestore/pkl via load_data() for any columns rawdata doesn't have.
    print("\n  Loading schedule data for profile fallback...")
    try:
        from services.nn_feature_engine import _load_schedule, RAWDATA_DIR
        all_games = _load_schedule(RAWDATA_DIR)
        if all_games.empty:
            raise ValueError("rawdata schedule empty")
        has_schedule = True
        print(f"  Loaded {len(all_games)} games from rawdata schedule")
    except Exception as e:
        print(f"  [warn] rawdata schedule unavailable ({e}), falling back to Firestore/pkl")
        try:
            from services.data_service import load_data
            _, _, all_games, _, _, _, _ = load_data()
            has_schedule = True
        except Exception as e2:
            print(f"  [warn] Could not load schedule data: {e2}")
            all_games = pd.DataFrame()
            has_schedule = False

    ensemble_version = (
        f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"
    )
    if write_features:
        print(f"\n  Ensemble version for audit: {ensemble_version}")

    print(f"\n[3/3] Writing predictions...")
    total_written = 0
    seasons_done  = 0

    for year in years:
        # Schedule rows for this year (used by profile fallback for unplayed games)
        if has_schedule and not all_games.empty and "season" in all_games.columns:
            yr_schedule = all_games[all_games["season"] == year].copy()
        else:
            yr_schedule = pd.DataFrame()

        predictions_map = _build_predictions_map(year, ft_lookup, yr_schedule, all_games, args.force)

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

        if write_features:
            year_ft = ft[ft["season"] == year].copy()
            if not year_ft.empty:
                print(f"    Computing feature audit ({len(year_ft)} games)...")
                audit_records = compute_feature_audit(year_ft, nn_svc, xgb_svc, lr_svc)
                games_dict = {r["game_key"]: r for r in audit_records}
                dests = []
                if write_local:
                    write_prediction_features(year, ensemble_version, games_dict,
                                              use_local=True)
                    dests.append("local")
                if write_firestore:
                    write_prediction_features(year, ensemble_version, games_dict,
                                              use_local=False)
                    dests.append("Firestore")
                dest_label = ", ".join(dests) if dests else "dry-run"
                print(f"    [ok] {len(games_dict)} games written [{dest_label}] -> prediction_features_{year}_{ensemble_version}")
            else:
                print(f"    [warn] No feature rows for {year} -- feature audit skipped")

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
