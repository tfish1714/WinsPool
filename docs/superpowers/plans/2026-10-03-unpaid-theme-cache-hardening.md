# Unpaid Visibility, Theme Toggle, Cache Signals and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship gated unpaid-entry visibility (issue #87), a persisted light/dark theme (issue #107), cross-instance cache signals for draft writers (issue #71), the admin Pool tab default season, and four small tech-debt items.

**Architecture:** All changes are additive. Unpaid visibility adds pure functions to `services/pool_service.py`, one new gated endpoint beside `/pool/status` (which is not touched), and a new client script. The theme is a CSS token override block plus a tiny classic head script (a deferred module cannot prevent a flash) and a module-level toggle wiring in `main.js`. Cache, admin-season and tech-debt items are independent small TDD tasks.

**Tech Stack:** FastAPI, pandas, Firestore/pickle (`local_db_dir()`), vanilla ES6 JS, Jinja2, pytest (plus node vm for JS behavior, as in `tests/test_auth_guard.py`).

**Specs:** `docs/superpowers/specs/2026-09-27-unpaid-visibility.md`, `docs/superpowers/specs/2026-09-27-offseason-hardening-backlog.md` (B1, B4), `docs/superpowers/specs/2026-09-26-consolidated-followups-and-tech-debt.md` (4.4, 5.4, 5.7, 5.8), `docs/frontend.md`.

## Global Constraints

- No emojis in code, comments, commit messages or docs.
- Zero deletion: no existing feature, endpoint or test is removed. A test may only be replaced by an exact drop-in equivalent (Task 3, item 5.8, is the only such case).
- Write the failing test first and watch it fail for the right reason before any production code.
- Any test or code touching `.local_db` must use `services/local_paths.py::local_db_dir()`. Tests must not write to the real `.local_db/`; `tests/test_local_db_isolation.py` must stay green.
- `GET /api/pool/status` and its pinned key-set test (`tests/test_pool_service.py::TestPoolStatusRoute`) must not change.
- Commit trailer on every commit: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
- Sprint branch: `sprint-unpaid-theme-cache-hardening` (created; baseline `pytest tests/test_local_db_isolation.py -q` = 13 passed).
- Before finishing UI tasks, check desktop (1280px) and mobile (390px) widths; a nav link change would need both `main.js` `updateNav()` and `base.html` drawer (this plan adds buttons, not links).

## Deviations From The Task Text (verified against the code, 2026-10-03)

1. **Item 4.4 file:** `services/player_profile_service.py` and `tests/test_player_profile_service.py` do not exist. `compute_preseason_player_profiles` lives in `services/nn_feature_engine.py:1294`. The guard goes there; the new test file keeps the requested name `tests/test_player_profile_service.py`.
2. **Current week:** `get_latest_season_and_week(games)` returns the schedule maximum (2026, 18) while only week 3 has results, so using it would put the pool straight into the `banner` stage. A thin `current_played_week(games, season)` wrapper over the existing `data_service.get_most_recent_completed_week` is used instead (no new week logic).
3. **Task 2.2:** `get_active_season` takes `(games, draft_results, rules)`, not no arguments. `fetch_admin_seasons` already loads those frames.
4. **Task 2.1 item 3:** `set_member_paid` already calls both `clear_data_cache` and `signal_data_update`. It is switched to `_invalidate_static()` (behavior identical) and pinned by a test.
5. **Task 1.2 theme init:** `main.js` is a deferred module, so it cannot run before first paint. Init lives in a classic `static/js/theme_init.js` loaded in `<head>`; `main.js` imports `STORAGE_KEYS.THEME` and wires toggle buttons and cross-tab sync.
6. **Item 5.7:** draft results and standings both use `OAK` for 2017-2019 today, so the join works; the risk is one side being normalized. The join key is normalized on both sides in `calculate_wins_pool_standings`, leaving the displayed `team` untouched.
7. **Not in scope (stated in the unpaid spec as optional or outside the task list):** R5 nudge emails/push, and the pool-chip text change to "N of M paid".
8. **`docs/frontend.md` token table is stale** (lists `--accent`, `--text-primary` values that no longer match `static/style.css`); Task 9 corrects it.
9. **Known behavior, left unchanged:** `auth_guard.js` sign-out calls `localStorage.clear()`, which also clears the saved theme; the theme then falls back to the system preference.

## Review Focus

- Unpaid list leak: non-admin in `off`/`nudge` stage, or `enabled: false` at a late week, must get `unpaid: []` (tested in Task 5).
- Season with no `draft_order` rows, or a `paid` column missing or NaN: must not 500; NaN counts as unpaid; empty list.
- Caller not a member of the season: `me_unpaid` is `null`, never an error (Task 5).
- Invalid or out-of-order stored week settings: fall back to defaults with `enabled: false`, never raise (Task 4).
- Browser storage unavailable (private window) or an invalid stored theme value: page still renders with the system theme (Task 8).
- `/api/admin/seasons` active season absent from the list, or `get_active_season` failing: tab falls back to the first season (Task 2).
- Weekly roster file without a `week` column: `{}` rather than `KeyError` (Task 3).

## File Structure

| File | Change |
|---|---|
| `services/db_service.py` | add `_invalidate_static()`; use it in 3 writers |
| `services/pool_service.py` | `clean_unpaid_visibility`, `get_unpaid_visibility`, `get_payment_note`, `compute_unpaid_stage`, `current_played_week`, `build_unpaid_payload`; extend `set_pool_config` (optional kwargs) |
| `routes/models.py` | `UnpaidVisibilityItem`; `unpaidVisibility`, `paymentNote` on `PoolConfigRequest` |
| `routes/admin_routes.py` | `active_season` in `fetch_admin_seasons`; extra keys in `_pool_config_response`; pass new fields in `set_pool_config` |
| `routes/api_routes.py` | `GET /api/pool/unpaid` |
| `services/nn_feature_engine.py` | `week` column guard |
| `services/model_promotion.py` | `_fmt` accepts `numbers.Real` |
| `services/analysis_service.py` | normalized join key in `calculate_wins_pool_standings` |
| `static/js/admin_pool.js`, `templates/admin.html` | unpaid controls, payment note, default season |
| `static/js/unpaid_notice.js` (new), `templates/wins_pool.html`, `templates/player_profile.html`, `static/style.css` | standings and profile UI, theme tokens |
| `static/js/theme_init.js` (new), `static/js/auth_service.js`, `static/js/main.js`, `templates/base.html` | theme |
| tests (new) | `test_db_cache_signals.py`, `test_unpaid_visibility.py`, `test_theme.py`, `test_player_profile_service.py`, `test_historical_oak_join.py`; edits to `test_admin_routes.py`, `test_model_promotion.py`, `test_team_abbr_consolidation.py` |
| docs | `docs/api_endpoints.md`, `docs/frontend.md`, `CLAUDE.md` |

---

### Task 1: Cache signals for draft writers (Issue #71, backlog B1)

**Files:**
- Modify: `services/db_service.py` (`delete_draft_results_for_season` ~line 369, `add_draft_order` ~line 420, `set_member_paid` ~line 467)
- Test: `tests/test_db_cache_signals.py` (create)

**Interfaces:**
- Produces: `services.db_service._invalidate_static() -> None` (clear then signal, `DOMAIN_STATIC`).

- [ ] **Step 1: Write the failing tests**

```python
"""Draft/pool writers must clear the local cache AND signal other instances (issue #71)."""
import ast
import pathlib

import pandas as pd
import pytest

import services.db_service as db
from services.cache_service import DOMAIN_STATIC


@pytest.fixture
def spies(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "clear_data_cache", lambda domain: calls.append(("clear", domain)))
    monkeypatch.setattr(db, "signal_data_update", lambda domain="static": calls.append(("signal", domain)))
    monkeypatch.setattr(db, "get_db", lambda: None)
    monkeypatch.setattr(db, "_save_df_to_local", lambda *a, **k: None)
    return calls


def test_invalidate_static_clears_then_signals(spies):
    db._invalidate_static()
    assert spies == [("clear", DOMAIN_STATIC), ("signal", DOMAIN_STATIC)]


def test_delete_draft_results_for_season_signals(spies, monkeypatch):
    monkeypatch.setattr(db, "get_collection_df",
                        lambda name, filters=None: pd.DataFrame({"season": [2026, 2025], "draftPick": [1, 1]}))
    db.delete_draft_results_for_season(2026)
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_add_draft_order_signals(spies, monkeypatch):
    monkeypatch.setattr(db, "get_collection_df", lambda name, filters=None: pd.DataFrame())
    db.add_draft_order(2026, 1, 7)
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_set_member_paid_signals(spies, monkeypatch):
    order = pd.DataFrame({"season": [2026], "playerId": [7], "draftOrder": [1]})
    monkeypatch.setattr(db, "get_collection_df", lambda name, filters=None: order.copy())
    assert db.set_member_paid(2026, 7, True) is True
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_no_writer_clears_static_cache_without_signalling():
    """Guard: any db_service function that calls clear_data_cache must also signal."""
    tree = ast.parse(pathlib.Path(db.__file__).read_text(encoding="utf-8"))
    offenders = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        names = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if "clear_data_cache" in names and not ({"signal_data_update", "_invalidate_static"} & names):
            offenders.append(fn.name)
    assert offenders == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_db_cache_signals.py -q`
Expected: FAIL. `_invalidate_static` missing (AttributeError); the two writer tests and the guard fail on the missing signal (guard lists `delete_draft_results_for_season`, `add_draft_order`).

- [ ] **Step 3: Implement**

Add directly after `signal_data_update` in `services/db_service.py`:

```python
def _invalidate_static() -> None:
    """Drop this process's static cache and tell every other instance to refresh."""
    clear_data_cache(DOMAIN_STATIC)
    signal_data_update(DOMAIN_STATIC)
```

