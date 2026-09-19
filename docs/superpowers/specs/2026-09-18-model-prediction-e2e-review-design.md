# Model Prediction Pipeline — End-to-End Review

**Date:** 2026-09-18
**Status:** Audit complete (all 4 stages) — ready to consolidate findings and
write implementation plans per fix (see "Rollout")

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
- **Storage — corrected by Stage 4's finding:** not just `prediction_features`
  docs. Stage 4 found the *prediction* record itself
  (`game_predictions`/`.local_db/game_predictions_{year}.json`) carries no
  `model_version`/`ensemble_version` field anywhere, top-level or inside
  `explanation` — so there's currently no way, even in principle, to know
  which model produced a given locked prediction's win probability, not
  just whether its feature audit matches. The commit-SHA stamp needs to
  land on both: `prediction_features` docs (alongside `ensemble_version`,
  as originally scoped) *and* every `game_predictions` entry written by
  `cache_builder.py`'s daily write and `backfill_schedule_predictions.py`.
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

**Complete, 2026-09-18 — essentially clean.** One minor finding, everything
else confirmed clean with cited evidence:

1. **[Minor, structural cleanup, no live bug]** `compute_preseason_roster_features()`
   (`nn_feature_engine.py:375-386`) still has the pre-fix Bug B pattern
   (reads the non-week-indexed rolling `roster_{year}.csv`), but it's fully
   dead — zero real call sites anywhere (`grep -rn
   "compute_preseason_roster_features(" services/ scripts/ routes/ tests/`
   finds only the `def` itself and an unused import). Its own docstring
   says it was replaced. No risk today; risk only if someone re-wires it
   back in without noticing. Worth deleting during whatever PR picks up
   this review's other Stage 1 cleanup, low priority on its own.
2. **Rolling features (checklist item 1) — clean.** `_load_rolling_epa`,
   `_load_trench_rolling_stats`, `_load_box_stats_from_weekly`, the
   point-differential block, and `_load_pressure_stats` all use the
   identical `.groupby(...).transform(lambda s: s.expanding().mean().shift(1))`
   pattern — every one excludes the current week's own row, and the
   week-1 NaN-fill fallback only uses *prior-season* averages, never
   current-season same-week data.
3. **Roster-value week-indexing / Bug B (item 2) — confirmed fixed in the
   live path.** `services/roster_value_service.py:428` and
   `nn_feature_engine.py:1297` both read `weekly_rosters/roster_weekly_{season}.csv`;
   `compute_preseason_player_profiles` takes an explicit `week` arg and
   filters to it; the EPA inputs blended in always come from the season
   *before* `target_season`, never same-season in-week stats.
4. **Other data sources / depth charts (item 3) — clean.**
   `_load_declared_starters` uses `merge_asof(..., direction="backward")`
   against each week's own earliest kickoff — picks the latest depth-chart
   snapshot strictly before kickoff.
5. **Elo (item 4) — clean.** `scripts/compute_elo.py` captures
   `home_elo_pre`/`away_elo_pre` before calling `_update_game()`, which
   only mutates state for later iterations. Causal by construction.
6. **Bonus, cross-checking Stage 2's git-log dig:** independently confirmed
   the `off_rush_epa` scale bug is fixed (explicit code comment + correct
   key separation) and both minimum-sample-size gates (offense, DL) are
   present today — supports Stage 2's read that the honest-cohort AUC
   decline isn't explained by these specific bugs still being live.

### Stage 1b — Feature improvement: opponent-adjusted EPA (DVOA-style)

Distinct sub-goal, added 2026-09-18: not "is what's there correct" (items
1-4 above), but "is there a better feature to add." Promoted from
backlog (see prior memory note on the DVOA idea, 2026-09-09) now that
Stage 1 is auditing this exact file anyway.

- **What exists today:** `pass_epa_matchup`/`rush_epa_matchup`/
  `early_down_matchup` (`FEATURE_COLUMNS`, computed at
  `services/nn_feature_engine.py:2390-2398`) are a **raw** matchup delta —
  `(home_off − away_def) − (away_off − home_def)` — not opponent-adjusted.
  No strength-of-schedule regression, no situational weighting (down/
  distance), no garbage-time exclusion. A team's raw offensive EPA against
  a string of bad defenses reads the same as the same EPA against good
  ones.
