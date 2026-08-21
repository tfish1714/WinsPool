# Preseason Simulation Spread Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Wire preseason player profiles into the Elo initial state and 5 static model features so the 2026 win projection spread widens to 3–14 wins and reflects actual 2026 roster moves (trades like Garrett, Parsons).

**Architecture:** (1) Two new constants control a profile-composite Elo boost applied at simulation start; (2) five static features in `_precompute_static_features()` are overridden with profile z-scores when profiles are available; (3) the stale `elo_predictions` / `prediction_snapshot` path is removed from the draft board, leaving `preseason_predictions` as the single win projection.

**Tech Stack:** Python, NumPy, pandas, FastAPI, Vanilla JS. No model changes, no new data sources.

---

## File Map

| File | Change |
|---|---|
| `services/constants.py` | Add `PRESEASON_ELO_BOOST_MAX`, `PRESEASON_ELO_WEIGHTS` |
| `services/nn_projection_engine.py` | Elo boost in `_build_initial_state()`; feature overrides in `_precompute_static_features()` |
| `tests/test_preseason_profiles.py` | New test classes for Elo boost and static feature overrides |
| `services/draft_service.py` | Remove `elo_predictions` block and state key |
| `static/js/ui_renderer.js` | Remove `eloPredictions` params and "NN Proj" rendering |
| `static/js/main.js` | Remove `elo_predictions` from WebSocket state destructure and renderer calls |

---

## Key Data Facts

**Profile dimensions and their sign convention (preseason profiles output):**
- `off_pass_epa`, `off_rush_epa` — higher = better offense (already mean-centered)
- `def_pass_epa`, `def_rush_epa` — lower = better defense (already mean-centered, must flip sign)
- `qb_tier` — QB pass_epa_rate (~0.05–0.25 range), higher = better
- `ol_av` — raw OL snap×age score (~100K–400K), higher = better
- `dl_perf` — raw DL composite sack/pressure score (~5K–50K), higher = better

**`_precompute_static_features()` reads from `profile_dict`** which is built from `self._team_profiles` — the 2025 rolling averages. The feature overrides inject `_preseason_profiles` values into 5 specific features while leaving all others on 2025 data.

**`_build_initial_state()` state layout:**
- dim 0: Elo (float, ~1300–1700)
- dims 1–4: EPA state (set from `_preseason_profiles` in existing code)
- dim 5: margin_roll

---

## Task 1: New Constants

**Files:**
- Modify: `services/constants.py`

- [x] **Step 1: Add constants**

Append to `services/constants.py` after the MC constants block:

```python
# Preseason simulation spread tuning.
# PRESEASON_ELO_BOOST_MAX: maximum Elo points added/subtracted based on profile quality.
# ±150 gives top-profiled teams ~13 projected wins and bottom-profiled ~4.
# Increase to widen the win band, decrease to narrow it.
PRESEASON_ELO_BOOST_MAX = 150

# Weights for the profile composite that drives the Elo adjustment.
# Defensive dimensions (def_pass_epa, def_rush_epa) are sign-flipped before
# weighting so a better defense contributes positively to the composite.
PRESEASON_ELO_WEIGHTS = {
    "qb_tier":      0.30,
    "off_pass_epa": 0.20,
    "def_pass_epa": 0.20,
    "dl_perf":      0.15,
    "ol_av":        0.10,
    "off_rush_epa": 0.03,
    "def_rush_epa": 0.02,
}
```

- [x] **Step 2: Add to imports in `nn_projection_engine.py`**

In `services/nn_projection_engine.py`, find the existing constants import (line ~11–15):

```python
from services.constants import (
    UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT,
    PROB_CLIP_MIN, PROB_CLIP_MAX, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE,
    MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT,
)
```

Replace with:

```python
from services.constants import (
    UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT,
    PROB_CLIP_MIN, PROB_CLIP_MAX, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE,
    MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT,
    PRESEASON_ELO_BOOST_MAX, PRESEASON_ELO_WEIGHTS,
)
```

- [x] **Step 3: Commit**

```bash
git add services/constants.py services/nn_projection_engine.py
git commit -m "feat: add PRESEASON_ELO_BOOST_MAX and PRESEASON_ELO_WEIGHTS constants"
```

---

## Task 2: Profile-to-Elo Boost in `_build_initial_state()`

**Files:**
- Modify: `services/nn_projection_engine.py` (lines ~157–167)
- Modify: `tests/test_preseason_profiles.py`

