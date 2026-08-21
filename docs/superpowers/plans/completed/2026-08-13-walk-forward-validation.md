# Walk-Forward Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a diagnostic harness that trains the NN/XGB/LR ensemble on strictly-prior seasons for five expanding-window folds (2021-2025), scores each fold's honest out-of-sample season projection against actual wins and analyst consensus, and reports whether the architecture beats the ≈2.18 consensus MAE bar — without touching any production data or registry.

**Architecture:** One new script (`scripts/walk_forward_validate.py`) orchestrates training, projection, and scoring per fold, reusing the existing `NNPredictionService`/`XGBPredictionService`/`LRPredictionService`/`NNProjectionEngine` classes unmodified except for one small additive constructor change. Fold artifacts persist outside the production model registries; results land in `reports/`.

**Tech Stack:** Python, TensorFlow/Keras (NN), XGBoost, scikit-learn (LR), pandas, pytest.

## Global Constraints

- Diagnostic-only: never write to `preseason_predictions`, never call `save_versioned_model()`/`save_versioned()`, never modify `model_registry.json` / `xgb_registry.json` / `lr_registry.json`.
- Fold years are exactly 2021, 2022, 2023, 2024, 2025 (5 folds). Each fold trains on `build_master_feature_table(min_season=2006, max_season=fold_year-1)` — strictly prior seasons only.
- Fold model artifacts live at `models/walkforward/` and are gitignored — never registered anywhere production code reads.
- `NNProjectionEngine.__init__` gets optional `nn_svc=None, xgb_svc=None, lr_svc=None` params; when all are omitted, behavior is byte-for-byte identical to today (resolves registry `"latest"`).
- Reuse `engine.simulate_season(schedule_df, n_sims=...)` and `scripts/predict_season.py::_load_schedule` unmodified — the real production Monte Carlo path, no shortcuts.
- Feature importance: XGB uses `feature_importance()` (native, already exists), LR uses `feature_importance()` (native, already exists), NN uses a new permutation-importance helper against the fold's own held-out validation split (`NNPredictionService._split_data(feature_table)`, a `@staticmethod`).
- No test in this plan trains a real fold-scale model end-to-end (too slow). Tests use the codebase's existing convention for ML tests: tiny synthetic feature tables (2 seasons, ~20-40 rows) with real (but small/fast) models, guarded by `@pytest.mark.skipif(not TF_AVAILABLE, ...)` / equivalent for XGBoost where TensorFlow/XGBoost is required. See `tests/test_lr_prediction.py`, `tests/test_xgb_prediction.py`, `tests/test_nn_prediction_service.py` for the pattern.

---

## File Structure

- **Modify:** `services/nn_projection_engine.py` — constructor accepts optional pre-built services.
- **Modify:** `tests/test_nn_projection_engine.py` — regression tests for the constructor change.
- **Create:** `scripts/walk_forward_validate.py` — the harness (fold artifact I/O, feature importance, per-fold scoring, CLI/reporting).
- **Create:** `tests/test_walk_forward_validate.py` — unit tests for everything in the new script except the real multi-minute training loop.
- **Modify:** `.gitignore` — add `models/walkforward/`.

---

### Task 1: `NNProjectionEngine` constructor injection

**Files:**
- Modify: `services/nn_projection_engine.py:40-52`
- Test: `tests/test_nn_projection_engine.py`

**Interfaces:**
- Produces: `NNProjectionEngine(nn_svc: NNPredictionService | None = None, xgb_svc: XGBPredictionService | None = None, lr_svc: LRPredictionService | None = None)`. When an arg is `None`, the engine constructs a fresh service of that type and calls its default `load_model()` (unchanged from today). When an arg is provided, the engine uses it directly and never calls `load_model()` on it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_nn_projection_engine.py`:

```python
from unittest.mock import patch, MagicMock


