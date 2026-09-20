# Scheduled Jobs Hardening Follow-Ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix a real, pre-existing team-abbreviation-matching bug in `services/live_score_service.py`, and close five specific test-coverage gaps left open by the scheduled-jobs plan's final review.

**Architecture:** One straightforward bug fix (normalize the correct side of a dict-key match) plus targeted unit tests added to existing test files (or one new file per untested module), each exercising a single named gap from the spec. No new abstractions, no refactoring of production code beyond the one bug fix.

**Tech Stack:** Python, pytest, `unittest.mock` (`patch`/`MagicMock`/`monkeypatch`) — this codebase never spins up a real or emulated Firestore/ESPN endpoint in tests.

**Spec:** `docs/superpowers/specs/2026-08-20-scheduled-jobs-hardening-followups.md`

## Global Constraints

- No test in this plan may hit the network or a real Firestore/ESPN instance.
- Follow each target file's own established test-mocking style exactly (`tests/test_sync_live_scores.py` uses `monkeypatch.setattr(module, "name", ...)` for module-level function replacement and `patch("scripts.module.name", ...)` for context-managed patches — match whichever the surrounding tests in that file already use, don't introduce a third style).
- `services/live_score_service.py::sync_live_scores_to_df()` is a shared function with its own established callers (`scripts/cache_builder.py`) — this plan only fixes the abbreviation-matching bug, no other behavior change (per the spec's own scoping: "the fix is straightforward: normalize the ESPN-sourced keys, not the repo-sourced ones").
- Do not remove or replace `sync_live_scores_to_df()` — the spec raises but does not resolve the open question of whether it's still needed; that decision is explicitly out of scope here (see spec's "Questions for whoever picks this up").
- Run `pytest tests/ -n auto` after every task and confirm 0 regressions before moving to the next task. This suite has known, pre-existing, order-dependent flakes under parallel execution (different unrelated tests fail on different runs, always passing in isolation and on immediate re-run) — if a failure looks unrelated to the file(s) a task touched, re-run that specific test in isolation before treating it as a regression.

---

## Task 1: Fix the team-abbreviation bug in `services/live_score_service.py::sync_live_scores_to_df()`

**Files:**
- Modify: `services/live_score_service.py:81-138` (`sync_live_scores_to_df`)
- Test: Create `tests/test_live_score_service.py`

**Interfaces:**
- Consumes: `services.utils.normalize_team_abbr` (already imported in this file), `get_live_updates()` (already defined in this file, returns `{(home_abbr, away_abbr): {home_score, away_score, status, clock, period, possession}}` with **raw, un-normalized ESPN abbreviations as keys** — confirmed by reading `get_live_updates()`'s own body, which builds `abbr = team_data.get('team', {}).get('abbreviation')` with no normalization).
- Produces: no new interfaces — `sync_live_scores_to_df(games_df: pd.DataFrame) -> pd.DataFrame` keeps its existing signature and return shape; only its internal matching logic changes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_live_score_service.py`:

```python
"""services/live_score_service.py::sync_live_scores_to_df() -- team
abbreviation matching. ESPN returns raw abbreviations (LAR/WSH/JAC) that
differ from nflverse's (LA/WAS/JAX, the repo's own canonical form) -- see
services/utils.py::normalize_team_abbr(). The repo side of games_df is
already in canonical form; only the ESPN side needs normalizing before the
two are compared."""
import pandas as pd

import services.live_score_service as lss


def _repo_game(home, away):
    return pd.DataFrame([
        {"home_team": home, "away_team": away, "home_score": 0, "away_score": 0, "result": None},
    ])


def test_rams_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'LA' must match ESPN's raw 'LAR'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("LAR", "SF"): {"home_score": 20, "away_score": 17, "status": "STATUS_IN_PROGRESS",
                        "clock": "5:23", "period": 3},
    })

    result = lss.sync_live_scores_to_df(_repo_game("LA", "SF"))

    assert result.at[0, "home_score"] == 20
    assert result.at[0, "away_score"] == 17
    assert result.at[0, "is_live"] == True


def test_commanders_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'WAS' must match ESPN's raw 'WSH'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("WSH", "DAL"): {"home_score": 14, "away_score": 10, "status": "STATUS_IN_PROGRESS",
                        "clock": "2:00", "period": 2},
    })

    result = lss.sync_live_scores_to_df(_repo_game("WAS", "DAL"))

    assert result.at[0, "home_score"] == 14
    assert result.at[0, "away_score"] == 10
    assert result.at[0, "is_live"] == True


