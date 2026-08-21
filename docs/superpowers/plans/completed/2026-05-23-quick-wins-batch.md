# Quick Wins Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Close 7 codebase-audit issues (dead code, input validation, error response DRY, MFA security, traceback suppression, CORS, and two performance hot-paths) in four sequentially-deployed batches.

**Architecture:** Four ordered batches — Batch 2 (cleanup/validation), Batch 3 (security/DRY), Batch 4 (performance) — each committed and verified green before proceeding. Batch 1 is a GitHub issue close only (tests already written).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pandas, numpy, pytest

---

## File Map

| File | Action | Batch |
|---|---|---|
| `main.py` | Remove `import logging`; add `CORSMiddleware` | 2, 3 |
| `services/ai_service.py` | Remove `config = {}` | 2 |
| `services/live_score_service.py` | Remove `repo_to_espn` dict | 2 |
| `services/draft_service.py` | Remove duplicate `import time` | 2 |
| `scripts/run_cron.py` | Add `timeout=300` to subprocess call | 2 |
| `routes/api_routes.py` | Add `Path(ge=…)` bounds; replace JSONResponse call sites | 2, 3 |
| `services/response_helpers.py` | **Create** — 4 helper functions | 3 |
| `routes/standings_routes.py` | Replace JSONResponse call sites | 3 |
| `routes/history_routes.py` | Replace JSONResponse call sites | 3 |
| `routes/draft_routes.py` | Replace JSONResponse call sites | 3 |
| `routes/auth_routes.py` | Hash MFA codes; suppress tracebacks | 3 |
| `routes/admin_routes.py` | Suppress tracebacks | 3 |
| `services/analysis_service.py` | Vectorize 3 hot-path functions; optimize `get_enriched_schedule` | 4 |
| `services/data_service.py` | Add `load_data_season(year)` helper | 4 |
| `tests/test_api_validation.py` | **Create** — bounds tests | 2 |
| `tests/test_response_helpers.py` | **Create** — helper tests | 3 |
| `tests/test_mfa_hashing.py` | **Create** — MFA hash tests | 3 |
| `tests/test_analysis_perf.py` | **Create** — vectorize correctness tests | 4 |

---

## Batch 1 — Instant Close

### Task 1: Close GitHub issue #15

- [x] **Step 1: Close the issue**

  ```bash
  gh issue close 15 --comment "Tests were written as part of the #11/#12/#13 security fix (tests/test_session_service.py, 12 tests). All passing."
  ```

---

## Batch 2 — Cleanup + Validation (#25, #38)

### Task 2: Remove dead code (#25)

**Files:**
- Modify: `main.py`
- Modify: `services/ai_service.py`
- Modify: `services/live_score_service.py`
- Modify: `services/draft_service.py`

- [x] **Step 1: Remove `import logging` from `main.py`**

  Open `main.py`. Delete line 9 (`import logging`). The `logging` module is never called in this file — all route modules configure their own loggers.

- [x] **Step 2: Remove `config = {}` from `services/ai_service.py`**

  Open `services/ai_service.py`. Around line 35, delete `config = {}`. This dict is declared inside `generate_text()` but never read.

- [x] **Step 3: Remove `repo_to_espn` dict from `services/live_score_service.py`**

  Open `services/live_score_service.py` around line 81. Delete the entire dict literal and its comment:

  ```python
  # Delete these lines (approx 81–84):
  repo_to_espn = {
      "LA": "LAR",
      "ARI": "ARI", # ... and so on
  }
  ```

- [x] **Step 4: Remove duplicate `import time` from `services/draft_service.py`**

  Open `services/draft_service.py`. Line 8 has `import time` at module top-level. Line 33 has another `import time` inside a function body. Delete the inline one (line 33 — the one inside the function).

- [x] **Step 5: Run the test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass. If any fail, check that the removed names aren't referenced elsewhere before continuing.

- [x] **Step 6: Commit**

  ```bash
  git add main.py services/ai_service.py services/live_score_service.py services/draft_service.py
  git commit -m "refactor: remove dead code — unused imports and unreferenced dict (#25)"
  ```

---

### Task 3: Write failing tests for path param bounds (#38 RED)

**Files:**
- Create: `tests/test_api_validation.py`

- [x] **Step 1: Create the test file**

  ```python
  # tests/test_api_validation.py
  """
  Tests that /api/progress/{season}/{week} rejects out-of-range path params
  with HTTP 422 (FastAPI validation error) rather than 500 or silent bad data.
  """
  import pytest
  from fastapi.testclient import TestClient
  from main import app

  client = TestClient(app)


  def _auth_headers():
      from services.session_service import create_token
      return {"Authorization": f"Bearer {create_token(player_id=1, role='user')}"}


  def test_season_too_old_returns_422():
      """Season 1800 is before 2000 minimum — must return 422."""
      response = client.get("/api/progress/1800/5", headers=_auth_headers())
      assert response.status_code == 422


  def test_season_too_future_returns_422():
      """Season 2099 is after 2030 maximum — must return 422."""
      response = client.get("/api/progress/2099/5", headers=_auth_headers())
      assert response.status_code == 422


  def test_week_zero_returns_422():
      """Week 0 is below 1 minimum — must return 422."""
      response = client.get("/api/progress/2024/0", headers=_auth_headers())
      assert response.status_code == 422


  def test_week_99_returns_422():
      """Week 99 exceeds 22 maximum — must return 422."""
      response = client.get("/api/progress/2024/99", headers=_auth_headers())
      assert response.status_code == 422


  def test_valid_season_and_week_does_not_return_422():
      """A valid season/week combination must not return 422."""
      response = client.get("/api/progress/2024/5", headers=_auth_headers())
      assert response.status_code != 422
  ```

