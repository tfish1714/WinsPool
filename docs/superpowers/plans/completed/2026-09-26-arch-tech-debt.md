# Architecture and Tech Debt Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the repo one canonical team-abbreviation map and normalizer (GitHub #101), move player analytics loading out of a route module into the analysis service (#56), and make the five Firestore scripts reuse `services.db_service.get_db()` instead of each re-implementing credential decoding (#64).

**Architecture:** Three independent refactors, each behavior-preserving and each with characterization tests written first. (1) `TEAM_ABBR_MAP` lives in `services/constants.py`; `services.utils.normalize_team_abbr` is the only normalizer; `nn_feature_engine._normalize_team` remains as a thin alias so nothing that imports it breaks, but all route and script call sites move to the canonical function. (2) `get_player_analytics_data(player_id)` becomes a public function in `services/analysis_service.py`. (3) Each script keeps its existing init function name (tests patch these names) but the body delegates to `get_db()`.

**Tech Stack:** Python 3.11, FastAPI, pandas, pytest, `unittest.mock`.

**Spec:** GitHub issues #101, #56, #64 and the run directive; there is no design doc. Facts verified against the code on 2026-09-26 are recorded in the task notes below.

**Worktree / branch:** `worktree-arch-tech-debt`, created with `git worktree add .claude/worktrees/arch-tech-debt -b worktree-arch-tech-debt main` from the main checkout. `docs/` is gitignored, so this plan lives only in the main checkout; implementers receive task text from the controller.

## Global Constraints

- No emojis in code, comments, docs, tests or commit messages.
- Zero-deletion policy: no feature or test is removed. Tests that patch a moved symbol are MODIFIED to patch its new location (same assertions); no test file or test function is deleted. Public/private names that other code imports (`_normalize_team`, script init function names) stay defined.
- Behavior preserving: no change to any HTTP response, prediction value, or Firestore write. The only deliberate behavior change is that `normalize_team_abbr` now also maps `OAK->LV`, `SD->LAC`, `STL->LA`, strips whitespace, and passes non-string values (NaN, None) through unchanged, because it absorbs `nn_feature_engine._normalize_team`.
- `get_team_logo_url`'s `{"LA": "LAR", "WAS": "WSH"}` mapping is the INVERSE direction (canonical to ESPN logo file names) and is NOT a duplicate of the normalization map; leave it.
- Scripts that write to Firestore must keep forcing `USE_LOCAL_DATA=False` (repo CLAUDE.md gotcha): `get_db()` returns `None` whenever that env var is true.
- Run `pytest tests/ -q` at the end of every task; compare any failures to a clean `main` run (known env-failing tests are in the `reference_worktree_workflow_gotchas` memory).

## Review Focus

- `nn_feature_engine._normalize_team` currently returns non-string input unchanged (pandas NaN in `.apply`); the canonical function must do the same instead of raising `AttributeError`.
- `" kc "` and lowercase input must normalize to `"KC"` (the NN feature path stripped whitespace; `normalize_team_abbr` did not).
- Historical relocations: `OAK`, `SD`, `STL` must map to `LV`, `LAC`, `LA` in every path that used to only map in the NN feature engine (live scores, injury service, consensus scripts gain this; that is intended).
- Moving `_get_player_analytics_data` changes where `load_data` is looked up: tests that patched `routes.history_routes.load_data` for that code path must patch `services.analysis_service.load_data`, and the `/api/player/{id}/analytics` route test must patch the function where `api_routes` looks it up.
- Import cycle: `services/analysis_service.py` will import `services.data_service`; verify `import services.analysis_service` and `import services.data_service` each succeed in a fresh interpreter.
- A script run with no credentials must still fail loudly (exit code 1 or the original exception), not continue with `db=None`.

---

### Task 1: Canonical team abbreviation map and normalizer (#101)