Replace the trailing `clear_data_cache(DOMAIN_STATIC)` in `delete_draft_results_for_season` and `add_draft_order` with `_invalidate_static()`, and replace the `clear_data_cache(DOMAIN_STATIC)` / `signal_data_update(DOMAIN_STATIC)` pair in `set_member_paid` with `_invalidate_static()`. Do not touch other writers.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_db_cache_signals.py tests/test_set_member_paid.py tests/test_db.py tests/test_local_db_isolation.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add services/db_service.py tests/test_db_cache_signals.py
git commit -m "fix: signal other instances after draft result and draft order writes (#71)"
```

---

### Task 2: Admin Pool tab defaults to the active season (backlog B4)

**Files:**
- Modify: `routes/admin_routes.py` (`fetch_admin_seasons`, ~line 201), `static/js/admin_pool.js` (`_poolInit`)
- Test: `tests/test_admin_routes.py` (append)

**Interfaces:**
- Produces: `GET /api/admin/seasons` -> `{"seasons": [int, ...], "active_season": int | null}`; JS helper `_poolPickInitialSeason(seasons, active) -> number`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_admin_routes.py`; reuse its `admin_token` fixture and module-level `client`)

```python
class TestAdminSeasonsActive:
    def _frames(self):
        games = pd.DataFrame({"season": [2026], "week": [1], "game_type": ["REG"], "result": [3.0]})
        order = pd.DataFrame({"season": [2026, 2025], "playerId": [1, 1], "draftOrder": [1, 1]})
        results = pd.DataFrame({"season": [2026, 2025], "draftPick": [1, 1], "team": ["KC", "BUF"], "playerId": [1, 1]})
        rules = pd.DataFrame({"season": [2026]})
        return (None, None, games, None, order, results, rules)

    def test_response_includes_active_season(self, admin_token):
        with patch("routes.admin_routes.load_data", return_value=self._frames()):
            r = client.get("/api/admin/seasons", headers={"Authorization": admin_token})
        assert r.status_code == 200
        body = r.json()
        assert body["seasons"] == [2026, 2025]
        assert body["active_season"] == 2026

    def test_active_season_null_when_lookup_fails(self, admin_token):
        with patch("routes.admin_routes.load_data", return_value=self._frames()), \
             patch("routes.admin_routes.get_active_season", side_effect=RuntimeError("boom")):
            r = client.get("/api/admin/seasons", headers={"Authorization": admin_token})
        assert r.status_code == 200
        assert r.json()["active_season"] is None
```

Append a source/behavior test for the JS to `tests/test_admin_routes.py` (or `tests/test_theme.py`-style file; keep it here):

```python
import re
import shutil
import subprocess
from pathlib import Path


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_admin_pool_prefers_active_season():
    src = (Path(__file__).resolve().parent.parent / "static/js/admin_pool.js").read_text(encoding="utf-8")
    m = re.search(r"function _poolPickInitialSeason\(.*?\n\}\n", src, re.S)
    assert m, "_poolPickInitialSeason must exist"
    script = m.group(0) + """
    const out = [
      _poolPickInitialSeason([2026, 2025], 2025),
      _poolPickInitialSeason([2026, 2025], 2031),
      _poolPickInitialSeason([2026, 2025], null),
      _poolPickInitialSeason([2026, 2025], undefined),
    ];
    console.log(JSON.stringify(out));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "[2025,2026,2026,2026]"
    assert "_poolPickInitialSeason(seasons, active_season)" in src
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_admin_routes.py -k "AdminSeasonsActive or admin_pool_prefers" -q`
Expected: FAIL (`KeyError: 'active_season'`; `get_active_season` not an attribute of `routes.admin_routes`; JS helper missing).

- [ ] **Step 3: Implement**

In `routes/admin_routes.py` add `from services.data_service import get_active_season` beside the existing `load_data` import, then change `fetch_admin_seasons`:

```python
        _, _, games_df, _, draft_order_df, draft_results_df, rules_df = load_data()
        # (seasons set is built exactly as before)
        try:
            active = int(get_active_season(games_df, draft_results_df, rules_df))
        except Exception:
            logger.warning("fetch_admin_seasons: active season lookup failed", exc_info=True)
            active = None
        return JSONResponse(content={
            "seasons": sorted([int(s) for s in seasons], reverse=True),
            "active_season": active,
        })
```

In `static/js/admin_pool.js` add above `_poolInit`:

```js
function _poolPickInitialSeason(seasons, active) {
    const n = Number(active);
    if (active !== null && active !== undefined && seasons.includes(n)) return n;
    return seasons[0];
}
```

and in `_poolInit` replace the destructure and load lines:

```js
        const { seasons, active_season } = await res.json();
        // (option building is unchanged)
        if (seasons.length) {
            const initial = _poolPickInitialSeason(seasons, active_season);
            sel.value = String(initial);
            _poolLoad(initial);
        } else _poolStatus('No seasons found.', true);
```

The test's literal-string assertion `_poolPickInitialSeason(seasons, active_season)` is satisfied by that call.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_admin_routes.py tests/test_admin_pool_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/admin_routes.py static/js/admin_pool.js tests/test_admin_routes.py
git commit -m "feat: admin pool tab defaults to the active season (B4)"
```

---

### Task 3: Tech-debt items 4.4, 5.4, 5.7, 5.8

Four independent red/green cycles, one commit each.

**Files:**
- Modify: `services/nn_feature_engine.py` (~line 1320), `services/model_promotion.py:36`, `services/analysis_service.py` (~line 635), `tests/test_team_abbr_consolidation.py`
- Test: `tests/test_player_profile_service.py` (create), `tests/test_model_promotion.py` (append), `tests/test_historical_oak_join.py` (create)

#### 3a. Item 4.4 roster ingestion guard

- [ ] **Step 1: Failing test** (`tests/test_player_profile_service.py`)

```python
"""compute_preseason_player_profiles degrades to {} on a malformed weekly roster file."""
import pandas as pd

import services.nn_feature_engine as nfe


def test_weekly_roster_without_week_column_returns_empty(monkeypatch, tmp_path):
    shared = {"roster_mode": "weekly", "roster": pd.DataFrame({"team": ["KC"], "gsis_id": ["x"]})}
    monkeypatch.setattr(nfe, "_load_profile_shared_inputs", lambda *a, **k: shared)
    assert nfe.compute_preseason_player_profiles(2026, tmp_path, week=3) == {}
```

- [ ] **Step 2:** `python -m pytest tests/test_player_profile_service.py -q` -> FAIL with `KeyError: 'week'`.
- [ ] **Step 3: Implement** in `compute_preseason_player_profiles`, replace the `if week is not None:` roster block with:

```python
    roster = shared_inputs["roster"]
    if week is not None:
        if "week" not in roster.columns:
            logger.warning(
                "compute_preseason_player_profiles: weekly roster for season=%s has no "
                "'week' column; returning no profiles", target_season,
            )
            return {}
        roster = roster[pd.to_numeric(roster["week"], errors="coerce") == week].copy()
        if roster.empty:
            return {}
```

- [ ] **Step 4:** `python -m pytest tests/test_player_profile_service.py tests/test_preseason_profiles.py -q` -> PASS.
- [ ] **Step 5:** `git add services/nn_feature_engine.py tests/test_player_profile_service.py && git commit -m "fix: return no profiles when weekly roster lacks a week column (4.4)"`

#### 3b. Item 5.4 numpy scalars in `_fmt`

- [ ] **Step 1: Failing test** (append to `tests/test_model_promotion.py`; add `import numpy as np` and `from services.model_promotion import _fmt` if absent)

```python
def test_fmt_accepts_numpy_scalars():
    assert _fmt(np.float32(0.5)) == "0.5000"
    assert _fmt(np.int64(1)) == "1.0000"
    assert _fmt(np.float64(0.25)) == "0.2500"


def test_fmt_still_rejects_bool_none_and_strings():
    assert _fmt(True) == "n/a"
    assert _fmt(np.bool_(True)) == "n/a"
    assert _fmt(None) == "n/a"
    assert _fmt("0.5") == "n/a"
```

- [ ] **Step 2:** `python -m pytest tests/test_model_promotion.py -k fmt -q` -> `test_fmt_accepts_numpy_scalars` FAILS (`'n/a' != '0.5000'`); the second test passes (existing behavior pinned).
- [ ] **Step 3: Implement** in `services/model_promotion.py`: add `import numbers` to the imports and change `_fmt`:

```python
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return "n/a"
```

- [ ] **Step 4:** `python -m pytest tests/test_model_promotion.py tests/test_promotion_gate_xgb.py -q` -> PASS.
- [ ] **Step 5:** `git add services/model_promotion.py tests/test_model_promotion.py && git commit -m "fix: format numpy scalars in promotion log lines (5.4)"`

#### 3c. Item 5.7 normalized join key for OAK/LV

- [ ] **Step 1: Failing test** (`tests/test_historical_oak_join.py`)

```python
"""Historical Oakland/Las Vegas rows join whichever abbreviation each side uses (5.7)."""
import pandas as pd
import pytest

from services.analysis_service import calculate_wins_pool_standings


def _frames(draft_team, standings_team):
    draft = pd.DataFrame({"season": [2018], "playerId": [1], "fullName": ["A"], "draftPick": [1],
                          "team": [draft_team]})
    standings = pd.DataFrame({"season": [2018], "team": [standings_team], "wins": [10], "losses": [6],
                              "ties": [0], "scored": [400], "allowed": [300], "net": [100], "pct": [0.625]})
    return draft, standings


@pytest.mark.parametrize("draft_team,standings_team", [("OAK", "LV"), ("LV", "OAK"), ("OAK", "OAK")])
def test_oak_lv_rows_join(draft_team, standings_team):
    draft, standings = _frames(draft_team, standings_team)
    out = calculate_wins_pool_standings(draft, standings, 2018)
    assert out["wins"].iloc[0] == 10
    assert out["team"].iloc[0] == draft_team  # displayed abbreviation is untouched
```

Before running, open `calculate_wins_pool_standings` (`services/analysis_service.py:~600`) and confirm its positional signature is `(draft_results, standings, season, ...)`; adjust the call if the real order differs, and add whatever minimal extra columns (for example `players` or `team_records`) the function requires to reach the merge. Do not change production code to fit the test.

- [ ] **Step 2:** `python -m pytest tests/test_historical_oak_join.py -q` -> the `("OAK","LV")` and `("LV","OAK")` cases FAIL (`wins` is 0.0 from the left-join fill); `("OAK","OAK")` PASSES.
- [ ] **Step 3: Implement** in `calculate_wins_pool_standings`, replace the `pd.merge(...)` call with:

```python
    from services.utils import normalize_team_abbr
    today_draft_results['_team_key'] = today_draft_results['team'].map(normalize_team_abbr)
    standings_for_join = today_standings.assign(
        _team_key=today_standings['team'].map(normalize_team_abbr)
    ).drop(columns=['team'])
    wins_pool_standings = pd.merge(
        today_draft_results, standings_for_join, on=['_team_key', 'season'], how='left'
    ).drop(columns=['_team_key'])
