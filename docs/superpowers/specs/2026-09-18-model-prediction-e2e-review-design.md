# Model Prediction Pipeline — End-to-End Review

**Date:** 2026-09-18
**Status:** Designed — ready for implementation plan (review phase only; fixes get their own plans per finding)

## Origin

Investigating "why wasn't Week 1 2026 good" surfaced a real, confirmed bug
(same-week snap-share leakage in `compute_qb_availability_flags()` — see
`docs/superpowers/specs/completed/2026-09-18-qb-availability-snap-share-leakage-design.md`)
within minutes of looking. That fix landed the same day. But the pattern —
one focused look, one real bug, first try — combined with two things
already known and sitting unaddressed in the backlog (see "Prior open
questions this review inherits" below) is what prompted the broader ask:
this pipeline was built up over many separate passes (Elo → EPA → roster
value → injury-aware grading → QB availability → betting screener, each its
own plan/spec/review), and there's a real chance other seams like the
snap-share one exist elsewhere, unnoticed because nothing has looked at the
whole thing end to end in one pass.

**User's own framing:** "all these things we reviewed seem to be the case
of frankenstein-ing it together and causing some issues." "I want this to
be very detailed and go step by step."

## Goal

Confirm — or fix until true — that given all information legitimately
available before a game, the system can:

1. **Predict the winner** (straight-up pick + confidence).
2. **Surface any edge vs. the Vegas spread** (model line vs. market line,
   ATS pick, graded correctly once the game is played — already verified
   correct for the ATS sign convention specifically, see below).
3. **Explain why** — both the plain-language version (`schedule_explain.js`'s
   "Why TEAM?" modal, `/api/predictions/explain`) and the technical version
   (`admin_accuracy.js`'s Feature Debug modal, `/api/prediction_features`) —
   and have both explanations actually correspond to the prediction being
   shown, not a freshly-recomputed, possibly-drifted one.

"Given all information legitimately available before a game" is the load-
bearing phrase — the snap-share bug violated exactly this, and it's the
first thing to check at every stage below, not just the one place it was
already found.

## Already verified correct (don't re-check, unless a stage below finds a
reason to doubt it)

- `derive_prediction_scalars()`'s ATS-pick sign convention
  (`services/nn_projection_engine.py`) — traced against a real graded game
  (Week 7 2025, JAX@LA) and confirmed correct. The doc that originally
  flagged this as "looks potentially inverted"
  (`docs/superpowers/specs/2026-09-17-qb-availability-followup-cleanup.md`)
  is now stale on this point.
- `get_remaining_games()` / `calculate_playoff_race()`'s sentinel-handling
  and self-matchup double-counting bugs — both fixed and committed
  (`d5955bf`, `bfcb902`), unrelated to the ML pipeline but found during the
  same session.

## Prior open questions this review inherits

Two backlog specs from a month ago turned out to predict exactly the kind
of problem this review exists to catch. Read them in full before starting;
don't re-derive what they already say:

- `docs/superpowers/specs/2026-08-22-model-quality-drift-monitoring-design.md`
  — flagged, before any of today's findings existed, that "nothing watches
  whether the predictions themselves are still good" and that a degraded
  model "would currently be caught only by a human noticing wrong-looking
  predictions." That is exactly what happened: XGB v9 (AUC 0.538, barely
  above coin-flip) and LR v7 (50.0% test accuracy, exactly random) are
  both currently deployed as "latest," while their own registries'
  `best_by` field already points at v1/v3 — nothing ever reads it. This
  review's Stage 2 should determine whether to resolve this backlog item
  now (wire something up) or roll back/retrain first and design monitoring
  after.
- `docs/superpowers/specs/2026-08-22-feature-computation-versioning-design.md`
  — flagged that stored predictions carry a model-version stamp
  (`ensemble_version`) but nothing marks which *feature-computation code*
  produced their inputs. This is why today's regrade of Week 1 2026 has no
  clean way to prove, from the stored data alone, that it used the
  post-fix feature logic rather than the pre-fix version — we know it did
  only because we ran it ourselves, right after landing the fix, and
  remember doing so. A month from now, nobody would be able to tell.

