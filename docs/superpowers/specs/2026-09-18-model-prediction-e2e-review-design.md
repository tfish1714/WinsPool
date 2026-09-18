# Model Prediction Pipeline — End-to-End Review

**Date:** 2026-09-18
**Status:** Designed — ready for implementation plan (review phase only; fixes get their own plans per finding)

## Origin

Investigating "why wasn't Week 1 2026 good" surfaced a real, confirmed bug
(same-week snap-share leakage in `compute_qb_availability_flags()` — see
`docs/superpowers/specs/completed/2026-09-18-qb-availability-snap-share-leakage-design.md`)
within minutes of looking. That fix landed the same day. But the pattern —
one focused look, one real bug, first try — combined with two things
already known and sitting unaddressed in the backlog (see "Folded-in
designs" below) is what prompted the broader ask:
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

## Folded-in designs: two backlog specs this review resolves

Two backlog specs from a month ago
(`docs/superpowers/specs/2026-08-22-model-quality-drift-monitoring-design.md`,
`docs/superpowers/specs/2026-08-22-feature-computation-versioning-design.md`)
sat at "Not designed — needs a proper brainstorming pass" since 2026-08-22.
Both predicted, almost exactly, the kind of problem this review exists to
catch. Rather than leave them as indefinite backlog, today's findings
answer most of their own open questions directly — folded in here as real
designs, superseding the two stub docs (each now carries a pointer to this
section; see "Rollout" for follow-up).

### Model quality gating (resolves `model-quality-drift-monitoring`)

**Correction (2026-09-18, same day):** the first pass at this finding
compared XGB "latest" (v9) against the registry's `best_by` pointer (v3)
and called it severe (AUC 0.663 → 0.538). That comparison is invalid and
the recommendation that followed from it ("roll back to `best`") is
actively wrong. `models/xgb_registry.json` shows v1/v2/v3 all include
`spread_line` — the actual Vegas line — in `feature_columns`; v4 onward
does not. That's the "ML De-Vegas Pass" boundary (May 2026, split out in
its own memory note): `spread_line` was deliberately removed because it
let the model back-solve the market's own answer instead of predicting
independently. `best_by` still points at v3 — the leaky generation — so a
promotion gate that blindly compares against "whatever the registry calls
best" would have silently reintroduced the exact leakage the de-Vegas pass
existed to remove. Same shape of mistake, one level up, in the fix for the
first mistake — worth remembering while designing the gate below.

**The honest comparison** (same feature schema, no `spread_line`,
confirmed identical training data range and hyperparameters across every
version compared): XGB v4→v9 AUC = 0.582, 0.597, 0.559, 0.573, 0.564,
**0.538**; LR v2→v7 AUC = 0.583, 0.604, 0.573, 0.576, 0.561, **0.556**.
There is a real, smaller decline within this honest cohort — "latest" is
the weakest of its own generation for both models — but it's a 0.54-0.60
range, not "coin-flip vs. 0.66." Since training config (data range,
hyperparameters) is identical across the whole honest cohort for XGB, any
metric drift came from the underlying *feature values* changing between
each retrain date (`nn_feature_engine.py` was under active, unrelated
revision this whole period — dynamic MC sim, preseason profiles,
injury-aware roster value, QB availability), not a training-process bug —
and could be partly attributable to noise from a small (~48-game)
held-out test window rather than a real trend. **This is exactly why
feature-computation versioning has to land before a promotion gate can be
trusted**: without knowing which `nn_feature_engine.py` commit produced
each training run's inputs, "compare against best" can't tell a real
regression apart from three months of unrelated feature changes riding
along.

The backlog doc's open questions (what metric, what threshold, what
window) also assumed the problem was **live, ongoing drift** — a model
that was good when shipped, degrading over a season from real-world
distribution shift. That's a distinct, still-real risk worth building for
separately. Two designs, corrected:

**1. Training-time promotion gate (new, directly fixes today's bug) --
schema-scoped, not "compare against whatever `best_by` says."** Adapted
from the `mle-workflow` skill's pattern (ECC project, MIT-licensed,
evaluated 2026-09-18 — see below), corrected for the mistake above: a
training script must not write a new version as `latest` unless it clears
explicit gates against the best version **within its own feature-schema
generation** — comparing against a differently-shaped feature set (e.g.
one that still had `spread_line`) is comparing against leakage, not a
quality bar. This depends on feature-computation versioning (below)
existing first, so the gate has something reliable to scope by; a stopgap
until then is comparing `feature_columns` lists directly (already present
in every registry entry) rather than assuming version numbers are
comparable.

