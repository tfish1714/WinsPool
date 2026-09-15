# Cache Mutability Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace WinsPool's year-keyed-plus-`'all'` in-memory Firestore cache with a mutability-based design (active season / historical seasons / static collections / predictions / admin analytics), fix the two invalidation bugs this replaces, and cut Firestore read/write volume at its two biggest identified sources (`sync_live_scores.py`'s unconditional full rewrite, and `analytics_cache`'s dead write path).

**Architecture:** A small number of named cache **domains** (`active`, `historical`, `static`, `predictions_active`, `predictions_historical`, `admin_analytics`) replace the current per-year dictionary. Each domain has its own in-memory slot and its own field in a single `metadata/cache_control` Firestore document, so a write anywhere in the app signals only the domain(s) it actually touched, and every process (including the separate `winspool-predict-daily` job) discovers that signal via the existing 60-second remote-check poll.

**Tech Stack:** Python, `pandas`, Firestore (`google-cloud-firestore` via `firebase_admin`), `pytest` with the existing `mock_firestore` fixture (`tests/conftest.py`, patches `services.db_service.get_db`).

**Spec:** `docs/superpowers/specs/2026-09-14-cache-mutability-redesign-design.md`

## Global Constraints

- Every writer that changes data flowing through a cache domain must signal that domain via `metadata/cache_control` using `merge=True` — a bare `.set()` without merge would wipe every other domain's field in the same document.
- The `DataBundle` 7-tuple `load_data()`/`load_data_season()` return (`standings, teams, games, players, draft_order, draft_results, draft_order_rules`) must not change shape — every route/service consuming it stays untouched.
- No fixes here touch live GCP infra config (this plan is application code only, unlike the read-only performance investigation it follows from).
- Domain names and their Firestore signal field names are exact string constants, defined once in `services/cache_service.py`, and imported everywhere else that needs them — never re-typed as a literal string in a second file.

---

## Task 1: Domain-keyed cache store (replaces year-keyed `_DATA_CACHE`)

**Files:**
- Modify: `services/cache_service.py:112-142` (the `_DATA_CACHE`/`_CACHE_TIMESTAMPS`/`clear_data_cache`/`_cache_key` block)
- Modify: `tests/test_cache_service.py:1-4,32-37` (imports and `test_clear_data_cache_wipes_memory`)
- Modify: `tests/test_data_service.py` (every `cs._DATA_CACHE.clear()` / `cs._CACHE_TIMESTAMPS.clear()` call — confirmed 3 files reference these internals: `test_cache_service.py`, `test_data_service.py`, `test_db.py`)
- Modify: `tests/test_db.py` (same internals references)

**Interfaces:**
- Produces: `DOMAIN_ACTIVE = "active"`, `DOMAIN_HISTORICAL = "historical"`, `DOMAIN_STATIC = "static"`, `DOMAIN_PREDICTIONS_ACTIVE = "predictions_active"`, `DOMAIN_PREDICTIONS_HISTORICAL = "predictions_historical"`, `DOMAIN_ADMIN_ANALYTICS = "admin_analytics"` (module-level string constants in `cache_service.py`). `DOMAIN_SIGNAL_FIELDS: dict[str, str]` mapping each domain constant to its Firestore field name (e.g. `DOMAIN_ACTIVE -> "active_updated"`). `get_domain(domain: str) -> Any | None`, `set_domain(domain: str, value: Any, timestamp: float = None) -> None`, `clear_domain(domain: str) -> None`, `get_domain_timestamp(domain: str) -> float`, and a rewritten `clear_data_cache(domain: str = None) -> None` (domain-scoped when given a domain name; wipes every domain when called with no argument, same as today's bare `clear_data_cache()`).
- Consumes: nothing from other tasks (this is the foundational task).

This task keeps `_CACHE_TTL_SECONDS` (`cache_service.py:118`) and `_LAST_REMOTE_CHECK`/`_REMOTE_CHECK_INTERVAL` (`cache_service.py:119-120`) untouched — Task 2 changes how the remote-check uses them, not their definitions.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_service.py`, replacing the existing `test_clear_data_cache_wipes_memory` (which references the old `_DATA_CACHE` global directly):

```python
def test_set_and_get_domain_round_trips():
    from services.cache_service import set_domain, get_domain, DOMAIN_ACTIVE, clear_domain
    clear_domain(DOMAIN_ACTIVE)  # isolate from other tests
    assert get_domain(DOMAIN_ACTIVE) is None
    set_domain(DOMAIN_ACTIVE, {"games": "placeholder"})
    assert get_domain(DOMAIN_ACTIVE) == {"games": "placeholder"}

def test_clear_domain_removes_only_that_domain():
    from services.cache_service import set_domain, clear_domain, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "active-value")
    set_domain(DOMAIN_STATIC, "static-value")
    clear_domain(DOMAIN_ACTIVE)
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) == "static-value"
    clear_domain(DOMAIN_STATIC)  # cleanup

def test_clear_data_cache_with_domain_clears_only_that_domain():
    from services.cache_service import set_domain, clear_data_cache, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "a")
    set_domain(DOMAIN_STATIC, "s")
    clear_data_cache(DOMAIN_ACTIVE)
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) == "s"
    clear_data_cache(DOMAIN_STATIC)  # cleanup

def test_clear_data_cache_with_no_domain_wipes_everything():
    from services.cache_service import set_domain, clear_data_cache, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "a")
    set_domain(DOMAIN_STATIC, "s")
    clear_data_cache()
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) is None

def test_get_domain_timestamp_defaults_to_zero_when_unset():
    from services.cache_service import get_domain_timestamp, clear_domain, DOMAIN_HISTORICAL
    clear_domain(DOMAIN_HISTORICAL)
    assert get_domain_timestamp(DOMAIN_HISTORICAL) == 0

def test_domain_signal_fields_cover_every_domain_constant():
    """Every domain constant must have exactly one signal field -- catches a
    typo'd or missing entry before it ships."""
    from services.cache_service import (
        DOMAIN_SIGNAL_FIELDS, DOMAIN_ACTIVE, DOMAIN_HISTORICAL, DOMAIN_STATIC,
        DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL, DOMAIN_ADMIN_ANALYTICS,
    )
    expected = {DOMAIN_ACTIVE, DOMAIN_HISTORICAL, DOMAIN_STATIC,
                DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL, DOMAIN_ADMIN_ANALYTICS}
    assert set(DOMAIN_SIGNAL_FIELDS.keys()) == expected
    assert len(set(DOMAIN_SIGNAL_FIELDS.values())) == len(expected)  # no duplicate field names
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_service.py -k "domain or signal_fields" -v`
Expected: FAIL — `ImportError: cannot import name 'set_domain'` (none of these names exist yet).

- [ ] **Step 3: Replace the year-keyed cache block**

In `services/cache_service.py`, replace lines 112-142 (from `# --- Data Cache (Moved from data_service to break circular import) ---` through the end of `_cache_key`) with:

```python
# --- Data Cache: domain-keyed, not year-keyed (see docs/superpowers/specs/
# 2026-09-14-cache-mutability-redesign-design.md) ---
import time

DOMAIN_ACTIVE = "active"
DOMAIN_HISTORICAL = "historical"
DOMAIN_STATIC = "static"
DOMAIN_PREDICTIONS_ACTIVE = "predictions_active"
DOMAIN_PREDICTIONS_HISTORICAL = "predictions_historical"
DOMAIN_ADMIN_ANALYTICS = "admin_analytics"

# Maps each cache domain to the field name it owns inside the single
# metadata/cache_control Firestore document. A writer signals exactly the
# domain(s) it touched; every process (including winspool-predict-daily,
# which never shares memory with the web service) discovers the signal via
# the existing remote-check poll below.
DOMAIN_SIGNAL_FIELDS = {
    DOMAIN_ACTIVE: "active_updated",
    DOMAIN_HISTORICAL: "historical_updated",
    DOMAIN_STATIC: "static_updated",
    DOMAIN_PREDICTIONS_ACTIVE: "predictions_active_updated",
    DOMAIN_PREDICTIONS_HISTORICAL: "predictions_historical_updated",
    DOMAIN_ADMIN_ANALYTICS: "admin_analytics_updated",
}

_DOMAIN_CACHE: dict = {}
_DOMAIN_TIMESTAMPS: dict = {}
_CACHE_TTL_SECONDS = 3600  # 1-hour TTL; long enough to avoid Firestore spam on every request, short enough to catch same-day data changes
_LAST_REMOTE_CHECK = 0
_REMOTE_CHECK_INTERVAL = 60  # Check Firestore for invalidation every 60 seconds


def get_domain(domain: str):
    """Return the cached value for `domain`, or None if not cached."""
    return _DOMAIN_CACHE.get(domain)


def set_domain(domain: str, value, timestamp: float = None) -> None:
    """Cache `value` under `domain`, stamped with the given (or current) time."""
    _DOMAIN_CACHE[domain] = value
    _DOMAIN_TIMESTAMPS[domain] = timestamp if timestamp is not None else time.time()


def clear_domain(domain: str) -> None:
    """Evict one domain's cached value and timestamp."""
    _DOMAIN_CACHE.pop(domain, None)
    _DOMAIN_TIMESTAMPS.pop(domain, None)


def get_domain_timestamp(domain: str) -> float:
    """Return when `domain` was last cached, or 0 if never cached."""
    return _DOMAIN_TIMESTAMPS.get(domain, 0)


def clear_data_cache(domain: str = None) -> None:
    """Invalidate one named cache domain, or every domain if none given.

    `domain` must be one of the DOMAIN_* constants above (a bare Firestore
    collection name or a season year is no longer a valid argument -- see
    the design doc's "year-keyed-plus-'all'" replacement).
    """
    if domain is not None:
        clear_domain(domain)
        logger.info("Cache domain '%s' cleared.", domain)
    else:
        _DOMAIN_CACHE.clear()
        _DOMAIN_TIMESTAMPS.clear()
        logger.info("All cache domains cleared.")
```