```

(Move the import to module top if `services.utils` is already imported there; it is: `from services.utils import filter_season`, so extend that import instead.)

- [ ] **Step 4:** `python -m pytest tests/test_historical_oak_join.py tests/test_wins_pool_missing_standings.py tests/test_standings_player_links.py tests/test_analysis_service.py -q` -> PASS (include any other `analysis`/standings test files the repo has; run `python -m pytest tests -k "standings" -q` to be sure).
- [ ] **Step 5:** `git add services/analysis_service.py tests/test_historical_oak_join.py && git commit -m "fix: join standings on a normalized team key so OAK/LV rows match (5.7)"`

#### 3d. Item 5.8 AST scan replaces the string check

- [ ] **Step 1: Failing test** — add to `tests/test_team_abbr_consolidation.py`, above the existing guard:

```python
import ast


def _has_abbr_dict_literal(source: str) -> bool:
    """True if any dict literal in source has both "WSH" and "JAC" string keys."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"WSH", "JAC"} <= keys:
                return True
    return False


def test_abbr_dict_detector():
    assert _has_abbr_dict_literal('M = {"WSH": "WAS", "JAC": "JAX"}')
    assert _has_abbr_dict_literal("M = {'JAC': 'JAX', 'X': 1, 'WSH': 'WAS'}")
    assert not _has_abbr_dict_literal('M = {"JAC": "JAX"}')
    assert not _has_abbr_dict_literal('s = "JAC"; t = "WSH"')
```

- [ ] **Step 2:** `python -m pytest tests/test_team_abbr_consolidation.py -q` -> FAIL only if the helper is absent; it is defined in the same edit, so instead run the file once before editing the guard and confirm everything else passes (baseline), then proceed.
- [ ] **Step 3: Implement (exact replacement)** — replace the body of `test_no_second_abbreviation_dict_outside_constants` with:

```python
def test_no_second_abbreviation_dict_outside_constants():
    offenders = []
    for folder in ("services", "routes", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "constants.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if _has_abbr_dict_literal(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
```

If any file fails to parse, the test must surface it (do not swallow `SyntaxError`). If the AST scan reports offenders the old string check missed, STOP and report them; do not edit production files to satisfy the test.

- [ ] **Step 4:** `python -m pytest tests/test_team_abbr_consolidation.py tests/test_player_profile_service.py tests/test_model_promotion.py tests/test_historical_oak_join.py -q` -> PASS.
- [ ] **Step 5:** `git add tests/test_team_abbr_consolidation.py && git commit -m "test: scan dict literals via AST for a second team abbreviation map (5.8)"`

---

### Task 4: Unpaid visibility config, models and admin API (R1 backend)

**Files:**
- Modify: `services/pool_service.py`, `routes/models.py`, `routes/admin_routes.py`
- Test: `tests/test_unpaid_visibility.py` (create), `tests/test_admin_pool_config.py` (append); existing `tests/test_pool_service.py` must stay green.

**Interfaces:**
- Produces (`services/pool_service.py`):
  - `DEFAULT_UNPAID_VISIBILITY = {"enabled": False, "nudge_week": 8, "public_week": 10, "banner_week": 13}`
  - `clean_unpaid_visibility(raw) -> dict` (always a valid dict; invalid -> defaults with `enabled: False`)
  - `get_unpaid_visibility(settings: dict, season: int) -> dict`
  - `get_payment_note(settings: dict, season: int) -> str` (plain text, max 200 chars, "" if unset)
  - `set_pool_config(season, entry_fee, payouts, unpaid_visibility=None, payment_note=None) -> dict` (a `None` argument preserves that season's existing stored value; other seasons untouched)
- Produces (`routes/models.py`): `UnpaidVisibilityItem(enabled: bool, nudge_week, public_week, banner_week)` with 1..22 bounds and `nudge <= public <= banner`; `PoolConfigRequest.unpaidVisibility: Optional[UnpaidVisibilityItem]`, `PoolConfigRequest.paymentNote: Optional[str]` (max 200).
- Admin `GET/POST /api/admin/pool/config` responses gain `unpaid_visibility` and `payment_note`.

- [ ] **Step 1: Write the failing tests** (start `tests/test_unpaid_visibility.py`)

```python
"""Unpaid entry visibility: config, stage function, gated endpoint, source contracts."""
from unittest.mock import patch

import pandas as pd
import pytest
from starlette.testclient import TestClient

from main import app
from services import pool_service as ps
from services.pool_service import (
    DEFAULT_UNPAID_VISIBILITY, clean_unpaid_visibility, get_payment_note, get_unpaid_visibility,
)


def _vis(**kw):
    base = {"enabled": True, "nudge_week": 8, "public_week": 10, "banner_week": 13}
    base.update(kw)
    return base


class TestCleanUnpaidVisibility:
    def test_defaults_when_missing(self):
        assert clean_unpaid_visibility(None) == DEFAULT_UNPAID_VISIBILITY
        assert DEFAULT_UNPAID_VISIBILITY["enabled"] is False

    def test_valid_passes_through(self):
        assert clean_unpaid_visibility(_vis(nudge_week=3, public_week=3, banner_week=22)) == \
            _vis(nudge_week=3, public_week=3, banner_week=22)

    @pytest.mark.parametrize("bad", [
        _vis(nudge_week=0), _vis(banner_week=23), _vis(nudge_week=11),          # range and order
        _vis(public_week="x"), _vis(public_week=True), _vis(public_week=10.5),  # types
        "junk", 5, [],
    ])
    def test_invalid_falls_back_to_disabled_defaults(self, bad):
        assert clean_unpaid_visibility(bad) == DEFAULT_UNPAID_VISIBILITY

    def test_enabled_must_be_a_real_bool(self):
        assert clean_unpaid_visibility(_vis(enabled="yes"))["enabled"] is False


class TestStoredConfig:
    def test_get_reads_per_season(self):
        settings = {"pool_config": {"2026": {"unpaid_visibility": _vis(public_week=11)}}}
        assert get_unpaid_visibility(settings, 2026)["public_week"] == 11
        assert get_unpaid_visibility(settings, 2025) == DEFAULT_UNPAID_VISIBILITY
        assert get_unpaid_visibility({}, 2026) == DEFAULT_UNPAID_VISIBILITY

    def test_payment_note_trimmed_and_capped(self):
        settings = {"pool_config": {"2026": {"payment_note": "  @venmo-handle  "}}}
        assert get_payment_note(settings, 2026) == "@venmo-handle"
        long = {"pool_config": {"2026": {"payment_note": "x" * 500}}}
        assert len(get_payment_note(long, 2026)) == 200
        assert get_payment_note({}, 2026) == ""
        assert get_payment_note({"pool_config": {"2026": {"payment_note": 5}}}, 2026) == ""

    def test_set_round_trip_season_isolation_and_preservation(self):
        store = {"pool_config": {"2025": {"entry_fee": 100.0, "payouts": [{"place": 1, "amount": 100.0}],
                                          "unpaid_visibility": _vis(nudge_week=2, public_week=3, banner_week=4)}}}
        with patch.object(ps, "get_config_settings", side_effect=lambda: store), \
             patch.object(ps, "set_config_settings", side_effect=lambda d: store.update(d)):
            ps.set_pool_config(2026, 200, [{"place": 1, "amount": 200}],
                               unpaid_visibility=_vis(), payment_note="venmo @x")
            assert store["pool_config"]["2026"]["unpaid_visibility"] == _vis()
            assert store["pool_config"]["2026"]["payment_note"] == "venmo @x"
            assert store["pool_config"]["2025"]["unpaid_visibility"]["nudge_week"] == 2  # other season untouched
            # Omitting the new fields preserves what is stored for that season.
            ps.set_pool_config(2026, 250, [{"place": 1, "amount": 250}])
            assert store["pool_config"]["2026"]["entry_fee"] == 250.0
            assert store["pool_config"]["2026"]["unpaid_visibility"] == _vis()
            assert store["pool_config"]["2026"]["payment_note"] == "venmo @x"

    def test_set_without_new_fields_keeps_legacy_shape(self):
        store = {}
        with patch.object(ps, "get_config_settings", side_effect=lambda: store), \
             patch.object(ps, "set_config_settings", side_effect=lambda d: store.update(d)):
            saved = ps.set_pool_config(2026, 200, [{"place": 1, "amount": 200}])
        assert set(saved) == {"entry_fee", "payouts"}
```

Admin round-trip tests: append to `tests/test_admin_pool_config.py` (it already provides the `store` fixture, `_h` and `admin_token`; add `from services.pool_service import DEFAULT_UNPAID_VISIBILITY` to its imports):

```python
_VIS = {"enabled": True, "nudge_week": 8, "public_week": 10, "banner_week": 13}
_BASE = {"season": 2026, "entryFee": 200, "payouts": [{"place": 1, "amount": 1400}]}


class TestAdminPoolConfigUnpaid:
    def test_post_and_get_round_trip_with_season_isolation(self, admin_token, store):
        c = TestClient(app)
        r = c.post("/api/admin/pool/config", headers=_h(admin_token),
                   json={**_BASE, "unpaidVisibility": _VIS, "paymentNote": "venmo @x"})
        assert r.status_code == 200
        assert r.json()["unpaid_visibility"] == _VIS and r.json()["payment_note"] == "venmo @x"
        g = c.get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["unpaid_visibility"] == _VIS and g["payment_note"] == "venmo @x"
        g27 = c.get("/api/admin/pool/config?season=2027", headers=_h(admin_token)).json()
        assert g27["unpaid_visibility"] == DEFAULT_UNPAID_VISIBILITY and g27["payment_note"] == ""

    def test_get_defaults_to_disabled(self, admin_token, store):
        g = TestClient(app).get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["unpaid_visibility"] == DEFAULT_UNPAID_VISIBILITY
        assert g["unpaid_visibility"]["enabled"] is False

    def test_post_without_new_fields_keeps_stored_values(self, admin_token, store):
        c = TestClient(app)
        c.post("/api/admin/pool/config", headers=_h(admin_token),
               json={**_BASE, "unpaidVisibility": _VIS, "paymentNote": "venmo @x"})
        r = c.post("/api/admin/pool/config", headers=_h(admin_token), json={**_BASE, "entryFee": 250})
        assert r.json()["entry_fee"] == 250
        assert r.json()["unpaid_visibility"] == _VIS and r.json()["payment_note"] == "venmo @x"

    @pytest.mark.parametrize("vis", [
        {**_VIS, "nudge_week": 0},
        {**_VIS, "banner_week": 23},
        {**_VIS, "nudge_week": 12, "public_week": 10},
        {**_VIS, "public_week": "x"},
    ])
    def test_invalid_weeks_rejected(self, admin_token, store, vis):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token),
                                     json={**_BASE, "unpaidVisibility": vis})
        assert r.status_code == 422
        m.assert_not_called()

    def test_payment_note_over_200_chars_rejected(self, admin_token, store):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token),
                                     json={**_BASE, "paymentNote": "x" * 201})
        assert r.status_code == 422
        m.assert_not_called()

    def test_non_admin_cannot_set_visibility(self, store):
        tok = create_token(player_id=2, role="player")
        r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": f"Bearer {tok}"},
                                 json={**_BASE, "unpaidVisibility": _VIS})
        assert r.status_code == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_unpaid_visibility.py tests/test_admin_pool_config.py -q`
Expected: collection ImportError on `clean_unpaid_visibility` (and later failures once imports exist).

- [ ] **Step 3: Implement `services/pool_service.py`**

```python
DEFAULT_UNPAID_VISIBILITY = {"enabled": False, "nudge_week": 8, "public_week": 10, "banner_week": 13}
MAX_PAYMENT_NOTE_LEN = 200


def _default_unpaid_visibility() -> dict:
    return dict(DEFAULT_UNPAID_VISIBILITY)


def _clean_week(raw):
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 1 <= raw <= 22 else None


def clean_unpaid_visibility(raw) -> dict:
    """Valid unpaid_visibility dict; anything invalid -> defaults with enabled False. Never raises."""
    if not isinstance(raw, dict):
        return _default_unpaid_visibility()
    nudge, public, banner = (_clean_week(raw.get(k)) for k in ("nudge_week", "public_week", "banner_week"))
    if None in (nudge, public, banner) or not (nudge <= public <= banner):
        return _default_unpaid_visibility()
    return {"enabled": raw.get("enabled") is True, "nudge_week": nudge,
            "public_week": public, "banner_week": banner}


def _season_entry(settings: dict, season: int) -> dict:
    try:
        cfg_map = (settings or {}).get("pool_config")
        entry = cfg_map.get(str(season)) if isinstance(cfg_map, dict) else None
    except Exception:
        entry = None
    return entry if isinstance(entry, dict) else {}


def get_unpaid_visibility(settings: dict, season: int) -> dict:
    return clean_unpaid_visibility(_season_entry(settings, season).get("unpaid_visibility"))


def get_payment_note(settings: dict, season: int) -> str:
    note = _season_entry(settings, season).get("payment_note")
    return note.strip()[:MAX_PAYMENT_NOTE_LEN] if isinstance(note, str) else ""
```

Replace `set_pool_config` with:

```python
def set_pool_config(season: int, entry_fee: float, payouts: list,
                    unpaid_visibility=None, payment_note=None) -> dict:
    """Read-modify-write the whole pool_config map so other seasons are preserved.

    unpaid_visibility / payment_note default to None, meaning "keep what is stored for this season".
    """
    current = get_config_settings() or {}
    existing = current.get("pool_config")
    merged = dict(existing) if isinstance(existing, dict) else {}
    prev = merged.get(str(season)) if isinstance(merged.get(str(season)), dict) else {}
    saved = {
        "entry_fee": float(entry_fee),
        "payouts": _sort_payouts([{"place": p["place"], "amount": float(p["amount"])} for p in payouts]),
    }
    if unpaid_visibility is not None:
        saved["unpaid_visibility"] = clean_unpaid_visibility(unpaid_visibility)
    elif "unpaid_visibility" in prev:
        saved["unpaid_visibility"] = prev["unpaid_visibility"]
    if payment_note is not None:
        saved["payment_note"] = payment_note.strip()[:MAX_PAYMENT_NOTE_LEN]
    elif "payment_note" in prev:
        saved["payment_note"] = prev["payment_note"]
    merged[str(season)] = saved
    set_config_settings({"pool_config": merged})
    return saved
```

Update the module docstring's schema line to mention the two optional keys. `get_pool_config` is left unchanged on purpose (its output feeds `/pool/status`).

**`routes/models.py`** (add above `PoolConfigRequest`; `Optional` is already imported):

```python
class UnpaidVisibilityItem(BaseModel):
    enabled: bool = Field(False, description="Master switch for showing unpaid entries to members.")
    nudge_week: int = Field(8, ge=1, le=22, description="First week the unpaid player is nudged privately.")
    public_week: int = Field(10, ge=1, le=22, description="First week unpaid names are shown on the standings.")
    banner_week: int = Field(13, ge=1, le=22, description="First week the 'Still owed' line is shown.")

    @model_validator(mode="after")
    def _ordered_weeks(self):
        if not (self.nudge_week <= self.public_week <= self.banner_week):
            raise ValueError("weeks must satisfy nudge_week <= public_week <= banner_week")
        return self
```

and add to `PoolConfigRequest`:

```python
    unpaidVisibility: Optional[UnpaidVisibilityItem] = Field(None, description="Per-season unpaid visibility settings; omit to keep the stored value.")
    paymentNote: Optional[str] = Field(None, max_length=200, description="Plain-text payment instructions shown to unpaid players.")
```

**`routes/admin_routes.py`:** in `_pool_config_response` add
`"unpaid_visibility": pool_service.get_unpaid_visibility(settings, season), "payment_note": pool_service.get_payment_note(settings, season),` and in the POST handler pass
`unpaid_visibility=body.unpaidVisibility.model_dump() if body.unpaidVisibility else None, payment_note=body.paymentNote`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_unpaid_visibility.py tests/test_pool_service.py tests/test_admin_pool_config.py -q`
Expected: PASS. If an existing admin test pins the exact key set of the config response, replace only that assertion with the same set plus the two new keys (exact superset replacement) and say so in the commit message.

- [ ] **Step 5: Commit**

```bash
git add services/pool_service.py routes/models.py routes/admin_routes.py tests/test_unpaid_visibility.py tests/test_admin_pool_config.py
git commit -m "feat: per-season unpaid visibility config and payment note (#87)"
```

---

### Task 5: Stage function and gated `GET /api/pool/unpaid`

**Files:**
- Modify: `services/pool_service.py`, `routes/api_routes.py` (beside `get_pool_status`, ~line 739)
- Test: `tests/test_unpaid_visibility.py` (append)

**Interfaces:**
- Consumes: `get_unpaid_visibility`, `get_pool_config`, `get_payment_note` (Task 4).
- Produces (`services/pool_service.py`):
  - `compute_unpaid_stage(current_week: int, settings: dict) -> str` where `settings` is an unpaid_visibility dict; returns `"off" | "nudge" | "public" | "banner"`.
  - `current_played_week(games, season) -> int` (max REG week with a real result; 0 if none).
  - `build_unpaid_payload(order_df, players_df, settings, season, week, caller_id, is_admin) -> dict` with keys `enabled, stage, week, amount, unpaid, me_unpaid, payment_note, admin_view`.
- Route: `GET /api/pool/unpaid?season=` (auth required) returns that payload as JSON.

Payload rules: `unpaid` is `[{"playerId": int, "name": str}]` sorted by name, populated only if `is_admin` or (`enabled` and stage in `public`/`banner`), otherwise `[]`. `me_unpaid` is `bool` when stage is not `off` and the caller is a season member, else `None`. `payment_note` is non-empty only for admins or for an unpaid caller when stage is not `off`. A `paid` value that is missing or NaN counts as unpaid.

- [ ] **Step 1: Write the failing tests** (append)

```python
class TestComputeUnpaidStage:
    def test_boundaries(self):
        v = _vis()
        got = [ps.compute_unpaid_stage(w, v) for w in (0, 1, 7, 8, 9, 10, 12, 13, 22)]
        assert got == ["off", "off", "off", "nudge", "nudge", "public", "public", "banner", "banner"]

    def test_disabled_is_always_off(self):
        assert ps.compute_unpaid_stage(22, _vis(enabled=False)) == "off"

    def test_invalid_settings_and_weeks_are_off(self):
        assert ps.compute_unpaid_stage(15, {"enabled": True, "nudge_week": 20, "public_week": 10, "banner_week": 13}) == "off"
        assert ps.compute_unpaid_stage(None, _vis()) == "off"
        assert ps.compute_unpaid_stage("x", _vis()) == "off"
        assert ps.compute_unpaid_stage(15, None) == "off"

    def test_equal_weeks_pick_the_latest_stage(self):
        v = _vis(nudge_week=5, public_week=5, banner_week=5)
        assert ps.compute_unpaid_stage(5, v) == "banner"


class TestCurrentPlayedWeek:
    def test_uses_results_not_schedule(self):
        games = pd.DataFrame({"season": [2026] * 4, "game_type": ["REG"] * 4, "week": [1, 2, 3, 18],
                              "result": [3.0, -7.0, 10.0, None]})
        assert ps.current_played_week(games, 2026) == 3

    def test_ignores_other_seasons_playoffs_and_sentinel(self):
        from services.constants import UNDRAFTED_SENTINEL
        games = pd.DataFrame({"season": [2025, 2026, 2026], "game_type": ["REG", "POST", "REG"],
                              "week": [18, 20, 2], "result": [1.0, 3.0, UNDRAFTED_SENTINEL]})
        assert ps.current_played_week(games, 2026) == 0
        assert ps.current_played_week(pd.DataFrame(), 2026) == 0
        assert ps.current_played_week(None, 2026) == 0


def _order(paid_flags):
    return pd.DataFrame({"season": [2026] * len(paid_flags), "playerId": list(range(1, len(paid_flags) + 1)),
                         "draftOrder": list(range(1, len(paid_flags) + 1)), "paid": paid_flags})


def _players(n):
    return pd.DataFrame({"playerId": list(range(1, n + 1)), "fullName": [f"Player {chr(64 + i)}" for i in range(1, n + 1)]})


def _settings(vis=None, note=None):
    entry = {"entry_fee": 200, "payouts": [{"place": 1, "amount": 200}]}
    if vis is not None:
        entry["unpaid_visibility"] = vis
    if note is not None:
        entry["payment_note"] = note
    return {"pool_config": {"2026": entry}}


class TestBuildUnpaidPayload:
    def _p(self, week, caller=2, admin=False, vis=None, note=None, paid=(True, False, False)):
        return ps.build_unpaid_payload(_order(list(paid)), _players(len(paid)),
                                       _settings(_vis() if vis is None else vis, note), 2026, week, caller, admin)

    def test_off_and_nudge_never_list_names_for_members(self):
        for week in (0, 5, 8, 9):
            p = self._p(week)
            assert p["unpaid"] == []
            assert "Player B" not in str(p)

    def test_nudge_reports_only_callers_own_status(self):
        p = self._p(8, caller=2)
        assert p["stage"] == "nudge" and p["me_unpaid"] is True and p["amount"] == 200.0
        assert self._p(8, caller=1)["me_unpaid"] is False

    def test_public_and_banner_list_unpaid_names(self):
        for week, stage in ((10, "public"), (13, "banner")):
            p = self._p(week)
            assert p["stage"] == stage
            assert p["unpaid"] == [{"playerId": 2, "name": "Player B"}, {"playerId": 3, "name": "Player C"}]

    def test_disabled_late_week_does_not_leak(self):
        p = self._p(15, vis=_vis(enabled=False))
        assert p["stage"] == "off" and p["unpaid"] == [] and p["me_unpaid"] is None

    def test_admin_sees_full_list_in_every_stage(self):
        for week in (0, 8, 10):
            p = self._p(week, admin=True)
            assert p["admin_view"] is True and len(p["unpaid"]) == 2
        assert self._p(0, admin=True, vis=_vis(enabled=False))["unpaid"] != []
        assert self._p(10)["admin_view"] is False

    def test_caller_not_a_member(self):
        assert self._p(9, caller=99)["me_unpaid"] is None
        assert self._p(9, caller=None)["me_unpaid"] is None

    def test_nan_and_missing_paid_count_as_unpaid(self):
        p = self._p(10, paid=(True, None, float("nan")))
        assert [u["playerId"] for u in p["unpaid"]] == [2, 3]
        order = _order([True, True]).drop(columns=["paid"])
        p2 = ps.build_unpaid_payload(order, _players(2), _settings(_vis()), 2026, 10, 1, False)
        assert len(p2["unpaid"]) == 2

    def test_no_rows_for_season_is_empty_not_error(self):
        p = ps.build_unpaid_payload(pd.DataFrame(), pd.DataFrame(), _settings(_vis()), 2026, 10, 1, False)
        assert p["unpaid"] == [] and p["me_unpaid"] is None
        p = ps.build_unpaid_payload(None, None, {}, 2026, 10, 1, False)
        assert p["stage"] == "off"

    def test_payment_note_only_for_unpaid_caller_or_admin(self):
        assert self._p(9, caller=2, note="venmo @x")["payment_note"] == "venmo @x"
        assert self._p(9, caller=1, note="venmo @x")["payment_note"] == ""
        assert self._p(0, caller=2, note="venmo @x")["payment_note"] == ""
        assert self._p(0, caller=1, admin=True, note="venmo @x")["payment_note"] == "venmo @x"

    def test_missing_player_name_falls_back(self):
        p = ps.build_unpaid_payload(_order([False]), pd.DataFrame(), _settings(_vis()), 2026, 10, 9, False)
        assert p["unpaid"] == [{"playerId": 1, "name": "Player 1"}]


def _load(order, players, games):
    return (None, None, games, players, order, pd.DataFrame(), pd.DataFrame())


def _games(week=10):
    return pd.DataFrame({"season": [2026], "game_type": ["REG"], "week": [week], "result": [3.0]})


@pytest.fixture
def as_player_2():
    from routes.api_routes import require_auth
    app.dependency_overrides[require_auth] = lambda: {"sub": "2", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture
def as_admin():
    from routes.api_routes import require_auth
    app.dependency_overrides[require_auth] = lambda: {"sub": "1", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestUnpaidRoute:
    def _get(self, week, vis=None):
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, False, False]), _players(3), _games(week))), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis() if vis is None else vis)):
            return TestClient(app).get("/api/pool/unpaid?season=2026")

    def test_requires_auth(self):
        assert TestClient(app).get("/api/pool/unpaid").status_code in (401, 403)

    def test_member_off_stage_gets_empty_list(self, as_player_2):
        body = self._get(3).json()
        assert body["stage"] == "off" and body["unpaid"] == [] and body["week"] == 3

    def test_member_nudge_gets_own_flag_only(self, as_player_2):
        r = self._get(8)
        assert r.json()["me_unpaid"] is True and r.json()["unpaid"] == []
        assert "Player C" not in r.text

    def test_member_public_gets_names(self, as_player_2):
        assert len(self._get(10).json()["unpaid"]) == 2

    def test_admin_view(self, as_admin):
        body = self._get(0).json()
        assert body["admin_view"] is True and len(body["unpaid"]) == 2

    def test_status_endpoint_untouched(self, as_player_2):
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, False]), _players(2), _games())), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis())):
            body = TestClient(app).get("/api/pool/status?season=2026").json()
        assert "unpaid" not in body and "stage" not in body

    def test_set_member_paid_is_reflected_on_next_call(self, as_player_2):
        first = self._get(10).json()
        assert {u["playerId"] for u in first["unpaid"]} == {2, 3}
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, True, False]), _players(3), _games(10))), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis())):
            second = TestClient(app).get("/api/pool/unpaid?season=2026").json()
        assert {u["playerId"] for u in second["unpaid"]} == {3}
```

Note: the last test proves the route reads fresh `load_data()` output on each call (no response caching in the route); the actual cache invalidation path is pinned in `tests/test_set_member_paid.py::TestPoolStatusSeesPaidToggle` and Task 1.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_unpaid_visibility.py -q`
Expected: FAIL (`compute_unpaid_stage`, `current_played_week`, `build_unpaid_payload` undefined; route returns 404).

- [ ] **Step 3: Implement** in `services/pool_service.py` (add `import pandas as pd` and `from services.data_service import get_most_recent_completed_week` at the top):

```python
def compute_unpaid_stage(current_week, settings) -> str:
    """'off' | 'nudge' | 'public' | 'banner' for the week and an unpaid_visibility dict."""
    vis = clean_unpaid_visibility(settings)
    if not vis["enabled"]:
        return "off"
    try:
        week = int(current_week)
    except (TypeError, ValueError):
        return "off"
    if week >= vis["banner_week"]:
        return "banner"
    if week >= vis["public_week"]:
        return "public"
    if week >= vis["nudge_week"]:
        return "nudge"
    return "off"


def current_played_week(games, season) -> int:
    """Most recent completed REG week of `season`; 0 if none. Thin wrapper over the
    existing data_service.get_most_recent_completed_week (also used for recaps), which
    ignores the unplayed schedule, unlike get_latest_season_and_week."""
    if games is None:
        return 0
    week = get_most_recent_completed_week(games, season)
    return 0 if week is None else int(week)


def _unpaid_members(order_df, players_df, season) -> list:
    if order_df is None or order_df.empty or not {"season", "playerId"} <= set(order_df.columns):
        return []
    rows = order_df[order_df["season"] == season]
    if rows.empty:
        return []
    paid = rows["paid"].fillna(False).astype(bool) if "paid" in rows.columns else pd.Series(False, index=rows.index)
    names = {}
    if players_df is not None and not players_df.empty and {"playerId", "fullName"} <= set(players_df.columns):
        names = {int(r.playerId): str(r.fullName) for r in players_df.itertuples() if pd.notna(r.fullName)}
    out = [{"playerId": int(pid), "name": names.get(int(pid), f"Player {int(pid)}")}
           for pid in rows.loc[~paid, "playerId"]]
    return sorted(out, key=lambda r: r["name"].lower())


def build_unpaid_payload(order_df, players_df, settings, season, week, caller_id, is_admin) -> dict:
    """Gated unpaid-entry view. Names leave this function only for admins or in the
    public/banner stages of an enabled season; nudge and off return an empty list."""
    cfg = get_pool_config(settings, season)
    vis = get_unpaid_visibility(settings, season)
    stage = compute_unpaid_stage(week, vis)
    unpaid = _unpaid_members(order_df, players_df, season)
    unpaid_ids = {u["playerId"] for u in unpaid}

    member_ids = set()
    if order_df is not None and not order_df.empty and {"season", "playerId"} <= set(order_df.columns):
        member_ids = {int(p) for p in order_df.loc[order_df["season"] == season, "playerId"]}
    me_member = caller_id is not None and int(caller_id) in member_ids
    me_unpaid = (int(caller_id) in unpaid_ids) if (stage != "off" and me_member) else None

    reveal = bool(is_admin) or (vis["enabled"] and stage in ("public", "banner"))
    show_note = bool(is_admin) or (stage != "off" and me_unpaid is True)
    return {
        "enabled": vis["enabled"],
        "stage": stage,
        "week": int(week),
        "amount": cfg["entry_fee"],
        "unpaid": unpaid if reveal else [],
        "me_unpaid": me_unpaid,
        "payment_note": get_payment_note(settings, season) if show_note else "",
        "admin_view": bool(is_admin),
    }
```

In `routes/api_routes.py` add `from services.pool_service import build_pool_status, build_unpaid_payload, current_played_week` (extend the existing `build_pool_status` import) and, after `get_pool_status`:

```python
@router.get("/pool/unpaid")
def get_pool_unpaid(season: int | None = None, _auth: dict = Depends(require_auth)):
    """Gated unpaid-entry view; names are returned only to admins or in the public/banner stages."""
    try:
        from services.data_service import get_active_season
        _, _, games, players_df, order_df, draft_results, rules = load_data()
        if season is None:
            season = int(get_active_season(games, draft_results, rules))
        try:
            caller_id = int(_auth.get("sub"))
        except (TypeError, ValueError):
            caller_id = None
        payload = build_unpaid_payload(
            order_df, players_df, get_config_settings(), season,
            current_played_week(games, season), caller_id, _auth.get("role") == "admin",
        )
        return JSONResponse(content=payload)
    except Exception:
        logger.exception("Unhandled error in get_pool_unpaid")
        return server_error()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_unpaid_visibility.py tests/test_pool_service.py tests/test_set_member_paid.py -q`
Expected: PASS, including the unchanged `/api/pool/status` key-set test.

- [ ] **Step 5: Commit**

```bash
git add services/pool_service.py routes/api_routes.py tests/test_unpaid_visibility.py
git commit -m "feat: gated GET /api/pool/unpaid with staged visibility (#87)"
```

---

### Task 6: Admin Pool tab controls (R1 frontend)

**Files:**
- Modify: `templates/admin.html` (Pool section, after `#pool-payout-rows`/`#pool-add-payout`, before the Save row ~line 132), `static/js/admin_pool.js`
- Test: `tests/test_unpaid_visibility.py` (append source contracts and a node test)

**Interfaces:**
- Consumes: admin config response keys `unpaid_visibility`, `payment_note` (Task 4).
- Produces: DOM ids `pool-unpaid-enabled`, `pool-unpaid-nudge`, `pool-unpaid-public`, `pool-unpaid-banner`, `pool-unpaid-preview`, `pool-payment-note`; JS functions `_poolUnpaidPreview(vis)` (string) and `_poolUnpaidValidate(vis)` (error string or `null`).

- [ ] **Step 1: Write the failing tests**

```python
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_admin_pool_markup_has_unpaid_controls():
    html = _read("templates/admin.html")
    for el_id in ("pool-unpaid-enabled", "pool-unpaid-nudge", "pool-unpaid-public",
                  "pool-unpaid-banner", "pool-unpaid-preview", "pool-payment-note"):
        assert f'id="{el_id}"' in html
    assert "Show unpaid entries to members" in html
    assert re.search(r'id="pool-unpaid-nudge"[^>]*min="1"[^>]*max="22"', html)


def test_admin_pool_js_contract():
    js = _read("static/js/admin_pool.js")
    assert "unpaidVisibility" in js and "paymentNote" in js
    assert "innerHTML" not in js
    assert "textContent" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_admin_pool_unpaid_preview_and_validation():
    js = _read("static/js/admin_pool.js")
    fns = []
    for name in ("_poolUnpaidPreview", "_poolUnpaidValidate"):
        m = re.search(rf"function {name}\(.*?\n\}}\n", js, re.S)
        assert m, f"{name} missing"
        fns.append(m.group(0))
    script = "\n".join(fns) + """
    const ok = {enabled: true, nudge_week: 8, public_week: 10, banner_week: 13};
    console.log(JSON.stringify([
      _poolUnpaidPreview(ok),
      _poolUnpaidPreview({...ok, enabled: false}),
      _poolUnpaidValidate(ok),
      _poolUnpaidValidate({...ok, nudge_week: 11}) !== null,
      _poolUnpaidValidate({...ok, banner_week: 23}) !== null,
      _poolUnpaidValidate({...ok, public_week: NaN}) !== null,
    ]));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    import json
    got = json.loads(out)
    assert got[0] == "Nudge from week 8, names shown from week 10, list from week 13"
    assert got[1] == "Unpaid visibility is off."
    assert got[2] is None and got[3] and got[4] and got[5]
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_unpaid_visibility.py -k "admin_pool" -q` -> FAIL (ids and functions absent).

- [ ] **Step 3: Implement**

`templates/admin.html` (insert before the Save button row; match the file's existing indentation and the `pool-label`/`admin-input` classes already used there):

```html
        <h4 class="pool-label" style="margin-top: 1rem;">Unpaid entry visibility</h4>
        <label class="pool-label"><input type="checkbox" id="pool-unpaid-enabled"> Show unpaid entries to members</label>
        <div class="pool-config-grid">
            <div><label for="pool-unpaid-nudge" class="pool-label">Nudge from week</label>
                <input id="pool-unpaid-nudge" type="number" min="1" max="22" step="1" class="admin-input"></div>
            <div><label for="pool-unpaid-public" class="pool-label">Show names from week</label>
                <input id="pool-unpaid-public" type="number" min="1" max="22" step="1" class="admin-input"></div>
            <div><label for="pool-unpaid-banner" class="pool-label">Still-owed list from week</label>
                <input id="pool-unpaid-banner" type="number" min="1" max="22" step="1" class="admin-input"></div>
        </div>
        <div id="pool-unpaid-preview" class="pool-note"></div>
        <label for="pool-payment-note" class="pool-label" style="margin-top: 0.75rem;">Payment note (shown to unpaid players, max 200 characters)</label>
        <input id="pool-payment-note" type="text" maxlength="200" class="admin-input" placeholder="For example: Venmo @handle">
