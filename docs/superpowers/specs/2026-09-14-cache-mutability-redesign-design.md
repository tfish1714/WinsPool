# Cache Mutability Redesign: Firestore Read/Write Reduction

**Status:** Draft — ready for `superpowers:writing-plans`
**Date:** 2026-09-14

## Origin

Combines two previously-separate findings into one design, since both live in
the same `services/cache_service.py` / `services/data_service.py` machinery
and a proper fix to one requires understanding the other:

1. **Staleness bug** (`docs/superpowers/specs/2026-09-12-cache-invalidation-gap-followup.md`):
   `add_draft_result()`/`delete_draft_pick()` call `clear_data_cache(season)`,
   which evicts only the year-keyed cache entry, never the separate master
   `'all'`-keyed entry that every bare `load_data()` caller
   (`/wins-pool/{year}`, `/draft-results`, `/draft/{year}`) actually reads —
   so a page can serve pre-pick data for up to the 1-hour TTL during a live
   draft.
2. **Read-amplification finding** (`docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md`,
   Firestore Usage section): the *remote* `metadata/cache_control` signal path
   calls `clear_data_cache()` with no year argument, wiping every cached year
   plus `'all'`, so the next unscoped `load_data(year=None)` call re-fetches
   `nfl_games`/`nfl_standings` **unfiltered back to `POOL_START_YEAR=2013`** —
   the leading (unmeasured) candidate for the app's ≈382,300 reads/day.

Both bugs are two symptoms of the same root design flaw: the cache is keyed
by **year identity** (one entry per year, plus a redundant `'all'` entry that
duplicates every year's data), when what actually matters for correctness and
cost is **mutability** — exactly one season changes routinely (the active
one); everything else is effectively frozen. A third, previously-undocumented
instance of the same flaw was found while researching this spec: `add_draft_result()`/`delete_draft_pick()`
never call `signal_data_update()` at all (every other writer does), so the
cross-instance remote-invalidation path doesn't fire for a draft pick either
— only relevant if `maxScale` ever exceeds its current value of 1, but a real
gap in the same mechanism.

**Widened during design** to cover every Firestore-backed read this app makes
that fits the same mutability shape (active season changes routinely,
everything before it is frozen), not just `nfl_games`/`nfl_standings` — see
"Confirmed during design" and the Design section below. The principle applied
throughout: a collection gets the active/historical treatment if it's
season-scoped and read repeatedly; it's explicitly excluded (with reasoning,
not silently dropped) if it's real-time coordination state, write-once, or
low-enough-frequency that the complexity isn't worth it.

## Confirmed during design (read directly from source, not assumed)

- Of the 7 collections `load_data()` fetches, only `nfl_games` and
  `nfl_standings` are ever season-filtered (`season_filter` applied). The
  other 5 (`nfl_teams`, `players`, `draft_order`, `draft_results`,
  `draft_order_rules`) are **always fetched unfiltered regardless of the
  `year` argument** — they are small, and the year-keyed/`'all'` split buys
  them nothing today. This means the mutability split only needs to apply to
  `nfl_games`/`nfl_standings`; the other 5 collapse into one always-fresh,
  always-small bucket (below, `static`).
- `get_active_season()` (`data_service.py`) determines "the current season"
  from `games`/`draft_results`/`draft_order_rules` — i.e. it depends on data
  that itself needs to be loaded first. Any redesign has a bootstrapping
  step: a cold cache still needs one full unfiltered fetch to determine which
  season is active before it can be split into buckets.
- **Predictions run in a separate process entirely.** `winspool-predict-daily`
  (`cache_builder.py`, plus manually-run `backfill_schedule_predictions.py`/
  `predict_season.py`) is its own Cloud Run Job, its own container — the only
  image carrying the ML dependency stack, per `Dockerfile.predict`. There is
  no shared memory between it and the `winspool` web service; the *only*
  channel between them is Firestore plus the same `metadata/cache_control`
  polling this design already relies on for the games/standings split. Both
  `backfill_schedule_predictions.py` and `predict_season.py` **already**
  write to that doc today (the same inlined pattern `signal_data_update()`
  performs) — the retrain path already signals invalidation, it just can't
  say *what* changed.
- **Predictions aren't cached in-memory at all today** — `get_preseason_predictions()`/
  `get_consensus_projections()` hit Firestore directly on every call, bypassing
  `load_data()` entirely. That's *why* cross-process staleness isn't visible
  today (nothing is cached, so nothing can go stale) — but it's also the
  read-volume waste the performance report flagged. Caching predictions
  without wiring them into the same scoped-signal mechanism this design
  establishes for games/standings would silently reintroduce the exact
  cross-process staleness bug this design exists to fix, just for the
  model's output instead of its input. Folding predictions into this design
  now avoids that trap.
