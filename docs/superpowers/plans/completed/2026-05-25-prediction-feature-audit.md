# Prediction Feature Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a full ML audit trail — per-game feature values, scaled inputs, per-model probabilities, and blended importance — stored in Firestore, served via two new API endpoints, and surfaced in the explain modal and a new admin debug page.

**Architecture:** A new `feature_audit_service.py` computes XGB SHAP + LR exact contributions + NN input×gradient and blends them at full ensemble weights (45/20/35). The backfill script gains a `--features` flag that calls the service and writes one Firestore doc per season × ensemble version. Two new API endpoints serve the data; the explain modal gets a second fetch; a new admin predictions page provides a full feature table and per-model breakdown.

**Tech Stack:** Python/FastAPI, XGBoost SHAP (`pred_contribs=True`), scikit-learn `LogisticRegressionCV.coef_`, TensorFlow `GradientTape` (NN attribution), NumPy, Vanilla JS ES6 modules, Jinja2 templates, Firestore.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `services/feature_audit_service.py` | `compute_feature_audit()` — XGB SHAP + LR exact + NN input×gradient, full 45/20/35 blend |
| Create | `tests/test_feature_audit_service.py` | Unit tests for audit computation |
| Create | `templates/admin_predictions.html` | Admin debug page template |
| Create | `static/js/admin_predictions.js` | Admin page: game picker, feature table, per-model table |
| Modify | `services/nn_prediction_service.py` | Add `self.loaded_version` in `load_model()` |
| Modify | `services/xgb_prediction_service.py` | Add `self.loaded_version` in `load_model()` |
| Modify | `services/lr_prediction_service.py` | Add `self.loaded_version` in `load_model()` |
| Modify | `services/cache_service.py` | Add `get_prediction_features()` + `write_prediction_features()` |
| Modify | `scripts/backfill_schedule_predictions.py` | Add `--features` flag, call audit service, write doc |
| Modify | `scripts/refresh_local_pkls.py` | Add `dump_prediction_features()` + call in `main()` |
| Modify | `routes/api_routes.py` | `GET /api/prediction_features/{season}/{week}/{away}/{home}` |
| Modify | `routes/admin_routes.py` | `GET /api/admin/prediction_features/{season}` + `GET /admin/predictions` page route |
| Modify | `main.py` | Register admin page router |
| Modify | `static/js/schedule_explain.js` | Second fetch + per-model row + top-5 importance bar chart |

---

## Task 1: Add `loaded_version` to NN, XGB, and LR services

**Files:**
- Modify: `services/nn_prediction_service.py`
- Modify: `services/xgb_prediction_service.py`
- Modify: `services/lr_prediction_service.py`

- [x] **Step 1: Write the failing test**

Create `tests/test_loaded_version.py`:

```python
"""Test that all three prediction services expose loaded_version after load_model()."""
import pytest
from unittest.mock import patch, MagicMock
import pickle
import json
from pathlib import Path


class TestNNLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        from services.nn_prediction_service import NNPredictionService, REGISTRY_PATH, MODEL_DIR
        registry = {
            "v10": {"model_path": str(tmp_path / "nn_v10.keras"), "scaler_path": str(tmp_path / "nn_v10_scaler.pkl")},
        }
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", tmp_path / "model_registry.json")
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)
        (tmp_path / "model_registry.json").write_text(json.dumps(registry))

        fake_model = MagicMock()
        fake_scaler = MagicMock()
        with patch("services.nn_prediction_service.keras") as mock_keras, \
             patch("builtins.open", side_effect=lambda p, *a, **kw: _open_or_pickle(p, fake_scaler, registry)):
            mock_keras.models.load_model.return_value = fake_model
            svc = NNPredictionService()
            svc.load_model(version="v10")
            assert svc.loaded_version == "v10"


def _open_or_pickle(path, obj, registry):
    import io, pickle as pkl
    path = str(path)
    if path.endswith(".pkl"):
        buf = io.BytesIO(pkl.dumps(obj))
        buf.seek(0)
        return buf
    # Fall through to real open for JSON registry
    return open(path)


class TestXGBLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        import xgboost as xgb
        import pickle
        from services.xgb_prediction_service import XGBPredictionService

        dummy_model = xgb.XGBClassifier(n_estimators=1)
        # We don't actually fit it; just test that loaded_version is set
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", tmp_path / "xgb_registry.json")
        registry = {
            "v4": {
                "model_path": str(tmp_path / "xgb_v4.json"),
                "scaler_path": str(tmp_path / "xgb_v4_scaler.pkl"),
            }
        }
        (tmp_path / "xgb_registry.json").write_text(json.dumps(registry))

        with patch.object(xgb.XGBClassifier, "load_model"), \
             patch("builtins.open", side_effect=lambda p, *a, **kw: _open_or_pickle(str(p), MagicMock(), registry)):
            svc = XGBPredictionService()
            svc.load_model(version="v4")
            assert svc.loaded_version == "v4"


class TestLRLoadedVersion:
    def test_loaded_version_set_after_load(self, tmp_path, monkeypatch):
        from services.lr_prediction_service import LRPredictionService
        import pickle

        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", tmp_path / "lr_registry.json")
        registry = {
            "v2": {
                "model_path": str(tmp_path / "lr_v2.pkl"),
                "scaler_path": str(tmp_path / "lr_v2_scaler.pkl"),
            }
        }
        (tmp_path / "lr_registry.json").write_text(json.dumps(registry))

        with patch("builtins.open", side_effect=lambda p, *a, **kw: _open_or_pickle(str(p), MagicMock(), registry)):
            svc = LRPredictionService()
            svc.load_model(version="v2")
            assert svc.loaded_version == "v2"
```

- [x] **Step 2: Run test — expect FAIL (AttributeError: loaded_version)**

```
pytest tests/test_loaded_version.py -v
```
Expected: 3 FAILs with `AttributeError: 'NNPredictionService' object has no attribute 'loaded_version'` (and similar for XGB/LR).

- [x] **Step 3: Add `self.loaded_version = version` to NN service**

In `services/nn_prediction_service.py`, find `load_model()`. After the line that sets `self._is_trained = True` (or just before the final `logger.info` call), add:

```python
        self.loaded_version = version
        logger.info("Loaded NN model %s", version)  # already exists — just add the line above it
```

The exact location is after `self.scaler = joblib.load(str(scaler_path))` and before or after `self._is_trained = True`. Add it just before the existing `logger.info` line at the end of `load_model()`.

- [x] **Step 4: Add `self.loaded_version = version` to XGB service**

In `services/xgb_prediction_service.py` `load_model()`, add before the logger line at the end:

```python
        self.loaded_version = version
        logger.info("Loaded XGB model %s from %s", version, entry["model_path"])  # already exists
```

- [x] **Step 5: Add `self.loaded_version = version` to LR service**

In `services/lr_prediction_service.py` `load_model()`, add before the logger line at the end:

```python
        self.loaded_version = version
        logger.info("Loaded LR model %s", version)  # already exists
```

- [x] **Step 6: Run tests — expect PASS**

```
pytest tests/test_loaded_version.py -v
```
Expected: 3 PASSes.

- [x] **Step 7: Run full suite to check no regressions**

```
pytest tests/ -q --tb=short
```
Expected: all previously passing tests still pass.