```

`static/js/admin_pool.js` additions:

```js
function _poolUnpaidPreview(vis) {
    if (!vis.enabled) return 'Unpaid visibility is off.';
    return 'Nudge from week ' + vis.nudge_week + ', names shown from week ' + vis.public_week +
        ', list from week ' + vis.banner_week;
}

function _poolUnpaidValidate(vis) {
    const weeks = [vis.nudge_week, vis.public_week, vis.banner_week];
    if (!weeks.every(w => Number.isInteger(w) && w >= 1 && w <= 22)) return 'Weeks must be whole numbers from 1 to 22.';
    if (!(vis.nudge_week <= vis.public_week && vis.public_week <= vis.banner_week)) {
        return 'Weeks must satisfy nudge <= names <= list.';
    }
    return null;
}

function _poolUnpaidCollect() {
    return {
        enabled: _poolEl('pool-unpaid-enabled').checked,
        nudge_week: parseInt(_poolEl('pool-unpaid-nudge').value, 10),
        public_week: parseInt(_poolEl('pool-unpaid-public').value, 10),
        banner_week: parseInt(_poolEl('pool-unpaid-banner').value, 10),
    };
}

function _poolUnpaidRefreshPreview() {
    const el = _poolEl('pool-unpaid-preview');
    if (!el) return;
    const vis = _poolUnpaidCollect();
    el.textContent = _poolUnpaidValidate(vis) ? _poolUnpaidValidate(vis) : _poolUnpaidPreview(vis);
}
```

In `_poolRender(cfg)`, after the existing lines, populate from `cfg.unpaid_visibility` (checkbox, three numbers) and `cfg.payment_note`, then call `_poolUnpaidRefreshPreview()`. In `_poolSave`, after the duplicate-place check, validate and add to the body:

```js
    const vis = _poolUnpaidCollect();
    const visErr = _poolUnpaidValidate(vis);
    if (visErr) { _poolStatus(visErr, true); return; }
    // (the fetch call keeps its method/headers/credentials; only the body changes)
    body: JSON.stringify({ season, entryFee: fee, payouts, unpaidVisibility: vis,
                           paymentNote: _poolEl('pool-payment-note').value }),
