# Dynamic Monte Carlo Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the compressed preseason win-total distribution (currently 7–10 wins, target ~3–15) and add week-by-week Elo+EPA state updates inside the Monte Carlo simulation so win momentum propagates through the season.

**Architecture:** Add `_batch_predict()`, `_build_initial_state()`, `_precompute_static_features()`, `_vectorized_elo_update()`, `_vectorized_epa_update()`, and `simulate_season()` to `NNProjectionEngine`. Then slim down `predict_season.py` and `backfill_schedule_predictions.py` to call the engine rather than doing their own profile-building and MC logic.

**Tech Stack:** Python, NumPy, TensorFlow/Keras (NN), XGBoost, scikit-learn (LR), pytest

---

## File Map

| File | Change |
|---|---|
| `services/constants.py` | Add `MC_MARGIN_STD`, `MC_EPA_SCALE`, `MC_EPA_RUSH_WEIGHT` |
| `services/nn_projection_engine.py` | Add 6 new methods; keep existing `game_win_probability()` |
| `scripts/predict_season.py` | Remove `_build_team_profiles()`, `_compute_game_probs()`, `_run_monte_carlo()`; call `engine.simulate_season()` |
| `scripts/backfill_schedule_predictions.py` | Remove `_profile_predictions_for_year()`; update `_build_predictions_map()` to take `games_df` and call `engine.simulate_season()` |
| `tests/test_simulate_season.py` | New test file |

---

## Task 1: Add MC Constants

**Files:**
- Modify: `services/constants.py`

- [ ] **Step 1: Add constants after the existing prediction constants**

Open `services/constants.py`. After the `SPREAD_TO_PROB_SCALE` line, add:

```python
# Monte Carlo simulation — game margin sampling and state update tuning.
# MC_MARGIN_STD: std dev of NFL game margin distribution (~13 points real-world).
# MC_EPA_SCALE: EPA nudge per point of simulated margin (tune if spread is too narrow/wide).
# MC_EPA_RUSH_WEIGHT: rush EPA updates at half the weight of passing EPA.
MC_MARGIN_STD      = 13.0
MC_EPA_SCALE       = 0.004
MC_EPA_RUSH_WEIGHT = 0.5
```

- [ ] **Step 2: Write a quick sanity test**

Add to `tests/test_simulate_season.py` (create this file):

```python
"""tests/test_simulate_season.py -- Tests for NNProjectionEngine.simulate_season()."""

import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def test_mc_constants_exist():
    from services.constants import MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT
    assert MC_MARGIN_STD == 13.0
    assert MC_EPA_SCALE == 0.004
    assert MC_EPA_RUSH_WEIGHT == 0.5
```

- [ ] **Step 3: Run test**

```
pytest tests/test_simulate_season.py::test_mc_constants_exist -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```
git add services/constants.py tests/test_simulate_season.py
git commit -m "feat: add MC simulation constants (MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT)"
```

---

## Task 2: Add `_batch_predict()` to `NNProjectionEngine`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_simulate_season.py`

This is the batch inference seam — runs NN+XGB+LR on a `(N, 26)` feature matrix at once. Tests mock it rather than loading real models.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_simulate_season.py`:

```python
# ── Fixtures ──────────────────────────────────────────────────────────────────

from services.nn_feature_engine import FEATURE_COLUMNS as NN_FEATURE_COLUMNS


@pytest.fixture
def mock_engine():
    """NNProjectionEngine with mocked model services and synthetic 2-team profiles."""
    with patch("services.nn_projection_engine.NNPredictionService"), \
         patch("services.nn_projection_engine.XGBPredictionService"), \
         patch("services.nn_projection_engine.LRPredictionService"):
        from services.nn_projection_engine import NNProjectionEngine
        engine = NNProjectionEngine()

    # Override profiles: STRONG (Elo 1600, good EPA) vs WEAK (Elo 1400, bad EPA)
    engine._team_profiles = pd.DataFrame([
        {
            "team": "STRONG", "elo_pre": 1600.0,
            "off_pass_epa_roll": 0.15,  "off_rush_epa_roll": 0.05,
            "def_pass_epa_roll": 0.10,  "def_rush_epa_roll": 0.03,
            "off_early_roll": 0.10,     "def_early_roll": 0.08,
            "margin_roll": 8.0,         "trench_score": 0.3,
            "qb_pressure_roll": 0.2,    "def_pressures_roll": 0.25,
            "market_implied_team_total": 24.0,
            **{c: 0.0 for c in NN_FEATURE_COLUMNS},
        },
        {
            "team": "WEAK", "elo_pre": 1400.0,
            "off_pass_epa_roll": -0.10, "off_rush_epa_roll": -0.05,
            "def_pass_epa_roll": -0.08, "def_rush_epa_roll": -0.03,
            "off_early_roll": -0.05,    "def_early_roll": -0.04,
            "margin_roll": -6.0,        "trench_score": -0.3,
            "qb_pressure_roll": -0.2,   "def_pressures_roll": -0.15,
            "market_implied_team_total": 20.0,
            **{c: 0.0 for c in NN_FEATURE_COLUMNS},
        },
    ])
    engine._preseason_roster = {}
    engine._preseason_norm = None
    return engine


# ── _batch_predict tests ───────────────────────────────────────────────────────