- **What's being asked for:** an EPA-based proxy for DVOA (Football
  Outsiders/FTN's opponent-adjusted efficiency metric) — covering both
  offense and defense. Real DVOA is proprietary with no public API or free
  feed, so this means building an approximation, not ingesting the real
  thing.
- **Data already available:** `rawdata/pbp/play_by_play_{year}.csv` is
  already synced locally back to 1999 (confirmed 2026-09-18, no gap to
  fill before starting) — this is the right source for a from-scratch
  opponent-adjustment (per-play EPA with the actual opponent faced that
  play, not just a season-level average).
- **Scope reality check:** this changes `FEATURE_COLUMNS`, which means (per
  CLAUDE.md and [[project_ml_de_vegas]]'s prior cycle) a full retrain of
  NN/XGB/LR plus walk-forward validation (`scripts/walk_forward_validate.py`)
  before it's trustworthy — not a quick add, and not something to bundle
  into the same pass as Stage 1's correctness fixes. Treat as its own
  follow-up spec once Stage 1's correctness checklist is done: scope the
  actual opponent-adjustment methodology (regression-based SOS adjustment
  vs. a simpler weighted-opponent-average proxy) as a full brainstorming
  pass, not decided here.
- **Sequencing:** do Stage 1's correctness checklist (items 1-4) first —
  no point building a new feature on top of a feature-engine file that
  might still have live causality bugs. This sub-goal is the natural
  next step after Stage 1 is clean, before or alongside Stage 2's
  promotion-gate work (a new feature is exactly the kind of change the
  promotion gate should be gating).

## Stage 2 — Training (`train_nn_model.py`/`train_xgb_model.py`/`train_lr_model.py`)

**Run once on 2026-09-18, then re-run the same day under corrected
direction** — the first pass compared "latest" against the registry's
`best_by` pointer without checking whether `best_by` was itself from a
different, incomparable feature-schema generation. It was (see "Folded-in
designs" → "Model quality gating" above for the full correction). Re-run
Stage 2 scoped correctly before trusting any of its conclusions:

1. **[Re-audited 2026-09-18 — downgraded from "severe" to "structural, not
   proven"]** Within the honest (no-`spread_line`) feature schema, XGB
   v9/LR v7 ("latest") are the *numerically* weakest of their own
   generation (XGB v4-v9 AUC 0.582→0.538, LR v2-v7 AUC 0.583→0.556), with
   identical training data range/hyperparameters across the whole XGB
   cohort (confirmed) — so no config difference explains it. 28 commits
   touched `services/nn_feature_engine.py` between the v6 and v9 training
   dates (2026-05-26 to 2026-08-22), including real bugs alongside
   deliberate redesigns (`off_rush_epa`'s scale was wrong by ~5 orders of
   magnitude, ±30K vs. the intended ±0.1, until a fix landed mid-window;
   the preseason multi-season blend's minimum-sample-size gate was lost
   and re-added twice) — a plausible mechanism for real drift.

   **But:** the held-out test set is only ~48 games (season 2025, weeks
   16-18). At that size, accuracy alone carries a standard error around
   ±7 percentage points from sampling noise alone. The entire observed
   spread across the honest cohort (0.538-0.597 AUC) sits well within
   what pure noise could produce — **this data cannot distinguish "v9 is
   a real regression" from "this is noise."** Do not treat the numeric
   ranking as proof the current model is broken.

   **Do not roll back to `best` as currently labeled either way** — for
   XGB that's v3, for LR that's v1, both from before the de-Vegas pass and
   both still carrying `spread_line` as an input feature; rolling back
   would silently reintroduce the exact leakage that pass existed to
   remove.

   **What's actually actionable here:** not "fix the model," but the
   structural gaps that made this question unanswerable in the first
   place — no promotion gate (even a noisy one beats none), no
   feature-computation version stamp (so nobody could even reconstruct
   which of those 28 commits produced which training run's inputs), and a
   test set too small to give a confident verdict regardless. Stage 1
   should separately check whether the `off_rush_epa` scale bug and the
   twice-lost sample-size gate are fully fixed today, independent of
   whether they explain any historical metric movement.
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

**Complete, 2026-09-18.** Findings, most severe first:

1. **[Structural, retroactive-grading path only]** `services/nn_prediction_service.py:106-119`
   (`build_ensemble_lookup()`, used by `cache_builder.py`'s daily write and
   `backfill_schedule_predictions.py`) independently re-implements the same
   ATS-pick/edge-vs-vegas formula as `derive_prediction_scalars()`
   (`services/nn_projection_engine.py:1010-1041`, the MC-simulation/future-game
   path, already verified correct). Both currently compute identical results
   — no live bug — but they're two separately-maintained copies of one
   formula, not one shared function. Exactly the checklist's "training-only
   code copied into serving code" anti-pattern. A future fix to one (like
   today's ATS-sign re-verification, which only touched
   `derive_prediction_scalars`) wouldn't propagate to the other. Recommend
   consolidating into one shared function this review's implementation
   plan should include.
2. **[Structural, now measured — tight, not broken]** `RESIMULATE_LEAD_MINUTES=20`
   remains unvalidated in production, but a real local timing test gives a
   concrete number: `python scripts/cache_builder.py --resimulate
   2026_02_CAR_ATL` (warm environment, real nflverse sync included) took
   **443.8s (7m 24s)** — sync itself was only 15.8s of that, so ~428s went
   to `engine.initialize()` (6-season feature table + roster value, per
   its own docstring) + `simulate_season()`. That's **37% of the entire
   20-minute budget, before any Cloud Run cold-container-start penalty**
   (commonly 30-90s+ for a TensorFlow-loading image) is added on top.
   Not currently broken, but tight enough that the original "unvalidated"
   flag was well-founded — worth either increasing the lead time or
   profiling `engine.initialize()` for the actual bottleneck before
   trusting this close to a real kickoff.
3. **[Corrected — narrower than Stage 2 assumed, no action needed]** The
   "duplicate `prediction_service.py` system" is real but not the live risk
   it looked like: `PredictionService` (Elo+Pythagorean) backs exactly 5
   routes in `routes/prediction_routes.py`, and grepping every frontend
   file confirms **none of the 5 are called from any page** — dead,
   unreachable product surface, not a second prediction system users
   actually see. Also dead: unused imports of `PredictionService` in
   `services/draft_service.py:87` and `scripts/cache_builder.py:48`. Worth
   a decision (keep as legacy/fallback API, or delete) but not urgent.
4. **Checked clean — 45/20/35 blend consistency.** Exactly two blend sites
   exist (`nn_projection_engine.py:626` for the MC/preseason path,
   `nn_prediction_service.py:90` for the daily-write/backfill path, the
   latter confirmed shared, not duplicated), both sourcing
   `NN_WEIGHT`/`XGB_WEIGHT`/`LR_WEIGHT` from `services/constants.py`. No
   hardcoded duplicate weights found anywhere.
5. **Checked clean, with a structural caveat — "explanation drift" (Bug C).**
   The original crisis (daily job silently overwriting `explanation` with a
   thin dict) is genuinely fixed — `cache_builder.py:99-124` explicitly
   drops `explanation` from an entry rather than writing a thin one.
   Verified against real 2026 data: 272 predictions, 0 mismatches between
   top-level and nested `model_spread`. **Caveat:** that check only proves
   "clean right after a fresh backfill" — top-level fields still refresh
   daily via whatever feature code exists that day, while `explanation`
   stays frozen until the next Tuesday backfill, so divergence is still
   structurally possible over time, just not currently observed. Same root
   cause as, and another argument for, the feature-computation-versioning
   design already folded into this spec.

## Stage 4 — Explanation Surfaces

**Complete, 2026-09-18.** Findings, most severe first:

1. **[Structural, confirmed, retroactive-grading path — expands the
   already-folded-in feature-computation-versioning design]** There's no
   version join at all, and it's more fundamental than that design assumed:
   `services/cache_service.py::get_prediction_features(season)` (called
   with no explicit version from `api_routes.py:372`) returns "whichever
   `--features` backfill ran last for the season," full stop — no way to
   ask for a specific version. Worse: a real locked prediction
   (`.local_db/game_predictions_2026.json`, key `W01_SEA_NE`, inspected
   directly) carries **no `model_version`/`ensemble_version` field
   anywhere** — not top-level, not inside `explanation`. It's not just
   that the feature audit *might* be stale relative to the prediction it
   explains — there is structurally no way, even in principle, to verify
   which model version produced any given locked prediction's win
   probability. The already-designed fix needs to stamp the *prediction*
   record itself, not just the feature-audit doc — updating "Folded-in
   designs" above to reflect this. Severity: retroactive grading/
   explanation only, the live win-probability number itself isn't wrong,
   only its historical provenance is unrecoverable.
2. **Checked clean — `/api/predictions/explain` reads the real stored
   prediction, does not recompute.** `routes/api_routes.py:236-237`:
   `get_game_predictions(season)` → `preds.get(key)`. One narrow, scoped
   fallback (patches in `spread_line` from `nfl_games` and recomputes only
   `edge_vs_vegas` if `explanation.vegas_line` is missing) — not a drift
   risk. Note: this endpoint never returns `is_correct`/`is_correct_ats` at
   all (see finding 3b).
3a. **Checked clean, by construction — SU/ATS consistency between the
   per-game table and the Feature Debug modal.** `admin_accuracy.js`'s
   `_scoreSummaryHtml` reads `is_correct`/`is_correct_ats` from
   `_gameCache`, the exact same object the per-game table renders from —
   never independently re-fetched or recomputed. Structurally impossible
   for these two to disagree.
3b. **Gap, not a bug — the "Why TEAM?" modal never shows SU/ATS grade at
   all.** Not inconsistent with the other surfaces (finding 2's endpoint
   doesn't carry those fields), just incomplete relative to them. Fold
   into the version-stamp/explanation-consistency work (finding 1) rather
   than treating as new scope.
3c. **Narrows `docs/superpowers/specs/2026-09-18-betting-pick-type-clarity-design.md`
   — both modals are already unambiguous.** `schedule_explain.js` renders
   an explicitly labeled "ATS Pick" card (gold-highlighted when it differs
   from the straight-up pick); the Feature Debug modal already shows
   separately-labeled SU/ATS grade lines (verified in-browser earlier
   today). The ambiguity that stub spec found is confined to the
   betting-alert email (already partially fixed today, `4b9b114`) and the
   admin betting screener — updating that stub to reflect the narrower
   scope.
4. **Checked clean — no other class collision.** `schedule_explain.js`'s
   delegated listener targets exactly one class, `.pred-explain-btn`; grep
   confirms no stray reintroduction anywhere, and the file is loaded by
   exactly 2 templates (`admin.html`, `schedule.html`) — no third page to
   worry about.

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

### All 4 stages complete (2026-09-18) — consolidated findings, most severe first

1. **[Severe → downgraded to structural]** Same-week snap-share leakage in
   `compute_qb_availability_flags()` — **fixed, committed, deployed**
   (`2b3df61`). The Stage 2 model-quality finding that initially looked
   severe was itself corrected twice (invalid leaky-generation comparison,
   then a noise-vs-signal check) down to: no proven live regression, but a
   real structural gap (items 3-5 below).
2. **[Structural, confirmed twice — Stage 4 expands Stage-2-era scope]**
   No feature-computation-version stamp anywhere: not on
   `prediction_features` docs (as originally scoped) *and*, per Stage 4,
   not on `game_predictions` entries either — there's no way to know which
   model or which feature-engine commit produced any given locked
   prediction. Design complete ("Folded-in designs" above); not yet
   implemented.
3. **[Structural, confirmed]** No training-time promotion gate — nothing
   stopped XGB v9/LR v7 from becoming `latest` despite being the weakest
   of their own honest feature-schema generation. Design complete
   (schema-scoped, corrected after the de-Vegas mistake); not yet
   implemented.
4. **[Structural, confirmed]** Two independently-maintained copies of the
   ATS-pick/edge-vs-vegas formula (`nn_prediction_service.py::build_ensemble_lookup`,
   `nn_projection_engine.py::derive_prediction_scalars`) — currently
   consistent, but a future fix to one wouldn't propagate to the other.
   Not yet designed as its own item; straightforward consolidation into
   one shared function.
5. **[Structural, measured, not yet acted on]** `RESIMULATE_LEAD_MINUTES=20`
   is tight — a real warm-environment run took 443.8s (37% of budget)
   before any Cloud Run cold-start penalty. Not broken today; worth
   increasing the lead time or profiling `engine.initialize()` before it
   is.
6. **[Minor, low priority]** Dead code: `compute_preseason_roster_features()`
   (Stage 1) and `PredictionService`'s 5 unreachable routes plus 2 dead
   imports (Stage 3) — safe to delete whenever convenient, zero functional
   risk either way.
7. **[Gap, not a bug, low priority]** The "Why TEAM?" modal never shows
   SU/ATS grade (Stage 4, finding 3b) — fold into the version-stamp work
   rather than treating as separate scope.
8. **[Presentation only, already partially fixed]** Betting pick-type
   clarity (ATS vs. moneyline/SU) — confined to the betting-alert email
   (partially fixed today, `4b9b114`) and the admin betting screener, per
   Stage 4's finding that both explanation modals are already unambiguous.
   See `docs/superpowers/specs/2026-09-18-betting-pick-type-clarity-design.md`
   (needs updating to reflect this narrower scope).
9. **[Backlog, not part of this rollout, low priority]** Stage 1b —
   opponent-adjusted EPA (DVOA-style) as a new feature. Not a correctness
   fix like items 1-8; a proposed feature addition that changes
   `FEATURE_COLUMNS` and therefore requires a full NN/XGB/LR retrain +
   walk-forward validation before it's trustworthy. Needs its own
   brainstorming pass to scope the actual methodology (regression-based SOS
   adjustment vs. a simpler weighted-opponent-average proxy) — see the
   "Stage 1b" section above for full detail. Confirmed 2026-09-18: user
   wants this kept as backlog, not folded into the current 4-item rollout
   (items 1-4 below), so it doesn't get lost.

### Next steps

1. Decide, with the user, which findings above become their own
   implementation plans (via `superpowers:writing-plans`) and in what
   order. Suggested order, cheapest/highest-leverage first:
   - #3 (promotion gate) and #2 (version stamp) together — #3 depends on
     #2 existing to be trustworthy per its own design, so they're
     naturally one plan, not two.
   - #4 (consolidate the duplicated ATS/edge formula) — small, mechanical,
     no design work needed beyond picking which implementation is the
     source of truth.
   - #5 (resimulate timing) — needs a profiling pass on
     `engine.initialize()` before deciding whether to extend the lead
     time or optimize the bottleneck.
   - #6/#7/#8 — low-priority cleanup, batch into whichever other PR is
     already touching the relevant file.
   - The Stage 2 model rollback-or-retrain decision — explicitly deferred
     until #2/#3 exist, so a retrain (if chosen) doesn't just repeat the
     same unmeasured process.
   - Live weekly drift monitoring (folded-in design, separate from the
     promotion gate) — still backlog-shaped; decide whether it's worth
     building now or waiting to see if the promotion gate alone is enough.
2. Update `docs/superpowers/specs/2026-09-18-betting-pick-type-clarity-design.md`
   to reflect Stage 4's narrower scope finding (modals are fine; only the
   email and admin screener need work).
3. Update the two original backlog docs
   (`2026-08-22-model-quality-drift-monitoring-design.md`,
   `2026-08-22-feature-computation-versioning-design.md`) — already point
   to this spec; move them to `completed/` once the promotion-gate and
   version-stamp pieces actually land (design is done here; implementation
   is what completes them).
4. Re-run the same regrade pattern used for the snap-share fix
   (`backfill_schedule_predictions.py --force --features [--firestore]` +
   `weekly_model_eval.py --firestore`) once real fixes land, so Week 1
   2026's stamped accuracy number keeps reflecting the current state of
   truth rather than going stale again.
