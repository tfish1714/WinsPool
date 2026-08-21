# Bottom-Up Model Retrain (Narrow Fix) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two known-wrong feature computations in the training pipeline so models learn the correct response to preseason profile z-scores, then retrain NN/XGB/LR and re-enable the 5 preseason feature overrides in `nn_projection_engine.py`.

**Architecture:** (1) Add `_build_profile_z_table()` to `nn_feature_engine.py` and call it inside `build_master_feature_table()` to override 5 features for seasons 2020+ with profile-derived z-scores and fix the `off/def_roster_value_delta` home-only bug; (2) retrain all three models; (3) restore the RETRAIN SPEC comment block in `_precompute_static_features()` with corrected formulas; (4) backfill 2026 predictions and verify.

**Tech Stack:** Python, NumPy, pandas, TensorFlow/Keras (NN), XGBoost, scikit-learn (LR).

---

## File Map

| File | Change |
|---|---|
| `services/nn_feature_engine.py` | Add `_build_profile_z_table()`; override 5 features at end of `build_master_feature_table()` |
| `tests/test_preseason_profiles.py` | Add `TestProfileZTable` and `TestPreseasonFeatureOverridesPostRetrain` test classes |
| `services/nn_projection_engine.py` | Replace RETRAIN SPEC comment with live override code (corrected formulas) |

---

## Key Data Facts

**`compute_preseason_player_profiles(target_season, rawdata_dir)`** already exists in `nn_feature_engine.py` (line 798). It reads prior-season depth charts + stats and returns `{team: {off_pass_epa, off_rush_epa, def_pass_epa, def_rush_epa, ol_av, dl_perf, qb_tier}}` with EPA dimensions already mean-centered but NOT z-scored. The `ol_av` and `dl_perf` values are raw (not z-scored).

**`build_master_feature_table()`** (line 1375) ends with:
1. Trench features computed at ~line 1667
2. `roster_talent_delta` at ~line 1660
3. `off/def_roster_value_delta` etc. via `roster_value_service` at ~line 1732
4. Final numeric coercion at line 1761

We insert our override block **after line 1759** (after the roster value block) and **before line 1761** (before the final numeric coercion loop).

**The 5 target features and their formulas** (hz = home z-scores, az = away z-scores):
```
def_pressure_diff       = hz["dl_perf"]  − az["dl_perf"]
qb_pressure_advantage   = az["dl_perf"]  − hz["dl_perf"]
off_roster_value_delta  = (0.7·hz["qb_tier"] + 0.3·hz["ol_av"]) − (0.7·az["qb_tier"] + 0.3·az["ol_av"])
def_roster_value_delta  = (0.6·hz["dl_perf"] + 0.4·(−hz["def_pass_epa"])) − (0.6·az["dl_perf"] + 0.4·(−az["def_pass_epa"]))
roster_talent_delta     = mean(hz["qb_tier"], hz["off_pass_epa"], −hz["def_pass_epa"], hz["dl_perf"], hz["ol_av"])
                          − mean(az["qb_tier"], az["off_pass_epa"], −az["def_pass_epa"], az["dl_perf"], az["ol_av"])
```

**Z-scoring:** `_build_profile_z_table()` must z-score `dl_perf`, `ol_av` (raw values) and the EPA dims (already mean-centered but not unit-scaled) within each season across all teams.

**RETRAIN SPEC comment location:** `services/nn_projection_engine.py` lines 301–308. This comment block marks exactly where the live override code should go.

---

## Task 1: `_build_profile_z_table()` + tests

