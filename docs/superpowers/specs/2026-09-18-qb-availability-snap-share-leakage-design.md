# QB Availability Snap-Share Same-Week Leakage Fix

**Date:** 2026-09-18
**Status:** Designed — ready for implementation plan

## Origin

Follow-up to `docs/superpowers/specs/2026-09-17-qb-availability-and-prediction-freshness-design.md`
Part A (`compute_qb_availability_flags()`). While reviewing the admin ML
Accuracy page's feature-debug modal for the NE@SEA Week 1 2026 game, the
`home_qb_injury_flag` showed `1.0` for that game itself. Investigation traced
this to Sam Darnold (SEA's declared Week 1 starter) being injured *during*
that same game and finishing at 10% offensive snaps — a fact only knowable
after the game was played, not before its own kickoff.

## Problem

`compute_qb_availability_flags()` sets each `(season, week, team)` flag from
three signals — official injury report status, Reserve/IR roster status, and
the reference starter's own snap share:

```python
week_shares = snap_by_team_week.get((season, wk, team), {})
ref_share   = week_shares.get(reference)
ref_low_snaps = ref_share is not None and ref_share < 0.20
flags[(season, wk, team)] = 1.0 if (ref_out or ref_reserve or ref_low_snaps) else 0.0
```

The injury-report and Reserve/IR legs are legitimately pre-game — nflverse
publishes both before that week's kickoff. The snap-share leg is not: a
team's Week N snap distribution isn't known until Week N's game has been
played. Using `wk`'s own snap share to set `wk`'s own flag means:

- **Grading/backfill/explanation** (`backfill_schedule_predictions.py`,
  `weekly_model_eval.py`, the admin feature-debug modal) present a
  "prediction" for an already-played week as if the model knew, before
  kickoff, something that only became true during the game. This overstates
  what the model could have legitimately predicted and corrupts the accuracy
  numbers those tools report — including the Week 1 2026 accuracy figure
  (43.8%) pushed to Firestore on 2026-09-18, before this bug was found.
- **Training** (`train_nn_model.py`/`train_xgb_model.py`/`train_lr_model.py`,
  all via `build_master_feature_table()`) learns from a feature that can only
  ever be hindsight-derived in training but can never appear this way at real
  prediction time (the current week's own outcome doesn't exist yet when a
  real prediction is made) — train/serve skew. Worse, a mid-game snap-share
  drop is frequently a *symptom* of that same game already having gone badly
  (garbage-time benching), not an independent injury signal, so it partly
  leaks that game's own outcome into a feature meant to explain it.

There is no legitimate use case for same-week snap share, in either context.
An earlier version of this signal (`tests/test_nn_feature_engine.py::
test_starter_hurt_mid_game_week1_still_flags_via_snap_share`) explicitly
asserted the leaky behavior as correct, treating it as a training-only
convenience — that assertion is the bug being fixed, not a constraint to
preserve.

## Design

Single change, one behavior, no caller-facing parameter: the snap-share leg
always reads the reference starter's snap share from week `wk - 1`, never
week `wk`.

```python
prev_shares = snap_by_team_week.get((season, wk - 1, team), {})
ref_low_snaps = reference in prev_shares and prev_shares[reference] < 0.20
```

Everything else is unchanged:
- `ref_out` / `ref_reserve` still read week `wk`'s own data (legitimately
  pre-game).
- The challenger-streak flip-detection (`week_shares` for week `wk`,
  contributing to `reference` reassignment starting week `wk + 1`) is
  unaffected — it already only influences *future* weeks and was never the
  leaky part.
- For a team's first evaluated week (no `wk - 1` in `snap_by_team_week`),
  the snap-share leg is simply unavailable — same fail-open behavior already
  used elsewhere in this function (e.g. `reference is None`).

### Why one behavior, not a `pregame_only` toggle

An earlier draft of this spec proposed a `pregame_only: bool` parameter,
defaulting differently for training vs. grading/live call sites, to preserve
today's training behavior. That was wrong: there's no principled reason for
training to see information serving never will. A single, always-causal
definition is simpler, removes an entire class of "did I thread the flag
through correctly" risk across `build_master_feature_table()` and its five
call sites, and is the textbook-correct choice for avoiding train/serve skew.

### Worked example (Darnold, SEA, 2026)

- Week 1: no injury report entry, no Reserve/IR entry, no *prior* week to
  check snap share against → `flags[(2026, 1, "SEA")] = 0.0`. Correct — this
  is exactly what was knowable before Week 1's kickoff.
- Week 2: Week 1's snap share (10%, now in the past) is checked →
  `flags[(2026, 2, "SEA")] = 1.0`, *plus* whatever Week 2's own injury report
  independently shows. Correct — by Week 2's kickoff, Week 1's outcome is
  known and is a legitimate input.

### Forward-looking weeks beyond the next one

Not in scope for this fix (existing, working behavior, confirmed during
discussion): `compute_qb_availability_flags()` only returns an entry for a
`(season, week, team)` that some real data source already covers. For a week
with no declared-starter/report/reserve/snap data yet (i.e. more than about
one week out), the caller's `.get(..., 0.0)` default means "assume healthy."
The one signal that persists across many future weeks is Reserve/IR status,
which `weekly_rosters` keeps showing until the player actually returns.
Because `NNProjectionEngine.initialize()` re-fetches this dict fresh on every
run, a full-season simulation's optimism about a currently-questionable
starter's recovery timeline self-corrects day by day as each future week's
real reports actually publish.

## Implementation

1. `services/nn_feature_engine.py::compute_qb_availability_flags()` — apply
   the one-line change above; update the docstring to state the snap-share
   leg is strictly prior-week by design.
2. `tests/test_nn_feature_engine.py::TestComputeQbAvailabilityFlags` —
   rewrite `test_starter_hurt_mid_game_week1_still_flags_via_snap_share` to
   assert the corrected behavior: `flags[(2026, 1, "SEA")] == 0.0` (no prior
   week, no injury report — genuinely unknowable pre-kickoff),
   `flags[(2026, 2, "SEA")] == 1.0` (Week 1's low snap share now known,
   *plus* Week 2's own injury report if present). Add a case confirming a
   team's very first evaluated week never fires the snap-share leg
   regardless of that week's own snap data.
3. No other call site changes — `build_master_feature_table()` and its five
   callers (three training scripts, `weekly_model_eval.py`,
   `backfill_schedule_predictions.py`) and `nn_projection_engine.py`'s direct
   call all pick up the corrected behavior automatically.

## Rollout

1. Land the code + test fix, run the full suite.
2. Re-run `python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --force --features [--firestore]`
   to regrade Week 1 2026 with the corrected signal (supersedes the
   2026-09-18 backfill run, which predates this fix).
3. Re-run `python scripts/weekly_model_eval.py --season 2026 --week 1 --firestore`
   to replace the 43.8% snapshot with a legitimately causal number.
4. No retraining required immediately — the currently-deployed models
   (`nn_v15`/`xgb_v9`/`lr_v7`) were trained before this fix existed either
   way; this changes the *feature table* used for the next retrain and for
   all grading/backfill/explanation going forward, not today's already-fit
   model weights.

## Out of scope

- Modeling recovery-timeline uncertainty for weeks further out than the next
  one (see "Forward-looking weeks beyond the next one" above) — existing,
  accepted behavior, not part of this fix.
- The broader end-to-end review of the model/training/prediction pipeline
  requested alongside this fix — tracked separately.
