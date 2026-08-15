"""scripts/walk_forward_diagnose_tails.py -- Fine-grained calibration check
at the probability extremes, per model (NN vs XGB vs LR).

Follow-on to walk_forward_diagnose_calibration.py, which used coarse
0.30-0.70 buckets and only looked at the blended ensemble. This targets the
specific finding from nn_2025_worst_calls.py: the NN issued 95%+ confidence
on 28 of 272 2025 games and was wrong on an unusual share of them (driven
substantially by two mis-profiled teams, HOU and LV), while XGB/LR stayed
well-behaved on the same games (normal log-loss). This script checks whether
that's a 2025-specific accident or a general pattern: does the NN's raw
(pre-blend) output specifically over-commit in the tails (>0.90, >0.95,
>0.98) relative to XGB and LR, pooled across all 5 walk-forward folds?

Diagnostic only: reads cached models/walkforward/ artifacts (never
retrains), writes reports/walk_forward_tail_calibration.csv.

Usage:
    python scripts/walk_forward_diagnose_tails.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd

from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS
from services.nn_prediction_service import LABEL_COLUMN
from services.nn_projection_engine import NNProjectionEngine
from scripts.walk_forward_validate import ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_START, FOLD_END = 2021, 2025

# Fine-grained edges: dense near the tails, coarser in the middle.
EDGES = [0.0, 0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
         0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0]


def run_fold(fold_year: int) -> pd.DataFrame:
    if not _fold_artifacts_exist(ARTIFACTS_DIR, fold_year):
        logger.warning("[%d] No cached fold artifacts.", fold_year)
        return pd.DataFrame()

    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, fold_year)
    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)

    logger.info("[%d] Building feature table...", fold_year)
    table = build_master_feature_table(min_season=2006, max_season=fold_year)
    season_rows = table[table["season"] == fold_year].copy()
    if season_rows.empty:
        return pd.DataFrame()

    X = season_rows[FEATURE_COLUMNS].values.astype(np.float32)
    y = season_rows[LABEL_COLUMN].values.astype(int)
    nn_p, xgb_p, lr_p, blended = engine._batch_predict_components(X)

    out = pd.DataFrame({
        "season": fold_year,
        "home_win": y,
        "nn": np.asarray(nn_p),
        "xgb": np.asarray(xgb_p),
        "lr": np.asarray(lr_p),
        "blend": np.asarray(blended),
    })
    return out


def _tail_table(df: pd.DataFrame, model_col: str) -> pd.DataFrame:
    rows = []
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        m = (df[model_col] >= lo) & (df[model_col] < hi)
        n = int(m.sum())
        if n == 0:
            continue
        pred_pct = float(df.loc[m, model_col].mean()) * 100
        actual_pct = float(df.loc[m, "home_win"].mean()) * 100 if n > 0 else float("nan")
        rows.append({
            "model": model_col, "range": f"{lo:.2f}-{hi:.2f}", "n": n,
            "pred_pct": round(pred_pct, 1), "actual_pct": round(actual_pct, 1),
            "diff": round(actual_pct - pred_pct, 1),
        })
    return pd.DataFrame(rows)


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for fold_year in range(FOLD_START, FOLD_END + 1):
        print(f"\n{'=' * 60}\n  Fold {fold_year}\n{'=' * 60}")
        df = run_fold(fold_year)
        if not df.empty:
            all_rows.append(df)

    if not all_rows:
        print("No folds scored.")
        return

    pooled = pd.concat(all_rows, ignore_index=True)
    pooled.to_csv(REPORTS_DIR / "walk_forward_tail_raw.csv", index=False)

    print(f"\n{'=' * 70}\n  Tail Calibration by Model (pooled across all 5 folds, n={len(pooled)})\n{'=' * 70}")
    tables = []
    for model_col in ["nn", "xgb", "lr", "blend"]:
        t = _tail_table(pooled, model_col)
        tables.append(t)
        print(f"\n--- {model_col.upper()} ---")
        print(t.to_string(index=False))

    combined = pd.concat(tables, ignore_index=True)
    combined.to_csv(REPORTS_DIR / "walk_forward_tail_calibration.csv", index=False)

    # Extreme-confidence accuracy: of predictions >=0.90 or <=0.10, how often right?
    print(f"\n{'=' * 70}\n  Extreme-confidence accuracy (p>=0.90 or p<=0.10)\n{'=' * 70}")
    for model_col in ["nn", "xgb", "lr", "blend"]:
        p = pooled[model_col]
        extreme = (p >= 0.90) | (p <= 0.10)
        n = int(extreme.sum())
        if n == 0:
            continue
        called_home = (p[extreme] >= 0.5).astype(int)
        correct = (called_home == pooled.loc[extreme, "home_win"]).mean()
        print(f"  {model_col:<6} n={n:<5} accuracy={correct*100:.1f}%")


if __name__ == "__main__":
    main()
