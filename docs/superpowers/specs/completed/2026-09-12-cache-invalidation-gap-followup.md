# Follow-Up: Draft-Pick Cache Invalidation Misses the Master `'all'` Entry

**Date:** 2026-09-12
**Status:** Not designed — backlog, captured during the UI-tests (Playwright e2e) plan so it isn't lost. Needs its own brainstorming pass when picked up; no fix is proposed or attempted here.

## Origin

Discovered while writing `tests_e2e/test_live_draft.py` (Task 10 of
`docs/superpowers/sdd/2026-09-09-ui-tests-playwright`), which drives a full
10-player live draft through a real browser and checks `/wins-pool/{year}`
between picks. The mid-draft page did not reflect picks that had demonstrably
been written, which traced back to a cache-invalidation gap rather than
anything wrong with the test. The test was left asserting only what it can
legitimately assert (route renders without a 500, in one of its two valid
shapes) with the reason recorded inline; this spec is the durable record of
the underlying bug.

## What the bug is

Two write paths in `services/db_service.py` invalidate the wrong cache key:

- `add_draft_result()` — calls `clear_data_cache(season)`
- `delete_draft_pick()` — calls `clear_data_cache(season)`

`clear_data_cache(year)` (`services/cache_service.py`) evicts only the
**year-keyed** entry (`_DATA_CACHE[str(year)]`). The in-memory cache also holds
a separate **master `'all'`-keyed** entry (the unfiltered load), with a 1-hour
TTL (`_CACHE_TTL_SECONDS = 3600`). Nothing in either write path evicts `'all'`.

Every caller of bare `load_data()` (no year argument) reads that `'all'` entry.
So a pick can be fully committed to Firestore and the local pkl mirror while
the `'all'` cache still holds the pre-pick snapshot.

## What it affects

Anything reading through bare `load_data()`, which includes at least:

- `/wins-pool/{year}` (`routes/standings_routes.py`)
- `/draft-results` (`routes/draft_routes.py`)
- `/draft/{year}` (`routes/draft_routes.py`)

A full audit of bare `load_data()` callers is part of the work when this is
picked up — the three above are the ones observed, not necessarily the complete
set.

## Why it matters

The exposure is worst in exactly the situation the app exists for. On a
long-running warm process (i.e. production Cloud Run, where an instance stays
warm through a draft), these pages can serve stale data for **up to an hour**
during a live draft — including the degenerate "0 picks made" state if the
`'all'` entry was populated before the draft started. Local dev rarely shows it
because processes restart constantly.

Note the year-keyed eviction that *does* happen makes this inconsistent rather
than uniformly stale: some surfaces update immediately while others lag, which
is more confusing to a user mid-draft than a uniform delay would be.

## Open questions to resolve when scoped

- Is the right fix to evict `'all'` alongside the year key, to drop the
  year-keyed/`'all'` split entirely, or to have `load_data()` derive year slices
  from a single cached load? Each has different Firestore read-cost
  implications, which is presumably why the split exists.
- Are there other writers with the same asymmetry? (`clear_data_cache()` with
  no argument wipes everything, so only the `clear_data_cache(season)` call
  sites are suspect — there are exactly two today, both listed above.)
- Does the 60-second `_REMOTE_CHECK_INTERVAL` remote-invalidation path already
  partially cover this in production, and if so why didn't it? Worth
  understanding before changing eviction, since it may make a narrower fix
  sufficient.
- Multi-instance behavior: Cloud Run can run several instances, each with its
  own in-memory cache. Any fix that only evicts in-process still leaves other
  instances stale — is cross-instance invalidation in scope, or explicitly not?

## Non-goals

- Not designing the fix here. This is a parking spot, not a plan — the
  eviction-strategy question above is a real behavior/cost tradeoff that needs
  a deliberate decision, not a one-line patch.
- Not blocking the UI-tests plan. The e2e suite is green with the limitation
  documented at both the test site (`tests_e2e/test_live_draft.py`) and the bug
  sites (`services/db_service.py`'s two `clear_data_cache(season)` calls).