class TestBatchPredict:
    def test_returns_correct_shape(self, mock_engine):
        # _batch_predict doesn't exist yet — this should fail with AttributeError
        X = np.random.rand(50, len(NN_FEATURE_COLUMNS)).astype(np.float32)
        # Wire mocked scalers and models to produce fixed values
        mock_engine.svc.scaler = MagicMock()
        mock_engine.svc.scaler.transform = lambda x: x
        mock_engine.svc.model = MagicMock()
        mock_engine.svc.model.predict = MagicMock(
            return_value=np.full((50, 1), 0.6, dtype=np.float32)
        )
        mock_engine.xgb_svc.scaler = MagicMock()
        mock_engine.xgb_svc.scaler.transform = lambda x: x
        mock_engine.xgb_svc.model = MagicMock()
        mock_engine.xgb_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(50, 0.4), np.full(50, 0.6)])
        )
        mock_engine.lr_svc.scaler = MagicMock()
        mock_engine.lr_svc.scaler.transform = lambda x: x
        mock_engine.lr_svc.model = MagicMock()
        mock_engine.lr_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(50, 0.4), np.full(50, 0.6)])
        )

        result = mock_engine._batch_predict(X)
        assert result.shape == (50,)
        assert np.all(result >= 0.02) and np.all(result <= 0.98)

    def test_blends_models_correctly(self, mock_engine):
        X = np.ones((10, len(NN_FEATURE_COLUMNS)), dtype=np.float32)
        mock_engine.svc.scaler.transform = lambda x: x
        mock_engine.svc.model.predict = MagicMock(return_value=np.full((10, 1), 0.7))
        mock_engine.xgb_svc.scaler.transform = lambda x: x
        mock_engine.xgb_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(10, 0.3), np.full(10, 0.7)])
        )
        mock_engine.lr_svc.scaler.transform = lambda x: x
        mock_engine.lr_svc.model.predict_proba = MagicMock(
            return_value=np.column_stack([np.full(10, 0.3), np.full(10, 0.7)])
        )
        result = mock_engine._batch_predict(X)
        # All models agree on 0.7 → blended should be 0.7
        assert np.allclose(result, 0.7, atol=0.01)
```

- [ ] **Step 2: Run test to see it fail**

```
pytest tests/test_simulate_season.py::TestBatchPredict -v
```
Expected: FAIL with `AttributeError: 'NNProjectionEngine' object has no attribute '_batch_predict'`

- [ ] **Step 3: Implement `_batch_predict()`**

In `services/nn_projection_engine.py`, add this method to the `NNProjectionEngine` class (after `game_win_probability()`):

```python
def _batch_predict(self, X: np.ndarray) -> np.ndarray:
    """Run the NN+XGB+LR ensemble on a feature batch.

    Args:
        X: Raw (unscaled) feature matrix of shape (N, n_features).

    Returns:
        Blended win probabilities of shape (N,), clipped to [PROB_CLIP_MIN, PROB_CLIP_MAX].
    """
    from services.constants import PROB_CLIP_MIN, PROB_CLIP_MAX
    X_f = X.astype(np.float32)
    nn_p  = self.svc.model.predict(self.svc.scaler.transform(X_f), verbose=0).flatten()
    xgb_p = self.xgb_svc.model.predict_proba(self.xgb_svc.scaler.transform(X_f))[:, 1]
    lr_p  = self.lr_svc.model.predict_proba(self.lr_svc.scaler.transform(X_f))[:, 1]
    blended = NN_WEIGHT * nn_p + XGB_WEIGHT * xgb_p + LR_WEIGHT * lr_p
    return np.clip(blended, PROB_CLIP_MIN, PROB_CLIP_MAX).astype(np.float64)
```

Also add the import at the top of `nn_projection_engine.py` if not already present:
```python
from services.constants import (
    UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT,
    PROB_CLIP_MIN, PROB_CLIP_MAX, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE,
    MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT,
)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_simulate_season.py::TestBatchPredict -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```
git add services/nn_projection_engine.py tests/test_simulate_season.py
git commit -m "feat: add NNProjectionEngine._batch_predict() for vectorized ensemble inference"
```

---

## Task 3: Add `_build_initial_state()` and `_precompute_static_features()`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_simulate_season.py`

`_build_initial_state()` extracts per-team absolute values (Elo, EPA, margin) from the loaded profiles into a numpy array. `_precompute_static_features()` builds a `(26,)` feature array per game with time-invariant values (travel, trench, roster, etc.) — the 5 dynamic features (`elo_diff`, `elo_confidence`, `pass_epa_matchup`, `rush_epa_matchup`, `point_diff_advantage`) are left at 0.0 and overwritten per trial during simulation.

State array dimension layout (used throughout remaining tasks):
```
dim 0: elo
dim 1: off_pass_epa
dim 2: off_rush_epa
dim 3: def_pass_epa
dim 4: def_rush_epa
dim 5: margin_roll
```

- [ ] **Step 1: Write failing tests**

Add to `tests/test_simulate_season.py`:

```python
class TestBuildInitialState:
    def test_returns_correct_shapes(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        assert state_template.shape == (2, 6)   # 2 teams, 6 state dims
        assert len(team_list) == 2
        assert set(team_idx.keys()) == {"STRONG", "WEAK"}

    def test_elo_extracted_correctly(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        assert state_template[team_idx["STRONG"], 0] == pytest.approx(1600.0)
        assert state_template[team_idx["WEAK"],   0] == pytest.approx(1400.0)

    def test_epa_extracted_correctly(self, mock_engine):
        state_template, team_list, team_idx = mock_engine._build_initial_state()
        # off_pass_epa (dim 1)
        assert state_template[team_idx["STRONG"], 1] == pytest.approx(0.15)
        assert state_template[team_idx["WEAK"],   1] == pytest.approx(-0.10)


class TestPrecomputeStaticFeatures:
    def _make_schedule(self):
        return pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 1, "game_type": "REG",
             "spread_line": -7.0, "div_game": 0, "surface_type": 0},
        ])

    def test_returns_entry_for_each_game(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        assert "W01_STRONG_WEAK" in feats

    def test_static_array_correct_length(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        assert feats["W01_STRONG_WEAK"].shape == (len(NN_FEATURE_COLUMNS),)

    def test_dynamic_features_zeroed(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        arr = feats["W01_STRONG_WEAK"]
        col_idx = {c: i for i, c in enumerate(NN_FEATURE_COLUMNS)}
        for dyn in ("elo_diff", "elo_confidence", "pass_epa_matchup",
                    "rush_epa_matchup", "point_diff_advantage"):
            assert arr[col_idx[dyn]] == 0.0, f"{dyn} should be 0 in static features"

    def test_home_field_is_one(self, mock_engine):
        sched = self._make_schedule()
        feats = mock_engine._precompute_static_features(sched)
        col_idx = {c: i for i, c in enumerate(NN_FEATURE_COLUMNS)}
        assert feats["W01_STRONG_WEAK"][col_idx["home_field_advantage"]] == 1.0
```

- [ ] **Step 2: Run tests to confirm failure**

```
pytest tests/test_simulate_season.py::TestBuildInitialState tests/test_simulate_season.py::TestPrecomputeStaticFeatures -v
```
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement `_build_initial_state()`**