def test_jaguars_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'JAX' must match ESPN's raw 'JAC'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("JAC", "TEN"): {"home_score": 7, "away_score": 3, "status": "STATUS_IN_PROGRESS",
                        "clock": "10:00", "period": 1},
    })

    result = lss.sync_live_scores_to_df(_repo_game("JAX", "TEN"))

    assert result.at[0, "home_score"] == 7
    assert result.at[0, "away_score"] == 3
    assert result.at[0, "is_live"] == True


def test_already_matching_abbreviations_still_work(monkeypatch):
    """Regression guard: a team whose abbreviation needs no normalization
    (e.g. KC) must keep matching exactly as before this fix."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 24, "away_score": 21, "status": "STATUS_IN_PROGRESS",
                        "clock": "1:00", "period": 4},
    })

    result = lss.sync_live_scores_to_df(_repo_game("KC", "BUF"))

    assert result.at[0, "home_score"] == 24
    assert result.at[0, "away_score"] == 21


def test_no_matching_game_leaves_scores_unchanged(monkeypatch):
    """A game ESPN doesn't report on at all must be left untouched."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 24, "away_score": 21, "status": "STATUS_IN_PROGRESS",
                        "clock": "1:00", "period": 4},
    })

    result = lss.sync_live_scores_to_df(_repo_game("LA", "SF"))

    assert result.at[0, "home_score"] == 0
    assert result.at[0, "away_score"] == 0


def test_empty_games_df_returns_unchanged():
    result = lss.sync_live_scores_to_df(pd.DataFrame())
    assert result.empty


def test_no_live_data_returns_games_df_unchanged(monkeypatch):
    monkeypatch.setattr(lss, "get_live_updates", lambda: {})
    games = _repo_game("LA", "SF")
    result = lss.sync_live_scores_to_df(games)
    pd.testing.assert_frame_equal(result, games)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_live_score_service.py -v`
Expected: `test_rams_game_matches_after_espn_key_normalization`, `test_commanders_game_matches_after_espn_key_normalization`, and `test_jaguars_game_matches_after_espn_key_normalization` FAIL (scores stay `0`, `is_live` is never set) because the current code normalizes the repo side (a no-op for already-canonical abbreviations) instead of the ESPN side. The other four tests PASS already (they don't depend on the buggy normalization direction).

- [ ] **Step 3: Fix `sync_live_scores_to_df()`**

In `services/live_score_service.py`, use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
def sync_live_scores_to_df(games_df: pd.DataFrame) -> pd.DataFrame:
    """
    Overlays live ESPN data onto the provided games DataFrame.
    Only updates games that are NOT yet final in the source DF, 
    or games that ESPN says are currently active.
    """
    if games_df.empty:
        return games_df
        
    live_data = get_live_updates()
    if not live_data:
        return games_df
        
    df = games_df.copy()
    cache_needs_rebuild = False
    
    for idx, row in df.iterrows():
        # Only check games for the current/active week (or all for simplicity in V1)
        # Try to find match
        
        # Normalize repo teams to check against ESPN (usually they match, but be safe)
        home_norm = normalize_team_abbr(row['home_team'])
        away_norm = normalize_team_abbr(row['away_team'])
        match_key = (home_norm, away_norm)
            
        if match_key in live_data:
            update = live_data[match_key]
```

new_string:
```python
def sync_live_scores_to_df(games_df: pd.DataFrame) -> pd.DataFrame:
    """
    Overlays live ESPN data onto the provided games DataFrame.
    Only updates games that are NOT yet final in the source DF, 
    or games that ESPN says are currently active.
    """
    if games_df.empty:
        return games_df
        
    live_data = get_live_updates()
    if not live_data:
        return games_df

    # ESPN returns raw abbreviations (LAR/WSH/JAC) that differ from
    # nflverse's (LA/WAS/JAX) -- normalize the ESPN side before matching
    # against nflverse-normalized home_team/away_team keys, or Rams/
    # Commanders/Jaguars games silently never match (see
    # services/utils.py::normalize_team_abbr and the same pattern in
    # scripts/sync_live_scores.py::run_espn_overlay_safely()). The repo
    # side (row['home_team']/row['away_team']) is already canonical and
    # needs no normalization.
    normalized_live_data = {
        (normalize_team_abbr(h), normalize_team_abbr(a)): v
        for (h, a), v in live_data.items()
    }

    df = games_df.copy()
    cache_needs_rebuild = False
    
    for idx, row in df.iterrows():
        match_key = (row['home_team'], row['away_team'])
            
        if match_key in normalized_live_data:
            update = normalized_live_data[match_key]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_live_score_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add services/live_score_service.py tests/test_live_score_service.py
git commit -m "fix: normalize ESPN's team abbreviations, not the repo's, in sync_live_scores_to_df"
```

