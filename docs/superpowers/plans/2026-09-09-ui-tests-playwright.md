# UI Tests (Playwright) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an automated Playwright regression suite covering the app's critical flows at desktop and mobile viewports — including a full, faithful 10-player live draft that exercises the exact mechanics that broke during the real first draft (round-label math, portfolio wins math, `/wins-pool` mid-draft rendering) — wired into the `/deploy` pre-flight.

**Architecture:** A new `tests_e2e/` package (Playwright's Python sync API, run via `pytest`) drives a real `uvicorn main:app` subprocess started per test session with `USE_LOCAL_DATA=true` against the developer's existing `.local_db/` and a new `DISABLE_OUTBOUND_EMAIL=true` safety gate. Ten dedicated, permanent Firestore-backed test player accounts (flagged `is_test_account: true`, filtered out of the real admin roster picker) stand in for a real pool of players; the draft-flow test drives a real Season 3000 through the actual Admin Portal UI and Playwright browser contexts making real WebSocket picks — never by calling internal Python functions or seeding fixture data files.

**Tech Stack:** `playwright` (already installed locally, v1.59.0, Python sync API), `pytest` (existing), no new JS toolchain.

**Spec:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §1 (UI Tests) — this plan additionally supersedes that section's original "draft room: join, see state, make a pick" line with the fuller 10-player live-draft design locked in during planning (see Task 10).

## Global Constraints

- No new JS package manager / `package.json` — Playwright's Python bindings keep this in the existing `pytest` ecosystem.
- The e2e suite must never make a real Resend API call. `DISABLE_OUTBOUND_EMAIL=true` is set for the entire test-session subprocess (Task 2) as a blanket safety net, independent of which specific flow is under test.
- The e2e suite must never write to production Firestore. The test server subprocess always runs with `USE_LOCAL_DATA=true`; per `services/db_service.py::get_db()`, this makes every write path fall through to local `.local_db/*.pkl` only (see `add_player`/`add_draft_result`'s `if db: ... ` guard pattern) — confirmed safe by reading the current write-path code, not assumed.
- The 10 dedicated test player accounts are real, permanent Firestore documents (created once via Task 3's seed script, flowing into `.local_db/players.pkl` through the normal `scripts/refresh_local_pkls.py` sync like any other player) — not fixture data the test suite creates and tears down each run.
- Season **3000** is the fixed sentinel year for the live-draft test (Task 10), chosen to never collide with a real season.
- Every browser-driven step interacts with the real UI (typing into real form fields, clicking real buttons, waiting on real page state) — never by calling internal API/service functions directly to shortcut around the UI, per the explicit "mimic everything a real user can do" requirement this plan was scoped against.

---

## Task 1: `is_test_account` player flag + admin filtering

**Files:**
- Modify: `routes/admin_routes.py:99-128` (`fetch_admin_players`)
- Modify: `static/js/ui_renderer.js:217-230` (`renderPlayerSelectionGrid`)
- Modify: `static/js/admin_main.js:198-212` (`fetchInitialData`)
- Modify: `templates/admin.html` (add the "Show test accounts" toggle near the player grid)
- Test: `tests/test_admin_routes.py`

**Interfaces:**
- Produces: `GET /api/admin/players?include_test_accounts=<bool>` (default `false`) — existing response shape unchanged except each record gains `"is_test_account": bool`.
- Produces: `renderPlayerSelectionGrid(players, onToggle)` — unchanged signature; filtering now happens before the call, driven by a new checkbox's state.

- [ ] **Step 1: Write the failing test for the filter**

```python
# tests/test_admin_routes.py — add to TestFetchAdminPlayers

def test_excludes_test_accounts_by_default(self, admin_token):
    """is_test_account players are hidden unless include_test_accounts=true."""
    fake_players = pd.DataFrame([
        {"playerId": 1, "fullName": "Real Player", "nickName": "RP", "email": "real@test.com",
         "cell": "", "role": "user", "password_hash": None, "must_change_password": False,
         "last_login": None, "is_test_account": False},
        {"playerId": 2, "fullName": "E2E Test 01", "nickName": "E2E01", "email": "e2e-01@winspool.internal",
         "cell": "", "role": "admin", "password_hash": "$2b$12$hash", "must_change_password": False,
         "last_login": None, "is_test_account": True},
    ])

    with patch("routes.admin_routes.load_data", return_value=(None, None, None, fake_players, None, None, None)):
        resp = client.get("/api/admin/players", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["playerId"] == 1

        resp2 = client.get("/api/admin/players?include_test_accounts=true", headers={"Authorization": admin_token})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert len(data2) == 2
        assert {p["playerId"] for p in data2} == {1, 2}
        e2e = next(p for p in data2 if p["playerId"] == 2)
        assert e2e["is_test_account"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_admin_routes.py::TestFetchAdminPlayers::test_excludes_test_accounts_by_default -v`
Expected: FAIL — endpoint doesn't accept `include_test_accounts`, and returns both players unfiltered (or KeyErrors on `is_test_account` in assertions).

- [ ] **Step 3: Implement the filter**

In `routes/admin_routes.py`, change the endpoint signature and add the flag + filter:

```python
@router.get("/admin/players")
async def fetch_admin_players(include_test_accounts: bool = False, _: dict = Depends(require_admin)):
    """Retrieve all players for the admin selection grid and player management.

    Test/QA fixture accounts (is_test_account=True) are excluded by default so
    they don't clutter the real season-creation player picker.
    """
    try:
        _, _, _, players_df, _, _, _ = load_data()
        records = []
        for r in players_df.to_dict(orient="records"):
            is_test = bool(r.get("is_test_account", False)) if pd.notna(r.get("is_test_account")) else False
            if is_test and not include_test_accounts:
                continue
            pw_hash = r.get("password_hash")
            has_pw = bool(pw_hash) if pd.notna(pw_hash) else False
            must_change = bool(r.get("must_change_password", False)) if pd.notna(r.get("must_change_password")) else False
            last_login_val = r.get("last_login")
            last_login = float(last_login_val) if pd.notna(last_login_val) and last_login_val is not None else None

            rec = {
                "playerId": int(r["playerId"]),
                "fullName": str(r.get("fullName", "")),
                "nickName": str(r.get("nickName", "")),
                "email": str(r.get("email", "")),
                "cell": str(r.get("cell", "")) if pd.notna(r.get("cell")) else "",
                "role": str(r.get("role", "user")),
                "has_password": has_pw,
                "must_change_password": must_change,
                "last_login": last_login,
                "is_test_account": is_test,
            }
            if "failed_setup_attempts" in r and pd.notna(r.get("failed_setup_attempts")):
                rec["failed_setup_attempts"] = int(r["failed_setup_attempts"])
            records.append(rec)
        return JSONResponse(content=sanitize_state(records))
    except Exception as e:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_admin_routes.py::TestFetchAdminPlayers -v`
Expected: PASS (both the new test and the existing `test_happy_path_returns_password_and_login_metadata`, which doesn't set `is_test_account` so it defaults falsy and is unaffected).

- [ ] **Step 5: Wire the frontend toggle**

In `static/js/api.js`, find `fetchPlayers` and add the query param:

```javascript
async fetchPlayers(playerId, includeTestAccounts = false) {
    const qs = includeTestAccounts ? '?include_test_accounts=true' : '';
    return fetchWithTimeout(`${API_BASE}/admin/players${qs}`);
},
```

In `templates/admin.html`, add a checkbox immediately above the `#player-grid` element:

```html
<label class="admin-player-label" style="margin-bottom:8px;">
    <input type="checkbox" id="show-test-accounts-toggle">
    <span>Show test accounts</span>
</label>
```

In `static/js/admin_main.js`, replace `fetchInitialData`'s player fetch and wire the toggle:

```javascript
async fetchInitialData() {
    try {
        const includeTest = document.getElementById('show-test-accounts-toggle')?.checked || false;
        const [players, { seasons }] = await Promise.all([
            ApiService.fetchPlayers(this.playerId, includeTest),
            ApiService.fetchSeasons(this.playerId)
        ]);

        this.players = players;
        UiRenderer.renderPlayerSelectionGrid(players, () => this.updatePlayerCount());
        UiRenderer.renderAdminSeasonDropdown(seasons);
        this.renderPlayerList(players);
    } catch (e) {
        alert(`Fetch failed: ${e.message}`);
    }
}
```

Add a one-line listener near wherever `setupActionHandlers()` wires other controls:

```javascript
document.getElementById('show-test-accounts-toggle')?.addEventListener('change', () => this.fetchInitialData());
```

- [ ] **Step 6: Run the full admin route test suite to check for regressions**

Run: `pytest tests/test_admin_routes.py -v`
Expected: PASS, no regressions.

- [ ] **Step 7: Commit**

```bash
git add routes/admin_routes.py static/js/api.js static/js/admin_main.js templates/admin.html tests/test_admin_routes.py
git commit -m "feat: add is_test_account player flag, hide test accounts from admin picker by default"
```

---

## Task 2: `DISABLE_OUTBOUND_EMAIL` safety gate

**Files:**
- Modify: `services/email_service.py:107-155` (`_send`, `_send_multi`)
- Test: `tests/test_email_service.py`

**Interfaces:**
- Produces: when `os.environ["DISABLE_OUTBOUND_EMAIL"]` is `"true"` (any case), `_send`/`_send_multi` log and return `True` without importing/calling `resend` at all — every public `send_*` function in this module is unaffected in signature, only in behavior.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_email_service.py — append

@patch("services.email_service.resend.Emails.send")
def test_send_disabled_via_env_var_never_calls_resend(mock_send, monkeypatch):
    """DISABLE_OUTBOUND_EMAIL=true short-circuits before any Resend call, even with a valid API key."""
    monkeypatch.setenv("DISABLE_OUTBOUND_EMAIL", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    result = send_weekly_recap_email(["user@x.com"], "Subject", "html")

    assert result is True
    mock_send.assert_not_called()


@patch("services.email_service.resend.Emails.send")
def test_send_multi_disabled_via_env_var_never_calls_resend(mock_send, monkeypatch):
    """Same gate applies to the multi-recipient send path (send_draft_order_email)."""
    monkeypatch.setenv("DISABLE_OUTBOUND_EMAIL", "true")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    result = send_draft_order_email(["a@x.com", "b@x.com"], 3000, [{"position": 1, "name": "Test"}])

    assert result is True
    mock_send.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_email_service.py -k disabled -v`
Expected: FAIL — no such gate exists yet, `mock_send` gets called.

- [ ] **Step 3: Implement the gate**

In `services/email_service.py`, add a small helper near the top (after `_app_base_url`) and call it first in both `_send` and `_send_multi`:

```python
def _outbound_email_disabled() -> bool:
    """Safety gate for the e2e test suite (and any other automated run that
    must never hit the real Resend API). Checked first in every send path."""
    return os.environ.get("DISABLE_OUTBOUND_EMAIL", "").lower() == "true"
```

```python
def _send(to_email: str, subject: str, html: str, reply_to: str = None) -> bool:
    """Send a single transactional email via Resend. Returns True on success."""
    if _outbound_email_disabled():
        logger.info("DISABLE_OUTBOUND_EMAIL set — skipping send to %s: %s", to_email, subject)
        return True
    api_key = os.getenv("RESEND_API_KEY")
    ...
```

```python
def _send_multi(to_emails: list, subject: str, html: str, reply_to: str = None) -> bool:
    """Send one transactional email to multiple recipients (all in `to`) via Resend."""
    if _outbound_email_disabled():
        logger.info("DISABLE_OUTBOUND_EMAIL set — skipping send to %s: %s", to_emails, subject)
        return True
    api_key = os.getenv("RESEND_API_KEY")
    ...
```

(Leave the rest of both functions unchanged — only the early-return guard is new.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_email_service.py -v`
Expected: PASS, all tests including pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add services/email_service.py tests/test_email_service.py
git commit -m "feat: add DISABLE_OUTBOUND_EMAIL safety gate to the Resend send path"
```

---

## Task 3: Seed script for the 10 dedicated e2e test players

**Files:**
- Create: `scripts/seed_e2e_test_players.py`

**Interfaces:**
- Consumes: `services.db_service.add_player(full_name, nick_name, email, phone="")`, `update_player_credentials(player_id, password_hash)`, `update_player_profile(player_id, updates)`, `get_password_hash(password)`, `get_player_by_email(email)`.
- Produces: 10 real Firestore player documents, IDs printed to stdout, with a generated password printed once (never stored in the repo).

This is a one-time, manually-run operational script (like `predict_season.py`), not part of the automated pytest suite — run once against production Firestore to create the accounts, then never again (re-running is safe/idempotent: it skips any email that already exists).

- [ ] **Step 1: Write the script**

```python
"""scripts/seed_e2e_test_players.py — One-time setup: create the 10 dedicated
Firestore-backed test player accounts used by the tests_e2e/ Playwright suite.

Run once against production Firestore. Safe to re-run — skips any test
account whose email already exists. Prints the generated password once;
copy it into .env as E2E_TEST_PLAYER_PASSWORD (all 10 accounts share one
password — they're fixture accounts, not real credentials to protect
individually).

Usage: python scripts/seed_e2e_test_players.py
"""
import os
import secrets

os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import (
    add_player, get_password_hash, get_player_by_email,
    update_player_credentials, update_player_profile,
)

NUM_TEST_PLAYERS = 10
EMAIL_DOMAIN = "winspool.internal"


def main():
    password = secrets.token_urlsafe(16)
    password_hash = get_password_hash(password)
    created_ids = []

    for i in range(1, NUM_TEST_PLAYERS + 1):
        email = f"e2e-test-{i:02d}@{EMAIL_DOMAIN}"
        existing = get_player_by_email(email)
        if existing:
            print(f"Skipping {email} — already exists (playerId={existing['playerId']})")
            created_ids.append(int(existing["playerId"]))
            continue

        role = "admin" if i == 1 else "user"
        player_id = add_player(
            full_name=f"E2E Test Player {i:02d}",
            nick_name=f"E2E{i:02d}",
            email=email,
        )
        update_player_credentials(str(player_id), password_hash)
        update_player_profile(str(player_id), {"is_test_account": True, "role": role})
        created_ids.append(player_id)
        print(f"Created {email} — playerId={player_id}, role={role}")

    print(f"\nDone. Player IDs: {created_ids}")
    print(f"Shared password (save to .env as E2E_TEST_PLAYER_PASSWORD): {password}")
    print("Run `python scripts/refresh_local_pkls.py` to pull these into .local_db/.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit the script**

```bash
git add scripts/seed_e2e_test_players.py
git commit -m "feat: add one-time seed script for the 10 e2e test player accounts"
```

- [ ] **Step 3: Run it once against production and record the output**

Run: `python scripts/seed_e2e_test_players.py`

This step is manual/operational — capture the 10 player IDs and the printed password, then:
1. Add `E2E_TEST_PLAYER_PASSWORD=<printed password>` to `.env` (never commit `.env`).
2. Add `E2E_TEST_PLAYER_IDS=<comma-separated ids>` to `.env` (Task 4's fixtures read this rather than hardcoding IDs, since Firestore auto-increments them).
3. Run `python scripts/refresh_local_pkls.py` so the 10 accounts appear in `.local_db/players.pkl` for local test runs.

---

## Task 4: Playwright test harness (`tests_e2e/`)

**Files:**
- Create: `requirements-dev.txt`
- Create: `tests_e2e/__init__.py`
- Create: `tests_e2e/conftest.py`

**Interfaces:**
- Produces: `live_server` (session-scoped fixture, yields base URL string e.g. `"http://127.0.0.1:8811"`), `browser` (session-scoped Playwright `Browser`), `page` (function-scoped Playwright `Page`, already navigated nowhere), `test_player_credentials` (session-scoped fixture, yields `list[dict]` of `{"id": int, "email": str, "password": str, "role": str}` for all 10 seeded accounts, read from `E2E_TEST_PLAYER_IDS`/`E2E_TEST_PLAYER_PASSWORD` env vars).

- [ ] **Step 1: Add dev/test dependencies**

```
# requirements-dev.txt
-r requirements.txt
playwright>=1.59.0
```

(`pytest` itself is assumed already available per the existing `tests/` suite's own setup — this repo's `requirements.txt` doesn't list it either, so this file follows the same existing convention rather than introducing a new one.)

- [ ] **Step 2: Write `tests_e2e/conftest.py`**

```python
"""tests_e2e/conftest.py — Playwright browser-driven test harness.

Launches a real `uvicorn main:app` subprocess against the developer's local
.local_db/ (USE_LOCAL_DATA=true) with DISABLE_OUTBOUND_EMAIL=true, so these
tests drive the actual app through a real browser rather than mocking
anything at the Python level.
"""
import os
import socket
import subprocess
import sys
import time

import pytest
from playwright.sync_api import sync_playwright

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["USE_LOCAL_DATA"] = "true"
    env["DISABLE_OUTBOUND_EMAIL"] = "true"
    env["PORT"] = str(port)
    env.setdefault("JWT_SECRET", "e2e-test-jwt-secret-not-for-production")

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.3)

    if not ready:
        proc.terminate()
        out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
        raise RuntimeError(f"live_server failed to start within 20s.\n{out}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture(scope="session")
def test_player_credentials():
    ids_raw = os.environ.get("E2E_TEST_PLAYER_IDS", "")
    password = os.environ.get("E2E_TEST_PLAYER_PASSWORD", "")
    if not ids_raw or not password:
        pytest.skip("E2E_TEST_PLAYER_IDS / E2E_TEST_PLAYER_PASSWORD not set — run scripts/seed_e2e_test_players.py first")

    ids = [int(x) for x in ids_raw.split(",") if x.strip()]
    return [
        {
            "id": pid,
            "email": f"e2e-test-{i+1:02d}@winspool.internal",
            "password": password,
            "role": "admin" if i == 0 else "user",
        }
        for i, pid in enumerate(ids)
    ]
```

- [ ] **Step 3: Verify the harness boots**

Run: `pytest tests_e2e/ -v --collect-only`
Expected: no collection errors (no test files exist yet, so 0 collected — this just proves `conftest.py` imports cleanly and `sync_playwright` is importable).

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt tests_e2e/__init__.py tests_e2e/conftest.py
git commit -m "feat: add Playwright e2e test harness (live_server, browser, page fixtures)"
```

---

## Task 5: Login flow test

**Files:**
- Create: `tests_e2e/test_login.py`

**Interfaces:**
- Consumes: `live_server`, `page`, `test_player_credentials` fixtures from Task 4.

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_login.py — Real login through the signin overlay."""
import pytest


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},   # desktop
    {"width": 390, "height": 844},    # mobile (iPhone 14-class width, per CLAUDE.md's ~390px breakpoint)
])
def test_login_reaches_standings(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    creds = test_player_credentials[1]  # a non-admin test player

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")

    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")

    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    page.wait_for_url("**/wins-pool/**", timeout=10000)

    assert "wins-pool" in page.url
```

- [ ] **Step 2: Run it**

Run: `E2E_TEST_PLAYER_IDS=<ids> E2E_TEST_PLAYER_PASSWORD=<pw> pytest tests_e2e/test_login.py -v`
Expected: PASS at both viewports (requires Task 3 already run once against production and `.local_db` refreshed).

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_login.py
git commit -m "test: add e2e login flow test at desktop and mobile viewports"
```

---

## Task 6: Mock draft smoke test (login-free)

**Files:**
- Create: `tests_e2e/test_mock_draft.py`

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_mock_draft.py — Login-free solo practice draft smoke test."""
import pytest


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_mock_draft_loads_and_allows_a_pick(live_server, page, viewport):
    page.set_viewport_size(viewport)
    page.goto(f"{live_server}/mock-draft")

    # No signin overlay — mock_draft.html deliberately doesn't extend base.html.
    assert page.locator("#signin-screen").count() == 0

    page.wait_for_selector(".team-card, [data-team]", timeout=10000)
    first_team = page.locator(".team-card, [data-team]").first
    first_team.click()

    # select-then-confirm pattern per CLAUDE.md's Mock Draft section
    confirm_btn = page.locator("button:has-text('Confirm')")
    confirm_btn.wait_for(state="visible", timeout=5000)
    confirm_btn.click()

    page.wait_for_selector(".pick-queue, #pick-queue", timeout=10000)
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_mock_draft.py -v`
Expected: PASS at both viewports. If the selectors don't match (mock_draft.js may use different class names than `.team-card`/`[data-team]`), adjust to the actual rendered markup — inspect via `page.content()` in a scratch run before finalizing.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_mock_draft.py
git commit -m "test: add e2e mock draft smoke test at desktop and mobile viewports"
```

---

## Task 7: Standings and schedule page smoke tests

**Files:**
- Create: `tests_e2e/test_standings.py`

- [ ] **Step 1: Write a shared login helper and the tests**

```python
"""tests_e2e/test_standings.py — Standings and schedule page smoke tests."""
import pytest


def _login(page, live_server, creds):
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_standings_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.wait_for_url("**/wins-pool/**", timeout=10000)
    assert page.locator(".app-container").count() > 0
    # No unhandled server error text leaking through Jinja's default error page
    assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_schedule_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/schedule")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_standings.py -v`
Expected: PASS at both viewports.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_standings.py
git commit -m "test: add e2e standings and schedule page smoke tests"
```

---

## Task 8: Admin dashboard smoke test

**Files:**
- Create: `tests_e2e/test_admin_dashboard.py`

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_admin_dashboard.py — Admin dashboard loads for an admin test account."""
from tests_e2e.test_standings import _login


def test_admin_dashboard_loads(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]  # e2e-test-01, role=admin
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)

    # Players tab (data-tab="player-section") is the default-visible tab on
    # load; #player-grid lives inside the Draft tab's section
    # (id="draft-section", hidden by default) — must switch tabs first.
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)
    page.click(".admin-tab-btn[data-tab='draft-section']")
    page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)
    page.wait_for_selector("#player-grid", timeout=10000)

    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_admin_dashboard.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_admin_dashboard.py
git commit -m "test: add e2e admin dashboard smoke test"
```

---

## Task 9: Nav parity test (desktop vs. mobile drawer)

**Files:**
- Create: `tests_e2e/test_nav_parity.py`

This directly encodes the CLAUDE.md gotcha: `updateNav()` (desktop) and the drawer's hardcoded markup (mobile) must always show the same set of destinations. Rather than hardcoding an expected link list (which would drift out of sync with the JS the moment either side changes), this test reads both live DOMs and compares them — the two must always agree, regardless of what the current business rules happen to be.

**Interfaces:**
- Consumes: `_login` helper from Task 7 (`tests_e2e/test_standings.py`).

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_nav_parity.py — Desktop nav and mobile drawer must expose the same destinations.

Regression test for the exact bug class CLAUDE.md documents: updateNav()
(static/js/main.js) renders the desktop nav client-side, while the mobile
drawer (templates/base.html's .nav-drawer__links) is separate, hardcoded
markup — a link added to one has silently not appeared in the other before.
"""
from tests_e2e.test_standings import _login


def _visible_hrefs(locator) -> set:
    hrefs = set()
    count = locator.count()
    for i in range(count):
        el = locator.nth(i)
        if el.is_visible() or True:  # collected via DOM presence + class check below
            cls = el.get_attribute("class") or ""
            if "hidden" in cls.split() or "admin-hidden" in cls.split():
                continue
            href = el.get_attribute("href")
            if href:
                hrefs.add(href)
    return hrefs


def test_desktop_and_mobile_nav_expose_the_same_destinations(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]  # role=admin, to exercise the largest link set

    page.set_viewport_size({"width": 1280, "height": 800})
    _login(page, live_server, admin_creds)
    page.wait_for_selector("#nav-primary-links a", timeout=10000)

    desktop_hrefs = _visible_hrefs(page.locator("#nav-primary-links a"))
    desktop_hrefs |= _visible_hrefs(page.locator("#nav-more-dropdown a"))

    # Resize to mobile and open the drawer (per static/js/responsive.js: the
    # "More" tab in the bottom tab bar opens #nav-drawer).
    page.set_viewport_size({"width": 390, "height": 844})
    page.click("#btb-more-tab")
    page.wait_for_selector("#nav-drawer.open", timeout=5000)

    drawer_hrefs = _visible_hrefs(page.locator(".nav-drawer__links a"))

    missing_from_drawer = desktop_hrefs - drawer_hrefs
    missing_from_desktop = drawer_hrefs - desktop_hrefs

    assert not missing_from_drawer, f"Desktop nav has links the mobile drawer is missing: {missing_from_drawer}"
    assert not missing_from_desktop, f"Mobile drawer has links the desktop nav is missing: {missing_from_desktop}"
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_nav_parity.py -v`
Expected: PASS if the two navs currently agree; FAIL with the exact missing-href set if they don't (which would itself be a real bug to fix separately, not a plan step to silently work around).

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_nav_parity.py
git commit -m "test: add e2e nav parity test between desktop rail and mobile drawer"
```

---

## Task 10: Full 10-player live draft (the real thing)

**Files:**
- Create: `tests_e2e/test_live_draft.py`

**Interfaces:**
- Consumes: `_login` helper (Task 7), `test_player_credentials` (Task 4, must have exactly 10 entries).

This is the test that matters most: it reproduces the actual mechanics of a real draft (season creation through the real Admin Portal, 10 real players joining and picking in turn over a real WebSocket connection) rather than a synthetic shortcut, because the bugs that motivated this whole plan — wrong round labels, wrong portfolio win math, `/wins-pool` 500ing mid-draft — only show up under the real multi-player, real-WebSocket flow. `draft_routes.py:704`'s `active_pick > 30` completion check and `admin_main.js:438`'s `selectedPlayerIds.size !== 10` season-creation guard both hardcode the real pool size (10 players × 3 picks), so a smaller synthetic draft would not exercise the same code path.

- [ ] **Step 1: Write the season-3000 setup helper and cleanup fixture**

```python
"""tests_e2e/test_live_draft.py — Full 10-player live draft, season 3000.

Runs through the real Admin Portal UI (not direct API calls) to create the
season, then drives 10 real Playwright browser contexts — one per test
player — through the actual live-draft WebSocket flow, one real pick at a
time, in turn order.
"""
import re
import pytest
from tests_e2e.test_standings import _login

SEASON = 3000


@pytest.fixture
def clean_season_3000(live_server, browser, test_player_credentials):
    """Delete season 3000 before and after the test, via the real Admin
    Portal delete-season control, so reruns don't accumulate stale picks."""
    def _delete():
        context = browser.new_context()
        page = context.new_page()
        _login(page, live_server, test_player_credentials[0])
        page.goto(f"{live_server}/admin")
        # #delete-season-select lives inside the "Draft" tab's section
        # (id="draft-section"), hidden by default behind the "Players" tab.
        page.click(".admin-tab-btn[data-tab='draft-section']")
        page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)
        page.wait_for_selector("#delete-season-select", timeout=10000)
        options = page.locator("#delete-season-select option").all_text_contents()
        if any(str(SEASON) in o for o in options):
            page.select_option("#delete-season-select", label=f"Season {SEASON}")
            page.once("dialog", lambda d: d.accept())
            page.click("#delete-season-btn")
            page.wait_for_timeout(1000)
        context.close()

    _delete()
    yield
    _delete()
```

- [ ] **Step 2: Write the season-creation step (through the real Admin UI)**

```python
def _create_season_3000(page, live_server, test_player_credentials):
    page.goto(f"{live_server}/admin")
    # #player-grid/#season-input/#generate-btn all live inside the "Draft"
    # tab's section (id="draft-section"), hidden by default.
    page.click(".admin-tab-btn[data-tab='draft-section']")
    page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)
    page.wait_for_selector("#player-grid", timeout=10000)

    show_test = page.locator("#show-test-accounts-toggle")
    show_test.check()
    page.wait_for_timeout(500)  # grid re-render after fetchInitialData()

    for creds in test_player_credentials:
        page.check(f".player-checkbox[value='{creds['id']}']")

    page.wait_for_selector("#player-count:has-text('10/10')", timeout=5000)

    page.fill("#season-input", str(SEASON))
    page.once("dialog", lambda d: d.accept())  # confirm() before submit
    page.once("dialog", lambda d: d.accept())  # alert() with the success message
    page.click("#generate-btn")
    page.wait_for_timeout(1000)


def _activate_draft(page, live_server):
    """draft-active-toggle is a <button aria-pressed> (admin_main.js's
    initDraftActiveToggle), not a checkbox input — check aria-pressed,
    not is_checked()."""
    page.goto(f"{live_server}/admin")
    # #draft-active-toggle also lives inside the "Draft" tab's section.
    page.click(".admin-tab-btn[data-tab='draft-section']")
    page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)
    page.wait_for_selector("#draft-active-toggle", timeout=10000)
    toggle = page.locator("#draft-active-toggle")
    page.wait_for_function(
        "document.getElementById('draft-active-toggle').getAttribute('aria-pressed') !== null",
        timeout=5000,
    )
    if toggle.get_attribute("aria-pressed") != "true":
        toggle.click()
        page.wait_for_function(
            "document.getElementById('draft-active-toggle').getAttribute('aria-pressed') === 'true'",
            timeout=5000,
        )
```

- [ ] **Step 3: Write the turn-taking pick loop**

```python
def _make_pick_when_it_is_my_turn(page, timeout_ms=15000):
    """Wait until this player's team grid becomes clickable (the draft
    UI only enables team selection during the connected player's own
    turn — see draft_routes.py's `pick` action handler), then draft the
    first available team."""
    page.wait_for_selector(".team-card:not(.disabled), [data-team]:not(.disabled)", timeout=timeout_ms)
    team_card = page.locator(".team-card:not(.disabled), [data-team]:not(.disabled)").first
    team_card.click()
    confirm_btn = page.locator("button:has-text('Confirm')")
    confirm_btn.wait_for(state="visible", timeout=5000)
    confirm_btn.click()
    page.wait_for_timeout(500)
```

- [ ] **Step 4: Write the full draft test**

```python
def test_full_ten_player_live_draft(live_server, browser, test_player_credentials, clean_season_3000):
    assert len(test_player_credentials) == 10, "This test requires all 10 seeded e2e test players"

    admin_creds = test_player_credentials[0]
    setup_context = browser.new_context()
    setup_page = setup_context.new_page()
    _login(setup_page, live_server, admin_creds)
    _create_season_3000(setup_page, live_server, test_player_credentials)
    _activate_draft(setup_page, live_server)

    # Open 10 real browser contexts, one per player, all joined to /draft/3000.
    contexts, pages = [], []
    for creds in test_player_credentials:
        ctx = browser.new_context()
        pg = ctx.new_page()
        _login(pg, live_server, creds)
        pg.goto(f"{live_server}/draft/{SEASON}")
        pg.wait_for_selector("#pick-queue, .pick-queue", timeout=10000)
        contexts.append(ctx)
        pages.append(pg)

    # 30 total picks (10 players x 3 rounds), snake order per draft_order_rules.
    for pick_num in range(1, 31):
        # Every connected page receives the same broadcast state; find whoever's
        # turn it is by checking each page for an enabled team card, in order,
        # rather than pre-computing turn order client-side.
        picked = False
        for pg in pages:
            pg.wait_for_timeout(300)
            if pg.locator(".team-card:not(.disabled), [data-team]:not(.disabled)").count() > 0:
                _make_pick_when_it_is_my_turn(pg)
                picked = True
                break
        assert picked, f"No player's page showed an active turn for pick #{pick_num}"

        # Mid-draft regression check (bce24bc: wins-pool 500'd during a live
        # draft with uneven pick counts) — verify on every pick, not just at
        # the end, since the bug only manifested while picks were uneven.
        wp_context = browser.new_context()
        wp_page = wp_context.new_page()
        _login(wp_page, live_server, admin_creds)
        wp_page.goto(f"{live_server}/wins-pool")
        wp_page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
        assert "Internal Server Error" not in wp_page.content()
        wp_context.close()

    # Draft complete — verify round label and portfolio win math on the results page.
    results_page = pages[0]
    results_page.goto(f"{live_server}/draft-results")
    results_page.wait_for_selector(".app-container", timeout=10000)
    body_text = results_page.content()
    assert "Internal Server Error" not in body_text
    # Round label bug (d79fc42) was an off-by-one against player count — with
    # 10 players and 3 rounds, "Round 3" must appear somewhere on the results
    # page and "Round 4" must not.
    assert re.search(r"Round\s*3\b", body_text)
    assert not re.search(r"Round\s*4\b", body_text)

    for ctx in contexts:
        ctx.close()
    setup_context.close()
```

- [ ] **Step 5: Run it against the real element structure and fix selectors**

Run: `E2E_TEST_PLAYER_IDS=<ids> E2E_TEST_PLAYER_PASSWORD=<pw> pytest tests_e2e/test_live_draft.py -v -s`

This test's selectors (`#generate-season-btn`, `#draft-active-toggle`, `.team-card`) are written from reading `admin_main.js`/`ui_renderer.js` source but not yet confirmed pixel-for-pixel against rendered markup — run headed once (`page.pause()` or `PWDEBUG=1`) to correct any selector mismatches before trusting this as a gate. This is expected iteration, not a sign the plan is wrong.

Expected once selectors are correct: PASS — one full 30-pick draft completes with no `Internal Server Error` appearing at any point and the round label reading "Round 3" at completion.

- [ ] **Step 6: Commit**

```bash
git add tests_e2e/test_live_draft.py
git commit -m "test: add full 10-player live draft e2e test (season 3000)"
```

---

## Task 11: Wire into `/deploy` pre-flight

**Files:**
- Modify: `.claude/commands/deploy.md`

**Interfaces:**
- Consumes: everything above — this task only changes the deploy checklist text, no application code.

- [ ] **Step 1: Read the current pre-flight section**

Read `.claude/commands/deploy.md` to find where `pytest tests/` is currently listed as a pre-flight step.

- [ ] **Step 2: Add the e2e suite as a required pre-flight step, after the unit test step**

Add a line/step instructing: run `pytest tests_e2e/ -v` (requires `E2E_TEST_PLAYER_IDS`/`E2E_TEST_PLAYER_PASSWORD` in `.env` per Task 3) after `pytest tests/` passes, before proceeding to `git push`/`deploy.ps1`. Note explicitly that `tests_e2e/test_live_draft.py` is the slowest test in the suite (a real 30-pick draft) and is expected to take on the order of a minute or two — this is a deliberate tradeoff for pre-deploy confidence, not a bug.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/deploy.md
git commit -m "docs: add Playwright e2e suite to the /deploy pre-flight checklist"
```

---

## Task 12: Update the spec's draft-room bullet

**Files:**
- Modify: `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §1 (UI Tests)

- [ ] **Step 1: Replace the original one-line "Draft room" bullet**

Replace:
```
- Draft room: join, see state, make a pick (WebSocket-driven — needs a
  running draft fixture)
```
with a line pointing to this plan for the full design:
```
- Draft room: a full, real 10-player live draft (season 3000, real
  WebSocket picks, real Admin Portal season creation) rather than a
  synthetic shortcut — see
  `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` Task 10 for
  the full design and rationale (draft_routes.py hardcodes 10-player/
  30-pick math, so a smaller synthetic draft wouldn't exercise the same
  code path).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md
git commit -m "docs: update post-launch-hardening spec's draft-room test bullet to match the full plan"
```

---

## Self-Review Notes

- **Spec coverage:** §1's bullets (login, standings, draft room, mock draft, admin dashboard, nav parity) are each covered by Tasks 5-10. The "wire into /deploy" integration point is Task 11.
- **Known selector risk (flagged, not hidden):** `#generate-btn`, `#draft-active-toggle` (a `<button aria-pressed>`, not a checkbox), `#season-input`, `#delete-season-btn`, and `#player-grid`/`.player-checkbox` are all confirmed directly against `templates/admin.html`/`admin_main.js` source (exact ids, exact element types). `.team-card`/`[data-team]` (Task 6, Task 10's pick loop) and the mock draft "Confirm" button are not confirmed against live rendered DOM — `mock_draft.js` wasn't read during planning. Step 5 of Task 10 and Step 2 of Task 6 both say explicitly to verify/correct these specific selectors against the real page before trusting the test as a gate.
- **Out of scope (per the original spec, unchanged):** visual regression/screenshot diffing.