**Files:**
- Modify: `services/constants.py` (add `TEAM_ABBR_MAP`)
- Modify: `services/utils.py:100-106` (`normalize_team_abbr`)
- Modify: `services/nn_feature_engine.py:113-115` and `:160-163` (`TEAM_ABBR_MAP`, `_normalize_team`)
- Modify (call sites): `routes/admin_routes.py:524`, `routes/api_routes.py:97,195,405`, `scripts/backfill_schedule_predictions.py:47`, `scripts/cache_builder.py:54-56`, `scripts/compare_models.py:34`, `scripts/generate_weekly_predictions.py:36-40`, `scripts/predict_season.py:35-38`, `scripts/walk_forward_validate.py`, and any other file `git grep -n "_normalize_team"` reports outside `services/nn_feature_engine.py` and `tests/`
- Create: `tests/test_team_abbr_consolidation.py`
- Modify: `tests/test_utils.py` (extend, do not alter existing assertions)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `services.constants.TEAM_ABBR_MAP: dict[str, str]`; `services.utils.normalize_team_abbr(abbr)` (accepts `str` or any non-str, returns normalized upper-case `str` or the non-str value unchanged); `services.nn_feature_engine._normalize_team` (kept, delegates to `normalize_team_abbr`); `services.nn_feature_engine.TEAM_ABBR_MAP` (kept, is the same object as the constants map).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_team_abbr_consolidation.py`:

```python
"""One canonical team-abbreviation map (GitHub #101)."""
import pathlib
import pytest

from services.constants import TEAM_ABBR_MAP
from services.utils import normalize_team_abbr

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("raw,expected", [
    ("LAR", "LA"), ("WSH", "WAS"), ("JAC", "JAX"),
    ("OAK", "LV"), ("SD", "LAC"), ("STL", "LA"),
    ("buf", "BUF"), (" kc ", "KC"), ("LA", "LA"), ("LV", "LV"), ("", ""),
])
def test_normalize_team_abbr_canonical(raw, expected):
    assert normalize_team_abbr(raw) == expected


def test_normalize_team_abbr_passes_non_strings_through():
    nan = float("nan")
    assert normalize_team_abbr(nan) is nan
    assert normalize_team_abbr(None) is None


def test_canonical_map_contents():
    assert TEAM_ABBR_MAP == {
        "LAR": "LA", "WSH": "WAS", "JAC": "JAX",
        "OAK": "LV", "SD": "LAC", "STL": "LA",
    }


def test_nn_feature_engine_reuses_canonical_map_and_function():
    import services.nn_feature_engine as nfe
    assert nfe.TEAM_ABBR_MAP is TEAM_ABBR_MAP
    for raw in ("OAK", "wsh", " kc ", "LAR"):
        assert nfe._normalize_team(raw) == normalize_team_abbr(raw)
    nan = float("nan")
    assert nfe._normalize_team(nan) is nan


