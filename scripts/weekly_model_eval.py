"""scripts/weekly_model_eval.py -- Track ensemble model accuracy week-by-week.

After each week of NFL games, run this script to evaluate how well the model
predicted that week's outcomes. Results are appended to reports/nn_weekly_accuracy.csv
so you can track accuracy drift and decide when to retrain.

Usage:
    python scripts/weekly_model_eval.py --season 2025 --week 14
    python scripts/weekly_model_eval.py --season 2025 --week 14 --model best
    python scripts/weekly_model_eval.py --season 2025 --week 1 --week 17  # range
"""

import sys
import os
import argparse
import pathlib
import csv
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

import numpy as np
import pandas as pd

from services.nn_feature_engine import build_master_feature_table
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS,
    REGISTRY_PATH,
)
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService
from sklearn.metrics import log_loss as sklearn_log_loss
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
ACCURACY_CSV = REPORTS_DIR / "nn_weekly_accuracy.csv"

CSV_FIELDS = [
    "evaluated_at",
    "model_version",
    "season",
    "week",
    "games",
    "correct",
    "accuracy_pct",
    "avg_prob_correct",   # calibration: how confident when right
    "avg_prob_wrong",     # calibration: how confident when wrong
    "brier_score",        # lower is better; 0 = perfect
    "log_loss",           # cross-entropy loss; lower is better; ~0.693 = random
    "season_r2_ytd",      # season-level R2 using all weeks up to this one
    "season_mae_ytd",
]


def _resolve_model_version(svc: NNPredictionService) -> str:
    """Return the version string of the loaded model."""
    if REGISTRY_PATH.exists():
        import json
        reg = json.loads(REGISTRY_PATH.read_text())
        return reg.get("latest", "unknown")
    return "nn_v1"  # pre-registry default


def _evaluate_weeks(
    svc: NNPredictionService,
    xgb_svc: XGBPredictionService,
    lr_svc: LRPredictionService,
    feature_table: pd.DataFrame,
    season: int,
    weeks: list[int],
) -> list[dict]:
    """Evaluate full NN+XGB+LR ensemble on each specified week. Returns one row per week."""
    from sklearn.metrics import r2_score, mean_absolute_error

    season_data = feature_table[
        (feature_table["season"] == season)
        & (feature_table["home_win"].notna())
    ].copy()

    if season_data.empty:
        print(f"  No completed games found for season {season}.")
        return []

    X_raw = season_data[FEATURE_COLUMNS].values.astype("float32")

    # NN predictions (scaled)
    X_nn = svc.scaler.transform(X_raw) if svc.scaler is not None else X_raw
    nn_probs = svc.model.predict(X_nn, verbose=0).flatten()

    # XGB predictions
    xgb_probs = xgb_svc.model.predict_proba(X_raw)[:, 1]

    # LR predictions
    X_lr = lr_svc.scaler.transform(X_raw) if lr_svc.scaler is not None else X_raw
    lr_probs = lr_svc.model.predict_proba(X_lr)[:, 1]

    blended = np.clip(
        NN_WEIGHT * nn_probs + XGB_WEIGHT * xgb_probs + LR_WEIGHT * lr_probs,
        PROB_CLIP_MIN, PROB_CLIP_MAX,
    )

    season_data = season_data.copy()
    season_data["pred_home_wp"] = blended
    season_data["pred_away_wp"] = 1.0 - blended

    model_version = _resolve_model_version(svc)
    now = datetime.now(timezone.utc).isoformat()
    rows = []

    for week in sorted(weeks):
        week_data = season_data[season_data["week"] == week]
        if week_data.empty:
            print(f"  Week {week}: no completed games — skipping.")
            continue

        # Predicted winner = home if pred_home_wp >= 0.5
        pred_home_wins = week_data["pred_home_wp"] >= 0.5
        actual_home_wins = week_data["home_win"] == 1.0

        # Ties count as wrong (no correct pick)
        correct_mask = (
            (pred_home_wins & actual_home_wins) |
            (~pred_home_wins & (week_data["home_win"] == 0.0))
        )
        n_games = len(week_data)
        n_correct = correct_mask.sum()
        accuracy = n_correct / n_games if n_games else 0.0

        # Calibration: avg confidence when right vs wrong
        conf = week_data["pred_home_wp"].where(pred_home_wins, week_data["pred_away_wp"])
        avg_prob_correct = float(conf[correct_mask].mean()) if correct_mask.any() else 0.0
        avg_prob_wrong = float(conf[~correct_mask].mean()) if (~correct_mask).any() else 0.0

        # Brier score: mean squared error of probability vs 0/1 outcome
        brier = float(np.mean((week_data["pred_home_wp"].values - week_data["home_win"].values) ** 2))

        # Log loss — exclude ties (home_win == 0.5); probabilities already clipped [0.02, 0.98]
        non_tie = week_data[week_data["home_win"] != 0.5]
        ll = float(sklearn_log_loss(
            non_tie["home_win"].values,
            non_tie["pred_home_wp"].values,
        )) if len(non_tie) > 0 else None

        # Season-level YTD: sum probabilities to get projected wins vs actual for all weeks <= this one
        ytd = season_data[season_data["week"] <= week]
        season_r2 = None
        season_mae = None
        if not ytd.empty:
            records = []
            for team in set(ytd["home_team"]) | set(ytd["away_team"]):
                home_g = ytd[ytd["home_team"] == team]
                away_g = ytd[ytd["away_team"] == team]
                actual = (
                    (home_g["home_win"] == 1.0).sum()
                    + (away_g["home_win"] == 0.0).sum()
                    + 0.5 * (home_g["home_win"] == 0.5).sum()
                    + 0.5 * (away_g["home_win"] == 0.5).sum()
                )
                pred = home_g["pred_home_wp"].sum() + away_g["pred_away_wp"].sum()
                if len(home_g) + len(away_g) >= 4:
                    records.append((float(actual), float(pred)))
            if len(records) >= 5:
                actuals = np.array([r[0] for r in records])
                preds = np.array([r[1] for r in records])
                season_r2 = round(float(r2_score(actuals, preds)), 4)
                season_mae = round(float(mean_absolute_error(actuals, preds)), 3)

        rows.append({
            "evaluated_at": now,
            "model_version": model_version,
            "season": season,
            "week": week,
            "games": n_games,
            "correct": int(n_correct),
            "accuracy_pct": round(accuracy * 100, 1),
            "avg_prob_correct": round(avg_prob_correct, 4),
            "avg_prob_wrong": round(avg_prob_wrong, 4),
            "brier_score": round(brier, 4),
            "log_loss": round(ll, 4) if ll is not None else None,
            "season_r2_ytd": season_r2,
            "season_mae_ytd": season_mae,
        })

        print(
            f"  Week {week:>2} | {n_correct}/{n_games} correct "
            f"({accuracy*100:.1f}%) | Brier: {brier:.4f} | "
            f"LogLoss: {f'{ll:.4f}' if ll is not None else 'N/A'} | "
            f"Season R2 YTD: {season_r2 if season_r2 is not None else 'N/A'}"
        )

    return rows


