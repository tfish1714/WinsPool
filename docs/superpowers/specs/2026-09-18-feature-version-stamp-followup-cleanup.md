# Feature-Version Stamp + Promotion Gate — Follow-Up Cleanup

**Date:** 2026-09-18
**Status:** Backlog — parked findings from implementing
`docs/superpowers/plans/2026-09-18-feature-version-stamp-promotion-gate.md`.
None of these block that branch; all were explicitly ruled non-blocking
during implementation (task reviews or the final whole-branch review's own
triage). Collected here so they aren't lost.

**Explicitly excluded from this doc** (tracked/handled separately, not
here): the other 3 items in the original 4-item rollout order this plan's
own item #1 came from — consolidating the duplicated ATS-pick/edge-vs-vegas
formula, profiling `RESIMULATE_LEAD_MINUTES`, and the low-priority cleanup
batch (dead code, "Why TEAM?" modal SU/ATS display, betting pick-clarity
stub). All three are already tracked in
`docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md`'s
Rollout section — this doc is only for things that surfaced *during*
implementing item #1, not the rest of the rollout.

## Decisions to make deliberately (not yet acted on)

- **The NN model registry's `feature_columns` gap is now visible, but still
  empty for all 15 existing entries.** Task 5 fixed `save_versioned()` to
  stamp `feature_columns` onto every *new* NN registry entry, and the final
  review confirmed (against the real `models/model_registry.json`) that all
  15 pre-existing entries still lack it — so the promotion gate has no
  same-schema baseline to compare against for NN until the next real NN
  retrain happens. This is correct, forward-only behavior per the plan's
  own constraint (no retroactive backfill onto historical records) — but
  it's worth being a *conscious* choice, not a surprise: **do not**
  backfill `feature_columns` onto the old entries using today's
  `FEATURE_COLUMNS` list. The real registry shows NN's schema went 33 → 32
  → 27 columns across v1–v15; stamping today's 27-column list onto an old
  33-column entry would manufacture exactly the invalid cross-schema
  comparison this whole plan exists to prevent. Task 5's fix
  (`services/model_promotion.py`'s "no same-schema baseline → no-op, now
  logged" behavior) already makes this state diagnosable — leaving it
  inert is the right call, this bullet just exists so the reasoning is on
  the record.
- **Expect the promotion gate to fire for real on the next LR retrain, and
  that's it working as designed, not a false positive.** The final review
  checked the real `models/lr_registry.json`: the same-schema LR best is
  v5 (`test_accuracy` 0.5625, `test_auc` 0.5764), while the currently
  "latest" v7 sits at `test_accuracy` 0.50 — 6.25pp below, well outside
  even the widened `-0.05` tolerance. The first real encounter with the
  gate will be a block, not a pass. Worth knowing ahead of time so the
  reflex when it happens is "investigate the regression," not
  `--force-promote` out of habit.

## Observability / durability gaps

- **`--force-promote` discards the exact failure detail it's overriding.**
  `services/nn_prediction_service.py`, `services/xgb_prediction_service.py`,
  `services/lr_prediction_service.py` all use a bare `except ValueError:`
  around the gate check and log a fixed string when `force_promote=True`.
  The caught `ValueError` carries the full `{metric: {new, best,
  tolerance}}` payload from `assert_promotion_ready`, and it's thrown away.
  Change each to `except ValueError as e: logger.warning("... saving
  anyway: %s", e)` so the override's log line actually shows what was
  overridden.
- **No durable record that a registry entry was force-promoted.** A
  force-promoted version is indistinguishable from one that passed the
  gate cleanly, once saved. Consider stamping `"force_promoted": True`
  onto the entry dict in `save_versioned()` when the override actually
  fires — cheap, and it's the audit trail that makes having the override
  at all safe.

## Docker build efficiency

- **`ARG GIT_SHA`/`ENV GIT_SHA` in `Dockerfile.sync`/`Dockerfile.predict`
  are placed above the `pip install` layer**, which busts Docker's layer
  cache on every build (the SHA changes every deploy). No production
  impact today — neither `cloudbuild-*.yaml` passes `--cache-from`, so
  Cloud Build is already doing a cold build every time — but it does slow
  down local `docker build` iteration, including this plan's own Step 6
  manual verification. Move the `ARG`/`ENV` block to just above `COPY . .`
  (after the `RUN pip install` layer) in both Dockerfiles.

## Minor wiring gaps (harmless today, worth tightening)

- **`scripts/backfill_schedule_predictions.py`'s `main()` computes
  `feature_version` but doesn't pass it into `write_prediction_features()`
  calls** (`write_prediction_features(year, ensemble_version, games_dict,
  use_local=...)`, no `feature_version=` kwarg). The callee auto-computes
  its own via a second `git rev-parse` per season when omitted — same
  value in practice on a single run, so harmless — but it's a missed
  wire-through of an already-computed value, and it leaves a theoretical
  seam where the `prediction_features` audit doc and the `game_predictions`
  entries for the same run could disagree if the two `git rev-parse` calls
  ever raced across a mid-run commit (extremely unlikely, but avoidable by
  just passing the value through).
- **`scripts/predict_season.py::_model_version_string()` remains a
  separate producer of the `nn_vX+xgb_vY+lr_vZ` string format**, not
  wired to the new `services/model_version.py::build_ensemble_version_string()`
  helper. Pre-existing, not introduced by this plan, and genuinely not a
  drop-in replacement as-is — it reads version strings out of registry
  files directly rather than off already-loaded service objects, so
  switching it over would need its own small refactor. Noting only so
  "one canonical ensemble-version builder" isn't over-claimed as fully
  true repo-wide.
- **Cosmetic guard-style inconsistency between the two backfill/build
  paths.** `scripts/cache_builder.py::_publish_game_probs` stamps
  `ensemble_version` with `if ensemble_version:` (skip if falsy);
  `scripts/backfill_schedule_predictions.py::_build_predictions_map`
  auto-defaults `feature_version` first, then assigns unconditionally.
  Both are correct for their own function's contract, just stylistically
  different. Not worth unifying on its own; if either function is touched
  again for another reason, consider aligning the two idioms.
- **The web-service `Dockerfile` (the main app image, not the two Cloud
  Run Job images) never gets `GIT_SHA` baked in** — only `Dockerfile.sync`/
  `Dockerfile.predict` do. Currently harmless: no route reachable from the
  web service calls `write_prediction_features()` (verified during the
  final review). If that ever changes, `get_feature_version()` would
  silently fall back to `"unknown"` for a web-service-triggered write. A
  one-line preventive addition (same `ARG`/`ENV` pattern) to the main
  `Dockerfile` would close this before it matters.

## Test coverage gaps (all judged low-risk, none upgraded to blocking)

- **`services/model_promotion.py`'s degenerate single-class-test-split
  case is untested and only loosely documented.** If an XGB/LR training
  run's held-out test set happens to be single-class (all wins or all
  losses — `len(np.unique(y_test)) > 1` failing in
  `xgb_prediction_service.py`/`lr_prediction_service.py`), that entry gets
  `test_accuracy` but no `test_auc`, and could flip the whole candidate
  set's ranking key from `test_auc` to `test_accuracy` if it coexists with
  `test_auc`-bearing entries in the registry. Judged practically
  unreachable (a full ~48-64 game season test split going entirely one way
  never happens in real NFL data) but a one-line docstring note on
  `find_same_schema_best` would make the assumption explicit rather than
  implicit.