def test_no_second_abbreviation_dict_outside_constants():
    offenders = []
    for folder in ("services", "routes", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "constants.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if '"JAC": "JAX"' in text or "'JAC': 'JAX'" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_call_sites_import_canonical_function_not_private_alias():
    offenders = []
    for folder in ("routes", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "_normalize_team" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
```

Append to `tests/test_utils.py` (after the existing `test_normalize_team_abbr`, existing assertions untouched):

```python
def test_normalize_team_abbr_handles_relocated_franchises():
    assert normalize_team_abbr("OAK") == "LV"
    assert normalize_team_abbr("SD") == "LAC"
    assert normalize_team_abbr("STL") == "LA"
    assert normalize_team_abbr(" wsh ") == "WAS"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_team_abbr_consolidation.py tests/test_utils.py -v`
Expected: FAIL (`ImportError: cannot import name 'TEAM_ABBR_MAP' from 'services.constants'`).

- [ ] **Step 3: Implement the canonical map and normalizer**

1. `services/constants.py`: append (keep existing content):

```python
# Canonical team abbreviation normalization: source-specific and historical
# abbreviations -> the identifiers used throughout the repo. Single source of
# truth; services.utils.normalize_team_abbr is the only function that applies it.
TEAM_ABBR_MAP = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA",
}
```

2. `services/utils.py`: add `from services.constants import TEAM_ABBR_MAP` with the other imports (verify `services.constants` does not import `services.utils`; it has no imports today). Replace `normalize_team_abbr`:

```python
def normalize_team_abbr(abbr: str) -> str:
    """Maps various source abbreviations to the canonical ones used in the repo.

    Non-string input (pandas NaN, None) is returned unchanged so this can be
    applied over a DataFrame column without raising.
    """
    if not isinstance(abbr, str):
        return abbr
    cleaned = abbr.upper().strip()
    return TEAM_ABBR_MAP.get(cleaned, cleaned)
```

3. `services/nn_feature_engine.py`: replace the local dict with `from services.constants import TEAM_ABBR_MAP` (keeps `nfe.TEAM_ABBR_MAP` importable, same object) and a `from services.utils import normalize_team_abbr` import; replace the `_normalize_team` body with `return normalize_team_abbr(abbr)` and a docstring saying it is a backward-compatible alias. Leave the comment marker "Canonical team abbreviation normalization" pointing at constants. Internal uses inside this module may keep calling `_normalize_team`.

- [ ] **Step 4: Move every call site to the canonical function**

Run `git grep -n "_normalize_team"`. For each file under `routes/` and `scripts/` (and any other non-test, non-`nn_feature_engine.py` file):
- Replace the import of `_normalize_team` (from `services.nn_feature_engine`, including multi-line import lists in `scripts/cache_builder.py`, `scripts/generate_weekly_predictions.py`, `scripts/predict_season.py`) with `from services.utils import normalize_team_abbr` (add the import; remove `_normalize_team` from the nn_feature_engine import list, keeping the other names).
- Replace every use of the identifier `_normalize_team` with `normalize_team_abbr` (including `.apply(_normalize_team)` and `.map(_normalize_team)`).
- If a file already imports `normalize_team_abbr` from `services.utils`, do not import it twice.

Also `git grep -n '"LAR"'` across `services/`, `routes/`, `scripts/` and report any OTHER inline abbreviation maps found; consolidate one only if it is a plain source-abbreviation-to-canonical dict (do not touch the logo mapping).

Tests under `tests/` that reference `_normalize_team` keep working through the alias; do not edit them.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_team_abbr_consolidation.py tests/test_utils.py tests/test_live_score_service.py -v` then `pytest tests/ -q`
Expected: new tests PASS; full suite has no new failures. Also run `python -c "import scripts.cache_builder, scripts.predict_season, scripts.generate_weekly_predictions, scripts.backfill_schedule_predictions, scripts.compare_models, scripts.walk_forward_validate"` (with the venv) to confirm every edited script still imports; if an import fails for a reason unrelated to this change (missing optional ML dependency), confirm the same failure on `main` and note it.

- [ ] **Step 6: Commit**

```bash
git add -A services routes scripts tests
git commit -m "refactor: single canonical TEAM_ABBR_MAP and normalize_team_abbr (#101)"
```

---

### Task 2: Move player analytics loading into the analysis service (#56)

**Files:**
- Modify: `services/analysis_service.py` (add imports and `get_player_analytics_data`)
- Modify: `routes/history_routes.py:193-212` (remove the private helper, call the service), imports
- Modify: `routes/api_routes.py:20` and `:377` (import and call the service function)
- Modify: `tests/test_player_analytics.py` (route test patch target), `tests/test_historical_projection_routes.py` (call site, patch target)
- Create: (tests are added to the two existing test files above)

**Interfaces:**
- Consumes: existing `services.analysis_service.get_player_analytics(player_id, all_draft_results, standings_master, players, preseason_preds, active_season=...)`; `services.data_service.load_data`, `get_active_season`, `get_season_projection_legacy_shape`.
- Produces: `services.analysis_service.get_player_analytics_data(player_id: int) -> dict | None` (returns `None` when the player does not exist).

- [ ] **Step 1: Write the failing tests**

In `tests/test_historical_projection_routes.py`, add next to the existing test (which currently calls `history_routes._get_player_analytics_data(1)` under `patch.object(history_routes, "load_data", ...)`) a new test for the service function:

```python
def test_service_get_player_analytics_data_uses_consensus_projection(deleted_preseason_rows):
    import services.analysis_service as analysis_service

    with patch.object(analysis_service, "load_data", return_value=_mock_load_data()):
        analytics = analysis_service.get_player_analytics_data(1)

    pick = analytics["seasons"][0]["picks"][0]
    assert pick["team"] == "KC"
    assert pick["projectedWins"] == 9.5
    assert pick["vsProjected"] == 1.5


def test_service_get_player_analytics_data_returns_none_for_unknown_player(deleted_preseason_rows):
    import services.analysis_service as analysis_service

    with patch.object(analysis_service, "load_data", return_value=_mock_load_data()):
        assert analysis_service.get_player_analytics_data(999) is None


def test_history_routes_no_longer_defines_private_analytics_helper():
    import routes.history_routes as history_routes
    import routes.api_routes as api_routes
    assert not hasattr(history_routes, "_get_player_analytics_data")
    assert not hasattr(api_routes, "_get_player_analytics_data")
```

Reuse whatever fixtures the neighboring test uses (`deleted_preseason_rows`); if the fixture patches `get_season_projection_legacy_shape` on `routes.history_routes`, the new tests must patch it on `services.analysis_service` instead (see Step 3).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_historical_projection_routes.py -v`
Expected: the three new tests FAIL (`AttributeError`/attribute exists); existing tests PASS.

- [ ] **Step 3: Implement**

1. `services/analysis_service.py`: add near the other imports
   `from services.data_service import load_data, get_active_season, get_season_projection_legacy_shape`
   and, if a cycle appears when importing `services.data_service` in a fresh interpreter, move that import inside the function body instead (then tests must patch `services.data_service.*`). Add, after `get_player_analytics`:

```python
def get_player_analytics_data(player_id: int) -> Optional[Dict[str, Any]]:
    """Load and compute analytics for one player. Returns None if player not found."""
    standings_master, _, all_games, players, _, all_draft_results, rules = load_data()
    player_row = players[players["playerId"] == player_id] if not players.empty else pd.DataFrame()
    if player_row.empty:
        return None
    player_seasons = (
        all_draft_results[all_draft_results["playerId"] == player_id]["season"].unique()
        if not all_draft_results.empty else []
    )
    # Resolver, not get_preseason_predictions: these are historical seasons, whose
    # projections live in consensus_projections now. frozen=True: a player's
    # past-season draft value must not keep moving as the model is retrained.
    preseason_preds = {int(s): get_season_projection_legacy_shape(int(s), frozen=True) for s in player_seasons}
    active_season = get_active_season(all_games, all_draft_results, rules)
    return get_player_analytics(
        player_id, all_draft_results, standings_master, players, preseason_preds,
        active_season=active_season,
    )
```
   (The body is the existing route helper moved verbatim; the only change is the final call, which is now a direct call within the module.)

2. `routes/history_routes.py`: delete `_get_player_analytics_data` and change its caller in `player_profile` to `analytics = analysis.get_player_analytics_data(player_id)` (the module already has `import services.analysis_service as analysis`). Keep `load_data`, `get_active_season`, `get_available_years` imports; remove `get_season_projection_legacy_shape` from the import line ONLY if `git grep -n get_season_projection_legacy_shape routes/history_routes.py` shows no other use.
3. `routes/api_routes.py`: replace `from routes.history_routes import _get_player_analytics_data` with `from services.analysis_service import get_player_analytics_data` and the call at line ~377 with `get_player_analytics_data(player_id)`.
4. Update existing tests (same assertions, new patch targets):
   - `tests/test_player_analytics.py:180` `@patch("routes.history_routes._get_player_analytics_data")` becomes `@patch("routes.api_routes.get_player_analytics_data")` (it exercises `/api/player/1/analytics`, served by `api_routes`).
   - `tests/test_historical_projection_routes.py` existing test that calls `history_routes._get_player_analytics_data(1)`: call `analysis_service.get_player_analytics_data(1)` under `patch.object(analysis_service, "load_data", ...)`; the `deleted_preseason_rows` fixture and any `patch("routes.history_routes.load_data" / "get_season_projection_legacy_shape")` used for this code path must target `services.analysis_service`. Route-level tests for `/history/player/{id}` that still go through `history_routes.player_profile` must patch `services.analysis_service.load_data` and `services.analysis_service.get_season_projection_legacy_shape` as well (`test_player_profile_page_returns_404_for_unknown_player` in `tests/test_player_analytics.py` patches `routes.history_routes.load_data`, which no longer feeds the analytics path: add the analysis-service patches, keep the existing ones).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_historical_projection_routes.py tests/test_player_analytics.py -v` then `pytest tests/ -q`
Expected: PASS, no new failures. Also run `python -c "import services.analysis_service"` and `python -c "import services.data_service"` in fresh interpreters.

- [ ] **Step 5: Commit**

```bash
git add services/analysis_service.py routes/history_routes.py routes/api_routes.py tests
git commit -m "refactor: move player analytics loading into analysis_service (#56)"
```

---

### Task 3: Scripts reuse `db_service.get_db()` (#64)

**Files:**
- Modify: `scripts/daily_nfl_sync.py:1-34` (`initialize_firebase`)
- Modify: `scripts/backfill_schedule_predictions.py:184-207` (`_init_firestore`)
- Modify: `scripts/predict_season.py:91-114` (`_init_firebase`)
- Modify: `scripts/generate_weekly_predictions.py:115-138` (`_init_firebase`)
- Modify: `scripts/upload_configfiles.py:20-49` (`_init_firebase`)
- Create: `tests/test_script_firebase_init.py`

**Interfaces:**
- Consumes: `services.db_service.get_db() -> Firestore client | None` (returns `None` when `USE_LOCAL_DATA` is true or no credentials exist).
- Produces: each script keeps its existing function name and zero-argument signature, returning a Firestore client and never returning `None`. On missing credentials: `daily_nfl_sync`, `predict_season`, `generate_weekly_predictions`, `upload_configfiles` exit with status 1 after the same style of message they print today; `backfill_schedule_predictions._init_firestore` raises `FileNotFoundError` as it does today.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_script_firebase_init.py`:

```python
"""Scripts share services.db_service.get_db() instead of decoding credentials themselves (#64)."""
import importlib
import os
import pathlib
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (module, init function name, exception raised when no client is available)
CASES = [
    ("scripts.daily_nfl_sync", "initialize_firebase", SystemExit),
    ("scripts.backfill_schedule_predictions", "_init_firestore", FileNotFoundError),
    ("scripts.predict_season", "_init_firebase", SystemExit),
    ("scripts.generate_weekly_predictions", "_init_firebase", SystemExit),
    ("scripts.upload_configfiles", "_init_firebase", SystemExit),
]


@pytest.mark.parametrize("module_name,_func,_exc", CASES)
def test_script_source_has_no_duplicated_credential_bootstrap(module_name, _func, _exc):
    path = ROOT / (module_name.replace(".", "/") + ".py")
    text = path.read_text(encoding="utf-8")
    assert "initialize_app" not in text
    assert "b64decode" not in text


@pytest.mark.parametrize("module_name,func,_exc", CASES)
def test_init_returns_shared_db_client_and_forces_remote_mode(monkeypatch, module_name, func, _exc):
    mod = importlib.import_module(module_name)
    fake_db = MagicMock(name="db")
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    assert getattr(mod, func)() is fake_db
    assert os.environ["USE_LOCAL_DATA"].lower() == "false"


@pytest.mark.parametrize("module_name,func,exc", CASES)
def test_init_fails_loudly_without_credentials(monkeypatch, module_name, func, exc):
    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod, "get_db", lambda: None)
    with pytest.raises(exc):
        getattr(mod, func)()