---

## Task 2: Branch-logic tests for `scripts/daily_nfl_sync.py::initialize_firebase()`

**Files:**
- Test: `tests/test_daily_nfl_sync.py`

**Interfaces:**
- Consumes: `scripts.daily_nfl_sync.initialize_firebase` (existing, no changes), `scripts.daily_nfl_sync.firebase_admin`/`credentials`/`firestore` (module-level imports already present in that file, at lines 6-7).
- Produces: no new interfaces — test-only task.

This closes the spec's gap in substance: `scripts/cache_builder.py` calls this exact function at *module import time* (`from scripts.daily_nfl_sync import initialize_firebase; initialize_firebase()` at `scripts/cache_builder.py:36-37`), which makes testing that specific call site directly impractical (importing `scripts.cache_builder` in a test would trigger a real Firebase connection attempt or `sys.exit(1)` before any mocking could apply). Testing `initialize_firebase()`'s own branch logic directly — which is the actual behavior in question — covers the real risk.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_daily_nfl_sync.py` (append after the existing tests):

```python
class TestInitializeFirebase:
    """initialize_firebase() has 4 branches: already-initialized shortcut,
    FIREBASE_CREDENTIALS env var (how Cloud Run passes credentials), local
    firebase_credentials.json fallback (dev machines), and sys.exit(1) when
    neither is available. None of the existing tests exercise this function
    directly -- every other test in this file monkeypatches it away entirely."""

    def test_already_initialized_returns_client_without_reinitializing(self, monkeypatch):
        import scripts.daily_nfl_sync as daily_nfl_sync
        monkeypatch.setattr(daily_nfl_sync.firebase_admin, "_apps", {"[DEFAULT]": object()})
        sentinel_client = object()
        with patch.object(daily_nfl_sync.firestore, "client", return_value=sentinel_client), \
             patch.object(daily_nfl_sync.firebase_admin, "initialize_app") as mock_init:
            result = daily_nfl_sync.initialize_firebase()

        assert result is sentinel_client
        mock_init.assert_not_called()

    def test_uses_env_var_credentials_when_present(self, monkeypatch):
        import base64
        import scripts.daily_nfl_sync as daily_nfl_sync
        monkeypatch.setattr(daily_nfl_sync.firebase_admin, "_apps", {})
        monkeypatch.setenv("FIREBASE_CREDENTIALS", base64.b64encode(b'{"project_id": "test"}').decode())
        sentinel_client = object()

        with patch.object(daily_nfl_sync.credentials, "Certificate") as mock_cert, \
             patch.object(daily_nfl_sync.firebase_admin, "initialize_app") as mock_init, \
             patch.object(daily_nfl_sync.firestore, "client", return_value=sentinel_client):
            result = daily_nfl_sync.initialize_firebase()

        assert result is sentinel_client
        mock_init.assert_called_once()
        mock_cert.assert_called_once()
        # The temp file Certificate() was pointed at must actually contain
        # the decoded credentials JSON.
        written_path = mock_cert.call_args[0][0]
        with open(written_path) as f:
            assert f.read() == '{"project_id": "test"}'

    def test_falls_back_to_local_file_when_no_env_var(self, monkeypatch):
        import scripts.daily_nfl_sync as daily_nfl_sync
        monkeypatch.setattr(daily_nfl_sync.firebase_admin, "_apps", {})
        monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
        sentinel_client = object()

        with patch("pathlib.Path.exists", return_value=True), \
             patch.object(daily_nfl_sync.credentials, "Certificate") as mock_cert, \
             patch.object(daily_nfl_sync.firebase_admin, "initialize_app") as mock_init, \
             patch.object(daily_nfl_sync.firestore, "client", return_value=sentinel_client):
            result = daily_nfl_sync.initialize_firebase()

        assert result is sentinel_client
        mock_init.assert_called_once()
        mock_cert.assert_called_once()
        assert mock_cert.call_args[0][0].endswith("firebase_credentials.json")

    def test_exits_when_no_env_var_and_no_local_file(self, monkeypatch):
        import scripts.daily_nfl_sync as daily_nfl_sync
        monkeypatch.setattr(daily_nfl_sync.firebase_admin, "_apps", {})
        monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)

        with patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                daily_nfl_sync.initialize_firebase()

        assert exc_info.value.code == 1
