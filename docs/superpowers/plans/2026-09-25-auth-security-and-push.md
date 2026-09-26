# Auth Rate Limiting, Push Broadcast and Infrastructure Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IP-based rate limiting to the auth endpoints (GitHub #43), an admin push broadcast endpoint with dead-subscription pruning (GitHub #88, hardening spec section 3), and codified Cloud Run scaling guardrails (hardening spec section 4).

**Architecture:** A small reusable in-process sliding-window limiter (`services/rate_limit_service.py`) with an injectable clock is used by `routes/auth_routes.py` and by the broadcast endpoint. `services/push_service.py` gets one shared delivery function used by both single-send and broadcast, which prunes subscriptions that the push service reports as gone (404/410). `deploy/deploy.ps1` pins `--max-instances` and `--concurrency`, matching what production already runs.

**Tech Stack:** FastAPI, Starlette `TestClient`, pywebpush, Firestore (`firebase_admin`), PowerShell deploy script, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md` sections 3 (Alerts / Push Notifications) and 4 (Cost Checks); GitHub issues #43 and #88.

## Global Constraints

- No emojis anywhere: code, comments, docs, commit messages.
- Zero-deletion policy: do not remove any existing feature, endpoint, or test. `routes/mock_draft_routes.py`'s own limiter stays as is.
- Match surrounding style; comments explain why.
- `docs/` is gitignored: commit plan/doc files under `docs/` with `git add -f <exact path>`. Never `git add -f tests`.
- Commit messages end with the two attribution lines given by the session harness.
- Known baseline failures (not regressions): `tests/test_loaded_version.py` (2), `test_firebase_schema`/`test_data_alignment` errors (5), flaky `tests/test_player_analytics.py::test_api_player_analytics_returns_200`. Record the baseline before Task 1.

## Design Rulings (made up front so no task stalls)

1. **Real route paths are `POST /api/login`, `POST /api/set_password` (underscore), and `GET /api/check_player`.** The task text says `set-password`; the code and issue #43 say `set_password`. Limit the real paths.
2. **Limits:** `/api/login` and `/api/set_password` share one bucket per IP, default 5 requests per 60 seconds (`AUTH_RATE_LIMIT_PER_MINUTE`). `/api/check_player` (issue #43 also lists it; enumeration vector) gets its own bucket, default 30 per 60 seconds (`AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE`), looser because the login screen calls it on every email entry.
3. **Every attempt counts** (success or failure), counted before any credential work, so unknown-email spraying (which returns 401 immediately) is throttled.
4. **429 response shape:** `{"error": "Too many requests. Try again in N seconds."}` with header `Retry-After: N` (integer seconds, minimum 1, computed from the oldest request in the window). The `error` key matches every other auth error the front end already renders.
5. **Client IP:** never trust the left-most `X-Forwarded-For` entry (a client can prepend anything and rotate it to dodge the limit; `routes/mock_draft_routes.py::_client_ip` does take the left-most, and is left unchanged). Use `TRUSTED_PROXY_HOPS` (default 1): take the entry that many positions from the RIGHT of `X-Forwarded-For`, falling back to `request.client.host` when the header is absent or shorter than the hop count.
6. **In-process state is correct here because production runs exactly one instance** (`autoscaling.knative.dev/maxScale: '1'`, verified read-only with `gcloud run services describe winspool --region us-east1`: `containerConcurrency: 80`, `timeoutSeconds: 3600`, cpu `1000m`, memory `512Mi`). Task 4 codifies this so a redeploy cannot silently change it. The draft room's WebSocket state is also per-process, so `max-instances=1` is required, not just a cost cap.
7. **Test isolation:** unit tests and the e2e server share one client IP, so limiter state must not leak across tests. `tests/conftest.py` gets an autouse fixture that resets all limiters; `tests_e2e/conftest.py` sets `AUTH_RATE_LIMIT_PER_MINUTE` and `AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE` to a large value in the server subprocess env (the e2e suite logs in dozens of times per minute from one IP).
8. **Push pruning:** a delivery failure whose exception carries `response.status_code` of 404 or 410 deletes that player's `push_subscription` field (Firestore `DELETE_FIELD`) and calls `signal_data_update("static")` so the warm players cache refreshes. Any other failure never deletes anything. String matching on exception text is not used.
9. **Broadcast safety:** admin only (`require_admin`); title 1-100 chars, body 1-500 chars after strip; one broadcast per 60 seconds per admin (issue #88 asks for a VAPID-quota guard); returns `{"total", "sent", "failed", "pruned"}`; returns 503 with `{"error": ...}` when VAPID keys are not configured. No admin UI is added (YAGNI; the endpoint is the deliverable).

## Review Focus

- Client sends `X-Forwarded-For` with 5 different spoofed left-most values from one real address: must still be limited (rightmost-hop rule).
- 6th login attempt in a window returns 429 with an integer `Retry-After >= 1`, and the 7th after the window passes succeeds again (window slides, not stuck).
- Valid credentials on request 1-5 succeed; a limiter that counts only failures would miss unknown-email spraying, so the test uses an unknown email.
- Limiter memory: buckets for IPs that stop calling must be evicted (no unbounded dict growth).
- Broadcast when zero players have a subscription: 200 with all zeros, not a 500.
- Broadcast when one subscription raises a non-410 error and another raises 410: only the 410 one is pruned, both counted correctly, loop continues.
- `get_db()` returns `None` in local-data mode: broadcast and prune must not crash.
- A player document whose `push_subscription` is missing, null, or NaN: skipped, not sent, not counted as failed.
- Body validation: empty, whitespace-only, or oversized title/body returns 422, sends nothing.

## File Structure

- Create `services/rate_limit_service.py`: `RateLimiter`, `client_ip()`, module-level named limiters registry with `reset_all()`.
- Modify `routes/auth_routes.py`: throttle `login`, `set_password`, `check_player`.
- Modify `routes/models.py`: `PushBroadcastRequest`.
- Modify `routes/admin_routes.py`: `POST /admin/push/broadcast`.
- Modify `services/push_service.py`: `_deliver()`, `_prune_subscription()`, `broadcast_push_notification()`, `is_configured()`; `send_push_notification()` reuses `_deliver()`.
- Modify `deploy/deploy.ps1` and `DEPLOY.md`: scaling flags and documentation.
- Modify `tests/conftest.py`, `tests_e2e/conftest.py`: limiter isolation.
- Tests: `tests/test_rate_limit_service.py` (create), `tests/test_auth.py`, `tests/test_push_service.py`, `tests/test_admin_routes.py`, `tests/test_deploy_config.py` (create).

---

### Task 1: Rate limiter service and test isolation

**Files:**
- Create: `services/rate_limit_service.py`
- Modify: `tests/conftest.py`, `tests_e2e/conftest.py`
- Test: `tests/test_rate_limit_service.py` (create)

**Interfaces:**
- Produces:
  - `class RateLimiter(max_requests: int, window_seconds: float, clock: Callable[[], float] = time.time)` with `check(key: str) -> tuple[bool, int]` (returns `(allowed, retry_after_seconds)`; `retry_after_seconds` is 0 when allowed, otherwise an int >= 1; records the request only when allowed) and `reset() -> None`. `max_requests` is a mutable public attribute.
  - `client_ip(request, trusted_hops: Optional[int] = None) -> str`
  - `get_limiter(name: str, max_requests: int, window_seconds: float = 60.0) -> RateLimiter` (creates once per name, registers it) and `reset_all() -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rate_limit_service.py`:

```python
"""services/rate_limit_service.py -- sliding-window per-key limiter and the
proxy-aware client IP helper."""
from types import SimpleNamespace

