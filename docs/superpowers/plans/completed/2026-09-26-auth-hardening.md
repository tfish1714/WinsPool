# Auth Hardening (Profile Update, MFA Attempts, Hosting Cleanup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the unauthenticated `/api/profile/update` takeover path, cap `/api/mfa/verify` guesses, and retire the dead Firebase Hosting config and the hardcoded recap-email link.

**Architecture:** All auth changes are confined to `routes/auth_routes.py`. Profile update gains `Depends(require_auth)` plus an ownership check and the same per-account lockout bookkeeping as `login`. MFA verify gains the shared per-IP `_login_limiter`, a per-code attempt counter stored on the player document, and a constant-time digest comparison. The hosting cleanup empties the dead `hosting` block of `firebase.json` and makes the recap email read `APP_BASE_URL` through the existing `email_service._app_base_url()`.

**Tech Stack:** FastAPI, pytest with `fastapi.testclient.TestClient`, `unittest.mock.patch`, stdlib `hmac`.

**Spec:** `docs/superpowers/specs/2026-09-26-auth-hardening-followups.md` (sections 1 and 2; section 3, VAPID, is out of scope for this plan) and `docs/superpowers/specs/2026-09-26-cleanup-followups.md` (section 1 only).

**Worktree / branch:** `worktree-auth-hardening`, created with `git worktree add .claude/worktrees/auth-hardening -b worktree-auth-hardening main` from the main checkout. Note `docs/` is gitignored in this repo; this plan and the specs live only in the main checkout, so implementers get task text from the controller, not from the worktree.

## Global Constraints

- No emojis in code, comments, docs, tests or commit messages (an existing emoji in `scripts/generate_weekly_summary.py` is pre-existing and must be left as is unless a line being rewritten contains it; keep it exactly when moving lines).
- Zero-deletion policy: no existing feature or test may be removed. Existing tests that must change because the endpoint now requires auth are MODIFIED (an auth header/cookie is added), never deleted or weakened.
- Do not refactor `login`. Duplicate its lockout bookkeeping in `update_profile`.
- Keep `_rate_limited_response(request, _login_limiter)` as the first statement of each handler body that already has it.
- Handlers stay `async def` (the in-process `RateLimiter` has no lock).
- Response shapes on success are unchanged: `{"message": "Profile updated successfully!"}` for profile update, the `status: success` payload plus cookie for MFA verify.
- Never log a submitted MFA code.
- Run tests with `pytest tests/` from the worktree root; a task is done only when the targeted tests and the full `tests/test_auth.py tests/test_mfa_hashing.py` pass.

## Review Focus

- JWT `sub` is a `str` (`create_token` does `str(player_id)`) while `body.playerId` is a `str` from JSON but the Firestore `playerId` can be int/float: compare `str(_auth["sub"]) != str(body.playerId)` only, never against the Firestore value.
- Stale or missing cookie on `/api/profile/update` must give a clean 401 from `require_auth`, not a 500.
- `mfa_token` unset in Firestore arrives via pandas as `float('nan')` (truthy): the verify handler must return the "expired or invalid" 401, not raise `TypeError` (500) inside the digest comparison.
- `mfa_attempts` NaN or missing must count as 0 (`_int_field`).
- A non-ASCII or otherwise odd stored digest must not crash `hmac.compare_digest` (compare encoded bytes).
- A per-IP limit test must not accidentally rely on the limiter to produce the account-lockout 429 (the login bucket is 5 per minute); lockout tests must stay at or under 5 requests per test.

---

### Task 1: Authenticate `/api/profile/update` and route wrong passwords through lockout

**Files:**
- Modify: `routes/auth_routes.py` (`update_profile`, currently ~lines 328-381)
- Modify: `tests/test_auth.py` (`TestProfileUpdate`, `test_profile_update_rate_limited_after_five_attempts_sharing_login_bucket`, `_profile_update`; add new tests)

