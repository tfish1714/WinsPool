"""scripts/run_ablation.py -- Controlled feature ablation study for the NFL NN.

Systematically trains the model with different feature subsets while keeping
all hyperparameters constant. Each experiment is logged to a CSV leaderboard
for side-by-side comparison.

The experiments test:
  1. Baseline (all 12 features)
  2. Market-only (Elo + spread -- what Vegas gives you for free)
  3. EPA-only (team efficiency metrics)
  4. EPA + Market (the likely sweet spot)
  5. Drop each feature one at a time (leave-one-out ablation)
  6. Expanded: add extra features from rawdata

Usage:
    python scripts/run_ablation.py
    python scripts/run_ablation.py --experiments baseline,market_only,epa_plus_market
"""

import sys
import os
import argparse
import pathlib
import time
import json
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging
logging.basicConfig(level=logging.WARNING)

import numpy as np
import pandas as pd

from services.nn_feature_engine import build_master_feature_table
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
)

RESULTS_DIR = pathlib.Path(__file__).parent.parent / "reports"

# ---------------------------------------------------------------------------
# Experiment Definitions
# ---------------------------------------------------------------------------
# Each experiment specifies a name and which features to include.

ALL_FEATURES = [
    "off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa",
    "turnover_margin", "third_down_pct",
    "elo_pre", "qb_elo_pre", "spread_line", "rest_advantage",
    "home_flag", "roster_talent_delta",
]

EXPERIMENTS = {
    # --- Core Feature Set Comparisons ---
    "baseline": {
        "desc": "All 12 original features",
        "features": ALL_FEATURES,
    },
    "market_only": {
        "desc": "Market signals only (Elo + spread + home)",
        "features": ["elo_pre", "qb_elo_pre", "spread_line", "home_flag"],
    },
    "epa_only": {
        "desc": "EPA efficiency metrics only",
        "features": [
            "off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa",
            "home_flag",
        ],
    },
    "epa_plus_market": {
        "desc": "EPA + Market signals (no roster/turnover noise)",
        "features": [
            "off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa",
            "elo_pre", "qb_elo_pre", "spread_line", "home_flag",
        ],
    },
    "epa_market_context": {
        "desc": "EPA + Market + Contextual (rest, div game)",
        "features": [
            "off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa",
            "elo_pre", "qb_elo_pre", "spread_line", "rest_advantage",
            "home_flag",
        ],
    },
    "spread_only": {
        "desc": "Vegas spread + home flag only (Vegas baseline)",
        "features": ["spread_line", "home_flag"],
    },
    "elo_only": {
        "desc": "Elo ratings + home flag only",
        "features": ["elo_pre", "qb_elo_pre", "home_flag"],
    },
    "no_turnover": {
        "desc": "All features minus turnover_margin (noisy metric)",
        "features": [f for f in ALL_FEATURES if f != "turnover_margin"],
    },
    "no_third_down": {
        "desc": "All features minus third_down_pct",
        "features": [f for f in ALL_FEATURES if f != "third_down_pct"],
    },
    "no_roster": {
        "desc": "All features minus roster_talent_delta",
        "features": [f for f in ALL_FEATURES if f != "roster_talent_delta"],
    },
    "no_rest": {
        "desc": "All features minus rest_advantage",
        "features": [f for f in ALL_FEATURES if f != "rest_advantage"],
    },
    "no_qb_elo": {
        "desc": "All features minus qb_elo_pre",
        "features": [f for f in ALL_FEATURES if f != "qb_elo_pre"],
    },
    "minimal_market_epa": {
        "desc": "Spread + pass EPA only (4 features)",
        "features": ["off_pass_epa", "def_pass_epa", "spread_line", "home_flag"],
    },
}


# ---------------------------------------------------------------------------
# Experiment Runner
# ---------------------------------------------------------------------------

def _run_single_experiment(
    name: str,
    features: list,
    desc: str,
    feature_table: pd.DataFrame,
    run_id: str,
) -> dict:
    """Train the model with a specific feature set and return metrics.

    Uses identical hyperparameters across all experiments for fair comparison.
    """
    print(f"\n  [{name}] {desc}")
    print(f"    Features ({len(features)}): {', '.join(features)}")

    # Verify all features exist in the table
    missing = [f for f in features if f not in feature_table.columns]
    if missing:
        print(f"    SKIPPED: missing columns {missing}")
        return {"experiment": name, "status": "skipped", "reason": str(missing)}

    start = time.time()

    # Temporarily override FEATURE_COLUMNS in the service
    import services.nn_prediction_service as nn_svc
    original_features = nn_svc.FEATURE_COLUMNS
    nn_svc.FEATURE_COLUMNS = features

    try:
        svc = NNPredictionService()
        metrics = svc.train(feature_table)
        duration = time.time() - start

        result = {
            "experiment": name,
            "description": desc,
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "n_features": len(features),
            "features": json.dumps(features),
            "train_r2": metrics.get("train_r2"),
            "train_mae": metrics.get("train_mae"),
            "train_accuracy": metrics.get("train_accuracy"),
            "test_r2": metrics.get("test_r2"),
            "test_mae": metrics.get("test_mae"),
            "test_accuracy": metrics.get("test_accuracy"),
            "season_r2": metrics.get("season_r2"),
            "season_mae": metrics.get("season_mae"),
            "season_n": metrics.get("season_n"),
            "epochs": metrics.get("epochs_trained"),
            "duration_s": round(duration, 1),
            "status": "ok",
        }

        sr2 = metrics.get("season_r2")
        sr2_str = f"{sr2:.4f}" if sr2 is not None else "N/A"
        acc = metrics.get("train_accuracy", 0)
        print(f"    Season R2: {sr2_str} | "
              f"Train Acc: {acc:.3f} | "
              f"Time: {duration:.1f}s")
        return result

    except Exception as e:
        print(f"    ERROR: {e}")
        return {"experiment": name, "status": "error", "reason": str(e)}
    finally:
        nn_svc.FEATURE_COLUMNS = original_features


