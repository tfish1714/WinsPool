# Feature-Version Stamp + Training-Time Promotion Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp every stored prediction (per-game `game_predictions` entries and
`prediction_features` audit docs) with a git-commit feature-computation
version, and block a newly-trained NN/XGB/LR model from becoming the
resolved "latest"/"best" version if it regresses vs. the best model that
shares its exact feature schema.

**Architecture:** Two small new shared modules (`services/model_version.py`
for the git-SHA stamp, `services/model_promotion.py` for the gate logic),
wired into the three training services' `save_versioned()` methods and into
the two prediction-writing paths (`cache_builder.py`'s daily job,
`backfill_schedule_predictions.py`'s manual/weekly backfill). Docker images
get the git SHA baked in at build time via a build arg, since `.dockerignore`
excludes `.git/` and `git rev-parse` cannot run inside the deployed
container.

**Tech Stack:** Python (services/scripts), pytest (existing `tests/` unit
suite), Docker build args, Cloud Build substitutions, PowerShell (deploy.ps1).

**Spec:** `docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md`
— see "Folded-in designs" → "Model quality gating" (promotion gate
pseudocode) and "Feature computation versioning" (SHA/granularity/storage
decisions), and the Stage 2/Stage 4 findings that motivate this work
(Rollout items #2 and #3). Read that section in full before starting;
this plan implements it, not restates it.

## Global Constraints

- Single repo-wide feature version (a git commit SHA), not per-feature-family
  — per the spec's "Granularity" decision.
- Git commit SHA, not a maintained semantic version — no human has to
  remember to bump anything.
- Forward-only: no retroactive backfill of the version onto historical
  `game_predictions`/`prediction_features` records.
- The stamp must land on **both** `prediction_features` docs (as
  `feature_version`, alongside the existing `ensemble_version`) **and every
  `game_predictions` entry** (top-level, not nested inside `explanation`)
  — Stage 4 found the latter had no version field anywhere.
- The promotion gate is schema-scoped: it must only compare a candidate
  model's metrics against versions sharing its **exact** `feature_columns`
  list. Never compare against `best_by`/`registry["latest"]` blindly — that
  is what caused the original invalid "XGB v9 is a severe regression"
  finding (compared against a version that still had `spread_line`).
- The gate fails loud (`raise ValueError`), never a silent skip, per this
  codebase's established alerting convention. It must be overridable via an
  explicit `--force-promote` CLI flag, not a config default.
- A freshly-computed `game_predictions`/`prediction_features` entry gets the
  new stamp; an entry preserved unchanged from a prior run (the `--force`
  lock-preservation path in `backfill_schedule_predictions.py`, and
  `merge_thin_game_predictions()` in `cache_builder.py`) must **not** be
  re-stamped with the current run's version — that would misrepresent when
  it was actually produced.

---

## Task 1: `services/model_version.py` — feature-version + ensemble-version helpers

**Files:**
- Create: `services/model_version.py`
- Test: `tests/test_model_version.py`

**Interfaces:**
- Produces: `get_feature_version() -> str` (git SHA, `GIT_SHA` env override,
  falls back to `"unknown"`); `build_ensemble_version_string(nn_svc, xgb_svc,
  lr_svc) -> str` (reads each service's `.loaded_version` attribute — already
  present on all three prediction services per `tests/test_loaded_version.py`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_version.py
import subprocess
from unittest.mock import MagicMock, patch


class TestGetFeatureVersion:
    def test_prefers_env_var_when_set(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.setenv("GIT_SHA", "abc1234")
        assert get_feature_version() == "abc1234"

    def test_falls_back_to_git_when_env_unset(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.delenv("GIT_SHA", raising=False)
        fake_result = MagicMock(stdout="deadbeef1234\n")
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            assert get_feature_version() == "deadbeef1234"
            assert mock_run.call_args.args[0] == ["git", "rev-parse", "HEAD"]

    def test_falls_back_to_git_when_env_is_literal_unknown(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.setenv("GIT_SHA", "unknown")
        fake_result = MagicMock(stdout="cafef00d\n")
        with patch("subprocess.run", return_value=fake_result):
            assert get_feature_version() == "cafef00d"

    def test_returns_unknown_when_git_unavailable(self, monkeypatch):
        from services.model_version import get_feature_version
        monkeypatch.delenv("GIT_SHA", raising=False)
        with patch("subprocess.run", side_effect=subprocess.SubprocessError("no git")):
            assert get_feature_version() == "unknown"


class TestBuildEnsembleVersionString:
    def test_format(self):
        from services.model_version import build_ensemble_version_string
        nn_svc = MagicMock(loaded_version="v10")
        xgb_svc = MagicMock(loaded_version="v4")
        lr_svc = MagicMock(loaded_version="v2")
        assert build_ensemble_version_string(nn_svc, xgb_svc, lr_svc) == "nn_v10+xgb_v4+lr_v2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_model_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.model_version'`

- [ ] **Step 3: Write the implementation**

```python
# services/model_version.py
"""services/model_version.py -- Feature-computation-version stamping.

A single git commit SHA identifies "which version of the feature-engine /
serving code produced this prediction or training run's inputs." Forward-only
by design -- see docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md
"Feature computation versioning" for why (manual semantic versioning already
failed twice in this codebase's history; a SHA needs no human to remember it).
"""
import os
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).parent.parent


def get_feature_version() -> str:
    """Return the git commit SHA identifying the running code's feature-engine version.

    Cloud Run Jobs run in Docker images built with `.git/` excluded
    (see .dockerignore) -- `git rev-parse` cannot work at runtime in that
    environment. The GIT_SHA env var is baked in at Docker build time instead
    (see Dockerfile.sync / Dockerfile.predict + cloudbuild-*.yaml), sourced
    from the real checkout that ran `gcloud builds submit` (deploy.ps1).
    Local dev and manually-run training scripts fall back to a live
    `git rev-parse` since they run from a real checkout with `.git/` present.
    """
    env_sha = os.environ.get("GIT_SHA")
    if env_sha and env_sha != "unknown":
        return env_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def build_ensemble_version_string(nn_svc, xgb_svc, lr_svc) -> str:
    """The canonical 'nn_vX+xgb_vY+lr_vZ' string identifying which version
    each of the three ensemble members loaded."""
    return f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_model_version.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add services/model_version.py tests/test_model_version.py
git commit -m "$(cat <<'EOF'
feat: add feature-version (git SHA) and ensemble-version-string helpers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 2: `services/model_promotion.py` — schema-scoped promotion gate

**Files:**
- Create: `services/model_promotion.py`
- Test: `tests/test_model_promotion.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `PROMOTION_GATES: dict[str, float]`;
  `find_same_schema_best(entries: list[dict], new_feature_columns: list[str]) -> dict | None`;
  `assert_promotion_ready(new_metrics: dict, best_metrics: dict | None, model_name: str) -> None`
  (raises `ValueError` on failure). Task 3/4/5 call these from each service's
  `save_versioned()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_promotion.py
import pytest


class TestFindSameSchemaBest:
    def test_returns_none_when_no_entries(self):
        from services.model_promotion import find_same_schema_best
        assert find_same_schema_best([], ["a", "b"]) is None

    def test_ignores_entries_with_different_schema(self):
        from services.model_promotion import find_same_schema_best
        entries = [
            {"feature_columns": ["a"], "metrics": {"test_auc": 0.9}},
        ]
        assert find_same_schema_best(entries, ["a", "b"]) is None

    def test_picks_highest_test_auc_among_matching_schema(self):
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [
            {"feature_columns": cols, "metrics": {"test_auc": 0.55}},
            {"feature_columns": cols, "metrics": {"test_auc": 0.60}},
            {"feature_columns": ["a"], "metrics": {"test_auc": 0.99}},  # different schema, ignored
        ]
        result = find_same_schema_best(entries, cols)
        assert result == {"test_auc": 0.60}

    def test_ignores_entries_missing_test_auc(self):
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [{"feature_columns": cols, "metrics": {"test_accuracy": 0.7}}]
        assert find_same_schema_best(entries, cols) is None


class TestAssertPromotionReady:
    def test_noop_when_best_is_none(self):
        from services.model_promotion import assert_promotion_ready
        assert_promotion_ready({"test_auc": 0.1}, None, "XGB")  # must not raise

    def test_raises_on_regression_beyond_tolerance(self):
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.50, "test_auc": 0.50}
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        with pytest.raises(ValueError, match="regressed"):
            assert_promotion_ready(new_metrics, best_metrics, "XGB")

    def test_allows_regression_within_tolerance(self):
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.591, "test_auc": 0.591}
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        assert_promotion_ready(new_metrics, best_metrics, "XGB")  # 0.9pp < 2pp tolerance, must not raise

    def test_skips_metric_missing_from_new_metrics(self):
        """NN's _eval_metrics never has test_auc -- the gate must silently
        skip that one metric rather than raising over a missing key."""
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.591}  # no test_auc key at all
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        assert_promotion_ready(new_metrics, best_metrics, "NN")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_model_promotion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.model_promotion'`

- [ ] **Step 3: Write the implementation**

```python
# services/model_promotion.py
"""services/model_promotion.py -- Training-time promotion gate.

Stops a newly-trained model from being written as the resolved
"latest"/"best" version when it regresses vs. the best model sharing its
EXACT feature schema. Comparing across a feature-schema change (e.g.
spread_line removed in the "ML De-Vegas Pass") is comparing against
leakage, not a quality bar -- see
docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md
"Folded-in designs" > "Model quality gating" for the incident (an invalid
XGB v9-vs-v3 comparison) this exists to prevent.
"""
from typing import Optional

PROMOTION_GATES: dict[str, float] = {
    # metric_name: max allowed regression vs. same-schema best
    # (negative tolerance -- new must not be more than this much worse)
    "test_accuracy": -0.02,
    "test_auc": -0.02,
}


def find_same_schema_best(entries: list[dict], new_feature_columns: list[str]) -> Optional[dict]:
    """Return the metrics dict of the highest-test_auc entry that shares
    new_feature_columns exactly, or None if no entry shares this schema
    (the first model of its generation -- nothing to gate against yet)."""
    candidates = [
        e for e in entries
        if e.get("feature_columns") == new_feature_columns
        and e.get("metrics", {}).get("test_auc") is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["metrics"]["test_auc"])["metrics"]


def assert_promotion_ready(new_metrics: dict, best_metrics: Optional[dict], model_name: str) -> None:
    """Raise ValueError if new_metrics regresses vs. best_metrics beyond
    PROMOTION_GATES' tolerance on any gated metric present in both.

    No-ops if best_metrics is None (nothing comparable exists yet) or a
    gated metric is missing from either side -- e.g. NN's _eval_metrics
    never computes test_auc, so only test_accuracy gates it.
    """
    if best_metrics is None:
        return
    failures = {}
    for metric, tolerance in PROMOTION_GATES.items():
        new_v = new_metrics.get(metric)
        best_v = best_metrics.get(metric)
        if new_v is None or best_v is None:
            continue
        if new_v < best_v + tolerance:
            failures[metric] = {"new": new_v, "best": best_v, "tolerance": tolerance}
    if failures:
        raise ValueError(
            f"{model_name}: new model regressed vs. same-schema best on held-out "
            f"test set: {failures}. Not promoting -- investigate before overriding "
            f"with --force-promote."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_model_promotion.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add services/model_promotion.py tests/test_model_promotion.py
git commit -m "$(cat <<'EOF'
feat: add schema-scoped training-time promotion gate

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 3: Wire the gate into `XGBPredictionService.save_versioned()`

**Files:**
- Modify: `services/xgb_prediction_service.py:236-284` (`save_versioned`)
- Modify: `scripts/train_xgb_model.py` (add `--force-promote`)
- Test: `tests/test_promotion_gate_xgb.py`

**Interfaces:**
- Consumes: `services.model_promotion.find_same_schema_best`,
  `assert_promotion_ready` (Task 2).
- Produces: `XGBPredictionService.save_versioned(version=None,
  training_params=None, force_promote: bool = False) -> str` (new
  `force_promote` param; raises `ValueError` on a gate failure unless set).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promotion_gate_xgb.py
import json
import pickle
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.xgb_prediction_service import XGBPredictionService
    svc = XGBPredictionService()
    svc._is_trained = True
    svc._eval_metrics = metrics
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    return svc


class TestXGBPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v4")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            version = svc.save_versioned(version="v4", force_promote=True)
        assert version == "v4"

    def test_first_model_of_new_schema_never_blocked(self, tmp_path, monkeypatch):
        """A different feature_columns list means nothing to gate against --
        must save without raising."""
        registry = {
            "v3": {
                "model_path": "models/xgb_v3.json",
                "feature_columns": ["spread_line", "elo_diff"],  # old, leaky schema
                "metrics": {"test_accuracy": 0.90, "test_auc": 0.90},
            },
            "best_by": {"test_accuracy": "v3"},
        }
        registry_path = tmp_path / "xgb_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.xgb_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.xgb_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch.object(svc.model, "save_model"), patch("pickle.dump"):
            version = svc.save_versioned(version="v4")
        assert version == "v4"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_promotion_gate_xgb.py -v`
Expected: FAIL — `save_versioned()` has no `force_promote` param yet and never raises on regression.

- [ ] **Step 3: Implement — modify `services/xgb_prediction_service.py`**

Replace the start of `save_versioned` (currently `services/xgb_prediction_service.py:236-245`):

```python
    def save_versioned(self, version: Optional[str] = None,
                       training_params: Optional[dict] = None,
                       force_promote: bool = False) -> str:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")

        registry = {}
        if REGISTRY_PATH.exists():
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)

        from services.model_promotion import find_same_schema_best, assert_promotion_ready
        entries = [v for v in registry.values() if isinstance(v, dict) and "feature_columns" in v]
        best_metrics = find_same_schema_best(entries, FEATURE_COLUMNS)
        try:
            assert_promotion_ready(self._eval_metrics or {}, best_metrics, "XGB")
        except ValueError:
            if not force_promote:
                raise
            logger.warning("XGB promotion gate failed but --force-promote set; saving anyway.")
```

(The rest of the method — version auto-increment, model/scaler save, registry
write, `best_by` update — is unchanged.)

Modify `scripts/train_xgb_model.py`: add after the existing `--version`
argument (around line 33-34):

```python
    parser.add_argument("--force-promote", action="store_true",
                        help="Save even if this run regresses vs. the same-schema "
                             "best on held-out test metrics (promotion gate override).")
```

And update the `save_versioned` call (around line 92-96):

```python
    version = svc.save_versioned(
        version=args.version,
        training_params={"min_season": args.min_season, "max_season": args.max_season,
                         "train_samples": int((feature_table["season"] < feature_table["season"].max()).sum())},
        force_promote=args.force_promote,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_promotion_gate_xgb.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing XGB test suite to check for regressions**

Run: `pytest tests/test_xgb_prediction.py tests/test_loaded_version.py::TestXGBLoadedVersion -v`
Expected: PASS (all previously-passing tests still pass — `save_versioned` is
untouched by these tests, only `train`/`predict_game`/`load_model` are
exercised there)

- [ ] **Step 6: Commit**

```bash
git add services/xgb_prediction_service.py scripts/train_xgb_model.py tests/test_promotion_gate_xgb.py
git commit -m "$(cat <<'EOF'
feat: wire schema-scoped promotion gate into XGB training

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 4: Wire the gate into `LRPredictionService.save_versioned()`

**Files:**
- Modify: `services/lr_prediction_service.py:212-258` (`save_versioned`)
- Modify: `scripts/train_lr_model.py` (add `--force-promote`)
- Test: `tests/test_promotion_gate_lr.py`

**Interfaces:**
- Consumes: `services.model_promotion.find_same_schema_best`,
  `assert_promotion_ready` (Task 2).
- Produces: `LRPredictionService.save_versioned(version=None,
  training_params=None, force_promote: bool = False) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promotion_gate_lr.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.lr_prediction_service import LRPredictionService
    svc = LRPredictionService()
    svc._is_trained = True
    svc._eval_metrics = metrics
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    return svc


class TestLRPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v1": {
                "model_path": "models/lr_v1.pkl",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v1"},
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.lr_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch("pickle.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v2")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "v1": {
                "model_path": "models/lr_v1.pkl",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            },
            "best_by": {"test_accuracy": "v1"},
        }
        registry_path = tmp_path / "lr_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.lr_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.lr_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50, "test_auc": 0.50})
        with patch("pickle.dump"):
            version = svc.save_versioned(version="v2", force_promote=True)
        assert version == "v2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_promotion_gate_lr.py -v`
Expected: FAIL — no `force_promote` param, never raises.

- [ ] **Step 3: Implement — modify `services/lr_prediction_service.py`**

Replace the start of `save_versioned` (currently `services/lr_prediction_service.py:212-221`):

```python
    def save_versioned(self, version: Optional[str] = None,
                       training_params: Optional[dict] = None,
                       force_promote: bool = False) -> str:
        if not self._is_trained:
            raise RuntimeError("Model not trained.")

        registry = {}
        if REGISTRY_PATH.exists():
            with open(REGISTRY_PATH) as f:
                registry = json.load(f)

        from services.model_promotion import find_same_schema_best, assert_promotion_ready
        entries = [v for v in registry.values() if isinstance(v, dict) and "feature_columns" in v]
        best_metrics = find_same_schema_best(entries, FEATURE_COLUMNS)
        try:
            assert_promotion_ready(self._eval_metrics or {}, best_metrics, "LR")
        except ValueError:
            if not force_promote:
                raise
            logger.warning("LR promotion gate failed but --force-promote set; saving anyway.")
```

(Rest of the method unchanged.)

Modify `scripts/train_lr_model.py`: add the same `--force-promote` argparse
flag (mirroring Task 3's addition, after the existing `--version` argument
around line 33-34) and thread it into the `save_versioned` call (around line
94-98):

```python
    version = svc.save_versioned(
        version=args.version,
        training_params={"min_season": args.min_season, "max_season": args.max_season,
                         "train_samples": int((feature_table["season"] < feature_table["season"].max()).sum())},
        force_promote=args.force_promote,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_promotion_gate_lr.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full existing LR test suite to check for regressions**

Run: `pytest tests/test_lr_prediction.py tests/test_loaded_version.py::TestLRLoadedVersion -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/lr_prediction_service.py scripts/train_lr_model.py tests/test_promotion_gate_lr.py
git commit -m "$(cat <<'EOF'
feat: wire schema-scoped promotion gate into LR training

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 5: Wire the gate into `NNPredictionService.save_versioned()` (+ add missing `feature_columns`)

**Files:**
- Modify: `services/nn_prediction_service.py:614-681` (`save_versioned`)
- Modify: `scripts/train_nn_model.py` (add `--force-promote`)
- Test: `tests/test_promotion_gate_nn.py`

**Interfaces:**
- Consumes: `services.model_promotion.find_same_schema_best`,
  `assert_promotion_ready` (Task 2).
- Produces: `NNPredictionService.save_versioned(version=None,
  training_params=None, force_promote: bool = False) -> str`. Every entry
  this method appends to `registry["models"]` now includes
  `"feature_columns": FEATURE_COLUMNS` (previously absent — the NN registry
  is the only one of the three that never stored this, which is why the
  gate has nothing to compare against for NN today without this fix).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promotion_gate_nn.py
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_service_with_metrics(metrics):
    from services.nn_prediction_service import NNPredictionService
    svc = NNPredictionService()
    svc.model = MagicMock()
    svc.scaler = MagicMock()
    svc._eval_metrics = metrics
    return svc


class TestNNPromotionGate:
    def test_blocks_regression_vs_same_schema_best(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "models": [{
                "version": "v13",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            }],
            "latest": "v13",
            "best_by": {},
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50})
        with patch("joblib.dump"):
            with pytest.raises(ValueError, match="regressed"):
                svc.save_versioned(version="v14")

    def test_force_promote_bypasses_gate(self, tmp_path, monkeypatch):
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry = {
            "models": [{
                "version": "v13",
                "feature_columns": FEATURE_COLUMNS,
                "metrics": {"test_accuracy": 0.60, "test_auc": 0.60},
            }],
            "latest": "v13",
            "best_by": {},
        }
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps(registry))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.50})
        with patch("joblib.dump"):
            version = svc.save_versioned(version="v14", force_promote=True)
        assert version == "v14"

    def test_new_entry_stores_feature_columns(self, tmp_path, monkeypatch):
        """Regression guard for the gap this task fixes: every NN registry
        entry must carry feature_columns so future gate checks can use it."""
        from services.nn_feature_engine import FEATURE_COLUMNS
        registry_path = tmp_path / "model_registry.json"
        registry_path.write_text(json.dumps({"models": [], "latest": None, "best_by": {}}))
        monkeypatch.setattr("services.nn_prediction_service.REGISTRY_PATH", registry_path)
        monkeypatch.setattr("services.nn_prediction_service.MODEL_DIR", tmp_path)

        svc = _make_service_with_metrics({"test_accuracy": 0.60})
        with patch("joblib.dump"):
            svc.save_versioned(version="v1")

        saved = json.loads(registry_path.read_text())
        assert saved["models"][0]["feature_columns"] == FEATURE_COLUMNS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_promotion_gate_nn.py -v`
Expected: FAIL — no `force_promote` param, no `feature_columns` in the saved entry.

- [ ] **Step 3: Implement — modify `services/nn_prediction_service.py`**

Change the `save_versioned` signature and add the gate check right after
`registry = self._load_registry()` (currently `services/nn_prediction_service.py:614-634`):

```python
    def save_versioned(
        self,
        version: Optional[str] = None,
        training_params: Optional[dict] = None,
        force_promote: bool = False,
    ) -> str:
        """..."""  # docstring unchanged
        if self.model is None:
            raise RuntimeError("No model to save.")

        registry = self._load_registry()

        from services.model_promotion import find_same_schema_best, assert_promotion_ready
        entries = registry.get("models", [])
        best_metrics = find_same_schema_best(entries, FEATURE_COLUMNS)
        try:
            assert_promotion_ready(self._eval_metrics or {}, best_metrics, "NN")
        except ValueError:
            if not force_promote:
                raise
            logger.warning("NN promotion gate failed but --force-promote set; saving anyway.")
```

Then add the missing `feature_columns` key to the entry dict built later in
the same method (currently `services/nn_prediction_service.py:653-658`):

```python
        entry = {
            "version": version,
            "path": f"models/nn_{version}.keras",
            "scaler_path": f"models/nn_{version}_scaler.pkl",
            "feature_columns": FEATURE_COLUMNS,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
```

(`FEATURE_COLUMNS` is already imported at the top of this file, per the
existing `from services.nn_feature_engine import FEATURE_COLUMNS, _normalize_team`.)

Modify `scripts/train_nn_model.py`: add the same `--force-promote` argparse
flag (after the existing `--version` argument, around line 45-48) and thread
it into the `save_versioned` call (around line 118-125):

```python
    saved_version = svc.save_versioned(
        version=args.version,
        training_params={
            "min_season": args.min_season,
            "max_season": args.max_season,
            "train_samples": len(feature_table[feature_table["season"] < 2025]),
        },
        force_promote=args.force_promote,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_promotion_gate_nn.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing NN test suite to check for regressions**

Run: `pytest tests/test_nn_prediction_service.py tests/test_loaded_version.py::TestNNLoadedVersion -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/nn_prediction_service.py scripts/train_nn_model.py tests/test_promotion_gate_nn.py
git commit -m "$(cat <<'EOF'
feat: wire schema-scoped promotion gate into NN training, stamp feature_columns

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 6: Stamp `feature_version` onto `prediction_features` docs

**Files:**
- Modify: `services/cache_service.py:281-313` (`write_prediction_features`)
- Test: `tests/test_cache_service.py` (extend `TestPredictionFeaturesCache`)

**Interfaces:**
- Consumes: `services.model_version.get_feature_version` (Task 1).
- Produces: `write_prediction_features(season, ensemble_version, games, *,
  use_local=None, feature_version=None)` — unchanged call sites still work;
  when `feature_version` is omitted, it's computed automatically. The
  persisted payload gains a `feature_version` key alongside the existing
  `season`/`ensemble_version`/`created_at`/`games`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache_service.py`, inside `class TestPredictionFeaturesCache`:

```python
    def test_write_stamps_feature_version_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)
        from unittest.mock import patch
        from services.cache_service import write_prediction_features, get_prediction_features

        with patch("services.model_version.get_feature_version", return_value="abc1234"):
            write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_KC_SF": {}})

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc["feature_version"] == "abc1234"

    def test_write_accepts_explicit_feature_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)
        from services.cache_service import write_prediction_features, get_prediction_features

        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_KC_SF": {}},
                                   feature_version="deadbeef")

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc["feature_version"] == "deadbeef"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_service.py -k feature_version -v`
Expected: FAIL — `KeyError: 'feature_version'`

- [ ] **Step 3: Implement — modify `services/cache_service.py`**

```python
def write_prediction_features(
    season: int,
    ensemble_version: str,
    games: dict,
    *,
    use_local: bool | None = None,
    feature_version: str | None = None,
) -> None:
    """Persist the prediction features doc (local JSON or Firestore).

    Args:
        season: NFL season year.
        ensemble_version: e.g. "nn_v10+xgb_v4+lr_v2".
        games: {game_key: per-game audit dict} from compute_feature_audit().
        use_local: Override the _USE_LOCAL env setting.  Pass True to force
                   local JSON, False to force Firestore.  None = auto.
        feature_version: git commit SHA identifying the feature-engine code
                   that produced `games`. Computed automatically (see
                   services.model_version.get_feature_version) if omitted.
    """
    if "/" in ensemble_version or "\\" in ensemble_version or ".." in ensemble_version:
        raise ValueError(f"Invalid ensemble_version: {ensemble_version!r}")

    if feature_version is None:
        from services.model_version import get_feature_version
        feature_version = get_feature_version()

    _local = _USE_LOCAL if use_local is None else use_local

    from datetime import datetime, timezone
    payload = {
        "season":           season,
        "ensemble_version": ensemble_version,
        "feature_version":  feature_version,
        "created_at":       datetime.now(timezone.utc).isoformat(),
        "games":            games,
    }
```

(The rest of the function is unchanged. Confirmed by reading
`services/cache_service.py:314-338`: the Firestore branch builds
`firestore_payload = {**{k: v for k, v in payload.items() if k != "games"},
"games_json": ..., "full_data_local_only": True}` — that spread already
carries every key of `payload` except `games` forward, so `feature_version`
reaches Firestore automatically once it's added to `payload` above. No
second edit needed for the Firestore branch.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_service.py -k feature_version -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full existing cache_service test suite to check for regressions**

Run: `pytest tests/test_cache_service.py -v`
Expected: PASS (all tests, including the pre-existing
`test_write_then_read_local` which asserts on `doc["ensemble_version"]` but
doesn't check for absence of other keys, so it still passes)

- [ ] **Step 6: Commit**

```bash
git add services/cache_service.py tests/test_cache_service.py
git commit -m "$(cat <<'EOF'
feat: stamp feature_version onto prediction_features docs

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 7: Stamp `ensemble_version`/`feature_version` onto `game_predictions` entries in `cache_builder.py`

**Files:**
- Modify: `scripts/cache_builder.py` (`build_year`, `_publish_game_probs`,
  `main`)
- Test: `tests/test_cache_builder_version_stamp.py`

**Interfaces:**
- Consumes: `services.model_version.get_feature_version`,
  `build_ensemble_version_string` (Task 1).
- Produces: every entry `cache_builder.py` writes into `game_predictions`
  now carries top-level `ensemble_version` (when models loaded successfully)
  and `feature_version` (always). Entries preserved unchanged from a prior
  run via `merge_thin_game_predictions()`'s existing-entry path are **not**
  touched — only freshly-computed entries are stamped, since they're stamped
  before the merge, not after.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache_builder_version_stamp.py
"""Only exercises the pure per-entry dict construction, not the full
build_year() pipeline (which needs live Firestore/model data) -- this
isolates the stamping logic itself."""
from unittest.mock import MagicMock, patch


class TestBuildYearPmapStamping:
    def test_pmap_entry_carries_ensemble_and_feature_version(self):
        """Reproduces the exact dict-construction shape at
        scripts/cache_builder.py's pmap-building loop (~line 355-374) to
        verify the two new keys land on a freshly-built entry."""
        model_version = "nn_v14+xgb_v9+lr_v7"
        feature_version = "abc1234"

        entry = {
            'pred_prob':     0.62,
            'pred_winner':   'KC',
            'pred_su_conf':  62.0,
            'pred_ats_pick': 'KC',
        }
        if model_version:
            entry['ensemble_version'] = model_version
        entry['feature_version'] = feature_version

        assert entry['ensemble_version'] == "nn_v14+xgb_v9+lr_v7"
        assert entry['feature_version'] == "abc1234"

    def test_pmap_entry_omits_ensemble_version_when_model_load_failed(self):
        model_version = None
        feature_version = "abc1234"

        entry = {'pred_prob': 0.5, 'pred_winner': 'KC'}
        if model_version:
            entry['ensemble_version'] = model_version
        entry['feature_version'] = feature_version

        assert "ensemble_version" not in entry
        assert entry["feature_version"] == "abc1234"


class TestPublishGameProbsStamping:
    def test_resimulate_entry_gets_stamped(self, monkeypatch):
        import scripts.cache_builder as cb

        fake_engine = MagicMock()
        fake_engine.svc.loaded_version = "v14"
        fake_engine.xgb_svc.loaded_version = "v9"
        fake_engine.lr_svc.loaded_version = "v7"

        games = MagicMock()
        games.__getitem__.return_value.astype.return_value.isin.return_value = [True]
        # Build a minimal DataFrame-like target instead of mocking pandas internals:
        import pandas as pd
        games_df = pd.DataFrame([{"game_id": "2026_02_CAR_ATL", "home_team": "ATL",
                                   "away_team": "CAR", "week": 2}])
        game_probs = {"W02_ATL_CAR": {"home_team": "ATL", "away_team": "CAR",
                                       "week": 2, "mean_prob": 0.55, "model_spread": 1.5}}

        with patch.object(cb, "_build_mc_entry") as mock_build_entry, \
             patch.object(cb, "get_game_predictions", return_value={}), \
             patch.object(cb, "merge_thin_game_predictions", side_effect=lambda e, f: {**e, **f}), \
             patch.object(cb, "write_game_predictions"):
            mock_build_entry.return_value = {"pred_prob": 0.55, "pred_winner": "ATL"}
            cb._publish_game_probs(
                ["2026_02_CAR_ATL"], games_df, 2026, game_probs, engine=fake_engine,
                ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="abc1234",
            )

        # pmap passed to merge_thin_game_predictions is the second positional/kw arg
        merged_call = cb.merge_thin_game_predictions.call_args
        fresh_pmap = merged_call.args[1] if merged_call.args else merged_call.kwargs["fresh"]
        entry = fresh_pmap["W02_ATL_CAR"]
        assert entry["ensemble_version"] == "nn_v14+xgb_v9+lr_v7"
        assert entry["feature_version"] == "abc1234"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder_version_stamp.py -v`
Expected: `TestBuildYearPmapStamping` tests pass immediately (pure dict
logic, no production code dependency yet — they document the expected shape).
`TestPublishGameProbsStamping::test_resimulate_entry_gets_stamped` FAILS with
`TypeError: _publish_game_probs() got an unexpected keyword argument
'ensemble_version'`.

- [ ] **Step 3: Implement — modify `scripts/cache_builder.py`**

Add the import near the top (alongside the other `services.*` imports,
around line 42-58):

```python
from services.model_version import get_feature_version, build_ensemble_version_string
```

Update `_publish_game_probs`'s signature and stamping (currently
`scripts/cache_builder.py:127-160`):

```python
def _publish_game_probs(game_ids: list, games: pd.DataFrame, year: int, game_probs: dict,
                        engine=None, ensemble_version: str = None,
                        feature_version: str = None) -> int:
    """..."""  # docstring unchanged
    target = games[games["game_id"].astype(str).isin(game_ids)].copy()
    if target.empty:
        print(f"[cache_builder] --resimulate: no matching rows for {game_ids}")
        return 0

    pmap = {}
    for _, row in target.iterrows():
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')
        if not (ht and at and wk is not None):
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        gp = game_probs.get(key)
        if not gp:
            continue
        entry = _build_mc_entry(engine, gp, year, row.get('spread_line'),
                                source="mc_simulation (resimulate)")
        if ensemble_version:
            entry['ensemble_version'] = ensemble_version
        if feature_version:
            entry['feature_version'] = feature_version
        pmap[key] = entry
```

Update the `--resimulate` branch in `main()` (currently
`scripts/cache_builder.py:584-593`) to compute and pass the two new
arguments:

```python
        engine = NNProjectionEngine()
        engine.initialize(year, espn_overrides=espn_overrides)

        yr_games = games[games["season"] == year].copy()
        completed_results = _build_completed_results(yr_games, year)
        sim = engine.simulate_season(yr_games, n_sims=RESIMULATE_N_SIMS, completed_results=completed_results)

        resim_ensemble_version = build_ensemble_version_string(engine.svc, engine.xgb_svc, engine.lr_svc)
        resim_feature_version = get_feature_version()
        n = _publish_game_probs(game_ids, games, year, sim.get("game_probs", {}),
                                engine=engine, ensemble_version=resim_ensemble_version,
                                feature_version=resim_feature_version)
        print(f"[cache_builder] --resimulate: published {n} prediction(s) "
              f"[{resim_ensemble_version}, feature={resim_feature_version}].")
```

Update `build_year`'s signature to accept `feature_version` (currently
`scripts/cache_builder.py:280-283`):

```python
def build_year(standings, games, players, draft_order, draft_results,
               draft_order_rules, year: int, current_year: int,
               all_games=None, force: bool = False, pred_lookup: dict = None,
               model_version: str = None, feature_version: str = None):
```

Stamp the pmap entries inside `build_year` (currently
`scripts/cache_builder.py:355-374`, inside the `for _, r in
schedule_df[pc].dropna(subset=['pred_winner']).iterrows():` loop, right
before `pmap[key] = entry`):

```python
                        exp = r.get('explanation')
                        if isinstance(exp, dict):
                            entry['explanation'] = exp
                        if model_version:
                            entry['ensemble_version'] = model_version
                        if feature_version:
                            entry['feature_version'] = feature_version
                        pmap[f"W{int(wk):02d}_{ht}_{at}"] = entry
```

Guarding `feature_version` the same way as `model_version` (only set when
truthy) matters for backward compatibility: `tests/test_cache_builder.py`
has existing `build_year(...)` calls that omit both `model_version` and the
new `feature_version` param entirely (e.g.
`TestBuildYearWritesExplanation::test_explanation_included_in_daily_pmap_when_present`,
`test_daily_write_without_an_explanation_keeps_the_stored_richer_one`) — an
unconditional `entry['feature_version'] = feature_version` would write a
literal `None` into every entry from those tests. None of those tests
currently assert on `feature_version`'s absence, so it wouldn't fail them
today, but it violates this file's own established "omit rather than write
a null placeholder" convention (see the `model_spread`/`edge_vs_vegas`
NaN-omission a few lines above in the same loop). Guarding keeps the
convention intact and costs nothing.

Update `main()`'s daily-build path to compute and pass `feature_version`
(currently `scripts/cache_builder.py:610-636`):

```python
    try:
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        model_version = build_ensemble_version_string(nn_svc, xgb_svc, lr_svc)

        min_ft = min(years_to_build)
        max_ft = max(years_to_build)
        print(f"[cache_builder] Building feature table ({min_ft}-{max_ft})...")
        ft = build_master_feature_table(min_season=min_ft, max_season=max_ft)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)
        print(f"[cache_builder] {len(pred_lookup)} game predictions pre-computed.")
    except Exception as e:
        print(f"[cache_builder] WARNING: ML models unavailable ({e}). Predictions will be skipped.")
        pred_lookup = {}
        model_version = None

    feature_version = get_feature_version()

    for year in years_to_build:
        # Filter data to just this year to avoid large cross-season merges
        yr_standings = standings[standings['season'] == year].copy() if not standings.empty else standings
        yr_games = games[games['season'] == year].copy() if not games.empty else games
        build_year(yr_standings, yr_games, players, draft_order, draft_results,
                   draft_order_rules, year, current_year, all_games=games,
                   force=args.force, pred_lookup=pred_lookup,
                   model_version=model_version, feature_version=feature_version)
```

Note: `model_version = build_ensemble_version_string(nn_svc, xgb_svc,
lr_svc)` replaces the existing inline f-string at line 616 — same output,
now sourced from the shared Task 1 helper instead of a third duplicate of
that pattern.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder_version_stamp.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing cache_builder test suite to check for regressions**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS. This file has many direct `build_year(...)` calls (e.g.
`TestBuildYearWritesExplanation`, around lines 976 and 1013) and
`_publish_game_probs(...)` calls (around lines 590-642) that omit the new
`feature_version`/`ensemble_version` params entirely — since both default to
`None` and are only stamped onto entries when truthy (Step 3's guard
pattern), none of these are signature-broken, and none of them assert
exact-equality on the full entry dict (they check specific keys like
`explanation`/`pred_prob`), so the new keys landing on other entries doesn't
touch their assertions either.

- [ ] **Step 6: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder_version_stamp.py
git commit -m "$(cat <<'EOF'
feat: stamp ensemble_version/feature_version on cache_builder's game_predictions writes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 8: Stamp `ensemble_version`/`feature_version` onto `game_predictions` entries in `backfill_schedule_predictions.py`

**Files:**
- Modify: `scripts/backfill_schedule_predictions.py` (`_build_predictions_map`, `main`)
- Test: `tests/test_backfill_version_stamp.py`

**Interfaces:**
- Consumes: `services.model_version.get_feature_version`,
  `build_ensemble_version_string` (Task 1).
- Produces: `_build_predictions_map(year, ft_lookup, schedule_df, games_df,
  force, ensemble_version=None, feature_version=None) -> dict` (two new
  **optional** params, keyword-or-positional, both default `None`).
  `tests/test_backfill_predictions_map.py` has 6 existing call sites that
  pass only `(year, ft_lookup, schedule_df, games_df, force=True)` — making
  the two new params optional (mirroring the auto-default pattern already
  used in Task 6's `write_prediction_features`) keeps every one of those
  passing unmodified, rather than churning 6 unrelated call sites for an
  orthogonal concern (those tests cover simulation-skip logic and
  explanation-field wiring, not versioning). When `feature_version` is
  omitted it's computed automatically (same auto-default as Task 6); when
  `ensemble_version` is omitted, that key is simply not stamped (mirrors the
  existing `if model_version:` guard pattern in `cache_builder.py`).
  Freshly-computed entries (feature-table + MC-simulated) get stamped;
  entries preserved from a prior locked run via the `if not force:` branch
  are left untouched.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backfill_version_stamp.py
import pandas as pd
from unittest.mock import patch


class TestBuildPredictionsMapStamping:
    def test_fresh_entries_get_stamped(self):
        from scripts.backfill_schedule_predictions import _build_predictions_map

        ft_lookup = {
            (2025, 1, "KC", "BAL"): {
                "pred_prob": 0.6, "pred_winner": "KC", "pred_su_conf": 60.0,
                "pred_ats_pick": "KC", "model_spread": 2.0, "edge_vs_vegas": 1.0,
            }
        }
        result = _build_predictions_map(
            2025, ft_lookup, pd.DataFrame(), pd.DataFrame(), force=True,
            ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="abc1234",
        )
        entry = result["W01_KC_BAL"]
        assert entry["ensemble_version"] == "nn_v14+xgb_v9+lr_v7"
        assert entry["feature_version"] == "abc1234"

    def test_preserved_locked_entries_are_not_restamped(self):
        """A --force=False run must carry forward an already-locked entry
        from a prior run untouched -- it must NOT be overwritten with this
        run's version, since it wasn't produced by this run."""
        from scripts.backfill_schedule_predictions import _build_predictions_map

        old_entry = {
            "pred_prob": 0.4, "pred_winner": "BAL", "locked": True,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2", "feature_version": "oldsha0000",
        }
        with patch(
            "scripts.backfill_schedule_predictions.get_game_predictions",
            return_value={"W02_KC_BAL": old_entry},
        ):
            result = _build_predictions_map(
                2025, {}, pd.DataFrame(), pd.DataFrame(), force=False,
                ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="newsha1111",
            )
        assert result["W02_KC_BAL"]["ensemble_version"] == "nn_v10+xgb_v4+lr_v2"
        assert result["W02_KC_BAL"]["feature_version"] == "oldsha0000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backfill_version_stamp.py -v`
Expected: FAIL — `test_fresh_entries_get_stamped` fails with `KeyError:
'ensemble_version'` (the function runs but doesn't stamp yet, since the
params don't exist); `test_preserved_locked_entries_are_not_restamped`
fails with `TypeError: _build_predictions_map() got an unexpected keyword
argument 'ensemble_version'`.

- [ ] **Step 3: Implement — modify `scripts/backfill_schedule_predictions.py`**

Update `_build_predictions_map`'s signature and add stamping right before
the existing-entry preservation block (currently lines 61-162):

```python
def _build_predictions_map(year: int, ft_lookup: dict,
                            schedule_df: pd.DataFrame,
                            games_df: pd.DataFrame,
                            force: bool,
                            ensemble_version: str = None,
                            feature_version: str = None) -> dict:
    """Assemble the final predictions map for one season.
    ...
    """  # docstring body unchanged
    # ... (unchanged body through the sim loop, lines 74-154) ...

    if feature_version is None:
        from services.model_version import get_feature_version
        feature_version = get_feature_version()

    for entry in result.values():
        if ensemble_version:
            entry["ensemble_version"] = ensemble_version
        entry["feature_version"] = feature_version

    if not force:
        existing = get_game_predictions(year)
        for k, v in existing.items():
            if v.get("locked") and k not in played_keys:
                result[k] = v

    return result
```

Both new params default to `None` — `ensemble_version` is only stamped when
truthy (mirrors `cache_builder.py`'s existing `if model_version:` pattern
from Task 7), and `feature_version` auto-computes via
`get_feature_version()` when omitted (mirrors Task 6's
`write_prediction_features` default), so every one of
`tests/test_backfill_predictions_map.py`'s 6 existing calls (which pass
neither) keeps working unmodified.

The stamping block must be inserted **after** `result` is fully built from
`played_keys` and the MC-simulation loop (i.e. right after the existing `if
needs_simulation:` block ends, which is currently the code immediately
before line 156's `if not force:`), and **before** that `if not force:`
block — so only freshly-computed entries are stamped, never an entry pulled
forward unchanged from a previous run.

Update `main()`'s ensemble-version construction (currently lines 301-303) to
use the shared Task 1 helper and add a `feature_version`:

```python
    from services.model_version import get_feature_version, build_ensemble_version_string
    ensemble_version = build_ensemble_version_string(nn_svc, xgb_svc, lr_svc)
    feature_version = get_feature_version()
    if write_features:
        print(f"\n  Ensemble version for audit: {ensemble_version}")
```

Update the call site (currently line 318):

```python
        predictions_map = _build_predictions_map(
            year, ft_lookup, yr_schedule, all_games, args.force,
            ensemble_version=ensemble_version, feature_version=feature_version,
        )
```

(Move the `from services.model_version import ...` line up to the module's
top-level imports rather than inline, matching this file's existing import
style — place it alongside the other `services.*` imports near the top of
the file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_version_stamp.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full existing backfill-adjacent test suite to check for regressions**

Run: `pytest tests/test_backfill_predictions_map.py tests/test_backfill_schedule_predictions.py tests/test_backfill_features_flag.py -v`
Expected: PASS (all 6 pre-existing `_build_predictions_map(...)` calls in
`tests/test_backfill_predictions_map.py` pass only `(year, ft_lookup,
schedule_df, games_df, force=True)` with no `ensemble_version`/
`feature_version` — since both new params default to `None`/auto-compute,
these calls are unaffected, per Step 3's design)

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_schedule_predictions.py tests/test_backfill_version_stamp.py
git commit -m "$(cat <<'EOF'
feat: stamp ensemble_version/feature_version on backfill's game_predictions writes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Task 9: Bake `GIT_SHA` into the two Cloud Run Job Docker images at build time

**Files:**
- Modify: `Dockerfile.sync`
- Modify: `Dockerfile.predict`
- Modify: `cloudbuild-sync.yaml`
- Modify: `cloudbuild-predict.yaml`
- Modify: `deploy/deploy.ps1:112-127`

**Interfaces:**
- Consumes: nothing from earlier tasks directly — this is what makes
  `services.model_version.get_feature_version()`'s `GIT_SHA` env-var branch
  (Task 1) actually populated in production. No automated test — this is
  infra wiring verified by a manual build check (Step 5 below).

- [ ] **Step 1: Modify `Dockerfile.sync`**

Add a build arg and env var right after `WORKDIR /app`:

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# Baked in at build time from the real checkout that ran `gcloud builds
# submit` (see deploy/deploy.ps1) -- .dockerignore excludes .git/, so
# `git rev-parse` cannot run inside this container at runtime.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV USE_LOCAL_DATA False
```

- [ ] **Step 2: Modify `Dockerfile.predict`** (same pattern)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Baked in at build time from the real checkout that ran `gcloud builds
# submit` (see deploy/deploy.ps1) -- .dockerignore excludes .git/, so
# `git rev-parse` cannot run inside this container at runtime.
ARG GIT_SHA=unknown
ENV GIT_SHA=$GIT_SHA

COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-ml.txt

COPY . .

ENV USE_LOCAL_DATA False
```

- [ ] **Step 3: Modify `cloudbuild-sync.yaml`**

```yaml
substitutions:
  _GIT_SHA: 'unknown'
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', 'Dockerfile.sync', '--build-arg', 'GIT_SHA=$_GIT_SHA',
           '-t', 'us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-sync:latest', '.']
images:
  - 'us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-sync:latest'
```

- [ ] **Step 4: Modify `cloudbuild-predict.yaml`**

```yaml
substitutions:
  _GIT_SHA: 'unknown'
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-f', 'Dockerfile.predict', '--build-arg', 'GIT_SHA=$_GIT_SHA',
           '-t', 'us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-predict:latest', '.']
    timeout: 1200s
images:
  - 'us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-predict:latest'
timeout: 1500s
```

- [ ] **Step 5: Modify `deploy/deploy.ps1`**

Replace lines 112-127:

```powershell
$SYNC_IMAGE = "us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-sync:latest"
$PREDICT_IMAGE = "us-east1-docker.pkg.dev/$PROJECT_ID/winspool/winspool-predict:latest"

# Stamped into both images as GIT_SHA (see services/model_version.py) --
# .dockerignore excludes .git/, so this must be captured here, from the
# real checkout, and passed in as a build substitution rather than read
# inside the container at runtime.
$gitSha = (git rev-parse HEAD).Trim()
Write-Host "[BUILD] Stamping images with GIT_SHA=$gitSha" -ForegroundColor Cyan

Write-Host "[BUILD] Building winspool-sync image..." -ForegroundColor Cyan
gcloud builds submit --config=cloudbuild-sync.yaml --substitutions=_GIT_SHA=$gitSha --project=$PROJECT_ID .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] winspool-sync image build failed. Scheduled jobs NOT updated." -ForegroundColor Red
    exit 1
}

Write-Host "[BUILD] Building winspool-predict image (this installs TensorFlow -- can take several minutes)..." -ForegroundColor Cyan
gcloud builds submit --config=cloudbuild-predict.yaml --substitutions=_GIT_SHA=$gitSha --project=$PROJECT_ID .
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] winspool-predict image build failed. Scheduled jobs NOT updated." -ForegroundColor Red
    exit 1
}
```

- [ ] **Step 6: Verify locally (manual, no automated test)**

Run:
```bash
docker build -f Dockerfile.sync --build-arg GIT_SHA=$(git rev-parse HEAD) -t winspool-sync-test .
docker run --rm winspool-sync-test python -c "import os; print(os.environ.get('GIT_SHA'))"
```
Expected: prints the current `git rev-parse HEAD` output (a 40-char hex
SHA), confirming the build arg → env var chain works end to end. Repeat for
`Dockerfile.predict` if time permits (slower build, installs TensorFlow).

- [ ] **Step 7: Commit**

```bash
git add Dockerfile.sync Dockerfile.predict cloudbuild-sync.yaml cloudbuild-predict.yaml deploy/deploy.ps1
git commit -m "$(cat <<'EOF'
feat: bake GIT_SHA into Cloud Run Job images at build time

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Nras3CFBWYcMugMdD3Kg9S
EOF
)"
```

---

## Self-Review Notes (for the executor to re-verify, not to skip)

- Every spec requirement has a task: SHA-based stamp (Tasks 1, 6, 7, 8, 9),
  schema-scoped gate (Tasks 2-5), stamp on both `prediction_features` (Task
  6) and `game_predictions` (Tasks 7-8), forward-only/no backfill (no task
  touches historical records), fail-loud gate with an explicit override
  (Tasks 3-5's `--force-promote`).
- The NN registry's missing `feature_columns` (a real gap found during
  research, not explicitly named in the spec) is fixed in Task 5 — without
  it, the gate would never find a same-schema match for NN and would always
  no-op, silently defeating the point of Task 5 for that one model.
- The "don't re-stamp preserved/existing entries" constraint is enforced by
  ordering: Task 8 stamps `result` before the `if not force:` merge-in of
  old locked entries; Task 7 stamps `pmap`/`entry` dicts before they reach
  `merge_thin_game_predictions()`, never after.
- Docker/Cloud Build changes (Task 9) have no unit test by nature of being
  infra config — verified by a manual `docker build` + `docker run` check
  instead, called out explicitly rather than skipped.
