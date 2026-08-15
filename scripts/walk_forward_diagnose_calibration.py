"""scripts/walk_forward_diagnose_calibration.py -- Score the per-game
classifiers directly, independent of the season-simulation layer.

Follow-on diagnostic to walk_forward_validate.py and walk_forward_diagnose_mc.py.
Those two showed that neither the Elo K/HFA/MoV fix nor the Monte Carlo vs.
direct-sum-of-probabilities choice explains why the model loses to analyst
consensus on season win totals -- both are close to a wash. This script asks
the next question: are the NN/XGB/LR classifiers themselves any good at the
one thing they're actually trained to do (predict a single game), using each
fold's own held-out season with real in-season features (not team-profile
proxies, not simulated state)?

For each cached fold (2021-2025):
  1. Build the feature table through the fold's target season (features use
     only data available before each game -- no leakage), filter to that
     season's real games.
  2. Run the fold's cached NN/XGB/LR models (trained on strictly-prior
     seasons) on those real rows.
  3. Report accuracy, Brier score, log-loss for NN, XGB, LR, and the blended
     45/20/35 ensemble, plus a calibration-by-probability-bucket table
     (predicted vs. actual home-win rate) for the blend, mirroring the
     diagnostic in scripts/calibrate_elo_constants.py.

Diagnostic only: reads cached models/walkforward/ artifacts (never
retrains), writes reports/walk_forward_calibration.csv, touches no
registries or Firestore collections.

Usage:
    python scripts/walk_forward_diagnose_calibration.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score

from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS
from services.nn_prediction_service import LABEL_COLUMN
from services.nn_projection_engine import NNProjectionEngine
from scripts.walk_forward_validate import ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_START, FOLD_END = 2021, 2025


def _metrics(y_true: np.ndarray, p: np.ndarray) -> dict:
    p_clipped = np.clip(p, 1e-6, 1 - 1e-6)
    return {
        "accuracy": float(accuracy_score(y_true, (p >= 0.5).astype(int))),
        "brier": float(brier_score_loss(y_true, p)),
        "log_loss": float(log_loss(y_true, p_clipped, labels=[0, 1])),
    }


def _calibration_table(y_true: np.ndarray, p: np.ndarray, n_buckets: int = 6) -> pd.DataFrame:
    edges = np.linspace(0.30, 0.70, n_buckets + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() < 5:
            continue
        rows.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "n": int(m.sum()),
            "pred_pct": round(float(p[m].mean()) * 100, 1),
            "actual_pct": round(float(y_true[m].mean()) * 100, 1),
        })
    return pd.DataFrame(rows)


def run_fold(fold_year: int) -> dict:
    if not _fold_artifacts_exist(ARTIFACTS_DIR, fold_year):
        logger.warning("[%d] No cached fold artifacts -- run walk_forward_validate.py first.", fold_year)
        return {}

    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, fold_year)
    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)

    logger.info("[%d] Building feature table through target season...", fold_year)
    table = build_master_feature_table(min_season=2006, max_season=fold_year)
    season_rows = table[table["season"] == fold_year].copy()
    if season_rows.empty:
        logger.warning("[%d] No rows for target season.", fold_year)
        return {}

    X = season_rows[FEATURE_COLUMNS].values.astype(np.float32)
    y = season_rows[LABEL_COLUMN].values.astype(int)

    nn_p, xgb_p, lr_p, blended = engine._batch_predict_components(X)

    row = {"season": fold_year, "n_games": len(y)}
    for name, p in [("nn", nn_p), ("xgb", xgb_p), ("lr", lr_p), ("blend", blended)]:
        m = _metrics(y, np.asarray(p))
        for k, v in m.items():
            row[f"{name}_{k}"] = round(v, 4)

    calib = _calibration_table(y, np.asarray(blended))
    calib.insert(0, "season", fold_year)

    return {"summary": row, "calibration": calib}


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries, calibrations = [], []

    for fold_year in range(FOLD_START, FOLD_END + 1):
        print(f"\n{'=' * 70}\n  Fold {fold_year}\n{'=' * 70}")
        result = run_fold(fold_year)
        if not result:
            continue
        summaries.append(result["summary"])
        calibrations.append(result["calibration"])

    if not summaries:
        print("\nNo folds scored -- check that models/walkforward/ artifacts exist.")
        return

    summary_df = pd.DataFrame(summaries)
    summary_df.to_csv(REPORTS_DIR / "walk_forward_calibration.csv", index=False)

    calib_df = pd.concat(calibrations, ignore_index=True) if calibrations else pd.DataFrame()
    calib_df.to_csv(REPORTS_DIR / "walk_forward_calibration_buckets.csv", index=False)

    print(f"\n{'=' * 70}\n  Per-Game Classifier Diagnostics (own held-out target season)\n{'=' * 70}")
    print(summary_df.to_string(index=False))

    print(f"\n{'=' * 70}\n  Blend Calibration by Probability Bucket (all folds)\n{'=' * 70}")
    print(calib_df.to_string(index=False))

    print(f"\n{'=' * 70}\n  Pooled Across Folds\n{'=' * 70}")
    n_total = summary_df["n_games"].sum()
    for name in ["nn", "xgb", "lr", "blend"]:
        # weighted (by game count) average of per-fold metrics
        for metric in ["accuracy", "brier", "log_loss"]:
            col = f"{name}_{metric}"
            weighted = (summary_df[col] * summary_df["n_games"]).sum() / n_total
            print(f"  {name:<6} {metric:<10}: {weighted:.4f}")


if __name__ == "__main__":
    main()
