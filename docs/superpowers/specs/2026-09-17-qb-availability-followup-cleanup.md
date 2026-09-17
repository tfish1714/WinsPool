# QB Availability Branch — Follow-Up Cleanup

**Date:** 2026-09-17
**Status:** Backlog — parked findings from implementing
`docs/superpowers/plans/2026-09-17-qb-availability-and-prediction-freshness.md`.
None of these block that branch; all were explicitly ruled non-blocking
during implementation (task reviews, the final whole-branch review, or the
final fix-wave re-review). Collected here so they aren't lost.

**Explicitly excluded from this doc** (tracked/handled separately, not here):
- The `derive_prediction_scalars` ATS-pick sign convention
  (`services/nn_projection_engine.py`), which looks potentially inverted
  under the codebase's stated "negative = home favored" convention. This
  predates the QB-availability branch and wasn't touched by it either way —
  needs its own investigation, not a quick cleanup item.
- `docs/superpowers/plans/2026-09-17-qb-availability-and-prediction-freshness.md`
  Task 10 Steps 2-4 (real-rawdata smoke test, the one-time `--force
  --firestore` production regrade, admin ML Accuracy page verification) —
  those are deployment/rollout actions, not code cleanup, and need explicit
  go-ahead when run.

## Performance / duplicated I/O

- **Double-reads of the same rawdata files across sibling loaders.**
  `services/nn_feature_engine.py`'s `_load_injury_flags` and
  `_load_qb_report_status` each independently glob-and-parse
  `injuries/injuries_*.csv`; `rosters/roster_*.csv` and `snap_counts/*.csv`
  are similarly read more than once per `build_master_feature_table()` run
  across different loader functions. None of this is incorrect — each
  loader is independently fail-open and correct — it's pure redundant I/O.
  A shared per-run cache (even a simple `functools.lru_cache` keyed on the
  glob pattern + rawdata dir) would remove it. Noted during Task 4 and
  Task 6 reviews; the final review's fix wave addressed the specific ~20x
  per-week amplification in `_build_profile_z_table` (that one *was*
  fixed), but the broader cross-loader duplication across the rest of the
  pipeline is still there.