```python
# scripts/train_xgb_model.py / train_lr_model.py / train_nn_model.py --
# after computing this run's test metrics, before writing latest/best:

PROMOTION_GATES = {
    # (direction, max allowed regression vs. same-schema best)
    "test_accuracy": ("min", -0.02),   # new must not be >2pp worse
    "test_auc":       ("min", -0.02),
}

def same_schema_best(registry: dict, new_feature_columns: list[str]) -> dict | None:
    """The best-metrics entry among versions sharing this run's exact
    feature_columns -- never compare across a feature-set change."""
    candidates = [
        v for v in registry.values()
        if v.get("feature_columns") == new_feature_columns
    ]
    if not candidates:
        return None  # first model of this schema generation -- nothing to gate against yet
    return max(candidates, key=lambda v: v["metrics"]["test_auc"])["metrics"]


def assert_promotion_ready(new_metrics: dict, best_metrics: dict | None) -> None:
    if best_metrics is None:
        return  # nothing comparable exists yet; can't gate, don't block
    failures = {
        name: (new_metrics[name], best_metrics[name])
        for name, (direction, tolerance) in PROMOTION_GATES.items()
        if direction == "min" and new_metrics[name] < best_metrics[name] + tolerance
    }
    if failures:
        raise ValueError(
            f"New model regressed vs. same-schema best on held-out test set: {failures}. "
            "Not promoting to latest -- investigate before overriding."
        )
```

Needs zero new infrastructure — `models/xgb_registry.json`/`lr_registry.json`
already store both models' test metrics; this just reads them before the
write instead of never reading them. Failing loud (raise, not a silent
skip) matches this codebase's own established alerting pattern
(`send_alert_email()` on unhandled exception) — wire the training scripts'
own failure path into that if this is ever run as an automated retrain,
not just manually.

**2. Live weekly drift monitoring (the backlog doc's original scope, still
separate, still valid).** A model that *passes* the promotion gate above
can still degrade in-season from real distribution shift. Piggyback on
`weekly_model_eval.py`/the existing Tuesday backfill step
(`cache_builder.py::_run_weekly_backfill_if_tuesday`) rather than
provisioning a new job — same pattern already used for the betting-alert
piggyback (PR #120). Compare each week's accuracy against a rolling
baseline (e.g. trailing 4-week average) rather than a single-week
threshold, since NFL outcomes are noisy by nature — this answers the
backlog doc's "what threshold, over what window" question directly: use a
rolling window, not a single-week trigger, to avoid crying wolf on normal
variance. Alert via the existing `send_alert_email()` path.

### Feature computation versioning (resolves `feature-computation-versioning`)

The backlog doc's open questions, answered:

- **Granularity:** single repo-wide version, not per-feature-family — this
  project's scale doesn't justify the bookkeeping overhead the backlog doc
  itself flagged as a tradeoff.
- **Git commit SHA, not a maintained semantic version.** The backlog doc's
  own evidence is that manual version-bumping already failed twice (the
  2026-08-15 preseason-profile fixes, the injury-grading change) — a SHA
  needs no human to remember anything, which is the exact failure mode
  this spec exists to prevent. Today added a third: the snap-share fix
  itself, deployed 2026-09-18 with no way to mark which predictions were
  computed before vs. after it, other than session memory.
- **Storage:** alongside `ensemble_version` in `prediction_features` docs
  (`services/cache_service.py::write_prediction_features()`), stamped from
  whatever commit SHA is checked out at write time.
- **Retroactive backfill:** no — forward-only from whenever this lands.
  Not worth trying to reconstruct which commit produced historical rows.

## Review checklist and anti-patterns (adapted from ECC's `mle-workflow`
skill, MIT-licensed, evaluated 2026-09-18 — not installed as a dependency,
just used as reference; see chat history for the full evaluation)

Apply this checklist at every stage below, not just where a bug has
already been found:

- [ ] Leakage risk checked against prediction-time availability (the
  snap-share bug's exact shape — check it again at every stage, not just
  where it was already found)
- [ ] Training and serving feature-computation code are shared or
  equivalence-tested, never manually duplicated
- [ ] Metrics compare against the registry's own `best`/baseline, not just
  "did this number go up," before anything gets called `latest`