- [ ] **Step 4: Update the existing tests that poke the old internals**

In `tests/test_cache_service.py`, change the import line (currently `from services.cache_service import get_cached, write_cache, clear_data_cache, _DATA_CACHE`) to drop `_DATA_CACHE` (no longer exists) and replace the old `test_clear_data_cache_wipes_memory` test body with:

```python
def test_clear_data_cache_wipes_all_domains():
    from services.cache_service import set_domain, get_domain, clear_data_cache, DOMAIN_ACTIVE
    set_domain(DOMAIN_ACTIVE, pd.DataFrame())
    clear_data_cache()
    assert get_domain(DOMAIN_ACTIVE) is None
```

In `tests/test_data_service.py`, every occurrence of `cs._DATA_CACHE.clear()` / `cs._CACHE_TIMESTAMPS.clear()` (used to reset cache state between tests) becomes `cs.clear_data_cache()` (the public function now does the same full wipe). Find and replace all such pairs.

In `tests/test_db.py`, search for the same internals (`grep -n "_DATA_CACHE\|_CACHE_TIMESTAMPS\|clear_data_cache" tests/test_db.py` to find exact lines) and apply the same replacement pattern: any direct `_DATA_CACHE`/`_CACHE_TIMESTAMPS` manipulation becomes a call to the new `set_domain`/`clear_data_cache()` functions; any call to `clear_data_cache(<year>)` (a season year, e.g. `clear_data_cache(2024)`) is from code this plan's later tasks will rewrite to pass a domain name instead — leave a `# TODO(Task 4)` comment on any such call for now rather than guessing the right domain in this task, since Task 4 owns deciding which domain each `db_service.py` writer signals.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cache_service.py tests/test_data_service.py tests/test_db.py -v`
Expected: PASS for the new domain tests; the pre-existing tests that were only touched for the rename should still pass unchanged. `test_data_service.py`'s `test_load_data` etc. will still reference `load_data()` which Task 3 hasn't rewritten yet — they should still pass since `load_data()` itself is untouched by this task.

- [ ] **Step 6: Commit**

```bash
git add services/cache_service.py tests/test_cache_service.py tests/test_data_service.py tests/test_db.py
git commit -m "refactor: replace year-keyed cache with named domains"
```

---

## Task 2: Domain-aware invalidation signaling

**Files:**
- Modify: `services/db_service.py` (the `signal_data_update()` function and its ~10 call sites)
- Modify: `services/data_service.py:52-84` (the remote-check block inside `load_data()`)
- Test: `tests/test_db.py` (signal_data_update tests)

**Interfaces:**
- Consumes: `DOMAIN_SIGNAL_FIELDS`, `DOMAIN_STATIC`, `get_domain_timestamp`, `clear_domain` from Task 1.
- Produces: `signal_data_update(domain: str = DOMAIN_STATIC) -> None` (new required-ish `domain` parameter, defaults to `DOMAIN_STATIC` to match every current call site's actual behavior — all of today's callers are static-collection writers). A new `check_remote_signals() -> None` function in `data_service.py` that later tasks' `load_data()` rewrite calls instead of the current inline block.

Find `signal_data_update`'s current definition first: `grep -n "def signal_data_update" services/db_service.py` (it's referenced at line 10/11's import and called at lines 273, 384, 431, 468, 645, 714 per earlier investigation — read the actual current function body before editing, since this plan was written against those line numbers and a rebase since then could have shifted them).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py` (find the existing `signal_data_update` tests first with `grep -n "signal_data_update" tests/test_db.py` and place these alongside them):

```python
def test_signal_data_update_writes_domain_specific_field(mock_firestore):
    from services.db_service import signal_data_update
    from services.cache_service import DOMAIN_ACTIVE

    signal_data_update(DOMAIN_ACTIVE)

    mock_firestore.collection.assert_called_with("metadata")
    mock_firestore.collection.return_value.document.assert_called_with("cache_control")
    set_call = mock_firestore.collection.return_value.document.return_value.set
    set_call.assert_called_once()
    args, kwargs = set_call.call_args
    assert list(args[0].keys()) == ["active_updated"]
    assert kwargs.get("merge") is True

def test_signal_data_update_defaults_to_static_domain(mock_firestore):
    from services.db_service import signal_data_update

    signal_data_update()  # no argument -- must match every pre-existing call site's intent

    set_call = mock_firestore.collection.return_value.document.return_value.set
    args, kwargs = set_call.call_args
    assert list(args[0].keys()) == ["static_updated"]
    assert kwargs.get("merge") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_db.py -k "signal_data_update_writes_domain or signal_data_update_defaults" -v`
Expected: FAIL — the current `signal_data_update()` writes `{"last_update": time.time()}` with no `merge=True`, so both assertions on the written dict's keys fail.

- [ ] **Step 3: Rewrite `signal_data_update()`**

Replace the current function body in `services/db_service.py` (read it first to preserve its docstring/imports) with:

```python
def signal_data_update(domain: str = "static") -> None:
    """Signal that `domain`'s cached data changed, so every process (this
    one and, via the 60s remote-check poll, any other -- including
    winspool-predict-daily, which never shares memory with the web service)
    knows to refresh that domain's cache.

    Writes with merge=True: metadata/cache_control holds one field per
    domain in the same document, and a bare .set() would wipe every other
    domain's field.
    """
    from services.cache_service import DOMAIN_SIGNAL_FIELDS
    db = get_db()
    if db is None:
        return
    field = DOMAIN_SIGNAL_FIELDS[domain]
    db.collection("metadata").document("cache_control").set(
        {field: time.time()}, merge=True
    )
```

