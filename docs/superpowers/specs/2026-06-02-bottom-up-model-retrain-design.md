# Bottom-Up Model Retrain (Spec C) — Design Spec (STUB)
**Date:** 2026-06-02
**Status:** Stub — pending brainstorm session to finalize

---

## Problem

Spec 1 (preseason player profiles) and Spec 3 (in-season weekly updates) produce bottom-up team quality estimates that feed into the simulation as starting-state overrides. However, the NN/XGB/LR ensemble models themselves were trained on team-level stat averages and have never seen granular positional signals like QB tier, WR talent index, or CB coverage rate as explicit features.

Adding these as new `FEATURE_COLUMNS` and retraining would allow the models to learn the direct relationship between player-level quality and game outcomes — a fundamentally more accurate signal than team-level rolling averages.

---

## Intended Approach (to be refined in brainstorm)

### New feature columns to add to `FEATURE_COLUMNS`

Candidates (to be finalized in brainstorm):
- `qb_tier_delta` — home QB EPA per dropback minus away QB EPA per dropback
- `wr_talent_delta` — home WR/TE receiving quality minus away WR/TE receiving quality
- `rb_talent_delta` — home RB rushing quality minus away RB rushing quality
- `dl_pressure_rate_delta` — home DL pressure rate minus away DL pressure rate
- `cb_coverage_rate_delta` — home CB/S coverage quality minus away CB/S coverage quality
- `lb_run_stop_delta` — home LB run-stopping quality minus away

### Training data generation
- Need to backfill player-level profiles for historical seasons (2020–2025) using the same methodology as Spec 1
- Each historical game row gets enriched with bottom-up feature values computed from the roster/advstats of that season
- Requires running `_preseason_offense()` / `_preseason_defense()` for each prior season

### Model update
- Retrain NN, XGB, LR with expanded feature set (current 27 features + N new bottom-up features)
- Use same train/val/test split logic as `train_nn_model.py`
- Auto-increment model version in registry (`nn_v11`, `xgb_v5`, `lr_v3`)
- Evaluate against existing accuracy baseline in `reports/nn_weekly_accuracy.csv`

### Risk
- New features may not add signal if player stats from prior year are poor proxies mid-season
- Need A/B comparison: retrained model vs. current ensemble on held-out test set before deploying

---

## Open Questions for Brainstorm

1. Which new feature columns have the highest expected signal — worth exploring correlation with game outcomes before committing to all 6
2. How do we generate training labels for historical seasons where advstats coverage is incomplete (pre-2020)?
3. Should we add the bottom-up features as replacements for existing EPA features, or additive alongside them?
4. Do we need a separate feature pipeline for historical backfill vs. the live preseason pipeline?
5. What accuracy improvement threshold justifies deploying the retrained model?
6. Should in-season updates (Spec 3) be running before retraining so the model learns from dynamic roster states?

---

## Dependencies

- **Spec 1 (preseason player profiles)** — the `_preseason_offense()` / `_preseason_defense()` functions are reused to generate historical training features
- **Spec 3 (in-season weekly updates)** — ideally shipped before retraining so mid-season features are part of training data
- Sufficient historical data: advstats back to 2018+, snap_counts back to 2021

---

## Out of Scope

- UI changes
- Changes to the simulation loop
- Changing ensemble blend weights (separate tuning exercise)
