"""scripts/walk_forward_diagnose_nn_calibration_test.py -- Test the NN
post-hoc calibration fix (NNProjectionEngine's new `nn_calibrator` hook).

Follow-on to walk_forward_diagnose_tails.py, which found the NN issues
95-99% confidence calls that are correct only ~53% of the time (n=60
across 5 folds), while XGB/LR essentially never commit to extreme
confidence. This fits an isotonic-regression calibrator on each fold's own
held-out validation split (the same split _nn_permutation_importance
already uses), wires it into NNProjectionEngine via the new
`nn_calibrator` constructor arg, and re-scores:

  1. Season win-total MAE via the direct sum-of-per-game-probabilities
     method (walk_forward_diagnose_mc.py already showed this tracks
     simulate_season()'s Monte Carlo output to within ~0.02 MAE overall,
     so it stands in for the full MC re-simulation at a fraction of the
     cost -- no need to re-run 10,000-trial simulation twice per fold).
  2. Per-game NN accuracy/Brier/log-loss on the fold's real target season
     -- comparable to walk_forward_diagnose_calibration.py.
  3. NN tail calibration (>=0.90 buckets) before vs after -- comparable to
     walk_forward_diagnose_tails.py, but now with the fix applied, to
     directly verify the miscalibration is resolved.

Diagnostic only: reads cached models/walkforward/ artifacts (never
retrains), writes reports/walk_forward_nn_calibration_test.csv.

Usage:
    python scripts/walk_forward_diagnose_nn_calibration_test.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, accuracy_score


class PlattCalibrator:
    """2-parameter Platt scaling: P_cal = sigmoid(A * logit(raw_p) + B).

    Isotonic regression (tried first) overfit badly on the ~208-game
    validation split available per fold, manufacturing high-confidence
    predictions the raw model never made. Platt scaling only fits 2
    parameters, which a sample this size can support reliably -- the
    standard choice for small calibration sets in the literature.
    """
    def __init__(self, lr_model):
        self.lr_model = lr_model

    def predict(self, raw_p: np.ndarray) -> np.ndarray:
        raw_p = np.clip(np.asarray(raw_p), 1e-6, 1 - 1e-6)
        logit = np.log(raw_p / (1 - raw_p)).reshape(-1, 1)
        return self.lr_model.predict_proba(logit)[:, 1]

from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS
from services.nn_prediction_service import NNPredictionService, LABEL_COLUMN
from services.nn_projection_engine import NNProjectionEngine
from scripts.predict_season import _load_schedule
from services.nn_feature_engine import RAWDATA_DIR
from scripts.walk_forward_validate import (
    ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist,
    _actual_wins, _consensus_wins,
)

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_START, FOLD_END = 2021, 2025


def _fit_nn_calibrator(nn_svc, val_df) -> PlattCalibrator:
    X_val = val_df[FEATURE_COLUMNS].values.astype(np.float32)
    y_val = val_df[LABEL_COLUMN].values.astype(int)
    X_scaled = nn_svc.scaler.transform(X_val)
    raw_p = nn_svc.model.predict(X_scaled, verbose=0).flatten()
    raw_p_c = np.clip(raw_p, 1e-6, 1 - 1e-6)
    logit = np.log(raw_p_c / (1 - raw_p_c)).reshape(-1, 1)
    lr_model = LogisticRegression()
    lr_model.fit(logit, y_val)
    return PlattCalibrator(lr_model)


def _game_level_metrics(engine: NNProjectionEngine, fold_year: int) -> dict:
    table = build_master_feature_table(min_season=2006, max_season=fold_year)
    season_rows = table[table["season"] == fold_year].copy()
    X = season_rows[FEATURE_COLUMNS].values.astype(np.float32)
    y = season_rows[LABEL_COLUMN].values.astype(int)

    nn_p, xgb_p, lr_p, blended = engine._batch_predict_components(X)

    def _m(p):
        p = np.asarray(p)
        pc = np.clip(p, 1e-6, 1 - 1e-6)
        return {
            "accuracy": round(float(accuracy_score(y, (p >= 0.5).astype(int))), 4),
            "brier": round(float(brier_score_loss(y, p)), 4),
            "log_loss": round(float(log_loss(y, pc, labels=[0, 1])), 4),
        }

    row = {"n_games": len(y)}
    for name, p in [("nn", nn_p), ("blend", blended)]:
        for k, v in _m(p).items():
            row[f"{name}_{k}"] = v

    # Tail bucket for NN specifically (>=0.90)
    p = np.asarray(nn_p)
    m = p >= 0.90
    row["nn_ge90_n"] = int(m.sum())
    row["nn_ge90_pred_pct"] = round(float(p[m].mean()) * 100, 1) if m.sum() else None
    row["nn_ge90_actual_pct"] = round(float(y[m].mean()) * 100, 1) if m.sum() else None
    return row


def run_fold(fold_year: int) -> dict:
    if not _fold_artifacts_exist(ARTIFACTS_DIR, fold_year):
        logger.warning("[%d] No cached fold artifacts.", fold_year)
        return {}

    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, fold_year)

    feature_table = build_master_feature_table(min_season=2006, max_season=fold_year - 1)
    _, val_df, _ = NNPredictionService._split_data(feature_table)
    calibrator = _fit_nn_calibrator(nn_svc, val_df)

    uncal_engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)
    cal_engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc, nn_calibrator=calibrator)

    uncal_engine.initialize(fold_year)
    cal_engine.initialize(fold_year)

    logger.info("[%d] Game-level metrics (uncalibrated vs calibrated)...", fold_year)
    uncal_metrics = _game_level_metrics(uncal_engine, fold_year)
    cal_metrics = _game_level_metrics(cal_engine, fold_year)

    logger.info("[%d] Season win totals via sum-of-probabilities...", fold_year)
    schedule = _load_schedule(RAWDATA_DIR, fold_year, fold_year - 1)
    pairs = list(zip(schedule["home_team"], schedule["away_team"]))

    def _simple_wins(engine):
        preds = engine.game_win_probabilities_batch(pairs)
        wins = {}
        for p in preds:
            wins[p["home_team"]] = wins.get(p["home_team"], 0.0) + p["home_win_prob"]
            wins[p["away_team"]] = wins.get(p["away_team"], 0.0) + p["away_win_prob"]
        return wins

    uncal_wins = _simple_wins(uncal_engine)
    cal_wins = _simple_wins(cal_engine)

    actual = _actual_wins(fold_year)
    consensus = _consensus_wins(fold_year)

    rows = []
    for team, actual_w in actual.items():
        if team not in uncal_wins or team not in cal_wins:
            continue
        row = {
            "season": fold_year, "team": team, "actual_wins": actual_w,
            "uncal_wins": round(uncal_wins[team], 2),
            "uncal_abs_err": round(abs(uncal_wins[team] - actual_w), 2),
            "cal_wins": round(cal_wins[team], 2),
            "cal_abs_err": round(abs(cal_wins[team] - actual_w), 2),
        }
        if team in consensus:
            row["consensus_wins"] = consensus[team]
            row["consensus_abs_err"] = round(abs(consensus[team] - actual_w), 2)
        rows.append(row)

    return {"rows": rows, "uncal_metrics": uncal_metrics, "cal_metrics": cal_metrics}


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows, metric_rows = [], []

    for fold_year in range(FOLD_START, FOLD_END + 1):
        print(f"\n{'=' * 60}\n  Fold {fold_year}\n{'=' * 60}")
        result = run_fold(fold_year)
        if not result:
            continue
        all_rows.extend(result["rows"])
        m = {"season": fold_year}
        m.update({f"uncal_{k}": v for k, v in result["uncal_metrics"].items()})
        m.update({f"cal_{k}": v for k, v in result["cal_metrics"].items()})
        metric_rows.append(m)
        if all_rows:
            pd.DataFrame(all_rows).to_csv(REPORTS_DIR / "walk_forward_nn_calibration_test.csv", index=False)
        if metric_rows:
            pd.DataFrame(metric_rows).to_csv(REPORTS_DIR / "walk_forward_nn_calibration_test_metrics.csv", index=False)

    if not all_rows:
        print("No folds scored.")
        return

    df = pd.DataFrame(all_rows)
    print(f"\n{'=' * 70}\n  Season MAE: Uncalibrated vs Calibrated NN vs Consensus\n{'=' * 70}")
    print(f"  {'Season':<8}{'Uncal MAE':<12}{'Cal MAE':<10}{'Consensus MAE':<15}{'n':<5}")
    for season, grp in df.groupby("season"):
        cons = grp.dropna(subset=["consensus_abs_err"]) if "consensus_abs_err" in grp else pd.DataFrame()
        cons_mae = f"{cons['consensus_abs_err'].mean():.2f}" if not cons.empty else "n/a"
        print(f"  {season:<8}{grp['uncal_abs_err'].mean():<12.2f}{grp['cal_abs_err'].mean():<10.2f}{cons_mae:<15}{len(grp):<5}")
    overall_cons = df.dropna(subset=["consensus_abs_err"]) if "consensus_abs_err" in df else pd.DataFrame()
    overall_cons_mae = f"{overall_cons['consensus_abs_err'].mean():.2f}" if not overall_cons.empty else "n/a"
    print(f"  {'ALL':<8}{df['uncal_abs_err'].mean():<12.2f}{df['cal_abs_err'].mean():<10.2f}{overall_cons_mae:<15}{len(df):<5}")

    metrics_df = pd.DataFrame(metric_rows)
    print(f"\n{'=' * 70}\n  Per-Game NN Metrics: Uncalibrated vs Calibrated\n{'=' * 70}")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
