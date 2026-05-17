"""services/nn_prediction_service.py -- Neural Network NFL Prediction Service.

Provides a TensorFlow/Keras feedforward neural network for predicting NFL
game outcomes (home win probability) and projecting season win totals.

Architecture: Input(24) -> Dense(48,ReLU) -> Dropout(0.4) ->
              Dense(24,ReLU) -> Dropout(0.4) -> Dense(1,Sigmoid)
Loss: Binary cross-entropy

This service is entirely additive; the existing PredictionService
(Elo + Pythagorean) is not modified.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Attempt TensorFlow import with graceful fallback
try:
    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
    import tensorflow as tf
    from tensorflow import keras
    from tensorflow.keras import layers, callbacks
    TF_AVAILABLE = True
except ImportError:
    TF_AVAILABLE = False
    tf = None
    logger.warning("TensorFlow not installed. NNPredictionService will be unavailable.")

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import r2_score, mean_absolute_error, accuracy_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_DIR = Path(__file__).parent.parent / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "nn_v1.keras"
REGISTRY_PATH = MODEL_DIR / "model_registry.json"

from services.nn_feature_engine import FEATURE_COLUMNS, _normalize_team
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT

LABEL_COLUMN = "home_win"

# Training hyperparameters
EPOCHS = 200
BATCH_SIZE = 64
LEARNING_RATE = 0.0005
DROPOUT_RATE = 0.4
EARLY_STOP_PATIENCE = 20
VALIDATION_SPLIT_WEEK = 14  # 2025 weeks 1-14 for validation
TEST_SPLIT_WEEK = 15         # 2025 weeks 15+ for testing




# ---------------------------------------------------------------------------
# NNPredictionService
# ---------------------------------------------------------------------------

def build_ensemble_lookup(feature_table, nn_svc, xgb_svc, lr_svc) -> dict:
    """Run the NN+XGB+LR ensemble on every row in feature_table.

    Returns {(season, week, home_team, away_team): pred_dict} where pred_dict
    contains pred_prob, pred_winner, pred_su_conf, pred_ats_pick.
    Completed games only — the feature table has no rows for unplayed games.
    """
    import numpy as np
    if feature_table.empty:
        return {}

    X = feature_table[FEATURE_COLUMNS].values.astype(np.float32)
    nn_p  = nn_svc.model.predict(nn_svc.scaler.transform(X), verbose=0).flatten()
    xgb_p = xgb_svc.model.predict_proba(xgb_svc.scaler.transform(X))[:, 1]
    lr_p  = lr_svc.model.predict_proba(lr_svc.scaler.transform(X))[:, 1]
    blended = np.clip(NN_WEIGHT * nn_p + XGB_WEIGHT * xgb_p + LR_WEIGHT * lr_p, 0.02, 0.98)

    lookup = {}
    for i, row in enumerate(feature_table.itertuples(index=False)):
        hp     = float(blended[i])
        ht     = _normalize_team(row.home_team)
        at     = _normalize_team(row.away_team)
        spread = getattr(row, "spread_line", None)

        winner = ht if hp >= 0.5 else at
        conf   = round(min(99.0, max(50.0, (hp if hp >= 0.5 else 1.0 - hp) * 100)), 1)

        # ATS pick: compare ML-implied spread to Vegas spread.
        # implied_spread = -7.5 * log(hp / (1-hp)); negative = home favored.
        # If ML thinks home is undervalued vs the line, take home; else take away.
        ats = winner
        if spread is not None and not (isinstance(spread, float) and np.isnan(spread)):
            try:
                sv = float(spread)
                hp_clip = np.clip(hp, 0.02, 0.98)
                implied = -7.5 * np.log(hp_clip / (1.0 - hp_clip))
                ats = ht if implied < sv else at
            except (ValueError, TypeError):
                pass

        def _f(attr, default=0.0):
            v = getattr(row, attr, default)
            try:
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else default
            except (TypeError, ValueError):
                return default

        sl = _f("spread_line", None)
        vegas_home_prob = None
        if sl is not None:
            try:
                vegas_home_prob = round(1 / (1 + np.exp(sl / 7.5)), 4)
            except Exception:
                pass

        explanation = {
            "vegas_line":       round(sl, 1) if sl is not None else None,
            "vegas_home_prob":  vegas_home_prob,
            "elo_diff":         round(_f("tm_elo_pre") - _f("opp_elo_pre"), 1),
            "roster_delta":     round(_f("roster_talent_delta"), 3),
            "off_pass_epa":     round(_f("off_pass_epa"), 3),
            "def_pass_epa":     round(_f("def_pass_epa"), 3),
            "off_rush_epa":     round(_f("off_rush_epa"), 3),
            "def_rush_epa":     round(_f("def_rush_epa"), 3),
            "turnover_margin":  round(_f("turnover_margin_rolling"), 2),
            "qb_injury":        round(_f("qb_injury_flag"), 1),
            "home_qb_out":      round(_f("home_qb_out", 0.0), 1),
            "away_qb_out":      round(_f("away_qb_out", 0.0), 1),
            "rest_disadvantage":round(_f("travel_rest_disadvantage"), 2),
            "trench_dominance": round(_f("trench_dominance_metric"), 3),
            "off_roster_value": round(_f("off_roster_value_delta"), 3),
            "def_roster_value": round(_f("def_roster_value_delta"), 3),
        }

        lookup[(int(row.season), int(row.week), ht, at)] = {
            "pred_prob":     round(hp, 4),
            "pred_winner":   winner,
            "pred_su_conf":  conf,
            "pred_ats_pick": ats,
            "explanation":   explanation,
        }
    return lookup


class NNPredictionService:
    """Feedforward Neural Network for NFL game prediction.

    Usage:
        svc = NNPredictionService()
        metrics = svc.train(feature_table)
        prob = svc.predict_game(home_features, away_features)
        svc.save_model()
    """

    def __init__(self):
        self.model: Optional[object] = None
        self.scaler: Optional[object] = None
        self._train_history: Optional[dict] = None
        self._eval_metrics: Optional[dict] = None
        self._is_trained = False

    # ------------------------------------------------------------------
    # Model Architecture
    # ------------------------------------------------------------------

    @staticmethod
    def _build_model(input_dim: int) -> object:
        """Construct a 48-24 feedforward neural network.

        Smaller than the original 96-48-24 to prevent rapid overfitting on
        ~5000 training samples. Higher dropout (0.4) and lower learning rate
        (0.0005) further regularize the fit.

        Args:
            input_dim: Number of input features.

        Returns:
            Compiled Keras Sequential model.
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow is required but not installed.")

        model = keras.Sequential([
            layers.Input(shape=(input_dim,)),
            layers.Dense(48, activation="relu",
                         kernel_regularizer=keras.regularizers.l2(1e-4)),
            layers.Dropout(DROPOUT_RATE),
            layers.Dense(24, activation="relu",
                         kernel_regularizer=keras.regularizers.l2(1e-4)),
            layers.Dropout(DROPOUT_RATE),
            layers.Dense(1, activation="sigmoid"),
        ])

        model.compile(
            optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
            loss="binary_crossentropy",
            metrics=["accuracy"],
        )
        return model

    # ------------------------------------------------------------------
    # Data Splitting (Chronological)
    # ------------------------------------------------------------------

    @staticmethod
    def _split_data(df: pd.DataFrame) -> Tuple[
        pd.DataFrame, pd.DataFrame, pd.DataFrame
    ]:
        """Split the feature table chronologically.

        Train: seasons < 2025
        Validation: season == 2025, week <= 14
        Test: season == 2025, week > 14

        Args:
            df: The Master Feature Table.

        Returns:
            (train_df, val_df, test_df)
        """
        # Use the most recent season in the table as the test/val season
        test_season = int(df["season"].max())
        train = df[df["season"] < test_season].copy()
        val = df[(df["season"] == test_season) & (df["week"] <= VALIDATION_SPLIT_WEEK)].copy()
        test = df[(df["season"] == test_season) & (df["week"] > VALIDATION_SPLIT_WEEK)].copy()

        logger.info("Split sizes: train=%d, val=%d, test=%d",
                     len(train), len(val), len(test))
        return train, val, test

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, feature_table: pd.DataFrame) -> dict:
        """Train the NN on the provided feature table.

        Args:
            feature_table: DataFrame from build_master_feature_table().

        Returns:
            Dict of evaluation metrics including season-level R2.
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow is required but not installed.")
        if not SKLEARN_AVAILABLE:
            raise RuntimeError("scikit-learn is required but not installed.")

        # Split
        train_df, val_df, test_df = self._split_data(feature_table)

        if train_df.empty:
            raise ValueError("Training set is empty. Check season range.")

        # Extract features and labels
        X_train = train_df[FEATURE_COLUMNS].values.astype(np.float32)
        y_train = train_df[LABEL_COLUMN].values.astype(np.float32)

        X_val = val_df[FEATURE_COLUMNS].values.astype(np.float32) if not val_df.empty else None
        y_val = val_df[LABEL_COLUMN].values.astype(np.float32) if not val_df.empty else None

        X_test = test_df[FEATURE_COLUMNS].values.astype(np.float32) if not test_df.empty else None
        y_test = test_df[LABEL_COLUMN].values.astype(np.float32) if not test_df.empty else None

        # Scale
        self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        if X_val is not None:
            X_val = self.scaler.transform(X_val)
        if X_test is not None:
            X_test = self.scaler.transform(X_test)

        # Build model
        self.model = self._build_model(len(FEATURE_COLUMNS))

        # Callbacks
        cb = [
            callbacks.EarlyStopping(
                monitor="val_accuracy" if X_val is not None else "accuracy",
                mode="max",
                patience=EARLY_STOP_PATIENCE,
                restore_best_weights=True,
                verbose=1,
            ),
        ]

        # Train
        logger.info("Training NN: %d samples, %d features", len(X_train), len(FEATURE_COLUMNS))
        logger.info(
            "NFL NN Training | arch=%d-48-24-1 | train=%d val=%d test=%d",
            len(FEATURE_COLUMNS), len(X_train),
            len(X_val) if X_val is not None else 0,
            len(X_test) if X_test is not None else 0,
        )

        validation_data = (X_val, y_val) if X_val is not None else None

        history = self.model.fit(
            X_train, y_train,
            validation_data=validation_data,
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            callbacks=cb,
            verbose=1,
        )
        self._train_history = history.history
        self._is_trained = True

        # Game-level evaluation
        metrics = self._evaluate(X_train, y_train, X_test, y_test)

        # Season-level evaluation (the proper Pythagorean comparison)
        season_metrics = self._evaluate_season_level(train_df, feature_table)
        metrics.update(season_metrics)

        self._eval_metrics = metrics
        return metrics

    def _evaluate(
        self,
        X_train: np.ndarray, y_train: np.ndarray,
        X_test: Optional[np.ndarray], y_test: Optional[np.ndarray],
    ) -> dict:
        """Compute evaluation metrics on the test set.

        Returns:
            Dict with r2, mae, accuracy, train_r2, epochs_trained.
        """
        metrics = {
            "epochs_trained": len(self._train_history.get("loss", [])),
        }

        # Train set metrics
        train_preds = self.model.predict(X_train, verbose=0).flatten()
        metrics["train_r2"] = float(r2_score(y_train, train_preds))
        metrics["train_mae"] = float(mean_absolute_error(y_train, train_preds))
        metrics["train_accuracy"] = float(
            accuracy_score(y_train > 0.5, train_preds > 0.5)
        )

        # Test set metrics
        if X_test is not None and len(X_test) > 0:
            test_preds = self.model.predict(X_test, verbose=0).flatten()
            metrics["test_r2"] = float(r2_score(y_test, test_preds))
            metrics["test_mae"] = float(mean_absolute_error(y_test, test_preds))
            metrics["test_accuracy"] = float(
                accuracy_score(y_test > 0.5, test_preds > 0.5)
            )
        else:
            metrics["test_r2"] = None
            metrics["test_mae"] = None
            metrics["test_accuracy"] = None

        return metrics

    def _evaluate_season_level(self, train_df: pd.DataFrame,
                               full_table: pd.DataFrame) -> dict:
        """Aggregate game-level predictions into season win totals and compute R2.

        This is the proper apples-to-apples comparison with Pythagorean
        Expectation (R2 ~ 0.89). For each team-season, we sum the predicted
        win probabilities across all games and compare against actual wins.

        Uses only training data seasons to avoid data leakage.

        Returns:
            Dict with season_r2, season_mae, and season_n (sample count).
        """
        # Use the last 5 training seasons for season-level eval
        eval_seasons = sorted(train_df["season"].unique())[-5:]
        if len(eval_seasons) == 0:
            return {"season_r2": None, "season_mae": None, "season_n": 0}

        eval_data = full_table[full_table["season"].isin(eval_seasons)].copy()
        if eval_data.empty:
            return {"season_r2": None, "season_mae": None, "season_n": 0}

        # Predict all games
        X_eval = eval_data[FEATURE_COLUMNS].values.astype(np.float32)
        if self.scaler is not None:
            X_eval = self.scaler.transform(X_eval)
        eval_data = eval_data.copy()
        eval_data["pred_home_wp"] = self.model.predict(X_eval, verbose=0).flatten()

        # Aggregate to team-season level
        records = []
        for season in eval_seasons:
            s_data = eval_data[eval_data["season"] == season]
            all_teams = set(s_data["home_team"].unique()) | set(s_data["away_team"].unique())

            for team in all_teams:
                # Actual wins: count games where team won
                home_wins = s_data[
                    (s_data["home_team"] == team) & (s_data["home_win"] == 1.0)
                ].shape[0]
                away_wins = s_data[
                    (s_data["away_team"] == team) & (s_data["home_win"] == 0.0)
                ].shape[0]
                # Count ties as 0.5
                home_ties = s_data[
                    (s_data["home_team"] == team) & (s_data["home_win"] == 0.5)
                ].shape[0]
                away_ties = s_data[
                    (s_data["away_team"] == team) & (s_data["home_win"] == 0.5)
                ].shape[0]
                actual_wins = home_wins + away_wins + 0.5 * (home_ties + away_ties)

                # Predicted wins: sum probabilities
                home_pred = s_data[s_data["home_team"] == team]["pred_home_wp"].sum()
                away_pred = (1.0 - s_data[s_data["away_team"] == team]["pred_home_wp"]).sum()
                pred_wins = home_pred + away_pred

                total_games = (
                    s_data[s_data["home_team"] == team].shape[0]
                    + s_data[s_data["away_team"] == team].shape[0]
                )
                if total_games >= 10:  # Require at least 10 games for valid comparison
                    records.append({
                        "season": season, "team": team,
                        "actual_wins": actual_wins,
                        "pred_wins": pred_wins,
                        "games": total_games,
                    })

        if not records:
            return {"season_r2": None, "season_mae": None, "season_n": 0}

        results_df = pd.DataFrame(records)
        actual = results_df["actual_wins"].values
        predicted = results_df["pred_wins"].values

        return {
            "season_r2": float(r2_score(actual, predicted)),
            "season_mae": float(mean_absolute_error(actual, predicted)),
            "season_n": len(results_df),
        }

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict_game(self, features: dict) -> float:
        """Predict the home team win probability for a single game.

        Args:
            features: Dict with keys matching FEATURE_COLUMNS.

        Returns:
            Home win probability in [0, 1].
        """
        if not self._is_trained and self.model is None:
            raise RuntimeError("Model is not trained or loaded.")

        X = np.array([[features.get(c, 0.0) for c in FEATURE_COLUMNS]], dtype=np.float32)
        if self.scaler is not None:
            X = self.scaler.transform(X)
        pred = self.model.predict(X, verbose=0).flatten()[0]
        return float(np.clip(pred, 0.0, 1.0))

    def predict_season_wins(
        self, team: str, season: int, feature_table: pd.DataFrame
    ) -> dict:
        """Project total season wins for a team by summing game-level predictions.

        Args:
            team: Team abbreviation.
            season: Season year.
            feature_table: Full Master Feature Table (for the season).

        Returns:
            Dict with projected_wins, games, avg_win_prob.
        """
        # Get all games where this team is home or away
        home_games = feature_table[
            (feature_table["season"] == season) & (feature_table["home_team"] == team)
        ]
        away_games = feature_table[
            (feature_table["season"] == season) & (feature_table["away_team"] == team)
        ]

        total_win_prob = 0.0
        total_games = 0

        # Home games: predict directly
        if not home_games.empty:
            X = home_games[FEATURE_COLUMNS].values.astype(np.float32)
            if self.scaler is not None:
                X = self.scaler.transform(X)
            preds = self.model.predict(X, verbose=0).flatten()
            total_win_prob += float(np.sum(preds))
            total_games += len(preds)

        # Away games: probability of away team winning = 1 - home_win_prob
        if not away_games.empty:
            X = away_games[FEATURE_COLUMNS].values.astype(np.float32)
            if self.scaler is not None:
                X = self.scaler.transform(X)
            preds = self.model.predict(X, verbose=0).flatten()
            total_win_prob += float(np.sum(1.0 - preds))
            total_games += len(preds)

        avg_prob = total_win_prob / max(total_games, 1)
        return {
            "team": team,
            "projected_wins": round(total_win_prob, 2),
            "games": total_games,
            "avg_win_prob": round(avg_prob, 4),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_model(self, path: Optional[str] = None):
        """Save the trained model and scaler to disk.

        Args:
            path: Path for the .keras model file. Defaults to models/nn_v1.keras.
        """
        if self.model is None:
            raise RuntimeError("No model to save.")

        save_path = Path(path) if path else DEFAULT_MODEL_PATH
        save_path.parent.mkdir(parents=True, exist_ok=True)

        self.model.save(str(save_path))
        logger.info("Model saved to %s", save_path)

        # Save scaler
        if self.scaler is not None:
            import joblib
            scaler_path = save_path.parent / "nn_v1_scaler.pkl"
            joblib.dump(self.scaler, str(scaler_path))
            logger.info("Scaler saved to %s", scaler_path)

        logger.info("Model saved to %s", save_path)

    def load_model(self, path: Optional[str] = None):
        """Load a previously trained model and scaler from disk.

        Args:
            path: Path to the .keras model file. Pass "latest" or "best" to
                  resolve from the model registry. Defaults to models/nn_v1.keras.
        """
        if not TF_AVAILABLE:
            raise RuntimeError("TensorFlow is required but not installed.")

        if path in ("latest", "best") or path is None:
            registry = self._load_registry()
            if path == "best":
                version = registry.get("best_by", {}).get("season_r2") or registry.get("latest")
            else:
                version = registry.get("latest")

            if version:
                load_path = MODEL_DIR / f"nn_{version}.keras"
                scaler_path = MODEL_DIR / f"nn_{version}_scaler.pkl"
            else:
                load_path = DEFAULT_MODEL_PATH
                scaler_path = load_path.parent / f"{load_path.stem}_scaler.pkl"
        elif path and re.match(r"^v\d+$", path):
            # Version string like "v2", "v6" — resolve via MODEL_DIR
            load_path = MODEL_DIR / f"nn_{path}.keras"
            scaler_path = MODEL_DIR / f"nn_{path}_scaler.pkl"
        else:
            load_path = Path(path)
            scaler_path = load_path.parent / f"{load_path.stem}_scaler.pkl"

        if not load_path.exists():
            raise FileNotFoundError(f"Model file not found: {load_path}")

        self.model = keras.models.load_model(str(load_path))
        self._is_trained = True
        logger.info("Model loaded from %s", load_path)

        # Load scaler
        if scaler_path.exists():
            import joblib
            self.scaler = joblib.load(str(scaler_path))
            logger.info("Scaler loaded from %s", scaler_path)

    def save_versioned(
        self,
        version: Optional[str] = None,
        training_params: Optional[dict] = None,
    ) -> str:
        """Save a versioned model and update the model registry.

        Saves to models/nn_{version}.keras and models/nn_{version}_scaler.pkl,
        then writes metadata + metrics to models/model_registry.json.

        Args:
            version: Version string (e.g. "v2"). Auto-increments from registry if None.
            training_params: Extra metadata to store (min_season, max_season, etc.).

        Returns:
            The version string used.
        """
        if self.model is None:
            raise RuntimeError("No model to save.")

        registry = self._load_registry()

        if version is None:
            existing_nums = [
                int(e["version"].lstrip("v"))
                for e in registry.get("models", [])
                if e["version"].lstrip("v").isdigit()
            ]
            version = f"v{max(existing_nums, default=0) + 1}"

        model_path = MODEL_DIR / f"nn_{version}.keras"
        scaler_path = MODEL_DIR / f"nn_{version}_scaler.pkl"
        MODEL_DIR.mkdir(parents=True, exist_ok=True)

        self.model.save(str(model_path))
        if self.scaler is not None:
            import joblib
            joblib.dump(self.scaler, str(scaler_path))

        entry = {
            "version": version,
            "path": f"models/nn_{version}.keras",
            "scaler_path": f"models/nn_{version}_scaler.pkl",
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        if training_params:
            entry.update(training_params)
        if self._eval_metrics:
            entry["metrics"] = self._eval_metrics

        models = [m for m in registry.get("models", []) if m["version"] != version]
        models.append(entry)
        registry["models"] = models
        registry["latest"] = version

        # Track best model by season R2
        r2_entries = [
            (m["version"], m.get("metrics", {}).get("season_r2", -999))
            for m in models
            if m.get("metrics", {}).get("season_r2") is not None
        ]
        if r2_entries:
            registry.setdefault("best_by", {})["season_r2"] = max(r2_entries, key=lambda x: x[1])[0]

        REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
        logger.info("Versioned model %s saved to %s", version, model_path)
        logger.info("Saved %s -> %s", version, model_path)
        return version

    def _load_registry(self) -> dict:
        """Load the model registry JSON, or return an empty registry if none exists."""
        if REGISTRY_PATH.exists():
            return json.loads(REGISTRY_PATH.read_text())
        return {"models": [], "latest": None, "best_by": {}}

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_evaluation_metrics(self) -> Optional[dict]:
        """Return the evaluation metrics from the last training run."""
        return self._eval_metrics

    def get_training_history(self) -> Optional[dict]:
        """Return the full Keras training history from the last run."""
        return self._train_history

    @property
    def is_trained(self) -> bool:
        """Check if the model has been trained or loaded."""
        return self._is_trained