```

In the `DOMContentLoaded` handler add `input`/`change` listeners on the four controls that call `_poolUnpaidRefreshPreview`.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_unpaid_visibility.py tests/test_admin_routes.py -q` -> PASS. Then load `/admin` locally (see the `run` skill) and check the Pool tab at 1280px and 390px: controls wrap without horizontal scroll, preview updates live, Save round-trips.
- [ ] **Step 5: Commit**

```bash
git add templates/admin.html static/js/admin_pool.js tests/test_unpaid_visibility.py
git commit -m "feat: admin pool tab controls for unpaid visibility (#87)"
```

---

### Task 7: Standings and player-page UI (R3, R4)

**Files:**
- Create: `static/js/unpaid_notice.js`
- Modify: `templates/wins_pool.html` (after line 237), `templates/player_profile.html` (pool card IIFE ~line 423), `static/style.css`
- Test: `tests/test_unpaid_visibility.py` (append)

**Interfaces:**
- Consumes: `GET /api/pool/unpaid` payload (Task 5); `getAuthHeaders` from `auth_service.js`.
- Produces: classes `.unpaid-pill`, `.unpaid-notice`, `.unpaid-owed-line`; script hooks `data-player-id` (existing) and name containers `.wp-leader-name`, `.wp-row-name`, `.standings-stacked-card__name`.

