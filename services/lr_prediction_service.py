"""services/lr_prediction_service.py -- Logistic Regression NFL prediction service.

ElasticNet-regularized logistic regression. Maximally resistant to overfitting
on small datasets (~5k games). Acts as a calibration anchor in the ensemble —
when NN and XGB agree but are overconfident, LR pulls toward the base rate.

Coefficients are interpretable: positive = home team advantage, negative = away.

Usage:
    svc = LRPredictionService()
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
    from sklearn.linear_model import LogisticRegressionCV
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        accuracy_score, log_loss, brier_score_loss,
        roc_auc_score, r2_score, mean_absolute_error,
    )
    from sklearn.calibration import CalibratedClassifierCV
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not installed. LRPredictionService unavailable.")

from services.nn_feature_engine import FEATURE_COLUMNS

MODEL_DIR    = Path(__file__).parent.parent / "models"
REGISTRY_PATH = MODEL_DIR / "lr_registry.json"
LABEL_COLUMN  = "home_win"

VALIDATION_SPLIT_WEEK = 14
TEST_SPLIT_WEEK       = 15

# ElasticNet regularization: CV selects best C from this grid
# l1_ratio=0.5 balances sparsity (L1) with weight decay (L2)
LR_PARAMS = {
    "Cs":         [0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
    "cv":         5,
    "penalty":    "elasticnet",
    "solver":     "saga",
    "l1_ratios":  [0.1, 0.3, 0.5, 0.7, 0.9],
    "max_iter":   2000,
    "random_state": 42,
    "n_jobs":     -1,
    "scoring":    "neg_log_loss",
}


class LRPredictionService:
    """ElasticNet Logistic Regression for NFL home-win probability prediction."""

    def __init__(self):
        self.model:  Optional[object] = None
        self.scaler: Optional[object] = None
        self._eval_metrics: Optional[dict] = None
        self._is_trained = False
        self.loaded_version: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Split (mirrors NN / XGB)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _split_data(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        test_season = int(df["season"].max())
        train = df[df["season"] < test_season].copy()
        val   = df[(df["season"] == test_season) & (df["week"] <= VALIDATION_SPLIT_WEEK)].copy()
        test  = df[(df["season"] == test_season) & (df["week"] >  TEST_SPLIT_WEEK)].copy()
        return train, val, test

    # ------------------------------------------------------------------ #
    # Training
    # ------------------------------------------------------------------ #

    def train(self, feature_table: pd.DataFrame) -> dict:
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn is required but not installed.")

        train_df, _, test_df = self._split_data(feature_table)

        # LR handles 0.5 ties gracefully (treat as 50/50 soft label via rounding)
        train_df = train_df[train_df[LABEL_COLUMN] != 0.5].copy()
        if not test_df.empty:
            test_df = test_df[test_df[LABEL_COLUMN] != 0.5].copy()

        X_train = train_df[FEATURE_COLUMNS].values.astype(np.float32)
        y_train = train_df[LABEL_COLUMN].values.astype(int)
        X_test  = test_df[FEATURE_COLUMNS].values.astype(np.float32) if not test_df.empty else None
        y_test  = test_df[LABEL_COLUMN].values.astype(int)           if not test_df.empty else None

        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        if X_test is not None:
            X_test = self.scaler.transform(X_test)

        logger.info("Training LR: %d samples, %d features", len(X_train), len(FEATURE_COLUMNS))
        self.model = LogisticRegressionCV(**LR_PARAMS)
        self.model.fit(X_train, y_train)
        self._is_trained = True

        metrics = self._evaluate(X_train, y_train, X_test, y_test)
        season_metrics = self._evaluate_season_level(train_df, feature_table)
        metrics.update(season_metrics)

        # Record selected hyperparameters
        metrics["best_C"]       = float(self.model.C_[0])
        metrics["best_l1_ratio"] = float(self.model.l1_ratio_[0])

        self._eval_metrics = metrics
        return metrics

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def _evaluate(self, X_train, y_train, X_test, y_test) -> dict:
        metrics: dict = {}

        train_prob = self.model.predict_proba(X_train)[:, 1]
        metrics["train_accuracy"] = float(accuracy_score(y_train, (train_prob > 0.5).astype(int)))
        metrics["train_logloss"]  = float(log_loss(y_train, train_prob))
        metrics["train_brier"]    = float(brier_score_loss(y_train, train_prob))
        metrics["train_r2"]       = float(r2_score(y_train, train_prob))

        if X_test is not None and len(X_test) > 0:
            test_prob = self.model.predict_proba(X_test)[:, 1]
            metrics["test_accuracy"] = float(accuracy_score(y_test, (test_prob > 0.5).astype(int)))
            metrics["test_logloss"]  = float(log_loss(y_test, test_prob))
            metrics["test_brier"]    = float(brier_score_loss(y_test, test_prob))
            metrics["test_r2"]       = float(r2_score(y_test, test_prob))
            if len(np.unique(y_test)) > 1:
                metrics["test_auc"]  = float(roc_auc_score(y_test, test_prob))
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

        records = []
        for (season, team), grp in eval_df.groupby(["season", "home_team"]):
            records.append({"season": season, "team": team,
                            "pred_wins": grp["pred_prob"].sum(),
                            "actual_wins": (grp["home_win"] == 1.0).sum()})
        for (season, team), grp in eval_df.groupby(["season", "away_team"]):
            records.append({"season": season, "team": team,
                            "pred_wins": (1 - grp["pred_prob"]).sum(),
                            "actual_wins": (grp["home_win"] == 0.0).sum()})

        season_df = pd.DataFrame(records).groupby(["season", "team"]).sum().reset_index()
        if len(season_df) < 5:
            return {"season_r2": None, "season_mae": None, "season_n": len(season_df)}

        return {
            "season_r2":  float(r2_score(season_df["actual_wins"], season_df["pred_wins"])),
            "season_mae": float(mean_absolute_error(season_df["actual_wins"], season_df["pred_wins"])),
            "season_n":   len(season_df),
        }

    # ------------------------------------------------------------------ #
    # Feature importance (coefficients)
    # ------------------------------------------------------------------ #

    def feature_importance(self, top_n: int = 15) -> pd.DataFrame:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")
        coefs = np.abs(self.model.coef_[0])
        df = pd.DataFrame({"feature": FEATURE_COLUMNS, "abs_coef": coefs,
                           "coef": self.model.coef_[0]})
        return df.sort_values("abs_coef", ascending=False).head(top_n).reset_index(drop=True)

    # ------------------------------------------------------------------ #
    # Inference
    # ------------------------------------------------------------------ #

    def predict_game(self, features: dict) -> float:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")
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

        model_path  = MODEL_DIR / f"lr_{version}.pkl"
        scaler_path = MODEL_DIR / f"lr_{version}_scaler.pkl"

        with open(model_path,  "wb") as f: pickle.dump(self.model,  f)
        with open(scaler_path, "wb") as f: pickle.dump(self.scaler, f)

        entry = {
            "model_path":      str(model_path),
            "scaler_path":     str(scaler_path),
            "feature_columns": FEATURE_COLUMNS,
            "n_features":      len(FEATURE_COLUMNS),
            "metrics":         self._eval_metrics or {},
            "params":          LR_PARAMS,
            "training_params": training_params or {},
            "trained_at":      datetime.now(timezone.utc).isoformat(),
        }
        registry[version] = entry

        best_by = registry.get("best_by", {})
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

        logger.info("LR model %s saved to %s", version, model_path)
        return version

    def load_model(self, version: str = "latest") -> None:
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn not installed.")

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
            raise FileNotFoundError(f"LR version '{version}' not found in registry.")

        with open(entry["model_path"],  "rb") as f: self.model  = pickle.load(f)
        with open(entry["scaler_path"], "rb") as f: self.scaler = pickle.load(f)
        self._is_trained = True
        self.loaded_version = version
        logger.info("Loaded LR model %s", version)
