"""scripts/walk_forward_validate.py -- Walk-forward validation harness.

Trains the NN+XGB+LR ensemble on strictly-prior seasons for each of five
expanding-window folds (2021-2025), generates an honest out-of-sample season
projection per fold via the real NNProjectionEngine Monte Carlo path, and
scores it against actual wins and analyst consensus.

Diagnostic only: writes to reports/, never to preseason_predictions, and
never touches model_registry.json / xgb_registry.json / lr_registry.json.

Usage:
    python scripts/walk_forward_validate.py
    python scripts/walk_forward_validate.py --seasons 2021 2025
    python scripts/walk_forward_validate.py --force   # retrain even if cached
"""

import argparse
import pathlib
import pickle
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MODEL_DIR = pathlib.Path(__file__).parent.parent / "models"
ARTIFACTS_DIR = MODEL_DIR / "walkforward"
REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"

FOLD_START_DEFAULT = 2021
FOLD_END_DEFAULT = 2025


def _save_fold_artifacts(artifacts_dir, fold_year, nn_svc, xgb_svc, lr_svc,
                          skip_nn=False, skip_xgb=False, skip_lr=False):
    """Save one fold's trained models directly to disk, bypassing every
    service's registry-integrated save path."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if not skip_nn:
        import joblib
        nn_path = artifacts_dir / f"nn_{fold_year}.keras"
        nn_svc.model.save(str(nn_path))
        joblib.dump(nn_svc.scaler, str(artifacts_dir / f"nn_{fold_year}_scaler.pkl"))

    if not skip_xgb:
        xgb_path = artifacts_dir / f"xgb_{fold_year}.json"
        xgb_svc.model.save_model(str(xgb_path))
        with open(artifacts_dir / f"xgb_{fold_year}_scaler.pkl", "wb") as f:
            pickle.dump(xgb_svc.scaler, f)

    if not skip_lr:
        with open(artifacts_dir / f"lr_{fold_year}.pkl", "wb") as f:
            pickle.dump(lr_svc.model, f)
        with open(artifacts_dir / f"lr_{fold_year}_scaler.pkl", "wb") as f:
            pickle.dump(lr_svc.scaler, f)


def _fold_artifacts_exist(artifacts_dir, fold_year):
    """True only when all three model types' files are present for this fold --
    fold artifacts are all-or-nothing, never partially resumed."""
    required = [
        f"nn_{fold_year}.keras", f"nn_{fold_year}_scaler.pkl",
        f"xgb_{fold_year}.json", f"xgb_{fold_year}_scaler.pkl",
        f"lr_{fold_year}.pkl", f"lr_{fold_year}_scaler.pkl",
    ]
    return all((artifacts_dir / name).exists() for name in required)


def _load_fold_artifacts(artifacts_dir, fold_year, load_nn=True, load_xgb=True, load_lr=True):
    """Load one fold's models back from disk. Returns (nn_svc, xgb_svc, lr_svc);
    any skipped slot is None."""
    nn_svc = None
    if load_nn:
        from services.nn_prediction_service import NNPredictionService
        nn_svc = NNPredictionService()
        nn_svc.load_model(path=str(artifacts_dir / f"nn_{fold_year}.keras"))

    xgb_svc = None
    if load_xgb:
        import xgboost as xgb
        from services.xgb_prediction_service import XGBPredictionService
        xgb_svc = XGBPredictionService()
        xgb_svc.model = xgb.XGBClassifier()
        xgb_svc.model.load_model(str(artifacts_dir / f"xgb_{fold_year}.json"))
        with open(artifacts_dir / f"xgb_{fold_year}_scaler.pkl", "rb") as f:
            xgb_svc.scaler = pickle.load(f)
        xgb_svc._is_trained = True

    lr_svc = None
    if load_lr:
        from services.lr_prediction_service import LRPredictionService
        lr_svc = LRPredictionService()
        with open(artifacts_dir / f"lr_{fold_year}.pkl", "rb") as f:
            lr_svc.model = pickle.load(f)
        with open(artifacts_dir / f"lr_{fold_year}_scaler.pkl", "rb") as f:
            lr_svc.scaler = pickle.load(f)
        lr_svc._is_trained = True

    return nn_svc, xgb_svc, lr_svc


def _nn_permutation_importance(nn_svc, val_df):
    """Permutation importance: shuffle one feature at a time, measure MAE
    degradation on the fold's own held-out validation split. NN has no
    built-in importance measure the way XGB/LR do, so this is its equivalent."""
    import numpy as np
    import pandas as pd
    from sklearn.metrics import mean_absolute_error
    from services.nn_feature_engine import FEATURE_COLUMNS
    from services.nn_prediction_service import LABEL_COLUMN

    X_val = val_df[FEATURE_COLUMNS].values.astype("float32")
    y_val = val_df[LABEL_COLUMN].values.astype("float32")
    X_scaled = nn_svc.scaler.transform(X_val)

    baseline_pred = nn_svc.model.predict(X_scaled, verbose=0).flatten()
    baseline_mae = mean_absolute_error(y_val, baseline_pred)

    rng = np.random.default_rng(42)
    rows = []
    for i, feature in enumerate(FEATURE_COLUMNS):
        X_shuffled = X_scaled.copy()
        rng.shuffle(X_shuffled[:, i])
        shuffled_pred = nn_svc.model.predict(X_shuffled, verbose=0).flatten()
        shuffled_mae = mean_absolute_error(y_val, shuffled_pred)
        rows.append({"feature": feature, "importance": shuffled_mae - baseline_mae})

    result = pd.DataFrame(rows).sort_values("importance", ascending=False).reset_index(drop=True)
    result["importance_rank"] = result.index + 1
    return result


def _collect_feature_importance(fold_year, nn_svc, xgb_svc, lr_svc, val_df):
    """Combine all three models' feature importance into the report schema:
    season, model, feature, importance_rank, importance_value."""
    import pandas as pd
    from services.nn_feature_engine import FEATURE_COLUMNS

    rows = []

    # top_n defaults to 15 and truncates -- pass the true feature count
    # explicitly so this stays the full, untruncated list even as the
    # feature set grows past 15.
    xgb_imp = xgb_svc.feature_importance(top_n=len(FEATURE_COLUMNS))
    for rank, (_, r) in enumerate(xgb_imp.iterrows(), start=1):
        rows.append({"season": fold_year, "model": "xgb", "feature": r["feature"],
                      "importance_rank": rank, "importance_value": float(r["importance"])})

    lr_imp = lr_svc.feature_importance(top_n=len(FEATURE_COLUMNS))
    for rank, (_, r) in enumerate(lr_imp.iterrows(), start=1):
        rows.append({"season": fold_year, "model": "lr", "feature": r["feature"],
                      "importance_rank": rank, "importance_value": float(r["abs_coef"])})

    nn_imp = _nn_permutation_importance(nn_svc, val_df)
    for _, r in nn_imp.iterrows():
        rows.append({"season": fold_year, "model": "nn", "feature": r["feature"],
                      "importance_rank": int(r["importance_rank"]),
                      "importance_value": float(r["importance"])})

    return pd.DataFrame(rows)