Add to `NNProjectionEngine` in `services/nn_projection_engine.py`:

```python
def _build_initial_state(self) -> tuple:
    """Extract per-team absolute state from loaded team profiles.

    Returns:
        state_template: float32 array of shape (n_teams, 6).
                        Dims: [elo, off_pass_epa, off_rush_epa,
                                def_pass_epa, def_rush_epa, margin_roll]
        team_list: sorted list of team abbreviations.
        team_idx: {team: index into state_template}.
    """
    profile_dict = {row["team"]: row.to_dict() for _, row in self._team_profiles.iterrows()}
    team_list = sorted(profile_dict.keys())
    team_idx = {t: i for i, t in enumerate(team_list)}

    state_template = np.zeros((len(team_list), 6), dtype=np.float32)
    for team, idx in team_idx.items():
        p = profile_dict[team]
        state_template[idx, 0] = float(p.get("elo_pre",           1500.0))
        state_template[idx, 1] = float(p.get("off_pass_epa_roll",    0.0))
        state_template[idx, 2] = float(p.get("off_rush_epa_roll",    0.0))
        state_template[idx, 3] = float(p.get("def_pass_epa_roll",    0.0))
        state_template[idx, 4] = float(p.get("def_rush_epa_roll",    0.0))
        state_template[idx, 5] = float(p.get("margin_roll",          0.0))

    return state_template, team_list, team_idx
```

- [ ] **Step 4: Implement `_precompute_static_features()`**

Add to `NNProjectionEngine` after `_build_initial_state()`:

```python
def _precompute_static_features(self, schedule_df: pd.DataFrame) -> dict:
    """Build the time-invariant portion of the feature vector for each game.

    The 5 dynamic features (elo_diff, elo_confidence, pass_epa_matchup,
    rush_epa_matchup, point_diff_advantage) are left at 0.0 and overwritten
    per-trial inside simulate_season().

    Returns:
        {game_key: float32 array of shape (n_features,)}
    """
    from services.nn_feature_engine import _normalize_team, FEATURE_COLUMNS as NN_FC
    from services.prediction_service import _get_travel_distance

    profile_dict = {row["team"]: row.to_dict() for _, row in self._team_profiles.iterrows()}
    col_idx = {c: i for i, c in enumerate(NN_FC)}
    static_feats = {}

    for _, game in schedule_df.iterrows():
        ht = _normalize_team(str(game.get("home_team", "") or ""))
        at = _normalize_team(str(game.get("away_team", "") or ""))
        wk = game.get("week")
        if not ht or not at or wk is None:
            continue

        key = f"W{int(wk):02d}_{ht}_{at}"
        hp = profile_dict.get(ht, {})
        ap = profile_dict.get(at, {})

        feat = np.zeros(len(NN_FC), dtype=np.float32)

        # Game-context static values
        feat[col_idx["home_field_advantage"]]   = 1.0
        feat[col_idx["rest_advantage"]]         = 0.0
        feat[col_idx["home_qb_injury_flag"]]    = 0.0
        feat[col_idx["away_qb_injury_flag"]]    = 0.0
        feat[col_idx["playoff_flag"]]           = 0.0
        feat[col_idx["week"]]                   = float(wk)
        feat[col_idx["div_game_flag"]]          = float(game.get("div_game", 0) or 0)
        feat[col_idx["surface_type"]]           = float(game.get("surface_type", 0) or 0)

        # Travel (away team perspective)
        try:
            feat[col_idx["net_travel_disadvantage"]] = _get_travel_distance(at, ht) / 1000.0
        except Exception:
            pass

        # Team matchup features from profiles (static — prior-season baseline)
        feat[col_idx["market_implied_team_total"]]  = float(hp.get("market_implied_team_total", 22.0))
        feat[col_idx["passing_difficulty_index"]]   = float(hp.get("passing_difficulty_index", 0.0))
        feat[col_idx["early_down_matchup"]]         = (
            float(hp.get("off_early_roll", 0.0)) - float(ap.get("def_early_roll", 0.0))
            - float(ap.get("off_early_roll", 0.0)) + float(hp.get("def_early_roll", 0.0))
        )
        feat[col_idx["turnover_margin_rolling"]]    = (
            float(hp.get("turnover_margin_rolling", 0.0)) - float(ap.get("turnover_margin_rolling", 0.0))
        )
        feat[col_idx["net_success_rate"]]           = (
            float(hp.get("net_success_rate", 0.0)) - float(ap.get("net_success_rate", 0.0))
        )
        feat[col_idx["qb_pressure_advantage"]]      = (
            float(ap.get("qb_pressure_roll", 0.0)) - float(hp.get("qb_pressure_roll", 0.0))
        )
        feat[col_idx["def_pressure_diff"]]          = (
            float(hp.get("def_pressures_roll", 0.0)) - float(ap.get("def_pressures_roll", 0.0))
        )

        # Trench: preseason roster if available, else profile average
        if self._preseason_roster and self._preseason_norm:
            ol_mu, ol_sig, dl_mu, dl_sig = self._preseason_norm
            h_pr = self._preseason_roster.get(ht, {})
            a_pr = self._preseason_roster.get(at, {})
            h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                   + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
            a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                   + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
            feat[col_idx["trench_dominance_metric"]] = float(h_z - a_z)
        else:
            feat[col_idx["trench_dominance_metric"]] = (
                float(hp.get("trench_score", 0.0)) - float(ap.get("trench_score", 0.0))
            )

        # Roster value deltas (home-centric signed features from prior season)
        feat[col_idx["roster_talent_delta"]]     = (
            float(hp.get("roster_talent_delta", 0.0)) - float(ap.get("roster_talent_delta", 0.0))
        )
        feat[col_idx["off_roster_value_delta"]]  = float(hp.get("off_roster_value_delta", 0.0))
        feat[col_idx["def_roster_value_delta"]]  = float(hp.get("def_roster_value_delta", 0.0))
        feat[col_idx["st_value_delta"]]          = float(hp.get("st_value_delta", 0.0))
        feat[col_idx["qb_resilience_delta"]]     = float(hp.get("qb_resilience_delta", 0.0))

        static_feats[key] = feat

    return static_feats
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_simulate_season.py::TestBuildInitialState tests/test_simulate_season.py::TestPrecomputeStaticFeatures -v
```
Expected: PASS (all 7 tests)

