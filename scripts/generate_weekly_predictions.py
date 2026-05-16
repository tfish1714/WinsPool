"""scripts/generate_weekly_predictions.py -- Pre-generate per-game win probabilities.

For every regular-season matchup in a given season, runs the blended
NN + power-rating model and saves predictions to Firestore
(weekly_game_predictions collection). This allows the web app to serve
draft confidence scores and portfolio projections from pre-computed data
rather than computing on every request.

For completed games, actual scores are stored alongside predictions so
the data can double as an accuracy audit trail.

Usage:
    python scripts/generate_weekly_predictions.py                  # upcoming season
    python scripts/generate_weekly_predictions.py --season 2026
    python scripts/generate_weekly_predictions.py --season 2026 --week 1
    python scripts/generate_weekly_predictions.py --season 2026 --week 1 --week 9
    python scripts/generate_weekly_predictions.py --season 2026 --dry-run
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

NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35


def _default_season() -> int:
    today = datetime.date.today()
    return today.year + 1 if today.month >= 9 else today.year


# ---------------------------------------------------------------------------
# Reuse helpers from predict_season (duplicated to keep scripts independent)
# ---------------------------------------------------------------------------

def _build_team_profiles(feature_table: pd.DataFrame, prior_season: int) -> dict:
    season_data = feature_table[feature_table["season"] == prior_season].copy()
    if season_data.empty:
        prior_season = int(feature_table["season"].max())
        season_data = feature_table[feature_table["season"] == prior_season].copy()

    home_avg = season_data.groupby("home_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    home_avg.rename(columns={"home_team": "team"}, inplace=True)
    away_avg = season_data.groupby("away_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    away_avg.rename(columns={"away_team": "team"}, inplace=True)

    combined = pd.concat([home_avg, away_avg], ignore_index=True)
    profiles_df = combined.groupby("team")[NN_FEATURE_COLUMNS].mean().reset_index()
    return {row["team"]: {c: row[c] for c in NN_FEATURE_COLUMNS}
            for _, row in profiles_df.iterrows()}


def _predict_game(nn_svc: NNPredictionService, xgb_svc: XGBPredictionService,
                  lr_svc: LRPredictionService, hp: dict, ap: dict) -> float:
    """Return blended home-win probability for a single matchup."""
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
    return float(np.clip(
        NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
        0.02, 0.98,
    ))


# ---------------------------------------------------------------------------
# Firestore
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


def _upload_weekly_predictions(records: list, dry_run: bool):
    if dry_run:
        print(f"  [dry-run] Would upload {len(records)} game predictions.")
        return

    db = _init_firebase()
    batch = db.batch()
    committed = 0
    for rec in records:
        doc_id = f"{rec['season']}_W{rec['week']:02d}_{rec['home_team']}_{rec['away_team']}"
        ref = db.collection("weekly_game_predictions").document(doc_id)
        batch.set(ref, rec)
        committed += 1
        if committed % 400 == 0:
            batch.commit()
            batch = db.batch()
            print(f"  ...committed {committed} records")

    if committed % 400 != 0:
        batch.commit()

    db.collection("metadata").document("cache_control").set({"last_update": time.time()})
    print(f"  Uploaded {committed} game predictions to weekly_game_predictions.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate per-game win probability predictions")
    parser.add_argument("--season", type=int, default=_default_season(),
                        help="Target NFL season year (default: upcoming season)")
    parser.add_argument("--week", type=int, action="append", dest="weeks",
                        help="Week(s) to generate (default: all regular season weeks). "
                             "Pass twice for a range: --week 1 --week 9")
    parser.add_argument("--model", type=str, default=None,
                        help="Path to .keras model (default: latest from registry)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results without uploading to Firestore")
    args = parser.parse_args()

    season = args.season
    prior_season = season - 1

    # Resolve week range
    if args.weeks is None:
        week_filter = None  # all weeks
        week_label = "all weeks"
    elif len(args.weeks) == 1:
        week_filter = set(args.weeks)
        week_label = f"week {args.weeks[0]}"
    else:
        lo, hi = min(args.weeks), max(args.weeks)
        week_filter = set(range(lo, hi + 1))
        week_label = f"weeks {lo}–{hi}"

    print("=" * 72)
    print(f"  Weekly Game Predictions -- {season} Season ({week_label})")
    print(f"  Feature baseline: {prior_season}  |  NN:{NN_WEIGHT:.0%} / XGB:{XGB_WEIGHT:.0%} / LR:{LR_WEIGHT:.0%}")
    print("=" * 72)

    # Load models
    print("\n[1/3] Loading trained models (NN + XGB + LR)...")
    nn_svc = NNPredictionService()
    nn_svc.load_model(args.model)
    xgb_svc = XGBPredictionService()
    xgb_svc.load_model()
    lr_svc = LRPredictionService()
    lr_svc.load_model()

    # Build team profiles
    print(f"[2/3] Building team profiles from {prior_season}...")
    feature_table = build_master_feature_table(min_season=prior_season - 4, max_season=prior_season)
    team_profiles = _build_team_profiles(feature_table, prior_season)
    print(f"  {len(team_profiles)} teams with feature profiles.")

    # Load schedule
    print(f"[3/3] Loading {season} schedule and generating predictions...")
    games_path = RAWDATA_DIR / "schedules" / "games.csv"
    if not games_path.exists():
        print("ERROR: rawdata/schedules/games.csv not found.")
        sys.exit(1)

    df = _read_csv_safe(str(games_path))
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df["result"] = pd.to_numeric(df.get("result", pd.Series(dtype=float)), errors="coerce")

    season_games = df[(df["season"] == season) & (df["game_type"] == "REG")].copy()
    if season_games.empty:
        print(f"ERROR: No regular season games found for {season} in games.csv.")
        sys.exit(1)

    if week_filter is not None:
        season_games = season_games[season_games["week"].isin(week_filter)]

    generated_at = time.time()
    records = []
    skipped = 0
    weeks_seen = set()

    for _, game in season_games.sort_values(["week"]).iterrows():
        ht = game["home_team"]
        at = game["away_team"]
        week = int(game["week"])

        if ht not in team_profiles or at not in team_profiles:
            skipped += 1
            continue

        home_win_prob = _predict_game(nn_svc, xgb_svc, lr_svc,
                                      team_profiles[ht], team_profiles[at])

        # Capture actual result if game is completed
        home_score = game["home_score"] if pd.notna(game.get("home_score")) else None
        away_score = game["away_score"] if pd.notna(game.get("away_score")) else None
        result = game["result"] if pd.notna(game.get("result")) else None
        is_completed = home_score is not None

        records.append({
            "season": season,
            "week": week,
            "home_team": ht,
            "away_team": at,
            "home_win_prob": round(home_win_prob, 4),
            "away_win_prob": round(1.0 - home_win_prob, 4),
            "is_completed": is_completed,
            "home_score": float(home_score) if home_score is not None else None,
            "away_score": float(away_score) if away_score is not None else None,
            "result": float(result) if result is not None else None,
            "generated_at": generated_at,
        })
        weeks_seen.add(week)

    print(f"\n  Generated predictions for {len(records)} games across {len(weeks_seen)} weeks.")
    if skipped:
        print(f"  Skipped {skipped} games (teams missing from feature profiles).")

    # Sample output
    if records:
        print(f"\n  {'Wk':<4}{'Home':<6}{'Away':<6}{'Home%':<8}{'Away%':<8}{'Result'}")
        print(f"  {'-'*40}")
        for r in records[:10]:
            result_str = f"{int(r['home_score'])}-{int(r['away_score'])}" if r["is_completed"] else "—"
            print(f"  {r['week']:<4}{r['home_team']:<6}{r['away_team']:<6}"
                  f"{r['home_win_prob']:.1%}   {r['away_win_prob']:.1%}   {result_str}")
        if len(records) > 10:
            print(f"  ... ({len(records) - 10} more games)")

    print()
    _upload_weekly_predictions(records, dry_run=args.dry_run)

    if not args.dry_run:
        print("Done.")


if __name__ == "__main__":
    main()
