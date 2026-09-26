# Cleanup Follow-Ups: Dead Firebase Hosting Rewrite, Push Save Result, Small Tidy-Ups

**Date:** 2026-09-26
**Status:** Design, not yet planned or implemented. Low risk; none of these block anything.
**Origin:** Findings and deferred minors from the three branches merged
2026-09-25 (`worktree-standings-ux-and-magic-number`,
`worktree-auth-security-and-push`, `worktree-performance-and-mlops-logging`).
Companion to `2026-09-26-auth-hardening-followups.md`, which covers the
security-relevant follow-ups; everything here is hygiene.

## 1. Dead Firebase Hosting rewrite in `firebase.json`

### Problem

`firebase.json` configures Firebase Hosting (`public: "static"`) with rewrites
(`/api/**`, `/ws/**`, `/admin`, and further routes) that all target Cloud Run
service `winspool-api` in region `us-central1`. `gcloud run services list`
shows only `winspool` in `us-east1`, so that service does not exist and every
rewrite points at nothing. The app is reached directly at its Cloud Run URL
(`APP_BASE_URL`, DEPLOY.md). The one leftover consumer is
`scripts/generate_weekly_summary.py:76`, which puts a link to
`https://winspool.web.app` in the weekly recap email.

Why it matters:
- The `winspool.web.app` link in a recap email may be broken or may serve only
  the static folder with dead API rewrites. This is the only user-visible risk
  and must be checked first.
- If anyone ever runs `firebase deploy`, it would publish a Hosting site whose
  API calls 404.
- It creates a wrong mental model: the review of the rate limiter had to
  investigate whether an extra proxy hop existed (which would change
  `TRUSTED_PROXY_HOPS`). A dead config invites that confusion.

### Requirements

1. **Verify first, change second.** Open `https://winspool.web.app` and the
   recap link target in a browser and record what happens (loads the app,
   blank, 404). Check whether a Firebase Hosting site is actually deployed for
   the project (`firebase hosting:sites:list`, read-only) and whether
   `winspool.web.app` resolves. Record the result in the plan/PR.