- [ ] **Step 6: Commit**

```
git add services/nn_projection_engine.py tests/test_simulate_season.py
git commit -m "feat: add _build_initial_state() and _precompute_static_features() to NNProjectionEngine"
```

---

## Task 4: Add Vectorized Elo and EPA Update Helpers

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_simulate_season.py`

Both helpers mutate `state` in-place across all `n_sims` trials simultaneously using numpy vectorization.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_simulate_season.py`:

```python
class TestVectorizedEloUpdate:
    def _make_state(self, n_sims, n_teams=2, strong_elo=1600.0, weak_elo=1400.0):
        """state[n_sims, n_teams, 6]. Team 0 = strong, team 1 = weak."""
        state = np.zeros((n_sims, n_teams, 6), dtype=np.float32)
        state[:, 0, 0] = strong_elo
        state[:, 1, 0] = weak_elo
        return state

    def test_elo_increases_for_winner(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_strong_elo = state[:, 0, 0].copy()
        # Home team (idx 0 = STRONG) wins all trials
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 0, 0] > initial_strong_elo), "Winner Elo should increase"

    def test_elo_decreases_for_loser(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_weak_elo = state[:, 1, 0].copy()
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 1, 0] < initial_weak_elo), "Loser Elo should decrease"

    def test_elo_zero_sum(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        total_before = state[:, 0, 0] + state[:, 1, 0]
        margins = np.full(n_sims, 7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        total_after = state[:, 0, 0] + state[:, 1, 0]
        np.testing.assert_allclose(total_before, total_after, rtol=1e-4)

    def test_away_team_wins_when_negative_margin(self, mock_engine):
        n_sims = 100
        state = self._make_state(n_sims)
        initial_weak_elo = state[:, 1, 0].copy()
        # Negative margin → away team (WEAK, idx 1) wins
        margins = np.full(n_sims, -7.0, dtype=np.float32)
        mock_engine._vectorized_elo_update(state, h_idx=0, a_idx=1, margins=margins)
        assert np.all(state[:, 1, 0] > initial_weak_elo), "Away winner's Elo should increase"


class TestVectorizedEpaUpdate:
    def test_winner_epa_increases(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 14.0, dtype=np.float32)  # home wins by 14
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        # Home team (winner) off_pass_epa (dim 1) should increase
        assert np.all(state[:, 0, 1] > 0.0)

    def test_loser_epa_decreases(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 14.0, dtype=np.float32)
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        # Away team (loser) off_pass_epa (dim 1) should decrease
        assert np.all(state[:, 1, 1] < 0.0)

    def test_rush_weight_is_half_passing(self, mock_engine):
        n_sims = 50
        state = np.zeros((n_sims, 2, 6), dtype=np.float32)
        margins = np.full(n_sims, 10.0, dtype=np.float32)
        mock_engine._vectorized_epa_update(state, h_idx=0, a_idx=1, margins=margins)
        pass_delta = state[0, 0, 1]   # off_pass_epa
        rush_delta = state[0, 0, 2]   # off_rush_epa
        assert rush_delta == pytest.approx(pass_delta * 0.5, rel=0.01)
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_simulate_season.py::TestVectorizedEloUpdate tests/test_simulate_season.py::TestVectorizedEpaUpdate -v
```
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement `_vectorized_elo_update()`**

Add to `NNProjectionEngine`:

```python
def _vectorized_elo_update(
    self,
    state: np.ndarray,   # (n_sims, n_teams, 6) — mutated in-place
    h_idx: int,
    a_idx: int,
    margins: np.ndarray, # (n_sims,) — positive = home wins
) -> None:
    """Update Elo ratings in-place for all trials after a simulated game."""
    home_wins = margins > 0
    abs_margin = np.abs(margins)

    h_elo = state[:, h_idx, 0]
    a_elo = state[:, a_idx, 0]

    # Elo diff from winner's perspective (home advantage = 48 pts)
    winner_elo_diff = np.where(
        home_wins,
        h_elo - a_elo + 48.0,   # home won: home advantage helps them
        a_elo - h_elo - 48.0,   # away won: home advantage hurt them
    )

    # Expected win probability for the actual winner
    expected = 1.0 / (10.0 ** (-winner_elo_diff / 400.0) + 1.0)

    # Margin-of-victory multiplier (FiveThirtyEight formula)
    log_comp = np.log(np.maximum(abs_margin, 1.0) + 1.0)
    autocorr = winner_elo_diff * 0.001 + 2.2
    mov_mult = log_comp * (2.2 / np.maximum(autocorr, 0.01))

    shift = 20.0 * (1.0 - expected) * mov_mult  # K = 20

    state[:, h_idx, 0] = np.where(home_wins, h_elo + shift, h_elo - shift)
    state[:, a_idx, 0] = np.where(home_wins, a_elo - shift, a_elo + shift)
```

- [ ] **Step 4: Implement `_vectorized_epa_update()`**

Add to `NNProjectionEngine`:

```python
def _vectorized_epa_update(
    self,
    state: np.ndarray,   # (n_sims, n_teams, 6) — mutated in-place
    h_idx: int,
    a_idx: int,
    margins: np.ndarray, # (n_sims,) — positive = home wins
) -> None:
    """Update EPA and margin_roll in-place for all trials after a simulated game."""
    home_wins = margins > 0
    abs_margin = np.abs(margins).astype(np.float32)

    delta      = abs_margin * MC_EPA_SCALE
    rush_delta = delta * MC_EPA_RUSH_WEIGHT
    sign_h = np.where(home_wins,  1.0, -1.0).astype(np.float32)
    sign_a = np.where(home_wins, -1.0,  1.0).astype(np.float32)

    # off_pass_epa (dim 1), def_pass_epa (dim 3)
    state[:, h_idx, 1] += sign_h * delta
    state[:, a_idx, 1] += sign_a * delta
    state[:, h_idx, 3] += sign_h * delta
    state[:, a_idx, 3] += sign_a * delta

    # off_rush_epa (dim 2), def_rush_epa (dim 4)
    state[:, h_idx, 2] += sign_h * rush_delta
    state[:, a_idx, 2] += sign_a * rush_delta
    state[:, h_idx, 4] += sign_h * rush_delta
    state[:, a_idx, 4] += sign_a * rush_delta

    # margin_roll (dim 5) — exponential moving average toward game result
    game_margin_h =  margins.astype(np.float32)
    game_margin_a = -margins.astype(np.float32)
    state[:, h_idx, 5] = 0.85 * state[:, h_idx, 5] + 0.15 * game_margin_h
    state[:, a_idx, 5] = 0.85 * state[:, a_idx, 5] + 0.15 * game_margin_a
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_simulate_season.py::TestVectorizedEloUpdate tests/test_simulate_season.py::TestVectorizedEpaUpdate -v
```
Expected: PASS (all 7 tests)

