# High Severity Test Coverage — Issues #16 and #17

**Date:** 2026-05-24  
**Issues:** #16 (admin routes + db_service write coverage), #17 (ML prediction services)  
**Effort:** M × 2  
**Priority:** High — implement now  

---

## Overview

Two high-severity gaps in test coverage. Both follow TDD: write failing tests first, watch them fail, implement any fixes needed, watch them pass.

**No production code should need to change** — these are coverage gaps, not bugs. If a test reveals a real bug while being written, fix it separately.

---

## Issue #16 — Admin Routes + db_service Write Operations

### Scope

**New file:** `tests/test_admin_routes.py`  
**Expand:** `tests/test_db.py` (currently 4 tests covering only read + basic add)

### Admin Routes Test Strategy

All admin endpoints require `require_admin` — every test class should include an **auth guard** test asserting 401/403 when called with no token or a non-admin user token. Use the `admin_token` and `auth_token` fixtures from conftest.

The `TestClient(app)` pattern from `test_api.py` and `test_auth.py` is the established pattern — follow it.

For any endpoint that calls db_service write functions, patch the specific write function at the call site using `unittest.mock.patch`. Do not use `mock_firestore` for write tests — patch the function directly so the assertion is precise.

#### Endpoints to cover

| Endpoint | Happy path | Auth guard | Error path |
|---|---|---|---|
| `POST /admin/new_season` | 200 + calls `add_draft_order` + `add_draft_rule` for each player | 401/403 user token | 400 season already exists |
| `POST /admin/delete_season` | 200 + calls `delete_season_data` | 401/403 user token | — |
| `POST /admin/reset_draft` | 200 + calls `delete_draft_results_for_season` | 401/403 no token | — |
| `POST /admin/create_player` | 200 + returns new player ID | 401/403 user token | 400 duplicate email |
| `POST /admin/reset_password` | 200 + calls `update_player_credentials` | 401/403 no token | 404 player not found |
| `POST /admin/set_temp_password` | 200 + calls `update_player_profile` with temp flag | 401/403 no token | — |
| `POST /admin/members/paid` | 200 + calls `set_member_paid` | 401/403 user token | — |

Pattern for each test:

```python
def test_new_season_happy_path(admin_token):
    with patch("routes.admin_routes.add_draft_order") as mock_order, \
         patch("routes.admin_routes.add_draft_rule") as mock_rule, \
         patch("routes.admin_routes.get_collection_df", return_value=pd.DataFrame()):
        resp = client.post("/admin/new_season",
                           json={"playerId": 1, "season": 2026, "playerIds": [1, 2, 3]},
                           headers={"Authorization": admin_token})
    assert resp.status_code == 200
    assert mock_order.called

def test_new_season_requires_admin(auth_token):
    resp = client.post("/admin/new_season",
                       json={"playerId": 1, "season": 2026, "playerIds": [1]},
                       headers={"Authorization": auth_token})
    assert resp.status_code in (401, 403)
```

### db_service Write Tests (expand test_db.py)

Target functions (currently untested):

| Function | What to assert |
|---|---|
| `delete_draft_results_for_season(season)` | Calls `get_collection_df`, filters to season, calls `_save_df_to_local`; all storage mocked in-memory |
| `delete_season_data(season)` | Calls both `delete_draft_results_for_season` and cleans up standings/game data; storage mocked |
| `increment_failed_setup_attempts(player_id, new_count, lockout_until)` | Calls `update_player_profile` with correct keys including `lockout_until` when provided |
| `set_member_paid(season, player_id, paid)` | Updates the `paid` flag in draft_results for that player+season; returns `True` on success |

Existing `test_add_player_integrity` is the reference pattern for db_service mocking — all storage calls (`get_collection_df`, `_save_df_to_local`, `clear_data_cache`, `signal_data_update`) must be patched.

---

## Issue #17 — ML Prediction Service Tests

### Scope

**New file:** `tests/test_xgb_prediction.py`  
**New file:** `tests/test_lr_prediction.py`  
**Existing:** `tests/test_nn_prediction_service.py` already covers `NNPredictionService` model architecture — no changes needed there.

### Test Design Principles

- **Never read from `rawdata/`** — all tests use fixture DataFrames.
- **Never load real model files** — train a fresh in-memory model using the service's own `train()` method on a minimal fixture DataFrame (XGB and LR train in <1s on 40 rows).
- Both services have identical public API (`predict_game`, `feature_importance`, `load_model`, `save_versioned`) — tests are structurally parallel.

### Fixture: Minimal Feature DataFrame

```python
@pytest.fixture
def feature_table():
    """40-row feature table (20 home wins, 20 losses) for fast in-memory training."""
    from services.nn_feature_engine import FEATURE_COLUMNS
    rng = np.random.default_rng(42)
    df = pd.DataFrame(rng.standard_normal((40, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    df["home_win"] = [1.0] * 20 + [0.0] * 20
    df["season"] = 2024
    df["week"] = list(range(1, 21)) * 2
    return df
```

### XGB Tests (`tests/test_xgb_prediction.py`)

| Test | Assertion |
|---|---|
| `test_predict_game_returns_float_in_unit_interval` | Train on fixture → predict → `isinstance(float)` and `0 ≤ p ≤ 1` |
| `test_predict_game_handles_missing_features` | Call `predict_game` with a dict missing 3 features → no raise, returns float in [0,1] |
| `test_predict_game_untrained_returns_none_or_raises` | Fresh `XGBPredictionService()` before `train()` → returns `None` OR raises `ValueError` (not `AttributeError`) |
| `test_feature_importance_returns_dataframe` | Train → `feature_importance(top_n=5)` → `isinstance(pd.DataFrame)`, len==5, columns include `feature` and `importance` |
| `test_output_bounded_on_random_inputs` | Train → 50 random feature dicts → all predictions in [0,1] |

### LR Tests (`tests/test_lr_prediction.py`)

Same 5 tests, same assertions, `XGBPredictionService` → `LRPredictionService`.

### Architecture Note

Neither XGB nor LR load models at import time. The untrained-behavior test must match whichever behavior the implementation actually has — read the source before writing that specific assertion.

---

## Testing Strategy

- TDD: create test file → run (confirm failures) → tests pass on correct behavior
- Existing `autouse` `mock_env_vars` + `os.environ.setdefault` in conftest.py ensures `USE_LOCAL_DATA=true` automatically
- For admin routes: `TestClient(app)` at module level; tests assert status code + mock was called with correct args
- For ML services: train real in-memory models on the small fixture — no mocking of model internals

---

## File Changeset

| File | Action |
|---|---|
| `tests/test_admin_routes.py` | **Create** — ~80 lines, ~16 tests |
| `tests/test_db.py` | Expand — add 4 write-operation tests (8 total) |
| `tests/test_xgb_prediction.py` | **Create** — ~60 lines, 5 tests |
| `tests/test_lr_prediction.py` | **Create** — ~60 lines, 5 tests |

---

## Completion Criteria

- [ ] `pytest tests/test_admin_routes.py` — all pass
- [ ] `pytest tests/test_db.py` — all 8 tests pass (4 existing + 4 new)
- [ ] `pytest tests/test_xgb_prediction.py` — all pass
- [ ] `pytest tests/test_lr_prediction.py` — all pass
- [ ] `pytest tests/ -q` — full suite green (no new failures)
- [ ] `gh issue close 16` and `gh issue close 17` with explanatory comments
