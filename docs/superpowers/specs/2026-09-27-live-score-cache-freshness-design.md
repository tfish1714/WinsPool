# Live Score Cache Freshness Architecture

**Date:** 2026-09-27
**Status:** Proposed
**Target Areas:** `services/data_service.py`, `services/cache_service.py`, new `services/live_window_service.py`, `scripts/sync_live_scores.py`, `static/js/live_refresh.js`
**Precedes:** Builds on the already-implemented `docs/superpowers/specs/completed/2026-09-20-smart-live-scores-and-realtime-standings-design.md` (fast-exit job window, `/api/live-standings`, client polling). This spec does not revisit any of that — it targets a *separate* problem discovered downstream of it.

---

## 1. Context & Motivation

### The observed symptom

A user watching `/schedule/{year}` during a live game window reported: no score/clock update for several minutes while resident on the page, then a jump to fresh data immediately after navigating away and back (a full page reload).

### Root cause

`/api/live-scores` and `/api/live-standings` (`routes/api_routes.py`) both read games through `services/data_service.py::load_data()`, which serves the **`DOMAIN_ACTIVE`** in-memory cache bucket — the same bucket every other endpoint uses (schedule page render, predictions, portfolio, standings page, etc.).

That bucket's freshness is governed by `check_remote_signals()`:

```python
if use_local or (current_time - cs._LAST_REMOTE_CHECK) <= cs._REMOTE_CHECK_INTERVAL:
    return
```

`_REMOTE_CHECK_INTERVAL` is a flat **60 seconds**, and it is a *per-process* (per Cloud Run instance) clock. So the actual end-to-end staleness a browser can see is:

```
live-scores job write cadence (~2 min in-window)
  + up to 60s before THIS instance's own poll clock next fires
  + request latency
```

i.e. **up to ~3 minutes**, and that bound is **not synchronized across instances** — Cloud Run can run several instances behind the load balancer, each with its own independent 60-second clock starting from whenever it booted. A request that stays on the same long-lived connection/instance can sit stuck at "just missed the window" for a couple of minutes, while a fresh navigation has a good chance of landing on an instance whose clock happens to fire sooner (or a cold instance, which always fetches fresh on first load). That divergence is exactly the "frozen, then jumps on navigation" symptom — it isn't a bug in the polling JS or the `/api/live-scores` payload (both were verified correct against production), it's the shared cache's freshness bound.

This 60-second interval is the right, deliberate tradeoff for `DOMAIN_ACTIVE`'s *other* consumers (full schedule render, predictions, portfolio) — none of them need sub-minute freshness, and checking the tiny `metadata/cache_control` doc even every 60s already exists purely as a cost-saving compromise over checking on every request. The bug is that the two **live-polling endpoints inherit that same 60s bound**, even though the whole point of client-side polling every 30s is to be near-real-time.

### Confirmed, not guessed

Before writing this spec: `/api/live-scores` and `/api/live-standings` were curled directly against production during a live window and both returned correct, fresh `is_live`/`clock`/`period` values on that single request — so the payload logic itself is right. The staleness is specifically about *which* request happens to land after a stale-cache instance's 60s clock has or hasn't ticked yet.

### A smaller, related gap

`static/js/standings_refresh.js` (added by the prior spec) listens for `visibilitychange` and polls immediately when the tab becomes visible again. `static/js/live_refresh.js` (the older, schedule-page poller) does not — it only has the 30s `setInterval` gated by `if (document.hidden) return;`. Backgrounding the schedule-page tab and returning to it can add up to another 30s of visible staleness for no reason, now that the pattern to fix it already exists in the codebase.

---

## 2. Architecture & Technical Design

### Part 1: Extract a pure, shared "live window" check

`scripts/sync_live_scores.py::is_live_score_window_active()` already computes "is `now_et` inside an NFL game window" from a games DataFrame's `gameday`/`gametime`/`result` columns — but it's defined inside a script that also imports `daily_nfl_sync` (Firestore init, `batch_upload`, etc.), so the web app can't cheaply import just the window logic today.

Move the **pure** part (no I/O) into a new `services/live_window_service.py`:

```python
def is_within_live_window(games: pd.DataFrame, now_et: datetime | None = None) -> bool:
    """Same algorithm as the live-scores job's own window check (kickoff -20min
    through final+30min / non-final+4.5h), but takes an already-loaded games
    DataFrame instead of reading rawdata/schedules or hitting the network.
    Fails open (True) on any error or empty input -- see module docstring."""
```

`scripts/sync_live_scores.py::is_live_score_window_active()` keeps its existing signature and fast-exit behavior, but becomes a thin wrapper: load the schedule (local file or nflverse GET, unchanged) then delegate to `is_within_live_window()`. `tests/test_live_score_window.py`'s existing cases move to (or are duplicated against) the new pure function; the script-level test keeps covering the I/O fallback.