```

Add the needed imports at the top of `tests/test_daily_nfl_sync.py` if not already present — check the current top-of-file imports first (`from unittest.mock import MagicMock` is already there per the existing file); add `patch` to that import line and add `import pytest` if it's not already imported.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_daily_nfl_sync.py::TestInitializeFirebase -v`
Expected: FAIL — `patch` and/or `pytest` not imported yet (or, once those imports are added first, this step should instead show the tests passing already, since `initialize_firebase()` itself is unchanged and correct; this task adds coverage, it does not fix a bug). If all 4 tests pass immediately after adding the imports, that is the expected, correct outcome for this task — proceed to Step 3's verification instead of expecting a red step.

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_daily_nfl_sync.py -v`
Expected: PASS (all tests in the file, old and new — 4 new tests added)

- [ ] **Step 4: Commit**

```bash
git add tests/test_daily_nfl_sync.py
git commit -m "test: cover initialize_firebase()'s env-var/local-file/already-initialized branches"
```

---

## Task 3: Boundary tests for `scripts/run_cron.py`'s `CURRENT_SEASON` and its STEPS wiring

**Files:**
- Test: Create `tests/test_run_cron.py`

**Interfaces:**
- Consumes: `scripts.run_cron.CURRENT_SEASON` (module-level constant, recomputed on `importlib.reload()`), `scripts.run_cron.STEPS` (module-level list).
- Produces: no new interfaces — test-only task.

`CURRENT_SEASON = _today.year if _today.month >= 9 else _today.year - 1` is computed once at module-import time from the real `datetime.date.today()`. To test both sides of the September 1st boundary without adding a new dependency (this codebase has no `freezegun`/`time-machine` installed, and adding one for a single test is disproportionate), patch the `datetime.date` class itself (not `scripts.run_cron.date`, which would be re-bound to the real class by `run_cron.py`'s own `from datetime import date` statement every time the module reloads) and force a reload so the module-level constant recomputes against the patched `.today()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_run_cron.py`:

```python
"""scripts/run_cron.py -- CURRENT_SEASON's September 1st rollover boundary,
and its wiring into the "Elo Recompute + Firestore Push" step's args.

CURRENT_SEASON is computed once at module-import time from datetime.date.today().
To control that value in a test we patch datetime.date itself (not
scripts.run_cron.date, which `from datetime import date` would re-bind to the
real class on every reload) and force a reload so the constant recomputes."""
import importlib
from datetime import date
from unittest.mock import patch

import scripts.run_cron as run_cron_mod


def _reload_with_frozen_today(frozen_date):
    with patch("datetime.date") as mock_date_cls:
        mock_date_cls.today.return_value = frozen_date
        importlib.reload(run_cron_mod)
    return run_cron_mod


def test_current_season_is_prior_year_in_august():
    """Aug 31 is still last season -- the new season hasn't started yet."""
    try:
        mod = _reload_with_frozen_today(date(2026, 8, 31))
        assert mod.CURRENT_SEASON == 2025
    finally:
        importlib.reload(run_cron_mod)  # restore real date.today() for later tests


def test_current_season_rolls_over_on_september_first():
    """Sept 1 is the first day of the new season."""
    try:
        mod = _reload_with_frozen_today(date(2026, 9, 1))
        assert mod.CURRENT_SEASON == 2026
    finally:
        importlib.reload(run_cron_mod)


def test_current_season_stays_same_year_through_january():
    """A season that started in Sept 2026 is still "2026" the following January."""
    try:
        mod = _reload_with_frozen_today(date(2027, 1, 15))
        assert mod.CURRENT_SEASON == 2026
    finally:
        importlib.reload(run_cron_mod)


