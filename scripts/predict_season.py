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
    build_master_feature_table,
    _read_csv_safe,
    _normalize_team,
    RAWDATA_DIR,
)
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS as NN_FEATURE_COLUMNS,
)
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService

N_SIMULATIONS = 10_000
NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35


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


def _build_team_profiles(feature_table: pd.DataFrame, prior_season: int) -> pd.DataFrame:
    """Build per-team average feature profiles from the prior season."""
    season_data = feature_table[feature_table["season"] == prior_season].copy()
    if season_data.empty:
        latest = feature_table["season"].max()
        print(f"  No data for {prior_season}, falling back to {latest}.")
        season_data = feature_table[feature_table["season"] == latest].copy()

    home_avg = season_data.groupby("home_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    home_avg.rename(columns={"home_team": "team"}, inplace=True)
    away_avg = season_data.groupby("away_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    away_avg.rename(columns={"away_team": "team"}, inplace=True)

    combined = pd.concat([home_avg, away_avg], ignore_index=True)
    return combined.groupby("team")[NN_FEATURE_COLUMNS].mean().reset_index()


# ---------------------------------------------------------------------------
# Probability engine
# ---------------------------------------------------------------------------

def _compute_game_probs(nn_svc, xgb_svc, lr_svc, team_profiles: pd.DataFrame, schedule: pd.DataFrame) -> list:
    """Return list of (home_team, away_team, blended_prob) for every scheduled game."""
    profile_dict = {row["team"]: {c: row[c] for c in NN_FEATURE_COLUMNS}
                    for _, row in team_profiles.iterrows()}

    game_probs = []
    for _, game in schedule.iterrows():
        ht, at = game["home_team"], game["away_team"]
        if ht not in profile_dict or at not in profile_dict:
            continue

        hp, ap = profile_dict[ht], profile_dict[at]
        features = {}
        for col in NN_FEATURE_COLUMNS:
            if col == "home_flag":
                features[col] = 1.0
            elif col in ("roster_talent_delta", "star_qb_value_delta",
                         "trench_dominance_metric", "net_success_rate",
                         "high_leverage_regression_flag"):
                features[col] = hp.get(col, 0.0) - ap.get(col, 0.0)
            elif col.startswith("def_"):
                off_equiv = col.replace("def_", "off_")
                features[col] = ap.get(off_equiv, ap.get(col, 0.0))
            elif col.startswith("opp_"):
                features[col] = ap.get(col.replace("opp_", "tm_"), ap.get(col, 0.0))
            elif col == "vegas_elo_spread_delta":
                elo_diff = hp.get("tm_elo_pre", 1500) - ap.get("tm_elo_pre", 1500)
                features[col] = abs((elo_diff / 25.0) - hp.get("spread_line", 0))
            elif col == "market_implied_team_total":
                features[col] = hp.get(col, 22.0)
            else:
                features[col] = hp.get(col, 0.0)

        nn_prob  = nn_svc.predict_game(features)
        xgb_prob = xgb_svc.predict_game(features)
        lr_prob  = lr_svc.predict_game(features)
        blended  = float(np.clip(
            NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
            0.02, 0.98,
        ))
        game_probs.append((ht, at, blended))

    return game_probs


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------

def _run_monte_carlo(game_probs: list, all_teams: list, n_simulations: int) -> dict:
    team_idx = {t: i for i, t in enumerate(all_teams)}
    n_teams = len(all_teams)
    win_matrix = np.zeros((n_simulations, n_teams), dtype=np.float32)

    valid = [(g[0], g[1], g[2]) for g in game_probs if g[0] in team_idx and g[1] in team_idx]
    home_idx = np.array([team_idx[g[0]] for g in valid])
    away_idx = np.array([team_idx[g[1]] for g in valid])
    probs = np.array([g[2] for g in valid])

    rng = np.random.default_rng(seed=42)
    home_wins = (rng.random((n_simulations, len(probs))) < probs).astype(np.float32)

    for g in range(len(probs)):
        win_matrix[:, home_idx[g]] += home_wins[:, g]
        win_matrix[:, away_idx[g]] += 1.0 - home_wins[:, g]

    results = {}
    for team in all_teams:
        if team not in team_idx:
            continue
        w = win_matrix[:, team_idx[team]]
        results[team] = {
            "median_wins": float(np.median(w)),
            "mean_wins": float(np.mean(w)),
            "std_dev": float(np.std(w)),
            "p5": float(np.percentile(w, 5)),
            "p25": float(np.percentile(w, 25)),
            "p75": float(np.percentile(w, 75)),
            "p95": float(np.percentile(w, 95)),
        }
    return results


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
            "sources": {"model": "nn_xgb_lr_ensemble"},
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
    parser.add_argument("--season", type=int, default=_default_season(),
                        help="Target NFL season year (default: upcoming season)")
    parser.add_argument("--simulations", type=int, default=N_SIMULATIONS,
                        help=f"Monte Carlo trials (default: {N_SIMULATIONS})")
    parser.add_argument("--model", type=str, default=None,
                        help="Path to .keras model (default: latest from registry)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without uploading to Firestore")
    args = parser.parse_args()

    season = args.season
    prior_season = season - 1

    print("=" * 72)
    print(f"  NFL ML Ensemble -- {season} Season Projections (Monte Carlo)")
    print(f"  Feature baseline: {prior_season}  |  Simulations: {args.simulations:,}")
    print(f"  Blend: NN={NN_WEIGHT:.0%} / XGB={XGB_WEIGHT:.0%} / LR={LR_WEIGHT:.0%}")
    print("=" * 72)

    print("\n[1/6] Loading trained models (NN + XGB + LR)...")
    nn_svc = NNPredictionService()
    nn_svc.load_model(args.model)
    xgb_svc = XGBPredictionService()
    xgb_svc.load_model()
    lr_svc = LRPredictionService()
    lr_svc.load_model()

    print(f"[2/6] Building feature baseline from {prior_season} data...")
    feature_table = build_master_feature_table(min_season=prior_season - 4, max_season=prior_season)
    team_profiles = _build_team_profiles(feature_table, prior_season)
    print(f"  {len(team_profiles)} teams with feature profiles.")

    print(f"[3/6] Loading {season} schedule...")
    schedule = _load_schedule(RAWDATA_DIR, season, prior_season)
    if schedule.empty:
        print("ERROR: No schedule data found.")
        sys.exit(1)
    print(f"  {len(schedule)} regular season games.")

    print(f"[4/6] Computing blended probabilities (NN:{NN_WEIGHT:.0%} / XGB:{XGB_WEIGHT:.0%} / LR:{LR_WEIGHT:.0%})...")
    game_probs = _compute_game_probs(nn_svc, xgb_svc, lr_svc, team_profiles, schedule)
    probs_only = [p for _, _, p in game_probs]
    print(f"  {len(game_probs)} matchups | range: [{min(probs_only):.3f}, {max(probs_only):.3f}]")

    print(f"[5/6] Running Monte Carlo ({args.simulations:,} trials)...")
    teams_in_data = sorted({g[0] for g in game_probs} | {g[1] for g in game_probs})
    mc_results = _run_monte_carlo(game_probs, teams_in_data, args.simulations)

    projections = sorted([
        {
            "team": team,
            "proj_wins": round(stats["median_wins"], 1),
            "mean_wins": round(stats["mean_wins"], 1),
            "std_dev": round(stats["std_dev"], 2),
            "floor": round(stats["p5"], 1),
            "p25": round(stats["p25"], 1),
            "p75": round(stats["p75"], 1),
            "ceiling": round(stats["p95"], 1),
        }
        for team, stats in mc_results.items()
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
    print(f"  {len(projections)} teams | {args.simulations:,} simulations | total wins: {total:.0f}")
    print(f"  Range: {min(p['proj_wins'] for p in projections):.1f}-{max(p['proj_wins'] for p in projections):.1f} wins")
    print(f"{'='*55}")

    if args.dry_run:
        print("\n[dry-run] Skipping Firestore upload.")
    else:
        print("\n[Upload] Saving to Firestore preseason_predictions...")
        _upload_predictions(season, projections)
        print("Done.")


if __name__ == "__main__":
    main()