import pytest

from services.rate_limit_service import RateLimiter, client_ip, get_limiter, reset_all


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_allows_up_to_max_then_blocks():
    clock = _Clock()
    lim = RateLimiter(3, 60, clock=clock)
    assert [lim.check("ip")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = lim.check("ip")
    assert allowed is False and retry >= 1


def test_retry_after_counts_down_from_oldest_request():
    clock = _Clock()
    lim = RateLimiter(1, 60, clock=clock)
    lim.check("ip")
    clock.now += 45
    allowed, retry = lim.check("ip")
    assert allowed is False and retry == 15


def test_window_slides_and_allows_again():
    clock = _Clock()
    lim = RateLimiter(2, 60, clock=clock)
    lim.check("ip"); lim.check("ip")
    assert lim.check("ip")[0] is False
    clock.now += 61
    assert lim.check("ip")[0] is True


def test_keys_are_independent():
    lim = RateLimiter(1, 60, clock=_Clock())
    assert lim.check("a")[0] is True
    assert lim.check("b")[0] is True
    assert lim.check("a")[0] is False


def test_blocked_requests_are_not_recorded():
    clock = _Clock()
    lim = RateLimiter(1, 60, clock=clock)
    lim.check("ip")
    for _ in range(10):
        lim.check("ip")
    clock.now += 61
    assert lim.check("ip")[0] is True   # not extended by the blocked attempts


def test_idle_keys_are_evicted():
    clock = _Clock()
    lim = RateLimiter(5, 60, clock=clock)
    for i in range(50):
        lim.check(f"ip{i}")
    clock.now += 120
    lim.check("fresh")
    assert len(lim._buckets) == 1


def _req(xff=None, host="9.9.9.9"):
    headers = {"x-forwarded-for": xff} if xff is not None else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


def test_client_ip_uses_rightmost_hop_by_default():
    assert client_ip(_req("6.6.6.6, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_ignores_spoofed_left_entries():
    assert client_ip(_req("evil-1, evil-2, 1.2.3.4")) == "1.2.3.4"


def test_client_ip_respects_trusted_hops():
    assert client_ip(_req("1.1.1.1, 2.2.2.2, 3.3.3.3"), trusted_hops=2) == "2.2.2.2"


def test_client_ip_falls_back_when_header_short_or_missing():
    assert client_ip(_req(None)) == "9.9.9.9"
    assert client_ip(_req("1.1.1.1"), trusted_hops=3) == "9.9.9.9"


def test_client_ip_no_client_object():
    req = SimpleNamespace(headers={}, client=None)
    assert client_ip(req) == "unknown"


def test_registry_reuses_named_limiter_and_reset_all_clears():
    a = get_limiter("t-registry", 1)
    b = get_limiter("t-registry", 99)
    assert a is b
    a.check("ip")
    assert a.check("ip")[0] is False
    reset_all()
    assert a.check("ip")[0] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_rate_limit_service.py -v`
Expected: FAIL (`ModuleNotFoundError: services.rate_limit_service`).

- [ ] **Step 3: Implement**

Create `services/rate_limit_service.py`:

```python
"""services/rate_limit_service.py -- in-process sliding-window rate limiting.

State lives in this process's memory: it resets on restart and is not shared
across instances. That is deliberate and correct for this app because
production is pinned to a single Cloud Run instance (deploy/deploy.ps1
--max-instances=1, also required by the draft room's in-process WebSocket
state). This is abuse protection, not a correctness guarantee.
"""
import math
import os
import time
from typing import Callable, Optional


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: float, clock: Callable[[], float] = time.time):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._buckets: dict[str, list[float]] = {}

    def _evict_idle(self, cutoff: float) -> None:
        for key in [k for k, ts in self._buckets.items() if not ts or ts[-1] < cutoff]:
            del self._buckets[key]

    def check(self, key: str) -> tuple[bool, int]:
        """Record and allow the request, or refuse it.

        Returns (allowed, retry_after_seconds). Refused requests are NOT
        recorded, so hammering a blocked endpoint does not extend the block.
        """
        now = self._clock()
        cutoff = now - self.window_seconds
        self._evict_idle(cutoff)
        bucket = [t for t in self._buckets.get(key, []) if t > cutoff]
        if len(bucket) >= self.max_requests:
            self._buckets[key] = bucket
            retry = math.ceil(bucket[0] + self.window_seconds - now)
            return False, max(1, retry)
        bucket.append(now)
        self._buckets[key] = bucket
        return True, 0

    def reset(self) -> None:
        self._buckets.clear()


_registry: dict[str, RateLimiter] = {}


def get_limiter(name: str, max_requests: int, window_seconds: float = 60.0) -> RateLimiter:
    if name not in _registry:
        _registry[name] = RateLimiter(max_requests, window_seconds)
    return _registry[name]


def reset_all() -> None:
    for limiter in _registry.values():
        limiter.reset()


def client_ip(request, trusted_hops: Optional[int] = None) -> str:
    """Best-effort client address behind Cloud Run's proxy.

    Takes the X-Forwarded-For entry `trusted_hops` positions from the RIGHT
    (default env TRUSTED_PROXY_HOPS, 1): entries further left are supplied by
    the client and can be forged to dodge a per-IP limit. Falls back to the
    socket peer when the header is missing or shorter than the hop count.
    """
    if trusted_hops is None:
        try:
            trusted_hops = max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
        except ValueError:
            trusted_hops = 1
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(parts) >= trusted_hops:
            return parts[-trusted_hops]
    return request.client.host if request.client else "unknown"
```

Add to `tests/conftest.py` (additive, near the other autouse fixtures):

```python
@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Every TestClient request shares one client address; without this the
    per-IP auth limiter would leak state between unrelated tests."""
    from services.rate_limit_service import reset_all
    reset_all()
    yield
    reset_all()
```

In `tests_e2e/conftest.py`, next to `env["DISABLE_OUTBOUND_EMAIL"] = "true"` add:

```python
    # The suite logs in dozens of times a minute from one address; the
    # production auth rate limit (5/min per IP) would 429 unrelated tests.
    env["AUTH_RATE_LIMIT_PER_MINUTE"] = "100000"
    env["AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE"] = "100000"
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_rate_limit_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/rate_limit_service.py tests/conftest.py tests_e2e/conftest.py tests/test_rate_limit_service.py
git commit -m "feat: add in-process sliding-window rate limiter service"
```

---

### Task 2: Throttle the auth endpoints

**Files:**
- Modify: `routes/auth_routes.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `get_limiter`, `client_ip` from Task 1.
- Produces: `login`, `set_password`, and `check_player` handlers each take `request: Request` and return the 429 shape from Design Ruling 4 when limited.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_auth.py` (reuse that file's existing `client`/`TestClient` setup and its mocking of `get_player_by_email`; if it has none, patch `routes.auth_routes.get_player_by_email` to return `None`):

```python
def _login_unknown(client, email="nobody@example.com", headers=None):
    return client.post("/api/login", json={"email": email, "password": "x"}, headers=headers or {})


def test_login_rate_limited_after_five_attempts_per_ip(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    codes = [_login_unknown(c).status_code for _ in range(5)]
    assert codes == [401] * 5
    resp = _login_unknown(c)
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    assert "error" in resp.json()


def test_set_password_shares_the_login_bucket(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c)
    resp = c.post("/api/set_password", json={
        "email": "a@b.com", "password": "Aa1!aaaaaaaaaa", "confirm_password": "Aa1!aaaaaaaaaa"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_spoofed_left_forwarded_for_entries_do_not_evade_limit(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    codes = []
    for i in range(6):
        codes.append(_login_unknown(c, headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"}).status_code)
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_different_client_ips_have_separate_buckets(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c, headers={"X-Forwarded-For": "203.0.113.7"})
    assert _login_unknown(c, headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 401


def test_check_player_has_its_own_looser_bucket(monkeypatch):
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    for _ in range(5):
        _login_unknown(c)                        # exhaust the login bucket
    assert c.get("/api/check_player", params={"email": "a@b.com"}).status_code == 200
    codes = [c.get("/api/check_player", params={"email": "a@b.com"}).status_code for _ in range(40)]
    assert 429 in codes


def test_limit_is_configurable_via_env(monkeypatch):
    import services.rate_limit_service as rl
    from routes import auth_routes
    monkeypatch.setattr(auth_routes._login_limiter, "max_requests", 2)
    monkeypatch.setattr("routes.auth_routes.get_player_by_email", lambda e: None)
    from fastapi.testclient import TestClient
    from main import app
    c = TestClient(app)
    assert [_login_unknown(c).status_code for _ in range(3)] == [401, 401, 429]
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_auth.py -k "rate_limited or shares_the_login or spoofed or separate_buckets or own_looser or configurable" -v`
Expected: FAIL (no 429 is ever returned; `_login_limiter` attribute missing).

- [ ] **Step 3: Implement**

In `routes/auth_routes.py`:

```python
from fastapi import APIRouter, Depends, Request, Response
from services.rate_limit_service import get_limiter, client_ip
```

Add after `_IS_PROD`:

```python
def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


# login and set_password share one bucket: both accept guessable secrets and
# an attacker should not get a fresh allowance by alternating endpoints.
# check_player (email enumeration) gets a looser bucket of its own because
# the sign-in screen calls it on every email entry.
_login_limiter = get_limiter("auth-login", _env_int("AUTH_RATE_LIMIT_PER_MINUTE", 5))
_lookup_limiter = get_limiter("auth-lookup", _env_int("AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE", 30))


def _rate_limited_response(request: Request, limiter) -> Optional[JSONResponse]:
    """A 429 JSONResponse (with Retry-After) if this client is over the limit, else None.

    Called first in each guarded handler so every attempt counts, including
    unknown-email requests that would otherwise return before any lockout logic.
    """
    ip = client_ip(request)
    allowed, retry_after = limiter.check(ip)
    if allowed:
        return None
    logger.warning("auth rate limit hit: ip=%s path=%s retry_after=%ss", ip, request.url.path, retry_after)
    return JSONResponse(
        status_code=429,
        content={"error": f"Too many requests. Try again in {retry_after} seconds."},
        headers={"Retry-After": str(retry_after)},
    )
```

(add `from typing import Optional`). Then change signatures and add the first lines:

```python
@router.get("/check_player")
async def check_player(request: Request, email: str):
    limited = _rate_limited_response(request, _lookup_limiter)
    if limited:
        return limited
    ...
```
```python
@router.post("/set_password")
async def set_password(request: Request, body: SetPasswordRequest):
    limited = _rate_limited_response(request, _login_limiter)
    if limited:
        return limited
    try:
    ...
```
```python
@router.post("/login")
async def login(request: Request, body: LoginRequest):
    limited = _rate_limited_response(request, _login_limiter)
    if limited:
        return limited
    try:
    ...
```

Leave every other line of those handlers unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_auth.py tests/test_mfa_hashing.py tests/test_security_audit.py tests/test_draft_auth.py -v`
Expected: PASS. (Existing tests that log in repeatedly are protected by the autouse reset from Task 1. Any pre-existing test that itself posts more than 5 logins inside ONE test needs `monkeypatch.setattr(routes.auth_routes._login_limiter, "max_requests", 1000)`; add that only where a test fails for this reason and say so in the report.)

- [ ] **Step 5: Commit**

```bash
git add routes/auth_routes.py tests/test_auth.py
git commit -m "feat: IP-based rate limiting on login, set_password and check_player"
```

---

### Task 3: Push delivery pruning and admin broadcast

**Files:**
- Modify: `services/push_service.py`
- Modify: `routes/models.py`
- Modify: `routes/admin_routes.py`
- Test: `tests/test_push_service.py`, `tests/test_admin_routes.py`

**Interfaces:**
- Consumes: `get_limiter` (Task 1); existing `require_admin`, `server_error`.
- Produces:
  - `push_service.is_configured() -> bool`
  - `push_service._deliver(player_id, sub: dict, title: str, body: str) -> str` returning `"sent"`, `"failed"`, or `"pruned"`
  - `push_service.broadcast_push_notification(title: str, body: str) -> dict` with keys `total`, `sent`, `failed`, `pruned`
  - `POST /api/admin/push/broadcast` body `{"title": str, "body": str}` returning that dict.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_push_service.py`:

```python
class _Resp:
    def __init__(self, status):
        self.status_code = status


class _PushError(Exception):
    def __init__(self, status):
        super().__init__(f"push failed {status}")
        self.response = _Resp(status)


def _player_doc(pid, sub):
    doc = MagicMock()
    doc.id = str(pid)
    doc.to_dict.return_value = {"push_subscription": sub} if sub is not None else {}
    return doc


def _configure(monkeypatch):
    monkeypatch.setattr(push_service, "_VAPID_PUBLIC", "pub")
    monkeypatch.setattr(push_service, "_VAPID_PRIVATE", "priv")


def test_410_on_single_send_prunes_subscription(monkeypatch):
    _configure(monkeypatch)
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    db = _mock_db_with_doc(exists=True, subscription=sub)
    with patch("services.db_service.get_db", return_value=db), \
         patch("services.db_service.signal_data_update") as signal, \
         patch("pywebpush.webpush", side_effect=_PushError(410)):
        assert push_service.send_push_notification(7, "t", "b") is False
    db.collection.return_value.document.return_value.update.assert_called_once()
    signal.assert_called_once_with("static")


def test_non_gone_failure_never_prunes(monkeypatch):
    _configure(monkeypatch)
    sub = {"endpoint": "https://push.example.com/x", "keys": {"auth": "a", "p256dh": "b"}}
    db = _mock_db_with_doc(exists=True, subscription=sub)
    with patch("services.db_service.get_db", return_value=db), \
         patch("pywebpush.webpush", side_effect=_PushError(500)):
        assert push_service.send_push_notification(7, "t", "b") is False
    db.collection.return_value.document.return_value.update.assert_not_called()


def test_broadcast_counts_sent_failed_and_pruned(monkeypatch):
    _configure(monkeypatch)
    sub = lambda n: {"endpoint": f"https://push.example.com/{n}", "keys": {"auth": "a", "p256dh": "b"}}
    docs = [_player_doc(1, sub(1)), _player_doc(2, sub(2)), _player_doc(3, sub(3)),
            _player_doc(4, None), _player_doc(5, float("nan"))]
    db = MagicMock()
    db.collection.return_value.stream.return_value = docs
    outcomes = {"https://push.example.com/1": None,
                "https://push.example.com/2": _PushError(404),
                "https://push.example.com/3": _PushError(500)}

    def fake_webpush(subscription_info, **kwargs):
        err = outcomes[subscription_info["endpoint"]]
        if err:
            raise err

    with patch("services.db_service.get_db", return_value=db), \
         patch("services.db_service.signal_data_update"), \
         patch("pywebpush.webpush", side_effect=fake_webpush):
        result = push_service.broadcast_push_notification("Hello", "World")
    assert result == {"total": 3, "sent": 1, "failed": 1, "pruned": 1}


def test_broadcast_with_no_subscriptions_returns_zeros(monkeypatch):
    _configure(monkeypatch)
    db = MagicMock()
    db.collection.return_value.stream.return_value = [_player_doc(1, None)]
    with patch("services.db_service.get_db", return_value=db):
        assert push_service.broadcast_push_notification("t", "b") == {
            "total": 0, "sent": 0, "failed": 0, "pruned": 0}


def test_broadcast_in_local_mode_without_db_does_not_crash(monkeypatch):
    _configure(monkeypatch)
    with patch("services.db_service.get_db", return_value=None):
        assert push_service.broadcast_push_notification("t", "b") == {
            "total": 0, "sent": 0, "failed": 0, "pruned": 0}


def test_broadcast_logs_a_summary_line(monkeypatch, caplog):
    _configure(monkeypatch)
    db = MagicMock()
    db.collection.return_value.stream.return_value = []
    with patch("services.db_service.get_db", return_value=db), caplog.at_level(logging.INFO):
        push_service.broadcast_push_notification("t", "b")
    assert any("broadcast complete" in r.message for r in caplog.records)
```

Append to `tests/test_admin_routes.py` (use that file's existing admin-token client pattern; endpoints require `Authorization: <admin_token>` where the fixture returns `"Bearer ..."`):

```python
def _bcast(client, admin_token, **body):
    payload = {"title": "Draft tonight", "body": "8pm ET, be there."}
    payload.update(body)
    return client.post("/api/admin/push/broadcast", json=payload, headers={"Authorization": admin_token})


def test_broadcast_requires_admin(client, auth_token):
    resp = client.post("/api/admin/push/broadcast", json={"title": "t", "body": "b"},
                       headers={"Authorization": auth_token})
    assert resp.status_code in (401, 403)


def test_broadcast_requires_auth(client):
    assert client.post("/api/admin/push/broadcast", json={"title": "t", "body": "b"}).status_code in (401, 403)


def test_broadcast_delivers_and_returns_summary(client, admin_token, monkeypatch):
    import services.push_service as ps
    monkeypatch.setattr(ps, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(ps, "broadcast_push_notification",
                        lambda t, b: calls.append((t, b)) or {"total": 2, "sent": 2, "failed": 0, "pruned": 0})
    resp = _bcast(client, admin_token)
    assert resp.status_code == 200
    assert resp.json() == {"total": 2, "sent": 2, "failed": 0, "pruned": 0}
    assert calls == [("Draft tonight", "8pm ET, be there.")]


def test_broadcast_503_when_push_not_configured(client, admin_token, monkeypatch):
    import services.push_service as ps
    monkeypatch.setattr(ps, "is_configured", lambda: False)
    resp = _bcast(client, admin_token)
    assert resp.status_code == 503 and "error" in resp.json()


@pytest.mark.parametrize("bad", [
    {"title": ""}, {"title": "   "}, {"body": ""}, {"body": "x" * 501}, {"title": "x" * 101},
])
def test_broadcast_rejects_invalid_bodies_and_sends_nothing(client, admin_token, monkeypatch, bad):
    import services.push_service as ps
    monkeypatch.setattr(ps, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(ps, "broadcast_push_notification", lambda t, b: sent.append(1))
    assert _bcast(client, admin_token, **bad).status_code == 422
    assert sent == []


def test_broadcast_second_call_within_a_minute_is_429(client, admin_token, monkeypatch):
    import services.push_service as ps
    monkeypatch.setattr(ps, "is_configured", lambda: True)
    monkeypatch.setattr(ps, "broadcast_push_notification",
                        lambda t, b: {"total": 0, "sent": 0, "failed": 0, "pruned": 0})
    assert _bcast(client, admin_token).status_code == 200
    second = _bcast(client, admin_token)
    assert second.status_code == 429 and int(second.headers["Retry-After"]) >= 1
```

If `tests/test_admin_routes.py` has no `client` fixture, define `client = TestClient(app)` at module level the way the sibling tests do and drop the fixture parameter.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_push_service.py tests/test_admin_routes.py -k "broadcast or prune or 410 or non_gone" -v`
Expected: FAIL (functions and route missing).

- [ ] **Step 3: Implement**

`services/push_service.py`: add near the top constants:

```python
# HTTP statuses a push service returns when a subscription is permanently
# invalid (unsubscribed, expired, app uninstalled). Retrying is pointless and
# keeping the token just adds a failure to every future broadcast.
_GONE_STATUS_CODES = (404, 410)


def is_configured() -> bool:
    return bool(_VAPID_PUBLIC and _VAPID_PRIVATE)


def _is_gone_error(exc: Exception) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in _GONE_STATUS_CODES


def _prune_subscription(player_id) -> None:
    """Delete a dead push_subscription and tell every process the players cache changed."""
    from services.db_service import get_db, signal_data_update
    db = get_db()
    if db is None:
        return
    from firebase_admin import firestore
    db.collection("players").document(str(player_id)).update(
        {"push_subscription": firestore.DELETE_FIELD}
    )
    signal_data_update("static")
    logger.info("push_service: pruned dead push subscription for player %s", player_id)


def _deliver(player_id, sub: dict, title: str, body: str) -> str:
    """Send one notification. Returns "sent", "failed", or "pruned"."""
    from pywebpush import webpush
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=_VAPID_PRIVATE,
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        logger.info("push_service: sent push to player %s", player_id)
        return "sent"
    except Exception as e:
        logger.warning("push_service: send failed for player %s: %s", player_id, e)
        if _is_gone_error(e):
            try:
                _prune_subscription(player_id)
                return "pruned"
            except Exception:
                logger.exception("push_service: prune failed for player %s", player_id)
        return "failed"
```

Rewrite the body of `send_push_notification` so the `webpush(...)` call is replaced by `return _deliver(player_id, sub, title, body) == "sent"` inside the existing `try`, keeping the VAPID check, the no-subscription branch, and the outer `except Exception` log. Keep the existing log strings that current tests assert on ("VAPID keys not configured", "no push subscription stored", "no player document", "send failed").

Add:

```python
def broadcast_push_notification(title: str, body: str) -> dict:
    """Send title/body to every player with a stored push_subscription.

    Returns {"total", "sent", "failed", "pruned"} where total counts only
    players that actually had a subscription. One bad subscription never
    stops the loop.
    """
    from services.db_service import get_db
    counts = {"total": 0, "sent": 0, "failed": 0, "pruned": 0}
    db = get_db()
    if db is None:
        logger.warning("push_service: broadcast skipped, no database (local data mode)")
        return counts
    for doc in db.collection("players").stream():
        sub = (doc.to_dict() or {}).get("push_subscription")
        if not isinstance(sub, dict):
            continue
        counts["total"] += 1
        outcome = _deliver(doc.id, sub, title, body)
        counts[outcome] += 1
    logger.info(
        "push_service: broadcast complete total=%d sent=%d failed=%d pruned=%d",
        counts["total"], counts["sent"], counts["failed"], counts["pruned"],
    )
    return counts
```

`routes/models.py`: add (follow the file's existing pydantic import style):

```python
class PushBroadcastRequest(BaseModel):
    title: constr(strip_whitespace=True, min_length=1, max_length=100)
    body: constr(strip_whitespace=True, min_length=1, max_length=500)
```

(If the file uses pydantic v2 `Field`/`StringConstraints` conventions, mirror them instead of `constr`.)

`routes/admin_routes.py`: import `Request`, `PushBroadcastRequest`, `get_limiter`; add after the recap endpoints:

```python
_broadcast_limiter = get_limiter("admin-push-broadcast", 1, 60.0)


@router.post("/admin/push/broadcast")
async def broadcast_push(body: PushBroadcastRequest, admin: dict = Depends(require_admin)):
    """Send a custom push notification to every subscribed player (admin only).

    One broadcast per minute per admin: each call fans out one VAPID request
    per subscriber, so this is the quota guard issue #88 asks for.
    """
    try:
        import services.push_service as push_service
        if not push_service.is_configured():
            return JSONResponse(status_code=503, content={"error": "Push notifications are not configured."})
        allowed, retry_after = _broadcast_limiter.check(str(admin.get("sub")))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"error": f"A broadcast was sent recently. Try again in {retry_after} seconds."},
                headers={"Retry-After": str(retry_after)},
            )
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(push_service.broadcast_push_notification, body.title, body.body)
        logger.info("admin push broadcast by player %s: %s", admin.get("sub"), result)
        return JSONResponse(content=result)
    except Exception:
        logger.exception("Unhandled error in broadcast_push")
        return server_error()
```

Check how `require_admin` names its returned claims in `services/session_service.py` (the other admin routes discard it as `_`); use the actual claim key for the player id.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_push_service.py tests/test_admin_routes.py tests/test_draft_notifications.py -v`
Expected: PASS (existing push and draft-notification tests must be unchanged).

- [ ] **Step 5: Commit**

```bash
git add services/push_service.py routes/models.py routes/admin_routes.py tests/test_push_service.py tests/test_admin_routes.py
git commit -m "feat: admin push broadcast endpoint and dead-subscription pruning"
```

---

### Task 4: Cloud Run scaling guardrails in deploy.ps1

**Files:**
- Modify: `deploy/deploy.ps1`
- Modify: `DEPLOY.md`
- Test: `tests/test_deploy_config.py` (create)

**Interfaces:**
- Produces: `gcloud run deploy winspool` now passes `--max-instances=1` and `--concurrency=80`; documented in `DEPLOY.md`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_deploy_config.py`:

```python
"""deploy/deploy.ps1 must pin the web service's scaling limits. Production
already runs max-instances=1 (set out of band); a redeploy that omits the flag
keeps the current value today, but only by accident. Codifying it makes the
guardrail reviewable. It is also required for correctness: the draft room's
WebSocket state and the auth rate limiter are both in-process."""
import pathlib
import re

SCRIPT = (pathlib.Path(__file__).resolve().parent.parent / "deploy" / "deploy.ps1").read_text(encoding="utf-8")


def _web_deploy_block() -> str:
    m = re.search(r"gcloud run deploy winspool.*?(?=\n\s*\nif \(\$LASTEXITCODE)", SCRIPT, re.S)
    assert m, "web service deploy command not found"
    return m.group(0)


def test_web_service_pins_max_instances_to_one():
    assert "--max-instances=1" in _web_deploy_block()


def test_web_service_pins_concurrency():
    assert re.search(r"--concurrency=\d+", _web_deploy_block())


def test_guardrail_rationale_is_documented_in_script():
    assert "in-process" in SCRIPT and "max-instances" in SCRIPT
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_deploy_config.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `deploy/deploy.ps1`, extend the comment above `gcloud run deploy winspool` (keep the existing `--timeout` comment) with:

```powershell
# --max-instances=1 is a correctness requirement, not just a cost cap: the
# live draft room's WebSocket ConnectionManager/connected_players and the auth
# rate limiter (services/rate_limit_service.py) keep state in-process, so a
# second instance would split a draft room in two and double every per-IP
# limit. It also bounds worst-case spend. This matches the value production
# already runs (verified with `gcloud run services describe winspool`);
# raising it requires moving that state out of process first.
# --concurrency=80 is Cloud Run's default, pinned so it is reviewable; with
# one 1-vCPU instance it caps in-flight requests instead of letting a burst
# queue unbounded work.
```

and add to the `gcloud run deploy winspool` argument list (after `--timeout=3600 \``):

```powershell
    --max-instances=1 `
    --concurrency=80 `
```

Add to `DEPLOY.md` a section "Scaling and cost guardrails" recording: verified live settings (maxScale 1, containerConcurrency 80, timeout 3600, cpu 1, memory 512Mi as of 2026-09-25), why max-instances must stay 1, how to re-verify (`gcloud run services describe winspool --region us-east1 --project fishbone-wins-pool --format="yaml(spec.template.metadata.annotations,spec.template.spec.containerConcurrency)"`), and that the scheduled jobs are separate Cloud Run Jobs with their own task limits. If `DEPLOY.md` is gitignored, use `git add -f DEPLOY.md`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_deploy_config.py -v`
Expected: PASS. Do NOT run the deploy script.

- [ ] **Step 5: Commit**

```bash
git add deploy/deploy.ps1 tests/test_deploy_config.py
git add -f DEPLOY.md
git commit -m "chore: pin Cloud Run max-instances and concurrency in deploy script"
```

---

### Task 5: Full verification

- [ ] **Step 1:** Run `pytest tests/ -n auto -q`. Expected: only the known baseline failures.
- [ ] **Step 2:** Run `pytest tests_e2e --collect-only -q` to confirm the e2e suite still collects, and, if the e2e environment variables are available, run `pytest tests_e2e/test_login.py tests_e2e/test_lockout.py -v` to confirm the limiter does not interfere (the e2e server runs with the raised limits from Task 1).
- [ ] **Step 3:** Manual smoke against a local server (`USE_LOCAL_DATA=True JWT_SECRET=<32+ chars> uvicorn main:app --port 8124`): six rapid `curl -i -X POST localhost:8124/api/login -H "Content-Type: application/json" -d '{"email":"x@y.com","password":"z"}'` calls; the sixth must be 429 with a `Retry-After` header. Record the output in the report.
- [ ] **Step 4:** Commit nothing new unless a fix was needed.