def test_elo_step_args_use_the_computed_current_season():
    """CURRENT_SEASON must actually be threaded through to the subprocess
    args job_runner.run_steps() passes to compute_elo.py -- not just exist
    as an unused module-level constant."""
    try:
        mod = _reload_with_frozen_today(date(2026, 9, 1))
        by_name = {s["name"]: s for s in mod.STEPS}
        assert by_name["Elo Recompute + Firestore Push"]["args"] == \
            ["--firestore", "--max-season", "2026"]
    finally:
        importlib.reload(run_cron_mod)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_cron.py -v`
Expected: If the `datetime.date` patch/reload approach doesn't work as designed (e.g. the patch doesn't survive the reload, or `CURRENT_SEASON` doesn't change), these tests FAIL with an assertion mismatch showing the real current date's season instead of the frozen one. If they pass immediately, verify by temporarily changing one frozen date to an obviously wrong expected value (e.g. assert `mod.CURRENT_SEASON == 1999`) and confirming that fails — this proves the test actually exercises the frozen date rather than passing vacuously — then revert to the correct expected value.

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_run_cron.py -v`
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add tests/test_run_cron.py
git commit -m "test: cover run_cron.py's CURRENT_SEASON Sept 1 boundary and STEPS wiring"
```

---

## Task 4: Boundary tests for `scripts/sync_live_scores.py`'s 7-day `gameday` window filter

**Files:**
- Test: `tests/test_sync_live_scores.py`

**Interfaces:**
- Consumes: `scripts.sync_live_scores.sync_authoritative` (existing function, no changes), `scripts.sync_live_scores.load_games`/`batch_upload` (existing module-level names already monkeypatched by sibling tests in this file, per `TestSyncAuthoritativeScoping`).
- Produces: no new interfaces — test-only task.

The window filter is `active_season_games[(gameday >= today - pd.Timedelta(days=7)) & (gameday <= today)]` (`scripts/sync_live_scores.py:93`) — inclusive on both ends. `TestSyncAuthoritativeScoping::test_still_narrows_games_push_to_trailing_week` already exists and covers a 30-days-old (excluded) vs. today (included) case; this task adds the two boundary cases the spec calls out that aren't covered: a game exactly 7 days old (must be included, since the comparison is `>=`), and a game with an unparseable/missing `gameday` (must be excluded, not crash `pd.to_datetime(..., errors="coerce")` which turns it into `NaT`, and `NaT` compared with `>=`/`<=` is always `False`, correctly excluding it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_sync_live_scores.py`, inside the existing `TestSyncAuthoritativeScoping` class (add these as new methods on that class, using the same `self._patch_subprocess_ok()` helper already defined there):

```python
    def test_game_exactly_seven_days_old_is_included(self, monkeypatch):
        """The filter is >= today - 7 days, so a game exactly 7 days old is
        the oldest game still included, not the newest game excluded."""
        from scripts import sync_live_scores
        exactly_seven_days_ago = (pd.Timestamp.now().normalize() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "boundary",
             "gameday": exactly_seven_days_ago},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert list(captured["nfl_games"]["game_id"]) == ["boundary"]

    def test_game_eight_days_old_is_excluded(self, monkeypatch):
        """One day past the boundary must be excluded -- confirms the window
        actually has an edge, not just an off-by-a-lot margin."""
        from scripts import sync_live_scores
        eight_days_ago = (pd.Timestamp.now().normalize() - pd.Timedelta(days=8)).strftime("%Y-%m-%d")
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "too_old",
             "gameday": eight_days_ago},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert list(captured["nfl_games"]["game_id"]) == []

    def test_malformed_gameday_is_excluded_not_crashed_on(self, monkeypatch):
        """A game with an unparseable/missing gameday must be silently
        excluded from the trailing-week push, never raise or crash the sync."""
        from scripts import sync_live_scores
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "malformed",
             "gameday": "not-a-real-date"},
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "result": None, "home_score": None, "away_score": None, "game_id": "good",
             "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            result = sync_authoritative(MagicMock())  # must not raise

        assert list(captured["nfl_games"]["game_id"]) == ["good"]
        assert list(result["game_id"]) == ["good"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_live_scores.py -k "seven_days or eight_days or malformed_gameday" -v`