- [x] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestPreseasonEloBoost:
    def _make_engine(self, profiles):
        from unittest.mock import patch
        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            from services.nn_projection_engine import NNProjectionEngine
            from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
            engine = NNProjectionEngine()
        engine._team_profiles = pd.DataFrame([
            {"team": "GOOD", "elo_pre": 1500.0, "off_pass_epa_roll": 0.0,
             "off_rush_epa_roll": 0.0, "def_pass_epa_roll": 0.0,
             "def_rush_epa_roll": 0.0, "margin_roll": 0.0, **{c: 0.0 for c in NN_FC}},
            {"team": "BAD",  "elo_pre": 1500.0, "off_pass_epa_roll": 0.0,
             "off_rush_epa_roll": 0.0, "def_pass_epa_roll": 0.0,
             "def_rush_epa_roll": 0.0, "margin_roll": 0.0, **{c: 0.0 for c in NN_FC}},
        ])
        engine._preseason_profiles = profiles
        engine._preseason_norm = None
        return engine

    def test_strong_profile_boosts_elo_above_base(self):
        from services.nn_projection_engine import NNProjectionEngine
        profiles = {
            "GOOD": {"qb_tier": 0.25, "off_pass_epa": 0.3, "def_pass_epa": -0.3,
                     "dl_perf": 50000.0, "ol_av": 400000.0,
                     "off_rush_epa": 0.05, "def_rush_epa": -0.05},
            "BAD":  {"qb_tier": -0.25, "off_pass_epa": -0.3, "def_pass_epa": 0.3,
                     "dl_perf": 5000.0, "ol_av": 50000.0,
                     "off_rush_epa": -0.05, "def_rush_epa": 0.05},
        }
        engine = self._make_engine(profiles)
        state, _, team_idx = engine._build_initial_state()
        assert state[team_idx["GOOD"], 0] > 1500.0
        assert state[team_idx["BAD"],  0] < 1500.0

    def test_good_team_elo_strictly_higher_than_bad_team(self):
        profiles = {
            "GOOD": {"qb_tier": 0.2, "off_pass_epa": 0.2, "def_pass_epa": -0.2,
                     "dl_perf": 30000.0, "ol_av": 300000.0,
                     "off_rush_epa": 0.03, "def_rush_epa": -0.02},
            "BAD":  {"qb_tier": -0.1, "off_pass_epa": -0.1, "def_pass_epa": 0.1,
                     "dl_perf": 8000.0, "ol_av": 80000.0,
                     "off_rush_epa": -0.03, "def_rush_epa": 0.02},
        }
        engine = self._make_engine(profiles)
        state, _, team_idx = engine._build_initial_state()
        assert state[team_idx["GOOD"], 0] > state[team_idx["BAD"], 0]

    def test_elo_boost_bounded_by_boost_max(self):
        from services.constants import PRESEASON_ELO_BOOST_MAX
        profiles = {
            "GOOD": {"qb_tier": 999.0, "off_pass_epa": 999.0, "def_pass_epa": -999.0,
                     "dl_perf": 9999999.0, "ol_av": 9999999.0,
                     "off_rush_epa": 999.0, "def_rush_epa": -999.0},
            "BAD":  {"qb_tier": -999.0, "off_pass_epa": -999.0, "def_pass_epa": 999.0,
                     "dl_perf": 0.001, "ol_av": 0.001,
                     "off_rush_epa": -999.0, "def_rush_epa": 999.0},
        }
        engine = self._make_engine(profiles)
        state, _, team_idx = engine._build_initial_state()
        assert state[team_idx["GOOD"], 0] <= 1500.0 + PRESEASON_ELO_BOOST_MAX + 0.01
        assert state[team_idx["BAD"],  0] >= 1500.0 - PRESEASON_ELO_BOOST_MAX - 0.01

    def test_no_boost_when_profiles_empty(self):
        engine = self._make_engine({})
        state, _, team_idx = engine._build_initial_state()
        assert state[team_idx["GOOD"], 0] == pytest.approx(1500.0)
        assert state[team_idx["BAD"],  0] == pytest.approx(1500.0)
```

- [x] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonEloBoost -v
```
Expected: FAIL (4 tests, strong/bad test fails because no boost applied yet)

- [x] **Step 3: Implement the Elo boost**

In `services/nn_projection_engine.py`, find the existing preseason profile block in `_build_initial_state()`:

