"""scripts/train_nn_model.py -- Train the NFL Neural Network prediction model.

Reads rawdata CSVs, builds the Master Feature Table, trains the Keras
feedforward NN, prints evaluation metrics, and saves the model to disk.

Usage:
    python scripts/train_nn_model.py
    python scripts/train_nn_model.py --min-season 2010 --max-season 2025
"""

import sys
import os
import argparse
import pathlib
import time

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from services.nn_feature_engine import build_master_feature_table
from services.nn_prediction_service import NNPredictionService


def main():
    parser = argparse.ArgumentParser(
        description="Train the NFL Neural Network prediction model"
    )
    parser.add_argument(
        "--min-season", type=int, default=2006,
        help="Earliest season to include in training (default: 2006)"
    )
    parser.add_argument(
        "--max-season", type=int, default=2025,
        help="Latest season to include (default: 2025)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to save model (default: models/nn_v1.keras)"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  NFL Neural Network -- Model Training Pipeline")
    print("=" * 60)

    # Step 1: Build feature table
    print("\n[Step 1/3] Building Master Feature Table...")
    start = time.time()
    feature_table = build_master_feature_table(
        min_season=args.min_season,
        max_season=args.max_season,
    )
    build_time = time.time() - start
    print(f"  Feature table: {len(feature_table)} rows, "
          f"{len(feature_table.columns)} columns ({build_time:.1f}s)")
    print(f"  Seasons: {feature_table['season'].min()}-{feature_table['season'].max()}")
    print(f"  Teams: {feature_table['home_team'].nunique()} unique")

    # Step 2: Train model
    print("\n[Step 2/3] Training Neural Network...")
    start = time.time()
    svc = NNPredictionService()
    metrics = svc.train(feature_table)
    train_time = time.time() - start

    # Step 3: Print results
    print(f"\n{'='*60}")
    print(f"  Training Complete ({train_time:.1f}s)")
    print(f"{'='*60}")
    print(f"  Epochs trained:    {metrics['epochs_trained']}")

    # --- Game-Level Metrics (individual outcomes) ---
    print(f"\n  -- Game-Level Metrics (per-game binary outcomes) --")
    print(f"  Train R2:          {metrics['train_r2']:.4f}")
    print(f"  Train MAE:         {metrics['train_mae']:.4f}")
    print(f"  Train Accuracy:    {metrics['train_accuracy']:.4f}")
    if metrics.get("test_r2") is not None:
        print(f"  Test R2:           {metrics['test_r2']:.4f}")
        print(f"  Test MAE:          {metrics['test_mae']:.4f}")
        print(f"  Test Accuracy:     {metrics['test_accuracy']:.4f}")
    else:
        print("  Test set: (no 2025 data available)")

    # --- Season-Level Metrics (the Pythagorean comparison) ---
    print(f"\n  -- Season-Level Metrics (projected vs actual wins) --")
    if metrics.get("season_r2") is not None:
        print(f"  Season R2:         {metrics['season_r2']:.4f}")
        print(f"  Season MAE:        {metrics['season_mae']:.4f} wins")
        print(f"  Team-Seasons:      {metrics['season_n']}")
        print(f"\n  {'='*56}")
        if metrics["season_r2"] > 0.89:
            print(f"  RESULT: Season R2 ({metrics['season_r2']:.4f}) EXCEEDS "
                  f"Pythagorean baseline (0.89)")
        elif metrics["season_r2"] > 0.85:
            print(f"  RESULT: Season R2 ({metrics['season_r2']:.4f}) is COMPETITIVE "
                  f"with Pythagorean baseline (0.89)")
        else:
            print(f"  RESULT: Season R2 ({metrics['season_r2']:.4f}) is below "
                  f"Pythagorean baseline (0.89)")
        print(f"  {'='*56}")
    else:
        print("  Season-level: (insufficient data for evaluation)")
    print(f"{'='*60}")

    # Save model
    svc.save_model(args.output)
    print("\nDone.")


if __name__ == "__main__":
    main()