2. Decide between two dispositions and record the choice:
   - **Retire:** remove the Hosting rewrites file (or the whole `hosting`
     block) and change the recap email link to the real `APP_BASE_URL`. Read the
     base URL from the same `APP_BASE_URL` env var other code already uses
     (deploy.ps1 requires it) rather than hardcoding a second URL.
   - **Repoint:** if Hosting is intentionally kept for a friendlier domain, fix
     the rewrites to `serviceId: winspool`, `region: us-east1`, and add
     `TRUSTED_PROXY_HOPS=2` to the service env (client IP then sits one hop
     further right in `X-Forwarded-For`; see DEPLOY.md's guardrails section).
     This path needs a live verification of rate limiting after deploy.
3. Recommended default: **Retire**, unless step 1 shows Hosting is live and in
   use. Note the project's zero-deletion rule applies to features and tests; the
   owner explicitly approves deleting a confirmed-dead config here.
4. Whichever is chosen, `scripts/generate_weekly_summary.py` must stop
   pointing at a URL that does not serve the app.

### Tests

- If the email link changes: a unit test on the recap/summary HTML asserting the
  link uses `APP_BASE_URL` and does not contain `web.app`.
- If `firebase.json` is edited: a small test that parses it as JSON and asserts
  no rewrite references a `serviceId` other than a service the deploy script
  deploys (`winspool`), or that no `hosting` block remains.

### Out of scope

Setting up a custom domain, or moving the app behind a load balancer.

## 2. `save_push_subscription` can report failure after a successful write

### Problem

In `services/push_service.py`, `save_push_subscription` does the Firestore
`update(...)` and then `_invalidate_players_cache()` (local static-cache clear
plus `signal_data_update("static")`) inside the same `try`. If the invalidation
raises (for example a transient error writing `metadata/cache_control`) after
the write already succeeded, the function logs "failed to save subscription"
and returns `False`. The caller then tells the user the save failed although
the subscription is stored. Introduced by the stale-cache fix in the
auth/push branch; the write itself is correct.

A second, related gap: the same `_invalidate_players_cache()` pattern in the
prune path (`_prune_subscription` via `_deliver`) can make a successful delete
be reported as `"failed"` in broadcast counts. That one only skews a summary
number.

### Requirements

1. Separate the concerns: the write result decides the return value. Wrap
   `_invalidate_players_cache()` in its own `try/except Exception` that logs at
   WARNING (with the player id) and does not change the return value.
2. In `_deliver`'s prune branch, treat an invalidation failure after a
   successful delete as still `"pruned"` (log the invalidation failure
   separately).
3. Keep `save_push_subscription`'s behavior otherwise identical, including the
   `False` return on a failed write and in local-data mode (`get_db()` is
   `None`, `AttributeError` on `.collection`, caught by the existing handler).

### Tests

- Write succeeds, `_invalidate_players_cache` raises: returns `True`, a WARNING
  is logged, the Firestore `update` was called once.
- Write raises: still returns `False` (existing behavior).
- Prune: delete succeeds, invalidation raises: `_deliver` returns `"pruned"`
  and the broadcast counts show `pruned: 1`.
- Existing `tests/test_push_service.py` cases pass unchanged.

## 3. Small tidy-ups (batch into one commit)

Each is independent, trivial, and safe. Do them together in one dispatch.

| # | Where | Change |
|---|-------|--------|
| a | `services/analysis_service.py`, `calculate_wins_pool_standings` docstring | Document the `team_records` parameter (same sentence as `get_enriched_schedule`'s docstring: a precomputed dict is reused instead of rescanning `games`; an empty dict is valid and is not recomputed). |
| b | `tests/test_admin_routes.py` (`test_predictions_games_returns_actuals_and_grading` area) | Remove the unused `import inspect` in the new route test, only if it is genuinely unused after checking the neighboring `iterrows` source-inspection test. |
| c | `tests/test_prediction_results.py`, `_reference_lookup` docstring | Correct it: it is the admin_routes version of the old algorithm plus NaN-score guards the original lacked (the original raised on NaN). |
| d | `services/model_promotion.py`, `_fmt` | Accept any real number, not only `int`/`float`, so numpy `float32`/`int64` are not shown as `n/a`: use `isinstance(value, numbers.Real) and not isinstance(value, bool)`. Add a test with `numpy.float32` and `numpy.int64`. Current training paths already wrap metrics in `float()`, so this is defensive, not a live bug. |
| e | `services/rate_limit_service.py`, `get_limiter` docstring | One line: limits are fixed at first creation; a later call with different values returns the existing limiter. (May already be done in the auth branch's fix wave; skip if present.) |
| f | `tests/test_rate_limit_service.py` | Remove the unused `import pytest` only if pytest is genuinely unused after the auth branch's later edits (that fix wave added tests, so check first). |

### Explicitly not included

- `PROMOTED` being logged when nothing was comparable (baseline present but no
  shared gated metric): accepted as-is; the `n/a` fields make it visible. A
  distinct `PROMOTED_UNCOMPARED` value or a `compared=` field is optional future
  polish and would change log format consumers.
- The per-season rescan in `get_prediction_accuracy` (negligible at current
  season counts).
- A `season` column guard in `build_result_lookup`: `load_data` guarantees the
  column, and a missing one fails loudly (500), not silently.
- `save_push_subscription` invalidating on every save refetching all five
  static collections: acceptable at this pool size and save frequency.

## Sequencing

Item 1 needs a human check first (open the URL, list hosting sites) and may
change infrastructure config, so do it last and with the owner present. Items 2
and 3 are pure code, independent, and can go in one branch with two commits.

## Review focus

- Item 1: the recap email link is the only user-visible effect; do not change
  it to a URL that also fails. Verify the replacement resolves before merging.
- Item 2: make sure the new inner `try/except` does not swallow the exception
  from the write itself; only the invalidation is guarded.
- Item 3d: `bool` is a subclass of `int` and must still render as `n/a`.