class TestConstructorInjection:
    def test_default_construction_loads_from_registry(self):
        """No args: today's behavior — each service is constructed and load_model() is called."""
        with patch("services.nn_projection_engine.NNPredictionService") as MockNN, \
             patch("services.nn_projection_engine.XGBPredictionService") as MockXGB, \
             patch("services.nn_projection_engine.LRPredictionService") as MockLR:
            engine = NNProjectionEngine()

        MockNN.return_value.load_model.assert_called_once()
        MockXGB.return_value.load_model.assert_called_once()
        MockLR.return_value.load_model.assert_called_once()
        assert engine.svc is MockNN.return_value
        assert engine.xgb_svc is MockXGB.return_value
        assert engine.lr_svc is MockLR.return_value

    def test_injected_services_bypass_load_model(self):
        """Passing pre-built services skips construction and load_model() entirely."""
        fake_nn = MagicMock()
        fake_xgb = MagicMock()
        fake_lr = MagicMock()

        with patch("services.nn_projection_engine.NNPredictionService") as MockNN, \
             patch("services.nn_projection_engine.XGBPredictionService") as MockXGB, \
             patch("services.nn_projection_engine.LRPredictionService") as MockLR:
            engine = NNProjectionEngine(nn_svc=fake_nn, xgb_svc=fake_xgb, lr_svc=fake_lr)

        MockNN.assert_not_called()
        MockXGB.assert_not_called()
        MockLR.assert_not_called()
        fake_nn.load_model.assert_not_called()
        fake_xgb.load_model.assert_not_called()
        fake_lr.load_model.assert_not_called()
        assert engine.svc is fake_nn
        assert engine.xgb_svc is fake_xgb
        assert engine.lr_svc is fake_lr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_nn_projection_engine.py::TestConstructorInjection -v`
Expected: FAIL — `NNProjectionEngine() takes no arguments` (or similar `TypeError`).

- [ ] **Step 3: Implement the constructor change**

Replace `services/nn_projection_engine.py:40-46`:

```python
    def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
        self.svc = nn_svc if nn_svc is not None else NNPredictionService()
        if nn_svc is None:
            self.svc.load_model()
        self.xgb_svc = xgb_svc if xgb_svc is not None else XGBPredictionService()
        if xgb_svc is None:
            self.xgb_svc.load_model()
        self.lr_svc = lr_svc if lr_svc is not None else LRPredictionService()
        if lr_svc is None:
            self.lr_svc.load_model()
```

Leave the rest of `__init__` (the `_team_profiles`, `_preseason_profiles`, etc. attribute defaults) unchanged.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_nn_projection_engine.py -v`
Expected: PASS — all tests in the file, including the pre-existing Elo/K-factor tests.

- [ ] **Step 5: Commit**

```bash
git add services/nn_projection_engine.py tests/test_nn_projection_engine.py
git commit -m "feat: allow NNProjectionEngine to accept pre-built model services"
```

---

### Task 2: Fold artifact persistence helpers

