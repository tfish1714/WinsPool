# Scheduled Jobs: Hardening Follow-Ups (Live-Score Bug + Test Coverage)

**Date:** 2026-08-20
**Status:** Not designed — backlog, split out of the scheduled-jobs implementation (`2026-08-19-scheduled-jobs.md`) so these don't get lost. Needs its own pass when picked up, not decided here.

## Origin

While implementing and reviewing the scheduled-jobs plan, two things surfaced that are real but were deliberately not fixed inline: a separate pre-existing bug unrelated to this plan's scope, and gaps in test coverage across several of the final review's fixes. Both were explicitly deferred rather than bolted on mid-flight.

## 1. Pre-existing bug: `services/live_score_service.py::sync_live_scores_to_df()`

Found while building `scripts/sync_live_scores.py` (which deliberately does **not** reuse this function — it reimplements the ESPN overlay correctly instead, with a real "don't clobber a final game" guard this function's own docstring claims to have but doesn't).

**What's wrong:** `sync_live_scores_to_df()` normalizes team abbreviations on the wrong side of the match. ESPN returns raw abbreviations (`LAR`, `WSH`, `JAC`) that differ from nflverse's (`LA`, `WAS`, `JAX`) — see `services/utils.py::normalize_team_abbr()`. This function calls `normalize_team_abbr()` on the *repo* side of the comparison (the already-correct side) instead of the *ESPN* side (the side that actually needs it), so the normalization does nothing useful and Rams/Commanders/Jaguars games likely never match.

**Where it's still live:** `scripts/cache_builder.py` still calls `services.live_score_service.sync_live_scores_to_df()` during its nightly analytics build (`build_year()`, feeding `wins_pool_standings` and other cached analytics). This is a separate, older live-score path from the new `scripts/sync_live_scores.py` — the two now coexist with different (and differently buggy vs. differently correct) team-matching logic.

**Why not fixed here:** `sync_live_scores_to_df()` is a shared function with its own existing callers and its own established behavior in the nightly analytics build. Fixing it deserves its own review cycle — verifying it doesn't change `wins_pool_standings` output in some other subtle way — not a bolt-on while mid-flight on the scheduled-jobs plan.

**Questions for whoever picks this up:**
- Is `sync_live_scores_to_df()` still needed at all once `scripts/sync_live_scores.py` is live and already writes `is_live`/`clock`/`period` to `nfl_games` every 5 minutes? `cache_builder.py`'s nightly build might be able to just read what's already in Firestore instead of doing its own separate ESPN fetch.
- If it's still needed (e.g. for a nightly-build-time snapshot independent of the 5-minute job), the fix is straightforward: normalize the ESPN-sourced keys, not the repo-sourced ones — same pattern as `sync_live_scores.py::run_espn_overlay_safely()` already uses.

## 2. Test coverage gaps from the final review's fix wave

The final whole-branch review's one allowed fix wave (9 findings, `docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md`) added zero tests for any of its fixes — the 27/27 passing reported at the time was regression evidence for pre-existing behavior, not verification of the new fixes. Some of this gap was closed in a follow-up session (see below); the rest is still open.

**Closed since the final review** (real tests added, verified against actual behavior):
- `schedule_kickoffs.py`'s `oauth_token` (not `oidc_token`) choice — `TestEnqueueTask::test_uses_oauth_token_not_oidc_token`.
- `schedule_kickoffs.py`'s Cloud Tasks `AlreadyExists` handling — `TestEnqueueTask::test_already_exists_is_caught_not_raised` / `test_other_errors_still_propagate`. These are genuine tests against the real `google-cloud-tasks`/`google-api-core` libraries (installed locally to verify — the original implementation could only verify by inspection, since the package wasn't installed in that session).
- `cache_builder.py`'s own rawdata sync step (added in the same follow-up session, not part of the original 9) — `TestSyncRawdata`, `TestMainSyncWiring`.

**Still open:**
- `cache_builder.py`'s Firebase-init fix (env-var-aware, reusing `daily_nfl_sync.initialize_firebase()`) — no direct test confirming the env-var-vs-local-file branch behaves correctly from `cache_builder.py`'s call site specifically (as opposed to `daily_nfl_sync.py`'s own tests, if any exist, for the function itself).
- `run_cron.py`'s dynamic `--max-season` computation for the `compute_elo.py --firestore` step — no test confirming the computed season value is correct (e.g. across the Sept 1 boundary where the "current season" rolls over) or that it's actually passed through `job_runner`'s `args` mechanism correctly for this specific step.
- `sync_live_scores.py`'s 7-day `gameday` window filter on the `nfl_games` push (separate from the current+prior-season filter on `nfl_standings`, which does have coverage via `compute_standings()`) — no test confirming the boundary behavior (a game exactly 7 days old, a game with a `NaT`/malformed `gameday`, the UTC-vs-ET date-boundary reasoning the final review verified by hand).
- `sync_live_scores.py`'s cache-invalidation-after-overlay ordering — no test confirming the `metadata/cache_control` write happens after `run_espn_overlay_safely()`, not before (a regression here would be silent — nothing would fail, the live badge would just go back to being systematically stale during games).

## Non-goals

- Not re-opening the final review's other explicitly-deferred items (`oidc_token`/`oauth_token` and the `AlreadyExists` handling are now fixed and tested — see above; the models/rawdata gap in `Dockerfile.predict` is fixed via `cache_builder.py`'s own sync step and `.gcloudignore`).
- Not a general test-coverage audit of the whole scheduled-jobs branch — scoped specifically to the gaps enumerated above.