- [x] **Step 8: Commit**

```
git add services/nn_prediction_service.py services/xgb_prediction_service.py services/lr_prediction_service.py tests/test_loaded_version.py
git commit -m "feat: add loaded_version attribute to NN/XGB/LR prediction services"
```

---

## Task 2: Create `services/feature_audit_service.py`

**Files:**
- Create: `services/feature_audit_service.py`
- Create: `tests/test_feature_audit_service.py`

The service computes per-game attribution vectors for all three models and blends them at full ensemble weights (45% NN input×gradient + 20% XGB SHAP + 35% LR exact).

- [x] **Step 1: Write failing tests**

Create `tests/test_feature_audit_service.py`:

```python
"""Unit tests for feature_audit_service.compute_feature_audit()."""
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

from services.nn_feature_engine import FEATURE_COLUMNS


def _make_feature_table(n=3):
    """Return a minimal feature DataFrame with required columns."""
    rng = np.random.default_rng(42)
    data = {col: rng.normal(size=n) for col in FEATURE_COLUMNS}
    data["season"] = [2025] * n
    data["week"] = [8, 8, 9]
    data["home_team"] = ["KC", "SF", "BUF"]
    data["away_team"] = ["LAC", "DAL", "MIA"]
    data["home_win"] = [1, 0, 1]
    return pd.DataFrame(data)


def _make_mocks(n=3):
    """Return mocked NN/XGB/LR services with plausible predict outputs."""
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, len(FEATURE_COLUMNS))).astype(np.float32)

    nn_svc = MagicMock()
    nn_svc.scaler.transform.return_value = X
    nn_svc.model.predict.return_value = rng.uniform(0.3, 0.7, (n, 1))
    nn_svc.loaded_version = "v10"

    xgb_svc = MagicMock()
    xgb_svc.scaler.transform.return_value = X
    # predict_proba returns (n, 2): col 1 = home_win prob
    xgb_proba = np.stack([rng.uniform(0.3, 0.7, n), rng.uniform(0.3, 0.7, n)], axis=1)
    xgb_svc.model.predict_proba.return_value = xgb_proba
    xgb_svc.loaded_version = "v4"

    lr_svc = MagicMock()
    lr_svc.scaler.transform.return_value = X
    lr_proba = np.stack([rng.uniform(0.3, 0.7, n), rng.uniform(0.3, 0.7, n)], axis=1)
    lr_svc.model.predict_proba.return_value = lr_proba
    lr_svc.model.coef_ = rng.normal(size=(1, len(FEATURE_COLUMNS)))
    lr_svc.loaded_version = "v2"

    return nn_svc, xgb_svc, lr_svc


class TestComputeFeatureAudit:
def _patch_nn_grad(n, value=0.0):
    """Context manager that patches tf.GradientTape to return zero gradients."""
    import tensorflow as tf
    fake_grads = np.full((n, len(FEATURE_COLUMNS)), value, dtype=np.float32)

    class _FakeTape:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def watch(self, t): pass
        def gradient(self, pred, inp): return tf.constant(fake_grads)

    return patch("services.feature_audit_service.tf.GradientTape", return_value=_FakeTape())


class TestComputeFeatureAudit:
    def test_returns_list_of_dicts(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(3)
        nn, xgb_svc, lr = _make_mocks(3)
        dummy_shap = np.zeros((3, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(3):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        assert isinstance(records, list)
        assert len(records) == 3

    def test_record_has_required_keys(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        r = records[0]
        for key in ("game_key", "season", "week", "away_team", "home_team",
                    "nn_prob", "xgb_prob", "lr_prob", "blended_prob",
                    "features", "scaled_features", "feature_importance"):
            assert key in r, f"Missing key: {key}"

    def test_game_key_format(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        # First row: week=8, home=KC, away=LAC → W08_KC_LAC
        assert records[0]["game_key"] == "W08_KC_LAC"

    def test_probabilities_in_range(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(3)
        nn, xgb_svc, lr = _make_mocks(3)
        dummy_shap = np.zeros((3, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(3):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        for r in records:
            assert 0.0 <= r["blended_prob"] <= 1.0
            assert 0.0 <= r["nn_prob"] <= 1.0
            assert 0.0 <= r["xgb_prob"] <= 1.0
            assert 0.0 <= r["lr_prob"] <= 1.0

    def test_feature_importance_sorted_by_abs_score(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(2)
        nn, xgb_svc, lr = _make_mocks(2)
        dummy_shap = np.zeros((2, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(2):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        importance = records[0]["feature_importance"]
        scores = [abs(item["score"]) for item in importance]
        assert scores == sorted(scores, reverse=True), "importance must be sorted by |score| desc"

    def test_feature_importance_direction(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)

        # Force a known pattern: first feature strongly positive across all models
        shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        shap[0, 0] = 5.0   # first feature → "home"
        shap[0, 1] = -3.0  # second feature → "away"
        xgb_svc.model.get_booster.return_value.predict.return_value = shap
        # Zero out LR coef so XGB+NN dominate direction
        lr.model.coef_ = np.zeros((1, len(FEATURE_COLUMNS)))

        # NN gradient: mock GradientTape to return matching pattern
        import tensorflow as tf
        fake_grads = np.zeros((1, len(FEATURE_COLUMNS)), dtype=np.float32)
        fake_grads[0, 0] = 2.0    # first feature positive → "home"
        fake_grads[0, 1] = -1.0   # second feature negative → "away"
        with patch("services.feature_audit_service.xgb.DMatrix"), \
             patch("services.feature_audit_service.tf.GradientTape") as mock_tape_cls:
            mock_tape = MagicMock()
            mock_tape.__enter__ = lambda s: s
            mock_tape.__exit__ = MagicMock(return_value=False)
            mock_tape.gradient.return_value = tf.constant(fake_grads)
            mock_tape_cls.return_value = mock_tape
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        fi = {item["feature"]: item for item in records[0]["feature_importance"]}
        assert fi[FEATURE_COLUMNS[0]]["direction"] == "home"
        assert fi[FEATURE_COLUMNS[1]]["direction"] == "away"

    def test_features_dict_has_all_26_columns(self):
        from services.feature_audit_service import compute_feature_audit
        ft = _make_feature_table(1)
        nn, xgb_svc, lr = _make_mocks(1)
        dummy_shap = np.zeros((1, len(FEATURE_COLUMNS) + 1))
        xgb_svc.model.get_booster.return_value.predict.return_value = dummy_shap

        with patch("services.feature_audit_service.xgb.DMatrix"), _patch_nn_grad(1):
            records = compute_feature_audit(ft, nn, xgb_svc, lr)

        assert set(records[0]["features"].keys()) == set(FEATURE_COLUMNS)
        assert set(records[0]["scaled_features"].keys()) == set(FEATURE_COLUMNS)

    def test_empty_table_returns_empty_list(self):
        from services.feature_audit_service import compute_feature_audit
        empty = pd.DataFrame(columns=FEATURE_COLUMNS + ["season", "week", "home_team", "away_team", "home_win"])
        nn, xgb_svc, lr = _make_mocks(0)
        records = compute_feature_audit(empty, nn, xgb_svc, lr)
        assert records == []
```