Expected: These specific tests should already PASS if the underlying filter logic is correct (this task adds coverage; it doesn't fix a bug in this file). If `test_game_exactly_seven_days_old_is_included` fails, that reveals the boundary is actually exclusive (`>` not `>=`) in current code, or a timezone/normalization mismatch — investigate `scripts/sync_live_scores.py:91-93`'s exact comparison operators before changing anything, since the spec frames this as a coverage gap, not a known bug.

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: PASS (all tests in the file, old and new — 3 new tests added)

- [ ] **Step 4: Commit**

```bash
git add tests/test_sync_live_scores.py
git commit -m "test: cover sync_live_scores.py's 7-day gameday window boundary and malformed-date handling"
```

---

## Task 5: Call-order test for `scripts/sync_live_scores.py::main()`'s cache invalidation

**Files:**
- Test: `tests/test_sync_live_scores.py`

**Interfaces:**
- Consumes: `scripts.sync_live_scores.main`, `scripts.sync_live_scores.run_espn_overlay_safely`, `services.db_service.signal_data_update` (all existing, no changes).
- Produces: no new interfaces — test-only task.

`main()` (`scripts/sync_live_scores.py:156-200`) calls `run_espn_overlay_safely(db, games)` (line 181) and then `signal_data_update(DOMAIN_ACTIVE)` (line 198) — deliberately in that order, per the comment at lines 191-195: signaling before the overlay would let a page request rebuild the cache from `nfl_games` docs that were just overwritten without `is_live`/`clock`/`period`, caching away the LIVE badge for a full 5-minute cycle. `TestMainSignaling::test_signals_active_domain_after_overlay` (already in this file) confirms `signal_data_update` is called, but not that it happens *after* the overlay — a regression that swapped the two calls would still pass that existing test.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_sync_live_scores.py`, inside the existing `TestMainSignaling` class:

```python
    def test_overlay_runs_before_cache_signal_not_after(self):
        """Regression guard for the ordering itself, not just that both
        calls happen -- a swap would still pass test_signals_active_domain_after_overlay
        above, since that test only checks signal_data_update was called at
        all, not when."""
        call_order = []
        with patch("scripts.sync_live_scores.initialize_firebase", return_value=MagicMock()), \
             patch("scripts.sync_live_scores.sync_authoritative", return_value=_games_df()), \
             patch("scripts.sync_live_scores.run_espn_overlay_safely",
                   side_effect=lambda *a, **k: call_order.append("overlay") or 0), \
             patch("services.db_service.signal_data_update",
                   side_effect=lambda *a, **k: call_order.append("signal")):
            from scripts.sync_live_scores import main
            main()

        assert call_order == ["overlay", "signal"]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_sync_live_scores.py::TestMainSignaling -v`
Expected: PASS immediately (this task adds coverage for correct existing behavior — if it fails, `main()`'s call order has regressed and must be fixed to match the comment at `scripts/sync_live_scores.py:191-195` before proceeding).

- [ ] **Step 3: Run the full file to confirm no regressions**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 4: Commit**

```bash
git add tests/test_sync_live_scores.py
git commit -m "test: assert sync_live_scores.py signals the cache only after the ESPN overlay runs"
```

---

## Self-Review Notes

**Spec coverage:**
- Section 1 (the abbreviation bug) → Task 1: fixed and tested, without removing/replacing the function per the spec's own scoping.
- Section 2's four "still open" gaps → Tasks 2 (Firebase-init branches), 3 (`--max-season` boundary + wiring), 4 (7-day window boundary), 5 (cache-invalidation ordering) — one task per gap.
- Non-goals (not re-opening the already-closed `oidc_token`/`AlreadyExists` items, not a general coverage audit) — nothing in this plan touches those.

**Placeholder scan:** No TBD/TODO markers; every step has runnable code and a concrete expected outcome, including the two tasks (2, 5) where the expected outcome is "already passes, this task is pure coverage" rather than a red-to-green TDD cycle — that distinction is stated explicitly in each of those tasks' Step 2 rather than left implicit.

**Type consistency:** All five tasks are test-only or a single self-contained bug fix; no shared interfaces are introduced across tasks, so there is no cross-task signature-drift risk of the kind Track 1's plan had to manage. Task 1's fix changes no public signature.