**Files:**
- Create: `scripts/walk_forward_validate.py` (module skeleton + this task's functions)
- Test: `tests/test_walk_forward_validate.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `NNPredictionService`, `XGBPredictionService`, `LRPredictionService` (their `.model`/`.scaler` attributes after `.train()`).
- Produces:
  - `_save_fold_artifacts(artifacts_dir: Path, fold_year: int, nn_svc, xgb_svc, lr_svc) -> None`
  - `_fold_artifacts_exist(artifacts_dir: Path, fold_year: int) -> bool`
  - `_load_fold_artifacts(artifacts_dir: Path, fold_year: int) -> tuple[NNPredictionService, XGBPredictionService, LRPredictionService]`

These bypass every service's registry-integrated save path (`save_versioned`/`save_versioned_model`) — production registries must never see fold artifacts. Note `NNPredictionService.save_model()` has an existing bug: it always writes the scaler to a hardcoded `nn_v1_scaler.pkl` regardless of the `path` argument (see `services/nn_prediction_service.py:559`), which would silently clobber fold scalers if reused as-is across folds. These helpers write scaler files directly instead of calling `save_model()`, using the same stem-based naming `NNPredictionService.load_model(path=...)` already expects (`{stem}_scaler.pkl`) so the existing loader (used for resume) finds them correctly.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_walk_forward_validate.py`:

```python
"""tests/test_walk_forward_validate.py -- Unit tests for scripts/walk_forward_validate.py."""

import numpy as np
import pandas as pd
import pytest
from importlib.util import find_spec

TF_AVAILABLE = find_spec("tensorflow") is not None
XGB_AVAILABLE = find_spec("xgboost") is not None
SKLEARN_AVAILABLE = find_spec("sklearn") is not None


@pytest.fixture(scope="module")
def tiny_feature_table():
    """20-row, 2-season synthetic feature table -- fast real training for all 3 model types.

    Mirrors the fixture shape used in tests/test_lr_prediction.py and
    tests/test_xgb_prediction.py: interleaved wins/losses so every split
    segment contains both classes.
    """
    from services.nn_feature_engine import FEATURE_COLUMNS

    rng = np.random.default_rng(42)
    n = len(FEATURE_COLUMNS)
    df = pd.DataFrame(rng.standard_normal((20, n)), columns=FEATURE_COLUMNS)
    df["home_win"] = [1.0 if i % 2 == 0 else 0.0 for i in range(20)]
    df["season"] = [2020] * 10 + [2021] * 10
    df["week"] = list(range(1, 11)) * 2
    nfl_teams = ["NE", "BUF", "MIA", "NYJ", "BAL", "CLE", "PIT", "CIN",
                 "KC", "LAC", "DEN", "LV", "DAL", "PHI", "NYG", "WAS",
                 "GB", "MIN", "CHI", "DET"]
    df["home_team"] = nfl_teams
    df["away_team"] = nfl_teams[::-1]
    return df


@pytest.fixture
def trained_lr_svc(tiny_feature_table):
    from services.lr_prediction_service import LRPredictionService
    svc = LRPredictionService()
    svc.train(tiny_feature_table)
    return svc


@pytest.mark.skipif(not XGB_AVAILABLE or not SKLEARN_AVAILABLE, reason="xgboost/sklearn not installed")
class TestFoldArtifactRoundTrip:
    def test_save_then_load_preserves_predictions(self, tmp_path, tiny_feature_table, trained_lr_svc):
        """Saving and reloading a fold's LR model must produce identical predictions.

        LR is the cheapest of the three real model types to round-trip for real
        (no TF import cost), so it stands in for the save/load contract shared
        by all three artifact types.
        """
        from scripts.walk_forward_validate import _save_fold_artifacts, _load_fold_artifacts
        from services.xgb_prediction_service import XGBPredictionService
        from services.nn_feature_engine import FEATURE_COLUMNS

        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)

        row = tiny_feature_table.iloc[[0]]
        features = {c: float(row[c].iloc[0]) for c in FEATURE_COLUMNS}
        expected_lr_pred = trained_lr_svc.predict_game(features)
        expected_xgb_pred = xgb_svc.predict_game(features)

        # NN save/load requires a real Keras model; skip it here and cover it
        # in test_nn_round_trip_uses_correct_scaler_filename below instead.
        _save_fold_artifacts(tmp_path, 2021, nn_svc=None, xgb_svc=xgb_svc, lr_svc=trained_lr_svc,
                              skip_nn=True)

        assert (tmp_path / "xgb_2021.json").exists()
        assert (tmp_path / "xgb_2021_scaler.pkl").exists()
        assert (tmp_path / "lr_2021.pkl").exists()
        assert (tmp_path / "lr_2021_scaler.pkl").exists()

        _, loaded_xgb, loaded_lr = _load_fold_artifacts(tmp_path, 2021, load_nn=False)

        assert loaded_lr.predict_game(features) == pytest.approx(expected_lr_pred)
        assert loaded_xgb.predict_game(features) == pytest.approx(expected_xgb_pred)

    def test_fold_artifacts_exist_false_when_missing(self, tmp_path):
        from scripts.walk_forward_validate import _fold_artifacts_exist
        assert _fold_artifacts_exist(tmp_path, 2021) is False

    def test_fold_artifacts_exist_true_after_save(self, tmp_path, tiny_feature_table, trained_lr_svc):
        from scripts.walk_forward_validate import _save_fold_artifacts, _fold_artifacts_exist
        from services.xgb_prediction_service import XGBPredictionService

        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)

        assert _fold_artifacts_exist(tmp_path, 2022) is False
        _save_fold_artifacts(tmp_path, 2022, nn_svc=None, xgb_svc=xgb_svc, lr_svc=trained_lr_svc,
                              skip_nn=True)
        # With skip_nn=True the NN files are absent, so existence must stay False
        # until the NN artifact is present too -- fold artifacts are all-or-nothing.
        assert _fold_artifacts_exist(tmp_path, 2022) is False


@pytest.mark.skipif(not TF_AVAILABLE or not SKLEARN_AVAILABLE, reason="tensorflow/sklearn not installed")
class TestNNArtifactRoundTrip:
    def test_nn_round_trip_uses_correct_scaler_filename(self, tmp_path, tiny_feature_table):
        """Regression guard for the NNPredictionService.save_model() scaler-naming bug --
        two folds' NN scalers must never collide on disk."""
        from services.nn_prediction_service import NNPredictionService
        from scripts.walk_forward_validate import _save_fold_artifacts, _load_fold_artifacts
        from services.nn_feature_engine import FEATURE_COLUMNS

        svc_2021 = NNPredictionService()
        svc_2021.train(tiny_feature_table)
        svc_2022 = NNPredictionService()
        svc_2022.train(tiny_feature_table)

        _save_fold_artifacts(tmp_path, 2021, nn_svc=svc_2021, xgb_svc=None, lr_svc=None, skip_xgb=True, skip_lr=True)
        _save_fold_artifacts(tmp_path, 2022, nn_svc=svc_2022, xgb_svc=None, lr_svc=None, skip_xgb=True, skip_lr=True)

        assert (tmp_path / "nn_2021_scaler.pkl").exists()
        assert (tmp_path / "nn_2022_scaler.pkl").exists()

        loaded_2021, _, _ = _load_fold_artifacts(tmp_path, 2021, load_xgb=False, load_lr=False)

        row = tiny_feature_table.iloc[[0]]
        features = {c: float(row[c].iloc[0]) for c in FEATURE_COLUMNS}
        assert loaded_2021.predict_game(features) == pytest.approx(svc_2021.predict_game(features), abs=1e-4)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_walk_forward_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.walk_forward_validate'`.

- [ ] **Step 3: Write the module skeleton and the three functions**

Create `scripts/walk_forward_validate.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_walk_forward_validate.py -v`
Expected: PASS (tests requiring TensorFlow/XGBoost skip cleanly if those packages aren't installed in the current environment; run `pip install -r requirements.txt -r requirements-ml.txt` first if you want them to execute).

- [ ] **Step 5: Add the gitignore entry**

Add to `.gitignore`, near the existing `models/*.keras` / `models/*.pkl` rules (around line 41):

```
models/walkforward/
```

- [ ] **Step 6: Commit**

```bash
git add scripts/walk_forward_validate.py tests/test_walk_forward_validate.py .gitignore
git commit -m "feat: add fold artifact persistence for walk-forward validation"
```

---

### Task 3: Feature importance helpers

**Files:**
- Modify: `scripts/walk_forward_validate.py`
- Test: `tests/test_walk_forward_validate.py`

**Interfaces:**
- Consumes: `XGBPredictionService.feature_importance(top_n) -> pd.DataFrame` (columns `feature, importance`), `LRPredictionService.feature_importance(top_n) -> pd.DataFrame` (columns `feature, abs_coef, coef`) — both already exist and are unmodified. `NNPredictionService._split_data(feature_table)` (staticmethod, already exists) for the NN validation split.
- Produces:
  - `_nn_permutation_importance(nn_svc, val_df: pd.DataFrame) -> pd.DataFrame` — columns `feature, importance, importance_rank`.
  - `_collect_feature_importance(fold_year: int, nn_svc, xgb_svc, lr_svc, val_df: pd.DataFrame) -> pd.DataFrame` — columns `season, model, feature, importance_rank, importance_value` (the report schema from the spec).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_walk_forward_validate.py`:

```python
@pytest.mark.skipif(not TF_AVAILABLE or not SKLEARN_AVAILABLE, reason="tensorflow/sklearn not installed")
class TestNNPermutationImportance:
    def test_returns_one_row_per_feature_ranked(self, tiny_feature_table):
        from services.nn_prediction_service import NNPredictionService
        from services.nn_feature_engine import FEATURE_COLUMNS
        from scripts.walk_forward_validate import _nn_permutation_importance

        svc = NNPredictionService()
        svc.train(tiny_feature_table)
        _, val_df, _ = NNPredictionService._split_data(tiny_feature_table)

        result = _nn_permutation_importance(svc, val_df)

        assert set(result["feature"]) == set(FEATURE_COLUMNS)
        assert len(result) == len(FEATURE_COLUMNS)
        # Ranks are a contiguous 1..N sequence with no ties collapsed away
        assert sorted(result["importance_rank"].tolist()) == list(range(1, len(FEATURE_COLUMNS) + 1))
        # Sorted descending by importance
        assert list(result["importance"]) == sorted(result["importance"], reverse=True)


@pytest.mark.skipif(not XGB_AVAILABLE or not TF_AVAILABLE or not SKLEARN_AVAILABLE,
                     reason="tensorflow/xgboost/sklearn not installed")
class TestCollectFeatureImportance:
    def test_combines_all_three_models_with_common_schema(self, tiny_feature_table, trained_lr_svc):
        from services.nn_prediction_service import NNPredictionService
        from services.xgb_prediction_service import XGBPredictionService
        from scripts.walk_forward_validate import _collect_feature_importance

        nn_svc = NNPredictionService()
        nn_svc.train(tiny_feature_table)
        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)
        _, val_df, _ = NNPredictionService._split_data(tiny_feature_table)

        result = _collect_feature_importance(2021, nn_svc, xgb_svc, trained_lr_svc, val_df)

        assert set(result.columns) == {"season", "model", "feature", "importance_rank", "importance_value"}
        assert set(result["model"]) == {"nn", "xgb", "lr"}
        assert (result["season"] == 2021).all()
        # Every model contributes a rank-1 row (its top feature)
        for model in ("nn", "xgb", "lr"):
            assert 1 in result[result["model"] == model]["importance_rank"].values
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_walk_forward_validate.py::TestNNPermutationImportance tests/test_walk_forward_validate.py::TestCollectFeatureImportance -v`
Expected: FAIL — `ImportError: cannot import name '_nn_permutation_importance'`.

- [ ] **Step 3: Implement both functions**

Add to `scripts/walk_forward_validate.py`:

```python
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
```

Note: `feature_importance()` defaults to `top_n=15` and truncates, so passing `len(FEATURE_COLUMNS)` explicitly is required to get the full, untruncated list without modifying `XGBPredictionService`/`LRPredictionService` (out of scope per the Global Constraints).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_walk_forward_validate.py -v`
Expected: PASS (skip-guarded tests skip cleanly without ML deps installed).

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_validate.py tests/test_walk_forward_validate.py
git commit -m "feat: add per-fold feature importance collection"
```

---

### Task 4: Per-fold scoring pipeline (`run_fold`)

**Files:**
- Modify: `scripts/walk_forward_validate.py`
- Test: `tests/test_walk_forward_validate.py`

**Interfaces:**
- Consumes: `Task 1`'s `NNProjectionEngine(nn_svc=, xgb_svc=, lr_svc=)`; `Task 2`'s `_fold_artifacts_exist`/`_save_fold_artifacts`/`_load_fold_artifacts`; `Task 3`'s `_collect_feature_importance`; `scripts.predict_season._load_schedule(rawdata_dir, season, prior_season)` (existing, unmodified); `services.nn_feature_engine.build_master_feature_table(min_season, max_season)` (existing); `services.db_service.get_collection_df(collection, filters)` (existing); `services.data_service.get_consensus_projections(season) -> Dict[str, dict]` (existing).
- Produces:
  - `_get_or_train_fold_models(fold_year, artifacts_dir, feature_table, force=False) -> tuple[nn_svc, xgb_svc, lr_svc]`
  - `_project_fold_season(fold_year, nn_svc, xgb_svc, lr_svc) -> Dict[str, float]` (team → mean projected wins)
  - `_actual_wins(fold_year) -> Dict[str, float]` (team → actual regular-season wins)
  - `_consensus_wins(fold_year) -> Dict[str, float]` (team → analyst consensus mean, omitting teams with no consensus)
  - `run_fold(fold_year, artifacts_dir, force=False) -> dict` with keys `"rows"` (`List[dict]`, each `season, team, actual_wins, model_wins, model_abs_err, consensus_wins, consensus_abs_err`) and `"importance"` (`pd.DataFrame` from Task 3).

This task is glue: every expensive real computation (training, `simulate_season`, Firestore reads) is isolated behind the four helper functions above so `run_fold`'s row-assembly logic can be tested by monkeypatching them, without spinning up real training or a real Monte Carlo run.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_walk_forward_validate.py`:

```python
class TestRunFold:
    def test_assembles_rows_with_correct_errors(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "_get_or_train_fold_models",
                             lambda *a, **k: ("fake_nn", "fake_xgb", "fake_lr"))
        monkeypatch.setattr(wfv, "_project_fold_season",
                             lambda *a, **k: {"BUF": 11.0, "KC": 9.5, "NE": 6.0})
        monkeypatch.setattr(wfv, "_actual_wins",
                             lambda *a, **k: {"BUF": 13.0, "KC": 11.0, "NE": 4.0})
        monkeypatch.setattr(wfv, "_consensus_wins",
                             lambda *a, **k: {"BUF": 12.0, "KC": 10.5})  # NE missing on purpose
        monkeypatch.setattr(wfv, "build_master_feature_table", lambda **k: pd.DataFrame())
        monkeypatch.setattr(wfv, "_collect_feature_importance",
                             lambda *a, **k: pd.DataFrame([{"season": 2021, "model": "xgb",
                                                             "feature": "elo_diff",
                                                             "importance_rank": 1,
                                                             "importance_value": 0.5}]))

        result = wfv.run_fold(2021, tmp_path)
        rows = {r["team"]: r for r in result["rows"]}

        assert rows["BUF"]["model_abs_err"] == pytest.approx(2.0)
        assert rows["BUF"]["consensus_abs_err"] == pytest.approx(1.0)
        assert rows["KC"]["model_abs_err"] == pytest.approx(1.5)
        assert rows["NE"]["consensus_wins"] is None
        assert rows["NE"]["consensus_abs_err"] is None
        assert rows["NE"]["model_abs_err"] == pytest.approx(2.0)
        assert len(result["importance"]) == 1

    def test_skips_teams_with_no_model_projection(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "_get_or_train_fold_models", lambda *a, **k: (None, None, None))
        monkeypatch.setattr(wfv, "_project_fold_season", lambda *a, **k: {"BUF": 11.0})
        monkeypatch.setattr(wfv, "_actual_wins", lambda *a, **k: {"BUF": 13.0, "KC": 11.0})
        monkeypatch.setattr(wfv, "_consensus_wins", lambda *a, **k: {})
        monkeypatch.setattr(wfv, "build_master_feature_table", lambda **k: pd.DataFrame())
        monkeypatch.setattr(wfv, "_collect_feature_importance", lambda *a, **k: pd.DataFrame())

        result = wfv.run_fold(2021, tmp_path)
        teams = {r["team"] for r in result["rows"]}
        assert teams == {"BUF"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_walk_forward_validate.py::TestRunFold -v`
Expected: FAIL — `AttributeError: module 'scripts.walk_forward_validate' has no attribute '_get_or_train_fold_models'`.

- [ ] **Step 3: Implement the helpers and `run_fold`**

Add to `scripts/walk_forward_validate.py` (near the top, with the other imports, add `from services.nn_feature_engine import build_master_feature_table, RAWDATA_DIR` and `from services.nn_prediction_service import NNPredictionService` at module level so tests can monkeypatch `wfv.build_master_feature_table` directly):

```python
from services.nn_feature_engine import build_master_feature_table, RAWDATA_DIR
from services.nn_prediction_service import NNPredictionService


def _get_or_train_fold_models(fold_year, artifacts_dir, feature_table, force=False):
    if not force and _fold_artifacts_exist(artifacts_dir, fold_year):
        logger.info("[%d] Cached fold artifacts found, loading.", fold_year)
        return _load_fold_artifacts(artifacts_dir, fold_year)

    logger.info("[%d] Training fold models on seasons <= %d.", fold_year, fold_year - 1)
    from services.xgb_prediction_service import XGBPredictionService
    from services.lr_prediction_service import LRPredictionService

    nn_svc = NNPredictionService()
    nn_svc.train(feature_table)

    xgb_svc = XGBPredictionService()
    xgb_svc.train(feature_table)

    lr_svc = LRPredictionService()
    lr_svc.train(feature_table)

    _save_fold_artifacts(artifacts_dir, fold_year, nn_svc, xgb_svc, lr_svc)
    return nn_svc, xgb_svc, lr_svc


def _project_fold_season(fold_year, nn_svc, xgb_svc, lr_svc):
    from services.nn_projection_engine import NNProjectionEngine
    from scripts.predict_season import _load_schedule

    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)
    engine.initialize(fold_year)
    schedule = _load_schedule(RAWDATA_DIR, fold_year, fold_year - 1)
    results = engine.simulate_season(schedule, n_sims=10_000)
    return {team: stats["mean_wins"] for team, stats in results["team_stats"].items()}


def _actual_wins(fold_year):
    from services.db_service import get_collection_df
    df = get_collection_df("nfl_standings", filters=[("season", "==", fold_year)])
    if df.empty:
        return {}
    return dict(zip(df["team"], df["wins"]))


def _consensus_wins(fold_year):
    from services.data_service import get_consensus_projections
    consensus = get_consensus_projections(fold_year)
    return {
        team: v["consensus_mean"]
        for team, v in consensus.items()
        if v.get("consensus_mean") is not None
    }


def run_fold(fold_year, artifacts_dir, force=False):
    feature_table = build_master_feature_table(min_season=2006, max_season=fold_year - 1)
    nn_svc, xgb_svc, lr_svc = _get_or_train_fold_models(fold_year, artifacts_dir, feature_table, force)

    model_wins = _project_fold_season(fold_year, nn_svc, xgb_svc, lr_svc)
    actual = _actual_wins(fold_year)
    consensus = _consensus_wins(fold_year)

    _, val_df, _ = NNPredictionService._split_data(feature_table)
    importance_df = _collect_feature_importance(fold_year, nn_svc, xgb_svc, lr_svc, val_df)

    rows = []
    for team, actual_w in actual.items():
        if team not in model_wins:
            continue
        row = {
            "season": fold_year,
            "team": team,
            "actual_wins": actual_w,
            "model_wins": round(model_wins[team], 2),
            "model_abs_err": round(abs(model_wins[team] - actual_w), 2),
        }
        if team in consensus:
            row["consensus_wins"] = consensus[team]
            row["consensus_abs_err"] = round(abs(consensus[team] - actual_w), 2)
        else:
            row["consensus_wins"] = None
            row["consensus_abs_err"] = None
        rows.append(row)

    return {"rows": rows, "importance": importance_df}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_walk_forward_validate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/walk_forward_validate.py tests/test_walk_forward_validate.py
git commit -m "feat: add per-fold walk-forward scoring pipeline"
```

---

### Task 5: CLI entrypoint and reporting

**Files:**
- Modify: `scripts/walk_forward_validate.py`
- Test: `tests/test_walk_forward_validate.py`

**Interfaces:**
- Consumes: `Task 4`'s `run_fold(fold_year, artifacts_dir, force) -> dict`.
- Produces: `_print_summary(report_df: pd.DataFrame) -> None`; `main()` (argparse entrypoint) — writes `reports/walk_forward_validation.csv` and `reports/walk_forward_feature_importance.csv`, one bad fold logs an error and is skipped rather than aborting the run.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_walk_forward_validate.py`:

```python
class TestPrintSummary:
    def test_prints_per_season_and_overall_mae(self, capsys):
        import pandas as pd
        from scripts.walk_forward_validate import _print_summary

        df = pd.DataFrame([
            {"season": 2021, "team": "BUF", "actual_wins": 13, "model_wins": 11,
             "model_abs_err": 2.0, "consensus_wins": 12, "consensus_abs_err": 1.0},
            {"season": 2021, "team": "KC", "actual_wins": 11, "model_wins": 9.5,
             "model_abs_err": 1.5, "consensus_wins": None, "consensus_abs_err": None},
            {"season": 2022, "team": "BUF", "actual_wins": 12, "model_wins": 10,
             "model_abs_err": 2.0, "consensus_wins": 11, "consensus_abs_err": 1.0},
        ])

        _print_summary(df)
        out = capsys.readouterr().out

        assert "2021" in out
        assert "2022" in out
        assert "ALL" in out

    def test_handles_empty_report(self, capsys):
        import pandas as pd
        from scripts.walk_forward_validate import _print_summary

        _print_summary(pd.DataFrame())
        out = capsys.readouterr().out
        assert "No folds completed" in out


class TestMainLoop:
    def test_one_bad_fold_does_not_abort_the_run(self, monkeypatch, tmp_path):
        """A fold that raises during run_fold is logged and skipped; the
        remaining folds still produce a report."""
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "ARTIFACTS_DIR", tmp_path / "walkforward")
        monkeypatch.setattr(wfv, "REPORTS_DIR", tmp_path / "reports")

        def fake_run_fold(fold_year, artifacts_dir, force=False):
            if fold_year == 2022:
                raise RuntimeError("simulated feature table build failure")
            return {
                "rows": [{"season": fold_year, "team": "BUF", "actual_wins": 12,
                          "model_wins": 11, "model_abs_err": 1.0,
                          "consensus_wins": 11.5, "consensus_abs_err": 0.5}],
                "importance": pd.DataFrame([{"season": fold_year, "model": "xgb",
                                              "feature": "elo_diff",
                                              "importance_rank": 1, "importance_value": 0.5}]),
            }

        monkeypatch.setattr(wfv, "run_fold", fake_run_fold)
        monkeypatch.setattr(sys, "argv", ["walk_forward_validate.py", "--seasons", "2021", "2022"])

        wfv.main()

        report = pd.read_csv(wfv.REPORTS_DIR / "walk_forward_validation.csv")
        assert set(report["season"]) == {2021}  # 2022 skipped, no crash

        importance = pd.read_csv(wfv.REPORTS_DIR / "walk_forward_feature_importance.csv")
        assert set(importance["season"]) == {2021}