- **`tests/test_promotion_gate_xgb.py` has no filesystem-level proof that
  the gate raises before any file write happens** — it relies on
  `pytest.raises` + a mocked `model.save_model`, which can't distinguish
  "raised before writing" from "raised, but a write already happened and
  the mock just swallowed it." The ordering was independently verified
  correct by reading the method directly during task review, but a
  one-line addition (`assert not (tmp_path / "xgb_v4.json").exists()`
  after the `pytest.raises` block, since `MODEL_DIR` is already patched to
  `tmp_path` in that test) would make it a real filesystem assertion
  instead of an inference.
- **`tests/test_cache_service.py` doesn't test `feature_version` through
  `get_prediction_features()`'s "latest for season" glob-lookup path** —
  only the explicit-`ensemble_version` direct-doc-id path is covered. Low
  risk: that lookup path returns the whole stored JSON document unmodified
  (no per-key projection that could drop a field), so there's no code path
  by which this specific gap could actually lose the stamp — but a test
  would still close the coverage hole for completeness.
- **`scripts/backfill_schedule_predictions.py`'s `main()` line that
  replaced its own inline ensemble-version f-string with the shared
  `build_ensemble_version_string()` helper has no direct test** — only
  `_build_predictions_map()` is tested in isolation. Low risk since it's a
  1:1 substitution with an already-tested helper and the call site was
  read directly during review, but a `main()`-level integration test would
  close it.

## Nice-to-have follow-on features (not gaps, just ideas surfaced along the way)

- **Surface `feature_version` in the admin forecast/explain UI.**
  `routes/admin_routes.py` already reads `ensemble_version` off the same
  `prediction_features` doc it would need to also read `feature_version`
  from — this is a one-line addition, not a new data flow, and it's the
  difference between the new stamp being genuinely useful to a human
  debugging a stale-looking prediction versus being write-only data no one
  ever looks at.
- **Update `CLAUDE.md`** with the `--force-promote` CLI flag (on all three
  `scripts/train_*.py` scripts) and the `GIT_SHA` Docker build-arg
  contract (`Dockerfile.sync`/`Dockerfile.predict` + `cloudbuild-*.yaml` +
  `deploy/deploy.ps1`) — both are now part of the deploy/training surface
  and CLAUDE.md is this repo's durable record for exactly that kind of
  contract.