## Process

Four stages, run as independent audits (each may be delegated to a
sub-agent/fork since the codebase is large and the audits are read-heavy).
**No fixing during the audit pass.** Each stage produces a findings list,
most severe first, in this format:

- File:line
- What's wrong (or: checked, confirmed clean)
- Concrete proof (a repro, or a real data example — not just "this looks
  suspicious")
- Severity: does it affect live pre-game predictions, only retroactive
  grading/training, or both (same three-way split the snap-share fix used)
- Whether it's structural (belongs to "is this Frankensteined") or a
  one-off bug

After all four stages report, consolidate into one findings doc, then
write a separate implementation plan per fix (or a batch of related small
fixes) using `superpowers:writing-plans` — don't fix inline during the
audit, and don't let the audit sprawl into an unplanned refactor.

## Stage 1 — Feature Engineering (`services/nn_feature_engine.py`)

**Started, not finished** (a first audit sub-agent was launched and killed
mid-run on 2026-09-18 — re-launch fresh, don't try to resume).

Checklist:
1. Every rolling/trailing-window feature — rolling EPA (pass/rush,
   offense/defense), rolling point differential, rolling turnover margin,
   trench dominance / OL-DL performance, pressure stats — confirm each
   excludes the current week's own game from its own "as of week N"
   calculation. Same causality bug class as the QB flag; check case by
   case, don't assume the fix pattern generalizes without checking.
2. Confirm the roster-value week-indexing fix from
   `docs/superpowers/specs/completed/2026-09-17-qb-availability-and-prediction-freshness-design.md`
   Bug B (roster-derived features reading the *current*, overwritten-in-
   place `roster_{year}.csv` instead of week-indexed
   `weekly_rosters/roster_weekly_{year}.csv`) actually holds in the
   current code — verify against the live code path, not the spec's
   claim of having fixed it.
3. Any other data source (injuries, snap_counts, depth_charts,
   weekly_rosters) used in a way that could read a snapshot from after a
   graded/explained game's own kickoff.
4. Elo computation (`scripts/compute_elo.py` and however its output feeds
   `nn_feature_engine.py`) — confirm Elo for week N's prediction only
   incorporates results through week N-1.

## Stage 2 — Training (`train_nn_model.py`/`train_xgb_model.py`/`train_lr_model.py`)

**Already run to completion on 2026-09-18** — do not re-run; act on the
findings instead:

1. **[Severe, unresolved]** Deployed XGB v9 / LR v7 ("latest") score far
   worse than v1/v3 ("best," per each registry's own `best_by` field, never
   consulted by any call site) on the identical held-out test set (season
   2025, weeks 16-18): XGB AUC 0.663 → 0.538, LR test accuracy 60.4% → 50.0%
   (exactly random). XGB+LR are 55% of the ensemble blend weight. Needs:
   diff what actually changed in training between the two runs (same data
   range, so not a data-availability gap) — check whether some version of
   the snap-share leakage, or another bug from this same review, was
   present when v9/v7 were trained and is why their *training-time*
   metrics looked acceptable enough to ship despite generalizing badly.
   Decide: roll back to `best` now, or retrain properly once Stage 1's
   findings land (if Stage 1 finds real feature bugs, retraining now would
   just bake in the same bugs again).
2. **Checked clean:** feature-set parity (all three scripts import the
   same `FEATURE_COLUMNS`), scaler/version pairing, walk-forward (not
   random) train/val/test splitting, blend-weight (45/20/35) constant
   consistency (single source in `services/constants.py`).
3. **Noted, not audited:** `services/prediction_service.py`'s separate
   Elo+Pythagorean (70/30) predictor is still actively instantiated in
   `routes/prediction_routes.py` alongside the NN+XGB+LR ensemble. Resolve
   as part of Stage 3: which pages/features actually use which system, and
   whether the second system is intentional (a fallback? a different
   product surface?) or leftover from before the ensemble existed.

## Stage 3 — Serving / Ensemble Blending

Not yet started. Files: `services/prediction_service.py`,
`services/nn_prediction_service.py`, `services/xgb_prediction_service.py`,
`services/lr_prediction_service.py`, `services/nn_projection_engine.py`,
`scripts/cache_builder.py`, `scripts/backfill_schedule_predictions.py`.

1. Resolve the `prediction_service.py` duplicate-system question from
   Stage 2, item 3.
2. Confirm the 45/20/35 blend is applied identically on every path:
   season-simulation Monte Carlo, single-game preseason path, in-season
   single-game path, the daily `cache_builder.py` write, and the backfill
   script.
3. Re-check the "explanation drift" bug from
   `docs/superpowers/specs/completed/2026-09-17-qb-availability-and-prediction-freshness-design.md`
   Bug C: does the daily `cache_builder.py` job actually keep `explanation`
   in sync with the top-level prediction fields now, or does the fix only
   cover part of the write paths? (That spec claims this was fixed as Part
   C/D — verify against current code, same discipline as Stage 1 item 2.)
4. Confirm `derive_prediction_scalars()` (already verified correct in
   isolation) is the *only* place ATS pick / edge-vs-vegas gets computed —
   no divergent inline duplicate anywhere else in the serving path that
   could drift out of sync with it.
5. Sanity-check `RESIMULATE_LEAD_MINUTES` (`scripts/schedule_kickoffs.py`)
   against a real measured runtime of `--resimulate` — flagged as
   unvalidated in the original scheduled-jobs work and never followed up.

## Stage 4 — Explanation Surfaces

Not yet started. Files: `routes/api_routes.py` (`/api/predictions/explain`),
`routes/admin_routes.py` (`/api/prediction_features`),
`static/js/schedule_explain.js`, `static/js/admin_accuracy.js`.

1. Does `/api/predictions/explain` read the actual stored prediction's
   `explanation` dict, or recompute fresh at request time? If the latter,
   it can drift from whatever `is_correct`/`is_correct_ats` the per-game
   table shows for the same game (same class of risk as Stage 3 item 3).
2. Does `/api/prediction_features` (the Feature Debug modal's data source)
   correspond to the *same* model version as the stored prediction being
   displayed, or could it reflect a different (e.g. since-retrained)
   model's feature audit than the win probability shown alongside it? This
   is exactly what
   `docs/superpowers/specs/2026-08-22-feature-computation-versioning-design.md`
   already flagged as unresolved — decide whether to fix the display gap,
   the underlying versioning gap, or both.
3. Confirm SU/ATS grade, edge, and Vegas-line numbers are consistent
   between the per-game table (`admin_accuracy.js`) and both modals for
   the same game — spot-check a handful of real graded games, not just
   the two already checked during the modal-routing fix (NE@SEA, SF@LA).
4. The modal-routing bug (button class collision, fixed 2026-09-18,
   `22eb702`) was found by inspection, not a systematic check — confirm no
   sibling collision exists elsewhere between `schedule_explain.js`'s
   document-level delegated listeners and any other page's own button
   classes.

## Out of scope

- Retraining the models — a decision to make *after* Stage 1/2 findings
  are in, not a step of the review itself.
- Building the model-quality-drift-monitoring or feature-computation-
  versioning systems described in the two backlog specs — this review
  decides whether/when to pick them up, doesn't implement them.
- Any UI/UX changes beyond what's needed to fix a found correctness bug.

## Rollout

1. Run Stage 1 fresh (prior sub-agent was killed mid-run).
2. Run Stages 3 and 4.
3. Consolidate all four stages' findings into one severity-ordered list.
4. Decide, with the user, which findings become their own implementation
   plans (via `superpowers:writing-plans`) and in what order — expect this
   to include at minimum a Stage 2 model rollback-or-retrain decision.
5. Re-run the same regrade pattern used for the snap-share fix
   (`backfill_schedule_predictions.py --force --features [--firestore]` +
   `weekly_model_eval.py --firestore`) once real fixes land, so Week 1
   2026's stamped accuracy number keeps reflecting the current state of
   truth rather than going stale again.
