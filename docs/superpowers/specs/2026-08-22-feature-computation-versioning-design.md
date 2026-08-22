# Feature Computation Versioning

**Date:** 2026-08-22
**Status:** Not designed — backlog item, split out of the injury-aware-roster-value plan's final MLOps-lens review so it doesn't get lost. Needs a proper brainstorming pass before implementation.

## Origin

Surfaced while reviewing `docs/superpowers/plans/2026-08-22-injury-aware-roster-value.md` (already implemented and merged) through an MLOps-pipeline-quality lens: that plan changed what `off_roster_value_delta`/`def_roster_value_delta`/`st_value_delta`/`qb_resilience_delta` actually mean (graded by injury severity, week-aware instead of frozen) — but nothing in the stored prediction data marks which "meaning" of those features produced a given prediction.

## What's already verified

- `models/model_registry.json` / `xgb_registry.json` / `lr_registry.json` track **model** versions (`nn_v14`, `xgb_v8`, etc.) with `latest`/`best` pointers — this part is solid.
- `services/cache_service.py::write_prediction_features()` stamps an `ensemble_version` string (e.g. `"nn_v14+xgb_v8+lr_v6"`) onto its audit docs — but this identifies which **model weights** were used, not which **feature-computation code** produced the input values fed to those weights.
- `game_predictions` docs carry a `locked` flag that protects historical entries from being silently overwritten by a re-run — useful for accuracy-tracking integrity, but it's a write-protection mechanism, not a version marker. Two `locked=True` predictions for the same historical game, made months apart, could have been computed by two different versions of `roster_value_service.py`/`nn_feature_engine.py` with no way to tell them apart from the stored data alone.
- Concretely: this repo has already changed feature-computation logic multiple times without any version bump anywhere (the 2026-08-15 preseason-profile bug fixes, this plan's injury-grading change) — each one silently altered what existing feature columns mean for a subset of historical rows, and there is no way today to ask "was this specific stored prediction computed before or after fix X."

## What's genuinely needed here

Some lightweight marker — stored alongside `ensemble_version` or as its own field — that identifies the feature-computation code version in effect when a prediction was generated, so a future backtest, drift investigation, or "did this change actually help" comparison isn't guessing.

## Open questions to resolve when this is picked up

These need an actual brainstorming pass (clarifying questions → approaches → design), not decided here:

1. **What granularity?** A single repo-wide "feature schema version" bumped whenever `nn_feature_engine.py` or `roster_value_service.py`'s computation logic changes, vs. finer-grained per-feature-family versioning (e.g. roster-value features get their own version independent of EPA/Elo features)? The former is much simpler; the latter is more precise but adds real bookkeeping overhead for a hobby-scale project.
2. **Git commit hash vs. a maintained semantic version?** A commit hash is always accurate and needs no manual bump (the exact failure mode this spec exists to prevent), but is less human-readable and doesn't group "this set of commits together represent one coherent feature version." A maintained version string is more readable but requires someone to remember to bump it — which is exactly what didn't happen for the 2026-08-15 fixes or this plan, until a retrain forced the question.
3. **Where does it get stored?** `prediction_features` docs already have a natural home for it (alongside `ensemble_version`). Does `game_predictions` itself also need it, or is that store meant to always reflect "the latest we've got" with no historical version trail?
4. **Retroactive backfill?** Should already-stored historical predictions get tagged retroactively (best-effort, based on when they were written relative to known commits), or does this only apply going forward from whenever it's implemented?

## Non-goals

- Not a full feature store (e.g. Feast-style point-in-time feature versioning) — that's a much larger undertaking than this project's scale calls for.
- Not retroactively re-deriving exact historical feature values — only about tagging *which code version* was in effect, not reconstructing lost precision.
