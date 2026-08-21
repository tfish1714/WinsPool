"""services/xgb_prediction_service.py -- XGBoost NFL game prediction service.

Drop-in complement to NNPredictionService. Uses the same feature table,
same chronological train/val/test split, and same StandardScaler preprocessing.
Model persisted as .json (XGBoost native) + _scaler.pkl.

Usage:
    svc = XGBPredictionService()
    metrics = svc.train(feature_table)
    prob = svc.predict_game(features_dict)   # → float in [0, 1]
    svc.save_versioned()
"""

import json
import logging
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    logger.warning("xgboost not installed. XGBPredictionService will be unavailable.")

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        accuracy_score, log_loss, brier_score_loss,
        roc_auc_score, r2_score, mean_absolute_error,
    )
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

from services.nn_feature_engine import FEATURE_COLUMNS

MODEL_DIR = Path(__file__).parent.parent / "models"
REGISTRY_PATH = MODEL_DIR / "xgb_registry.json"
LABEL_COLUMN = "home_win"

# Validation/test split mirrors NNPredictionService
VALIDATION_SPLIT_WEEK = 14
TEST_SPLIT_WEEK = 15

# XGBoost hyperparameters (tuned for ~5k NFL game samples)
XGB_PARAMS = {
    "n_estimators":      1000,   # early stopping decides actual count
    "learning_rate":     0.02,   # slower learning → better generalization
    "max_depth":         3,      # shallower trees reduce memorization
    "subsample":         0.70,   # more variance injection per tree
    "colsample_bytree":  0.60,   # more feature randomness per tree
    "min_child_weight":  20,     # leaf needs ~0.4% of data minimum
    "gamma":             1.0,    # prune unless gain is meaningful
    "reg_alpha":         0.5,    # L1 sparsity
    "reg_lambda":        2.0,    # L2 weight decay
    "objective":         "binary:logistic",
    "eval_metric":       "logloss",
    "random_state":      42,
    "n_jobs":            -1,
}