- Two more currently-uncached reads bypass a cache that already has their
  data: `draft_service.py:67`'s raw `get_collection_df('draft_order')` call
  (used only to resolve the current season) and `mock_draft_service.py:36`'s
  raw `get_collection_df("draft_order_rules")` call each separately re-fetch
  data that will already be sitting in the `static` bucket once it exists.
  Same for `push_service.py:40`'s per-player `.get()` on every push send —
  the player's row is already in `static`'s in-memory `players` frame.
- `game_predictions` (used by `/schedule/{year}` — a real, non-admin,
  every-request hot path — and streamed in full by the admin accuracy
  endpoint) is per-season-doc, uncached, and fits the exact same
  active/historical shape as predictions.
- `elo_history`/`nn_weekly_accuracy` stream their **entire** collection
  (all seasons) on every admin page load, uncached. Real waste, but
  admin-only and low-frequency — see Design §4 for why these get a lighter
  treatment than predictions/game_predictions rather than the full split.
- `scripts/sync_live_scores.py` (the `winspool-live-scores` job) writes
  `nfl_standings` for "current + prior season" per the performance report —
  the reason wasn't tracked down during this design pass; flagged as an
  open question for whoever implements (see below).

## Explicitly out of scope (with reasoning)

- **`metadata/draft_timer_{season}`** — real-time per-pick coordination
  state, read-then-conditional-write inside the same request. Caching it
  separately from that write risks a race between the cache and the
  authoritative Firestore state. Left uncached, correctly.
- **`draft_chat`** — messages are write-once and read once per WS connect
  already; not a repeated-read pattern caching would help.
- **`prediction_features`, `weekly_recaps`** — admin-only, low-frequency per
  the performance report. Same shape as `elo_history` if someone wants them
  later (§4's pattern extends trivially), not worth it now.
- **`config/settings`** (the `draft_active` admin toggle) — small, single
  document, read at least twice per pick per the performance report. Cheap
  to fold into `static` opportunistically since it costs nothing extra, but
  not a priority: drafting is now a once-a-year event for the active season,
  not ongoing cost.
- **`analytics_cache`** — not read by the running app at all today (dead
  code per the performance report); nothing to cache.
- The live-draft/mock-draft code's literal duplicate-function-call structure
  (`get_season_projection_blended()` and `get_season_projection_dual()` each
  independently calling `get_preseason_predictions()`/`get_consensus_projections()`,
  and the chat `add()`-then-`get()` double-RPC) — once predictions are
  cached per this design, both calls hit the in-memory cache instead of
  Firestore, so the *read-cost* half of this is fixed as a side effect.
  What's left (calling the same in-memory-cached function twice instead of
  once, and the chat RPC shape) is a pure code-cleanliness item with no
  Firestore-cost implication — optional, not designed here.
- Client-side/browser caching. Considered during brainstorming and rejected:
  the measured bottleneck is server-side cache-miss cost, not request volume
  from users (peak concurrency was ~11.5), and pushing caching to the
  browser would mean managing staleness across every player's device instead
  of once, centrally, for a ~10-person pool where that trade only makes
  things worse. The app's one existing client-side cache (two `localStorage`
  booleans for instant nav render) is unrelated and untouched.
- Fixing `winspool-live-scores`' "current + prior season" write — flagged as
  something to verify (is it still needed once this design lands?) rather
  than designed around here.

## Design

### 1. Cache structure: three buckets, not year-keyed-N-plus-`'all'`

Replace `_DATA_CACHE`'s current shape (one entry per distinct year ever
requested, plus a separate `'all'` entry duplicating all of them) with
exactly three named entries:

- **`active`** — `nfl_games`/`nfl_standings` filtered to the current active
  season only (one season's data — small). Refreshed on every relevant write
  and by the existing 60-second remote-signal check, scoped to this bucket
  only.
- **`historical`** — `nfl_games`/`nfl_standings` for every season strictly
  before the active one. Fetched once, then effectively permanent: refreshed
  only when an explicit "historical data changed" signal fires (a retrain or
  backfill run that touches a past season), not on any routine cadence.
- **`static`** — the other 5 collections (`nfl_teams`, `players`,
  `draft_order`, `draft_results`, `draft_order_rules`), always fetched
  unfiltered as today, but now cached as **one** entry instead of being
  re-fetched inside every year-keyed slot. Refreshed on any write that
  touches one of these collections (draft picks, player edits, season
  admin actions) and by its own remote-signal check. `draft_service.py:67`'s
  and `mock_draft_service.py:36`'s raw `draft_order`/`draft_order_rules`
  fetches, and `push_service.py:40`'s per-player lookup, all get rerouted to
  read from this bucket's already-loaded frames instead of issuing their own
  Firestore calls.

`load_data(year=None)` (today's "all history, unfiltered" shape, used by
all-time standings, head-to-head, admin views, and `get_active_season()`'s
own computation) becomes a derived view: `concat(historical, active)` for
games/standings, `static` for everything else — computed in memory, no
separate Firestore fetch. `load_data(year=X)` becomes: if `X` is the active
season, return `active` directly; otherwise filter `historical` to that
season in memory (the same pattern `load_data_season()` already uses today,
now used everywhere instead of duplicated per-year Firestore queries).
`load_data_season()` becomes redundant once `load_data(year=X)` does this —
worth deduplicating into one function as part of this work, not two that do
almost the same thing.

**Cold-start bootstrapping:** on a fully cold cache (process start, or after
a historical-bucket invalidation), there's no way to know which season is
"active" without first loading enough data to compute it. The first load
after a cold start does one unfiltered fetch (same cost as today's `'all'`
fetch), computes `get_active_season()` from it, then splits the result into
`active`/`historical` for caching going forward. This is a one-time cost per
cold start, not a per-request cost — the whole point of the redesign is that
*subsequent* invalidations don't force this again.

**Season rollover:** once a year, the active season changes (a completed
draft advances `get_active_season()`'s result). When a `static`-bucket
refresh causes the computed active season to change, the `active` bucket
must be invalidated and rebuilt against the new season filter regardless of
its own TTL/signal state — otherwise it keeps serving last season's "active"
data under the new season's identity.

### 2. Invalidation signal: scoped, not a single timestamp

Extend `metadata/cache_control` (currently one `last_update` timestamp) to
carry independent timestamps, one per cache domain established in §1/§3/§4
below, in the same document (one read still covers every check).
`data_service.py`'s remote-check compares each domain's own field against
that domain's own cached timestamp and clears only the domains whose signal
is newer.

Every writer signals the domain(s) it actually touched:

| Writer | Signal field(s) |
|---|---|
| `add_draft_result`, `delete_draft_pick`, `add_player`, `update_player_profile`, `add_draft_order`, `add_draft_order_rule`, `delete_season_data` | `static_updated` |
| `daily_nfl_sync.py` (after the write-side fix in §5, scoped to the active season in normal runs) | `active_updated` |
| `sync_live_scores.py` / `live_score_service.py` | `active_updated` (and `historical_updated` too, if the "current + prior season" write turns out to be necessary — see open question) |
| `cache_builder.py` (`winspool-predict-daily`'s daily run) | `predictions_active_updated` |
| `backfill_schedule_predictions.py`, `predict_season.py` | `predictions_historical_updated` if the run's season range is entirely before the active season, `predictions_active_updated` if it includes the active season, both if it spans the boundary |
| `compute_elo.py --firestore` | `active_updated` if scoped to the active season only (see open question below), `admin_analytics_updated` otherwise |
| `weekly_model_eval.py --firestore` | `admin_analytics_updated` |

`add_draft_result()`/`delete_draft_pick()` gain the `signal_data_update()`
call they're currently missing (scoped to `static_updated`, since draft
results aren't part of the games/standings split), closing the
cross-instance gap found during this design pass alongside the original
staleness bug — both are the same underlying "writer forgot to signal
correctly" bug class, and this design's whole point is making that class of
bug structurally rarer (a handful of named domains with one clear owner
each, instead of year-keyed-N-plus-`'all'` where a new caller can silently
reintroduce the asymmetry).

### 3. Model predictions: same active/historical split, separate signal domain

`preseason_predictions`, `consensus_projections`, and `game_predictions`
(all per-season-scoped, all currently read uncached on every call) get the
same treatment as games/standings, but as their **own** cache
domain/signal pair (`predictions_active_updated` / `predictions_historical_updated`)
rather than sharing `active_updated`/`historical_updated` — predictions are
written by a completely different process on a completely different
schedule (`winspool-predict-daily` at 9:15 UTC vs. `winspool-sync-daily`'s
9:00 UTC), so a predictions-only update shouldn't force a games/standings
refetch, and vice versa.

- **`predictions_active`** — current season's `preseason_predictions`,
  `consensus_projections`, `game_predictions`. Refreshed by
  `cache_builder.py`'s daily run (signals `predictions_active_updated`) and
  by the remote-signal check, same 60-second cadence as everything else.
- **`predictions_historical`** — every other season's predictions. Fetched
  once, refreshed only when `backfill_schedule_predictions.py`/
  `predict_season.py` explicitly signal `predictions_historical_updated`
  (a retrain/backfill run touching past seasons — rare, matching the
  premise that historical data doesn't change absent a retrain).

`get_preseason_predictions()`, `get_consensus_projections()`, and
`get_game_predictions()` (`cache_service.py`) stop hitting `get_collection_df()`/
Firestore directly and instead read from whichever of these two in-memory
domains covers the requested season, exactly mirroring how `load_data(year=X)`
resolves against `active`/`historical` in §1.

### 4. Admin analytics: one coarse signal, not a full split

`elo_history` and `nn_weekly_accuracy` are read only by admin-only explorer
pages, and both currently stream their *entire* collection (every season) on
every load — real waste, but low-frequency enough that a full active/
historical split isn't worth the added complexity. Cache each collection's
full stream as one in-memory entry, refreshed by one shared
`admin_analytics_updated` signal field, written by `compute_elo.py --firestore`
and `weekly_model_eval.py --firestore` whenever either runs. An occasional
unnecessary full refetch here (e.g. an elo write triggering a
weekly-accuracy-cache clear too) is an acceptable trade for not building a
second full mutability split for admin-only traffic.

### 5. Write-side: `daily_nfl_sync.py` stops rewriting frozen history

Today `batch_upload()` overwrites every game/team-season since 2013,
unconditionally, every day. Two changes, both aimed at "don't write data
that's already there":

- **Scope to the active season by default.** Historical seasons don't
  change in normal operation (per the design's own premise above) — the
  daily sync has no reason to touch them at all unless explicitly asked to
  (keep a `--seasons`-style override for the rare manual backfill/correction
  case, consistent with how other scripts in this repo already expose a
  season-range flag).
- **Diff before writing.** Within the active season's write, compare each
  incoming row's values against what's currently stored and skip the
  `.set()` call for documents where nothing actually changed, instead of
  blindly overwriting every row every run. Reduces Firestore *writes*, not
  just reads — directly serves the "minimize reads and writes" goal, and is
  a bounded, mechanical change (read current active-season docs once, diff
  in memory, write only the deltas).

### Open questions to resolve during implementation

- Why does `sync_live_scores.py` write `nfl_standings` for "current + prior
  season," not just the active season? If it's correcting late-arriving
  final scores at a season boundary, it needs to signal `historical_updated`
  too when it does; if it's not actually necessary, scope it down to
  active-only like the rest of this design assumes.
- Whether `compute_elo.py --firestore`'s current full-history recompute
  (`elo_history`, min-season 2006 through current, confirmed ~21 docs/run in
  the performance report) should get the same active-only-by-default
  treatment as `daily_nfl_sync.py`. If scoped down to active-only, it should
  signal `active_updated` (elo becomes part of the same mutability shape as
  games/standings); if it stays a full recompute, it signals
  `admin_analytics_updated` per §4.

## Testing approach

- Unit tests around the bucket/domain split: writes to `static`-only
  collections don't touch `active`/`historical`/predictions domains; a
  signal on one domain's timestamp field doesn't clear any other domain;
  `load_data(year=X)` for a historical year, and `get_preseason_predictions()`
  for a historical season, never trigger a Firestore fetch once their
  respective bucket is warm.
- A live-draft-shaped test asserting that a pick becomes visible on
  `/wins-pool/{year}` and `/draft-results` immediately (not "eventually
  within the TTL") — this is the original staleness bug's regression test,
  and the existing e2e suite's known limitation comment
  (`tests_e2e/test_live_draft.py`) can be upgraded to a real assertion once
  this lands.
- A season-rollover test: simulate a draft completing and confirm the
  `active` bucket (and `predictions_active`) rebuild against the new season
  rather than continuing to serve the just-finished one.
- A cross-process test: write to `preseason_predictions` the way
  `predict_season.py` does (a raw Firestore write plus the
  `predictions_active_updated`/`predictions_historical_updated` signal, not
  an in-process cache call), then confirm a separate `load_data`/prediction
  read picks up the change within one remote-check interval — this is the
  regression test for the cross-process staleness trap this design is
  built to avoid.