- [ ] **Step 1: Write the failing tests**

```python
def test_unpaid_script_contract():
    js = _read("static/js/unpaid_notice.js")
    assert "/api/pool/unpaid" in js
    assert "innerHTML" not in js
    assert "createElement('span')" in js and "unpaid-pill" in js
    assert "createElement('a')" not in js          # pills are never links
    assert "querySelector('.unpaid-pill')" in js    # idempotent: skip when already present
    assert "MutationObserver" in js                 # re-applied after the 30s refresh
    assert "getAuthHeaders" in js
    assert "sessionStorage" in js                   # dismissible per session, in try/catch
    for container in (".wp-leader-name", ".wp-row-name", ".standings-stacked-card__name"):
        assert container in js


def test_wins_pool_loads_unpaid_script_as_module():
    html = _read("templates/wins_pool.html")
    assert re.search(r'<script type="module" src="\{\{ static_url\(\'js/unpaid_notice\.js\'\) \}\}"></script>', html)
    # nothing server-rendered by default
    assert "unpaid-pill" not in html and "Still owed" not in html


def test_unpaid_styles_exist_and_are_not_tap_targets():
    css = _read("static/style.css")
    assert ".unpaid-pill" in css and ".unpaid-notice" in css
    block = re.search(r"\.unpaid-pill\s*\{[^}]*\}", css).group(0)
    assert "pointer-events: none" in block and "margin-left" in block


def test_player_profile_shows_amount_owed_and_note_safely():
    html = _read("templates/player_profile.html")
    assert "/api/pool/unpaid" in html
    assert "payment_note" in html
    gate = html.index("own-page-only")
    assert html.index("/api/pool/unpaid") > gate
    seg = html[html.index("/api/pool/unpaid"): html.index("/api/pool/unpaid") + 1500]
    assert "innerHTML" not in seg
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_unpaid_visibility.py -k "unpaid_script or loads_unpaid or unpaid_styles or amount_owed" -q` -> FAIL (file and markup absent).