class XGBPredictionService:
    """XGBoost classifier for NFL home-win probability prediction."""

    def __init__(self):
        self.model: Optional[object] = None
        self.scaler: Optional[object] = None
        self._eval_metrics: Optional[dict] = None
        self._is_trained = False
        self.loaded_version: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Data splitting (identical to NNPredictionService)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_data(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        test_season = int(df["season"].max())
        train = df[df["season"] < test_season].copy()
        val   = df[(df["season"] == test_season) & (df["week"] <= VALIDATION_SPLIT_WEEK)].copy()
        test  = df[(df["season"] == test_season) & (df["week"] > TEST_SPLIT_WEEK)].copy()
        return train, val, test

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #

    def train(self, feature_table: pd.DataFrame) -> dict:
        if not XGB_AVAILABLE:
            raise RuntimeError("xgboost is required but not installed. pip install xgboost")
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn is required but not installed.")

        train_df, val_df, test_df = self._split_data(feature_table)
        if train_df.empty:
            raise ValueError("Training set is empty.")

        # XGBoost binary:logistic requires integer {0,1} labels; NFL ties (0.5) are dropped
        train_df = train_df[train_df[LABEL_COLUMN] != 0.5].copy()
        if not val_df.empty:
            val_df  = val_df[val_df[LABEL_COLUMN] != 0.5].copy()
        if not test_df.empty:
            test_df = test_df[test_df[LABEL_COLUMN] != 0.5].copy()

        X_train = train_df[FEATURE_COLUMNS].values.astype(np.float32)
        y_train = train_df[LABEL_COLUMN].values.astype(int)
        X_val   = val_df[FEATURE_COLUMNS].values.astype(np.float32)   if not val_df.empty  else None
        y_val   = val_df[LABEL_COLUMN].values.astype(int)             if not val_df.empty  else None
        X_test  = test_df[FEATURE_COLUMNS].values.astype(np.float32)  if not test_df.empty else None
        y_test  = test_df[LABEL_COLUMN].values.astype(int)            if not test_df.empty else None

        # Scale (keeps API consistent with NN; XGB doesn't strictly require it)
        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        if X_val  is not None: X_val  = self.scaler.transform(X_val)
        if X_test is not None: X_test = self.scaler.transform(X_test)

        # Build & fit with early stopping on validation log-loss
        logger.info("Training XGB: %d samples, %d features", len(X_train), len(FEATURE_COLUMNS))
        self.model = xgb.XGBClassifier(**XGB_PARAMS)

        fit_kwargs: dict = {"verbose": False}
        if X_val is not None:
            fit_kwargs["eval_set"] = [(X_val, y_val)]
            self.model.set_params(early_stopping_rounds=50)

        self.model.fit(X_train, y_train, **fit_kwargs)
        self._is_trained = True

        metrics = self._evaluate(X_train, y_train, X_test, y_test)
        season_metrics = self._evaluate_season_level(train_df, feature_table)
        metrics.update(season_metrics)
        self._eval_metrics = metrics
        return metrics

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def _evaluate(self, X_train, y_train, X_test, y_test) -> dict:
        metrics: dict = {}

        train_prob = self.model.predict_proba(X_train)[:, 1]
        train_pred = (train_prob > 0.5).astype(int)
        metrics["train_accuracy"]    = float(accuracy_score(y_train > 0.5, train_pred))
        metrics["train_logloss"]     = float(log_loss(y_train, train_prob))
        metrics["train_brier"]       = float(brier_score_loss(y_train > 0.5, train_prob))
        metrics["train_r2"]          = float(r2_score(y_train, train_prob))
        metrics["n_estimators_used"] = int(self.model.best_iteration + 1) if hasattr(self.model, "best_iteration") else XGB_PARAMS["n_estimators"]

        if X_test is not None and len(X_test) > 0:
            test_prob = self.model.predict_proba(X_test)[:, 1]
            test_pred = (test_prob > 0.5).astype(int)
            metrics["test_accuracy"] = float(accuracy_score(y_test > 0.5, test_pred))
            metrics["test_logloss"]  = float(log_loss(y_test, test_prob))
            metrics["test_brier"]    = float(brier_score_loss(y_test > 0.5, test_prob))
            metrics["test_r2"]       = float(r2_score(y_test, test_prob))
            if len(np.unique(y_test)) > 1:
                metrics["test_auc"]  = float(roc_auc_score(y_test > 0.5, test_prob))
        else:
            for k in ("test_accuracy", "test_logloss", "test_brier", "test_r2", "test_auc"):
                metrics[k] = None

        return metrics

    def _evaluate_season_level(self, train_df: pd.DataFrame, full_table: pd.DataFrame) -> dict:
        eval_seasons = sorted(train_df["season"].unique())[-5:]
        eval_df = full_table[full_table["season"].isin(eval_seasons)].copy()
        if eval_df.empty:
            return {"season_r2": None, "season_mae": None, "season_n": 0}

        X_eval = self.scaler.transform(eval_df[FEATURE_COLUMNS].values.astype(np.float32))
        eval_df = eval_df.copy()
        eval_df["pred_prob"] = self.model.predict_proba(X_eval)[:, 1]

        # Build team-season predicted wins
        records = []
        for (season, team), grp in eval_df.groupby(["season", "home_team"]):
            records.append({"season": season, "team": team,
                            "pred_wins": grp["pred_prob"].sum(),
                            "actual_wins": (grp["home_win"] == 1.0).sum()})
        for (season, team), grp in eval_df.groupby(["season", "away_team"]):
            pred_away = (1 - grp["pred_prob"]).sum()
            actual_away = (grp["home_win"] == 0.0).sum()
            records.append({"season": season, "team": team,
                            "pred_wins": pred_away, "actual_wins": actual_away})

        season_df = pd.DataFrame(records).groupby(["season", "team"]).sum().reset_index()
        if len(season_df) < 5:
            return {"season_r2": None, "season_mae": None, "season_n": len(season_df)}

        return {
            "season_r2":  float(r2_score(season_df["actual_wins"], season_df["pred_wins"])),
            "season_mae": float(mean_absolute_error(season_df["actual_wins"], season_df["pred_wins"])),
            "season_n":   len(season_df),
        }

    # ------------------------------------------------------------------ #
    # Feature importance
    # ------------------------------------------------------------------ #

    def feature_importance(self, top_n: int = 15) -> pd.DataFrame:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")
        scores = self.model.feature_importances_
        df = pd.DataFrame({"feature": FEATURE_COLUMNS, "importance": scores})
        return df.sort_values("importance", ascending=False).head(top_n).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    def predict_game(self, features: dict) -> float:
        """Return home-win probability for a single game.
        features: dict mapping each FEATURE_COLUMN name to its value.
        """
        if not self._is_trained:
            raise RuntimeError("Model not trained. Call train() or load_model() first.")
        X = np.array([[features.get(c, 0.0) for c in FEATURE_COLUMNS]], dtype=np.float32)
        X = self.scaler.transform(X)
        return float(self.model.predict_proba(X)[0, 1])

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def save_versioned(self, version: Optional[str] = None,
                       training_params: Optional[dict] = None) -> str:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")

        registry = {}
        if REGISTRY_PATH.exists():
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)

        if version is None:
            existing = [k for k in registry if k.startswith("v") and k[1:].isdigit()]
            version = f"v{max((int(k[1:]) for k in existing), default=0) + 1}"

        model_path  = MODEL_DIR / f"xgb_{version}.json"
        scaler_path = MODEL_DIR / f"xgb_{version}_scaler.pkl"

        self.model.save_model(str(model_path))
        with open(scaler_path, "wb") as f:
            pickle.dump(self.scaler, f)

        entry = {
            "model_path":       f"models/xgb_{version}.json",
            "scaler_path":      f"models/xgb_{version}_scaler.pkl",
            "feature_columns":  FEATURE_COLUMNS,
            "n_features":       len(FEATURE_COLUMNS),
            "metrics":          self._eval_metrics or {},
            "params":           XGB_PARAMS,
            "training_params":  training_params or {},
            "trained_at":       datetime.now(timezone.utc).isoformat(),
        }
        registry[version] = entry

        # Track best by test_accuracy and season_r2
        best_by: dict = registry.get("best_by", {})
        for metric in ("test_accuracy", "season_r2"):
            val = (self._eval_metrics or {}).get(metric)
            if val is None:
                continue
            best_v = best_by.get(metric)
            if best_v is None or val > (registry.get(best_v, {}).get("metrics", {}).get(metric) or 0):
                best_by[metric] = version
        registry["best_by"] = best_by

        with open(REGISTRY_PATH, "w") as f:
            json.dump(registry, f, indent=2)

        logger.info("Versioned XGB model %s saved to %s", version, model_path)
        return version

    def load_model(self, version: str = "latest") -> None:
        if not XGB_AVAILABLE:
            raise RuntimeError("xgboost not installed.")

        registry = {}
        if REGISTRY_PATH.exists():
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)

        if version in ("latest", "best"):
            if version == "best":
                version = registry.get("best_by", {}).get("test_accuracy", "v1")
            else:
                versions = [k for k in registry if k.startswith("v") and k[1:].isdigit()]
                version = max(versions, key=lambda v: int(v[1:])) if versions else "v1"

        entry = registry.get(version)
        if not entry:
            raise FileNotFoundError(f"XGB version '{version}' not found in registry.")

        # Reconstruct from MODEL_DIR + version rather than trusting the
        # registry's stored path literally -- older registry entries have
        # an absolute path baked in from whichever machine trained that
        # version (e.g. a Windows dev path), which doesn't exist in a
        # deployed container. Matches nn_prediction_service.py's pattern.
        model_path = MODEL_DIR / f"xgb_{version}.json"
        scaler_path = MODEL_DIR / f"xgb_{version}_scaler.pkl"

        self.model = xgb.XGBClassifier()
        self.model.load_model(str(model_path))
        with open(scaler_path, "rb") as f:
            self.scaler = pickle.load(f)
        self._is_trained = True
        self.loaded_version = version
        logger.info("Loaded XGB model %s from %s", version, model_path)