**Interfaces:**
- Consumes: `require_auth` (already imported in `routes/auth_routes.py`), `_int_field`, `_login_limiter`, `update_player_profile`, `verify_password`.
- Produces: `update_profile(body: UpdateProfileRequest, request: Request, _auth: dict = Depends(require_auth))`. No new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py` (after the existing `TestProfileUpdate` class is fine; helper functions at module level):

```python
def _bearer(player_id):
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id, 'user')}"}


class TestProfileUpdateAuth:
    """/api/profile/update requires a session and only edits the caller's own profile."""

    _BODY = {
        "playerId": "99",
        "currentPassword": "OldPass1!LongPwd",
        "fullName": "New Name",
        "nickName": "nn",
        "email": "old@test.com",
        "mfaEnabled": False,
    }

    def _player(self, **extra):
        return {
            "playerId": 99, "email": "old@test.com", "password_hash": "hash",
            "role": "user", "failed_login_attempts": 0, "lockout_until": None,
            **extra,
        }

    def test_no_token_returns_401(self):
        resp = client.post("/api/profile/update", json=self._BODY)
        assert resp.status_code == 401

    def test_garbage_cookie_returns_401_not_500(self):
        resp = client.post("/api/profile/update", json=self._BODY,
                           cookies={"session_token": "garbage"})
        assert resp.status_code == 401

    def test_token_for_other_player_returns_403(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()) as get_p, \
             patch("routes.auth_routes.verify_password", return_value=True) as vp, \
             patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(7))
        assert resp.status_code == 403
        assert resp.json() == {"error": "Forbidden."}
        get_p.assert_not_called()
        vp.assert_not_called()
        upd.assert_not_called()

    def test_matching_token_and_correct_password_returns_200(self):
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 200
        assert resp.json() == {"message": "Profile updated successfully!"}
        upd.assert_called_once()

    def test_cookie_only_auth_works(self):
        from services.session_service import create_token
        with patch("services.db_service.get_player_by_id", return_value=self._player()), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile"):
            resp = client.post("/api/profile/update", json=self._BODY,
                               cookies={"session_token": create_token(99, "user")})
        assert resp.status_code == 200

    def test_success_resets_failure_counters_in_same_write(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=3)), \
             patch("routes.auth_routes.verify_password", return_value=True), \
             patch("routes.auth_routes.update_player_profile") as upd:
            client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        upd.assert_called_once()
        sent = upd.call_args[0][1]
        assert sent["failed_login_attempts"] == 0
        assert sent["lockout_until"] is None

    def test_wrong_password_increments_failed_attempts(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=1)), \
             patch("routes.auth_routes.verify_password", return_value=False), \
             patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 401
        assert resp.json()["error"] == "Incorrect current password."
        upd.assert_called_once_with("99", {"failed_login_attempts": 2})

    def test_nan_failed_attempts_treated_as_zero(self):
        with patch("services.db_service.get_player_by_id",
                   return_value=self._player(failed_login_attempts=float("nan"))), \
             patch("routes.auth_routes.verify_password", return_value=False), \
             patch("routes.auth_routes.update_player_profile") as upd:
            resp = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
        assert resp.status_code == 401
        upd.assert_called_once_with("99", {"failed_login_attempts": 1})

    def test_fifth_wrong_password_locks_account_and_blocks_correct_password_and_login(self):
        store = self._player(failed_login_attempts=4)

        def fake_update(pid, updates):
            store.update(updates)

        with patch("services.db_service.get_player_by_id", side_effect=lambda pid: dict(store)), \
             patch("routes.auth_routes.get_player_by_email", side_effect=lambda e: dict(store)), \
             patch("routes.auth_routes.verify_password", side_effect=lambda p, h: p == "OldPass1!LongPwd"), \
             patch("routes.auth_routes.update_player_profile", side_effect=fake_update):
            bad = client.post("/api/profile/update",
                              json={**self._BODY, "currentPassword": "wrong"}, headers=_bearer(99))
            assert bad.status_code == 429
            assert "locked" in bad.json()["error"].lower()
            assert store["lockout_until"] > time.time()

            good = client.post("/api/profile/update", json=self._BODY, headers=_bearer(99))
            assert good.status_code == 429
            assert "locked" in good.json()["error"].lower()

            login = client.post("/api/login",
                                json={"email": "old@test.com", "password": "OldPass1!LongPwd"})
            assert login.status_code == 429
            assert "locked" in login.json()["error"].lower()