- [ ] Model version is present on every stored prediction (already true —
  `ensemble_version`); feature-computation version is not yet (see above)
- [ ] Rollback is "switch to a known-good artifact," not "retrain and
  hope" — confirmed true today (v1/v3 already exist and are loadable), but
  nothing exercises this path yet
- [ ] Monitoring covers prediction *quality*, not just job/service uptime
  (uptime already covered; quality is the gap this section resolves)

Anti-patterns worth actively grepping for during Stages 1/3/4, since they
're exactly the shape of bug already found once:

- Feature joins that ignore event time / label availability (the snap-share bug)
- Training-only feature code manually copied into serving code, or vice versa
- A model promoted to production without being compared against the
  current production model on the same test set
- A quality signal that only checks "did the job run," not "were the
  predictions any good"

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

**Run once on 2026-09-18, then re-run the same day under corrected
direction** — the first pass compared "latest" against the registry's
`best_by` pointer without checking whether `best_by` was itself from a
different, incomparable feature-schema generation. It was (see "Folded-in
designs" → "Model quality gating" above for the full correction). Re-run
Stage 2 scoped correctly before trusting any of its conclusions:

1. **[Corrected, needs re-audit]** Within the honest (no-`spread_line`)
   feature schema, XGB v9/LR v7 ("latest") are the weakest of their own
   generation (XGB v4-v9 AUC 0.582→0.538, LR v2-v7 AUC 0.583→0.556) — real,
   but a 0.54-0.60 range, not "coin-flip vs. 0.66." **Do not roll back to
   `best` as currently labeled** — for XGB that's v3, for LR that's v1,
   both from before the de-Vegas pass and both still carrying `spread_line`
   as an input feature; rolling back would silently reintroduce the exact
   leakage that pass existed to remove. The re-audit should instead: (a)
   confirm the same-schema-only framing above is right by checking every
   other registry entry's `feature_columns`, not just the ones already
   spot-checked; (b) since training data range and hyperparameters are
   identical across the whole honest cohort for XGB (confirmed), determine
   whether the decline traces to specific `nn_feature_engine.py` changes
   between each retrain date (May 26 - Aug 22) or is plausibly just noise
   from the ~48-game held-out test window; (c) only decide retrain-vs-roll
   forward once that's answered, and only against a same-schema baseline.
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
   is the exact display-side symptom of the versioning gap now designed
   in "Feature computation versioning" above — once the version stamp
   lands, this check becomes "does the modal show it," not "can we even
   tell."
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
- Live weekly drift monitoring's actual implementation (design is folded
  in above; building it is its own implementation plan, not a step of
  this review).
- Any UI/UX changes beyond what's needed to fix a found correctness bug.
- Installing the ECC plugin, or any other third-party skill/agent bundle,
  as a dependency — only the `mle-workflow` skill's checklist/pattern
  content was evaluated and adapted, nothing was installed.

## Rollout

1. Run Stage 1 fresh (prior sub-agent was killed mid-run).
2. Run Stages 3 and 4.
3. Consolidate all four stages' findings into one severity-ordered list,
   scored against the "Review checklist and anti-patterns" section above.
4. Decide, with the user, which findings become their own implementation
   plans (via `superpowers:writing-plans`) and in what order. At minimum,
   expect:
   - A Stage 2 model rollback-or-retrain decision.
   - Implementing the training-time promotion gate (above) — small,
     self-contained, directly prevents today's XGB v9/LR v7 bug from
     recurring, reasonable to do early regardless of what else is found.
   - Implementing the feature-computation-version stamp (above) — small,
     self-contained, makes every subsequent regrade in this review
     provable rather than remembered.
   - Deciding whether live weekly drift monitoring gets built now or
     stays backlog once the promotion gate exists (it covers a different,
     rarer failure mode: a model that passed promotion but degraded
     in-season).
5. Update the two original backlog docs
   (`2026-08-22-model-quality-drift-monitoring-design.md`,
   `2026-08-22-feature-computation-versioning-design.md`) with a
   "superseded by this spec" pointer, and move them to `completed/` once
   the promotion-gate and version-stamp pieces actually land (design is
   done here; implementation is what completes them).
6. Re-run the same regrade pattern used for the snap-share fix
   (`backfill_schedule_predictions.py --force --features [--firestore]` +
   `weekly_model_eval.py --firestore`) once real fixes land, so Week 1
   2026's stamped accuracy number keeps reflecting the current state of
   truth rather than going stale again.