**Files:**
- Modify: `services/nn_feature_engine.py` (after `compute_preseason_player_profiles`, around line 862)
- Modify: `tests/test_preseason_profiles.py` (append new class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestBuildProfileZTable:
    def _make_raw_profiles(self):
        """Minimal two-team profile dict as returned by compute_preseason_player_profiles."""
        return {
            "AAA": {"off_pass_epa": 0.10, "off_rush_epa": 0.02,
                    "def_pass_epa": -0.05, "def_rush_epa": -0.02,
                    "qb_tier": 0.18, "ol_av": 350000.0, "dl_perf": 500.0},
            "BBB": {"off_pass_epa": -0.10, "off_rush_epa": -0.02,
                    "def_pass_epa":  0.05, "def_rush_epa":  0.02,
                    "qb_tier": -0.05, "ol_av": 150000.0, "dl_perf": 200.0},
        }

    def test_returns_z_scores_for_each_team(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([2024], rawdata_dir=".")
        assert (2024, "AAA") in table
        assert (2024, "BBB") in table

    def test_z_scores_sum_to_zero_across_teams(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([2024], rawdata_dir=".")
        dims = ["dl_perf", "qb_tier", "ol_av", "off_pass_epa", "def_pass_epa"]
        for d in dims:
            total = table[(2024, "AAA")][d] + table[(2024, "BBB")][d]
            assert abs(total) < 1e-6, f"{d} z-scores don't sum to 0: {total}"

    def test_stronger_team_has_positive_dl_z(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([2024], rawdata_dir=".")
        # AAA has higher dl_perf (500 vs 200) so should have positive z
        assert table[(2024, "AAA")]["dl_perf"] > 0
        assert table[(2024, "BBB")]["dl_perf"] < 0

    def test_empty_profiles_returns_no_entry_for_season(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value={}):
            table = _build_profile_z_table([2024], rawdata_dir=".")
        assert not any(k[0] == 2024 for k in table)

    def test_multiple_seasons_both_populated(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([2023, 2024], rawdata_dir=".")
        assert any(k[0] == 2023 for k in table)
        assert any(k[0] == 2024 for k in table)
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestBuildProfileZTable -v
```
Expected: FAIL — `_build_profile_z_table` not defined.

- [ ] **Step 3: Implement `_build_profile_z_table()`**

Insert after `compute_preseason_player_profiles` (after line 862) in `services/nn_feature_engine.py`:

```python
def _build_profile_z_table(seasons: list, rawdata_dir) -> dict:
    """Compute cross-team z-scores for 5 profile dimensions for each season.

    Returns {(season, team): {dl_perf, qb_tier, ol_av, off_pass_epa, def_pass_epa}}
    where each value is that team's z-score within the season's 32-team distribution.
    Seasons where compute_preseason_player_profiles returns {} are skipped silently.
    """
    _DIMS = ["dl_perf", "qb_tier", "ol_av", "off_pass_epa", "def_pass_epa"]
    table: dict = {}
    for season in seasons:
        try:
            profiles = compute_preseason_player_profiles(season, rawdata_dir)
        except Exception:
            continue
        if not profiles:
            continue
        teams = list(profiles.keys())
        vals = {d: np.array([profiles[t].get(d, 0.0) for t in teams], dtype=float)
                for d in _DIMS}
        mu  = {d: float(np.mean(v)) for d, v in vals.items()}
        sig = {d: max(float(np.std(v)), 1e-6) for d, v in vals.items()}
        for team in teams:
            table[(season, team)] = {
                d: float((profiles[team].get(d, mu[d]) - mu[d]) / sig[d])
                for d in _DIMS
            }
    return table
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestBuildProfileZTable -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add _build_profile_z_table() for cross-team profile z-scores"
```

---

## Task 2: Override 5 features in `build_master_feature_table()` + tests

**Files:**
- Modify: `services/nn_feature_engine.py` (end of `build_master_feature_table()`)
- Modify: `tests/test_preseason_profiles.py` (append new class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestProfileZTableOverrideInFeatureTable:
    """Verify that build_master_feature_table() applies profile z-score overrides for 2020+."""

    def _make_schedule(self):
        return pd.DataFrame([{
            "season": 2024, "week": 1, "home_team": "KC", "away_team": "BAL",
            "game_type": "REG", "home_win": 1,
            "result": 7.0, "spread_line": -3.0,
        }])

    def test_override_applied_when_profiles_available(self, tmp_path, monkeypatch):
        """def_pressure_diff should reflect profile dl_perf z-scores, not rolling stats."""
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        # Patch _load_schedule to return our minimal schedule
        sched = self._make_schedule()

        kc_z  = {"dl_perf": 1.5, "qb_tier": 1.0, "ol_av": 0.5,
                  "off_pass_epa": 0.8, "def_pass_epa": -0.3}
        bal_z = {"dl_perf": -0.5, "qb_tier": 0.2, "ol_av": -0.3,
                  "off_pass_epa": 0.3, "def_pass_epa": 0.1}
        profile_table = {(2024, "KC"): kc_z, (2024, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        assert not result.empty
        row = result.iloc[0]
        # def_pressure_diff = kc_dl_z - bal_dl_z = 1.5 - (-0.5) = 2.0
        assert abs(row["def_pressure_diff"] - 2.0) < 0.01
        # qb_pressure_advantage = bal_dl_z - kc_dl_z = -0.5 - 1.5 = -2.0
        assert abs(row["qb_pressure_advantage"] - (-2.0)) < 0.01

    def test_off_roster_value_delta_is_differential_not_home_only(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        sched = self._make_schedule()
        kc_z  = {"dl_perf": 0.0, "qb_tier": 1.0, "ol_av": 1.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        bal_z = {"dl_perf": 0.0, "qb_tier": -1.0, "ol_av": -1.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        profile_table = {(2024, "KC"): kc_z, (2024, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        row = result.iloc[0]
        # off_roster_value_delta = (0.7*1 + 0.3*1) - (0.7*(-1) + 0.3*(-1)) = 1.0 - (-1.0) = 2.0
        assert abs(row["off_roster_value_delta"] - 2.0) < 0.01

    def test_roster_talent_delta_uses_all_5_dims(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        sched = self._make_schedule()
        # KC better on all dims, BAL zero on all
        kc_z  = {"dl_perf": 1.0, "qb_tier": 1.0, "ol_av": 1.0,
                  "off_pass_epa": 1.0, "def_pass_epa": -1.0}
        bal_z = {"dl_perf": 0.0, "qb_tier": 0.0, "ol_av": 0.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        profile_table = {(2024, "KC"): kc_z, (2024, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        row = result.iloc[0]
        # h_q = (1+1+1+1+1)/5 = 1.0 (note: def_pass_epa flipped: -(-1)=1)
        # a_q = 0
        # roster_talent_delta = 1.0 - 0.0 = 1.0
        assert abs(row["roster_talent_delta"] - 1.0) < 0.01
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestProfileZTableOverrideInFeatureTable -v
```
Expected: FAIL — override not yet implemented.

- [ ] **Step 3: Add the override block to `build_master_feature_table()`**

In `services/nn_feature_engine.py`, find the block that ends roster_value_service processing (around line 1759):

```python
    except Exception as _e:
        logger.warning("roster_value_service unavailable: %s", _e)
        for _col in ["off_roster_value_delta", "def_roster_value_delta",
                     "st_value_delta", "qb_resilience_delta"]:
            sched[_col] = 0.0

    # --- Ensure all required columns exist and are numeric ---
```

Insert **between** those two blocks:

```python
    # --- Profile z-score overrides for 5 features (seasons 2020+) ---
    # Replaces rolling-average values with cross-team z-scores derived from
    # preseason player profiles. This matches what _precompute_static_features()
    # feeds the models at inference time, fixing the scale mismatch that was
    # inverting predictions for outlier teams (e.g. elite DL).
    _profile_seasons = [s for s in sched["season"].unique() if s >= 2020]
    if _profile_seasons:
        _pz = _build_profile_z_table(sorted(_profile_seasons), rd)
        if _pz:
            def _apply_profile_overrides(row):
                hz = _pz.get((int(row["season"]), row["home_team"]))
                az = _pz.get((int(row["season"]), row["away_team"]))
                if hz is None or az is None:
                    return row
                row["def_pressure_diff"]      = hz["dl_perf"] - az["dl_perf"]
                row["qb_pressure_advantage"]  = az["dl_perf"] - hz["dl_perf"]
                row["off_roster_value_delta"] = (
                    (0.7 * hz["qb_tier"] + 0.3 * hz["ol_av"])
                    - (0.7 * az["qb_tier"] + 0.3 * az["ol_av"])
                )
                row["def_roster_value_delta"] = (
                    (0.6 * hz["dl_perf"] + 0.4 * (-hz["def_pass_epa"]))
                    - (0.6 * az["dl_perf"] + 0.4 * (-az["def_pass_epa"]))
                )
                h_q = (hz["qb_tier"] + hz["off_pass_epa"] + (-hz["def_pass_epa"])
                       + hz["dl_perf"] + hz["ol_av"]) / 5.0
                a_q = (az["qb_tier"] + az["off_pass_epa"] + (-az["def_pass_epa"])
                       + az["dl_perf"] + az["ol_av"]) / 5.0
                row["roster_talent_delta"] = h_q - a_q
                return row

            mask = sched["season"] >= 2020
            sched.loc[mask] = sched.loc[mask].apply(_apply_profile_overrides, axis=1)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestProfileZTableOverrideInFeatureTable -v
```
Expected: 3 tests pass.

- [ ] **Step 5: Run full test suite to check for regressions**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: override 5 features with preseason profile z-scores in build_master_feature_table() for seasons 2020+"
```

---

## Task 3: Retrain all three models

**Files:**
- Run: `scripts/train_nn_model.py`
- Run: `scripts/train_xgb_model.py`
- Run: `scripts/train_lr_model.py`
- Run: `scripts/weekly_model_eval.py`

No code changes needed — the training scripts call `build_master_feature_table()` automatically.

- [ ] **Step 1: Retrain NN**

```bash
python scripts/train_nn_model.py
```
Expected output: training completes, new version registered as `nn_v14` in `models/model_registry.json`. Training takes ~5–10 min.

- [ ] **Step 2: Retrain XGB**

```bash
python scripts/train_xgb_model.py
```
Expected: `xgb_v8` registered in `models/xgb_registry.json`.

- [ ] **Step 3: Retrain LR**

```bash
python scripts/train_lr_model.py
```
Expected: `lr_v6` registered in `models/lr_registry.json`.

- [ ] **Step 4: Evaluate on 2024 held-out season**

```bash
python scripts/weekly_model_eval.py --season 2024 --week 1 18
```

Check `reports/nn_weekly_accuracy.csv` for the new entry. Compare the new model's 2024 season accuracy to the existing ensemble (nn_v13 + xgb_v7 + lr_v5).

**Go / no-go:**
- New accuracy ≥ old accuracy on 2024 → proceed to Task 4.
- New accuracy within 1% lower → proceed to Task 4 (bug fixes justify deployment).
- New accuracy >2% lower → stop. Do not proceed. Investigate which feature override is hurting predictions before retraining again.

- [ ] **Step 5: Commit model files**

```bash
git add models/model_registry.json models/xgb_registry.json models/lr_registry.json
git add models/nn_v14.keras models/nn_v14_scaler.pkl
git add models/xgb_v8.json models/xgb_v8_scaler.pkl
git add models/lr_v6.pkl models/lr_v6_scaler.pkl
git add reports/nn_weekly_accuracy.csv
git commit -m "feat: retrain NN v14 + XGB v8 + LR v6 with profile z-score feature overrides"
```

---

## Task 4: Re-enable 5 feature overrides in `_precompute_static_features()`

**Files:**
- Modify: `services/nn_projection_engine.py` (lines 209–311)
- Modify: `tests/test_preseason_profiles.py` (append new class)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestPreseasonFeatureOverridesPostRetrain:
    """Verify _precompute_static_features() correctly applies profile z-score overrides."""

    def _make_engine(self, profiles):
        from unittest.mock import patch
        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            from services.nn_projection_engine import NNProjectionEngine
            from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
            engine = NNProjectionEngine()
        base = {"elo_pre": 1500.0, "off_pass_epa_roll": 0.0, "off_rush_epa_roll": 0.0,
                "def_pass_epa_roll": 0.0, "def_rush_epa_roll": 0.0, "margin_roll": 0.0,
                "off_early_roll": 0.0, "def_early_roll": 0.0,
                "turnover_margin_rolling": 0.0, "net_success_rate": 0.0,
                "qb_pressure_roll": 0.0, "def_pressures_roll": 0.0,
                "roster_talent_delta": 0.0, "off_roster_value_delta": 0.0,
                "def_roster_value_delta": 0.0, "st_value_delta": 0.0,
                "qb_resilience_delta": 0.0, "trench_score": 0.0,
                "market_implied_team_total": 22.0, "passing_difficulty_index": 0.0,
                **{c: 0.0 for c in NN_FC}}
        engine._team_profiles = pd.DataFrame([
            {"team": "HOME", **base},
            {"team": "AWAY", **base},
        ])
        engine._preseason_profiles = profiles
        engine._preseason_norm = None
        return engine

    def _get_feat(self, engine, feat_name):
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        sched = pd.DataFrame([{
            "home_team": "HOME", "away_team": "AWAY", "week": 1, "game_type": "REG"
        }])
        feats = engine._precompute_static_features(sched)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        return float(feats["W01_HOME_AWAY"][col_idx[feat_name]])

    def _strong_weak_profiles(self):
        return {
            "HOME": {"dl_perf": 40000.0, "qb_tier": 0.20, "ol_av": 350000.0,
                     "off_pass_epa": 0.25, "def_pass_epa": -0.25,
                     "off_rush_epa": 0.04, "def_rush_epa": -0.03},
            "AWAY": {"dl_perf": 8000.0,  "qb_tier": -0.05, "ol_av": 80000.0,
                     "off_pass_epa": -0.15, "def_pass_epa": 0.15,
                     "off_rush_epa": -0.03, "def_rush_epa": 0.03},
        }

    def test_def_pressure_diff_home_better_dl_is_positive(self):
        engine = self._make_engine(self._strong_weak_profiles())
        assert self._get_feat(engine, "def_pressure_diff") > 0

    def test_qb_pressure_advantage_away_better_dl_is_positive(self):
        profiles = {
            "HOME": {"dl_perf": 8000.0,  "qb_tier": 0.1, "ol_av": 150000.0,
                     "off_pass_epa": 0.0, "def_pass_epa": 0.0,
                     "off_rush_epa": 0.0, "def_rush_epa": 0.0},
            "AWAY": {"dl_perf": 40000.0, "qb_tier": 0.1, "ol_av": 150000.0,
                     "off_pass_epa": 0.0, "def_pass_epa": 0.0,
                     "off_rush_epa": 0.0, "def_rush_epa": 0.0},
        }
        engine = self._make_engine(profiles)
        assert self._get_feat(engine, "qb_pressure_advantage") > 0

    def test_off_roster_value_delta_is_h_minus_a_differential(self):
        engine = self._make_engine(self._strong_weak_profiles())
        # HOME has better QB+OL → positive differential (not just home value)
        val = self._get_feat(engine, "off_roster_value_delta")
        assert val > 0

    def test_roster_talent_delta_uses_all_5_dims(self):
        # HOME better on all 5 dims → positive composite
        engine = self._make_engine(self._strong_weak_profiles())
        assert self._get_feat(engine, "roster_talent_delta") > 0

    def test_fallback_to_zero_when_profiles_empty(self):
        engine = self._make_engine({})
        assert self._get_feat(engine, "def_pressure_diff")     == pytest.approx(0.0, abs=0.01)
        assert self._get_feat(engine, "qb_pressure_advantage") == pytest.approx(0.0, abs=0.01)
        assert self._get_feat(engine, "roster_talent_delta")   == pytest.approx(0.0, abs=0.01)
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonFeatureOverridesPostRetrain -v
```
Expected: FAIL — RETRAIN SPEC comment block doesn't apply any overrides.

- [ ] **Step 3: Replace the RETRAIN SPEC comment block with live code**

In `services/nn_projection_engine.py`, find the RETRAIN SPEC comment block (lines 301–308):

```python
            # RETRAIN SPEC: Override these 5 features with preseason profile z-scores
            # (def_pressure_diff, qb_pressure_advantage, off/def_roster_value_delta,
            # roster_talent_delta) once models are retrained on profile-derived z-scores
            # as feature values for historical seasons (2020–2025). Raw cross-team z-scores
            # from _preseason_profiles can reach ±3 for outlier teams (e.g. elite DL),
            # which pushes current models out of distribution and inverts predictions.
            # The Elo boost in _build_initial_state() already widens the spread correctly.
```

Also find the `static_feats = {}` line and the beginning of the for loop, then add the precomputation block and replace the comment with live override code. The full replacement in `_precompute_static_features()`:

**After `static_feats = {}` (currently line 209), insert the _pp_z precomputation block:**

```python
        static_feats = {}

        # Precompute profile z-scores once — used inside the per-game loop to override
        # 5 quality features. Models are now trained on these z-scores so the scale is correct.
        _pp_z: dict = {}
        if self._preseason_profiles:
            _pp_dims = ["dl_perf", "qb_tier", "ol_av", "off_pass_epa", "def_pass_epa"]
            _pp_vals = {
                d: [float(v.get(d, 0.0)) for v in self._preseason_profiles.values()]
                for d in _pp_dims
            }
            _pp_mu  = {d: float(np.mean(vs)) for d, vs in _pp_vals.items()}
            _pp_sig = {d: max(float(np.std(vs)), 1e-6) for d, vs in _pp_vals.items()}
            for team, pp in self._preseason_profiles.items():
                _pp_z[team] = {
                    d: (float(pp.get(d, _pp_mu[d])) - _pp_mu[d]) / _pp_sig[d]
                    for d in _pp_dims
                }
```

**Replace the RETRAIN SPEC comment (lines 301–308) with:**

```python
            # Override 5 features with profile z-scores — formulas match build_master_feature_table()
            if _pp_z and ht in _pp_z and at in _pp_z:
                hz, az = _pp_z[ht], _pp_z[at]
                feat[col_idx["def_pressure_diff"]]      = float(hz["dl_perf"] - az["dl_perf"])
                feat[col_idx["qb_pressure_advantage"]]  = float(az["dl_perf"] - hz["dl_perf"])
                feat[col_idx["off_roster_value_delta"]] = float(
                    (0.7 * hz["qb_tier"] + 0.3 * hz["ol_av"])
                    - (0.7 * az["qb_tier"] + 0.3 * az["ol_av"])
                )
                feat[col_idx["def_roster_value_delta"]] = float(
                    (0.6 * hz["dl_perf"] + 0.4 * (-hz["def_pass_epa"]))
                    - (0.6 * az["dl_perf"] + 0.4 * (-az["def_pass_epa"]))
                )
                h_q = (hz["qb_tier"] + hz["off_pass_epa"] + (-hz["def_pass_epa"])
                       + hz["dl_perf"] + hz["ol_av"]) / 5.0
                a_q = (az["qb_tier"] + az["off_pass_epa"] + (-az["def_pass_epa"])
                       + az["dl_perf"] + az["ol_av"]) / 5.0
                feat[col_idx["roster_talent_delta"]] = float(h_q - a_q)
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonFeatureOverridesPostRetrain -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add services/nn_projection_engine.py tests/test_preseason_profiles.py
git commit -m "feat: re-enable 5 preseason feature overrides in _precompute_static_features() with corrected h-a formulas"
```

---

## Task 5: Backfill 2026 predictions + verify

**Files:** No code changes — scripts only.

- [ ] **Step 1: Dry-run predict_season to gate before writing**

```bash
python scripts/predict_season.py --season 2026 --simulations 2000 --dry-run
```

**Gate — all three must pass before proceeding:**
1. LA Rams rank top-5
2. Win range ≥ 10 wide (max projected wins − min projected wins ≥ 10)
3. No team > 16 projected wins or < 2 projected wins

If the gate fails, stop and investigate. The most likely cause is a formula mismatch between the training override and the inference override.

- [ ] **Step 2: Run for real and backfill game predictions**

```bash
python scripts/predict_season.py --season 2026
python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --firestore
```

- [ ] **Step 3: Rebuild cache and refresh local**

```bash
python scripts/cache_builder.py --year 2026 --force
python scripts/refresh_local_pkls.py
```

- [ ] **Step 4: Commit + push**

```bash
git add reports/nn_weekly_accuracy.csv
git commit -m "chore: post-retrain 2026 predictions verified and cache rebuilt"
git push origin main
```