```

Add `import sys` near the top of `tests/test_walk_forward_validate.py` if not already present (needed for `monkeypatch.setattr(sys, "argv", ...)`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_walk_forward_validate.py::TestPrintSummary tests/test_walk_forward_validate.py::TestMainLoop -v`
Expected: FAIL — `ImportError: cannot import name '_print_summary'`.

- [ ] **Step 3: Implement `_print_summary` and `main()`**

Add to `scripts/walk_forward_validate.py`:

```python
def _print_summary(report_df):
    print(f"\n{'=' * 60}")
    print("  Walk-Forward Validation Summary")
    print(f"{'=' * 60}")

    if report_df.empty:
        print("\nNo folds completed successfully.")
        return

    print(f"  {'Season':<8}{'Model MAE':<12}{'Consensus MAE':<15}{'n':<5}")
    for season, grp in report_df.groupby("season"):
        cons = grp.dropna(subset=["consensus_abs_err"])
        cons_mae = f"{cons['consensus_abs_err'].mean():.2f}" if not cons.empty else "n/a"
        print(f"  {season:<8}{grp['model_abs_err'].mean():<12.2f}{cons_mae:<15}{len(grp):<5}")

    overall_cons = report_df.dropna(subset=["consensus_abs_err"])
    overall_cons_mae = f"{overall_cons['consensus_abs_err'].mean():.2f}" if not overall_cons.empty else "n/a"
    print(f"  {'ALL':<8}{report_df['model_abs_err'].mean():<12.2f}{overall_cons_mae:<15}{len(report_df):<5}")
    print("\n  Benchmark bar (2017-2025 pooled analyst consensus): 2.18")


def main():
    import pandas as pd

    parser = argparse.ArgumentParser(description="Walk-forward validation harness")
    parser.add_argument("--seasons", type=int, nargs=2, default=[FOLD_START_DEFAULT, FOLD_END_DEFAULT],
                         metavar=("START", "END"))
    parser.add_argument("--force", action="store_true",
                         help="Retrain fold models even if cached artifacts exist")
    args = parser.parse_args()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    all_rows = []
    all_importance = []
    for fold_year in range(args.seasons[0], args.seasons[1] + 1):
        print(f"\n{'=' * 60}\n  Fold {fold_year}\n{'=' * 60}")
        try:
            result = run_fold(fold_year, ARTIFACTS_DIR, force=args.force)
        except Exception as exc:
            logger.error("Fold %d failed: %s", fold_year, exc)
            continue
        all_rows.extend(result["rows"])
        all_importance.append(result["importance"])

    report_df = pd.DataFrame(all_rows)
    report_df.to_csv(REPORTS_DIR / "walk_forward_validation.csv", index=False)

    importance_df = pd.concat(all_importance, ignore_index=True) if all_importance else pd.DataFrame()
    importance_df.to_csv(REPORTS_DIR / "walk_forward_feature_importance.csv", index=False)

    _print_summary(report_df)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_walk_forward_validate.py -v`