```

If importing one of the script modules fails in this environment for a reason unrelated to this change (for example a missing optional ML dependency), confirm the same import failure on `main`, keep the source-scan test for that script, and mark only its two behavioral cases with `pytest.importorskip` on the missing dependency; do not drop the case silently.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_script_firebase_init.py -v`
Expected: FAIL (`initialize_app` still in sources; scripts have no `get_db` attribute).

- [ ] **Step 3: Implement in each script**

In every script add `from services.db_service import get_db` after that script's existing `sys.path` setup and other `services` imports (scripts already import from `services`; place it with them). Remove the now-unused `import firebase_admin` / `from firebase_admin import credentials, firestore` lines ONLY after `git grep -n "firestore\.\|credentials\.\|firebase_admin\." scripts/<name>.py` shows they are unused elsewhere in that file (for example `firestore.SERVER_TIMESTAMP` or `firestore.client()` used outside the init function must keep an import; keep only what is still used). Replace the body of each init function:

`scripts/daily_nfl_sync.py`, `predict_season.py`, `generate_weekly_predictions.py`, `upload_configfiles.py` (keep each function's existing name and docstring style; use that script's existing message and exit style: `print(...)` + `sys.exit(1)`, or `log.error(...)` + `sys.exit(1)` in `upload_configfiles.py`):