### Part 2: Dynamic remote-check interval for `DOMAIN_ACTIVE`

In `services/data_service.py::check_remote_signals()`, replace the flat interval with one that tightens only while the currently-cached active bucket's own games say a live window is underway:

```python
def _active_check_interval() -> float:
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_ACTIVE)
    games = cached["games"] if cached else None
    if games is None or games.empty:
        return cs._REMOTE_CHECK_INTERVAL  # cold cache: no window info yet, use the normal interval
    from services.live_window_service import is_within_live_window
    return (cs._LIVE_REMOTE_CHECK_INTERVAL
            if is_within_live_window(games)
            else cs._REMOTE_CHECK_INTERVAL)
```

`check_remote_signals()` uses `_active_check_interval()` in place of the flat `cs._REMOTE_CHECK_INTERVAL` for its elapsed-time gate. Add `_LIVE_REMOTE_CHECK_INTERVAL = 10` next to the existing `_REMOTE_CHECK_INTERVAL = 60` in `cache_service.py`.

**Why this is cheap:** tightening the check interval only changes how often this process re-reads the single tiny `metadata/cache_control` document (one doc get) — it does **not** change how often the full `DOMAIN_ACTIVE` bucket gets refetched, which is still driven purely by whether the remote signal timestamp actually advanced (i.e. by the live-scores job's own ~2-minute write cadence). Outside any live window, the interval is untouched (still 60s), so there is zero added Firestore load on a normal off-hours day. Inside a window, going from 60s to 10s adds at most ~5 extra tiny doc reads per minute per warm instance — during the ~20-24 hours per week NFL games are actually being played, which is the same "only pay for it when it matters" principle the completed smart-live-scores spec already established for the job side.

**Why this fixes the reported symptom:** it caps the worst-case per-instance staleness at `job cadence (~2 min) + 10s` instead of `+60s`, and — more importantly — it shrinks the *divergence window between instances* from up to 60s to up to 10s, so a request landing on "the wrong" instance no longer looks frozen for minutes.

### Part 3: Client-side visibility catch-up parity

Add the same catch-up listener `standings_refresh.js` already has to `static/js/live_refresh.js`:

```javascript
document.addEventListener('visibilitychange', function () {
    if (!document.hidden) poll(cfg.year);
});
```

Small, but closes a real gap now that the pattern exists in the codebase and should be the same on both live-polling pages.

---

## 3. Non-Goals & Boundaries

1. **No WebSockets / Firestore realtime listeners.** Same rationale as the prior spec's decision (Cloud Run scale-to-zero, mobile background termination, no other stateful-connection precedent in the codebase). This spec only tightens a polling interval, it does not change the polling model.
2. **No change to the live-scores job's own write cadence.** `*/2 * * 9-12,1 *` and its fast-exit window check are untouched.
3. **`DOMAIN_ACTIVE`'s TTL/signal design for non-live consumers is untouched.** Predictions, portfolio, the full schedule render, etc. keep the same 60s-checked, signal-driven freshness they have today — this only shortens the check cadence, and only while a live window is actually open.
4. **No dedicated "live" cache domain.** Considered and rejected: a separate `DOMAIN_LIVE` bucket queried directly from Firestore on every request would also fix the staleness, but duplicates the games-caching logic and adds a second signal to keep in sync for no real benefit over just tightening the existing check — `DOMAIN_ACTIVE` already holds exactly the games data both endpoints need.

---

## 4. Test and Verification Plan

1. **`tests/test_live_window_service.py`** (new, mirrors the existing window-check cases in `tests/test_live_score_window.py` but against `is_within_live_window(games_df)` directly — no schedule-file/network mocking needed):
   - Empty/None games -> `True` (fail open).
   - All games today, before earliest kickoff-20min -> `False`.
   - Inside a game's live window -> `True`.
   - All of today's games final, past the +30min tail -> `False`.
   - A non-final game past kickoff but within the +4.5h tail -> `True`.
2. **`tests/test_data_service_cache.py`** (extend or add):
   - With a cached `DOMAIN_ACTIVE` bucket whose games are mid-live-window, `check_remote_signals()` re-checks Firestore after `_LIVE_REMOTE_CHECK_INTERVAL` (10s) elapses, not the full 60s.
   - With a cached bucket whose games are outside any window, the interval stays 60s (no regression / no extra Firestore load off-hours).
   - Cold cache (`DOMAIN_ACTIVE` not yet populated) falls back to the normal 60s interval rather than erroring.
3. **`scripts/sync_live_scores.py`**: existing `tests/test_live_score_window.py` cases continue to pass unchanged against the thin wrapper.
4. **Manual verification during a live window** (or with `--force`-synced local data): open `/schedule/{year}`, confirm a score/clock update lands within ~10-15s of the live-scores job's next write, both on a freshly loaded tab and one left open for several minutes without navigating.
5. Full regression: `pytest tests/`.