```python
        # Override EPA dims 1-4 with bottom-up preseason player profiles when available
        if self._preseason_profiles:
            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                if pp:
                    state_template[idx, 1] = float(pp.get("off_pass_epa", state_template[idx, 1]))
                    state_template[idx, 2] = float(pp.get("off_rush_epa", state_template[idx, 2]))
                    state_template[idx, 3] = float(pp.get("def_pass_epa", state_template[idx, 3]))
                    state_template[idx, 4] = float(pp.get("def_rush_epa", state_template[idx, 4]))

        return state_template, team_list, team_idx
```

Replace with:

```python
        # Override EPA dims 1-4 with bottom-up preseason player profiles when available
        if self._preseason_profiles:
            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                if pp:
                    state_template[idx, 1] = float(pp.get("off_pass_epa", state_template[idx, 1]))
                    state_template[idx, 2] = float(pp.get("off_rush_epa", state_template[idx, 2]))
                    state_template[idx, 3] = float(pp.get("def_pass_epa", state_template[idx, 3]))
                    state_template[idx, 4] = float(pp.get("def_rush_epa", state_template[idx, 4]))

            # Profile composite → Elo boost: widen preseason spread to match SB odds.
            # Algorithm: z-score each profile dim across all teams, flip sign for
            # defensive dims (lower EPA allowed = better), compute weighted composite,
            # clip to ±2σ and scale to ±PRESEASON_ELO_BOOST_MAX.
            _dims  = list(PRESEASON_ELO_WEIGHTS.keys())
            _def_d = {"def_pass_epa", "def_rush_epa"}
            _vals  = {
                d: [float(self._preseason_profiles.get(t, {}).get(d, 0.0)) for t in team_list]
                for d in _dims
            }
            _mu  = {d: float(np.mean(v)) for d, v in _vals.items()}
            _sig = {d: max(float(np.std(v)), 1e-6) for d, v in _vals.items()}

            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                composite = 0.0
                for d, w in PRESEASON_ELO_WEIGHTS.items():
                    z = (float(pp.get(d, _mu[d])) - _mu[d]) / _sig[d]
                    if d in _def_d:
                        z = -z
                    composite += w * z
                elo_adj = float(np.clip(composite, -2.0, 2.0) / 2.0 * PRESEASON_ELO_BOOST_MAX)
                state_template[idx, 0] += elo_adj

        return state_template, team_list, team_idx
```

- [x] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonEloBoost -v
```
Expected: PASS (4 tests)

- [x] **Step 5: Commit**

```bash
git add services/nn_projection_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add profile-composite Elo boost to _build_initial_state()"
```

---

## Task 3: Static Feature Overrides in `_precompute_static_features()`

**Files:**
- Modify: `services/nn_projection_engine.py` (lines ~182–278)
- Modify: `tests/test_preseason_profiles.py`

- [x] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestPreseasonStaticFeatureOverrides:
    def _make_engine(self, profiles):
        from unittest.mock import patch
        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            from services.nn_projection_engine import NNProjectionEngine
            from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
            engine = NNProjectionEngine()
        # Minimal _team_profiles — all quality fields 0 so profile overrides are detectable
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
            "AWAY": {"dl_perf": 8000.0, "qb_tier": -0.05, "ol_av": 80000.0,
                     "off_pass_epa": -0.15, "def_pass_epa": 0.15,
                     "off_rush_epa": -0.03, "def_rush_epa": 0.03},
        }

    def test_def_pressure_diff_home_better_dl_is_positive(self):
        engine = self._make_engine(self._strong_weak_profiles())
        assert self._get_feat(engine, "def_pressure_diff") > 0

    def test_qb_pressure_advantage_away_better_dl_is_positive(self):
        # Flip: AWAY has better DL → more pressure on HOME QB → positive advantage
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

    def test_off_roster_value_delta_reflects_qb_and_ol(self):
        engine = self._make_engine(self._strong_weak_profiles())
        # HOME has strong QB + OL → positive off_roster_value_delta
        assert self._get_feat(engine, "off_roster_value_delta") > 0

    def test_def_roster_value_delta_reflects_dl_and_def_epa(self):
        engine = self._make_engine(self._strong_weak_profiles())
        # HOME has strong DL + def EPA → positive def_roster_value_delta
        assert self._get_feat(engine, "def_roster_value_delta") > 0

    def test_roster_talent_delta_home_better_is_positive(self):
        engine = self._make_engine(self._strong_weak_profiles())
        assert self._get_feat(engine, "roster_talent_delta") > 0

    def test_fallback_to_zero_when_profiles_empty(self):
        engine = self._make_engine({})
        # All _team_profiles quality fields are 0 → features should be 0
        assert self._get_feat(engine, "def_pressure_diff")    == pytest.approx(0.0, abs=0.01)
        assert self._get_feat(engine, "qb_pressure_advantage") == pytest.approx(0.0, abs=0.01)
        assert self._get_feat(engine, "roster_talent_delta")  == pytest.approx(0.0, abs=0.01)
```