- [ ] **Step 3: Implement**

`static/js/unpaid_notice.js`:

```js
// Unpaid entry notices on the wins pool page, driven entirely by GET /api/pool/unpaid.
// The server returns names only for admins or in the public/banner stages, so nothing
// here can reveal data by being edited. Failure or the "off" stage renders nothing.
import { getAuthHeaders } from './auth_service.js';

(function () {
    const chip = document.getElementById('pool-fee-banner');
    if (!chip) return;
    const DISMISS_KEY = 'nfl_wins_unpaid_dismissed';
    const NAME_SELECTORS = ['.wp-leader-name', '.wp-row-name', '.standings-stacked-card__name'];
    let unpaidIds = new Set();

    function money(n) {
        return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
    }

    function dismissed() {
        try { return sessionStorage.getItem(DISMISS_KEY) === '1'; } catch (e) { return false; }
    }

    function rememberDismiss() {
        try { sessionStorage.setItem(DISMISS_KEY, '1'); } catch (e) { /* storage unavailable */ }
    }

    function applyPills() {
        if (!unpaidIds.size) return;
        document.querySelectorAll('[data-player-id]').forEach(card => {
            if (!unpaidIds.has(String(card.dataset.playerId))) return;
            NAME_SELECTORS.forEach(sel => {
                const nameBox = card.querySelector(sel);
                if (!nameBox || nameBox.querySelector('.unpaid-pill')) return;
                const pill = document.createElement('span');
                pill.className = 'unpaid-pill';
                pill.textContent = 'Unpaid';
                nameBox.appendChild(pill);
            });
        });
    }

    function noticeHost() {
        let host = document.getElementById('unpaid-notice-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'unpaid-notice-host';
            chip.closest('header').insertAdjacentElement('afterend', host);
        }
        return host;
    }

    function showNudge(amount) {
        if (dismissed()) return;
        const host = noticeHost();
        host.textContent = '';
        const strip = document.createElement('div');
        strip.className = 'unpaid-notice';
        strip.setAttribute('role', 'status');
        const text = document.createElement('span');
        text.textContent = 'Your ' + money(amount) + ' entry is still unpaid.';
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'unpaid-notice__close';
        close.setAttribute('aria-label', 'Dismiss reminder');
        close.textContent = 'Dismiss';
        close.addEventListener('click', () => { rememberDismiss(); host.textContent = ''; });
        strip.appendChild(text);
        strip.appendChild(close);
        host.appendChild(strip);
    }

    function showBanner(names) {
        if (!names.length) return;
        const host = noticeHost();
        host.textContent = '';
        const line = document.createElement('div');
        line.className = 'unpaid-notice';
        line.textContent = 'Still owed: ' + names.join(', ');
        host.appendChild(line);
    }

    async function load() {
        try {
            const res = await fetch('/api/pool/unpaid?season=' + encodeURIComponent(chip.dataset.year),
                { headers: getAuthHeaders(), credentials: 'same-origin' });
            if (!res.ok) return;
            const d = await res.json();
            if (!d || d.stage === 'off') return;
            if (d.stage === 'nudge' && d.me_unpaid) showNudge(d.amount);
            if (d.stage === 'public' || d.stage === 'banner') {
                unpaidIds = new Set((d.unpaid || []).map(u => String(u.playerId)));
                applyPills();
                new MutationObserver(applyPills).observe(document.body, { childList: true, subtree: true });
            }
            if (d.stage === 'banner') showBanner((d.unpaid || []).map(u => u.name));
        } catch (e) {
            // Render nothing on any failure.
        }
    }
    load();
})();
```

(`applyPills` only appends when absent, so the observer it triggers settles after one pass and cannot loop.)

`templates/wins_pool.html`: add after the `pool_fee.js` line:
`<script type="module" src="{{ static_url('js/unpaid_notice.js') }}"></script>`

`static/style.css` (append; muted warning from the existing `--warn` token, no alarm colors):

```css
.unpaid-pill {
    display: inline-block;
    margin-left: 0.75rem;
    padding: 0.1rem 0.5rem;
    font: 500 11px/1.4 'JetBrains Mono', monospace;
    color: var(--warn);
    border: 1px solid var(--warn);
    border-radius: 999px;
    vertical-align: middle;
    pointer-events: none;
    user-select: none;
}

.unpaid-notice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin: 0 0 1rem;
    padding: 0.6rem 0.9rem;
    color: var(--warn);
    background: var(--bg-elev);
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    font-size: 0.9rem;
}

.unpaid-notice__close {
    min-height: 44px;
    min-width: 44px;
    color: var(--ink-2);
    background: transparent;
    border: 0;
    cursor: pointer;
}

.unpaid-owed-line {
    margin-top: 0.5rem;
    color: var(--warn);
    font-size: 0.9rem;
}
```

`templates/player_profile.html`: inside the existing pool-card IIFE, after `card.style.display = 'block';`, add a second fetch (still inside the own-page-only path; textContent only):

```js
      try {
        const ur = await fetch('/api/pool/unpaid', { headers, credentials: 'same-origin' });
        if (ur.ok) {
          const u = await ur.json();
          if (u && u.stage !== 'off' && u.me_unpaid) {
            add(card, 'div', 'unpaid-owed-line', 'Amount owed: ' + money(u.amount));
            if (u.payment_note) add(card, 'div', 'unpaid-owed-line', u.payment_note);
          }
        }
      } catch (e) { /* optional block */ }
```