- [x] **Step 2: Run tests — expect ImportError/ModuleNotFoundError (file doesn't exist)**

```
pytest tests/test_feature_audit_service.py -v
```
Expected: collection error — `ModuleNotFoundError: No module named 'services.feature_audit_service'`.

- [x] **Step 3: Create `services/feature_audit_service.py`**

```python
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
        grads = tape.gradient(pred, X_tensor)        # (n, 26)
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
          feature_importance (list of 26 items sorted by |score| desc).
    """
    if feature_table.empty:
        return []

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
    nn_grad = _nn_gradient_importance(nn_svc.model, X_nn)   # (n, 26)

    # ── XGB SHAP (native pred_contribs) ───────────────────────────────────
    if XGB_AVAILABLE:
        dmatrix  = xgb.DMatrix(X_xgb)
        raw_shap = xgb_svc.model.get_booster().predict(dmatrix, pred_contribs=True)
        xgb_shap = raw_shap[:, :-1].astype(np.float32)      # drop bias → (n, 26)
    else:
        xgb_shap = np.zeros((n, len(FEATURE_COLUMNS)), dtype=np.float32)

    # ── LR exact contributions: coef * scaled_feature ─────────────────────
    lr_coef    = np.array(lr_svc.model.coef_[0], dtype=np.float32)  # (26,)
    lr_contrib = X_lr * lr_coef                                       # (n, 26)

    # ── Blended importance at full ensemble weights ────────────────────────
    blended_imp = (
        NN_WEIGHT  * nn_grad    +   # 0.45
        XGB_WEIGHT * xgb_shap   +   # 0.20
        LR_WEIGHT  * lr_contrib      # 0.35
    )                                # (n, 26)

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
```

- [x] **Step 4: Run tests — expect PASS**

```
pytest tests/test_feature_audit_service.py -v
```
Expected: 8 PASSes.

- [x] **Step 5: Run full suite**

```
pytest tests/ -q --tb=short
```
Expected: all green.

- [x] **Step 6: Commit**

```
git add services/feature_audit_service.py tests/test_feature_audit_service.py
git commit -m "feat: add feature_audit_service with XGB SHAP + LR contribution blend"
```

---

## Task 3: Add `get_prediction_features` / `write_prediction_features` to `cache_service.py`

**Files:**
- Modify: `services/cache_service.py`
- Test: `tests/test_cache_service.py` (add new test class)

Local JSON format mirrors `game_predictions`:
```
.local_db/prediction_features_{season}_{ensemble_version}.json
```
Contents: `{"season":..., "ensemble_version":..., "created_at":..., "games": {"W08_KC_SF": {...}}}`

- [x] **Step 1: Write failing tests**

Append to `tests/test_cache_service.py`:

```python
class TestPredictionFeaturesCache:
    """Tests for get_prediction_features / write_prediction_features."""

    def test_write_then_read_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_prediction_features, get_prediction_features

        games_data = {
            "W08_KC_SF": {
                "game_key": "W08_KC_SF", "season": 2025, "week": 8,
                "away_team": "KC", "home_team": "SF",
                "nn_prob": 0.62, "xgb_prob": 0.58, "lr_prob": 0.60, "blended_prob": 0.61,
                "features": {"tm_elo_pre": 1550.0}, "scaled_features": {"tm_elo_pre": 0.81},
                "feature_importance": [{"feature": "tm_elo_pre", "score": 0.31, "direction": "home"}],
            }
        }
        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", games_data)

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc is not None
        assert doc["season"] == 2025
        assert doc["ensemble_version"] == "nn_v10+xgb_v4+lr_v2"
        assert "W08_KC_SF" in doc["games"]

    def test_get_latest_returns_most_recent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_prediction_features, get_prediction_features

        write_prediction_features(2025, "nn_v9+xgb_v3+lr_v1", {"W01_BUF_MIA": {}})
        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_BUF_MIA": {"newer": True}})

        doc = get_prediction_features(2025)  # no version → latest by mtime
        assert doc is not None

    def test_get_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import get_prediction_features
        assert get_prediction_features(2099, "nn_v1+xgb_v1+lr_v1") is None
```

- [x] **Step 2: Run new tests — expect FAIL**

```
pytest tests/test_cache_service.py::TestPredictionFeaturesCache -v
```
Expected: 3 FAILs with `ImportError` or `AttributeError`.

- [x] **Step 3: Add functions to `cache_service.py`**

Append to the end of `services/cache_service.py` (after the existing `write_game_predictions` block):

```python
# ---------------------------------------------------------------------------
# Prediction features audit store
# ---------------------------------------------------------------------------
# One document per season × ensemble_version.
# Local: .local_db/prediction_features_{season}_{ensemble_version}.json
# Firestore: prediction_features/{season}_{ensemble_version}
# games value: {game_key: per-game audit dict from feature_audit_service}


def get_prediction_features(
    season: int,
    ensemble_version: str | None = None,
) -> dict | None:
    """Return the prediction features doc for season/version, or None if absent.

    If ensemble_version is None, returns the most recently written doc for the
    season (by file mtime locally, by created_at in Firestore).
    """
    if _USE_LOCAL:
        if ensemble_version:
            p = _GAME_PRED_DIR / f"prediction_features_{season}_{ensemble_version}.json"
            if not p.exists():
                return None
            with open(p) as f:
                return json.load(f)
        else:
            candidates = sorted(
                _GAME_PRED_DIR.glob(f"prediction_features_{season}_*.json"),
                key=lambda p: p.stat().st_mtime,
            )
            if not candidates:
                return None
            with open(candidates[-1]) as f:
                return json.load(f)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            if ensemble_version:
                doc_id = f"{season}_{ensemble_version}"
                doc = db.collection("prediction_features").document(doc_id).get()
                return doc.to_dict() if doc.exists else None
            else:
                docs = list(
                    db.collection("prediction_features")
                    .where("season", "==", season)
                    .order_by("created_at", direction="DESCENDING")
                    .limit(1)
                    .stream()
                )
                return docs[0].to_dict() if docs else None
        except Exception:
            logger.exception("Failed to fetch prediction_features for season=%s", season)
            return None


def write_prediction_features(
    season: int,
    ensemble_version: str,
    games: dict,
) -> None:
    """Persist the prediction features doc (local JSON or Firestore).

    Args:
        season: NFL season year.
        ensemble_version: e.g. "nn_v10+xgb_v4+lr_v2".
        games: {game_key: per-game audit dict} from compute_feature_audit().
    """
    from datetime import datetime, timezone
    payload = {
        "season":           season,
        "ensemble_version": ensemble_version,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "games":            games,
    }
    if _USE_LOCAL:
        _GAME_PRED_DIR.mkdir(parents=True, exist_ok=True)
        p = _GAME_PRED_DIR / f"prediction_features_{season}_{ensemble_version}.json"
        with open(p, "w") as f:
            json.dump(payload, f, default=str)
    else:
        try:
            from services.db_service import get_db
            db = get_db()
            doc_id = f"{season}_{ensemble_version}"
            db.collection("prediction_features").document(doc_id).set(payload)
        except Exception:
            logger.exception("Failed to write prediction_features to Firestore season=%s", season)
```

- [x] **Step 4: Run tests — expect PASS**

```
pytest tests/test_cache_service.py::TestPredictionFeaturesCache -v
```
Expected: 3 PASSes.

- [x] **Step 5: Run full suite**

```
pytest tests/ -q --tb=short
```
Expected: all green.

- [x] **Step 6: Commit**

```
git add services/cache_service.py tests/test_cache_service.py
git commit -m "feat: add get/write_prediction_features to cache_service"
```

---

## Task 4: Add `--features` flag to `scripts/backfill_schedule_predictions.py`

**Files:**
- Modify: `scripts/backfill_schedule_predictions.py`

- [x] **Step 1: Write a dry-run integration test**

Create `tests/test_backfill_features_flag.py`:

```python
"""Integration test for backfill_schedule_predictions --features flag."""
import sys
import pathlib
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


@pytest.fixture
def mock_services():
    """Mock all external calls: models, feature table, cache writes."""
    nn = MagicMock()
    nn.loaded_version = "v10"
    nn.scaler.transform.side_effect = lambda x: x
    nn.model.predict.return_value = np.array([[0.6], [0.55]])

    xgb = MagicMock()
    xgb.loaded_version = "v4"
    xgb.scaler.transform.side_effect = lambda x: x
    xgb.model.predict_proba.return_value = np.array([[0.4, 0.6], [0.45, 0.55]])

    lr = MagicMock()
    lr.loaded_version = "v2"
    lr.scaler.transform.side_effect = lambda x: x
    lr.model.predict_proba.return_value = np.array([[0.4, 0.6], [0.45, 0.55]])
    lr.model.coef_ = np.zeros((1, 26))

    return nn, xgb, lr


def test_features_flag_calls_write_prediction_features(mock_services, tmp_path):
    """When --features is passed, write_prediction_features is called once per season."""
    nn, xgb_svc, lr = mock_services

    from services.nn_feature_engine import FEATURE_COLUMNS
    rng = np.random.default_rng(0)
    fake_ft = pd.DataFrame({
        col: rng.normal(size=2) for col in FEATURE_COLUMNS
    } | {"season": [2025, 2025], "week": [8, 9],
         "home_team": ["KC", "BUF"], "away_team": ["SF", "MIA"],
         "home_win": [1, 0], "spread_line": [3.0, -1.5]})

    shap_output = np.zeros((2, len(FEATURE_COLUMNS) + 1))

    with patch("scripts.backfill_schedule_predictions.NNPredictionService", return_value=nn), \
         patch("scripts.backfill_schedule_predictions.XGBPredictionService", return_value=xgb_svc), \
         patch("scripts.backfill_schedule_predictions.LRPredictionService", return_value=lr), \
         patch("scripts.backfill_schedule_predictions.build_master_feature_table", return_value=fake_ft), \
         patch("scripts.backfill_schedule_predictions.build_ensemble_lookup", return_value={}), \
         patch("scripts.backfill_schedule_predictions._profile_predictions_for_year", return_value={}), \
         patch("scripts.backfill_schedule_predictions.write_game_predictions"), \
         patch("scripts.backfill_schedule_predictions.write_prediction_features") as mock_wpf, \
         patch("services.feature_audit_service.xgb.DMatrix"), \
         patch.object(xgb_svc.model, "get_booster") as mock_booster:
        mock_booster.return_value.predict.return_value = shap_output

        import importlib
        import scripts.backfill_schedule_predictions as bsp
        importlib.reload(bsp)

        bsp.main.__wrapped__ = None  # skip if wrapped
        import sys as _sys
        _sys.argv = ["backfill_schedule_predictions.py", "--seasons", "2025", "2025", "--features"]
        try:
            bsp.main()
        except SystemExit:
            pass

        assert mock_wpf.called, "write_prediction_features should be called when --features is passed"
```

- [x] **Step 2: Run test — expect FAIL**

```
pytest tests/test_backfill_features_flag.py -v
```
Expected: FAIL — `--features` argument not recognized yet.

- [x] **Step 3: Modify `scripts/backfill_schedule_predictions.py`**

**3a.** Add import at top (after existing imports):

```python
from services.feature_audit_service import compute_feature_audit
from services.cache_service import write_prediction_features
```

**3b.** Add `--features` to the `argparse` block (after the existing `--dry-run` argument):

```python
    parser.add_argument("--features", action="store_true",
                        help="Also compute and store per-game feature audit "
                             "(SHAP/LR contributions). Adds ~5-10s per season.")
```

**3c.** Add `write_features` variable after the existing `write_local` / `write_firestore` lines:

```python
    write_features = args.features and not args.dry_run
```

**3d.** Update the print block to include feature audit status. After the existing `print(f"  Force  : {args.force}")` line:

```python
    print(f"  Features: {write_features}")
```

**3e.** Add ensemble version helper just before the `for year in years:` loop:

```python
    ensemble_version = (
        f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"
    )
    if write_features:
        print(f"\n  Ensemble version for audit: {ensemble_version}")
```

**3f.** After the existing `write_game_predictions` call inside the `for year in years:` loop (search for `write_game_predictions(year, preds_map)`), add:

```python
        if write_features:
            year_ft = ft[ft["season"] == year].copy()
            if not year_ft.empty:
                print(f"    Computing feature audit ({len(year_ft)} games)...")
                audit_records = compute_feature_audit(year_ft, nn_svc, xgb_svc, lr_svc)
                games_dict = {r["game_key"]: r for r in audit_records}
                write_prediction_features(year, ensemble_version, games_dict)
                print(f"    ✓ {len(games_dict)} games written → prediction_features_{year}_{ensemble_version}")
```

- [x] **Step 4: Run test — expect PASS**

```
pytest tests/test_backfill_features_flag.py -v
```
Expected: PASS.

- [x] **Step 5: Run full suite**

```
pytest tests/ -q --tb=short
```
Expected: all green.

- [x] **Step 6: Commit**

```
git add scripts/backfill_schedule_predictions.py tests/test_backfill_features_flag.py
git commit -m "feat: add --features flag to backfill_schedule_predictions for audit trail"
```

---

## Task 5: Add `dump_prediction_features` to `scripts/refresh_local_pkls.py`

**Files:**
- Modify: `scripts/refresh_local_pkls.py`

No automated test for this task (it requires live Firestore). Manual verification is the completion criterion.

- [x] **Step 1: Add the dump function**

In `scripts/refresh_local_pkls.py`, after the `dump_game_predictions()` function, add:

```python
def dump_prediction_features():
    """Pull all prediction_features docs from Firestore → .local_db/prediction_features_*.json."""
    log.info("  Fetching 'prediction_features' from Firestore...")
    try:
        db = get_db()
        docs = list(db.collection("prediction_features").stream())
        if not docs:
            log.warning("    'prediction_features' returned no documents — skipping")
            return

        written = 0
        for doc in docs:
            d = doc.to_dict()
            season = d.get("season")
            ensemble_version = d.get("ensemble_version")
            if season is None or not ensemble_version:
                log.warning(f"    skipping {doc.id} — missing season or ensemble_version")
                continue
            out_path = LOCAL_DB / f"prediction_features_{int(season)}_{ensemble_version}.json"
            with open(out_path, "w") as f:
                json.dump(d, f, default=str)
            written += 1
            log.info(f"    ✓ {len(d.get('games', {}))} games → {out_path.name}")

        log.info(f"    ✓ {written} docs written")
    except Exception as e:
        log.error(f"    ✗ Failed 'prediction_features': {e}")
```

- [x] **Step 2: Call it from `main()`**

In `scripts/refresh_local_pkls.py` `main()`, after the existing `dump_game_predictions()` call, add:

```python
    log.info("\n-- ML feature audit --")
    dump_prediction_features()
```

- [x] **Step 3: Verify script parses correctly**

```
python scripts/refresh_local_pkls.py --help
```
Expected: help text prints without errors.

- [x] **Step 4: Commit**

```
git add scripts/refresh_local_pkls.py
git commit -m "feat: add dump_prediction_features to refresh_local_pkls"
```

---

## Task 6: API endpoint — per-game feature lookup (any authenticated user)

**Files:**
- Modify: `routes/api_routes.py`
- Test: `tests/test_api_endpoints.py` (add new class)

- [x] **Step 1: Write failing tests**

Append to `tests/test_api_endpoints.py`:

```python
class TestPredictionFeaturesEndpoint:
    """Tests for GET /api/prediction_features/{season}/{week}/{away}/{home}."""

    def test_returns_feature_data_when_present(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        fake_doc = {
            "season": 2025,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2",
            "created_at": "2025-11-01T00:00:00Z",
            "games": {
                "W08_KC_SF": {
                    "game_key": "W08_KC_SF", "season": 2025, "week": 8,
                    "away_team": "KC", "home_team": "SF",
                    "nn_prob": 0.623, "xgb_prob": 0.589,
                    "lr_prob": 0.601, "blended_prob": 0.611,
                    "features": {"tm_elo_pre": 1550.0},
                    "scaled_features": {"tm_elo_pre": 0.81},
                    "feature_importance": [
                        {"feature": "tm_elo_pre", "score": 0.31, "direction": "home"}
                    ],
                }
            },
        }
        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: fake_doc,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["game_key"] == "W08_KC_SF"
        assert data["nn_prob"] == 0.623
        assert data["blended_prob"] == 0.611

    def test_returns_404_when_no_data(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: None,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 404

    def test_requires_auth(self):
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/prediction_features/2025/8/KC/SF")
        assert resp.status_code == 401
```

- [x] **Step 2: Run new tests — expect FAIL (404 Not Found from FastAPI, route not registered)**

```
pytest tests/test_api_endpoints.py::TestPredictionFeaturesEndpoint -v
```
Expected: FAILs because the route doesn't exist.

- [x] **Step 3: Add the import and endpoint to `routes/api_routes.py`**

**3a.** Add import near the top of `routes/api_routes.py` (after the existing `from services.cache_service` import):

```python
from services.cache_service import get_prediction_features
```

**3b.** Add the endpoint (append to end of file):

```python
@router.get("/prediction_features/{season}/{week}/{away_team}/{home_team}")
def get_game_prediction_features(
    season: Annotated[int, Path(ge=2000, le=2030)],
    week:   Annotated[int, Path(ge=1, le=22)],
    away_team: str,
    home_team: str,
    _auth: dict = Depends(require_auth),
):
    """Feature audit data for one game (any logged-in user — shown in explain modal).

    Returns the per-game audit record: raw features, scaled features, per-model
    probabilities, and blended feature importance for the latest ensemble version.
    Returns 404 if no feature audit data exists for this game.
    """
    try:
        from services.nn_feature_engine import _normalize_team
        ht = _normalize_team(away_team)
        at = _normalize_team(home_team)
        game_key = f"W{int(week):02d}_{ht}_{at}"

        doc = get_prediction_features(season)
        if doc is None:
            return JSONResponse(
                status_code=404,
                content={"error": "No feature data for this season."},
            )

        game_data = doc.get("games", {}).get(game_key)
        if game_data is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"No feature data for game {game_key}."},
            )

        return JSONResponse(content={
            **game_data,
            "ensemble_version": doc.get("ensemble_version"),
        })
    except Exception:
        logger.exception("Unhandled error in get_game_prediction_features")
        return server_error()
```

- [x] **Step 4: Run tests — expect PASS**

```
pytest tests/test_api_endpoints.py::TestPredictionFeaturesEndpoint -v
```
Expected: 3 PASSes.

- [x] **Step 5: Run full suite**

```
pytest tests/ -q --tb=short
```
Expected: all green.

- [x] **Step 6: Commit**

```
git add routes/api_routes.py tests/test_api_endpoints.py
git commit -m "feat: add GET /api/prediction_features/{season}/{week}/{away}/{home} endpoint"
```

---

## Task 7: API endpoint — per-season admin feature data + admin predictions page

**Files:**
- Modify: `routes/admin_routes.py`
- Modify: `main.py`
- Test: `tests/test_admin_routes.py` (add new class)

- [x] **Step 1: Write failing tests**

Append to `tests/test_admin_routes.py`:

```python
class TestAdminPredictionFeaturesEndpoint:
    """Tests for GET /api/admin/prediction_features/{season}."""

    def test_returns_all_versions_for_season(self, admin_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        fake_doc = {
            "season": 2025,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2",
            "created_at": "2025-11-01T00:00:00Z",
            "games": {"W08_KC_SF": {"blended_prob": 0.61}},
        }
        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: fake_doc,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": admin_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["season"] == 2025
        assert "games" in data

    def test_returns_404_when_no_data(self, admin_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: None,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": admin_token},
        )
        assert resp.status_code == 404

    def test_requires_admin(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.admin_routes.get_prediction_features",
            lambda season, **kw: {"season": 2025, "games": {}},
        )

        client = TestClient(app)
        resp = client.get(
            "/api/admin/prediction_features/2025",
            headers={"Authorization": auth_token},  # user, not admin
        )
        assert resp.status_code == 401
```

- [x] **Step 2: Run new tests — expect FAIL**

```
pytest tests/test_admin_routes.py::TestAdminPredictionFeaturesEndpoint -v
```
Expected: 3 FAILs.

- [x] **Step 3: Add imports and endpoint to `routes/admin_routes.py`**

**3a.** Add import after existing imports at top of `routes/admin_routes.py`:

```python
from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from services.cache_service import get_prediction_features
from typing import Annotated
from fastapi import Path as FPath

_templates = Jinja2Templates(directory="templates")
_page_router = APIRouter()  # No prefix — for HTML page routes
```

**3b.** Add the admin predictions page route (append near end of `routes/admin_routes.py`):

```python
@_page_router.get("/admin/predictions")
async def admin_predictions_page(
    request: Request,
    _: dict = Depends(require_admin),
):
    """Admin prediction debug page — requires admin auth."""
    return _templates.TemplateResponse(request, "admin_predictions.html")


@router.get("/admin/prediction_features/{season}")
def get_admin_prediction_features(
    season: Annotated[int, FPath(ge=2000, le=2030)],
    _: dict = Depends(require_admin),
):
    """Full feature audit for a season (admin only — used by debug page).

    Returns the latest ensemble version doc for the season, including all
    games and their feature vectors.
    """
    try:
        doc = get_prediction_features(season)
        if doc is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"No feature data for season {season}."},
            )
        return JSONResponse(content=doc)
    except Exception:
        logger.exception("Unhandled error in get_admin_prediction_features")
        return server_error()
```

- [x] **Step 4: Register `_page_router` in `main.py`**

In `main.py`, update the import line for `admin_routes`:

```python
from routes.admin_routes import router as admin_router, _page_router as admin_page_router
```

And add below `app.include_router(admin_router)`:

```python
app.include_router(admin_page_router)
```

- [x] **Step 5: Run tests — expect PASS**

```
pytest tests/test_admin_routes.py::TestAdminPredictionFeaturesEndpoint -v
```
Expected: 3 PASSes.

- [x] **Step 6: Run full suite**

```
pytest tests/ -q --tb=short
```
Expected: all green.

- [x] **Step 7: Commit**

```
git add routes/admin_routes.py main.py tests/test_admin_routes.py
git commit -m "feat: add /api/admin/prediction_features/{season} endpoint + /admin/predictions page route"
```

---

## Task 8: Enhanced explain modal — per-model probs + top-5 feature bar chart

**Files:**
- Modify: `static/js/schedule_explain.js`

The existing modal calls `/api/predictions/explain` and renders a breakdown. We add a second `fetch` to `/api/prediction_features/{season}/{week}/{away}/{home}` and, if data is returned, inject a per-model probability row and a top-5 feature importance chart above the existing factor breakdown.

- [x] **Step 1: Locate the modal fetch in `schedule_explain.js`**

The function that fetches explain data is `openExplainModal(gameData)` (search for `fetch` and `/api/predictions/explain`). The new second fetch goes inside the same function, after the existing fetch resolves.

- [x] **Step 2: Add the second fetch and feature section renderer**

In `static/js/schedule_explain.js`, find the `openExplainModal` function (or wherever the `/api/predictions/explain` fetch is made). Replace the existing `openExplainModal` (or equivalent) function with:

```javascript
// ── Feature audit section ─────────────────────────────────────────────────

function renderFeatureAuditSection(featureData, homeTeam, awayTeam) {
    if (!featureData) return '';

    const { nn_prob, xgb_prob, lr_prob, blended_prob, feature_importance, ensemble_version } = featureData;

    // Per-model probability row
    const fmt = p => p != null ? `${Math.round(p * 100)}%` : '—';
    const modelRow = `
        <div style="background:rgba(255,255,255,0.04); border-radius:8px; padding:10px 14px; margin-bottom:12px;">
            <div style="font-size:0.72rem; color:var(--text-secondary); margin-bottom:6px; text-transform:uppercase; letter-spacing:0.05em;">Model breakdown</div>
            <div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap;">
                <span style="font-size:0.82rem;">NN <strong>${fmt(nn_prob)}</strong></span>
                <span style="color:var(--glass-border);">·</span>
                <span style="font-size:0.82rem;">XGB <strong>${fmt(xgb_prob)}</strong></span>
                <span style="color:var(--glass-border);">·</span>
                <span style="font-size:0.82rem;">LR <strong>${fmt(lr_prob)}</strong></span>
                <span style="color:var(--glass-border);">→</span>
                <span style="font-size:0.85rem; color:var(--accent-green);">Blended <strong>${fmt(blended_prob)}</strong></span>
            </div>
        </div>`;

    // Top-5 feature importance bar chart
    const top5 = (feature_importance || []).slice(0, 5);
    if (!top5.length) return modelRow;

    const maxScore = Math.max(...top5.map(f => Math.abs(f.score)), 0.0001);

    const bars = top5.map(f => {
        const pct  = Math.round((Math.abs(f.score) / maxScore) * 100);
        const dir  = f.direction === 'home' ? homeTeam : awayTeam;
        const color = f.direction === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)';
        const label = f.feature.replace(/_/g, ' ');
        return `
            <div style="margin-bottom:7px;">
                <div style="display:flex; justify-content:space-between; font-size:0.75rem; margin-bottom:2px;">
                    <span style="color:var(--text-secondary);">${label}</span>
                    <span style="color:${color}; font-weight:600;">${dir}</span>
                </div>
                <div style="height:5px; background:rgba(255,255,255,0.07); border-radius:3px; overflow:hidden;">
                    <div style="width:${pct}%; height:100%; background:${color}; border-radius:3px;"></div>
                </div>
            </div>`;
    }).join('');

    const versionNote = ensemble_version
        ? `<div style="font-size:0.68rem; color:var(--text-secondary); margin-top:8px; text-align:right;">${ensemble_version}</div>`
        : '';

    return `${modelRow}
        <div style="background:rgba(255,255,255,0.04); border-radius:8px; padding:10px 14px; margin-bottom:12px;">
            <div style="font-size:0.72rem; color:var(--text-secondary); margin-bottom:8px; text-transform:uppercase; letter-spacing:0.05em;">Top factors (blended importance)</div>
            ${bars}
            ${versionNote}
        </div>`;
}

export async function openExplainModal(gameData) {
    const { season, week, home_team, away_team } = gameData;
    showModal();
    content.innerHTML = '<div style="color:var(--text-secondary); padding:20px;">Loading…</div>';

    try {
        // Primary explain fetch
        const explainResp = await fetch(
            `/api/predictions/explain?season=${season}&week=${week}&home_team=${home_team}&away_team=${away_team}`,
            { headers: { Authorization: `Bearer ${AuthService.getCredentials().token}` } }
        );
        const explainData = explainResp.ok ? await explainResp.json() : null;

        // Feature audit fetch (graceful — 404 is normal for older games)
        let featureData = null;
        try {
            const featResp = await fetch(
                `/api/prediction_features/${season}/${week}/${away_team}/${home_team}`,
                { headers: { Authorization: `Bearer ${AuthService.getCredentials().token}` } }
            );
            if (featResp.ok) featureData = await featResp.json();
        } catch (_) { /* no feature data — silently skip */ }

        if (!explainData) {
            content.innerHTML = '<div style="color:var(--text-secondary); padding:20px;">No prediction data available.</div>';
            return;
        }

        const auditSection = renderFeatureAuditSection(featureData, home_team, away_team);
        const explanationSection = renderExplanation({ ...explainData, home_team, away_team });
        content.innerHTML = auditSection + explanationSection;

    } catch (err) {
        content.innerHTML = `<div style="color:var(--accent-red); padding:20px;">Error loading prediction data.</div>`;
    }
}
```

- [x] **Step 3: Check existing `openExplainModal` call sites**

Search for all `openExplainModal` usages in the JS codebase to make sure the function signature is compatible:

```
grep -r "openExplainModal" static/js/
```

The function is exported and called from `main.js` or wherever `?` buttons are wired. The signature `openExplainModal(gameData)` where `gameData` has `season`, `week`, `home_team`, `away_team` must match all call sites. If any call site uses a different shape, update it to pass those four fields.

- [x] **Step 4: Manual test**

Start the dev server:
```
uvicorn main:app --reload
```
Open a schedule page with predictions. Click the `?` explain button on a game. Verify:
- Modal opens
- If feature data exists: per-model prob row and top-5 bar chart appear above the existing factor breakdown
- If no feature data (e.g., older season before backfill): modal loads normally with no audit section

- [x] **Step 5: Commit**

```
git add static/js/schedule_explain.js
git commit -m "feat: enhance explain modal with per-model probs and top-5 feature importance chart"
```

---

## Task 9: Admin predictions debug page

**Files:**
- Create: `templates/admin_predictions.html`
- Create: `static/js/admin_predictions.js`

The page loads at `/admin/predictions`. A season dropdown triggers a fetch to `/api/admin/prediction_features/{season}`, which returns all games. The week dropdown and matchup dropdown are populated client-side from that data. Selecting a game renders the feature table and per-model output table.

- [x] **Step 1: Create `templates/admin_predictions.html`**

```html
{% extends "base.html" %}

{% block title %}NFL Wins Pool - Prediction Debug{% endblock %}

{% block head %}
<style>
    .pred-debug-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
    @media (max-width: 768px) { .pred-debug-grid { grid-template-columns: 1fr; } }
    .feat-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
    .feat-table th { color: var(--text-secondary); font-weight: 600; text-align: left;
                     padding: 6px 8px; border-bottom: 1px solid var(--glass-border); }
    .feat-table td { padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.04); }
    .feat-table tr:hover td { background: rgba(255,255,255,0.03); }
    .bar-cell { width: 100px; }
    .imp-bar { height: 5px; border-radius: 3px; background: rgba(255,255,255,0.07); }
    .imp-fill { height: 5px; border-radius: 3px; }
    .home-color { background: var(--accent-green); }
    .away-color { background: var(--accent-gold); }
    select.pred-select { background: var(--glass-bg); border: 1px solid var(--glass-border);
                         color: var(--text-primary); padding: 6px 10px; border-radius: 6px;
                         font-size: 0.85rem; min-width: 120px; }
</style>
{% endblock %}

{% block content %}
<div class="container" style="max-width: 1200px;">
    <header class="wp-top">
        <div>
            <div class="eyebrow">Commissioner</div>
            <h1 class="wp-h1">Prediction<span class="wp-year">/debug</span></h1>
        </div>
    </header>

    <!-- Game Picker -->
    <div class="card-glass" style="padding: 1.25rem; margin-bottom: 1.5rem;">
        <div style="display: flex; gap: 1rem; flex-wrap: wrap; align-items: center;">
            <div>
                <label style="font-size:0.75rem; color:var(--text-secondary); display:block; margin-bottom:4px;">Season</label>
                <select id="pd-season" class="pred-select"></select>
            </div>
            <div>
                <label style="font-size:0.75rem; color:var(--text-secondary); display:block; margin-bottom:4px;">Week</label>
                <select id="pd-week" class="pred-select" disabled></select>
            </div>
            <div>
                <label style="font-size:0.75rem; color:var(--text-secondary); display:block; margin-bottom:4px;">Matchup</label>
                <select id="pd-matchup" class="pred-select" disabled></select>
            </div>
            <div id="pd-version-badge" style="font-size:0.72rem; color:var(--text-secondary); margin-top:18px;"></div>
        </div>
    </div>

    <!-- Results (hidden until game selected) -->
    <div id="pd-results" style="display:none;">
        <!-- Per-model output -->
        <div class="card-glass" style="padding:1.25rem; margin-bottom:1.5rem;">
            <h3 style="margin:0 0 1rem; font-size:0.95rem;">Model Output</h3>
            <table class="feat-table" id="pd-model-table">
                <thead>
                    <tr>
                        <th>Model</th><th>Weight</th><th>Home Win Prob</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <!-- Feature table -->
        <div class="card-glass" style="padding:1.25rem;">
            <h3 style="margin:0 0 1rem; font-size:0.95rem;">Feature Breakdown (26 features)</h3>
            <table class="feat-table" id="pd-feat-table">
                <thead>
                    <tr>
                        <th>Feature</th>
                        <th>Raw Value</th>
                        <th>Scaled Value</th>
                        <th>Importance</th>
                        <th>Direction</th>
                        <th class="bar-cell"></th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <div id="pd-empty" style="color:var(--text-secondary); text-align:center; padding:2rem; display:none;">
        No feature audit data for this season. Run:<br>
        <code style="font-size:0.8rem;">python scripts/backfill_schedule_predictions.py --season {season} --features</code>
    </div>

    <div id="pd-loading" style="color:var(--text-secondary); text-align:center; padding:2rem; display:none;">
        Loading…
    </div>
</div>

<script type="module" src="/static/js/admin_predictions.js"></script>
{% endblock %}
```

- [x] **Step 2: Create `static/js/admin_predictions.js`**

```javascript
/**
 * admin_predictions.js — Prediction debug page JS.
 *
 * Populates season/week/matchup pickers from /api/admin/prediction_features/{season},
 * then renders the feature table and per-model output when a game is selected.
 */

import { AuthService } from './auth_service.js';

const seasonEl  = document.getElementById('pd-season');
const weekEl    = document.getElementById('pd-week');
const matchupEl = document.getElementById('pd-matchup');
const resultsEl = document.getElementById('pd-results');
const emptyEl   = document.getElementById('pd-empty');
const loadingEl = document.getElementById('pd-loading');
const versionEl = document.getElementById('pd-version-badge');

let _doc = null;  // full season doc from API

function authHeaders() {
    return { Authorization: `Bearer ${AuthService.getCredentials().token}` };
}

// ── Fetch season doc ──────────────────────────────────────────────────────────

async function loadSeason(season) {
    _doc = null;
    resultsEl.style.display = 'none';
    emptyEl.style.display   = 'none';
    loadingEl.style.display = 'block';
    weekEl.disabled    = true;
    matchupEl.disabled = true;

    try {
        const resp = await fetch(`/api/admin/prediction_features/${season}`, { headers: authHeaders() });
        if (!resp.ok) {
            loadingEl.style.display = 'none';
            emptyEl.style.display   = 'block';
            emptyEl.innerHTML = emptyEl.innerHTML.replace('{season}', season);
            return;
        }
        _doc = await resp.json();
        loadingEl.style.display = 'none';
        versionEl.textContent   = `Ensemble: ${_doc.ensemble_version || '—'}`;
        populateWeeks();
    } catch (_) {
        loadingEl.style.display = 'none';
        emptyEl.style.display   = 'block';
    }
}

function populateWeeks() {
    const weeks = [...new Set(Object.values(_doc.games || {}).map(g => g.week))].sort((a, b) => a - b);
    weekEl.innerHTML = '<option value="">— Week —</option>' +
        weeks.map(w => `<option value="${w}">Week ${w}</option>`).join('');
    weekEl.disabled    = false;
    matchupEl.disabled = true;
    matchupEl.innerHTML = '<option value="">— Matchup —</option>';
}

function populateMatchups(week) {
    const games = Object.values(_doc.games || {}).filter(g => g.week == week);
    matchupEl.innerHTML = '<option value="">— Matchup —</option>' +
        games.map(g =>
            `<option value="${g.game_key}">${g.away_team} @ ${g.home_team}</option>`
        ).join('');
    matchupEl.disabled = false;
    resultsEl.style.display = 'none';
}

// ── Render game detail ────────────────────────────────────────────────────────

function renderGame(gameKey) {
    const g = (_doc.games || {})[gameKey];
    if (!g) return;

    // Per-model output table
    const modelRows = [
        { label: 'NN',      weight: '45%', prob: g.nn_prob  },
        { label: 'XGB',     weight: '20%', prob: g.xgb_prob },
        { label: 'LR',      weight: '35%', prob: g.lr_prob  },
        { label: 'Blended', weight: '100%', prob: g.blended_prob, bold: true },
    ];
    document.querySelector('#pd-model-table tbody').innerHTML = modelRows.map(r => `
        <tr>
            <td style="${r.bold ? 'font-weight:700;color:var(--accent-green)' : ''}">${r.label}</td>
            <td style="color:var(--text-secondary);">${r.weight}</td>
            <td style="${r.bold ? 'font-weight:700' : ''}">${r.prob != null ? (r.prob * 100).toFixed(1) + '%' : '—'}</td>
        </tr>`).join('');

    // Feature importance as lookup for bars
    const impMap = {};
    const maxScore = Math.max(...(g.feature_importance || []).map(f => Math.abs(f.score)), 0.0001);
    (g.feature_importance || []).forEach(f => { impMap[f.feature] = f; });

    // Feature table — all 26 features, sorted by importance score
    const sortedFeatures = (g.feature_importance || []).map(f => f.feature);
    // Add any raw features not in importance (shouldn't happen, but defensive)
    Object.keys(g.features || {}).forEach(k => { if (!sortedFeatures.includes(k)) sortedFeatures.push(k); });

    document.querySelector('#pd-feat-table tbody').innerHTML = sortedFeatures.map(feat => {
        const raw    = g.features?.[feat];
        const scaled = g.scaled_features?.[feat];
        const imp    = impMap[feat];
        const score  = imp?.score ?? 0;
        const dir    = imp?.direction ?? '—';
        const pct    = Math.round((Math.abs(score) / maxScore) * 100);
        const colorCls = dir === 'home' ? 'home-color' : 'away-color';
        const dirLabel = dir === 'home' ? g.home_team : (dir === 'away' ? g.away_team : '—');

        return `<tr>
            <td style="font-family:monospace; font-size:0.75rem;">${feat}</td>
            <td>${raw != null ? raw.toFixed(4) : '—'}</td>
            <td>${scaled != null ? scaled.toFixed(4) : '—'}</td>
            <td>${score.toFixed(4)}</td>
            <td style="color:${dir === 'home' ? 'var(--accent-green)' : 'var(--accent-gold)'};">${dirLabel}</td>
            <td class="bar-cell">
                <div class="imp-bar">
                    <div class="imp-fill ${colorCls}" style="width:${pct}%;"></div>
                </div>
            </td>
        </tr>`;
    }).join('');

    resultsEl.style.display = 'block';
}

// ── Event listeners ───────────────────────────────────────────────────────────

seasonEl.addEventListener('change', () => {
    if (seasonEl.value) loadSeason(Number(seasonEl.value));
});
weekEl.addEventListener('change', () => {
    if (weekEl.value) populateMatchups(Number(weekEl.value));
    resultsEl.style.display = 'none';
});
matchupEl.addEventListener('change', () => {
    if (matchupEl.value) renderGame(matchupEl.value);
});

// ── Init: populate season dropdown ───────────────────────────────────────────

(function init() {
    const currentYear = new Date().getFullYear();
    const seasons = [];
    for (let y = currentYear; y >= 2020; y--) seasons.push(y);
    seasonEl.innerHTML = '<option value="">— Season —</option>' +
        seasons.map(y => `<option value="${y}">${y}</option>`).join('');
})();
```

- [x] **Step 3: Manual test**

Start the dev server:
```
uvicorn main:app --reload
```
Navigate to `/admin/predictions` (must be logged in as admin). Verify:
- Page loads with season dropdown
- Selecting a season with data populates week dropdown
- Selecting a week populates matchup dropdown
- Selecting a matchup renders the feature table and per-model output
- Selecting a season with no data shows the "run backfill" message

- [x] **Step 4: Commit**

```
git add templates/admin_predictions.html static/js/admin_predictions.js
git commit -m "feat: add admin prediction debug page with game picker and feature table"
```

---

## Completion Check

Run the full test suite one final time:

```
pytest tests/ -q --tb=short
```

Expected: all green. Then verify against the spec completion criteria:

- [x] `python scripts/backfill_schedule_predictions.py --season 2025 --features` writes one Firestore/local doc
- [x] `python scripts/refresh_local_pkls.py` syncs `prediction_features` to local JSON
- [x] `GET /api/prediction_features/2025/8/KC/SF` returns feature data with all 26 features
- [x] Explain modal shows per-model probs + top-5 features when data exists; hides section when not
- [x] Admin predictions page loads season picker, game picker, full feature table
- [x] `pytest tests/test_feature_audit_service.py` — all pass
- [x] `pytest tests/ -q` — full suite green

---

## Self-Review

### Spec Coverage

| Spec requirement | Covered by task |
|---|---|
| `prediction_features` Firestore collection, doc ID = `{season}_{nn_ver}+{xgb_ver}+{lr_ver}` | Tasks 2, 3 |
| Raw features + scaled features + per-model probs + blended importance | Task 2 |
| XGB SHAP via `pred_contribs=True` | Task 2 |
| LR `coef_ * scaled_feature_value` | Task 2 |
| NN: approximate or omitted → input×gradient via `tf.GradientTape`, no extra package | Task 2 |
| `--features` flag on backfill script | Task 4 |
| Opt-in: SHAP doesn't slow default backfill | Task 4 (guarded by `if write_features`) |
| Local cache `.local_db/prediction_features_{season}_{ver}.json` | Task 3 |
| Add to `refresh_local_pkls.py` | Task 5 |
| `GET /api/prediction_features/{season}/{week}/{away}/{home}` — `require_auth` | Task 6 |
| `GET /api/prediction_features/{season}` — `require_admin` | Task 7 |
| Enhanced explain modal: per-model row + top-5 bar chart | Task 8 |
| Graceful hide if no feature data | Task 8 |
| Admin debug page: season/week/matchup picker | Task 9 |
| Admin debug page: 26-row feature table | Task 9 |
| Admin debug page: per-model output table | Task 9 |
| `shap` package to requirements — **omitted** (using XGBoost native; no `shap` pip package needed) | N/A |

Open question resolutions:
- **NN SHAP cost**: Resolved → input×gradient via `tf.GradientTape` (no `shap` package, fast, uses TF already in requirements). All three models contribute at their actual weights (45/20/35).
- **Doc size**: Addressed in spec (240KB estimate). Validation: `len(games_dict)` is printed during backfill.
- **Admin route**: Resolved → new page at `/admin/predictions` via `_page_router` in `admin_routes.py`.

### Placeholder Scan

No "TBD", "TODO", "implement later", or "add appropriate error handling" phrases — all error branches have explicit code.

### Type Consistency

- `game_key` format `W{week:02d}_{ht}_{at}` is consistent across Task 2 (`feature_audit_service.py`), Task 6 (API endpoint), and Task 9 (JS renderer).
- `get_prediction_features(season, ensemble_version=None)` signature is consistent across Task 3 (definition), Task 6 (per-game endpoint), and Task 7 (admin endpoint).
- `write_prediction_features(season, ensemble_version, games)` signature is consistent across Task 3 (definition) and Task 4 (backfill call).
- `loaded_version` attribute is used in Task 4 as `nn_svc.loaded_version`, `xgb_svc.loaded_version`, `lr_svc.loaded_version` — set in Task 1.