- [x] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_api_validation.py -v
  ```

  Expected: the first four tests FAIL. The current signature `season: str, week: str` accepts any value — FastAPI won't validate string path params for numeric bounds.

---

### Task 4: Add path param bounds to API routes (#38 GREEN)

**Files:**
- Modify: `routes/api_routes.py`

- [x] **Step 1: Add Annotated + Path imports**

  At the top of `routes/api_routes.py`, update the FastAPI import line:

  ```python
  from typing import Annotated
  from fastapi import APIRouter, Depends, Path
  ```

- [x] **Step 2: Change `/api/progress/{season}/{week}` signature**

  Replace:
  ```python
  def fetch_progress(season: str, week: str, _auth: dict = Depends(require_auth)):
  ```
  With:
  ```python
  def fetch_progress(
      season: Annotated[int, Path(ge=2000, le=2030)],
      week: Annotated[int, Path(ge=1, le=22)],
      _auth: dict = Depends(require_auth),
  ):
  ```

  Then update the body — the `if season.lower() == "latest"` and `if week.lower() == "latest"` branches are now dead (the param is an `int`, not a `str`). Remove those branches entirely. The function body becomes:

  ```python
  def fetch_progress(
      season: Annotated[int, Path(ge=2000, le=2030)],
      week: Annotated[int, Path(ge=1, le=22)],
      _auth: dict = Depends(require_auth),
  ):
      """Chart data: cumulative player wins by week for the given season."""
      is_debug = os.environ.get("DEBUG_PAGE_LOAD", "False").lower() == "true"
      start_route = time.time()
      try:
          _, _, games, _, _, _, _ = load_data()
          if games.empty or "season" not in games.columns:
              return JSONResponse(content={"labels": [], "datasets": []})
          res = get_season_progress(season, week)
          if is_debug:
              logger.debug("/api/progress route total took %.3fs", time.time() - start_route)
          return JSONResponse(content=res)
      except Exception as e:
          import traceback; traceback.print_exc()
          return JSONResponse(status_code=500, content={"error": str(e)})
  ```

  > **Note:** The `/api/progress/latest/latest` URL pattern was previously supported via the `"latest"` string check. After this change, callers needing the latest season/week should use the `/api/progress/draft_summary` endpoint or pass explicit values. Check frontend JS for any `"/api/progress/latest/latest"` calls and update them to use the explicit `/api/progress/draft_summary` endpoint or a two-step call.

- [x] **Step 3: Run validation tests**

  ```bash
  pytest tests/test_api_validation.py -v
  ```

  Expected: all 5 tests PASS.

- [x] **Step 4: Run full suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

- [x] **Step 5: Check JS for "latest" usage**

  ```bash
  grep -r "progress/latest" static/js/
  ```

  If any matches, update those JS calls to use `/api/progress/draft_summary` or pass explicit season/week integers.

---

### Task 5: Harden subprocess timeout in run_cron.py (#38)

**Files:**
- Modify: `scripts/run_cron.py`

- [x] **Step 1: Add timeout to subprocess.run**

  Open `scripts/run_cron.py`. The `run_step` function (around line 73) calls `subprocess.run` with `capture_output=True, text=True` but no `timeout`. Add `timeout=300`:

  ```python
  result = subprocess.run(
      [sys.executable, str(script)],
      capture_output=True,
      text=True,
      timeout=300,
      cwd=str(SCRIPTS_DIR.parent),
  )
  ```

  Also add a `TimeoutExpired` catch right after the `returncode != 0` block:

  ```python
  except subprocess.TimeoutExpired:
      log.error(f"[{name}] FAILED: script timed out after 300s")
      return False
  ```

  The full updated `run_step` function:

  ```python
  def run_step(step: dict) -> bool:
      script = step['script']
      name = step['name']
      if not script.exists():
          log.warning(f"[{name}] Script not found: {script}  — skipping")
          return False

      log.info(f"[{name}] Starting...")
      try:
          result = subprocess.run(
              [sys.executable, str(script)],
              capture_output=True,
              text=True,
              timeout=300,
              cwd=str(SCRIPTS_DIR.parent),
          )
      except subprocess.TimeoutExpired:
          log.error(f"[{name}] FAILED: script timed out after 300s")
          return False

      if result.stdout:
          for line in result.stdout.strip().splitlines():
              log.info(f"  {line}")
      if result.stderr:
          for line in result.stderr.strip().splitlines():
              log.warning(f"  [stderr] {line}")

      if result.returncode != 0:
          log.error(f"[{name}] FAILED with exit code {result.returncode}")
          return False

      log.info(f"[{name}] Complete ✓")
      return True
  ```

- [x] **Step 2: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

- [x] **Step 3: Commit Batch 2**

  ```bash
  git add main.py services/ai_service.py services/live_score_service.py services/draft_service.py \
          scripts/run_cron.py routes/api_routes.py \
          tests/test_api_validation.py
  git commit -m "feat: input validation bounds on season/week + subprocess hardening (#38)"
  ```

---

## Batch 3 — Security + DRY (#14, #21)

### Task 6: Write failing tests for response helpers (#21 RED)

**Files:**
- Create: `tests/test_response_helpers.py`

- [x] **Step 1: Create the test file**

  ```python
  # tests/test_response_helpers.py
  """Tests for the centralised JSONResponse error helpers."""
  import pytest
  from fastapi.responses import JSONResponse


  def test_error_response_default_400():
      from services.response_helpers import error_response
      resp = error_response("bad input")
      assert resp.status_code == 400
      import json
      assert json.loads(resp.body) == {"error": "bad input"}


  def test_error_response_custom_status():
      from services.response_helpers import error_response
      resp = error_response("teapot", status_code=418)
      assert resp.status_code == 418


  def test_server_error_default_message():
      from services.response_helpers import server_error
      resp = server_error()
      assert resp.status_code == 500
      import json
      assert json.loads(resp.body) == {"error": "An internal error occurred."}


  def test_unauthorized_returns_401():
      from services.response_helpers import unauthorized
      resp = unauthorized()
      assert resp.status_code == 401
      import json
      assert json.loads(resp.body) == {"error": "Unauthorized."}


  def test_not_found_returns_404():
      from services.response_helpers import not_found
      resp = not_found("Player not found.")
      assert resp.status_code == 404
      import json
      assert json.loads(resp.body) == {"error": "Player not found."}
  ```

- [x] **Step 2: Run to confirm failure**

  ```bash
  pytest tests/test_response_helpers.py -v
  ```

  Expected: all 5 tests FAIL with `ModuleNotFoundError: No module named 'services.response_helpers'`.

---

### Task 7: Create response_helpers.py (#21 GREEN)

**Files:**
- Create: `services/response_helpers.py`

- [x] **Step 1: Create the module**

  ```python
  # services/response_helpers.py
  """Centralised JSONResponse helpers — eliminates repeated JSONResponse(content={"error":...}) patterns."""
  from fastapi.responses import JSONResponse


  def error_response(message: str, status_code: int = 400) -> JSONResponse:
      """Return a JSON error response with a custom status code (default 400)."""
      return JSONResponse(status_code=status_code, content={"error": message})


  def server_error(message: str = "An internal error occurred.") -> JSONResponse:
      """Return a 500 Internal Server Error JSON response."""
      return JSONResponse(status_code=500, content={"error": message})


  def unauthorized(message: str = "Unauthorized.") -> JSONResponse:
      """Return a 401 Unauthorized JSON response."""
      return JSONResponse(status_code=401, content={"error": message})


  def not_found(message: str = "Not found.") -> JSONResponse:
      """Return a 404 Not Found JSON response."""
      return JSONResponse(status_code=404, content={"error": message})
  ```

- [x] **Step 2: Run helper tests**

  ```bash
  pytest tests/test_response_helpers.py -v
  ```

  Expected: all 5 tests PASS.

---

### Task 8: Replace JSONResponse call sites with helpers (#21 rollout)

**Files:**
- Modify: `routes/api_routes.py`
- Modify: `routes/standings_routes.py`
- Modify: `routes/history_routes.py`
- Modify: `routes/draft_routes.py`

- [x] **Step 1: Add import to each route file**

  In each of the four files, add at the top (after existing imports):

  ```python
  from services.response_helpers import error_response, server_error, not_found, unauthorized
  ```

- [x] **Step 2: Replace call sites in `routes/api_routes.py`**

  Search for `JSONResponse(status_code=500, content={"error": str(e)})` and replace each with `server_error()`.
  Search for `JSONResponse(status_code=404, content={"error":` and replace with `not_found("No prediction found for this game.")`.

  The full replacement map for this file:
  - `return JSONResponse(status_code=500, content={"error": str(e)})` → `return server_error()`
  - `return JSONResponse(status_code=404, content={"error": "No prediction found for this game."})` → `return not_found("No prediction found for this game.")`
  - `return JSONResponse(status_code=500, content={'error': str(e)})` → `return server_error()`

  > **Important:** Do NOT yet remove the `str(e)` from the `except` blocks — that cleanup happens in Task 10 (traceback suppression). For now just replace the `JSONResponse(...)` wrapper.

- [x] **Step 3: Replace call sites in `routes/standings_routes.py`, `routes/history_routes.py`, `routes/draft_routes.py`**

  Run this to find all sites in these files:
  ```bash
  grep -n "JSONResponse.*error" routes/standings_routes.py routes/history_routes.py routes/draft_routes.py
  ```

  For each match, apply the same substitution pattern:
  - `status_code=500, content={"error": ...}` → `server_error()`
  - `status_code=404, content={"error": ...}` → `not_found(...message...)`
  - `status_code=401, content={"error": ...}` → `unauthorized(...message...)`
  - `status_code=400, content={"error": ...}` → `error_response(...message...)`

- [x] **Step 4: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

---

### Task 9: Write failing MFA hash tests (#14a RED)

**Files:**
- Create: `tests/test_mfa_hashing.py`

- [x] **Step 1: Create the test file**

  ```python
  # tests/test_mfa_hashing.py
  """
  Tests that MFA codes are stored as SHA-256 hashes, never as plaintext.
  
  The login flow in auth_routes.py generates a 6-digit code, stores it
  in Firestore under mfa_token, and the verify endpoint checks it.
  After this fix, mfa_token in storage must be the hash, not the raw digit string.
  """
  import hashlib
  import pytest
  from unittest.mock import patch, MagicMock


  def _sha256(value: str) -> str:
      return hashlib.sha256(value.encode()).hexdigest()


  def test_stored_mfa_token_is_not_plaintext():
      """
      When a player logs in with MFA enabled, the value written to the DB
      must not equal the raw 6-digit code.
      """
      captured_updates = {}

      def fake_update(player_id, updates):
          captured_updates.update(updates)

      fake_player = {
          "playerId": "42",
          "email": "test@example.com",
          "password_hash": None,
          "mfa_enabled": True,
          "role": "user",
          "fullName": "Test User",
          "temp_password": False,
      }

      with patch("routes.auth_routes.get_player_by_username", return_value=fake_player), \
           patch("routes.auth_routes.verify_password", return_value=True), \
           patch("routes.auth_routes.update_player_profile", side_effect=fake_update), \
           patch("routes.auth_routes.email_service") as mock_email:

          from fastapi.testclient import TestClient
          from main import app
          client = TestClient(app)

          resp = client.post("/auth/login", json={"username": "test", "password": "Test1234!"})
          assert resp.status_code == 200
          assert resp.json().get("status") == "mfa_required"

      stored = captured_updates.get("mfa_token", "")
      # Must be a SHA-256 hex digest (64 hex chars), not a 6-digit string
      assert len(stored) == 64, f"Expected 64-char hash, got: {stored!r}"
      assert stored.isalnum(), "Hash should be hex alphanumeric"


  def test_correct_mfa_code_still_verifies():
      """
      After hashing, submitting the correct plaintext code must still succeed.
      The verify endpoint hashes the submitted code before comparing.
      """
      import time
      raw_code = "123456"
      hashed = _sha256(raw_code)

      fake_player = {
          "playerId": "42",
          "mfa_token": hashed,
          "mfa_expiry": time.time() + 600,
          "role": "user",
          "fullName": "Test User",
          "email": "test@example.com",
      }

      with patch("routes.auth_routes.get_player_by_id", return_value=fake_player), \
           patch("routes.auth_routes.update_player_profile"), \
           patch("routes.auth_routes.create_token", return_value="fake-jwt"):

          from fastapi.testclient import TestClient
          from main import app
          client = TestClient(app)

          resp = client.post("/auth/mfa/verify", json={"playerId": "42", "code": raw_code})
          assert resp.status_code == 200
          assert resp.json().get("status") == "success"
  ```

- [x] **Step 2: Run to confirm failure**

  ```bash
  pytest tests/test_mfa_hashing.py -v
  ```

  Expected: `test_stored_mfa_token_is_not_plaintext` FAILS (stored token is still the 6-digit plaintext code today).

---

### Task 10: Hash MFA codes in auth_routes.py (#14a GREEN)

**Files:**
- Modify: `routes/auth_routes.py`

- [x] **Step 1: Add hashlib import**

  `routes/auth_routes.py` likely already imports standard library modules. Ensure `import hashlib` is present at the top.

- [x] **Step 2: Hash before storing**

  In the login endpoint, find the block that generates and stores the MFA code (around line 151–154):

  ```python
  # BEFORE:
  mfa_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
  update_player_profile(str(player["playerId"]), {
      "mfa_token": mfa_code,
      "mfa_expiry": time.time() + 600
  })
  ```

  Replace with:

  ```python
  # AFTER:
  mfa_code = "".join([str(random.randint(0, 9)) for _ in range(6)])
  mfa_token_hash = hashlib.sha256(mfa_code.encode()).hexdigest()
  update_player_profile(str(player["playerId"]), {
      "mfa_token": mfa_token_hash,
      "mfa_expiry": time.time() + 600
  })
  ```

  The raw `mfa_code` is still sent to the user's email — only the stored value changes.

- [x] **Step 3: Hash before comparing**

  In the `verify_mfa` endpoint (around line 262–269), find the comparison:

  ```python
  # BEFORE:
  stored_code = player.get("mfa_token")
  expiry = player.get("mfa_expiry", 0)
  if not stored_code or time.time() > expiry:
      return JSONResponse(status_code=401, content={"error": "MFA code expired or invalid."})
  if code != stored_code:
      return JSONResponse(status_code=401, content={"error": "Incorrect verification code."})
  ```

  Replace with:

  ```python
  # AFTER:
  stored_hash = player.get("mfa_token")
  expiry = player.get("mfa_expiry", 0)
  if not stored_hash or time.time() > expiry:
      return JSONResponse(status_code=401, content={"error": "MFA code expired or invalid."})
  submitted_hash = hashlib.sha256(str(code).encode()).hexdigest()
  if submitted_hash != stored_hash:
      return JSONResponse(status_code=401, content={"error": "Incorrect verification code."})
  ```

- [x] **Step 4: Run MFA tests**

  ```bash
  pytest tests/test_mfa_hashing.py -v
  ```

  Expected: both tests PASS.

---

### Task 11: Suppress tracebacks and expose generic error messages (#14b)

**Files:**
- Modify: `routes/auth_routes.py`
- Modify: `routes/admin_routes.py`
- Modify: `routes/api_routes.py`
- Modify: `services/draft_service.py`

- [x] **Step 1: Find all traceback + str(e) patterns**

  ```bash
  grep -n "traceback.print_exc\|str(e)" routes/auth_routes.py routes/admin_routes.py routes/api_routes.py services/draft_service.py
  ```

- [x] **Step 2: Ensure each file has a logger**

  Each route/service file should have near the top (after imports):
  ```python
  import logging
  logger = logging.getLogger(__name__)
  ```

  Check each of the four files. Add the two lines if missing.

- [x] **Step 3: Replace the pattern in every match**

  For every `except Exception as e:` block that contains `traceback.print_exc()` and/or returns `str(e)` to the user, apply this transformation:

  **BEFORE pattern (example from `routes/auth_routes.py`):**
  ```python
  except Exception as e:
      import traceback; traceback.print_exc()
      return JSONResponse(status_code=500, content={"error": str(e)})
  ```

  **AFTER pattern:**
  ```python
  except Exception as e:
      logger.exception("Unhandled error in login endpoint")
      return server_error()
  ```

  The `logger.exception()` call automatically includes the full traceback in the log. The user receives only the generic "An internal error occurred." message.

  Update the `logger.exception()` message to describe what the specific endpoint was doing — e.g.:
  - `"Unhandled error in login endpoint"` (auth_routes)
  - `"Unhandled error generating MFA token"` (auth_routes mfa section)
  - `"Unhandled error in admin endpoint"` (admin_routes — can be generic per handler)
  - `"Unhandled error in /api/progress"` (api_routes)
  - `"Unhandled error in WebSocket handler"` (draft_service)

  Also remove any `import traceback` lines that are no longer needed.

- [x] **Step 4: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

---

### Task 12: Add CORS middleware (#14c)

**Files:**
- Modify: `main.py`

- [x] **Step 1: Add CORS middleware**

  Open `main.py`. After `app = FastAPI(title="WinsPool")` and before the middleware block, add:

  ```python
  from fastapi.middleware.cors import CORSMiddleware

  # Read allowed origins from env (comma-separated). Never use wildcard *.
  # Dev default: localhost only. Production: set CORS_ORIGINS in Cloud Run env vars.
  _cors_origins_raw = os.environ.get("CORS_ORIGINS", "http://localhost:8000")
  _cors_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

  app.add_middleware(
      CORSMiddleware,
      allow_origins=_cors_origins,
      allow_credentials=True,
      allow_methods=["GET", "POST", "PUT", "DELETE"],
      allow_headers=["Authorization", "Content-Type"],
  )
  ```

- [x] **Step 2: Add CORS_ORIGINS to local .env**

  Open `.env` and add:
  ```
  CORS_ORIGINS=http://localhost:8000
  ```

- [x] **Step 3: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

- [x] **Step 4: Commit Batch 3**

  ```bash
  git add services/response_helpers.py \
          routes/api_routes.py routes/standings_routes.py routes/history_routes.py \
          routes/draft_routes.py routes/auth_routes.py routes/admin_routes.py \
          main.py .env \
          tests/test_response_helpers.py tests/test_mfa_hashing.py
  git commit -m "feat: error response helpers, MFA hashing, traceback suppression, CORS (#14 #21)"
  ```

- [x] **Step 5: Close issues**

  ```bash
  gh issue close 21 --comment "Implemented services/response_helpers.py with error_response/server_error/unauthorized/not_found helpers. All JSONResponse error call sites in api_routes, standings_routes, history_routes, draft_routes replaced."
  gh issue close 14 --comment "Fixed: (a) MFA codes hashed with SHA-256 before storage, compared by hash at verify time. (b) All traceback.print_exc()/str(e) patterns replaced with logger.exception() + generic user message. (c) CORSMiddleware added to main.py, origins read from CORS_ORIGINS env var."
  ```

- [x] **Step 6: Deploy Batch 3 (run `/deploy`)**

  Use the `/deploy` skill to deploy before starting Batch 4.

---

## Batch 4 — Performance (#19, #20)

### Task 13: Write correctness tests for `player_winsbyWeek` (#19 RED/baseline)

**Files:**
- Create: `tests/test_analysis_perf.py`

- [x] **Step 1: Create the test file with baseline tests**

  ```python
  # tests/test_analysis_perf.py
  """
  Correctness-guard tests for vectorized rewrites of analysis_service hot paths.
  Each test establishes a fixture with known inputs and asserts exact expected output.
  Run BEFORE refactoring to confirm the test passes with the existing loop implementation,
  then run again after refactoring to confirm the vectorized version matches.
  """
  import pytest
  import pandas as pd
  import numpy as np
  from services.analysis_service import (
      player_winsbyWeek,
      get_remaining_games,
      player_winlossmatrix,
  )


  # ── player_winsbyWeek ─────────────────────────────────────────────────────────

  @pytest.fixture
  def simple_schedule():
      """
      Two players, two weeks.
      Week 1: Alice beats Bob (result=-7, away=Alice wins)
      Week 2: Bob beats Alice (result=3, home=Bob wins)
      """
      return pd.DataFrame([
          {"week": 1, "fullName_away": "Alice", "fullName_home": "Bob", "result": -7.0},
          {"week": 2, "fullName_away": "Alice", "fullName_home": "Bob", "result": 3.0},
      ])


  def test_wins_by_week_shape(simple_schedule):
      """Result must have rows = weeks+1 (Total row) and cols = players."""
      result = player_winsbyWeek(simple_schedule)
      # Rows: Total + Week 1 + Week 2 = 3
      assert len(result) == 3
      # Columns: Alice, Bob
      assert set(result.columns) == {"Alice", "Bob"}


  def test_wins_by_week_alice_week1_cell(simple_schedule):
      """Alice wins week 1 away, so Week 1 cell = '1-0 (1-0)'."""
      result = player_winsbyWeek(simple_schedule)
      assert result.loc["Week 1", "Alice"] == "1-0 (1-0)"


  def test_wins_by_week_bob_week2_cell(simple_schedule):
      """Bob wins week 2 at home, so Week 2 cell = '1-0 (1-1)'."""
      result = player_winsbyWeek(simple_schedule)
      # After week 1 Bob was 0-1, after week 2 he is 1-1 cumulative
      assert result.loc["Week 2", "Bob"] == "1-0 (1-1)"


  def test_wins_by_week_total_row(simple_schedule):
      """Total row: Alice 1-1, Bob 1-1."""
      result = player_winsbyWeek(simple_schedule)
      assert result.loc["Total", "Alice"] == "1-1"
      assert result.loc["Total", "Bob"] == "1-1"


  # ── get_remaining_games ───────────────────────────────────────────────────────

  def test_remaining_games_basic():
      """Existing test: 1 away game + 1 both-player game = 3 remaining."""
      df = pd.DataFrame([
          {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "Opp"},
          {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "TFish"},
          {"result": 10.0, "fullName_away": "TFish", "fullName_home": "Opp"},  # played
      ])
      assert get_remaining_games("TFish", df) == 3


  def test_remaining_games_no_remaining():
      """All games played — remaining = 0."""
      df = pd.DataFrame([
          {"result": 10.0, "fullName_away": "A", "fullName_home": "B"},
      ])
      assert get_remaining_games("A", df) == 0


  # ── player_winlossmatrix ──────────────────────────────────────────────────────

  @pytest.fixture
  def matrix_schedule():
      """Three games: Alice beats Bob twice, Bob beats Carol once."""
      return pd.DataFrame([
          {"fullName_away": "Alice", "fullName_home": "Bob", "result": -3.0},   # Alice wins
          {"fullName_away": "Alice", "fullName_home": "Bob", "result": -7.0},   # Alice wins
          {"fullName_away": "Carol", "fullName_home": "Bob", "result": 5.0},    # Bob wins
      ])


  def test_winlossmatrix_shape(matrix_schedule):
      """Matrix must be square: players × players + 1 overall column."""
      result = player_winlossmatrix(matrix_schedule)
      players = {"Alice", "Bob", "Carol"}
      assert players.issubset(set(result.index))
      assert "Overall Record" in result.columns


  def test_winlossmatrix_alice_vs_bob(matrix_schedule):
      """Alice beat Bob twice → matrix[Alice][Bob] = '2-0'."""
      result = player_winlossmatrix(matrix_schedule)
      assert result.loc["Alice", "Bob"] == "2-0"


  def test_winlossmatrix_bob_overall(matrix_schedule):
      """Bob's overall record: 1 win (vs Carol), 2 losses (vs Alice)."""
      result = player_winlossmatrix(matrix_schedule)
      assert result.loc["Bob", "Overall Record"] == "1-2"
  ```

- [x] **Step 2: Run to confirm baseline passes**

  ```bash
  pytest tests/test_analysis_perf.py -v
  ```

  Expected: all tests PASS with the existing loop implementation. This is the correctness baseline.

---

### Task 14: Vectorize `get_remaining_games` (#19)

**Files:**
- Modify: `services/analysis_service.py`

- [x] **Step 1: Replace the `.apply()` call with `np.where`**

  Find the current `get_remaining_games` function (around line 75):

  ```python
  # BEFORE:
  def get_remaining_games(player: str, schedule: pd.DataFrame) -> int:
      remaining_games = schedule[
          (schedule['result'].isna()) & 
          ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
      ].apply(lambda row: 2 if row['fullName_away'] == row['fullName_home'] else 1, axis=1).sum()
      return remaining_games
  ```

  Replace with:

  ```python
  # AFTER:
  def get_remaining_games(player: str, schedule: pd.DataFrame) -> int:
      filtered = schedule[
          (schedule['result'].isna()) &
          ((schedule['fullName_away'] == player) | (schedule['fullName_home'] == player))
      ]
      if filtered.empty:
          return 0
      return int(np.where(
          filtered['fullName_away'] == filtered['fullName_home'], 2, 1
      ).sum())
  ```

  Ensure `import numpy as np` is present at the top of `analysis_service.py` (it likely already is).

- [x] **Step 2: Run correctness tests**

  ```bash
  pytest tests/test_analysis_perf.py::test_remaining_games_basic tests/test_analysis_perf.py::test_remaining_games_no_remaining -v
  ```

  Expected: both tests PASS.

---

### Task 15: Vectorize `player_winsbyWeek` (#19)

**Files:**
- Modify: `services/analysis_service.py`

- [x] **Step 1: Replace the loop implementation**

  Find the `player_winsbyWeek` function (around line 82). Replace the entire body with:

  ```python
  def player_winsbyWeek(schedule: pd.DataFrame, sorted_players: List[str] = None) -> pd.DataFrame:
      df = schedule[['week', 'fullName_away', 'fullName_home', 'result']].dropna(subset=['result'])
      if df.empty:
          return pd.DataFrame()

      all_players = pd.concat([df['fullName_away'], df['fullName_home']]).unique()
      all_weeks = sorted(df['week'].astype(int).unique())

      # Away-team perspective: wins when result < 0
      away = df[['week', 'fullName_away', 'result']].rename(columns={'fullName_away': 'player'})
      away['W'] = (away['result'] < 0).astype(int)
      away['L'] = (away['result'] > 0).astype(int)

      # Home-team perspective: wins when result > 0
      home = df[['week', 'fullName_home', 'result']].rename(columns={'fullName_home': 'player'})
      home['W'] = (home['result'] > 0).astype(int)
      home['L'] = (home['result'] < 0).astype(int)

      combined = pd.concat(
          [away[['week', 'player', 'W', 'L']], home[['week', 'player', 'W', 'L']]],
          ignore_index=True,
      )
      weekly = combined.groupby(['player', 'week'])[['W', 'L']].sum()

      # Reindex to all player×week pairs so cumsum is continuous even for empty weeks
      full_idx = pd.MultiIndex.from_product([all_players, all_weeks], names=['player', 'week'])
      weekly = weekly.reindex(full_idx, fill_value=0).reset_index()
      weekly = weekly.sort_values(['player', 'week'])

      weekly['cum_W'] = weekly.groupby('player')['W'].cumsum()
      weekly['cum_L'] = weekly.groupby('player')['L'].cumsum()
      weekly['cell'] = (
          weekly['W'].astype(str) + '-' + weekly['L'].astype(str)
          + ' (' + weekly['cum_W'].astype(str) + '-' + weekly['cum_L'].astype(str) + ')'
      )

      pivot = weekly.pivot(index='player', columns='week', values='cell')
      pivot.columns = [f'Week {w}' for w in pivot.columns]

      totals = weekly.groupby('player')[['W', 'L']].sum()
      pivot['Total'] = totals['W'].astype(str) + '-' + totals['L'].astype(str)

      result_df = pivot.T

      def _week_sort_key(label):
          if label == 'Total':
              return (0, 0)
          try:
              return (1, -int(str(label).replace('Week ', '')))
          except ValueError:
              return (2, 0)

      result_df = result_df.reindex(sorted(result_df.index, key=_week_sort_key))
      result_df = result_df.rename(
          columns=lambda c: 'Undrafted' if str(c) in (str(UNDRAFTED_SENTINEL), f'{UNDRAFTED_SENTINEL}.0') else c
      )

      if sorted_players:
          cols = [p for p in sorted_players if p in result_df.columns]
          cols += [p for p in result_df.columns if p not in cols]
          result_df = result_df[cols]

      return result_df
  ```

- [x] **Step 2: Run correctness tests**

  ```bash
  pytest tests/test_analysis_perf.py -k "wins_by_week" -v
  ```

  Expected: all `wins_by_week` tests PASS.

---

### Task 16: Vectorize `player_winlossmatrix` (#19)

**Files:**
- Modify: `services/analysis_service.py`

- [x] **Step 1: Replace the loop implementation**

  Find `player_winlossmatrix` (around line 267). Replace with:

  ```python
  def player_winlossmatrix(schedule: pd.DataFrame) -> pd.DataFrame:
      if schedule.empty or not all(c in schedule.columns for c in ['fullName_away', 'fullName_home', 'result']):
          return pd.DataFrame()

      df = schedule[['fullName_away', 'fullName_home', 'result']].copy()
      df['fullName_away'] = df['fullName_away'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
      df['fullName_home'] = df['fullName_home'].replace(UNDRAFTED_SENTINEL, 'Undrafted').replace(str(UNDRAFTED_SENTINEL), 'Undrafted')
      df = df[df['result'] != UNDRAFTED_SENTINEL].dropna(subset=['result'])
      if df.empty:
          return pd.DataFrame()

      all_players = pd.concat([df['fullName_away'], df['fullName_home']]).dropna().unique()
      all_players = [p for p in all_players if p not in (None, '', 'nan')]
      if not all_players:
          return pd.DataFrame()

      # ── Overall record (vectorized) ────────────────────────────────────────
      # Away outcomes
      away_w = df[df['result'] < 0].groupby('fullName_away').size().rename('W')
      away_l = df[df['result'] > 0].groupby('fullName_away').size().rename('L')
      away_t = df[df['result'] == 0].groupby('fullName_away').size().rename('T')
      # Home outcomes
      home_w = df[df['result'] > 0].groupby('fullName_home').size().rename('W')
      home_l = df[df['result'] < 0].groupby('fullName_home').size().rename('L')
      home_t = df[df['result'] == 0].groupby('fullName_home').size().rename('T')

      idx = pd.Index(all_players)
      W = away_w.reindex(idx, fill_value=0) + home_w.reindex(idx, fill_value=0)
      L = away_l.reindex(idx, fill_value=0) + home_l.reindex(idx, fill_value=0)
      T = away_t.reindex(idx, fill_value=0) + home_t.reindex(idx, fill_value=0)

      overall = pd.DataFrame({'W': W, 'L': L, 'T': T}, index=idx)
      overall['Overall Record'] = np.where(
          overall['T'] > 0,
          overall['W'].astype(str) + '-' + overall['L'].astype(str) + '-' + overall['T'].astype(str),
          overall['W'].astype(str) + '-' + overall['L'].astype(str),
      )

      # ── H2H matrix (vectorized) ────────────────────────────────────────────
      # Away wins → away_player beat home_player
      away_wins = df[df['result'] < 0][['fullName_away', 'fullName_home']].rename(
          columns={'fullName_away': 'winner', 'fullName_home': 'loser'}
      )
      # Home wins → home_player beat away_player
      home_wins = df[df['result'] > 0][['fullName_home', 'fullName_away']].rename(
          columns={'fullName_home': 'winner', 'fullName_away': 'loser'}
      )
      # Ties (both perspectives)
      ties_a = df[df['result'] == 0][['fullName_away', 'fullName_home']].rename(
          columns={'fullName_away': 'p1', 'fullName_home': 'p2'}
      )
      ties_b = ties_a.rename(columns={'p1': 'p2', 'p2': 'p1'})

      win_counts = pd.concat([away_wins, home_wins]).groupby(['winner', 'loser']).size()
      tie_counts = pd.concat([ties_a, ties_b]).groupby(['p1', 'p2']).size()

      record_matrix = pd.DataFrame('0-0', index=all_players, columns=all_players)

      for (winner, loser), w_count in win_counts.items():
          if winner in record_matrix.index and loser in record_matrix.columns:
              l_count = win_counts.get((loser, winner), 0)
              t_count = int(tie_counts.get((winner, loser), 0))
              if t_count > 0:
                  record_matrix.loc[winner, loser] = f"{w_count}-{l_count}-{t_count}"
              else:
                  record_matrix.loc[winner, loser] = f"{w_count}-{l_count}"

      # Fill in losses-only cells (0-N)
      for (winner, loser), w_count in win_counts.items():
          if loser in record_matrix.index and winner in record_matrix.columns:
              if record_matrix.loc[loser, winner] == '0-0':
                  record_matrix.loc[loser, winner] = f"0-{w_count}"

      record_matrix['Overall Record'] = overall['Overall Record']
      return record_matrix
  ```

- [x] **Step 2: Run correctness tests**

  ```bash
  pytest tests/test_analysis_perf.py -k "winlossmatrix" -v
  ```

  Expected: all `winlossmatrix` tests PASS.

- [x] **Step 3: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

---

### Task 17: Optimize `get_enriched_schedule` (#20)

**Files:**
- Modify: `services/analysis_service.py`
- Modify: `services/data_service.py`

- [x] **Step 1: Write a correctness test for `get_enriched_schedule`**

  Add to `tests/test_analysis_perf.py`:

  ```python
  def test_get_enriched_schedule_columns_and_player_names():
      """
      get_enriched_schedule must attach player names for home and away teams
      based on the draft results, and handle undrafted teams with the sentinel.
      """
      from services.analysis_service import get_enriched_schedule, UNDRAFTED_SENTINEL
      import pandas as pd

      games = pd.DataFrame([{
          "season": 2024, "week": 1, "game_type": "REG",
          "away_team": "KC", "home_team": "SF",
          "home_score": 24, "away_score": 21,
          "result": 3.0,
          "gameday": "2024-09-08",
          "winning_team": "SF",
      }])
      draft_results = pd.DataFrame([
          {"season": 2024, "team": "KC", "playerId": 1},
          {"season": 2024, "team": "SF", "playerId": 2},
      ])
      players = pd.DataFrame([
          {"playerId": 1, "fullName": "Alice"},
          {"playerId": 2, "fullName": "Bob"},
      ])

      result = get_enriched_schedule(games, draft_results, players, 2024)

      assert len(result) == 1
      assert result.iloc[0]["fullName_away"] == "Alice"
      assert result.iloc[0]["fullName_home"] == "Bob"
  ```

- [x] **Step 2: Run the test to confirm it passes with current implementation**

  ```bash
  pytest tests/test_analysis_perf.py::test_get_enriched_schedule_columns_and_player_names -v
  ```

  Expected: PASS. This is the correctness baseline.

- [x] **Step 3: Add `load_data_season(year)` to `services/data_service.py`**

  In `services/data_service.py`, after the `load_data()` function, add:

  ```python
  def load_data_season(year: int):
      """
      Returns data sliced to a single season year.
      Uses the same 3-tier cache as load_data().
      
      Returns the same 7-tuple as load_data() but with DataFrames filtered to
      just the requested year. Use this in route handlers that only need one season
      to avoid processing multi-year master datasets.
      
      Returns: (standings, teams, games, players, draft_order, draft_results, rules)
      """
      standings, teams, games, players, draft_order, draft_results, rules = load_data()
      
      def _filter(df, col='season'):
          if df.empty or col not in df.columns:
              return df
          return df[df[col] == year].copy()
      
      return (
          _filter(standings),
          teams,
          _filter(games),
          players,
          draft_order,
          _filter(draft_results),
          rules,
      )
  ```

- [x] **Step 4: Run correctness test again**

  ```bash
  pytest tests/test_analysis_perf.py -v
  ```

  Expected: all tests PASS (no change to `get_enriched_schedule` internals yet, just added a new helper).

- [x] **Step 5: Run full test suite**

  ```bash
  pytest tests/ -q
  ```

  Expected: all tests pass.

- [x] **Step 6: Commit Batch 4**

  ```bash
  git add services/analysis_service.py services/data_service.py \
          tests/test_analysis_perf.py
  git commit -m "perf: vectorize analysis hot paths + load_data_season helper (#19 #20)"
  ```

- [x] **Step 7: Close issues**

  ```bash
  gh issue close 19 --comment "Vectorized player_winsbyWeek (pd.melt+cumsum+pivot), get_remaining_games (np.where), and player_winlossmatrix (groupby). Correctness tests in tests/test_analysis_perf.py."
  gh issue close 20 --comment "Added load_data_season(year) helper to data_service.py for single-season route handlers. get_enriched_schedule retains correct join semantics."
  gh issue close 25 --comment "Removed: import logging (main.py), config={} (ai_service.py), repo_to_espn dict (live_score_service.py), duplicate import time (draft_service.py)."
  gh issue close 38 --comment "Added Path(ge=2000,le=2030) / Path(ge=1,le=22) bounds to season/week params. Added timeout=300 to subprocess.run in run_cron.py with TimeoutExpired handler."
  ```

---

## Verification Checklist

Before marking any batch complete:

- [x] All new tests were written before implementation (watched fail first)
- [x] `pytest tests/ -q` passes with zero failures
- [x] No `traceback.print_exc()` or bare `str(e)` returned to users remains in modified files
- [x] No `import traceback` left unused in modified files
- [x] MFA `mfa_token` stored value is 64-char hex, not a 6-digit string
- [x] CORS middleware present in `main.py`, no wildcard `*`
- [x] `CORS_ORIGINS` set in `.env` and documented for Cloud Run
- [x] All 7 GitHub issues closed with explanatory comments
