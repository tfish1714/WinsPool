# QB Starter Availability Signal + Prediction Data Freshness

**Date:** 2026-09-17
**Status:** Designed — ready for implementation plan

## Origin

Investigating "how did week 1 do" surfaced four related, compounding bugs in the
same area of the pipeline (`services/nn_feature_engine.py`,
`services/nn_projection_engine.py`, `scripts/cache_builder.py`,
`scripts/backfill_schedule_predictions.py`, `services/cache_service.py`).
None of these are hypothetical — all four were reproduced against live prod
data during the investigation (see each section's "Evidence").

## Problem summary

| # | Problem | Where |
|---|---|---|
| A | The QB-starter-unavailable signal is dead code (computed, never wired to the model) — and even wired up, the intended weeks-1-3 baseline logic can't fire before week 4 and mis-identifies the starter when the real starter is hurt mid-game | `nn_feature_engine.py::compute_starter_qb_flags` |
| B | Roster-derived features (`roster_talent_delta`, `off/def_roster_value_delta`, `def_pressure_diff`, `qb_pressure_advantage`) read the *current* roster file, which is overwritten in place all season — re-grading an old week picks up today's roster, not that week's | `nn_feature_engine.py::compute_preseason_player_profiles` |
| C | `explanation` (the feature-level breakdown the betting screener reads) is only ever written by `backfill_schedule_predictions.py`; the daily `cache_builder.py` job never refreshes it, so it silently diverges from the top-level fields it's supposed to explain | `cache_builder.py::build_year`, `cache_service.py::merge_thin_game_predictions` |
| D | Nothing runs the "grade completed games via the feature table" backfill on a schedule — it only ran because we ran it by hand. Without it, predictions for a just-finished week sit on stale MC-simulation values (sometimes self-consistent, sometimes not) until `cache_builder`'s daily thin-write happens to catch up | `scripts/schedule_kickoffs.py` (missing step) |

## Evidence already gathered

- `W01_PIT_ATL`'s stored prediction had `pred_prob: 0.632` (63.2% PIT) but
  `model_spread: -3.2` (implies ATL favored) — internally contradictory, and
  contradicted its own `elo_diff: +51.2` and `vegas_home_prob: 0.6064`, both
  of which favored PIT. Root cause: B (this game's roster-derived features had
  drifted since week 1).
- `explanation.model_spread` for the same game is `0.4` (from a manual backfill
  run three days ago) while the top-level `model_spread` is now `3.8` (from
  today's automatic `cache_builder` run) — same game, two different numbers,
  proving C is a live, ongoing divergence, not a one-time artifact.
- `compute_starter_qb_flags()` is called at `nn_feature_engine.py:1995` and the
  result is never referenced again anywhere in the file — confirmed dead.
- `NNProjectionEngine._precompute_static_features()` hardcodes
  `home_qb_injury_flag = 0.0` / `away_qb_injury_flag = 0.0` for every future
  game (`nn_projection_engine.py:346-347`) — this is why SEA/MIN's week-2
  predictions show no QB signal at all right now, independent of bug A.
- Seattle week 1: depth chart (`depth_charts_2026.csv`, snapshot
  `2026-09-08T11:56:57Z`, pre-kickoff) lists Sam Darnold `pos_rank=1`. Actual
  week-1 snaps (`snap_counts_2026.csv`): Darnold 5 (10%), Drew Lock 45 (90%) —
  Darnold was hurt early in the game. A "most week-1 snaps" rule would wrongly
  anchor on Lock.
- Minnesota week 1: same pattern — depth chart pre-kickoff has Kyler Murray
  `pos_rank=1`; actual snaps: Murray 11 (17%), Carson Wentz 54 (83%).
- `roster_{year}.csv` (used by bug B) has **zero** players appearing in both
  week 1 and week 2 rows (2,963 players, 0 overlap) — it's a rolling
  current-status file, not a week-indexed history. `weekly_rosters/
  roster_weekly_{year}.csv` has identical columns but 2,485/2,963 players
  present in both weeks — it's the correct, already-synced, append-only
  source (available back to 2002).

## Part A — QB starter availability signal

### Design

**Per-team "reference starter," recomputed forward week by week, sticky until proven both healthy and benched:**

1. **Initial reference starter for a team's season** = the QB with
   `pos_rank == 1` at the depth-chart snapshot closest to (but before) that
   team's week-1 kickoff. (Not "most week-1 snaps" — that misattributes the
   starter when they're hurt mid-game, as both SEA and MIN show right now.)
2. **Each subsequent week**, the reference starter carries forward unchanged
   *unless* both of the following are true as of that week:
   - the current reference starter has no `Out`/`Doubtful` injury-report
     entry that week (the same definition used by the flag itself, below —
     `Questionable` does not block the flip, since a questionable player
     typically does play) **and** no Reserve/IR roster status, **and**
   - they are **not** the current depth-chart `pos_rank=1` at QB.

   Only then does the reference starter flip to whoever the depth chart
   currently names as `pos_rank=1`. This is what distinguishes "hurt, still
   the guy" (stays pinned, e.g. Darnold/Murray this week) from "healthy, but
   lost the job" (flips to the new guy).
3. **Availability flag for week W** = `1.0` if the week-W reference starter is
   any of:
   - on the official injury report as `Out` or `Doubtful` that week, or
   - on Reserve/IR roster status (`weekly_rosters` `status == "RES"`) that
     week, or
   - (historical/completed weeks only, for training and backtest grading —
     not available for a future game) took less than 20% of the team's QB
     offensive snaps that week.

   Otherwise `0.0`.

### Output feeds the *existing* feature, not a new one

`compute_starter_qb_flags()` is replaced by a new function,
`compute_qb_availability_flags(seasons, rawdata_dir) -> {(season, week, team): float}`,
whose output is OR'd into the existing `home_qb_injury_flag`/`away_qb_injury_flag`
merge (`nn_feature_engine.py:2144-2161`) alongside the current official-injury-report
flag. **No new feature column, no retraining.**

### Three call sites need it (not just one)

| Call site | Use | Notes |
|---|---|---|
| `build_master_feature_table()` | Historical/completed games — training, backtest grading, `weekly_model_eval.py` | Full logic above, including the snap-count leg |
| `NNProjectionEngine._precompute_static_features()` | Live predictions for future/unplayed games | Currently hardcodes 0.0 — this is the actual reason SEA/MIN show nothing this week. Injury-report + Reserve legs only (no snap-count leg — game hasn't happened) |
| `backfill_schedule_predictions.py`'s MC-sim fallback `explanation` builder | Display only | Currently also hardcodes `home_qb_out`/`away_qb_out: 0.0`; should call the same shared helper for consistency between what's predicted and what's shown |

A single function in `nn_feature_engine.py`, importable by the other two
modules, avoids duplicating the sticky-reference logic three times.

### Data sources and the ID crosswalk problem

- **Depth chart:** `depth_charts/depth_charts_{year}.csv` (dated snapshots,
  `gsis_id` present, back to 2001).
- **Injury report:** `injuries/injuries_{year}.csv` (`gsis_id`, `report_status`,
  back to 2009 — pre-2009 seasons get `0.0`, matching existing behavior).
- **Reserve/IR status:** `weekly_rosters/roster_weekly_{year}.csv`
  (`gsis_id`, `status`, back to 2002).
- **Snap counts (historical-only leg):** `snap_counts/snap_counts_{year}.csv`
  — **keyed by `pfr_player_id`, not `gsis_id`.** Cross-walk via
  `rosters/roster_{year}.csv`, which carries both `gsis_id` and `pfr_id` per
  player. If a player can't be matched (missing ID mapping), log a warning
  and treat as available (`0.0`) — fail open, consistent with this file's
  existing pattern (e.g. `compute_preseason_player_profiles`'s data-quality
  warnings never raise).
- Kickoff timing for "closest depth-chart snapshot before kickoff" comes from
  the schedule's own game date/time per week (already loaded as `_load_schedule`).

### Explicitly decided edge cases

- A backup filling in for one game while the starter is merely resting
  (healthy, not on any report) but returns next week as the depth chart's #1
  the whole time: flag stays `0.0` throughout — the sticky-reference flip
  requires the *reference starter* to be off the depth chart's #1 spot, which
  never happens here.
- Committee/rotation situations (~50/50 snap split, no reference starter
  concept clearly established): the depth-chart-based initial identification
  avoids treating this as ambiguous — whoever's `pos_rank=1` pre-kickoff is
  the reference, snap-count only matters for the *available/unavailable*
  check, not for establishing who "the starter" is.
- Missing data (bye weeks, sync lag, pre-2009/pre-2001 seasons per source):
  fail open to `0.0` (assume available), matching existing conventions
  throughout this file.

## Part B — Point-in-time roster snapshots

`compute_preseason_player_profiles()` (`nn_feature_engine.py:1281`) switches
its roster source based on caller intent:

- **Historical/completed-game path** (`build_master_feature_table`, used by
  training, `backfill_schedule_predictions.py`, `weekly_model_eval.py`):
  read `weekly_rosters/roster_weekly_{year}.csv` filtered to the row's own
  `week`, instead of `rosters/roster_{year}.csv`. This makes a "locked"
  prediction for week N actually stay historically correct no matter when
  it's (re)computed.
- **Future-game path** (`NNProjectionEngine.simulate_season()`): keep reading
  the latest roster snapshot — predicting a game that hasn't happened yet
  correctly wants today's best information, not a frozen one.

Requires threading a `week` parameter through
`compute_preseason_player_profiles()` → `_build_profile_z_table()` →
`_apply_profile_overrides()`, which currently key by `(season, team)` only.
Schemas are identical between the two roster files (verified), so this is a
source-and-filter change, not a restructure of the downstream logic.

## Part C — Keep `explanation` in sync with the top-level fields

`_apply_predictions()` (`cache_builder.py`) already has the full
`explanation` dict available from `pred_lookup` (built by
`build_ensemble_lookup`, which always returns one) for the completed-game
branch — it's just never carried through to the write. Fix:

- Thread `explanation` through `_apply_predictions()`'s output columns
  alongside the existing `pred_winner`/`model_spread`/etc., and include it in
  the daily `pmap` write (`build_year()`, `cache_builder.py:320-354`)
  whenever `pred_lookup` supplied one.
- For the MC-sim fallback branch (unplayed games), build an equivalent
  `explanation` dict the same way `backfill_schedule_predictions.py` already
  does for its own fallback branch, instead of omitting it.

This makes `explanation` refresh in lockstep with the top-level fields on
every daily run — no separate job needed for this piece, and the betting
screener stops reading permanently-stale feature values.

## Part D — Weekly lock-in timing

Add `backfill_schedule_predictions.py --firestore` as a step that runs **once
a week, Tuesday mornings**, after `winspool-sync-daily`/`winspool-predict-daily`
— the earliest point every game in the prior week (including Monday Night
Football) is guaranteed final. (Thursday morning is before that week's games
happen; Sunday morning is before Sunday's and Monday's games happen — neither
can fully grade "that week.")

Implementation choice: rather than adding ML dependencies
(`requirements-ml.txt`) to `winspool-schedule-kickoffs`'s image
(`Dockerfile.sync`, which deliberately excludes them), add this as a
**day-of-week-conditional step inside `winspool-predict-daily`**
(`scripts/cache_builder.py`'s entrypoint already runs daily via
`Dockerfile.predict`, which already has the ML deps it needs) — run the
normal daily work every day, and additionally invoke
`backfill_schedule_predictions.py --firestore` only when the job runs on a
Tuesday. This avoids any new Docker image or Cloud Scheduler trigger.

## Rollout

Once Parts A-C land, re-run `backfill_schedule_predictions.py --force
--firestore` once to regrade the current season's already-`locked` weeks
(currently poisoned by the Part B drift bug) with corrected data. This is a
one-time manual step post-deploy, not a recurring job (Part D's weekly job
handles going forward without `--force`, since new weeks aren't locked yet
when it runs).

## Non-goals

- Not modeling *who* the backup QB is or their individual skill level beyond
  the binary availability flag — the preseason player-profile features
  (`qb_tier` etc.) still describe the original starter until profiles are
  separately regenerated. Recognized as a real limitation, out of scope here.
- Not backfilling `explanation` for already-completed historical seasons
  (2020-2025) — Part C only prevents *future* drift; those seasons' rosters
  are frozen now (season over), so Part B's drift bug doesn't apply to them
  regardless.
- Not adding a UI indicator distinguishing "flag from official injury report"
  vs. "flag from snap-count inference" — both fold into the same
  `home_qb_injury_flag`/`away_qb_injury_flag` value by design (Part A).
