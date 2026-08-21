# Quick Wins Batch — Issues #14, #15, #19, #20, #21, #25, #38

**Date:** 2026-05-23  
**Issues:** #14, #15, #19, #20, #21, #25, #38  
**Effort:** S×3, M×4  
**Approach:** Option C — category batches, 3 commits

---

## Overview

Seven codebase-audit issues resolved in four ordered batches. Each batch is independently deployable. Security and correctness work is completed before performance work to avoid compounding risk.

---

## Batch 1 — Instant Close: #15

**Action:** Close GitHub issue #15 with no code changes.  
**Reason:** JWT session service tests (`tests/test_session_service.py`, 12 tests) were written as part of the #11/#12/#13 security fix session. The work is already committed and passing in CI.

---

## Batch 2 — Cleanup + Validation: #25, #38

### #25 Dead Code Removal

Remove four inert artifacts. No behavior change; verify test suite still passes after.

| File | Artifact | Action |
|---|---|---|
| `main.py` | `import logging` | Delete — logger never called in this file |
| `services/ai_service.py` | `config = {}` | Delete — set but never read |
| `services/live_score_service.py` | `repo_to_espn` dict | Delete — populated but never referenced |
| `services/draft_service.py` | Duplicate `import time` | Remove second occurrence |

### #38 Subprocess Hardening

Three changes to the subprocess call site and API path params.

**Subprocess (wherever `subprocess.run` is called in scripts or services):**
- Add `stderr=subprocess.PIPE` (captured, not leaked) and log with `logger.error()` on non-zero return code
- Add `timeout=300` to prevent indefinite hangs

**API path param bounds (`routes/api_routes.py`):**
- `season`: `conint(ge=2000, le=2030)` — rejects obviously invalid seasons with HTTP 422
- `week`: `conint(ge=1, le=22)` — rejects out-of-range weeks with HTTP 422

**TDD (test-first):**
1. Write tests asserting `GET /api/progress/1800/5` → 422 and `GET /api/progress/2024/99` → 422
2. Watch them fail (params accept any int today)
3. Add `conint` bounds
4. Watch tests go green

---

## Batch 3 — Security + DRY: #14, #21

### #21 Error Response Helpers

**New file:** `services/response_helpers.py`

```python
def error_response(message: str, status_code: int = 400) -> JSONResponse
def server_error(message: str = "An internal error occurred.") -> JSONResponse
def unauthorized(message: str = "Unauthorized.") -> JSONResponse
def not_found(message: str = "Not found.") -> JSONResponse
```

Each returns `JSONResponse({"error": message}, status_code=<code>)`.

**Rollout:** Replace all `JSONResponse(content={"error": ...})` call sites in:
- `routes/api_routes.py`
- `routes/standings_routes.py`
- `routes/history_routes.py`
- `routes/draft_routes.py`

Estimated 50+ sites. Search pattern: `JSONResponse(content={"error"`.

**TDD:** One test file `tests/test_response_helpers.py`, 4 tests (one per helper), watched fail before `response_helpers.py` exists.

### #14 Security Fixes

Three independent sub-tasks, all in Batch 3:

#### 14a — MFA Code Hashing

**Location:** `services/db_service.py` — wherever MFA codes are stored and verified.

- **Store:** `hashlib.sha256(code.encode()).hexdigest()` instead of plaintext
- **Verify:** hash the submitted code and compare to stored hash

**TDD:**
1. Test: stored value != plaintext code → watch fail (currently stores plaintext)
2. Test: correct plaintext code still passes verification → watch fail
3. Implement hashing → both green

#### 14b — Traceback Suppression

**Pattern to eliminate:**
```python
traceback.print_exc()
str(e)  # returned to user in error response
```

**Replacement:**
```python
logger.exception("Context: what was being attempted")
# return generic message to user
```

Grep targets: `traceback.print_exc`, `except.*str(e)`.  
No TDD — logging-only change; existing tests still pass.

#### 14c — CORS Configuration

**Location:** `main.py`

Add `CORSMiddleware` with:
- `allow_origins`: read from `CORS_ORIGINS` env var (comma-separated list)
- Dev default: `["http://localhost:8000"]`
- Production: set `CORS_ORIGINS` in Cloud Run env vars to the actual domain
- Never use wildcard `*`

No TDD — config addition; existing integration tests confirm no regression.

---

## Batch 4 — Performance: #19, #20

### #19 Vectorize Standings Hot Paths

**Location:** `services/analysis_service.py`

Three functions replaced with vectorized pandas equivalents:

| Function | Current | Replacement |
|---|---|---|
| `player_winsbyWeek()` | Row-by-row Python loop | `pd.melt` + `groupby` + `pivot_table` |
| `get_remaining_games()` | Loop with conditionals | Vectorized boolean mask on games DataFrame |
| `player_winlossmatrix()` | Nested loop | `pd.crosstab` or `pivot_table` |

**TDD per function:**
1. Write test with small fixture DataFrame asserting output shape and key values
2. Run — passes with existing loop implementation (green baseline)
3. Replace loop with vectorized version
4. Re-run — must still be green (correctness guard)

The tests serve as regression protection; performance improvement is validated by inspection.

### #20 Optimize `get_enriched_schedule()`

**Location:** `services/analysis_service.py`

Two changes:

1. **Pre-join draft_results + players once** at the top of the function into a single lookup dict (`{team_abbr: player_name}`), replacing repeated per-game lookups inside the loop.

2. **`load_data_season(year)` helper** in `services/data_service.py`: accepts a `year` argument and returns only the season slice, avoiding 5 sequential full-dataset `load_data()` calls in the function.

**TDD:**
1. Write test calling `get_enriched_schedule()` with a fixture dataset, asserting output is identical to expected (correctness baseline)
2. Watch test pass with current implementation
3. Apply both optimizations
4. Re-run — output must be identical

---

## Testing Strategy

- All logic changes use TDD (test-first, watch fail, implement, watch pass)
- Logging and config changes (traceback suppression, CORS) are covered by regression: existing tests must still pass
- Dead code removal: no new tests; full suite must still pass
- Performance refactors: correctness-first tests written before vectorizing

---

## File Changeset Summary

| File | Changes |
|---|---|
| `main.py` | Remove `import logging`; add `CORSMiddleware` |
| `services/ai_service.py` | Remove `config = {}` |
| `services/live_score_service.py` | Remove `repo_to_espn` dict |
| `services/draft_service.py` | Remove duplicate `import time` |
| `services/response_helpers.py` | **New file** — 4 helper functions |
| `services/db_service.py` | Hash MFA codes; replace tracebacks with logger |
| `services/data_service.py` | Add `load_data_season(year)` helper |
| `services/analysis_service.py` | Vectorize 3 hot-path functions; optimize `get_enriched_schedule()` |
| `routes/api_routes.py` | Add `conint` bounds to `season`/`week`; replace JSONResponse call sites |
| `routes/standings_routes.py` | Replace JSONResponse call sites |
| `routes/history_routes.py` | Replace JSONResponse call sites |
| `routes/draft_routes.py` | Replace JSONResponse call sites |
| `tests/test_response_helpers.py` | **New file** — 4 tests |
| `tests/test_api_validation.py` | **New file** — bounds tests for season/week params |
| `tests/test_mfa_hashing.py` | **New file** — 2 MFA tests |

---

## Deployment

Each batch is a separate commit. After Batch 3 passes CI, deploy to Cloud Run before starting Batch 4. Batch 4 (performance) carries the highest refactor risk and should be confirmed green locally before deploying.

**New env var for production:** `CORS_ORIGINS=https://winspool-1045965963135.us-east1.run.app`