Expected: PASS — full file, all tasks.

- [ ] **Step 5: Run the complete test suite**

Run: `pytest tests/ -q`
Expected: PASS, no regressions elsewhere (in particular `tests/test_nn_projection_engine.py` and any test that constructs `NNProjectionEngine()` with no args).

- [ ] **Step 6: Commit**

```bash
git add scripts/walk_forward_validate.py tests/test_walk_forward_validate.py
git commit -m "feat: add walk-forward validation CLI and reporting"
```

---

## Manual Verification (not part of automated tests)

The harness itself is validated by actually running it, not by CI — per the spec, no test trains a real fold-scale model. After Task 5 is committed:

1. Ensure ML dependencies are installed: `pip install -r requirements.txt -r requirements-ml.txt`.
2. Run: `python scripts/walk_forward_validate.py` — this trains 15 real models (5 folds × 3 types) and will take a while; there's no prior timing measurement, so treat the first run as that measurement.
3. Check `reports/walk_forward_validation.csv` and `reports/walk_forward_feature_importance.csv` exist and look sane (32 teams × 5 seasons = 160 rows in the first; a `season, model, feature, importance_rank, importance_value` row per feature per model per fold in the second).
4. Compare the printed overall model MAE against the 2.18 consensus benchmark bar.
5. Force-add the two report CSVs despite `reports/` being gitignored, following the existing precedent of `reports/nn_weekly_accuracy.csv`:
   ```bash
   git add -f reports/walk_forward_validation.csv reports/walk_forward_feature_importance.csv
   git commit -m "data: walk-forward validation results for 2021-2025"
   ```
