# Bottom-Up Model Retrain — Narrow Fix (Spec C1) Design Spec

**Date:** 2026-06-04
**Status:** Approved

---

## Problem

The NN/XGB/LR ensemble has two known-incorrect feature computations that cause wrong predictions during the preseason:

1. **Scale mismatch on 5 features.** `def_pressure_diff`, `qb_pressure_advantage`, `off_roster_value_delta`, `def_roster_value_delta`, and `roster_talent_delta` were trained on in-season rolling averages (roughly ±0.5 range). At preseason inference time `_precompute_static_features()` injects cross-team profile z-scores (±1–3), pushing the models out of distribution and **inverting predictions** — a team with an elite DL actually projects *fewer* wins because the model learned the wrong direction at this scale.

2. **Home-only bug on `off/def_roster_value_delta`.** Both features are computed as only the home team's WAR proxy value with no away-team subtraction, despite their "delta" naming. Every other delta feature in `FEATURE_COLUMNS` is a home−away differential; these two are not.

---

## Scope

**Narrow fix only** — no new `FEATURE_COLUMNS`, no new positional signals, no architecture changes. The broader feature expansion (qb_tier_delta, wr_talent_delta, etc.) is deferred to a future spec after this baseline is validated.

---

## Architecture

Four phases in dependency order:

```
Phase 1: build_master_feature_table() changes
    ↓
Phase 2: Retrain NN + XGB + LR
    ↓
Phase 3: Re-enable 5 feature overrides in nn_projection_engine.py
    ↓
Phase 4: Backfill 2026 + verify
```

---

## Phase 1 — Training Data Changes

**Files:**
- Modify: `services/nn_feature_engine.py`

### 1a. New helper: `_build_profile_z_table()`

Add a private function to `nn_feature_engine.py` that computes preseason profile z-scores for a list of seasons and returns a lookup table:

```python
def _build_profile_z_table(seasons: list[int], rawdata_dir) -> dict:
    """Return {(season, team): {dl_perf_z, qb_tier_z, ol_av_z, off_pass_epa_z, def_pass_epa_z}}
    for each season in the list. Uses compute_preseason_player_profiles() which
    reads prior-season depth charts, rosters, stats, advstats, and snap counts.
    Returns empty dict for any season where profile data is unavailable.
    """
```

For each season:
1. Call `compute_preseason_player_profiles(season, rawdata_dir)` → `{team: {raw values}}`
2. Cross-team z-score each of the 5 dimensions within that season
3. Store in the lookup as `(season, team)` → `{5 z-scores}`

Seasons before 2020 are skipped (advstats coverage incomplete); those training rows keep their existing feature values.

### 1b. Override 5 features in `build_master_feature_table()`

After all existing feature computation, call `_build_profile_z_table()` for seasons 2020–present and override the 5 features for any game row where both teams have profile data:

| Feature | Formula (home perspective) |
|---|---|
| `def_pressure_diff` | `hz["dl_perf"] − az["dl_perf"]` |
| `qb_pressure_advantage` | `az["dl_perf"] − hz["dl_perf"]` |
| `off_roster_value_delta` | `(0.7·hz["qb_tier"] + 0.3·hz["ol_av"]) − (0.7·az["qb_tier"] + 0.3·az["ol_av"])` |
| `def_roster_value_delta` | `(0.6·hz["dl_perf"] + 0.4·(−hz["def_pass_epa"])) − (0.6·az["dl_perf"] + 0.4·(−az["def_pass_epa"]))` |
| `roster_talent_delta` | `mean(hz["qb_tier"], hz["off_pass_epa"], −hz["def_pass_epa"], hz["dl_perf"], hz["ol_av"]) − same for away` |

Where `hz` = home team z-scores, `az` = away team z-scores for that season.

Rows without profile data (pre-2020, or missing teams) keep their existing values unchanged.

### 1c. Fix home-only bug on `off/def_roster_value_delta`

