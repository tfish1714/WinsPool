# Preseason Simulation Spread — Design Spec
**Date:** 2026-06-03
**Status:** Approved

---

## Problem

The preseason win projection simulation compresses win totals into a 5–11 range, far narrower than the real NFL (3–15). Two root causes:

1. **Elo too narrow at initialization.** The best team's Elo is only ~75 points above the league mean, producing a 3-point spread equivalent and a 60% win probability — averaging ~10.2 wins regardless of roster quality.
2. **Static features locked to 2025 rolling averages.** Features like `def_pressure_diff`, `qb_pressure_advantage`, and the roster value deltas never see the 2026 player profiles we already built, so trades (Myles Garrett to LA, Micah Parsons to GB) are invisible to those features.

The preseason profiles (`compute_preseason_player_profiles()`) already compute accurate 2026 estimates using 2025 individual player EPA stats on the current 2026 depth chart. They connect to only 2 of 26 model features (initial EPA state, trench metric). This spec wires them into 5 more features and uses them to drive a profile-adjusted Elo that produces a realistic 3–14 win spread.

---

## Scope

**Preseason only** — applies when `_preseason_profiles` is set (i.e., before any 2026 games are played). Once games start, the in-season update spec (2026-06-02-inseason-weekly-profile-updates-design.md) takes over. No in-season prediction logic changes.

---

## Architecture

Three files change:

| File | Change |
|---|---|
| `services/constants.py` | Add `PRESEASON_ELO_BOOST_MAX` and `PRESEASON_ELO_WEIGHTS` |
| `services/nn_projection_engine.py` | Profile-to-Elo in `_build_initial_state()`; feature replacements in `_precompute_static_features()` |
| `services/draft_service.py` + `routes/draft_routes.py` + `static/js/main.js` + `static/js/ui_renderer.js` | Remove `elo_predictions` / `prediction_snapshot` path from draft board |

`scripts/cache_builder.py` is **out of scope** — the `prediction_snapshot` path it writes is being removed, so the schedule-dedup and `get_team_projected_wins()` fixes become irrelevant.

---

## Section 1: New Constants

```python
# services/constants.py

# Maximum Elo points added/subtracted based on preseason profile quality.
# ±150 gives top-profiled teams ~13 projected wins and bottom-profiled ~4.
PRESEASON_ELO_BOOST_MAX = 150

# Weights for the profile composite used to compute the Elo adjustment.
# Defensive dims (def_pass_epa, def_rush_epa) are sign-flipped before weighting
# so that better defenses contribute positively.
PRESEASON_ELO_WEIGHTS = {
    "qb_tier":      0.30,
    "off_pass_epa": 0.20,
    "def_pass_epa": 0.20,   # sign-flipped (lower = better)
    "dl_perf":      0.15,
    "ol_av":        0.10,
    "off_rush_epa": 0.03,
    "def_rush_epa": 0.02,   # sign-flipped (lower = better)
}
```

`PRESEASON_ELO_BOOST_MAX` is the primary tuning knob — increase it to widen the win band, decrease to narrow.

---

## Section 2: Profile-to-Elo Adjustment (`_build_initial_state()`)

Runs after the base state is built from `_team_profiles`, only when `_preseason_profiles` is non-empty.

**Algorithm:**

1. **Z-score each profile dimension** across all 32 teams (mean=0, std=1). This normalizes `qb_tier`, `ol_av`, and `dl_perf` (raw scales of 10K–400K) onto the same footing as the already-normalized EPA values.

2. **Flip sign for defensive dimensions.** `def_pass_epa` and `def_rush_epa` are lower-is-better. After z-scoring, negate them so a stronger defense contributes a positive composite score.

3. **Weighted composite.** `composite = sum(weight[dim] * z_score[dim])` using `PRESEASON_ELO_WEIGHTS`.

4. **Map to Elo adjustment.** Clip composite to ±2σ, divide by 2, multiply by `PRESEASON_ELO_BOOST_MAX`:
   `elo_adj = clip(composite, -2, 2) / 2 * PRESEASON_ELO_BOOST_MAX`

5. **Apply.** `state[team_idx, 0] += elo_adj`

**Expected effect at default settings:**
- Best-profiled team: +150 Elo → ~1725 → 9pt spread over average → 77% win prob → ~13.1 wins
- Worst-profiled team: −150 Elo → ~1325 → −9pt spread → 23% win prob → ~3.9 wins
- Range: ~3–14 wins (matching real NFL seasons)

---

## Section 3: Static Feature Replacements (`_precompute_static_features()`)

When `_preseason_profiles` is set, five feature groups are replaced with profile-derived values. All use z-scores computed across the same 32-team profile set. Defensive dimensions are sign-flipped where noted.

