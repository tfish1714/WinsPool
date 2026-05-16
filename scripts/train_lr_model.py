"""scripts/train_lr_model.py -- Train the ElasticNet Logistic Regression NFL prediction model.

Uses the same Master Feature Table as the NN / XGB trainers. Saves the model to
models/lr_v{N}.pkl with a versioned registry at models/lr_registry.json.

Usage:
    python scripts/train_lr_model.py
    python scripts/train_lr_model.py --min-season 2010 --max-season 2025
    python scripts/train_lr_model.py --version v2
"""

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import os
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from services.nn_feature_engine import build_master_feature_table
from services.lr_prediction_service import LRPredictionService


def main():
    parser = argparse.ArgumentParser(description="Train the ElasticNet LR NFL prediction model")
    parser.add_argument("--min-season", type=int, default=2006)
    parser.add_argument("--max-season", type=int, default=2025)
    parser.add_argument("--version", type=str, default=None,
                        help="Version string (e.g. 'v2'). Auto-increments if omitted.")
    args = parser.parse_args()

    print("=" * 60)
    print("  NFL Logistic Regression -- Model Training Pipeline")
    print("=" * 60)

    print("\n[Step 1/3] Building Master Feature Table...")
    t0 = time.time()
    feature_table = build_master_feature_table(
        min_season=args.min_season, max_season=args.max_season
    )
    print(f"  {len(feature_table)} rows | {feature_table['season'].min()}-"
          f"{feature_table['season'].max()} | {time.time()-t0:.1f}s")

    print("\n[Step 2/3] Training Logistic Regression (ElasticNet CV)...")
    t0 = time.time()
    svc = LRPredictionService()
    metrics = svc.train(feature_table)
    print(f"  Training complete ({time.time()-t0:.1f}s) | "
          f"best_C={metrics.get('best_C', '?'):.4f} | "
          f"best_l1_ratio={metrics.get('best_l1_ratio', '?'):.2f}")

    print(f"\n{'='*60}")
    print("  Results")
    print(f"{'='*60}")
    print(f"  -- Game-Level --")
    print(f"  Train accuracy:  {metrics['train_accuracy']:.4f}")
    print(f"  Train log-loss:  {metrics['train_logloss']:.4f}")
    print(f"  Train Brier:     {metrics['train_brier']:.4f}")
    if metrics.get("test_accuracy") is not None:
        print(f"  Test  accuracy:  {metrics['test_accuracy']:.4f}")
        print(f"  Test  log-loss:  {metrics['test_logloss']:.4f}")
        print(f"  Test  Brier:     {metrics['test_brier']:.4f}")
        if metrics.get("test_auc") is not None:
            print(f"  Test  AUC:       {metrics['test_auc']:.4f}")
    else:
        print("  Test: (no 2025 data)")

    print(f"\n  -- Season-Level (last 5 training seasons) --")
    if metrics.get("season_r2") is not None:
        print(f"  Season R2:       {metrics['season_r2']:.4f}")
        print(f"  Season MAE:      {metrics['season_mae']:.4f} wins")
        print(f"  Team-seasons:    {metrics['season_n']}")
        baseline = 0.89
        if metrics["season_r2"] > baseline:
            print(f"  RESULT: Exceeds Pythagorean baseline ({baseline})")
        elif metrics["season_r2"] > 0.85:
            print(f"  RESULT: Competitive with Pythagorean baseline ({baseline})")
        else:
            print(f"  RESULT: Below Pythagorean baseline ({baseline})")

    print(f"\n  -- Top Feature Coefficients (|coef|) --")
    fi = svc.feature_importance(top_n=10)
    for _, row in fi.iterrows():
        sign = "+" if row["coef"] >= 0 else "-"
        bar = "#" * int(abs(row["abs_coef"]) * 30)
        print(f"  {sign} {row['feature']:<30} {row['abs_coef']:.4f}  {bar}")

    print(f"\n[Step 3/3] Saving model...")
    version = svc.save_versioned(
        version=args.version,
        training_params={"min_season": args.min_season, "max_season": args.max_season,
                         "train_samples": int((feature_table["season"] < feature_table["season"].max()).sum())},
    )
    print(f"  Saved as {version} -> models/lr_{version}.pkl")
    print(f"{'='*60}")
    print("Done.")


if __name__ == "__main__":
    main()