- [x] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonStaticFeatureOverrides -v
```
Expected: FAIL (6 tests — features still read 2025 data)

- [x] **Step 3: Add z-score precomputation before the per-game loop**

In `services/nn_projection_engine.py`, find `_precompute_static_features()`. Find the line:

```python
        static_feats = {}
```

Insert AFTER that line (before the `for _, game in schedule_df.iterrows():` loop):

```python
        # Precompute profile z-scores once — used inside the per-game loop to replace
        # five quality features. Computed here to avoid repeating across 272 game iterations.
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

- [x] **Step 4: Add feature overrides inside the per-game loop**

Still in `_precompute_static_features()`, find the block near the end of the per-game loop body:

```python
            # Roster value deltas (home-centric signed features from prior season)
            feat[col_idx["roster_talent_delta"]]     = (
                float(hp.get("roster_talent_delta", 0.0)) - float(ap.get("roster_talent_delta", 0.0))
            )
            feat[col_idx["off_roster_value_delta"]]  = float(hp.get("off_roster_value_delta", 0.0))
            feat[col_idx["def_roster_value_delta"]]  = float(hp.get("def_roster_value_delta", 0.0))
            feat[col_idx["st_value_delta"]]          = float(hp.get("st_value_delta", 0.0))
            feat[col_idx["qb_resilience_delta"]]     = float(hp.get("qb_resilience_delta", 0.0))

            static_feats[key] = feat
```

Replace with:

```python
            # Roster value deltas (home-centric signed features from prior season)
            feat[col_idx["roster_talent_delta"]]     = (
                float(hp.get("roster_talent_delta", 0.0)) - float(ap.get("roster_talent_delta", 0.0))
            )
            feat[col_idx["off_roster_value_delta"]]  = float(hp.get("off_roster_value_delta", 0.0))
            feat[col_idx["def_roster_value_delta"]]  = float(hp.get("def_roster_value_delta", 0.0))
            feat[col_idx["st_value_delta"]]          = float(hp.get("st_value_delta", 0.0))
            feat[col_idx["qb_resilience_delta"]]     = float(hp.get("qb_resilience_delta", 0.0))

            # Override 5 quality features with profile-derived z-scores when available.
            # def_pressure_diff / qb_pressure_advantage use dl_perf (DL pass rush quality).
            # off/def_roster_value use qb_tier+ol_av and dl_perf+def_pass_epa composites.
            # roster_talent_delta uses overall EPA + qb composite.
            # Note: off/def_roster_value are home-only to match training data — see retrain spec.
            if _pp_z and ht in _pp_z and at in _pp_z:
                hz, az = _pp_z[ht], _pp_z[at]
                feat[col_idx["def_pressure_diff"]]     = float(hz["dl_perf"] - az["dl_perf"])
                feat[col_idx["qb_pressure_advantage"]] = float(az["dl_perf"] - hz["dl_perf"])
                feat[col_idx["off_roster_value_delta"]] = float(
                    0.7 * hz["qb_tier"] + 0.3 * hz["ol_av"]
                )
                feat[col_idx["def_roster_value_delta"]] = float(
                    0.6 * hz["dl_perf"] + 0.4 * (-hz["def_pass_epa"])
                )
                h_q = (hz["off_pass_epa"] + (-hz["def_pass_epa"]) + hz["qb_tier"]) / 3.0
                a_q = (az["off_pass_epa"] + (-az["def_pass_epa"]) + az["qb_tier"]) / 3.0
                feat[col_idx["roster_talent_delta"]] = float(h_q - a_q)

            static_feats[key] = feat
```