def _append_to_csv(rows: list[dict]):
    """Append rows to the weekly accuracy CSV, creating it with headers if needed."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not ACCURACY_CSV.exists()

    with open(ACCURACY_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Appended {len(rows)} row(s) -> {ACCURACY_CSV}")


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate NN model accuracy for one or more weeks and log results."
    )
    parser.add_argument("--season", type=int, required=True, help="NFL season year (e.g. 2025)")
    parser.add_argument(
        "--week", type=int, nargs="+", required=True,
        help="Week number(s) to evaluate. Pass multiple for a range: --week 1 17"
    )
    parser.add_argument(
        "--model", type=str, default="latest",
        help="Model to use: 'latest', 'best', or a path to a .keras file (default: latest)"
    )
    parser.add_argument(
        "--min-season", type=int, default=2020,
        help="Earliest season to include in feature table (default: 2020)"
    )
    parser.add_argument(
        "--no-save", action="store_true",
        help="Print results but do not append to CSV."
    )
    args = parser.parse_args()

    # Expand week range if two values given (e.g. --week 1 17 → weeks 1..17)
    if len(args.week) == 2 and args.week[0] < args.week[1]:
        weeks = list(range(args.week[0], args.week[1] + 1))
    else:
        weeks = args.week

    print("=" * 65)
    print("  NFL Ensemble (NN+XGB+LR) -- Weekly Accuracy Tracker")
    print("=" * 65)

    print(f"\n[1/3] Loading models ({args.model})...")
    svc = NNPredictionService()
    svc.load_model(args.model if args.model not in ("latest", "best") else args.model)
    xgb_svc = XGBPredictionService()
    xgb_svc.load_model()
    lr_svc = LRPredictionService()
    lr_svc.load_model()
    print(f"  Weights: NN={NN_WEIGHT} XGB={XGB_WEIGHT} LR={LR_WEIGHT}")

    print(f"[2/3] Building feature table (seasons {args.min_season}–{args.season})...")
    ft = build_master_feature_table(min_season=args.min_season, max_season=args.season)
    print(f"  {len(ft)} games loaded.")

    print(f"\n[3/3] Evaluating season {args.season}, week(s) {weeks}...")
    rows = _evaluate_weeks(svc, xgb_svc, lr_svc, ft, args.season, weeks)

    if not rows:
        print("  No rows to record.")
        return

    if not args.no_save:
        _append_to_csv(rows)
    else:
        print("\n  (--no-save: results not written to CSV)")

    print(f"\n{'='*65}")
    print(f"  Done. To view all recorded accuracy:")
    print(f"  {ACCURACY_CSV}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