### 3a. `def_pressure_diff`
- **Old:** `_team_profiles` `def_pressures_roll` (home − away)
- **New:** `dl_perf` z-score (home − away)
- Reflects actual 2026 DL pass rush quality (correctly places Garrett on LA, Parsons on GB)

### 3b. `qb_pressure_advantage`
- **Old:** `_team_profiles` `qb_pressure_roll` (away − home)
- **New:** away `dl_perf` z-score − home `dl_perf` z-score
- Measures how much pressure the away team's DL puts on the home QB vs. vice versa

### 3c. `off_roster_value_delta`
- **Old:** 2025 `off_roster_value_delta` from `_team_profiles`
- **New:** `0.7 * qb_tier_z + 0.3 * ol_av_z` (home team only)
- QB quality dominates offensive roster value; OL quality contributes secondarily
- **Known debt:** This feature is home-team only in the training data (the name "delta" is misleading — it was week-over-week change, not home vs. away). The correct design is a `hp - ap` differential, but fixing it requires retraining the model. Tracked for the model retraining spec.

### 3d. `def_roster_value_delta`
- **Old:** 2025 `def_roster_value_delta` from `_team_profiles`
- **New:** `0.6 * dl_perf_z + 0.4 * (-def_pass_epa_z)` (home team only)
- DL pass rush quality + overall defensive efficiency
- **Known debt:** Same home-only limitation as 3c — to be fixed when retraining the model.

### 3e. `roster_talent_delta`
- **Old:** 2025 `roster_talent_delta` from `_team_profiles`
- **New:** `(off_pass_epa_z + (-def_pass_epa_z) + qb_tier_z) / 3` (home − away)
- Overall team quality matchup differential

### Features intentionally kept as 2025 data
The following have no clean profile equivalent and stay unchanged:

| Feature | Reason |
|---|---|
| `turnover_margin_rolling` | Turnovers are largely random; no roster-level signal |
| `net_success_rate` | Scheme and coaching dependent |
| `qb_resilience_delta` | Injury-history signal, not roster quality |
| `early_down_matchup` | Coaching/play-calling tendency, not talent |

---

## Section 4: Draft Board Cleanup

**Remove `elo_predictions` entirely** — this was a legacy path reading from `prediction_snapshot` via `cache_builder.py`. It used `get_team_projected_wins()` which called the per-game `game_win_probability()` method rather than `simulate_season()`, so it never reflected preseason profiles and always showed stale values.

### `services/draft_service.py`
- Delete the `elo_predictions = {}` block and the entire try/except that reads from `prediction_snapshot`
- Remove `elo_predictions` from the WebSocket state dict

### `routes/draft_routes.py`
- Remove any `elo_predictions` references

### `static/js/main.js`
- Remove `elo_predictions` from the destructured WebSocket state
- Remove all calls that pass `elo_predictions` to renderer functions

### `static/js/ui_renderer.js`
- Remove the "NN: XXW" label from draft board team cards and admin portfolio
- The "Base: XXW" label from `preseason_predictions` is the single win projection shown

The `preseason_predictions` collection (written by `predict_season.py`) already uses `simulate_season()` with the correct preseason profiles and is the authoritative projection.

---

## Data Flow After This Change

```
compute_preseason_player_profiles(2026)
    ↓
NNProjectionEngine.initialize(2026)
    ↓ stores _preseason_profiles
_build_initial_state()
    ├── dims 1-4: off/def EPA from profiles (existing)
    └── dim 0: base Elo + profile composite adjustment (NEW)
_precompute_static_features()
    ├── trench_dominance_metric: ol_av + dl_perf z-scores (existing)
    ├── def_pressure_diff: dl_perf z-scores (NEW)
    ├── qb_pressure_advantage: dl_perf z-scores (NEW)
    ├── off_roster_value_delta: qb_tier + ol_av composite (NEW)
    ├── def_roster_value_delta: dl_perf + def_pass_epa composite (NEW)
    └── roster_talent_delta: epa + qb_tier composite (NEW)
simulate_season() → predict_season.py → preseason_predictions (Firestore)
draft_service.py → preseason_predictions only → draft board "Base: XXW"
```

---

## Testing

- After implementation, run `predict_season.py --season 2026 --simulations 5000 --dry-run`
- **Pass criteria:**
  - Win range ≥ 10 points wide (e.g., 3–13 or 4–14)
  - Standard deviation ≥ 2.5 for most teams
  - LA Rams and GB Packers ranked top-10 defensively (Garrett/Parsons effect)
  - No team exceeds 16 wins or goes below 2 wins
- Run `pytest tests/test_preseason_profiles.py` — all existing tests pass with no regressions

---

## Out of Scope

- In-season profile updates (see 2026-06-02-inseason-weekly-profile-updates-design.md)
- Model retraining
- UI changes beyond removing the "NN:" label
- `cache_builder.py` schedule-dedup fix (moot once `elo_predictions` path is removed)