- [ ] **Step 6: Commit**

```
git add services/nn_projection_engine.py tests/test_simulate_season.py
git commit -m "feat: add vectorized Elo and EPA update helpers to NNProjectionEngine"
```

---

## Task 5: Implement `simulate_season()`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_simulate_season.py`

This assembles all helpers from Tasks 2–4 into the main simulation loop.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_simulate_season.py`:

```python
# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_schedule(n_weeks: int = 4) -> pd.DataFrame:
    """Alternating home/away schedule between STRONG and WEAK over n_weeks."""
    rows = []
    for wk in range(1, n_weeks + 1):
        if wk % 2 == 1:
            rows.append({"home_team": "STRONG", "away_team": "WEAK",
                         "week": wk, "game_type": "REG",
                         "spread_line": -7.0, "div_game": 0, "surface_type": 0})
        else:
            rows.append({"home_team": "WEAK", "away_team": "STRONG",
                         "week": wk, "game_type": "REG",
                         "spread_line": 7.0, "div_game": 0, "surface_type": 0})
    return pd.DataFrame(rows)


def _elo_only_predict(X: np.ndarray) -> np.ndarray:
    """Logistic win probability from elo_diff alone — no model loading needed."""
    elo_diff_idx = list(NN_FEATURE_COLUMNS).index("elo_diff")
    elo_diff = X[:, elo_diff_idx]
    return np.clip(1.0 / (1.0 + np.power(10.0, -elo_diff / 400.0)), 0.02, 0.98)


# ── simulate_season tests ──────────────────────────────────────────────────────