(`domain: str = "static"` rather than importing `DOMAIN_STATIC` as the default avoids a module-level circular import between `db_service.py` and `cache_service.py` — `cache_service.py` already imports from `db_service.py` inside function bodies to avoid exactly this; match that existing pattern by importing `DOMAIN_SIGNAL_FIELDS` inside the function body too, as shown above. `"static"` and `DOMAIN_STATIC` must be the same string — Task 1 fixes `DOMAIN_STATIC = "static"`, so this is safe today, not a coincidence to maintain by hand.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_db.py -k "signal_data_update_writes_domain or signal_data_update_defaults" -v`
Expected: PASS.

- [ ] **Step 5: Run the full existing `signal_data_update` call-site tests**

Run: `pytest tests/test_db.py -v`
Expected: every pre-existing test that calls a function which internally calls `signal_data_update()` (e.g. `add_player`) still passes, since the default argument preserves today's behavior (`static_updated` is written, with `merge=True` now added). If any pre-existing test asserted the *exact* dict passed to `.set()` (e.g. asserted `{"last_update": ...}` verbatim), it will now fail because the field name changed from `last_update` to `static_updated` and `merge=True` is now present — update any such assertion to match the new field/merge shape rather than skip it.

- [ ] **Step 6: Replace `data_service.py`'s remote-check block with a domain-aware version**

Read `services/data_service.py:52-84` first (the block starting `# 1. Check for remote invalidation signals...`). Replace it with a call to a new, separately-testable function:

```python
def check_remote_signals(use_local: bool) -> None:
    """Poll metadata/cache_control (at most once per _REMOTE_CHECK_INTERVAL)
    and clear any cache domain whose remote signal is newer than what this
    process has cached -- the only channel by which a separate process
    (winspool-predict-daily, or another web-service instance) can tell this
    process its cached data is stale.
    """
    import services.cache_service as cs
    current_time = time.time()
    if use_local or (current_time - cs._LAST_REMOTE_CHECK) <= cs._REMOTE_CHECK_INTERVAL:
        return
    cs._LAST_REMOTE_CHECK = current_time
    try:
        from services.db_service import get_db
        db = get_db()
        if not db:
            return
        ctrl = db.collection("metadata").document("cache_control").get(timeout=5)
        if not ctrl.exists:
            return
        remote = ctrl.to_dict()
        for domain, field in cs.DOMAIN_SIGNAL_FIELDS.items():
            remote_ts = remote.get(field, 0)
            if remote_ts > cs.get_domain_timestamp(domain):
                logger.info("Remote invalidation detected for domain '%s' (remote=%s, local=%s).",
                            domain, remote_ts, cs.get_domain_timestamp(domain))
                cs.clear_domain(domain)
    except Exception as e:
        logger.warning("Failed to check remote cache control: %s", e)
```

Call `check_remote_signals(use_local)` at the top of `load_data()` in place of the block being replaced (Task 3 will restructure `load_data()`'s body further, but this call belongs at its current position in the function regardless).

- [ ] **Step 7: Write a test for the new remote-check function**

```python
def test_check_remote_signals_clears_only_domains_with_newer_signal(mock_firestore, monkeypatch):
    import time
    from services.data_service import check_remote_signals
    import services.cache_service as cs

    cs.set_domain(cs.DOMAIN_ACTIVE, "old-active", timestamp=100.0)
    cs.set_domain(cs.DOMAIN_STATIC, "old-static", timestamp=100.0)
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", 0)

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"active_updated": 200.0, "static_updated": 50.0}
    mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

    check_remote_signals(use_local=False)

    assert cs.get_domain(cs.DOMAIN_ACTIVE) is None       # 200 > 100 -> cleared
    assert cs.get_domain(cs.DOMAIN_STATIC) == "old-static"  # 50 < 100 -> untouched
    cs.clear_data_cache()  # cleanup
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_data_service.py -k "check_remote_signals" -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add services/db_service.py services/data_service.py tests/test_db.py tests/test_data_service.py
git commit -m "feat: make cache invalidation signaling domain-scoped"
```

---

## Task 3: Rewrite `load_data()`/`load_data_season()` around active/historical/static buckets

**Files:**
- Modify: `services/data_service.py:36-257` (`load_data`, `load_data_season`, and the `fetch_or_load`/`fetch_tasks` machinery inside `load_data`)
- Test: `tests/test_data_service.py`

**Interfaces:**
- Consumes: `DOMAIN_ACTIVE`, `DOMAIN_HISTORICAL`, `DOMAIN_STATIC`, `get_domain`, `set_domain`, `get_domain_timestamp`, `_CACHE_TTL_SECONDS` (Task 1); `check_remote_signals` (Task 2).
- Produces: `load_data(year=None) -> DataBundle` (same 7-field shape as today — `standings, teams, games, players, draft_order, draft_results, draft_order_rules`), `load_data_season(year) -> DataBundle` (now a thin wrapper: `load_data(year=year)`, since the season-filtering logic it used to duplicate is now `load_data`'s own job). `get_active_season()` (existing function, untouched signature) is now called internally to resolve which season is "active" rather than only by external callers.

This is the largest single task in this plan. Read the current `load_data()` (lines 36-229) in full before starting — this task replaces its body, not just a few lines.

- [ ] **Step 1: Write the failing tests**

```python
def test_load_data_active_season_comes_from_active_bucket(monkeypatch):
    """A season equal to the resolved active season is served from the
    active bucket, not by scanning the historical bucket."""
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    bundle = load_data()  # cold start -- bootstraps both buckets
    active_games = bundle.games
    assert not active_games.empty
    # The active bucket must now be warm without a second cold-start fetch.
    assert cs.get_domain(cs.DOMAIN_ACTIVE) is not None
    assert cs.get_domain(cs.DOMAIN_HISTORICAL) is not None

def test_load_data_historical_year_is_served_from_historical_bucket(monkeypatch):
    import services.cache_service as cs
    from services.data_service import load_data, get_active_season
    cs.clear_data_cache()
    bundle = load_data()
    active_season = get_active_season(bundle.games, bundle.draft_results, bundle.draft_order_rules)
    historical_year = active_season - 1
    result = load_data(year=historical_year)
    assert (result.games["season"] == historical_year).all() or result.games.empty

def test_load_data_none_returns_full_history_concat_of_both_buckets():
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    full = load_data(year=None)
    active_season = full.games["season"].max()
    historical_only = load_data(year=int(active_season) - 1)
    # every historical row must also appear in the unfiltered bundle
    if not historical_only.games.empty:
        assert historical_only.games["season"].iloc[0] in full.games["season"].values

def test_load_data_season_is_now_a_thin_wrapper():
    from services.data_service import load_data, load_data_season, get_active_season
    bundle = load_data()
    season = get_active_season(bundle.games, bundle.draft_results, bundle.draft_order_rules)
    direct = load_data(year=season)
    via_season = load_data_season(season)
    assert list(via_season.games.columns) == list(direct.games.columns)

def test_static_bucket_shared_across_year_arguments(monkeypatch):
    """players/draft_order/draft_results/draft_order_rules/teams never
    differ by year -- confirm both calls hit the same cached static bucket
    (no extra Firestore fetch for the second call)."""
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    first = load_data(year=None)
    static_after_first = cs.get_domain(cs.DOMAIN_STATIC)
    assert static_after_first is not None
    second = load_data(year=2020)
    assert cs.get_domain(cs.DOMAIN_STATIC) is static_after_first  # same object, not refetched
    assert list(first.players.columns) == list(second.players.columns)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_service.py -k "active_season_comes_from or historical_year_is_served or full_history_concat or thin_wrapper or static_bucket_shared" -v`
Expected: FAIL — `load_data()` hasn't been rewritten yet, so `cs.DOMAIN_ACTIVE`/`get_domain` calls against it return `None` (nothing populates them under the old code path).

- [ ] **Step 3: Implement the bucket-building helpers**

In `services/data_service.py`, add these new module-level functions (placed above `load_data`, after the existing imports):

```python
def _fetch_static_bucket():
    """The 5 collections that are never season-filtered -- one shared
    in-memory bundle instead of being re-fetched inside every year-keyed
    slot the old cache used."""
    return {
        "teams":             get_collection_df("nfl_teams"),
        "players":           get_collection_df("players"),
        "draft_order":       get_collection_df("draft_order"),
        "draft_results":     get_collection_df("draft_results"),
        "draft_order_rules": get_collection_df("draft_order_rules"),
    }


def _get_static_bucket():
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_STATIC)
    if cached is not None:
        return cached
    bucket = _fetch_static_bucket()
    cs.set_domain(cs.DOMAIN_STATIC, bucket)
    return bucket


def _bootstrap_games_standings():
    """Cold-start only: one unfiltered fetch of nfl_games/nfl_standings,
    used to determine the active season, then split in memory into the
    active/historical buckets so this fetch is never repeated per-bucket.
    """
    import services.cache_service as cs
    all_games = get_collection_df("nfl_games")
    all_standings = get_collection_df("nfl_standings")
    static = _get_static_bucket()
    season = get_active_season(all_games, static["draft_results"], static["draft_order_rules"])

    active_bucket = {
        "season": season,
        "games": all_games[all_games["season"] == season].copy() if not all_games.empty else all_games,
        "standings": all_standings[all_standings["season"] == season].copy() if not all_standings.empty else all_standings,
    }
    historical_bucket = {
        "games": all_games[all_games["season"] < season].copy() if not all_games.empty else all_games,
        "standings": all_standings[all_standings["season"] < season].copy() if not all_standings.empty else all_standings,
    }
    cs.set_domain(cs.DOMAIN_ACTIVE, active_bucket)
    cs.set_domain(cs.DOMAIN_HISTORICAL, historical_bucket)
    return active_bucket, historical_bucket


def _get_active_bucket():
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_ACTIVE)
    if cached is not None:
        # Season-rollover guard: if the static bucket's own draft data now
        # resolves to a different active season, this bucket is stale by
        # identity, not just by TTL -- rebuild regardless of cache age.
        static = _get_static_bucket()
        all_games_for_check = pd.concat([
            cs.get_domain(cs.DOMAIN_HISTORICAL)["games"] if cs.get_domain(cs.DOMAIN_HISTORICAL) else pd.DataFrame(),
            cached["games"],
        ], ignore_index=True) if not cached["games"].empty else cached["games"]
        current_active = get_active_season(all_games_for_check, static["draft_results"], static["draft_order_rules"])
        if current_active == cached["season"]:
            return cached
        cs.clear_domain(cs.DOMAIN_ACTIVE)
    active, _ = _bootstrap_games_standings()
    return active


def _get_historical_bucket():
    import services.cache_service as cs
    cached = cs.get_domain(cs.DOMAIN_HISTORICAL)
    if cached is not None:
        return cached
    _, historical = _bootstrap_games_standings()
    return historical
```

(Note: `_get_active_bucket()`'s rollover check reconstructs a games frame to re-derive the active season on every warm call — this is a real, if small, per-call cost. If profiling later shows this matters, it can be cheapened by caching the resolved season number as its own tiny domain value updated only when `static` or `active` actually rebuild, rather than recomputed every call; not needed for correctness now, flagged here rather than silently over-engineered up front.)

- [ ] **Step 4: Rewrite `load_data()` and `load_data_season()`**

Replace `load_data()`'s body (lines 36-229) with:

```python
def load_data(year: int = None):
    """
    Loads data for the given season year using the active/historical/static
    cache domains (see docs/superpowers/specs/2026-09-14-cache-mutability-
    redesign-design.md). year=None returns the full multi-year history.
    """
    use_local = os.environ.get('USE_LOCAL_DATA', 'False').lower() == 'true'
    check_remote_signals(use_local)

    active = _get_active_bucket()
    historical = _get_historical_bucket()
    static = _get_static_bucket()

    if year is None:
        games = pd.concat([historical["games"], active["games"]], ignore_index=True)
        standings = pd.concat([historical["standings"], active["standings"]], ignore_index=True)
    elif year == active["season"]:
        games = active["games"]
        standings = active["standings"]
    else:
        games = historical["games"][historical["games"]["season"] == year].copy() if not historical["games"].empty else historical["games"]
        standings = historical["standings"][historical["standings"]["season"] == year].copy() if not historical["standings"].empty else historical["standings"]

    teams = static["teams"]
    players = static["players"]
    draft_order = static["draft_order"]
    draft_results = static["draft_results"]
    draft_order_rules = static["draft_order_rules"]

    # Schema Healing — Ensure MixedCase column names for downstream logic
    RENAME_MAP = {
        'playerid': 'playerId',
        'fullname': 'fullName',
        'draftpick': 'draftPick',
        'draftorder': 'draftOrder',
        'teamid': 'teamId',
        'nickname': 'nickName',
        'score': 'TotalWinsBySeason'
    }
    for df in [standings, teams, games, players, draft_order, draft_results, draft_order_rules]:
        if not df.empty:
            df.rename(columns={k: v for k, v in RENAME_MAP.items() if k in df.columns}, inplace=True)
            if 'season' in df.columns:
                df['season'] = pd.to_numeric(df['season'], errors='coerce').fillna(0).astype(int)
            if 'week' in df.columns:
                df['week'] = pd.to_numeric(df['week'], errors='coerce').fillna(0).astype(int)
            if 'playerId' in df.columns:
                df['playerId'] = pd.to_numeric(df['playerId'], errors='coerce').fillna(0).astype(int)
            if 'draftPick' in df.columns:
                df['draftPick'] = pd.to_numeric(df['draftPick'], errors='coerce').fillna(0).astype(int)
            if 'draftOrder' in df.columns:
                df['draftOrder'] = pd.to_numeric(df['draftOrder'], errors='coerce').fillna(0).astype(int)

    if not players.empty and 'playerId' in players.columns:
        players = players.dropna(subset=['playerId'])
        players = players.sort_values('playerId').drop_duplicates(subset=['playerId'], keep='last').reset_index(drop=True)

    return DataBundle(standings, teams, games, players, draft_order, draft_results, draft_order_rules)


def load_data_season(year: int):
    """Returns data sliced to a single season year -- now a thin wrapper,
    since load_data(year=X) does exactly this via the historical/active
    bucket split above."""
    return load_data(year=year)
```

Delete the old `fetch_or_load`/`fetch_tasks`/`ThreadPoolExecutor` machinery entirely (lines 97-187 of the original) — it's superseded by `_fetch_static_bucket`/`_bootstrap_games_standings` above. Delete the old `local_dir`/pkl-fallback logic from inside `load_data()` too; local-pkl support for the bucket helpers is out of scope for this plan (all of Task 3's tests run against `USE_LOCAL_DATA=true` fixture data the same way `test_load_data()` already does today, reading through `get_collection_df()`, which already handles the local-vs-Firestore split — confirm this by reading `services/db_service.py::get_collection_df()` before assuming, since this task's tests depend on it still working correctly in local mode).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_data_service.py -v`
Expected: PASS, including every pre-existing test in this file (`test_load_data`, `test_load_data_with_debug_flag`, `test_load_data_year_slice_falls_back_to_base_pkl` — the last of these tested the now-deleted pkl-fallback path directly; read it and either adapt it to assert against the new bucket-based behavior or, if it's now testing removed machinery, replace it with an equivalent assertion that a historical year's data is still correctly retrievable in local mode).

- [ ] **Step 6: Add the season-rollover regression test**

```python
def test_active_bucket_rebuilds_when_active_season_changes(monkeypatch):
    """Simulates a season rollover: the static bucket's draft data now
    resolves to a later active season than the cached active bucket."""
    import services.cache_service as cs
    from services.data_service import load_data, _get_active_bucket
    cs.clear_data_cache()
    first = load_data()
    old_active_season = first.games["season"].max() if not first.games.empty else None

    # Force a stale active bucket claiming an older season than what
    # get_active_season() would now resolve to.
    stale = cs.get_domain(cs.DOMAIN_ACTIVE)
    if stale and old_active_season is not None:
        cs.set_domain(cs.DOMAIN_ACTIVE, {**stale, "season": stale["season"] - 1})
        rebuilt = _get_active_bucket()
        assert rebuilt["season"] != stale["season"] - 1  # rebuilt against the real active season
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_data_service.py -k "rebuilds_when_active_season_changes" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add services/data_service.py tests/test_data_service.py
git commit -m "feat: rewrite load_data() around active/historical/static cache buckets"
```

---

## Task 4: Fix the original staleness bug and audit `static`-domain writers

**Files:**
- Modify: `services/db_service.py` (`add_draft_result`, `delete_draft_pick`, and every other `clear_data_cache(...)`/`signal_data_update(...)` call site — confirmed sites: lines ~272-273, ~312, ~333, ~355, ~383-384, ~406, ~430, ~467-468, per earlier investigation; re-grep before editing since line numbers may have shifted)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `DOMAIN_STATIC`, `clear_data_cache(domain)`, `signal_data_update(domain)` from Tasks 1-2.

This task closes both halves of the original bug (`docs/superpowers/specs/2026-09-12-cache-invalidation-gap-followup.md`): `clear_data_cache(season)` missing the `'all'` eviction (now structurally impossible — `static` is one domain, not year-keyed), and the missing `signal_data_update()` call for cross-instance correctness.

- [ ] **Step 1: Write the failing tests**

```python
def test_add_draft_result_clears_static_domain_and_signals(mock_firestore, monkeypatch):
    import services.cache_service as cs
    from services.db_service import add_draft_result
    cs.set_domain(cs.DOMAIN_STATIC, "stale-static-bundle")

    add_draft_result(season=2026, draft_pick=1, player_id=14, team="KC")

    assert cs.get_domain(cs.DOMAIN_STATIC) is None  # evicted
    set_call = mock_firestore.collection.return_value.document.return_value.set
    # one of the .set() calls must be the cache_control signal with static_updated
    signal_calls = [c for c in set_call.call_args_list if c.args and "static_updated" in c.args[0]]
    assert len(signal_calls) == 1
    assert signal_calls[0].kwargs.get("merge") is True

def test_delete_draft_pick_clears_static_domain_and_signals(mock_firestore, monkeypatch):
    import services.cache_service as cs
    from services.db_service import delete_draft_pick
    cs.set_domain(cs.DOMAIN_STATIC, "stale-static-bundle")

    delete_draft_pick(season=2026, draft_pick=1)

    assert cs.get_domain(cs.DOMAIN_STATIC) is None
    set_call = mock_firestore.collection.return_value.document.return_value.set
    signal_calls = [c for c in set_call.call_args_list if c.args and "static_updated" in c.args[0]]
    assert len(signal_calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -k "add_draft_result_clears_static or delete_draft_pick_clears_static" -v`
Expected: FAIL — `add_draft_result`/`delete_draft_pick` currently call `clear_data_cache(season)` (a season year, meaningless to the new domain-keyed `clear_data_cache`) and never call `signal_data_update()` at all.

- [ ] **Step 3: Fix `add_draft_result()` and `delete_draft_pick()`**

Read the current bodies first (`grep -n "def add_draft_result\|def delete_draft_pick" -A 40 services/db_service.py`). Replace the trailing `clear_data_cache(season)` line in each with:

```python
    clear_data_cache(DOMAIN_STATIC)
    signal_data_update(DOMAIN_STATIC)
```

adding `from services.cache_service import DOMAIN_STATIC` to the module's existing `from services.cache_service import clear_data_cache, _cache_key` import line (drop `_cache_key`, which no longer exists per Task 1 — check whether anything else in this file still imports it before removing).

Delete the "KNOWN GAP" comment blocks above both functions (the ones referencing `docs/superpowers/specs/2026-09-12-cache-invalidation-gap-followup.md`) — the gap they describe no longer exists.

- [ ] **Step 4: Audit and fix every other `clear_data_cache(...)`/`signal_data_update(...)` call site in this file**

Run `grep -n "clear_data_cache(\|signal_data_update(" services/db_service.py` and, for each remaining call site (`add_player`, `delete_season_data`, `add_draft_order`, `add_draft_order_rule`, `update_player_profile`, per the design's signal-routing table — confirm the actual full list from the grep, don't assume this list is exhaustive), change any bare `clear_data_cache()`/`signal_data_update()` call to explicitly pass `DOMAIN_STATIC` — these all write to `players`/`draft_order`/`draft_results`/`draft_order_rules`, which now live in the `static` domain. A bare `clear_data_cache()` with no argument still works today (Task 1 made it "wipe everything" when called with no argument), but leaving it bare here is misleading now that named domains exist — make every call site explicit about which domain it's signaling, matching the "one clear owner each" principle the whole redesign is built on.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS, including every pre-existing test for the writers touched in Step 4 (their behavior is unchanged — same domain wiped, just explicitly named now).

- [ ] **Step 6: Write the live-draft staleness regression test**

This is the original bug's real-world regression test. Add to `tests/test_db.py`:

```python
def test_add_draft_result_makes_pick_visible_through_load_data(mock_firestore, monkeypatch):
    """Regression test for the original staleness bug: a page reading via
    bare load_data() (year=None) must see a just-added pick immediately,
    not after up to an hour's TTL."""
    import services.cache_service as cs
    from services.data_service import load_data
    from services.db_service import add_draft_result

    cs.clear_data_cache()
    before = load_data()  # warms the static domain with pre-pick draft_results
    before_count = len(before.draft_results)

    # Simulate the pick actually landing in draft_results (mock_firestore
    # doesn't persist real documents, so patch get_collection_df's
    # draft_results read to reflect one more row after the write).
    import pandas as pd
    from unittest.mock import patch
    after_df = pd.concat([
        before.draft_results,
        pd.DataFrame([{"season": 2026, "draftPick": 1, "playerId": 14, "team": "KC"}]),
    ], ignore_index=True)

    add_draft_result(season=2026, draft_pick=1, player_id=14, team="KC")
    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.side_effect = lambda name, *a, **kw: (
            after_df if name == "draft_results" else pd.DataFrame()
        )
        after = load_data()
    assert len(after.draft_results) == before_count + 1
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_db.py -k "add_draft_result_makes_pick_visible" -v`
Expected: PASS — the `static` domain was cleared by `add_draft_result()` in Step 3, so `load_data()`'s next call rebuilds it from the (mocked) fresh Firestore read rather than serving the stale cached bundle.

- [ ] **Step 8: Commit**

```bash
git add services/db_service.py tests/test_db.py
git commit -m "fix: close draft-pick cache staleness bug and cross-instance signal gap"
```

---

## Task 5: Reroute redundant bypass reads through the static bucket

**Files:**
- Modify: `services/draft_service.py:67` (raw `draft_order` fetch)
- Modify: `services/mock_draft_service.py:36` (raw `draft_order_rules` fetch)
- Modify: `services/push_service.py:40` (per-player lookup)
- Test: `tests/test_draft_service.py`, `tests/test_mock_draft_service.py`, `tests/test_push_service.py` (find each with `find tests -iname "*draft_service*" -o -iname "*mock_draft_service*" -o -iname "*push_service*"`)

**Interfaces:**
- Consumes: `_get_static_bucket()` from Task 3 (note: this is a "private" `_`-prefixed helper today — either export a public `get_static_bucket()` alias from `data_service.py` for these three call sites to import, or import the underscore-prefixed name directly, matching whatever convention this codebase already uses for cross-module internal helper access; check a couple of existing cross-`services/*.py` imports first, e.g. how `draft_service.py` currently imports from `data_service.py`, and follow that pattern rather than inventing a new one).

- [ ] **Step 1: Read each call site**

```bash
grep -n -B3 -A3 "get_collection_df('draft_order')" services/draft_service.py
grep -n -B3 -A3 'get_collection_df("draft_order_rules")' services/mock_draft_service.py
grep -n -B3 -A3 "collection(\"players\").document" services/push_service.py
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_draft_service.py
def test_resolve_season_uses_static_bucket_not_a_fresh_fetch(monkeypatch):
    import services.cache_service as cs
    from services import draft_service, data_service
    cs.clear_data_cache()
    data_service.load_data()  # warm the static bucket
    from unittest.mock import patch
    with patch("services.data_service.get_collection_df") as mock_fetch:
        draft_service.load_draft_state(year=None)  # or whatever internal function resolves the season -- confirm the real entry point by reading draft_service.py
        assert not any(c.args and c.args[0] == "draft_order" for c in mock_fetch.call_args_list)
```

```python
# tests/test_mock_draft_service.py
def test_get_pick_sequence_uses_static_bucket_not_a_fresh_fetch(monkeypatch):
    import services.cache_service as cs
    from services import mock_draft_service, data_service
    cs.clear_data_cache()
    data_service.load_data()
    from unittest.mock import patch
    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_draft_service.get_pick_sequence()
        assert not any(c.args and c.args[0] == "draft_order_rules" for c in mock_fetch.call_args_list)
```

```python
# tests/test_push_service.py
def test_send_push_notification_uses_static_bucket_players(monkeypatch):
    import services.cache_service as cs
    from services import push_service, data_service
    cs.clear_data_cache()
    data_service.load_data()
    from unittest.mock import patch
    with patch("services.db_service.get_db") as mock_get_db:
        push_service.send_push_notification(player_id=14, title="t", body="b")
        # the players collection must not be hit with a fresh .document().get()
        # for this player -- confirm via whichever assertion matches this
        # file's existing test style for send_push_notification (read it first)
```

(These three test bodies name the exact behavior to verify — "the redundant fetch is gone" — but the precise mocking shape depends on each file's actual current structure, which you must read before finalizing the assertion; do not guess a mock target that doesn't match the real import.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_draft_service.py tests/test_mock_draft_service.py tests/test_push_service.py -k "static_bucket" -v`
Expected: FAIL — all three still call `get_collection_df`/Firestore directly.

- [ ] **Step 4: Reroute `draft_service.py:67`**

Replace the raw `get_collection_df('draft_order')` call with a lookup against the static bucket:

```python
from services.data_service import _get_static_bucket
# ...
draft_order_df = _get_static_bucket()["draft_order"]
```

- [ ] **Step 5: Reroute `mock_draft_service.py:36`**

Same pattern for `draft_order_rules`:

```python
from services.data_service import _get_static_bucket
# ...
rules_df = _get_static_bucket()["draft_order_rules"]
```

- [ ] **Step 6: Reroute `push_service.py:40`**

Replace the direct `get_db().collection("players").document(str(player_id)).get()` with a lookup by `playerId` in the static bucket's `players` DataFrame, falling back to the direct Firestore read only if the player isn't found in the cached frame (defensive — a brand-new player added between cache refreshes should still resolve):

```python
from services.data_service import _get_static_bucket

def _get_player_row(player_id):
    players_df = _get_static_bucket()["players"]
    if not players_df.empty and "playerId" in players_df.columns:
        match = players_df[players_df["playerId"] == int(player_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    # Fallback: not in the cached bundle (e.g. added after the bucket warmed).
    db = get_db()
    if db is None:
        return None
    doc = db.collection("players").document(str(player_id)).get()
    return doc.to_dict() if doc.exists else None
```

Use `_get_player_row(player_id)` wherever `push_service.py` previously did the direct `.get()`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_draft_service.py tests/test_mock_draft_service.py tests/test_push_service.py -v`
Expected: PASS, including all pre-existing tests in these three files (their observable behavior — the data returned — is unchanged; only the fetch path changed).

- [ ] **Step 8: Commit**

```bash
git add services/draft_service.py services/mock_draft_service.py services/push_service.py tests/test_draft_service.py tests/test_mock_draft_service.py tests/test_push_service.py
git commit -m "perf: read draft_order/draft_order_rules/players from the static cache bucket instead of refetching"
```

---

## Task 6: Cache model predictions (active/historical split, own signal domain)

**Files:**
- Modify: `services/data_service.py` (`get_preseason_predictions`, `get_consensus_projections`; add `get_game_predictions` caching in `cache_service.py`)
- Modify: `services/cache_service.py` (`get_game_predictions`)
- Modify: `scripts/cache_builder.py` (signal `predictions_active_updated` after its daily write)
- Modify: `scripts/backfill_schedule_predictions.py:432-433` (replace the bare `cache_control` write with a domain-scoped signal)
- Modify: `scripts/predict_season.py:185-186` (same)
- Test: `tests/test_data_service.py`, `tests/test_cache_service.py`

**Interfaces:**
- Consumes: `DOMAIN_PREDICTIONS_ACTIVE`, `DOMAIN_PREDICTIONS_HISTORICAL`, `get_domain`, `set_domain` (Task 1); `signal_data_update(domain)` (Task 2); `_get_active_bucket()`'s resolved `season` value (Task 3) to decide which predictions bucket a request falls into.
- Produces: `get_preseason_predictions(season)`, `get_consensus_projections(season)`, `get_game_predictions(season)` all become cache-backed (same return signatures as today — no caller elsewhere in the codebase needs to change).

- [ ] **Step 1: Write the failing tests**

```python
def test_get_preseason_predictions_active_season_is_cached(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()  # resolves the active season
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    from unittest.mock import patch
    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(active_season)
        data_service.get_preseason_predictions(active_season)  # second call
        preseason_calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "preseason_predictions"]
        assert len(preseason_calls) == 1  # only the first call actually fetched

def test_get_preseason_predictions_historical_season_is_cached_separately(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]
    historical_season = active_season - 1

    from unittest.mock import patch
    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(historical_season)
        data_service.get_preseason_predictions(historical_season)
        calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "preseason_predictions"]
        assert len(calls) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_service.py -k "preseason_predictions_active_season_is_cached or historical_season_is_cached_separately" -v`
Expected: FAIL — `get_preseason_predictions()` currently calls `get_collection_df()` on every single call, uncached.

- [ ] **Step 3: Implement the predictions cache**

In `services/data_service.py`, add a helper mirroring the active/historical split pattern from Task 3, but for predictions:

```python
def _predictions_domain_for(season: int) -> str:
    import services.cache_service as cs
    active = _get_active_bucket()
    return cs.DOMAIN_PREDICTIONS_ACTIVE if season == active["season"] else cs.DOMAIN_PREDICTIONS_HISTORICAL


def _get_predictions_bucket(season: int) -> dict:
    """Returns {"preseason": {team: {...}}, "consensus": {team: {...}}} for
    `season`, cached per-season within whichever domain (active/historical)
    that season falls into today."""
    import services.cache_service as cs
    domain = _predictions_domain_for(season)
    bucket = cs.get_domain(domain) or {}
    if season in bucket:
        return bucket[season]

    preseason_df = get_collection_df("preseason_predictions", filters=[("season", "==", season)])
    consensus_df = get_collection_df("consensus_projections", filters=[("season", "==", season)])
    entry = {"preseason_df": preseason_df, "consensus_df": consensus_df}
    bucket[season] = entry
    cs.set_domain(domain, bucket)
    return entry
```

Rewrite `get_preseason_predictions(season)` and `get_consensus_projections(season)` to read from `_get_predictions_bucket(season)["preseason_df"]`/`["consensus_df"]` instead of calling `get_collection_df()` directly, keeping every line below the fetch (the `res[row["team"]] = {...}` dict-building loop) exactly as it is today — only the data-acquisition line changes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_service.py -v`
Expected: PASS, including every pre-existing prediction-related test in this file (the returned dict shape is unchanged).

- [ ] **Step 5: Cache `get_game_predictions()` the same way**

In `services/cache_service.py`, apply the identical pattern to `get_game_predictions(season)` (currently at lines 158-178): check the same predictions bucket by domain/season, falling back to the existing Firestore/local-pkl read only on a cache miss. Write the equivalent test in `tests/test_cache_service.py`, following the same two-call/one-fetch assertion shape as Step 1.

- [ ] **Step 6: Signal `predictions_active_updated` from `cache_builder.py`**

Read `scripts/cache_builder.py`'s end-of-run section (near its `if __name__` block or wherever it currently finishes a year's processing) and add, once per full run (not once per year in the loop):

```python
if write_firestore:  # or whatever the existing flag/condition is -- confirm from the real code
    os.environ["USE_LOCAL_DATA"] = "False"  # already required per CLAUDE.md's gotcha -- confirm this is already set earlier in the file before adding this line redundantly
    from services.db_service import signal_data_update, get_db
    from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE
    if get_db():
        signal_data_update(DOMAIN_PREDICTIONS_ACTIVE)
```

- [ ] **Step 7: Update `backfill_schedule_predictions.py` and `predict_season.py`'s signal calls**

In `scripts/backfill_schedule_predictions.py`, replace the bare write at line 433 (`db.collection("metadata").document("cache_control").set({"last_update": time.time()})`) with domain-aware signaling based on the run's `--seasons MIN MAX` argument:

```python
from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL
from services.db_service import signal_data_update
# `lo, hi` are this run's --seasons bounds (already parsed earlier in the
# script, per the args.seasons handling at line 304-305) -- reuse them
# rather than re-deriving. If --seasons wasn't passed, the run covered
# every season this script knows about, which must include the active one:
# signal both.
from services.data_service import _get_active_bucket
active_season = _get_active_bucket()["season"]
if args.seasons:
    lo, hi = args.seasons
    if hi < active_season:
        signal_data_update(DOMAIN_PREDICTIONS_HISTORICAL)
    elif lo > active_season:
        signal_data_update(DOMAIN_PREDICTIONS_ACTIVE)
    else:
        signal_data_update(DOMAIN_PREDICTIONS_ACTIVE)
        signal_data_update(DOMAIN_PREDICTIONS_HISTORICAL)
else:
    signal_data_update(DOMAIN_PREDICTIONS_ACTIVE)
    signal_data_update(DOMAIN_PREDICTIONS_HISTORICAL)
```

In `scripts/predict_season.py`, this script always targets one `--season` (default `_default_season()`, presumably the active/next season per its existing default logic — read `_default_season()` to confirm before assuming). Replace the bare write at line 185-186 with:

```python
from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL
from services.db_service import signal_data_update
from services.data_service import _get_active_bucket
domain = DOMAIN_PREDICTIONS_ACTIVE if season == _get_active_bucket()["season"] else DOMAIN_PREDICTIONS_HISTORICAL
signal_data_update(domain)
```

- [ ] **Step 8: Write the cross-process staleness regression test**

```python
def test_predict_season_write_is_picked_up_by_a_separate_load(monkeypatch):
    """Regression test for the cross-process staleness trap: a write from
    predict_season.py's process (a raw Firestore write + a scoped signal,
    never an in-process cache call) must be visible to a completely
    separate load_data()/prediction read within one remote-check cycle."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]
    data_service.get_preseason_predictions(active_season)  # warm the predictions cache
    assert cs.get_domain(cs.DOMAIN_PREDICTIONS_ACTIVE) is not None

    # Simulate predict_season.py's out-of-process signal.
    from unittest.mock import patch, MagicMock
    with patch("services.db_service.get_db") as mock_get_db:
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"predictions_active_updated": 9999999999.0}
        mock_get_db.return_value.collection.return_value.document.return_value.get.return_value = mock_doc
        cs._LAST_REMOTE_CHECK = 0  # force the next check to actually poll
        data_service.check_remote_signals(use_local=False)

    assert cs.get_domain(cs.DOMAIN_PREDICTIONS_ACTIVE) is None  # cleared by the remote signal
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest tests/test_data_service.py -k "predict_season_write_is_picked_up" -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add services/data_service.py services/cache_service.py scripts/cache_builder.py scripts/backfill_schedule_predictions.py scripts/predict_season.py tests/test_data_service.py tests/test_cache_service.py
git commit -m "feat: cache preseason/consensus/game predictions with active/historical domain split"
```

---

## Task 7: Cache admin analytics (`elo_history`/`nn_weekly_accuracy`) with one coarse signal

**Files:**
- Modify: `services/cache_service.py` (`get_all_elo_history`, `get_all_nn_weekly_accuracy`)
- Modify: `scripts/compute_elo.py` (add the missing `--firestore` signal — confirmed today it doesn't signal at all)
- Modify: `scripts/weekly_model_eval.py` (same — confirmed no signal today)
- Test: `tests/test_cache_service.py`

**Interfaces:**
- Consumes: `DOMAIN_ADMIN_ANALYTICS`, `get_domain`, `set_domain` (Task 1); `signal_data_update(domain)` (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
def test_get_all_elo_history_is_cached(mock_firestore, monkeypatch):
    from services import cache_service as cs
    cs.clear_data_cache()
    monkeypatch.setattr(cs, "_USE_LOCAL", False)
    doc = MagicMock()
    doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
    mock_firestore.collection.return_value.stream.return_value = [doc]

    cs.get_all_elo_history()
    cs.get_all_elo_history()

    assert mock_firestore.collection.return_value.stream.call_count == 1

def test_get_all_nn_weekly_accuracy_is_cached(mock_firestore, monkeypatch):
    from services import cache_service as cs
    cs.clear_data_cache()
    monkeypatch.setattr(cs, "_USE_LOCAL", False)
    doc = MagicMock()
    doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
    mock_firestore.collection.return_value.stream.return_value = [doc]

    cs.get_all_nn_weekly_accuracy()
    cs.get_all_nn_weekly_accuracy()

    assert mock_firestore.collection.return_value.stream.call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_service.py -k "get_all_elo_history_is_cached or get_all_nn_weekly_accuracy_is_cached" -v`
Expected: FAIL — both functions currently `.stream()` the whole collection on every call.

- [ ] **Step 3: Cache both functions**

In `services/cache_service.py`, wrap `get_all_elo_history()`'s Firestore branch (and `get_all_nn_weekly_accuracy()`'s, identically) with a `DOMAIN_ADMIN_ANALYTICS`-keyed cache check:

```python
def get_all_elo_history() -> list[dict]:
    """Return every computed Elo row across all seasons, sorted oldest first."""
    if _USE_LOCAL:
        # ... existing local-file loop, unchanged ...
        all_rows = []
        for p in sorted(_GAME_PRED_DIR.glob("elo_history_*.json")):
            try:
                with open(p) as f:
                    all_rows.extend(json.load(f).get("rows", []))
            except Exception:
                logger.warning("Failed to read %s", p)
        all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))
        return all_rows

    cached = get_domain(DOMAIN_ADMIN_ANALYTICS)
    if cached is not None and "elo_history" in cached:
        return cached["elo_history"]

    all_rows: list[dict] = []
    try:
        from services.db_service import get_db
        db = get_db()
        for doc in db.collection("elo_history").stream():
            all_rows.extend(doc.to_dict().get("rows", []))
    except Exception:
        logger.exception("Failed to fetch elo_history from Firestore")
    all_rows.sort(key=lambda r: (r.get("season", 0), r.get("week", 0)))

    bucket = get_domain(DOMAIN_ADMIN_ANALYTICS) or {}
    bucket["elo_history"] = all_rows
    set_domain(DOMAIN_ADMIN_ANALYTICS, bucket)
    return all_rows
```

Apply the identical shape to `get_all_nn_weekly_accuracy()`, storing its result under `bucket["nn_weekly_accuracy"]` in the same shared `DOMAIN_ADMIN_ANALYTICS` dict (both collections share one signal per the design's §4 — an elo write clearing the whole domain and forcing a re-stream of `nn_weekly_accuracy` too is an accepted, low-cost trade for admin-only traffic).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_service.py -v`
Expected: PASS, all pre-existing elo/nn_weekly_accuracy tests included (`TestEloHistoryCache`, `TestNnWeeklyAccuracyCache` classes) — their local-mode tests are untouched by this change; their Firestore-mode tests (`test_get_all_firestore_streams_every_doc`) must still pass on a *cold* cache (first call) since this task doesn't change first-call behavior, only repeat-call behavior — if a pre-existing test calls `get_all_elo_history()` more than once expecting two `.stream()` calls, update it to `clear_data_cache()` between calls or assert the new one-stream-then-cached behavior instead, matching what this task actually changed.

- [ ] **Step 5: Add the missing signal to `compute_elo.py --firestore`**

Read `scripts/compute_elo.py`'s `--firestore` write path (`grep -n "write_elo_history_season\|args.firestore" scripts/compute_elo.py`) and add, once after the full write loop completes:

```python
from services.db_service import signal_data_update, get_db
from services.cache_service import DOMAIN_ADMIN_ANALYTICS
if args.firestore and get_db():
    signal_data_update(DOMAIN_ADMIN_ANALYTICS)
```

- [ ] **Step 6: Add the missing signal to `weekly_model_eval.py --firestore`**

Read its `--firestore` branch (near `write_nn_weekly_accuracy_rows(args.season, rows, use_local=False)`, line ~294) and add immediately after:

```python
from services.db_service import signal_data_update, get_db
from services.cache_service import DOMAIN_ADMIN_ANALYTICS
if get_db():
    signal_data_update(DOMAIN_ADMIN_ANALYTICS)
```

- [ ] **Step 7: Commit**

```bash
git add services/cache_service.py scripts/compute_elo.py scripts/weekly_model_eval.py tests/test_cache_service.py
git commit -m "perf: cache elo_history/nn_weekly_accuracy full-collection streams; add missing invalidation signals"
```

---

## Task 8: `batch_upload()` diff-before-write, `daily_nfl_sync.py` scoped to active season

**Files:**
- Modify: `scripts/daily_nfl_sync.py` (`batch_upload`, `sync_nfl_data`)
- Test: `tests/test_daily_nfl_sync.py` (create if it doesn't exist — check first: `find tests -iname "*daily_nfl_sync*"`)

**Interfaces:**
- Produces: `batch_upload(db, collection_name, dataframe, id_col=None, diff_before_write=False) -> int` (new `diff_before_write` parameter; returns the count of documents actually written, so callers can decide whether to signal at all). `sync_nfl_data(seasons: tuple[int, int] = None)` (new optional argument — when omitted, scopes to the active season only; a `(min, max)` tuple forces the old full-range behavior for manual backfills).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_daily_nfl_sync.py
from unittest.mock import MagicMock
import pandas as pd
from scripts.daily_nfl_sync import batch_upload

def test_batch_upload_without_diff_writes_every_row():
    db = MagicMock()
    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 5}])
    written = batch_upload(db, "nfl_standings", df)
    assert written == 1
    db.batch.return_value.set.assert_called_once()

def test_batch_upload_with_diff_skips_unchanged_rows():
    db = MagicMock()
    existing_snap = MagicMock()
    existing_snap.exists = True
    existing_snap.id = "2026_KC"
    existing_snap.to_dict.return_value = {"season": 2026, "team": "KC", "wins": 5}
    db.get_all.return_value = [existing_snap]

    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 5}])  # identical to stored
    written = batch_upload(db, "nfl_standings", df, diff_before_write=True)

    assert written == 0
    db.batch.return_value.set.assert_not_called()

def test_batch_upload_with_diff_writes_only_changed_rows():
    db = MagicMock()
    existing_snap = MagicMock()
    existing_snap.exists = True
    existing_snap.id = "2026_KC"
    existing_snap.to_dict.return_value = {"season": 2026, "team": "KC", "wins": 5}
    db.get_all.return_value = [existing_snap]

    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 6}])  # wins changed 5 -> 6
    written = batch_upload(db, "nfl_standings", df, diff_before_write=True)

    assert written == 1
    db.batch.return_value.set.assert_called_once()

def test_batch_upload_with_diff_always_writes_rows_with_no_derivable_id():
    """A row batch_upload can't compute a stable doc_id for (no id_col, no
    season+team, no game_id) can't be diffed against anything -- always write it."""
    db = MagicMock()
    db.get_all.return_value = []
    df = pd.DataFrame([{"some_other_field": "x"}])
    written = batch_upload(db, "misc", df, diff_before_write=True)
    assert written == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_nfl_sync.py -v`
Expected: FAIL — `batch_upload()` has no `diff_before_write` parameter and always returns `None`, not a write count.

- [ ] **Step 3: Implement diffing in `batch_upload()`**

Replace the function body (`scripts/daily_nfl_sync.py:38-74`) with:

```python
def batch_upload(db, collection_name, dataframe, id_col=None, diff_before_write=False) -> int:
    """Upload a DataFrame to Firestore in batches of 400.

    When diff_before_write=True, reads the current stored value for every
    row that has a derivable, stable doc_id and skips the .set() call for
    any row whose value is already identical -- trades N writes for N reads
    when most rows are unchanged, which is the common case for a job that
    reruns the same mostly-final data on a fixed schedule.

    Returns the number of documents actually written.
    """
    print(f"Uploading {len(dataframe)} records to {collection_name}...")
    collection_ref = db.collection(collection_name)

    rows_with_ids = []
    for _, row in dataframe.iterrows():
        doc_data = row.dropna().to_dict()
        if id_col and id_col in doc_data:
            doc_id = str(doc_data[id_col])
        elif "season" in doc_data and "team" in doc_data:
            doc_id = f"{doc_data['season']}_{doc_data['team']}"
        elif "game_id" in doc_data:
            doc_id = str(doc_data["game_id"])
        else:
            doc_id = None
        rows_with_ids.append((doc_id, doc_data))

    if diff_before_write:
        diffable = [(doc_id, doc_data) for doc_id, doc_data in rows_with_ids if doc_id]
        existing = {}
        if diffable:
            refs = [collection_ref.document(doc_id) for doc_id, _ in diffable]
            for snap in db.get_all(refs):
                if snap.exists:
                    existing[snap.id] = snap.to_dict()
        filtered = []
        for doc_id, doc_data in rows_with_ids:
            if doc_id and existing.get(doc_id) == doc_data:
                continue  # unchanged -- skip the write
            filtered.append((doc_id, doc_data))
        rows_with_ids = filtered

    batch = db.batch()
    count = 0
    total_committed = 0
    for doc_id, doc_data in rows_with_ids:
        doc_ref = collection_ref.document(doc_id) if doc_id else collection_ref.document()
        batch.set(doc_ref, doc_data)
        count += 1
        if count == 400:
            batch.commit()
            total_committed += count
            print(f"  ...committed {total_committed} records")
            batch = db.batch()
            count = 0

    if count > 0:
        batch.commit()
        total_committed += count

    print(f"Successfully uploaded {collection_name}! ({total_committed} of {len(dataframe)} records written)")
    return total_committed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_daily_nfl_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Scope `sync_nfl_data()` to the active season by default**

Replace `sync_nfl_data()` (lines 135-154) with:

```python
def sync_nfl_data(seasons: tuple = None):
    """Sync nfl_games/nfl_standings to Firestore.

    seasons=None (the normal daily run): scopes to the active season only
    -- historical seasons don't change absent a manual correction, so the
    daily job has no routine reason to rewrite them. Pass an explicit
    (min, max) tuple to force a full-range backfill/correction.
    """
    print("Initializing Firebase...")
    db = initialize_firebase()

    print("Loading nflverse schedule from rawdata/schedules/games.csv...")
    df_games = load_games()
    print(f"  {len(df_games)} games loaded ({int(df_games['season'].min())}-{int(df_games['season'].max())})")

    if seasons is not None:
        lo, hi = seasons
        scoped_games = df_games[(df_games["season"] >= lo) & (df_games["season"] <= hi)].copy()
        print(f"  Scoped to explicit season range {lo}-{hi} ({len(scoped_games)} games)")
    else:
        active_season = int(df_games["season"].max())
        scoped_games = df_games[df_games["season"] == active_season].copy()
        print(f"  Scoped to active season {active_season} ({len(scoped_games)} games)")

    print("Computing standings from game results...")
    df_standings = compute_standings(scoped_games)

    standings_written = batch_upload(db, "nfl_standings", df_standings, diff_before_write=True)
    games_written = batch_upload(db, "nfl_games", scoped_games, diff_before_write=True)

    if standings_written or games_written:
        print("Signaling cache invalidation...")
        from services.db_service import signal_data_update
        from services.cache_service import DOMAIN_ACTIVE, DOMAIN_HISTORICAL
        domain = DOMAIN_HISTORICAL if seasons is not None and seasons[1] < int(df_games["season"].max()) else DOMAIN_ACTIVE
        signal_data_update(domain)
    else:
        print("No changes -- skipping cache invalidation signal.")

    print("Daily sync completed successfully!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", type=int, nargs=2, metavar=("MIN", "MAX"),
                         help="Force a full-range sync (manual backfill/correction) instead of active-season-only")
    args = parser.parse_args()
    sync_nfl_data(seasons=tuple(args.seasons) if args.seasons else None)
```

- [ ] **Step 6: Write a test for the active-season scoping**

```python
def test_sync_nfl_data_defaults_to_active_season_only(monkeypatch):
    from scripts import daily_nfl_sync
    import pandas as pd
    fake_games = pd.DataFrame([
        {"season": 2024, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    captured = {}
    def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
        captured[name] = df
        return 0
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", fake_batch_upload)

    daily_nfl_sync.sync_nfl_data()

    assert set(captured["nfl_games"]["season"].unique()) == {2026}
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_daily_nfl_sync.py -k "defaults_to_active_season_only" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/daily_nfl_sync.py tests/test_daily_nfl_sync.py
git commit -m "perf: diff-before-write in batch_upload(), scope daily sync to the active season by default"
```

---

## Task 9: Apply the same fix to `sync_live_scores.py` (the largest write driver)

**Files:**
- Modify: `scripts/sync_live_scores.py` (`sync_authoritative`, `main`)
- Test: `tests/test_sync_live_scores.py` (create if absent — check first)

**Interfaces:**
- Consumes: `batch_upload(..., diff_before_write=True)` (Task 8).

- [ ] **Step 1: Resolve the "current + prior season" open question**

Read `compute_standings()` (`scripts/daily_nfl_sync.py:88-132`) closely: it groups games by `(season, team)` independently per season, with no cross-season logic — nothing in `compute_standings()` itself requires prior-season game data to correctly compute the current season's standings. Check `sync_authoritative()`'s own comment (`scripts/sync_live_scores.py:12-14`, "filtered to the current + prior season only... the 5-minute cadence makes a full historical rewrite needlessly expensive") — this reads as an intermediate step down from "full history," not a deliberate "prior season needs live updates" decision, and no correction/finalization logic elsewhere in this script touches prior-season data specifically. **Conclusion: scope to the active season only**, matching `daily_nfl_sync.py`'s Task 8 change. If, after re-reading, you find an actual reason prior-season data needs touching every 5 minutes (e.g. a real late-scoring-correction case), stop and note it in this task's commit message instead of silently proceeding — but proceed with active-season-only scoping as the default unless you find one.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_sync_live_scores.py
from unittest.mock import MagicMock
import pandas as pd
from scripts.sync_live_scores import sync_authoritative

def test_sync_authoritative_scopes_standings_to_active_season_only(monkeypatch):
    from scripts import sync_live_scores
    fake_games = pd.DataFrame([
        {"season": 2025, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1",
         "gameday": "2025-09-01"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2",
         "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
    ])
    monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
    captured = {}
    def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
        captured[name] = df
        return 0
    monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

    sync_authoritative(MagicMock())

    assert set(captured["nfl_standings"]["season"].unique()) == {2026}

def test_sync_authoritative_uses_diff_before_write(monkeypatch):
    from scripts import sync_live_scores
    calls = []
    def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
        calls.append(diff_before_write)
        return 0
    monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)
    monkeypatch.setattr(sync_live_scores, "load_games", lambda: pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2",
         "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
    ]))

    sync_authoritative(MagicMock())

    assert all(calls)  # every batch_upload call in this function passes diff_before_write=True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: FAIL — `sync_authoritative()` currently filters to `current_season - 1` and calls `batch_upload()` with no `diff_before_write` argument.

- [ ] **Step 4: Update `sync_authoritative()`**

Replace lines 78-96 of `scripts/sync_live_scores.py`:

```python
def sync_authoritative(db) -> pd.DataFrame:
    """Part 1: re-pull rawdata, recompute standings for the active season,
    push nfl_games + nfl_standings, diffing against what's already stored."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py"), "--priority", "1"],
        capture_output=True, text=True, timeout=120,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"[warn] sync_nflverse_data.py --priority 1 exited non-zero (non-fatal): "
              f"{result.stderr.strip()[:500]}")

    games = load_games()
    current_season = games["season"].max()
    active_season_games = games[games["season"] == current_season].copy()

    standings = compute_standings(active_season_games)
    standings_written = batch_upload(db, "nfl_standings", standings, diff_before_write=True)

    # Narrow the nfl_games push to the trailing ~7 days by gameday -- these
    # are the only games that could plausibly be live or have just finished.
    gameday = pd.to_datetime(active_season_games["gameday"], errors="coerce")
    today = pd.Timestamp.now().normalize()
    window = active_season_games[(gameday >= today - pd.Timedelta(days=7)) & (gameday <= today)].copy()

    games_written = batch_upload(db, "nfl_games", window, diff_before_write=True)
    return window
```

(`standings_written`/`games_written` are computed but not yet used here — `main()` needs them to decide whether to signal, handled in Step 5.)

- [ ] **Step 5: Update `main()` to only signal when something changed, using the active domain**

Replace `sync_authoritative()`'s call site and the unconditional signal at the bottom of `main()`:

```python
def main():
    try:
        db = initialize_firebase()
        games = sync_authoritative(db)
    except (Exception, SystemExit):
        send_alert_email(
            "WinsPool job 'winspool-live-scores' failed",
            f"Authoritative sync failed:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)

    written = run_espn_overlay_safely(db, games)

    from services.cache_service import DOMAIN_ACTIVE
    from services.db_service import signal_data_update
    signal_data_update(DOMAIN_ACTIVE)  # ESPN overlay always writes is_live/clock/period
                                        # fields even when nflverse data didn't change,
                                        # so this signal fires every run regardless --
                                        # matches the existing comment above the old
                                        # unconditional write about placement timing.

    print(f"Live sync complete. ESPN overlay wrote {written} game(s).")
```

(Note: unlike `daily_nfl_sync.py`, this keeps the signal unconditional — the ESPN overlay step (`run_espn_overlay_safely`) does its own separate `.set(..., merge=True)` writes for `is_live`/`clock`/`period` on live games, which `sync_authoritative()`'s own `standings_written`/`games_written` counts don't capture, so gating the signal on those two counts alone would under-signal during an actual live game. Keeping this one always-fires is the correct, conservative choice — the diff-before-write savings on `nfl_standings`/`nfl_games` writes stand regardless of whether the signal itself fires every run.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/sync_live_scores.py tests/test_sync_live_scores.py
git commit -m "perf: scope winspool-live-scores to the active season and diff before writing"
```

---

## Task 10: Remove `analytics_cache`'s dead write path

**Files:**
- Modify: `scripts/cache_builder.py` (remove `write_cache()`/`is_cache_final()` calls for `wins_pool_standings`, `schedule_enriched`, and the other 3 analytics — confirmed 5 `write_cache()` call sites: lines ~310, ~380, ~395, ~410, ~450)
- Modify: `services/cache_service.py` (remove `get_cached`, `write_cache`, `is_cache_final`, `_local_path`)
- Modify: `tests/test_cache_service.py` (remove the now-dead tests for these three functions)
- Modify: `tests/inspect_cache.py` (references `get_cached` — check whether this script has any other purpose; if not, delete it)
- Test: confirm `/wins-pool/{year}` and `/api/schedule` still render correctly

**Interfaces:**
- Consumes: nothing from other tasks — this is independent cleanup, safe to do at any point in this plan, placed last since it's lowest-risk to defer.

- [ ] **Step 1: Confirm zero remaining production callers before deleting anything**

```bash
grep -rn "get_cached(\|write_cache(\|is_cache_final(" --include="*.py" . | grep -v "\.venv\|test_\|inspect_cache"
```

Expected: only `services/cache_service.py`'s own definitions and `scripts/cache_builder.py`'s 5+ call sites remain — confirm this matches the investigation's earlier finding before proceeding (if a new caller appeared since, stop and reassess; this task assumes the investigation's grep still holds).

- [ ] **Step 2: Remove the `analytics_cache` writes from `cache_builder.py`**

Read each of the 5 `write_cache(...)`/`is_cache_final(...)` call sites in full context first (`sed -n '295,455p' scripts/cache_builder.py`). For the `wins_pool_standings` block (around line 303-313):

```python
    # --- Wins Pool Standings ---
    df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year)
    print(f"  [ok]   wins_pool_standings year={year} computed (analytics_cache write removed -- "
          f"nothing reads it back; see docs/superpowers/specs/2026-09-14-cache-mutability-redesign-design.md SS6)")
```

Remove the `if not force and is_cache_final(...)` skip-check and the `write_cache(...)` call, keeping only the actual computation this block needs for its *other* side effects (if `df` feeds anything besides the dead `analytics_cache` write — check whether `df` is used anywhere after this block in the function; if it's genuinely only used for the `write_cache()` call, the whole computation can be removed too, not just the write — confirm by reading the surrounding ~15 lines before deciding, since removing a computation that turns out to feed something else would be a real regression).

Apply the same pattern to the `schedule_enriched` block (line ~316-380) — **but keep every part of this block that isn't the `analytics_cache` write**: it also writes to `game_predictions` (confirmed genuinely read by `/api/schedule`), so only the specific `write_cache(analytic, ...)`/`is_cache_final(analytic, ...)` lines targeting `'schedule_enriched'` come out, not the surrounding prediction-application/`game_predictions`-write logic.

Repeat for the remaining 3 `write_cache()` call sites (lines ~395, ~410, ~450) — read each one's surrounding context the same way before removing, since this plan can't enumerate exactly what (if anything) each one also feeds without reading the live file.

- [ ] **Step 3: Remove the now-fully-dead functions from `cache_service.py`**

Delete `_local_path` (lines 21-23), `get_cached` (lines 26-56), `write_cache` (lines 59-86), `is_cache_final` (lines 89-110) from `services/cache_service.py`.

- [ ] **Step 4: Remove the now-dead tests**

In `tests/test_cache_service.py`, remove `test_local_cache_read_write`, `test_remote_firestore_cache_read` (both test `get_cached`/`write_cache`, now deleted).

- [ ] **Step 5: Handle `tests/inspect_cache.py`**

Read this file (`cat tests/inspect_cache.py`) — if its only purpose is manually inspecting `get_cached()`'s output (per the one call site found earlier, `get_cached('prediction_snapshot', 2026, 0)`), delete it. If it does anything else, remove only the `get_cached` usage.

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -v`
Expected: PASS. Every removed function's test is gone (Step 4), and no remaining test references `get_cached`/`write_cache`/`is_cache_final`/`_local_path`.

- [ ] **Step 7: Manually verify the serving path still works**

Since this plan cannot execute a live server, name the concrete manual check for whoever runs this plan: start the app locally (`uvicorn main:app --reload` per this repo's standard dev workflow) and load `/wins-pool/{a real season year}` and `/api/schedule`(or the schedule page) directly, confirming both render real standings/schedule data with no server error — these are the two pages whose data comes from the computations this task stopped writing to `analytics_cache`, and they must still work purely from `analysis_service.py`'s on-the-fly computation over `load_data()`'s bundle, unaffected by this removal.

- [ ] **Step 8: Commit**

```bash
git add scripts/cache_builder.py services/cache_service.py tests/test_cache_service.py tests/inspect_cache.py
git commit -m "cleanup: remove analytics_cache's dead write path (zero production readers)"
```

---

## Self-Review Notes

- **Spec coverage:** Design §1 (three buckets) → Task 3. §2 (scoped signal) → Tasks 1-2. §3 (predictions) → Task 6. §4 (admin analytics) → Task 7. §5 (write-side, both jobs) → Tasks 8-9. §6 (analytics_cache removal) → Task 10. The original staleness bug + cross-instance signal gap → Task 4. The 3 bypass-read reroutes → Task 5. Every item in the spec's "Explicitly out of scope" section has no corresponding task, matching the spec's own scoping.
- **Open question resolved, not left dangling:** the spec flagged "why does sync_live_scores.py write current+prior season" as unresolved; Task 9 Step 1 requires the implementer to actually re-derive the answer from `compute_standings()`'s real logic (which has no cross-season dependency) and defaults to active-only scoping unless a real reason turns up — this plan does not silently inherit the spec's open question, it forces a decision.
- **Type/interface consistency checked:** `batch_upload()`'s new `diff_before_write` parameter and return type (int, not None) are used identically in both Task 8 (`daily_nfl_sync.py`) and Task 9 (`sync_live_scores.py`, which imports the same function). `DOMAIN_*`/`DOMAIN_SIGNAL_FIELDS` are defined exactly once (Task 1) and only ever imported, never re-declared, in every later task. `_get_static_bucket()`/`_get_active_bucket()` are defined once (Task 3) and consumed by name in Tasks 5-7 without redefinition.
- **Existing-test migration is an explicit, scoped step** (Task 1 Step 4, Task 4 Step 5, Task 7 Step 4) rather than an afterthought — the 3 test files confirmed to reference the old year-keyed internals are named explicitly, not left for a reviewer to discover via a failing CI run.
- **No placeholder scan:** every step above contains real code read from or directly derived from this repo's actual current source (not invented signatures) — the two spots where this plan explicitly tells the implementer to "read the real file before assuming" (Task 5 Step 1's mocking shape, Task 10 Step 2's per-block dependency check) are flagged as such because the exact answer depends on file content this plan's author could not fully enumerate without risking a stale line-number reference; both give a concrete fallback action, not an open-ended "figure it out."