def main():
    parser = argparse.ArgumentParser(
        description="Run controlled feature ablation experiments"
    )
    parser.add_argument(
        "--experiments", type=str, default=None,
        help="Comma-separated list of experiment names to run "
             "(default: all). Use 'list' to print available experiments."
    )
    parser.add_argument(
        "--min-season", type=int, default=2006,
        help="Earliest training season (default: 2006)"
    )
    parser.add_argument(
        "--max-season", type=int, default=2025,
        help="Latest season (default: 2025)"
    )
    args = parser.parse_args()

    # List mode
    if args.experiments == "list":
        print("Available experiments:")
        for name, cfg in EXPERIMENTS.items():
            print(f"  {name:<25} {cfg['desc']}")
            print(f"    Features: {', '.join(cfg['features'])}")
        return

    # Select experiments
    if args.experiments:
        selected = [e.strip() for e in args.experiments.split(",")]
        for name in selected:
            if name not in EXPERIMENTS:
                print(f"Unknown experiment: '{name}'. Use --experiments list")
                return
    else:
        selected = list(EXPERIMENTS.keys())

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 65)
    print("  NFL Neural Network -- Feature Ablation Study")
    print(f"  Run ID: {run_id}")
    print(f"  Experiments: {len(selected)}")
    print("=" * 65)

    # Build feature table once
    print("\n[1/2] Building feature table...")
    ft = build_master_feature_table(
        min_season=args.min_season, max_season=args.max_season
    )
    print(f"  {len(ft)} games, {ft['season'].min()}-{ft['season'].max()}")

    # Run experiments
    print(f"\n[2/2] Running {len(selected)} experiments...")
    results = []
    for name in selected:
        cfg = EXPERIMENTS[name]
        result = _run_single_experiment(
            name, cfg["features"], cfg["desc"], ft, run_id
        )
        results.append(result)

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_df = pd.DataFrame(results)
    csv_path = RESULTS_DIR / f"ablation_{run_id}.csv"
    results_df.to_csv(csv_path, index=False)

    # Also append to cumulative log
    cumulative_path = RESULTS_DIR / "ablation_all_runs.csv"
    if cumulative_path.exists():
        existing = pd.read_csv(cumulative_path)
        combined = pd.concat([existing, results_df], ignore_index=True)
        combined.to_csv(cumulative_path, index=False)
    else:
        results_df.to_csv(cumulative_path, index=False)

    # Print leaderboard
    ok_results = results_df[results_df["status"] == "ok"].copy()
    if not ok_results.empty:
        ok_results = ok_results.sort_values("season_r2", ascending=False)

        print(f"\n{'='*65}")
        print(f"  LEADERBOARD (sorted by Season R2)")
        print(f"{'='*65}")
        print(f"  {'#':<4}{'Experiment':<25}{'Feats':<7}"
              f"{'Season R2':<12}{'S-MAE':<8}{'Game Acc':<10}")
        print(f"  {'-'*60}")

        for i, (_, row) in enumerate(ok_results.iterrows(), 1):
            sr2 = f"{row['season_r2']:.4f}" if pd.notna(row["season_r2"]) else "N/A"
            smae = f"{row['season_mae']:.2f}" if pd.notna(row["season_mae"]) else "N/A"
            acc = f"{row['train_accuracy']:.3f}" if pd.notna(row["train_accuracy"]) else "N/A"
            marker = " <-- BEST" if i == 1 else ""
            print(f"  {i:<4}{row['experiment']:<25}{int(row['n_features']):<7}"
                  f"{sr2:<12}{smae:<8}{acc:<10}{marker}")

        best = ok_results.iloc[0]
        print(f"\n  Best experiment: {best['experiment']}")
        print(f"  Best Season R2:  {best['season_r2']:.4f}")
        print(f"  Features: {best['features']}")

    print(f"\n  Results saved to: {csv_path}")
    print(f"  Cumulative log:  {cumulative_path}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