(`add` and `money` are already defined in that scope; the enclosing IIFE is `async`, so `await` is valid.)

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_unpaid_visibility.py tests/test_standings_player_links.py tests/test_player_page.py tests/test_wins_pool_missing_standings.py -q` -> PASS. Then in a browser at 1280px and 390px with the feature enabled and a lowered `public_week` for the test season: pills appear in the leader card, desktop rows and stacked cards, are not clickable, do not shrink the 44px name link tap area, and survive the 30s refresh without duplicating.
- [ ] **Step 5: Commit**

```bash
git add static/js/unpaid_notice.js static/style.css templates/wins_pool.html templates/player_profile.html tests/test_unpaid_visibility.py
git commit -m "feat: unpaid pills, reminder strip, still-owed line and amount owed (#87)"
```

---

### Task 8: Light/dark theme (Issue #107)

**Files:**
- Create: `static/js/theme_init.js`
- Modify: `static/style.css`, `static/js/auth_service.js`, `static/js/main.js`, `templates/base.html`, `templates/player_profile.html`
- Test: `tests/test_theme.py` (create)

**Interfaces:**
- Produces: `STORAGE_KEYS.THEME = 'nfl_wins_theme'`; `window.WinsPoolTheme = { KEY, get(), set(theme), toggle(), init() }` (theme is `'light'` or `'dark'`); buttons marked `data-theme-toggle`; CSS block `:root[data-theme="light"]`.

Real tokens in `static/style.css` are `--bg`, `--bg-elev`, `--bg-elev-2`, `--line`, `--line-strong`, `--ink`, `--ink-2`, `--ink-3`, `--leader`, `--leader-soft`, `--pos`, `--neg`, `--link`, `--warn`, `--primary-hover`, `--glass-bg`; `--glass-border` aliases `--line-strong` and `--text-*` alias `--ink*`, so overriding the base tokens recolors the aliases.

- [ ] **Step 1: Write the failing tests** (`tests/test_theme.py`)

```python
"""Light/dark theme: token contract, WCAG AA contrast, and node-run init/toggle behavior."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _light_tokens():
    css = _read("static/style.css")
    m = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\n\}', css, re.S)
    assert m, 'static/style.css must define :root[data-theme="light"]'
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def _lum(hex_color):
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_storage_key_constant():
    assert "THEME: 'nfl_wins_theme'" in _read("static/js/auth_service.js")


def test_light_block_overrides_required_token_families():
    tokens = _light_tokens()
    for name in ("--bg", "--bg-elev", "--bg-elev-2", "--line", "--line-strong",
                 "--ink", "--ink-2", "--ink-3", "--glass-bg"):
        assert name in tokens, f"{name} missing from light theme"
    assert "color-scheme" in _read("static/style.css")


@pytest.mark.parametrize("fg", ["--ink", "--ink-2", "--ink-3", "--link", "--pos", "--neg", "--warn", "--leader"])
@pytest.mark.parametrize("bg", ["--bg", "--bg-elev"])
def test_light_text_tokens_meet_wcag_aa(fg, bg):
    t = _light_tokens()
    assert _ratio(t[fg], t[bg]) >= 4.5, f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}"


def test_base_loads_classic_theme_script_in_head_before_stylesheet_paint():
    html = _read("templates/base.html")
    head = html[: html.index("</head>")]
    m = re.search(r'<script(?![^>]*type="module")[^>]*theme_init\.js', head)
    assert m, "theme_init.js must be a classic script in <head>"


def test_toggle_buttons_exist_on_profile_and_drawer():
    assert "data-theme-toggle" in _read("templates/player_profile.html")
    assert "data-theme-toggle" in _read("templates/base.html")


def test_main_js_uses_theme_storage_key_and_wires_toggles():
    js = _read("static/js/main.js")
    assert "STORAGE_KEYS.THEME" in js
    assert "data-theme-toggle" in js
    assert "'storage'" in js


_HARNESS = """
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[1], 'utf8');
function run({stored, light, storageThrows, noMatchMedia}) {
  const attrs = {};
  const store = stored === undefined ? {} : {nfl_wins_theme: stored};
  const window = {
    localStorage: {
      getItem(k) { if (storageThrows) throw new Error('blocked'); return k in store ? store[k] : null; },
      setItem(k, v) { if (storageThrows) throw new Error('blocked'); store[k] = v; },
    },
  };
  if (!noMatchMedia) window.matchMedia = () => ({ matches: !!light });
  const document = { documentElement: {
    setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return k in attrs ? attrs[k] : null; } } };
  vm.runInNewContext(src, { window, document });
  return { window, attrs, store };
}
const out = {};
out.unsetLight = run({light: true}).attrs['data-theme'];
out.unsetDark = run({light: false}).attrs['data-theme'];
out.storedBeatsSystem = run({stored: 'dark', light: true}).attrs['data-theme'];
out.invalidStored = run({stored: 'purple', light: true}).attrs['data-theme'];
out.noMatchMedia = run({noMatchMedia: true}).attrs['data-theme'];
out.storageBlocked = run({storageThrows: true, light: true}).attrs['data-theme'];
const r = run({stored: 'dark'});
const first = r.window.WinsPoolTheme.toggle();
out.toggleOnce = [first, r.attrs['data-theme'], r.store.nfl_wins_theme];
out.toggleTwice = [r.window.WinsPoolTheme.toggle(), r.store.nfl_wins_theme];
const b = run({storageThrows: true});
out.toggleWithBlockedStorage = b.window.WinsPoolTheme.toggle();
out.key = r.window.WinsPoolTheme.KEY;
console.log(JSON.stringify(out));
"""


@needs_node
def test_theme_init_behavior(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(harness), str(ROOT / "static/js/theme_init.js")],
                         capture_output=True, text=True, check=True)
    got = json.loads(res.stdout)
    assert got["unsetLight"] == "light"
    assert got["unsetDark"] == "dark"
    assert got["storedBeatsSystem"] == "dark"
    assert got["invalidStored"] == "light"        # invalid value ignored, system preference used
    assert got["noMatchMedia"] == "dark"
    assert got["storageBlocked"] == "light"       # still renders when storage throws
    assert got["toggleOnce"] == ["light", "light", "light"]
    assert got["toggleTwice"] == ["dark", "dark"]
    assert got["toggleWithBlockedStorage"] in ("light", "dark")
    assert got["key"] == "nfl_wins_theme"
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_theme.py -q` -> FAIL (light block, `theme_init.js`, key and buttons all absent).

- [ ] **Step 3: Implement**

`static/js/auth_service.js`: add `THEME: 'nfl_wins_theme',` to `STORAGE_KEYS`.

`static/js/theme_init.js` (classic script, no module syntax):

```js
// Applies the saved (or system) theme before first paint and exposes a toggle.
// Classic script loaded from <head>: a deferred module such as main.js would run after
// the first paint and flash the dark theme. Every storage access is guarded.
(function () {
    var KEY = 'nfl_wins_theme';

    function stored() {
        try {
            var v = window.localStorage.getItem(KEY);
            return v === 'light' || v === 'dark' ? v : null;
        } catch (e) { return null; }
    }

    function system() {
        try {
            return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
        } catch (e) { return 'dark'; }
    }

    function apply(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        return theme;
    }

    function current() {
        return document.documentElement.getAttribute('data-theme') || stored() || system();
    }

    function set(theme) {
        try { window.localStorage.setItem(KEY, theme); } catch (e) { /* storage unavailable */ }
        return apply(theme);
    }

    window.WinsPoolTheme = {
        KEY: KEY,
        get: current,
        set: set,
        toggle: function () { return set(current() === 'light' ? 'dark' : 'light'); },
        init: function () { return apply(stored() || system()); },
    };
    window.WinsPoolTheme.init();
})();
```

`templates/base.html`: add in `<head>` before the stylesheet link: `<script src="{{ static_url('js/theme_init.js') }}"></script>`. Add the drawer button between `.nav-drawer__links` and `.nav-drawer__footer` (a button, not a link, so the nav-parity test is unaffected):

```html
            <button type="button" class="nav-drawer__theme" data-theme-toggle>Switch theme</button>
```

`static/js/main.js` (add `STORAGE_KEYS` to the existing `auth_service.js` import if not present, then call `wireThemeToggles()` once at app startup):

```js
function wireThemeToggles() {
    const theme = window.WinsPoolTheme;
    if (!theme) return;
    const buttons = document.querySelectorAll('[data-theme-toggle]');
    const label = () => {
        const next = theme.get() === 'light' ? 'dark' : 'light';
        buttons.forEach(b => { b.textContent = 'Switch to ' + next + ' mode'; b.setAttribute('aria-pressed', String(theme.get() === 'light')); });
    };
    buttons.forEach(b => b.addEventListener('click', () => { theme.toggle(); label(); }));
    window.addEventListener('storage', e => {
        if (e.key === STORAGE_KEYS.THEME && (e.newValue === 'light' || e.newValue === 'dark')) {
            theme.init();
            label();
        }
    });
    label();
}
```

`templates/player_profile.html`: add, in the always-visible area near the page header (theme is per device, not own-page-only), `<button type="button" class="btn-secondary" id="theme-toggle" data-theme-toggle>Switch theme</button>`; use the page's existing secondary button class.

`static/style.css`: add after the base `:root` block. Values below satisfy the AA test (adjust only if the test says otherwise):

```css
:root[data-theme="light"] {
    color-scheme: light;
    --bg:            #f4f5f7;
    --bg-elev:       #ffffff;
    --bg-elev-2:     #eceef2;
    --line:          rgba(15,23,42,0.10);
    --line-strong:   rgba(15,23,42,0.20);
    --ink:           #14171c;
    --ink-2:         #414854;
    --ink-3:         #5a616c;
    --leader:        #7d5f14;
    --leader-soft:   rgba(125,95,20,0.12);
    --pos:           #2b6f31;
    --neg:           #a83232;
    --link:          #1f4fc0;
    --primary-hover: #163f9e;
    --warn:          #86540f;
    --active-pulse:  rgba(31,79,192,0.35);
    --glass-bg:      rgba(255,255,255,0.88);
}
```

Then fix the hard-coded colors that do not use tokens. Run `grep -nE "rgba\(255, ?255, ?255|#fff\b|#ffffff|#000\b|rgba\(0, ?0, ?0" static/style.css`, load `/wins-pool/<year>`, `/player/<id>`, `/draft` and `/admin` at 1280px and 390px in each theme, and add targeted overrides of the form `:root[data-theme="light"] .selector { ... }` only for elements that are unreadable or invisible (for example `.glass-bg`, card hover tints, `backdrop-filter` blur strength). Do not edit existing dark rules.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_theme.py tests/test_auth_guard.py tests/test_player_page.py -q` -> PASS. Browser check (both widths, both themes): no flash of dark theme on reload with light stored, toggle persists across reload and across two tabs, drawer button works at 390px, sign-in screen readable in light.
- [ ] **Step 5: Commit**

```bash
git add static/js/theme_init.js static/js/auth_service.js static/js/main.js static/style.css templates/base.html templates/player_profile.html tests/test_theme.py
git commit -m "feat: light/dark theme with persisted user preference (#107)"
```

---

### Task 9: Docs and full-suite verification

**Files:**
- Modify: `docs/api_endpoints.md`, `docs/frontend.md`, `docs/architecture.md` (only if it describes cache invalidation; check with `grep -n "invalidat\|signal" docs/architecture.md`), `CLAUDE.md`

- [ ] **Step 1: Update docs**
  - `docs/api_endpoints.md`: document `GET /api/pool/unpaid` (params, payload keys, the leak invariant, stage meaning), the new `unpaid_visibility`/`payment_note` fields on `GET/POST /api/admin/pool/config` (`unpaidVisibility` snake_case inner keys, `paymentNote`, 422 rules), and `active_season` on `GET /api/admin/seasons` (~line 228).
  - `docs/frontend.md`: correct the stale CSS token table to the real tokens, add a "Theme" section (`theme_init.js`, `:root[data-theme="light"]`, `STORAGE_KEYS.THEME`, `data-theme-toggle`, sign-out clears the theme), add `STORAGE_KEYS.THEME` to the `auth_service.js` row, and document `unpaid_notice.js` and the Pool tab controls.
  - `CLAUDE.md`: add `THEME` to the `STORAGE_KEYS` list; add `/api/pool/unpaid` and the config fields under "Newer API endpoints" (state that `/api/pool/status` is unchanged); note `_invalidate_static()` beside the cache rules; note `active_season` on admin seasons; note that `current_played_week` (not `get_latest_season_and_week`) drives the unpaid stage.
  - Mark every checkbox in this plan as done.
- [ ] **Step 2: Full suite**

Run: `python -m pytest tests/ -n auto -q 2>&1 | tail -25`
Expected: all pass. Note the repo documents 34 environment failures only in a checkout without `.local_db/`; this checkout has it. Report any failure by name, including pre-existing ones, and confirm `git status` shows no changes under `.local_db/` (the session guard also enforces this).

- [ ] **Step 3: Targeted e2e (if the e2e env vars are set)**

Run: `python -m pytest tests_e2e/test_nav_parity.py -v`
Expected: PASS (the drawer gained a button, not a link). Skip with a note if `E2E_TEST_PLAYER_IDS` is unset.

- [ ] **Step 4: Refresh the graph and commit**

```bash
graphify update .
git add docs CLAUDE.md docs/superpowers/plans/2026-10-03-unpaid-theme-cache-hardening.md
git commit -m "docs: document unpaid visibility, theme, cache signals and admin seasons"
```

---

## Self-Review

- **Spec coverage:** Unpaid R1 (Tasks 4, 6), R2 (5), R3 (7), R4 (7); config defaults and validation (4); `/pool/status` untouched (5, test). Theme requirements 1-4 (8). B1 (1), B4 (2). 4.4, 5.4, 5.7, 5.8 (3). Docs (9). R5 and the chip text change are explicitly out of scope (Deviation 7).
- **Placeholder scan:** none open. The one-line `...` comments inside Task 3c/5 snippets are prose notes about unchanged surrounding code, not missing steps.
- **Type consistency:** `clean_unpaid_visibility`, `get_unpaid_visibility`, `get_payment_note`, `compute_unpaid_stage`, `current_played_week`, `build_unpaid_payload` signatures are identical wherever they appear; payload keys (`enabled, stage, week, amount, unpaid, me_unpaid, payment_note, admin_view`) match between Tasks 5, 7 and the docs; `WinsPoolTheme.{get,set,toggle,init,KEY}` match between Task 8's tests and implementation.
- **Review Focus:** each line maps to a test: leak and disabled (Task 5 `TestBuildUnpaidPayload`), no rows / NaN paid / non-member (Task 5), invalid weeks (Task 4 `TestCleanUnpaidVisibility`), storage unavailable and invalid theme (Task 8 harness), active season absent or failing (Task 2), missing `week` column (Task 3a).
