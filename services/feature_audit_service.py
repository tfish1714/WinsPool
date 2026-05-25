"""services/feature_audit_service.py — Per-game ML audit trail computation.

Computes raw features, scaled features, per-model probabilities, and blended
feature importance for every game in a feature table.

Feature importance methods:
  - NN:  input × gradient (first-order attribution via tf.GradientTape)
  - XGB: native SHAP via get_booster().predict(pred_contribs=True)
  - LR:  coef_ * scaled_feature_value (exact linear contribution)

Blend uses full ensemble weights: 0.45 × nn_grad + 0.20 × xgb_shap + 0.35 × lr_contrib
"""
import logging
from typing import Any

import numpy as np
import pandas as pd

try:
    import tensorflow as tf
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

from services.nn_feature_engine import FEATURE_COLUMNS, _normalize_team
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT

logger = logging.getLogger(__name__)


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Convert v to float, returning default for None/NaN."""
    try:
        f = float(v)
        return default if f != f else f   # f != f is the NaN check
    except (TypeError, ValueError):
        return default


def _nn_gradient_importance(model, X_scaled: np.ndarray) -> np.ndarray:
    """Compute input × gradient as a first-order attribution proxy for the NN.

    Uses tf.GradientTape to get d(output)/d(input), then multiplies by the
    scaled input value.  Shape: (n_games, n_features).

    Falls back to zeros if TF is unavailable or gradient computation fails.
    """
    if not TF_AVAILABLE:
        return np.zeros_like(X_scaled)
    try:
        X_tensor = tf.constant(X_scaled, dtype=tf.float32)
        with tf.GradientTape() as tape:
            tape.watch(X_tensor)
            pred = model(X_tensor, training=False)   # (n, 1)
        grads = tape.gradient(pred, X_tensor)        # (n, n_features)
        if grads is None:
            logger.warning("NN gradient is None (no graph path to input); using zeros for NN importance.")
            return np.zeros_like(X_scaled)
        return (X_scaled * grads.numpy()).astype(np.float32)
    except Exception:
        logger.warning("NN gradient computation failed; using zeros for NN importance.")
        return np.zeros_like(X_scaled)


def compute_feature_audit(
    feature_table: pd.DataFrame,
    nn_svc,
    xgb_svc,
    lr_svc,
) -> list[dict]:
    """Compute per-game audit records for every row in feature_table.

    Args:
        feature_table: DataFrame from build_master_feature_table(); must contain
                       all FEATURE_COLUMNS plus season, week, home_team, away_team.
        nn_svc:  Loaded NNPredictionService  (model + scaler + loaded_version).
        xgb_svc: Loaded XGBPredictionService (model + scaler + loaded_version).
        lr_svc:  Loaded LRPredictionService  (model + scaler + loaded_version).

    Returns:
        List of dicts, one per game, in feature_table row order. Each dict:
          game_key, season, week, away_team, home_team,
          nn_prob, xgb_prob, lr_prob, blended_prob,
          features (raw), scaled_features (post-nn-scaler),
          feature_importance (list of N items sorted by |score| desc).
    """
    if feature_table.empty:
        return []

    n_features = len(FEATURE_COLUMNS)
    X = feature_table[FEATURE_COLUMNS].values.astype(np.float32)
    n = X.shape[0]

    # ── Scaled inputs (each model has its own StandardScaler) ─────────────
    X_nn  = np.array(nn_svc.scaler.transform(X),  dtype=np.float32)
    X_xgb = np.array(xgb_svc.scaler.transform(X), dtype=np.float32)
    X_lr  = np.array(lr_svc.scaler.transform(X),  dtype=np.float32)

    # ── Per-model probabilities ────────────────────────────────────────────
    nn_probs  = np.array(nn_svc.model.predict(X_nn, verbose=0)).flatten()
    xgb_probs = xgb_svc.model.predict_proba(X_xgb)[:, 1]
    lr_probs  = lr_svc.model.predict_proba(X_lr)[:, 1]
    blended   = np.clip(
        NN_WEIGHT * nn_probs + XGB_WEIGHT * xgb_probs + LR_WEIGHT * lr_probs,
        0.02, 0.98,
    )

    # ── NN importance: input × gradient ───────────────────────────────────
    nn_grad = _nn_gradient_importance(nn_svc.model, X_nn)   # (n, n_features)

    # ── XGB SHAP (native pred_contribs) ───────────────────────────────────
    if XGB_AVAILABLE:
        dmatrix  = xgb.DMatrix(X_xgb)
        raw_shap = xgb_svc.model.get_booster().predict(dmatrix, pred_contribs=True)
        xgb_shap = raw_shap[:, :-1].astype(np.float32)      # drop bias → (n, n_features)
    else:
        xgb_shap = np.zeros((n, n_features), dtype=np.float32)

    # ── LR exact contributions: coef * scaled_feature ─────────────────────
    lr_coef    = np.array(lr_svc.model.coef_[0], dtype=np.float32)  # (n_features,)
    lr_contrib = X_lr * lr_coef                                       # (n, n_features)

    # ── Blended importance at full ensemble weights ────────────────────────
    blended_imp = (
        NN_WEIGHT  * nn_grad    +   # 0.45
        XGB_WEIGHT * xgb_shap   +   # 0.20
        LR_WEIGHT  * lr_contrib      # 0.35
    )                                # (n, n_features)

    # ── Assemble records ───────────────────────────────────────────────────
    records = []
    for i, row in enumerate(feature_table.itertuples(index=False)):
        season = int(getattr(row, "season", 0))
        week   = int(getattr(row, "week", 0))
        ht     = _normalize_team(str(getattr(row, "home_team", "") or ""))
        at     = _normalize_team(str(getattr(row, "away_team", "") or ""))
        game_key = f"W{week:02d}_{ht}_{at}"

        raw_features    = {col: round(_safe_float(getattr(row, col, 0.0)), 4)
                           for col in FEATURE_COLUMNS}
        # Note: scaled_features uses the NN scaler — may differ slightly from XGB/LR scalers
        scaled_features = {col: round(float(X_nn[i, j]), 4)
                           for j, col in enumerate(FEATURE_COLUMNS)}

        imp_row = blended_imp[i]
        feature_importance = sorted(
            [
                {
                    "feature":   col,
                    "score":     round(float(imp_row[j]), 4),
                    "direction": "home" if imp_row[j] >= 0 else "away",
                }
                for j, col in enumerate(FEATURE_COLUMNS)
            ],
            key=lambda x: abs(x["score"]),
            reverse=True,
        )

        records.append({
            "game_key":           game_key,
            "season":             season,
            "week":               week,
            "away_team":          at,
            "home_team":          ht,
            "nn_prob":            round(float(nn_probs[i]), 4),
            "xgb_prob":           round(float(xgb_probs[i]), 4),
            "lr_prob":            round(float(lr_probs[i]), 4),
            "blended_prob":       round(float(blended[i]), 4),
            "features":           raw_features,
            "scaled_features":    scaled_features,
            "feature_importance": feature_importance,
        })

    return records