6. Verify `models/walkforward/` was NOT staged (it's gitignored per Task 2) and that `model_registry.json` / `xgb_registry.json` / `lr_registry.json` show no diff (`git status`).

---

## Self-Review

**Spec coverage:**
- Fold structure (2021-2025, expanding window) — Task 4 (`run_fold` calls `build_master_feature_table(max_season=fold_year-1)`).
- Per-fold procedure (train → save → inject → simulate → score) — Tasks 2, 4.
- Architecture / `NNProjectionEngine` constructor change — Task 1.
- Data flow — Tasks 2-5 in sequence match the spec's diagram.
- Feature importance reporting — Task 3.
- Artifact retention outside registries, `.gitignore` — Task 2.
- Resume support / `--force` — Task 4 (`_get_or_train_fold_models`) and Task 5 (CLI flag).
- Error handling (bad fold doesn't abort the run; missing consensus is null, not a crash) — Task 4 (`_consensus_wins` omits missing teams, `run_fold` writes `None`), Task 5 (`main()`'s try/except per fold).
- Testing section's constraints (no real fold-scale training in CI, mirror existing ML test conventions) — every task's test step.
- Reporting outputs committed despite `reports/` gitignore, following `nn_weekly_accuracy.csv` precedent — Manual Verification step 5.

**Placeholder scan:** No TBD/TODO; every step has complete, runnable code.

**Type consistency:** `run_fold` returns `{"rows": [...], "importance": pd.DataFrame}` consistently between Task 4's implementation and Task 5's consumption (`result["rows"]`, `result["importance"]`). `_fold_artifacts_exist` / `_save_fold_artifacts` / `_load_fold_artifacts` signatures match between Task 2's definition and Task 4's usage. `ARTIFACTS_DIR` / `REPORTS_DIR` module-level constants (Task 2) are the same names Task 5's tests monkeypatch.

**Scope check:** Single cohesive deliverable (one script + one small production change); no decomposition needed.