```

- [ ] **Step 2: Modify the existing tests that now need a session (do not delete or weaken them)**

In `tests/test_auth.py`, `TestProfileUpdate`: every `client.post("/api/profile/update", ...)` call gains `headers=_bearer(99)` (all of those tests use `playerId` "99"). The `_bearer` helper is only called at test run time, so it may be defined anywhere at module level.

In `_profile_update(c)`, add the header for player 1 (the body uses `"playerId": "1"`):

```python
def _profile_update(c):
    return c.post("/api/profile/update",
                  json={"playerId": "1", "currentPassword": "wrong"},
                  headers=_bearer(1))
```

`test_profile_update_rate_limited_after_five_attempts_sharing_login_bucket` keeps its expectations: `[404] * 5` (player lookup patched to `None`; ownership passes because the token is for player 1), then 429 from the shared per-IP limiter.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `pytest tests/test_auth.py -k "ProfileUpdate" -v`
Expected: the new `TestProfileUpdateAuth` tests FAIL (no 401 without a token; no 403; no counter increments).

- [ ] **Step 4: Implement**

In `routes/auth_routes.py`, change `update_profile` as follows. Only the marked parts are new; everything else in the function stays exactly as it is.

```python
@router.post("/profile/update")
async def update_profile(body: UpdateProfileRequest, request: Request,
                         _auth: dict = Depends(require_auth)):
    # (existing rate-limit comment and guard stay the first statements)
    limited = _rate_limited_response(request, _login_limiter)
    if limited:
        return limited
    try:
        pid = body.playerId
        # NEW: a session may only edit its own profile. Both sides are
        # normalised to str: the JWT sub is a str, playerId may be numeric-ish.
        if str(_auth["sub"]) != str(pid):
            return JSONResponse(status_code=403, content={"error": "Forbidden."})
        # ... existing lines unchanged through the 404 "Player not found." check ...

        # NEW: same lockout gate as /api/login.
        lockout = player.get("lockout_until")
        if lockout and time.time() < lockout:
            rem = int((lockout - time.time()) // 60)
            return JSONResponse(status_code=429, content={"error": f"Account locked. Try again in {rem} minutes."})

        if not verify_password(curr_password, player.get("password_hash")):
            # NEW: count the failure exactly as /api/login does.
            fails = _int_field(player, "failed_login_attempts") + 1
            lockout_ts = time.time() + 1800 if fails >= 5 else None
            update_player_profile(str(player["playerId"]), {
                "failed_login_attempts": fails,
                **(({"lockout_until": lockout_ts}) if lockout_ts else {})
            })
            if lockout_ts:
                return JSONResponse(status_code=429, content={"error": "Too many failed login attempts. Account locked for 30 minutes."})
            return JSONResponse(status_code=401, content={"error": "Incorrect current password."})

        # ... unchanged: email collision check, updates dict building ...

        updates["mfa_enabled"] = bool(mfa_enabled)
        # NEW: a verified password clears the counters, mirroring login's reset_fields.
        updates["failed_login_attempts"] = 0
        updates["lockout_until"] = None
```

The wrong-password write uses `str(player["playerId"])` like `login` does; the Step 1 test expects `"99"` (fixture `playerId` is int 99).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auth.py -v`
Expected: all PASS, including the untouched login/set_password/rate-limit tests.

- [ ] **Step 6: Commit**

```bash
git add routes/auth_routes.py tests/test_auth.py
git commit -m "fix: require session and apply lockout on /api/profile/update"
```

---

### Task 2: Cap `/api/mfa/verify` attempts, rate limit it, compare in constant time

**Files:**
- Modify: `routes/auth_routes.py` (imports; `login` MFA branch; `verify_mfa`)
- Modify: `tests/test_auth.py` (`TestMfaVerify` wrong-code test patch; add `TestMfaAttemptCap`)
- Modify: `tests/test_mfa_hashing.py` (append tests; do not touch existing ones)

**Interfaces:**
- Consumes: `_login_limiter`, `_rate_limited_response`, `_int_field`, `update_player_profile`, `_player_role`, `create_token`, `_set_session_cookie` (all already in `routes/auth_routes.py`).
- Produces: module constant `_MFA_MAX_ATTEMPTS = 5`; `verify_mfa(body: MfaVerifyRequest, request: Request)`; player document field `mfa_attempts` (int).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py`:

```python
class TestMfaAttemptCap:
    _CODE = "123456"
    _HASH = hashlib.sha256(_CODE.encode()).hexdigest()

    def _store(self, **extra):
        return {
            "playerId": 99, "email": "mfa@test.com", "role": "user",
            "mfa_token": self._HASH, "mfa_expiry": time.time() + 600,
            **extra,
        }

    def _verify(self, store, code):
        def fake_update(pid, updates):
            store.update(updates)
        with patch("services.db_service.get_player_by_id", side_effect=lambda pid: dict(store)), \
             patch("routes.auth_routes.update_player_profile", side_effect=fake_update):
            return client.post("/api/mfa/verify", json={"playerId": "99", "code": code})

    def test_four_wrong_codes_leave_code_valid(self):
        store = self._store()
        for _ in range(4):
            assert self._verify(store, "000000").status_code == 401
        assert store["mfa_attempts"] == 4
        assert store["mfa_token"] == self._HASH
        assert self._verify(store, self._CODE).status_code == 200

    def test_fifth_wrong_code_invalidates_code_and_correct_code_then_fails(self):
        store = self._store(mfa_attempts=4)
        resp = self._verify(store, "000000")
        assert resp.status_code == 401
        assert "log in again" in resp.json()["error"].lower()
        assert store["mfa_token"] is None
        assert store["mfa_expiry"] == 0
        assert self._verify(store, self._CODE).status_code == 401

    def test_correct_code_at_or_over_cap_is_rejected(self):
        store = self._store(mfa_attempts=5)
        assert self._verify(store, self._CODE).status_code == 401

    def test_successful_verify_clears_code_and_attempts(self):
        store = self._store(mfa_attempts=2)
        assert self._verify(store, self._CODE).status_code == 200
        assert store["mfa_token"] is None
        assert store["mfa_expiry"] == 0
        assert store["mfa_attempts"] == 0

    def test_nan_and_missing_attempts_count_as_zero(self):
        store = self._store(mfa_attempts=float("nan"))
        assert self._verify(store, "000000").status_code == 401
        assert store["mfa_attempts"] == 1
        store2 = self._store()
        assert self._verify(store2, "000000").status_code == 401
        assert store2["mfa_attempts"] == 1

    def test_unset_mfa_token_nan_returns_401_not_500(self):
        store = self._store(mfa_token=float("nan"))
        assert self._verify(store, self._CODE).status_code == 401

    def test_cap_invalidation_is_logged_without_the_code(self, caplog):
        store = self._store(mfa_attempts=4)
        with caplog.at_level("WARNING", logger="routes.auth_routes"):
            self._verify(store, "654321")
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "99" in text
        assert "654321" not in text

    def test_verify_is_rate_limited_and_shares_login_bucket(self):
        with patch("services.db_service.get_player_by_id", return_value=None):
            codes = [client.post("/api/mfa/verify",
                                 json={"playerId": "999", "code": "1"}).status_code
                     for _ in range(5)]
            resp = client.post("/api/mfa/verify", json={"playerId": "999", "code": "1"})
        assert codes == [404] * 5
        assert resp.status_code == 429
        assert int(resp.headers["Retry-After"]) >= 1
        with patch("routes.auth_routes.get_player_by_email", return_value=None):
            assert client.post("/api/login",
                               json={"email": "x@y.com", "password": "x"}).status_code == 429
```

Append to `tests/test_mfa_hashing.py`:

```python
def test_issuing_a_new_mfa_code_resets_attempt_counter():
    captured = {}
    fake_player = {
        "playerId": "42", "email": "test@example.com", "password_hash": "h",
        "mfa_enabled": True, "role": "user", "fullName": "T",
        "must_change_password": False, "lockout_until": None,
        "failed_login_attempts": 0, "mfa_attempts": 4,
    }
    with patch("routes.auth_routes.get_player_by_email", return_value=fake_player), \
         patch("routes.auth_routes.verify_password", return_value=True), \
         patch("routes.auth_routes._is_legacy_sha256", return_value=False), \
         patch("routes.auth_routes.update_player_profile",
               side_effect=lambda pid, u: captured.update(u)), \
         patch("routes.auth_routes.email_service"):
        from fastapi.testclient import TestClient
        from main import app
        resp = TestClient(app).post("/api/login",
                                    json={"email": "test@example.com", "password": "x"})
    assert resp.json()["status"] == "mfa_required"
    assert captured["mfa_attempts"] == 0


def test_mfa_digest_comparison_uses_hmac_compare_digest():
    import time
    import hmac
    raw = "123456"
    fake_player = {
        "playerId": "42", "mfa_token": _sha256(raw), "mfa_expiry": time.time() + 600,
        "role": "user", "fullName": "T", "email": "t@example.com",
    }
    real = hmac.compare_digest
    with patch("services.db_service.get_player_by_id", return_value=fake_player), \
         patch("routes.auth_routes.update_player_profile"), \
         patch("routes.auth_routes.create_token", return_value="jwt"), \
         patch("routes.auth_routes.hmac.compare_digest", side_effect=real) as spy:
        from fastapi.testclient import TestClient
        from main import app
        resp = TestClient(app).post("/api/mfa/verify", json={"playerId": "42", "code": raw})
    assert resp.status_code == 200
    spy.assert_called_once()


def test_mfa_non_ascii_stored_digest_returns_401_not_500():
    import time
    fake_player = {
        "playerId": "42", "mfa_token": "café", "mfa_expiry": time.time() + 600,
        "role": "user", "email": "t@example.com",
    }
    with patch("services.db_service.get_player_by_id", return_value=fake_player), \
         patch("routes.auth_routes.update_player_profile"):
        from fastapi.testclient import TestClient
        from main import app
        resp = TestClient(app).post("/api/mfa/verify", json={"playerId": "42", "code": "123456"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Fix the existing `TestMfaVerify` wrong-code test so it does not hit the real write path**

In `test_mfa_verify_wrong_code_returns_401`, add `patch("routes.auth_routes.update_player_profile")` to the `with` block (the handler now records the failed attempt). Assertions are unchanged.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `pytest tests/test_auth.py::TestMfaAttemptCap tests/test_mfa_hashing.py -v`
Expected: new tests FAIL (no counter, no limiter, no `hmac` attribute on `routes.auth_routes`); the two pre-existing hashing tests PASS.

- [ ] **Step 4: Implement**

In `routes/auth_routes.py`:

1. Add `import hmac` with the other stdlib imports.
2. Below `_login_limiter` add `_MFA_MAX_ATTEMPTS = 5`.
3. In `login`'s MFA branch, add `"mfa_attempts": 0` to the dict written with `mfa_token` and `mfa_expiry`.
4. Replace `verify_mfa` with:

```python
@router.post("/mfa/verify")
async def verify_mfa(body: MfaVerifyRequest, request: Request):
    # Unauthenticated by design (the caller has no session yet), so the per-IP
    # login bucket is the first line of defense against code guessing.
    limited = _rate_limited_response(request, _login_limiter)
    if limited:
        return limited
    try:
        pid = body.playerId
        code = body.code

        if not pid or not code:
            return JSONResponse(status_code=400, content={"error": "Missing player ID or verification code."})

        from services.db_service import get_player_by_id
        player = get_player_by_id(pid)
        if not player:
            return JSONResponse(status_code=404, content={"error": "Player not found."})
        stored_hash = player.get("mfa_token")
        expiry = player.get("mfa_expiry", 0)

        # An unset Firestore field arrives as float('nan') (truthy), so require
        # a real non-empty str before treating the code as pending.
        if not isinstance(stored_hash, str) or not stored_hash or time.time() > expiry:
            return JSONResponse(status_code=401, content={"error": "MFA code expired or invalid."})

        attempts = _int_field(player, "mfa_attempts")
        if attempts >= _MFA_MAX_ATTEMPTS:
            return JSONResponse(status_code=401, content={"error": "Too many incorrect codes. Please log in again to get a new code."})

        submitted_hash = hashlib.sha256(str(code).encode()).hexdigest()
        if not hmac.compare_digest(submitted_hash.encode(), stored_hash.encode()):
            attempts += 1
            if attempts >= _MFA_MAX_ATTEMPTS:
                update_player_profile(pid, {"mfa_token": None, "mfa_expiry": 0, "mfa_attempts": attempts})
                logger.warning("MFA code invalidated after %d wrong attempts for player %s", attempts, pid)
                return JSONResponse(status_code=401, content={"error": "Too many incorrect codes. Please log in again to get a new code."})
            update_player_profile(pid, {"mfa_attempts": attempts})
            return JSONResponse(status_code=401, content={"error": "Incorrect verification code."})

        update_player_profile(pid, {"mfa_token": None, "mfa_expiry": 0, "mfa_attempts": 0, "last_login": time.time()})
        # ... unchanged: role, create_token, response payload, _set_session_cookie ...
```

Keep the existing outer `except Exception` block exactly as is.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_auth.py tests/test_mfa_hashing.py -v`
Expected: all PASS, including the untouched `test_correct_mfa_code_still_verifies` and `TestMfaVerify` tests.

- [ ] **Step 6: Commit**

```bash
git add routes/auth_routes.py tests/test_auth.py tests/test_mfa_hashing.py
git commit -m "fix: cap MFA verify attempts, rate limit, constant-time compare"
```

---

### Task 3: Retire the dead Firebase Hosting config and unhardcode the recap link

**Files:**
- Modify: `firebase.json`
- Modify: `scripts/generate_weekly_summary.py` (extract the email HTML into a function; use `_app_base_url()`)
- Create: `tests/test_hosting_cleanup.py`

**Interfaces:**
- Consumes: `services.email_service._app_base_url() -> str` (existing; reads `APP_BASE_URL`, default `http://localhost:8000`, strips trailing slash).
- Produces: `scripts.generate_weekly_summary.build_recap_html(week: int, summary_text: str) -> str`.

**Verification result to record in the PR body:** on 2026-09-26, `curl https://winspool.web.app` returns HTTP 404 "Site Not Found" (no Firebase Hosting site is deployed for it), and `gcloud run services list` (per the spec) shows only `winspool` in `us-east1`. Disposition: Retire (spec's recommended default; the site is confirmed dead and the run directive says to retire dead hosting rewrites).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hosting_cleanup.py`:

```python
"""Dead Firebase Hosting config is retired and the recap email links to the real app URL."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_firebase_json_has_no_rewrites_to_missing_service():
    config = json.loads((ROOT / "firebase.json").read_text(encoding="utf-8"))
    hosting = config.get("hosting")
    if hosting is None:
        return
    hosts = hosting if isinstance(hosting, list) else [hosting]
    for h in hosts:
        for rewrite in h.get("rewrites", []):
            run = rewrite.get("run")
            if run:
                assert run["serviceId"] == "winspool"


def test_firebase_json_has_no_hosting_block():
    config = json.loads((ROOT / "firebase.json").read_text(encoding="utf-8"))
    assert "hosting" not in config


def test_recap_html_links_to_app_base_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://winspool-abc.a.run.app/")
    from scripts.generate_weekly_summary import build_recap_html
    html = build_recap_html(5, "Summary body")
    assert 'href="https://winspool-abc.a.run.app"' in html
    assert "web.app" not in html
    assert "Summary body" in html
    assert "Week 5" in html


def test_recap_html_defaults_to_local_base_url(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    from scripts.generate_weekly_summary import build_recap_html
    assert 'href="http://localhost:8000"' in build_recap_html(1, "x")
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_hosting_cleanup.py -v`
Expected: FAIL (`hosting` block still present; `build_recap_html` not defined).

- [ ] **Step 3: Implement**

1. `firebase.json`: first run `git grep -n "firebase.json"` and confirm nothing (Dockerfiles, scripts, CI) depends on a `hosting` key; if something does, stop and report. Then replace the whole content with `{}` and a trailing newline.
2. `scripts/generate_weekly_summary.py`: extend the import to `from services.email_service import send_weekly_recap_email, _app_base_url`. Add a module-level function returning the HTML that `main()` currently builds inline, with week and summary interpolated and the footer link built from the base URL:

```python
def build_recap_html(week: int, summary_text: str) -> str:
    """HTML wrapper for the weekly recap email. The footer link points at the
    deployed app (APP_BASE_URL), not a hardcoded hostname."""
    base_url = _app_base_url()
    return f"""
        <html>
            <!-- the exact existing markup, moved verbatim, with {args.week} replaced by {week} -->
                    <p style="margin-top: 20px; font-size: 0.8em; color: #888;">
                        This recap was generated by Gemini AI for the Wins Pool.
                        View the full standings at <a href="{base_url}">{base_url}</a>
                    </p>
        </html>
        """
```

Then in `main()` replace the inline `html_body = f"""..."""` with `html_body = build_recap_html(args.week, summary_text)`. Keep the existing heading line (including its pre-existing emoji) byte-for-byte in the moved markup; add no new emojis.
3. `docs/deployment.md` (~line 141) says the default URL is `https://YOUR_PROJECT_ID.web.app`: append one sentence noting Firebase Hosting is not used and the app is served from the Cloud Run URL set in `APP_BASE_URL`. Do not delete existing text. `docs/` is gitignored, so this edit stays uncommitted unless `git add -f` is used; use `git add -f docs/deployment.md` only if the file is already tracked (`git ls-files docs/deployment.md`); otherwise skip and mention it in the report.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_hosting_cleanup.py tests/test_deploy_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add firebase.json scripts/generate_weekly_summary.py tests/test_hosting_cleanup.py
git commit -m "chore: retire dead Firebase Hosting config, recap link reads APP_BASE_URL"
```

---

## Final verification (after Task 3, before merge)

- [ ] Run `pytest tests/ -q` in the worktree and compare failures against the same run on `main`. Known environment-failing tests are listed in the `reference_worktree_workflow_gotchas` memory. Any new failure blocks the merge.
- [ ] Whole-branch code review: spec compliance for auth spec sections 1 and 2 and cleanup section 1, with a security lens.
- [ ] Report residual items not in this plan: VAPID secret (auth spec section 3) and cleanup spec sections 2 and 3.