- [x] **Step 5: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonStaticFeatureOverrides -v
```
Expected: PASS (6 tests)

- [x] **Step 6: Run all preseason profile tests**

```
pytest tests/test_preseason_profiles.py -v
```
Expected: PASS (all tests, no regressions)

- [x] **Step 7: Commit**

```bash
git add services/nn_projection_engine.py tests/test_preseason_profiles.py
git commit -m "feat: replace 5 static features with preseason profile z-scores in _precompute_static_features()"
```

---

## Task 4: Draft Board Cleanup

**Files:**
- Modify: `services/draft_service.py`
- Modify: `static/js/ui_renderer.js`
- Modify: `static/js/main.js`

No unit tests — verify manually that the draft page renders correctly after.

- [x] **Step 1: Remove `elo_predictions` from `draft_service.py`**

Find and remove this entire block (lines ~126–139):

```python
    # Elo-based projections (Sourced from CACHE to eliminate draft lag)
    elo_predictions = {}
    try:
        from services.cache_service import get_cached
        t2 = time.time()
        # Source the pre-calculated Week 0 prediction snapshot
        snapshot = get_cached('prediction_snapshot', int(season), 0)
        if snapshot and 'team_projections' in snapshot:
            elo_predictions = snapshot['team_projections']
            logger.debug("draft_state: loaded %d cached Elo projections for %s Week 0 (%.3fs)", len(elo_predictions), season, time.time() - t2)
        else:
            logger.warning("draft_state: no cached 'prediction_snapshot' found for %s Week 0. elo_predictions is EMPTY.", season)
    except Exception as e:
        logger.exception("Unhandled error in WebSocket draft handler")
```

Also remove `"elo_predictions": elo_predictions,` from the state dict (line ~183):

```python
    state = {
        "season": int(season), "available_seasons": available_seasons,
        "draft_board": draft_board, "active_pick": active_pick,
        "available_teams": available_teams, "draft_ready": True,
        "connected_players": list(connected_players), "all_players": all_players_info,
        "pick_start_time": pick_start_time, "preseason_predictions": preseason_predictions,
        "team_schedules": team_schedules,
    }
