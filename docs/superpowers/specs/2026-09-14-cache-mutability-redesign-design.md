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

## Confirmed during design (read directly from source, not assumed)

- Of the 7 collections `load_data()` fetches, only `nfl_games` and
  `nfl_standings` are ever season-filtered (`season_filter` applied). The
  other 5 (`nfl_teams`, `players`, `draft_order`, `draft_results`,
  `draft_order_rules`) are **always fetched unfiltered regardless of the
  `year` argument** — they are small, and the year-keyed/`'all'` split buys
  them nothing today. This means the mutability split below only needs to
  apply to `nfl_games`/`nfl_standings`; the other 5 collapse into one
  always-fresh, always-small bucket.
- `get_active_season()` (`data_service.py`) determines "the current season"
  from `games`/`draft_results`/`draft_order_rules` — i.e. it depends on data
  that itself needs to be loaded first. Any redesign has a bootstrapping
  step: a cold cache still needs one full unfiltered fetch to determine which
  season is active before it can be split into buckets.
- `backfill_schedule_predictions.py` and `predict_season.py` (the
  retrain-driven writers that touch historical-season prediction data)
  **already** call the same inlined `metadata/cache_control` write pattern
  `signal_data_update()` performs — so the retrain path already signals
  invalidation today. The problem is the signal carries no scope (just a
  timestamp), so it can't distinguish "only the active season changed" from
  "historical data changed," and today's code always treats every signal as
  "wipe everything."
- `scripts/sync_live_scores.py` (the `winspool-live-scores` job) writes
  `nfl_standings` for "current + prior season" per the performance report —
  the reason wasn't tracked down during this design pass; flagged as an
  open question for whoever implements (see below).

## Explicitly out of scope

- The live-draft WebSocket's per-pick uncached-read volume (projection
  dedup, chat double-RPC, push-lookup read) and the other items in the
  performance report's Recommendation #1 — these don't touch the caching
  architecture and are independent, already-scoped fixes.
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
  admin actions) and by its own remote-signal check.

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
carry three independent timestamps — `active_updated`, `historical_updated`,
`static_updated` — one field per bucket, in the same document (one read
still covers all three checks). `data_service.py`'s remote-check compares
each bucket's own field against that bucket's own cached timestamp and
clears only the buckets whose signal is newer.

Every writer signals the bucket(s) it actually touched:

| Writer | Bucket(s) to signal |
|---|---|
| `add_draft_result`, `delete_draft_pick`, `add_player`, `update_player_profile`, `add_draft_order`, `add_draft_order_rule`, `delete_season_data` | `static` |
| `daily_nfl_sync.py` (after the write-side fix below, scoped to the active season in normal runs) | `active` |
| `sync_live_scores.py` / `live_score_service.py` | `active` (and `historical` too, if the "current + prior season" write turns out to be necessary — see open question) |
| `backfill_schedule_predictions.py`, `predict_season.py`, `compute_elo.py --firestore` | `historical` if the run's season range is entirely before the active season, `active` if it includes the active season, both if it spans the boundary |

`add_draft_result()`/`delete_draft_pick()` gain the `signal_data_update()`
call they're currently missing (scoped to `static`, since draft results
aren't part of the games/standings split), closing the cross-instance gap
found during this design pass alongside the original staleness bug — both
are the same underlying "writer forgot to signal correctly" bug class, and
this design's whole point is making that class of bug structurally rarer (3
buckets with one clear owner each, instead of year-keyed-N-plus-`'all'`
where a new caller can silently reintroduce the asymmetry).

### 3. Write-side: `daily_nfl_sync.py` stops rewriting frozen history

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
  final scores at a season boundary, it needs to signal `historical` too
  when it does; if it's not actually necessary, scope it down to active-only
  like the rest of this design assumes.
- Whether `compute_elo.py --firestore`'s current full-history recompute
  (`elo_history`, min-season 2006 through current, confirmed ~21 docs/run in
  the performance report) should get the same active-only-by-default
  treatment as `daily_nfl_sync.py` — it wasn't in this design's original
  scope but shares the same "recomputes frozen history every day" shape.

## Testing approach

- Unit tests around the three-bucket split: writes to `static`-only
  collections don't touch `active`/`historical`; a signal on one bucket's
  timestamp field doesn't clear the other two; `load_data(year=X)` for a
  historical year never triggers a Firestore fetch when `historical` is
  already warm.
- A live-draft-shaped test asserting that a pick becomes visible on
  `/wins-pool/{year}` and `/draft-results` immediately (not "eventually
  within the TTL") — this is the original staleness bug's regression test,
  and the existing e2e suite's known limitation comment
  (`tests_e2e/test_live_draft.py`) can be upgraded to a real assertion once
  this lands.
- A season-rollover test: simulate a draft completing and confirm the
  `active` bucket rebuilds against the new season rather than continuing to
  serve the just-finished one.