```python
def initialize_firebase():
    """Return the shared Firestore client from services.db_service.get_db()."""
    # get_db() returns None whenever USE_LOCAL_DATA is true (repo CLAUDE.md
    # gotcha), so a Firestore-writing script must force it off first.
    os.environ["USE_LOCAL_DATA"] = "False"
    db = get_db()
    if db is None:
        print("ERROR: No FIREBASE_CREDENTIALS env var and no firebase_credentials.json found.")
        sys.exit(1)
    return db
```

`scripts/backfill_schedule_predictions.py` `_init_firestore` (keeps raising `FileNotFoundError`, and returns the client):

```python
def _init_firestore():
    os.environ["USE_LOCAL_DATA"] = "False"
    db = get_db()
    if db is None:
        raise FileNotFoundError(
            "No Firebase credentials found. Set FIREBASE_CREDENTIALS env var "
            "or place firebase_credentials.json in the project root."
        )
    return db
```

Confirm `os` and `sys` are imported in each edited file. Existing `os.environ["USE_LOCAL_DATA"] = "False"` lines elsewhere in these scripts stay (redundant but harmless; do not delete).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_script_firebase_init.py tests/test_daily_nfl_sync.py -v` then `pytest tests/ -q`
Expected: PASS; `tests/test_daily_nfl_sync.py` still patches `daily_nfl_sync.initialize_firebase` successfully (name preserved). Also run `python -c "import scripts.daily_nfl_sync, scripts.backfill_schedule_predictions, scripts.predict_season, scripts.generate_weekly_predictions, scripts.upload_configfiles"` to confirm they import.

- [ ] **Step 5: Commit**

```bash
git add scripts tests/test_script_firebase_init.py
git commit -m "refactor: scripts reuse db_service.get_db() for Firebase init (#64)"
```

---

## Final verification (after Task 3, before merge)

- [ ] Run `pytest tests/ -q` in the worktree and compare failures against the same run on `main`. Any new failure blocks the merge.
- [ ] Whole-branch code review against issues #101, #56, #64, checking specifically: no remaining `_normalize_team` in `routes/` or `scripts/`; no `initialize_app` or `b64decode` in the five scripts; no test deleted (`git diff main --stat` shows no removed test files and `git diff main | grep '^-def test_'` returns nothing).
- [ ] Report what was intentionally left: `get_team_logo_url`'s inverse mapping, script-local `USE_LOCAL_DATA` assignments, and the `nn_feature_engine._normalize_team` alias.