The formulas in 1b already correct this: both use `h_value − a_value` instead of `h_value` alone. The existing computation path in `build_master_feature_table()` (via `roster_value_service`) is replaced for 2020+ rows by the profile z-score override in 1b. For pre-2020 rows, the `_rv_delta()` helper already computes `h − a` correctly for `off_roster_value_delta` (checking the code — the bug only manifests in the preseason inference path in `nn_projection_engine.py`, which is fixed in Phase 3). No change needed in the existing `roster_value_service` code path.

---

## Phase 2 — Retrain All Three Models

**Files:**
- Run: `scripts/train_nn_model.py` → `nn_v14`
- Run: `scripts/train_xgb_model.py` → `xgb_v8`
- Run: `scripts/train_lr_model.py` → `lr_v6`

All three train scripts call `build_master_feature_table()` with no modifications needed. The Phase 1 changes flow through automatically.

### Evaluation

Immediately after retraining, run:
```bash
python scripts/weekly_model_eval.py --season 2024 --week 1 18
```

**Go/no-go gate:**
- **Deploy:** new ensemble accuracy ≥ current (nn_v13 + xgb_v7 + lr_v5) on 2024 season
- **Manual review + deploy:** accuracy slightly lower (≤1%) but feature fixes are clearly correct — known-wrong feature computation is reason enough to prefer the corrected model
- **Investigate:** accuracy drops >2% — indicates a bug in Phase 1 implementation; do not deploy

---

## Phase 3 — Re-enable Preseason Feature Overrides

**Files:**
- Modify: `services/nn_projection_engine.py`

Restore the RETRAIN SPEC comment block in `_precompute_static_features()`. Two changes from the original reverted version:

1. `off/def_roster_value_delta` use h−a differentials (now matching training data)
2. `roster_talent_delta` uses all 5 dimensions equally (now matching training data)

The `_pp_z` precomputation block (z-scoring across all teams before the per-game loop) is restored unchanged. The per-game override block is restored with updated formulas:

```python
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
    h_q = (hz["off_pass_epa"] + (-hz["def_pass_epa"]) + hz["qb_tier"]
           + hz["dl_perf"] + hz["ol_av"]) / 5.0
    a_q = (az["off_pass_epa"] + (-az["def_pass_epa"]) + az["qb_tier"]
           + az["dl_perf"] + az["ol_av"]) / 5.0
    feat[col_idx["roster_talent_delta"]] = float(h_q - a_q)
```

The RETRAIN SPEC comment block in the source is replaced with this live code.

---

## Phase 4 — Backfill + Verify

```bash
# Dry-run to gate before writing to Firestore
python scripts/predict_season.py --season 2026 --simulations 2000 --dry-run
```

**Gate:** LA Rams rank top-5, win range ≥ 10 wide, no team projected > 16 or < 2.

If gate passes:
```bash
python scripts/predict_season.py --season 2026
python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --firestore
python scripts/cache_builder.py --year 2026 --force
python scripts/refresh_local_pkls.py
```

---

## Tests

No new test classes needed — the existing `TestPreseasonEloBoost` and `TestPreseasonProfilesWiredIntoSimulation` tests in `tests/test_preseason_profiles.py` cover the `_build_initial_state()` path. A new test class `TestPreseasonFeatureOverrides` is added to cover the re-enabled `_precompute_static_features()` overrides (similar to the `TestPreseasonStaticFeatureOverrides` class that was removed when we reverted — but now with h−a differential formulas and the 5-dim roster_talent_delta).

---

## What This Does NOT Change

- `FEATURE_COLUMNS` list — still 27 features, same names
- Ensemble blend weights (45% NN + 20% XGB + 35% LR)
- Any UI or API surface
- Pre-2020 training rows (those keep existing feature values)
- The `_build_initial_state()` Elo boost logic (unchanged)

---

## Relation to Other Specs

- **Supersedes** the relevant sections of `2026-06-02-bottom-up-model-retrain-design.md` for the narrow fix items
- **Unblocks** `2026-06-02-inseason-weekly-profile-updates-design.md` — weekly profile rebuilds can now produce values in the correct scale the retrained model expects
- **Future** broader retrain (new positional features) can build on this baseline