class TestSimulateSeason:
    def test_returns_correct_top_level_keys(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        sched = _make_schedule(2)
        result = mock_engine.simulate_season(sched, n_sims=200)
        assert "team_stats" in result
        assert "game_probs" in result

    def test_team_stats_has_both_teams(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200)
        assert "STRONG" in result["team_stats"]
        assert "WEAK"   in result["team_stats"]

    def test_team_stats_has_all_percentile_keys(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200)
        for key in ("median_wins", "mean_wins", "std_dev", "p5", "p25", "p75", "p95"):
            assert key in result["team_stats"]["STRONG"]

    def test_wins_sum_to_total_games(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        n_weeks = 4
        result = mock_engine.simulate_season(_make_schedule(n_weeks), n_sims=500)
        total = (result["team_stats"]["STRONG"]["mean_wins"]
                 + result["team_stats"]["WEAK"]["mean_wins"])
        assert total == pytest.approx(n_weeks, abs=0.5)

    def test_strong_team_projects_more_wins(self, mock_engine):
        """200 Elo point gap → STRONG should project more wins than WEAK."""
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(18), n_sims=1000)
        strong_wins = result["team_stats"]["STRONG"]["mean_wins"]
        weak_wins   = result["team_stats"]["WEAK"]["mean_wins"]
        assert strong_wins > 10.0, f"Expected >10 but got {strong_wins:.1f}"
        assert weak_wins   <  8.0, f"Expected <8 but got {weak_wins:.1f}"

    def test_game_probs_populated_for_future_games(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(_make_schedule(3), n_sims=200)
        assert len(result["game_probs"]) == 3

    def test_completed_results_applied_deterministically(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        # Mark week 1 as STRONG winning by 7 (home wins, positive margin)
        completed = {"W01_STRONG_WEAK": 7.0}
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200,
                                              completed_results=completed)
        # Week 1 should not appear in game_probs (it was completed)
        assert "W01_STRONG_WEAK" not in result["game_probs"]
        # Week 2 should appear (future)
        assert "W02_WEAK_STRONG" in result["game_probs"]

    def test_completed_games_credited_to_winner(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        # STRONG wins week 1 by 7, WEAK wins week 2 by 3
        completed = {"W01_STRONG_WEAK": 7.0, "W02_WEAK_STRONG": -3.0}
        result = mock_engine.simulate_season(_make_schedule(2), n_sims=200,
                                              completed_results=completed)
        # All wins should be 1 each (1 game each, all completed deterministically)
        assert result["team_stats"]["STRONG"]["mean_wins"] == pytest.approx(1.0, abs=0.01)
        assert result["team_stats"]["WEAK"]["mean_wins"]   == pytest.approx(1.0, abs=0.01)
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_simulate_season.py::TestSimulateSeason -v
```
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Implement `simulate_season()`**

Add to `NNProjectionEngine`:

```python
def simulate_season(
    self,
    schedule_df: pd.DataFrame,
    n_sims: int = 10_000,
    completed_results: dict = None,
) -> dict:
    """Dynamic week-by-week Monte Carlo season simulation.

    Args:
        schedule_df: Full season schedule with columns week, home_team, away_team.
                     Should include game_type column; if absent, all rows are treated
                     as regular season.
        n_sims: Number of independent simulation trials.
        completed_results: {game_key: margin} for already-played games.
                           game_key format: "W{wk:02d}_{home}_{away}"
                           margin = home_score - away_score (positive = home won).

    Returns:
        {
            "team_stats":  {team: {median_wins, mean_wins, std_dev, p5, p25, p75, p95}},
            "game_probs":  {game_key: {mean_prob, model_spread, home_team, away_team, week}},
        }
    """
    from services.nn_feature_engine import _normalize_team, FEATURE_COLUMNS as NN_FC

    if completed_results is None:
        completed_results = {}

    # Filter to regular season
    if "game_type" in schedule_df.columns:
        reg = schedule_df[schedule_df["game_type"] == "REG"].copy()
    else:
        reg = schedule_df.copy()

    if reg.empty:
        return {"team_stats": {}, "game_probs": {}}

    # Normalize team abbreviations
    reg["home_team"] = reg["home_team"].apply(lambda x: _normalize_team(str(x)))
    reg["away_team"] = reg["away_team"].apply(lambda x: _normalize_team(str(x)))

    # Build initial state and index
    state_template, team_list, team_idx = self._build_initial_state()
    n_teams = len(team_list)

    # Broadcast initial state across all simulations: (n_sims, n_teams, 6)
    state = np.tile(state_template[np.newaxis], (n_sims, 1, 1)).astype(np.float32)
    win_matrix = np.zeros((n_sims, n_teams), dtype=np.float32)
    game_probs_out = {}

    # Pre-compute static feature arrays for all games
    static_feats = self._precompute_static_features(reg)
    col_idx = {c: i for i, c in enumerate(NN_FC)}
    rng = np.random.default_rng(seed=42)

    # Process weeks in ascending order
    for week, week_df in reg.groupby("week", sort=True):
        future_games = []

        for _, game in week_df.iterrows():
            ht = game["home_team"]
            at = game["away_team"]
            if ht not in team_idx or at not in team_idx:
                continue
            h_idx = team_idx[ht]
            a_idx = team_idx[at]
            key = f"W{int(week):02d}_{ht}_{at}"

            if key in completed_results:
                # Apply real result deterministically across all trials
                real_margin = float(completed_results[key])
                margins = np.full(n_sims, real_margin, dtype=np.float32)
                home_won = real_margin > 0
                win_matrix[:, h_idx] += float(home_won)
                win_matrix[:, a_idx] += float(not home_won)
                self._vectorized_elo_update(state, h_idx, a_idx, margins)
                self._vectorized_epa_update(state, h_idx, a_idx, margins)
            else:
                future_games.append((ht, at, h_idx, a_idx, key))

        if not future_games:
            continue

        # Build batched feature matrix: (G * n_sims, n_features)
        G = len(future_games)
        X_week = np.zeros((G * n_sims, len(NN_FC)), dtype=np.float32)

        for g_i, (ht, at, h_idx, a_idx, key) in enumerate(future_games):
            s, e = g_i * n_sims, (g_i + 1) * n_sims
            base = static_feats.get(key, np.zeros(len(NN_FC), dtype=np.float32))
            X_week[s:e] = np.broadcast_to(base, (n_sims, len(NN_FC))).copy()

            # Overwrite dynamic features from current trial states
            h_elo = state[:, h_idx, 0]
            a_elo = state[:, a_idx, 0]
            elo_diff = h_elo - a_elo

            X_week[s:e, col_idx["elo_diff"]]            = elo_diff
            X_week[s:e, col_idx["elo_confidence"]]      = np.abs(elo_diff) / ELO_TO_SPREAD
            X_week[s:e, col_idx["pass_epa_matchup"]]    = (
                (state[:, h_idx, 1] - state[:, a_idx, 3])
                - (state[:, a_idx, 1] - state[:, h_idx, 3])
            )
            X_week[s:e, col_idx["rush_epa_matchup"]]    = (
                (state[:, h_idx, 2] - state[:, a_idx, 4])
                - (state[:, a_idx, 2] - state[:, h_idx, 4])
            )
            X_week[s:e, col_idx["point_diff_advantage"]] = (
                state[:, h_idx, 5] - state[:, a_idx, 5]
            )

        # Batch predict: (G * n_sims,) → reshape to (G, n_sims)
        probs_flat = self._batch_predict(X_week)
        probs_matrix = probs_flat.reshape(G, n_sims)

        # Simulate outcomes and update state for each game
        for g_i, (ht, at, h_idx, a_idx, key) in enumerate(future_games):
            game_probs = probs_matrix[g_i].astype(np.float64)
            mean_prob = float(np.mean(game_probs))
            mean_prob_clipped = float(np.clip(mean_prob, PROB_CLIP_MIN, PROB_CLIP_MAX))

            # Sample margins: per-trial implied spread → Normal(implied, MC_MARGIN_STD)
            implied = SPREAD_TO_PROB_SCALE * np.log(
                np.clip(game_probs, PROB_CLIP_MIN, PROB_CLIP_MAX)
                / (1.0 - np.clip(game_probs, PROB_CLIP_MIN, PROB_CLIP_MAX))
            )
            margins = rng.normal(implied, MC_MARGIN_STD).astype(np.float32)

            # Update win counts
            win_matrix[:, h_idx] += (margins > 0).astype(np.float32)
            win_matrix[:, a_idx] += (margins < 0).astype(np.float32)

            # Update team state for future weeks
            self._vectorized_elo_update(state, h_idx, a_idx, margins)
            self._vectorized_epa_update(state, h_idx, a_idx, margins)

            # Record game prediction
            model_spread = float(
                SPREAD_TO_PROB_SCALE * np.log(mean_prob_clipped / (1.0 - mean_prob_clipped))
            )
            game_probs_out[key] = {
                "mean_prob":   round(mean_prob_clipped, 4),
                "model_spread": round(model_spread, 1),
                "home_team":   ht,
                "away_team":   at,
                "week":        int(week),
            }

    # Aggregate win distributions per team
    team_stats = {}
    for team, t_idx in team_idx.items():
        w = win_matrix[:, t_idx]
        team_stats[team] = {
            "median_wins": float(np.median(w)),
            "mean_wins":   float(np.mean(w)),
            "std_dev":     float(np.std(w)),
            "p5":          float(np.percentile(w, 5)),
            "p25":         float(np.percentile(w, 25)),
            "p75":         float(np.percentile(w, 75)),
            "p95":         float(np.percentile(w, 95)),
        }

    return {"team_stats": team_stats, "game_probs": game_probs_out}
```

- [ ] **Step 4: Run all simulate_season tests**

```
pytest tests/test_simulate_season.py::TestSimulateSeason -v
```
Expected: PASS (all 8 tests)

- [ ] **Step 5: Run full test suite to check for regressions**

```
pytest tests/ -x -q
```
Expected: all existing tests still pass

- [ ] **Step 6: Commit**

```
git add services/nn_projection_engine.py tests/test_simulate_season.py
git commit -m "feat: implement NNProjectionEngine.simulate_season() with dynamic Elo/EPA state updates"
```

---

## Task 6: Refactor `predict_season.py`

**Files:**
- Modify: `scripts/predict_season.py`

Remove the three internal functions that duplicated profile-building and MC logic. The script becomes a thin wrapper around the engine.

- [ ] **Step 1: Delete internal functions**

In `scripts/predict_season.py`, delete the following functions entirely:
- `_build_team_profiles()` (lines ~93–107)
- `_compute_game_probs()` (lines ~114–156)
- `_run_monte_carlo()` (lines ~163–194)

Also delete their imports that are no longer used (check `NNPredictionService`, `XGBPredictionService`, `LRPredictionService` — these are now loaded inside `NNProjectionEngine`). Keep `_load_schedule()`, `_upload_predictions()`, and `main()`.

- [ ] **Step 2: Update imports**

At the top of `scripts/predict_season.py`, remove these three imports (the engine loads them internally):
```python
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS as NN_FEATURE_COLUMNS,
)
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService
```

Add in their place:
```python
from services.nn_projection_engine import NNProjectionEngine
```

Keep all other imports (`NN_WEIGHT`, `XGB_WEIGHT`, `LR_WEIGHT`, `_load_schedule`, `_upload_predictions`, etc.).

- [ ] **Step 3: Rewrite `main()`**

Replace the body of `main()` with:

```python
def main():
    parser = argparse.ArgumentParser(description="Generate NFL season win projections")
    parser.add_argument("--season", type=int, default=_default_season())
    parser.add_argument("--simulations", type=int, default=N_SIMULATIONS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    season      = args.season
    prior_season = season - 1

    print("=" * 72)
    print(f"  NFL ML Ensemble -- {season} Season Projections (Dynamic MC)")
    print(f"  Feature baseline: {prior_season}  |  Simulations: {args.simulations:,}")
    print(f"  Blend: NN={NN_WEIGHT:.0%} / XGB={XGB_WEIGHT:.0%} / LR={LR_WEIGHT:.0%}")
    print("=" * 72)

    print("\n[1/4] Loading models and building team profiles...")
    from services.nn_projection_engine import NNProjectionEngine
    engine = NNProjectionEngine()
    engine.initialize(season)
    print(f"  {len(engine._team_profiles)} team profiles built from {prior_season} data.")

    print(f"[2/4] Loading {season} schedule...")
    schedule = _load_schedule(RAWDATA_DIR, season, prior_season)
    if schedule.empty:
        print("ERROR: No schedule data found.")
        sys.exit(1)
    print(f"  {len(schedule)} regular season games.")

    print(f"[3/4] Running dynamic Monte Carlo ({args.simulations:,} trials)...")
    results = engine.simulate_season(schedule, n_sims=args.simulations)
    team_stats = results["team_stats"]

    projections = sorted([
        {
            "team":      team,
            "proj_wins": round(stats["median_wins"], 1),
            "mean_wins": round(stats["mean_wins"],   1),
            "std_dev":   round(stats["std_dev"],     2),
            "floor":     round(stats["p5"],          1),
            "p25":       round(stats["p25"],         1),
            "p75":       round(stats["p75"],         1),
            "ceiling":   round(stats["p95"],         1),
        }
        for team, stats in team_stats.items()
    ], key=lambda x: x["proj_wins"], reverse=True)

    # Print table
    print(f"\n{'Rk':<4}{'Team':<6}{'Proj':<6}{'Mean':<6}{'StdDev':<8}"
          f"{'Floor':<7}{'25th':<6}{'75th':<6}{'Ceil':<6}")
    print("-" * 55)
    for i, p in enumerate(projections, 1):
        print(f"{i:<4}{p['team']:<6}{p['proj_wins']:<6.1f}{p['mean_wins']:<6.1f}"
              f"{p['std_dev']:<8.2f}{p['floor']:<7.1f}{p['p25']:<6.1f}"
              f"{p['p75']:<6.1f}{p['ceiling']:<6.1f}")

    total = sum(p["proj_wins"] for p in projections)
    print(f"\n{'='*55}")
    print(f"  {len(projections)} teams | {args.simulations:,} sims | total wins: {total:.0f}")
    print(f"  Range: {min(p['proj_wins'] for p in projections):.1f}"
          f"–{max(p['proj_wins'] for p in projections):.1f} wins")
    print(f"{'='*55}")

    if args.dry_run:
        print("\n[dry-run] Skipping Firestore upload.")
    else:
        print("\n[4/4] Saving to Firestore preseason_predictions...")
        _upload_predictions(season, projections)
        print("Done.")
```

Also update the imports block at the top — remove the direct model service imports (they move inside the engine), keep `_load_schedule`, `_upload_predictions`, `_default_season`, `_init_firebase`, `RAWDATA_DIR`, `NN_WEIGHT`, `XGB_WEIGHT`, `LR_WEIGHT`, `N_SIMULATIONS`.

- [ ] **Step 4: Verify dry-run works**

```
python scripts/predict_season.py --season 2026 --simulations 500 --dry-run
```

Expected output shows a win range wider than 7–10. Look for something like:
```
  Range: 4.0–13.0 wins   (or similar — definitely not 7.0–10.0)
```

If still 7–10, the engine is still using the old logic — check that `engine.initialize(season)` and `engine.simulate_season()` are the new code paths.

- [ ] **Step 5: Commit**

```
git add scripts/predict_season.py
git commit -m "refactor: predict_season.py now delegates to NNProjectionEngine.simulate_season()"
```

---

## Task 7: Refactor `backfill_schedule_predictions.py`

**Files:**
- Modify: `scripts/backfill_schedule_predictions.py`

Replace `_profile_predictions_for_year()` with `NNProjectionEngine.simulate_season()`. The engine gets `completed_results` built from actual `nfl_games` margins so it reconstructs the real team state before simulating forward.

- [ ] **Step 1: Delete `_profile_predictions_for_year()`**

Delete the entire function `_profile_predictions_for_year()` (lines ~62–173).

- [ ] **Step 2: Update `_build_predictions_map()` signature**

Change the function signature to accept `games_df`:

```python
def _build_predictions_map(year: int, ft_lookup: dict,
                            schedule_df: pd.DataFrame,
                            games_df: pd.DataFrame,
                            force: bool) -> dict:
```

- [ ] **Step 3: Replace profile-prediction body with engine call**

Replace the call to `_profile_predictions_for_year()` inside `_build_predictions_map()`. The full updated function body:

```python
def _build_predictions_map(year: int, ft_lookup: dict,
                            schedule_df: pd.DataFrame,
                            games_df: pd.DataFrame,
                            force: bool) -> dict:
    # Feature-table predictions for completed games → locked
    played_keys = {}
    for (s, wk, ht, at), pred in ft_lookup.items():
        if s == year:
            played_keys[f"W{wk:02d}_{ht}_{at}"] = pred
    result = {k: {**v, "locked": True} for k, v in played_keys.items()}

    # Build completed_results from actual nfl_games scores
    completed_results = {}
    if not games_df.empty and "result" in games_df.columns:
        yr_games = games_df[games_df["season"] == year] if "season" in games_df.columns else games_df
        for _, row in yr_games.iterrows():
            if pd.notna(row.get("result")) and row.get("game_type") == "REG":
                ht = _normalize_team(str(row.get("home_team", "") or ""))
                at = _normalize_team(str(row.get("away_team", "") or ""))
                wk = row.get("week")
                if ht and at and wk is not None:
                    key = f"W{int(wk):02d}_{ht}_{at}"
                    completed_results[key] = float(row["result"])

    # Run dynamic MC simulation for all games (completed apply deterministically,
    # future games simulate forward from rebuilt team state)
    if not schedule_df.empty:
        engine = NNProjectionEngine()  # already imported at top of file
        engine.initialize(year)
        sim = engine.simulate_season(schedule_df, n_sims=10_000,
                                     completed_results=completed_results)

        for key, gp in sim["game_probs"].items():
            if key in played_keys:
                continue  # never overwrite a locked feature-table prediction
            ht   = gp["home_team"]
            at   = gp["away_team"]
            wk   = gp["week"]
            hp   = gp["mean_prob"]
            winner = ht if hp >= 0.5 else at
            conf   = round(max(hp, 1.0 - hp) * 100, 1)
            ms     = gp["model_spread"]

            # Vegas line from schedule if available
            sched_row = schedule_df[
                (schedule_df["home_team"].apply(_normalize_team) == ht)
                & (schedule_df["week"] == wk)
            ]
            sl_val = None
            if not sched_row.empty and pd.notna(sched_row.iloc[0].get("spread_line")):
                sl_val = float(sched_row.iloc[0]["spread_line"])

            edge   = round(ms - sl_val, 1) if sl_val is not None else None
            ats    = ht if ms > (sl_val or 0) else at
            vhp    = (round(1.0 / (1.0 + np.exp(-sl_val / SPREAD_TO_PROB_SCALE)), 4)
                      if sl_val is not None else None)

            result[key] = {
                "pred_prob":     round(hp, 4),
                "pred_winner":   winner,
                "pred_su_conf":  conf,
                "pred_ats_pick": ats,
                "model_spread":  ms,
                "edge_vs_vegas": edge,
                "locked": False,
                "explanation": {
                    "vegas_line":           sl_val,
                    "vegas_home_prob":      vhp,
                    "model_spread":         ms,
                    "edge_vs_vegas":        edge,
                    "elo_diff":             0.0,
                    "roster_delta":         0.0,
                    "pass_epa_matchup":     0.0,
                    "rush_epa_matchup":     0.0,
                    "early_down_matchup":   0.0,
                    "turnover_margin":      0.0,
                    "point_diff_advantage": 0.0,
                    "home_qb_out":          0.0,
                    "away_qb_out":          0.0,
                    "rest_advantage":       0.0,
                    "travel_disadvantage":  0.0,
                    "trench_dominance":     0.0,
                    "off_roster_value":     0.0,
                    "def_roster_value":     0.0,
                    "source": "mc_simulation (10000 trials)",
                },
            }

    if not force:
        existing = get_game_predictions(year)
        for k, v in existing.items():
            if v.get("locked") and k not in played_keys:
                result[k] = v

    return result
```

- [ ] **Step 4: Update the call site in `main()`**

In `main()`, the `all_games` DataFrame is already loaded. Pass it into `_build_predictions_map`:

Find the line:
```python
predictions_map = _build_predictions_map(year, ft_lookup, yr_schedule, args.force)
```

Replace with:
```python
predictions_map = _build_predictions_map(year, ft_lookup, yr_schedule, all_games, args.force)
```

- [ ] **Step 5: Dry-run for 2026 only**

```
python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --dry-run
```

Expected output shows `2026  NNN games  [dry-run]  0 locked / NNN open` with NNN ≈ number of 2026 regular season games. No errors.

- [ ] **Step 6: Run existing backfill tests**

```
pytest tests/test_backfill_features_flag.py -v
```
Expected: PASS (no regression in existing tests)

- [ ] **Step 7: Commit**

```
git add scripts/backfill_schedule_predictions.py
git commit -m "refactor: backfill_schedule_predictions uses simulate_season() for future game predictions"
```

---

## Task 8: Validate Distribution and Run Full Test Suite

**Files:** No code changes — validation only.

- [ ] **Step 1: Run predict_season dry-run with full simulations**

```
python scripts/predict_season.py --season 2026 --simulations 10000 --dry-run
```

Expected: win range is at least 8 points wide (e.g. 4–13 or better). Std dev ≥ 2.0. If range is still 7–10, add some debug output to `simulate_season()` to print the Elo range being used — likely the profiles aren't loaded correctly.

- [ ] **Step 2: Check total wins sanity**

In the printed table, sum the `Proj` column mentally. Should be approximately `32 × 8.5 = 272` (each game produces exactly one winner). If it's wildly off, ties (margin == 0) may be eating wins — investigate.

- [ ] **Step 3: Run the full test suite**

```
pytest tests/ -q
```
Expected: all tests pass. Pay attention to any failures in:
- `test_game_prediction.py` — `NNProjectionEngine` still needs to work for its existing callers
- `test_nn_prediction_service.py` — model loading tests
- `test_simulate_season.py` — all new tests pass

- [ ] **Step 4: Final commit**

```
git add .
git commit -m "test: validate dynamic MC simulation produces realistic win distribution (4-13 range)"
```
