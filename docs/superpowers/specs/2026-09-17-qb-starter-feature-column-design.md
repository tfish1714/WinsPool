# QB Starter Availability as a Dedicated Feature Column (retrain required)

**Date:** 2026-09-17
**Status:** Not designed — backlog stub. Split out of
`docs/superpowers/specs/2026-09-17-qb-availability-and-prediction-freshness-design.md`
so the no-retrain version could ship first. Needs a full brainstorming pass
before implementation.

## Origin

The companion spec (above) folds the new week-1-starter/sticky-reference
availability signal into the *existing* `home_qb_injury_flag`/
`away_qb_injury_flag` feature columns specifically so it ships without
retraining the NN/XGB/LR models. That's the right choice to get the fix live
quickly, but it means "starter out per the official injury report" and
"starter out because of the sticky-reference/snap-count logic" collapse into
one number the model can't tell apart, and can't weight differently.

This stub captures the alternative — a **separate** feature column — for if/when
that conflation turns out to matter enough to justify a retrain.

## What a dedicated column would look like

- New column, e.g. `starter_continuity_flag` (name TBD), distinct from
  `home_qb_injury_flag`/`away_qb_injury_flag`. Computed by the same
  sticky-reference logic in the companion spec's Part A, but written to its
  own feature instead of OR'd into the existing one.
- Requires adding the new column to `FEATURE_COLUMNS`
  (`services/nn_feature_engine.py`), which changes the model's input
  dimensionality — every one of NN/XGB/LR must be retrained before this
  column has any effect (an untrained new input is just noise, or the model
  won't load at all if the shape doesn't match the saved architecture).
- Would need a fresh `model_registry.json`/`xgb_registry.json`/
  `lr_registry.json` version bump per `scripts/train_nn_model.py`'s existing
  versioning convention, same as any other retrain.

## Open questions to resolve when this is picked up

1. **Does the model actually benefit from the split?** Worth checking via
   `scripts/weekly_model_eval.py`-style backtest comparison once enough
   games have accumulated under the combined-flag version (this spec's
   companion) — if accuracy on games with a starter change looks the same
   either way, the extra column (and retrain overhead) may not be worth it.
2. **Severity, not just binary?** A separate column opens the door to
   something richer than 0/1 — e.g. a continuous "confidence the reference
   starter is out" score, or distinguishing "definitely out" (Reserve/IR)
   from "probably benched" (snap-count inferred) as different magnitudes
   instead of collapsing both to 1.0.
3. **Interaction with `qb_tier`/preseason profile features?** Noted as a
   non-goal in the companion spec: even with a starter-availability flag,
   the preseason player-profile features still describe the *original*
   starter's quality, not the backup's. A fuller fix might belong here too —
   swapping which player's profile feeds `qb_tier` once the reference
   starter flips — but that's a bigger change than a single new column and
   needs its own scoping.
4. **Backward compatibility of historical training data** — if this column
   is added, does the training pipeline backfill it for all historical
   seasons (2020+, per the companion spec's data availability), or only
   from whenever this ships forward?

## Non-goals (inherited from companion spec, still apply)

- Not modeling which specific backup is in and their skill level — still
  just an availability signal, richer or not.
- Not a full "current roster quality" recompute — that's the `qb_tier`
  interaction question above, explicitly deferred.