```

- [x] **Step 2: Update `renderDraftBoard` in `ui_renderer.js`**

Find:
```javascript
    renderDraftBoard(board, activePick, myPlayerId, draftSummary, totalPlayers = 10, role = null, preseasonPredictions = null, eloPredictions = null) {
```

Replace with:
```javascript
    renderDraftBoard(board, activePick, myPlayerId, draftSummary, totalPlayers = 10, role = null, preseasonPredictions = null) {
```

Find and remove the "NN: XXW" line inside the function:
```javascript
                                    ${eloPredictions && eloPredictions[item.team] !== undefined ? `<span style="color:var(--accent-blue);">NN: ${eloPredictions[item.team]}W</span>` : ''}
```

- [x] **Step 3: Update `renderTeamGrid` in `ui_renderer.js`**

Find:
```javascript
    renderTeamGrid(teams, selectedTeam, role, predictions, schedules, eloPredictions) {
```

Replace with:
```javascript
    renderTeamGrid(teams, selectedTeam, role, predictions, schedules) {
```

Find and remove:
```javascript
            const eloPred = (role === 'admin' && eloPredictions) ? eloPredictions[team] : null;
```

Find and remove the `nnHtml` block:
```javascript
            let nnHtml = '';
            if (eloPred !== null && eloPred !== undefined) {
                nnHtml = `<div class="nn-projection" style="font-size:0.7rem; color:var(--accent-blue, #60a5fa); margin-top:2px;" title="Neural Network + Monte Carlo Projection">NN Proj: ${eloPred}W</div>`;
            }
```

Find and remove `${nnHtml}` from the return template:
```javascript
                        ${predHtml}
                        ${nnHtml}
```

Replace with:
```javascript
                        ${predHtml}
```

- [x] **Step 4: Simplify `renderAdminPortfolio` in `ui_renderer.js`**

Replace the entire `renderAdminPortfolio` function with a version that uses only `preseasonPredictions`:

```javascript
    renderAdminPortfolio(draftBoard, allPlayers, preseasonPredictions) {
        const container = document.getElementById('admin-portfolio-content');
        if (!container) return;

        if (!allPlayers || allPlayers.length === 0) {
            container.innerHTML = '<p>No players available.</p>';
            return;
        }

        const activePlayerIds = new Set(draftBoard.map(pick => String(pick.playerId)));
        const portfolios = {};
        allPlayers.forEach(p => {
            if (!activePlayerIds.has(String(p.playerId))) return;
            portfolios[p.playerId] = { playerName: p.playerName, teams: [], totalBase: 0.0 };
        });

        draftBoard.forEach(pick => {
            if (pick.team && portfolios[pick.playerId]) {
                const team = pick.team;
                let baseWins = 0;
                if (preseasonPredictions && preseasonPredictions[team]) {
                    baseWins = typeof preseasonPredictions[team] === 'object'
                        ? parseFloat(preseasonPredictions[team].projected_wins)
                        : parseFloat(preseasonPredictions[team]);
                }
                portfolios[pick.playerId].teams.push({ team, base: baseWins });
                portfolios[pick.playerId].totalBase += baseWins;
            }
        });

        const sorted = Object.values(portfolios).sort((a, b) => b.totalBase - a.totalBase);

        let html = `
            <table class="wins-table" style="width:100%; border-collapse:collapse; text-align:left;">
                <thead>
                    <tr>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border);">Player</th>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border);">Teams</th>
                        <th style="padding:0.5rem; border-bottom:1px solid var(--glass-border); text-align:right;">Projected Wins</th>
                    </tr>
                </thead>
                <tbody>
        `;

        sorted.forEach(p => {
            const teamStrings = p.teams.map(t =>
                `<span style="display:inline-block; padding:2px 6px; background:rgba(255,255,255,0.1); border-radius:4px; margin:2px; font-size:0.8rem;">
                    <img src="${this.getTeamLogo(t.team)}" style="width:14px;height:14px;vertical-align:middle;margin-right:4px;">${t.team} (${t.base.toFixed(1)}W)
                </span>`
            ).join('');
            html += `
                <tr>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05); font-weight:bold;">${p.playerName}</td>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05);">${teamStrings || '<span style="color:#666;">No teams yet</span>'}</td>
                    <td style="padding:0.5rem; border-bottom:1px solid rgba(255,255,255,0.05); text-align:right; font-weight:bold;">${p.totalBase.toFixed(1)}</td>
                </tr>
            `;
        });

        html += `</tbody></table>`;
        container.innerHTML = html;
    },
```

- [x] **Step 5: Update `main.js` — remove `elo_predictions` from destructure and calls**

Find:
```javascript
        const { active_pick, draft_board, available_teams, draft_ready, preseason_predictions, team_schedules, season, elo_predictions } = state;
```

Replace with:
```javascript
        const { active_pick, draft_board, available_teams, draft_ready, preseason_predictions, team_schedules, season } = state;
```

Find:
```javascript
        UiRenderer.renderDraftBoard(draft_board, active_pick, this.user.playerId, this.draftSummary, state.all_players ? state.all_players.length : 10, this.user.role, preseason_predictions, elo_predictions);
```

Replace with:
```javascript
        UiRenderer.renderDraftBoard(draft_board, active_pick, this.user.playerId, this.draftSummary, state.all_players ? state.all_players.length : 10, this.user.role, preseason_predictions);
```

Find:
```javascript
            UiRenderer.renderAdminPortfolio(draft_board, state.all_players, elo_predictions, preseason_predictions);
```

Replace with:
```javascript
            UiRenderer.renderAdminPortfolio(draft_board, state.all_players, preseason_predictions);
```

Find:
```javascript
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules, elo_predictions);
```

Replace with:
```javascript
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules);
```

- [x] **Step 6: Commit**

```bash
git add services/draft_service.py static/js/ui_renderer.js static/js/main.js
git commit -m "feat: remove elo_predictions from draft board; preseason_predictions is now the single win projection"
```

---

## Task 5: End-to-End Validation + Push

**Files:** No code changes — validation only.

- [x] **Step 1: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing, no regressions

- [x] **Step 2: Run predict_season dry-run**

```
python scripts/predict_season.py --season 2026 --simulations 2000 --dry-run
```

Check:
- Win range ≥ 10 points wide (e.g., 3–13 or 4–14)
- No team exceeds 16 wins or falls below 2 wins
- LA Rams and GB Packers both rank top-10 (Garrett + Parsons effect)
- StdDev ≥ 2.5 for most teams

If the range is still too narrow, increase `PRESEASON_ELO_BOOST_MAX` in `constants.py` (try 175 or 200). If the range is too extreme (teams at 1–2 or 16+), decrease it (try 100 or 125).

- [x] **Step 3: Run predict_season for real and backfill game predictions**

```
python scripts/predict_season.py --season 2026
python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --firestore
```

- [x] **Step 4: Rebuild analytics cache and refresh local**

```
python scripts/cache_builder.py --year 2026 --force
python scripts/refresh_local_pkls.py
```

- [x] **Step 5: Push**

```bash
git push origin main
```