- **Two row-wise `.apply(axis=1)` passes for the home/away QB-flag OR.**
  `services/nn_feature_engine.py`'s `build_master_feature_table` (the block
  added in Task 4) runs one `.apply()` for `home_qb_injury_flag` and a
  second, identical-shape one for `away_qb_injury_flag`. Could be a single
  vectorized `.map()` over a tuple index, or one `.apply()` returning both
  columns. Consistent with other patterns already in the same function
  (this isn't a new idiom), so low priority — just noted for whenever
  someone next optimizes this function's hot path.
- **`_run_weekly_backfill_if_tuesday()` duplicates `_sync_rawdata()`'s
  subprocess-result-handling block almost verbatim** (`scripts/
  cache_builder.py`) — stdout-tail-print + non-zero-return-code warning,
  now also mirrored for the `TimeoutExpired` case added in the final fix
  wave. A small shared `_run_subprocess_step(cmd, label, timeout)` helper
  would remove the duplication between the two call sites.

## Test coverage gaps

- **`tests/test_cache_builder.py::test_runs_backfill_on_tuesday` defines an
  unused `tuesday` variable.** The test actually mocks out
  `_run_weekly_backfill_if_tuesday` entirely and only verifies `main()`
  calls the (mocked) step — it doesn't exercise the Tuesday branch despite
  reading like it does. Either delete the dead variable or rename the test
  to reflect what it actually checks (e.g.
  `test_main_invokes_weekly_backfill_step`).
- **`TestPredictionsActiveSignal::test_full_run_signals_predictions_active`
  doesn't mock `_run_weekly_backfill_if_tuesday`.** On a real Tuesday
  (UTC), running this test would actually attempt to spawn the backfill
  subprocess. Pre-existing gap (the weekday-gated call already existed
  before this branch), not introduced here — but the branch's own
  `TestWeeklyBackfillStep::test_runs_after_the_cache_invalidation_signal`
  shows the right patching pattern this older test should adopt.
- **`_build_profile_z_table`'s "empty after week-filter" case isn't
  separately unit-tested.** A `weekly_rosters` file that exists but has no
  rows for the requested week is only covered implicitly by code + the
  "file missing entirely" test, not its own dedicated test.
- **A malformed `weekly_rosters/roster_weekly_{year}.csv` missing its
  `week` column outright would raise `KeyError`** rather than degrade to
  `{}`, in `compute_preseason_player_profiles`'s week-aware path. Matches
  an identical pre-existing assumption already made in
  `services/roster_value_service.py`, so not a new class of risk — just
  worth a defensive check + test if this file's schema is ever a concern.

## Observability

- **`_load_qb_snap_shares` silently disables the entire snap-share leg of
  the QB availability signal** (returns empty) if `pfr_id`/`gsis_id` are
  absent from the roster crosswalk files — no log line. Add a
  `logger.warning(...)` so this is diagnosable if it ever happens in
  production rather than just silently degrading the signal.
- **`_load_declared_starters`'s duplicate-row resolution is order-dependent
  if the old and new nflverse depth-chart schemas ever overlap for the same
  season** (`services/nn_feature_engine.py`) — whichever schema's rows land
  last in the `pd.concat` wins for a given `(season, week, team)` key. Not
  currently possible (nflverse switched schemas cleanly at the 2025
  boundary) but worth a defensive dedup rule or at least a comment if this
  ever becomes ambiguous. Also: the new-schema branch uses `new["team"]`
  without first confirming that column exists on the gated frame — add an
  explicit check or rely on the existing `dt`/`gsis_id`/`pos_abb` gate
  being sufficient (verify it actually is before treating this as a
  non-issue).

## Documentation

- **`compute_qb_availability_flags`'s docstring is incomplete.** It states
  the two-consecutive-week, >65%-snap-share flip rule but omits the 20%
  snap-share unavailability threshold, and doesn't mention that a team with
  no declared-starter row anywhere in a season produces no keys at all for
  that team (fails open for the whole season, not per-week).
- **`_derive_prediction_fields`'s docstring** (pre-final-fix-wave) claimed
  the shared helper kept `_apply_predictions` and `_publish_game_probs`
  from drifting on `explanation` — this was fixed by the final fix wave
  (both call sites now route through the same `build_mc_prediction_entry`
  helper in `services/nn_projection_engine.py`), so the docstring claim is
  now actually true. Nothing to do here — noted only so this item isn't
  mistakenly re-opened by someone reading the original review findings
  without also reading the final fix wave's resolution.
- **`docs/prediction_model.md`'s "Top coefficients (LR v3)" table** still
  lists retired feature names including the old `qb_injury_flag` (pre-split
  into `home_qb_injury_flag`/`away_qb_injury_flag`). Deliberately left
  alone during the branch's docs fix (it's a historical trained-model
  coefficient snapshot, not a live feature description) — flagging in case
  a future docs pass wants to either annotate it as historical or refresh
  it against a current model version.

## Minor behavior notes (already ruled safe, informational only)

- `backfill_schedule_predictions.py` writes its season document via a
  whole-document `.set()`/JSON overwrite, not a merge. On the (logged,
  self-healing) error path where the shared explanation-builder can't
  build an explanation, this erases rather than preserves a previously
  stored explanation for that one game — self-heals on the next successful
  backfill run. Not worth fixing on its own; would only make sense as part
  of a broader "make all game_predictions writes merge-based" change.
- The shared `derive_prediction_scalars`'s ATS-pick fallback (`ats =
  winner` when a game has no Vegas line) changed
  `backfill_schedule_predictions.py`'s prior behavior for spread-less games
  to match `cache_builder.py`'s pre-existing behavior. A real, if small,
  behavior change — not a bug, just noting it happened as a side effect of
  the final fix wave's de-duplication.
- The Tuesday weekly backfill's `--seasons <current_year> <current_year>`
  scoping means it no longer touches an upcoming, not-yet-drafted season
  during the pre-draft window (when `current_year` resolves to the prior
  drafted season). The daily full build already covers that season every
  day regardless, and the weekly job's purpose is specifically to lock
  *completed* weeks, which don't exist yet for an undrafted season — so
  this is correct behavior, not a gap, just worth knowing if anyone
  wonders why the weekly job "skips" a season in August.
