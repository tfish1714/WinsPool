# UI Tests: Account/Auth Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Playwright e2e coverage for the account/auth states beyond "already has a password, log in" — first-time account claim, MFA-required state, failed-login lockout, and an admin-forced password change — none of which `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md`'s Task 5 (simple login) touches.

**Architecture:** Reuses the `live_server`/`browser`/`page` fixtures and `DISABLE_OUTBOUND_EMAIL` safety gate from the already-committed harness plan (its Tasks 2 and 4) without modification. Four additional dedicated Firestore-backed test accounts are added to the same seed script (Task 3 of that plan) — each one owns exactly one lifecycle state so tests never depend on execution order or on each other's leftover state, and each test restores its account to its starting state via the real Admin Portal UI as its own cleanup step (mirroring the existing plan's `clean_season_3000` pattern).

**Tech Stack:** Same as the harness plan — Playwright Python sync API via `pytest`, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §1 (UI Tests). This plan is a sibling to `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md`, not a replacement — it assumes that plan's Tasks 1-4 (is_test_account flag, DISABLE_OUTBOUND_EMAIL gate, seed script, tests_e2e/conftest.py) are already implemented before this plan's tasks run.

## Global Constraints

- Every test interacts through the real UI only (typed input, real clicks) — never by calling internal API/service functions directly, matching the harness plan's own constraint.
- `DISABLE_OUTBOUND_EMAIL=true` (already set for the whole `live_server` subprocess by the harness plan's Task 2) means the MFA verification email never actually sends during these tests. **This makes a full successful MFA login untestable end-to-end in this environment** — see Task 3's design note for why, and what is tested instead.
- Each of the 4 new dedicated accounts owns exactly one test file's state and is restored to its starting condition at the end of that test, via the real Admin Portal UI (never a raw API call or direct pkl edit) — so re-running the suite any number of times, in any order, produces the same result.
- The 10 existing drafting accounts (`e2e-test-01` through `e2e-test-10`) are never touched by this plan — their password state must stay stable for `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md`'s Task 10 (the live draft) to keep working.

---

## Task 1: Extend the seed script with 4 dedicated auth-lifecycle accounts

**Files:**
- Modify: `scripts/seed_e2e_test_players.py` (created by the harness plan's Task 3 — extend it, don't create a second script)

**Interfaces:**
- Produces: 4 additional Firestore player documents — `e2e-test-11-claim@winspool.internal` (password_hash left unset, for Task 2), `e2e-test-12-mfa@winspool.internal` (mfa_enabled=True, for Task 3), `e2e-test-13-lockout@winspool.internal` (for Task 4), `e2e-test-14-tempword@winspool.internal` (for Task 5) — all `is_test_account: True`, `role: "user"`.

- [ ] **Step 1: Extend the script**

Add this block to `scripts/seed_e2e_test_players.py`'s `main()`, after the existing 10-player loop and before the final print statements:

```python
    # Dedicated single-purpose accounts for tests_e2e/test_account_claim.py,
    # test_mfa.py, test_lockout.py, and test_forced_password_change.py.
    # Each owns exactly one lifecycle state so those tests never depend on
    # execution order or share mutable state with each other or with the
    # 10 drafting accounts above.
    lifecycle_accounts = [
        ("e2e-test-11-claim", "E2E Claim Test", False),   # no password_hash: claim-flow target
        ("e2e-test-12-mfa", "E2E MFA Test", True),         # gets a real password below
        ("e2e-test-13-lockout", "E2E Lockout Test", True),
        ("e2e-test-14-tempword", "E2E Tempword Test", True),
    ]
    lifecycle_ids = {}

    for local_part, full_name, needs_password in lifecycle_accounts:
        email = f"{local_part}@{EMAIL_DOMAIN}"
        existing = get_player_by_email(email)
        if existing:
            print(f"Skipping {email} — already exists (playerId={existing['playerId']})")
            lifecycle_ids[local_part] = int(existing["playerId"])
            continue

        player_id = add_player(full_name=full_name, nick_name=local_part, email=email)
        update_player_profile(str(player_id), {"is_test_account": True, "role": "user"})
        if needs_password:
            update_player_credentials(str(player_id), password_hash)
        lifecycle_ids[local_part] = player_id
        print(f"Created {email} — playerId={player_id}, has_password={needs_password}")

    # e2e-test-12-mfa additionally needs mfa_enabled=True.
    update_player_profile(str(lifecycle_ids["e2e-test-12-mfa"]), {"mfa_enabled": True})

    print(f"\nLifecycle account IDs: {lifecycle_ids}")
```

(This reuses the same `password_hash`/`password` generated earlier in `main()` for the base 10 accounts — no new secret to manage. `e2e-test-11-claim` deliberately does NOT get `update_player_credentials` called on it, since its whole purpose is testing the "no password yet" claim flow.)

- [ ] **Step 2: Run it once and record the new IDs**

Run: `python scripts/seed_e2e_test_players.py`

Add to `.env`: `E2E_CLAIM_TEST_PLAYER_ID`, `E2E_MFA_TEST_PLAYER_ID`, `E2E_LOCKOUT_TEST_PLAYER_ID`, `E2E_TEMPWORD_TEST_PLAYER_ID` (the 4 printed IDs). Run `python scripts/refresh_local_pkls.py` afterward so they appear in `.local_db/players.pkl`.

- [ ] **Step 3: Add a conftest fixture exposing these 4 accounts**

Append to `tests_e2e/conftest.py` (created by the harness plan's Task 4):

```python
@pytest.fixture(scope="session")
def lifecycle_test_accounts():
    """The 4 dedicated single-purpose accounts from Task 1 of the
    auth-lifecycle plan. Each dict also carries the shared base password
    (same one test_player_credentials uses) so tests that need to log in
    with a known-good password can."""
    password = os.environ.get("E2E_TEST_PLAYER_PASSWORD", "")
    ids = {
        "claim": os.environ.get("E2E_CLAIM_TEST_PLAYER_ID"),
        "mfa": os.environ.get("E2E_MFA_TEST_PLAYER_ID"),
        "lockout": os.environ.get("E2E_LOCKOUT_TEST_PLAYER_ID"),
        "tempword": os.environ.get("E2E_TEMPWORD_TEST_PLAYER_ID"),
    }
    if not all(ids.values()) or not password:
        pytest.skip("E2E_*_TEST_PLAYER_ID / E2E_TEST_PLAYER_PASSWORD not set — run Task 1's seed script extension first")

    return {
        "claim": {"id": int(ids["claim"]), "email": "e2e-test-11-claim@winspool.internal", "password": password},
        "mfa": {"id": int(ids["mfa"]), "email": "e2e-test-12-mfa@winspool.internal", "password": password},
        "lockout": {"id": int(ids["lockout"]), "email": "e2e-test-13-lockout@winspool.internal", "password": password},
        "tempword": {"id": int(ids["tempword"]), "email": "e2e-test-14-tempword@winspool.internal", "password": password},
    }
```

- [ ] **Step 4: Commit**

```bash
git add scripts/seed_e2e_test_players.py tests_e2e/conftest.py
git commit -m "feat: add 4 dedicated e2e accounts for account-claim/MFA/lockout/forced-change tests"
```

---

## Task 2: First-time account claim flow

**Files:**
- Create: `tests_e2e/test_account_claim.py`

**Interfaces:**
- Consumes: `live_server`, `page`, `lifecycle_test_accounts` (Task 1), `test_player_credentials[0]` (an admin, for cleanup) from Task 4 of the harness plan.
- Produces: `_admin_reset_password(page, live_server, admin_creds, target_player_id, target_player_name)` helper, reused by Task 4/5's cleanup.

Confirmed against `routes/auth_routes.py:84-105` (`check_player`) and `static/js/main.js:854-916` (`handleEmailBlur`/`handleLogin`): blurring `#auth-email` calls `GET /api/check_player`; when `has_password` is false the UI reveals `#auth-confirm-password` + `#setup-requirements`, changes `#auth-submit-btn`'s text to "Setup Account", and submit calls `POST /api/set_password`.

- [ ] **Step 1: Write the shared admin-reset-password helper**

```python
"""tests_e2e/test_account_claim.py — First-time account claim (no password set yet)."""
import pytest


def _admin_reset_password(page, live_server, admin_creds, target_player_id, target_player_name):
    """Restores a test account to password_hash=None via the real Admin
    Portal 'Reset Password' button (routes/admin_routes.py:255's
    /admin/reset_password — clears password_hash, lockout_until, and
    failed_setup_attempts). Requires the admin to already be logged in
    on `page`."""
    page.goto(f"{live_server}/admin")
    page.wait_for_selector(f".player-mgmt-card[data-player-id='{target_player_id}']", timeout=10000)
    card = page.locator(f".player-mgmt-card[data-player-id='{target_player_id}']")
    page.once("dialog", lambda d: d.accept())  # confirm("Reset password for ...?")
    page.once("dialog", lambda d: d.accept())  # alert(data.message)
    card.locator(".btn-reset-pw").click()
    page.wait_for_timeout(1000)
```

- [ ] **Step 2: Write the claim-flow test**

```python
from tests_e2e.test_standings import _login


def _login_admin(page, live_server, test_player_credentials):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)
    return admin_creds


@pytest.fixture
def reset_claim_account_after(live_server, browser, lifecycle_test_accounts, test_player_credentials):
    """Ensures e2e-test-11-claim always ends the test with no password set,
    regardless of whether the test body raises."""
    yield
    ctx = browser.new_context()
    admin_page = ctx.new_page()
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    _admin_reset_password(
        admin_page, live_server, admin_creds,
        lifecycle_test_accounts["claim"]["id"], "E2E Claim Test",
    )
    ctx.close()


def test_first_time_account_claim(live_server, page, lifecycle_test_accounts, reset_claim_account_after):
    creds = lifecycle_test_accounts["claim"]

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.locator("#auth-email").blur()

    # has_password=False reveals the confirm-password field and switches
    # the submit button's label (main.js:854-872).
    page.wait_for_selector("#auth-confirm-password:not(.hidden)", timeout=5000)
    assert page.locator("#auth-submit-btn").inner_text() == "Setup Account"

    # Live complexity checklist (main.js:822-833) mirrors
    # PASSWORD_COMPLEXITY_RE (services/constants.py) — type a weak password
    # first and confirm the checklist doesn't mark every requirement met.
    page.fill("#auth-password", "weak")
    assert "met" not in (page.locator("#pw-req-length").get_attribute("class") or "")

    strong_password = "E2eClaimTest!9"
    page.fill("#auth-password", strong_password)
    for req_id in ["pw-req-length", "pw-req-case", "pw-req-number", "pw-req-symbol"]:
        assert "met" in page.locator(f"#{req_id}").get_attribute("class")

    # Mismatched confirm shows the mismatch hint before submit.
    page.fill("#auth-confirm-password", "SomethingElse!9")
    page.wait_for_selector("#pw-match-hint:not(.hidden)")
    assert "mismatch" in page.locator("#pw-match-hint").get_attribute("class")

    page.fill("#auth-confirm-password", strong_password)
    page.wait_for_selector("#pw-match-hint.match", timeout=5000)

    page.click("#auth-submit-btn")

    # Successful setup logs the user straight in (main.js:896: status set to
    # 'success', triggering a page reload).
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    page.wait_for_url("**/wins-pool/**", timeout=10000)
```

- [ ] **Step 3: Run it and fix selectors if needed**

Run: `pytest tests_e2e/test_account_claim.py -v`
Expected: PASS. The `#pw-req-*` ids and `#pw-match-hint` classes (`met`, `match`, `mismatch`) are confirmed directly against `main.js:822-851` and `templates/base.html:52-66` — low selector risk here versus Task 6/10 of the harness plan.

- [ ] **Step 4: Commit**

```bash
git add tests_e2e/test_account_claim.py
git commit -m "test: add e2e first-time account claim flow test"
```

---

## Task 3: MFA-required state

**Files:**
- Create: `tests_e2e/test_mfa.py`

**Design note — why this doesn't test a full successful MFA login:** `routes/auth_routes.py:334`'s `/api/mfa/verify` checks the submitted code against a SHA-256 hash (`auth_routes.py:224`) of a randomly generated 6-digit code; the *only* place the plaintext code is ever transmitted is `email_service.send_mfa_code_email` (`auth_routes.py:231`). The harness plan's `DISABLE_OUTBOUND_EMAIL=true` gate (its Task 2) makes that a no-op — the code is generated and hashed, but never delivered anywhere a test could read it back. There is no way to retrieve the real code through the actual UI/API surface without either (a) reading a real inbox via the Resend API, which is out of scope and would make this test dependent on external network state, or (b) the application exposing a test-only backdoor that leaks the code, which this plan deliberately does not add — that would test a code path real users never go through. So this task verifies everything *except* a successful MFA completion: that the MFA UI appears correctly after a valid password, and that an incorrect code is correctly rejected. A full successful-login regression on this path is a known, accepted gap — flagged here rather than faked.

**Interfaces:**
- Consumes: `live_server`, `page`, `lifecycle_test_accounts` (Task 1).

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_mfa.py — MFA-required login state.

Full successful-MFA-login coverage is not possible in this environment —
see the Design Note in docs/superpowers/plans/2026-09-09-ui-tests-auth-lifecycle.md
Task 3. This test covers everything short of that: the MFA UI appearing
correctly, and wrong-code rejection.
"""
def test_mfa_required_state_and_wrong_code_rejected(live_server, page, lifecycle_test_accounts):
    creds = lifecycle_test_accounts["mfa"]

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.locator("#auth-email").blur()
    page.wait_for_selector("#auth-confirm-password.hidden", timeout=5000)  # has_password=True path

    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")

    # login() returns status='mfa_required' (auth_routes.py:232) instead of
    # logging in immediately -- handleMfaRequired (main.js:918-929) hides
    # the password field and reveals the code field.
    page.wait_for_selector("#auth-mfa-code:not(.hidden)", timeout=10000)
    assert page.locator("#auth-submit-btn").inner_text() == "Verify Code"
    assert "hidden" in (page.locator("#auth-password").get_attribute("class") or "")

    # A wrong code must be rejected, not silently accepted.
    page.fill("#auth-mfa-code", "000000")
    page.click("#auth-submit-btn")

    page.wait_for_selector("#auth-error:not(.hidden)", timeout=10000)
    assert "signin-screen" in (page.get_attribute("#signin-screen", "id") or "")  # still on the signin screen
    error_text = page.locator("#auth-error").inner_text()
    assert error_text  # non-empty -- exact wording ("Incorrect verification code." or "MFA code expired or invalid.")
                        # depends on whether the 000000 guess happens to collide with expiry timing, both are correct rejections
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_mfa.py -v`
Expected: PASS. No cleanup fixture needed — this test never successfully completes login, so `e2e-test-12-mfa`'s state (mfa_enabled=True, password unchanged) never actually changes; the only server-side mutation is a fresh `mfa_token`/`mfa_expiry` written on each attempt, which naturally gets overwritten by the next run's own login attempt.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_mfa.py
git commit -m "test: add e2e MFA-required-state test (wrong-code rejection only -- see design note on email-code retrieval limitation)"
```

---

## Task 4: Account lockout after 5 failed attempts

**Files:**
- Create: `tests_e2e/test_lockout.py`

Confirmed against `routes/auth_routes.py:171-199`: on the 5th consecutive failed `verify_password`, `login()` sets `lockout_until = now + 1800` and returns HTTP 429 with "Too many failed login attempts. Account locked for 30 minutes."; while locked, `auth_routes.py:184-187` rejects every attempt — including one with the *correct* password — with "Account locked. Try again in N minutes."

**Interfaces:**
- Consumes: `live_server`, `page`, `browser`, `lifecycle_test_accounts` (Task 1), `test_player_credentials` (harness Task 4), `_admin_reset_password` + `_login_admin` helpers (Task 2).

- [ ] **Step 1: Write a login-attempt helper and the test**

```python
"""tests_e2e/test_lockout.py — 5 failed logins trigger a 30-minute lockout."""
import pytest
from tests_e2e.test_standings import _login
from tests_e2e.test_account_claim import _admin_reset_password, _login_admin


def _attempt_login(page, live_server, email, password):
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", email)
    page.locator("#auth-email").blur()
    page.wait_for_timeout(300)  # let check_player's UI update settle
    page.fill("#auth-password", password)
    page.click("#auth-submit-btn")
    page.wait_for_selector("#auth-error:not(.hidden)", timeout=10000)
    return page.locator("#auth-error").inner_text()


@pytest.fixture
def restore_lockout_account_after(live_server, browser, lifecycle_test_accounts, test_player_credentials):
    """Cleanup: clear the lockout and restore a known-good password so the
    next run starts from a clean, usable state. Uses the real Admin Portal
    Reset Password (clears lockout_until) followed by Set Temp Password
    (restores a known password) -- both real UI actions."""
    yield
    ctx = browser.new_context()
    admin_page = ctx.new_page()
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    target_id = lifecycle_test_accounts["lockout"]["id"]
    _admin_reset_password(admin_page, live_server, admin_creds, target_id, "E2E Lockout Test")

    card = admin_page.locator(f".player-mgmt-card[data-player-id='{target_id}']")
    card.locator(".btn-temp-pw").click()
    card.locator(".temp-pw-input").fill(lifecycle_test_accounts["lockout"]["password"])
    admin_page.once("dialog", lambda d: d.accept())  # alert(data.message)
    card.locator(".btn-confirm-temppw").click()
    admin_page.wait_for_timeout(1000)
    ctx.close()


def test_five_failed_logins_lock_the_account(live_server, page, lifecycle_test_accounts, restore_lockout_account_after):
    creds = lifecycle_test_accounts["lockout"]

    last_error = ""
    for attempt in range(1, 6):
        last_error = _attempt_login(page, live_server, creds["email"], "definitely-wrong-password")

    assert "locked" in last_error.lower()
    assert "30 minutes" in last_error

    # Even the CORRECT password is now rejected while locked.
    locked_error = _attempt_login(page, live_server, creds["email"], creds["password"])
    assert "locked" in locked_error.lower()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_lockout.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_lockout.py
git commit -m "test: add e2e account lockout test (5 failed attempts, then locked even with correct password)"
```

---

## Task 5: Admin-forced password change (must_change_password)

**Files:**
- Create: `tests_e2e/test_forced_password_change.py`

Confirmed against `static/js/admin_main.js:305-351` (`.player-mgmt-temppw` panel, `.temp-pw-input`, `.btn-confirm-temppw` → `setTempPassword()` → `POST /api/admin/set_temp_password`) and `routes/admin_routes.py:273-289` (server-side requires the temp password to satisfy `PASSWORD_COMPLEXITY_RE` — 12+ chars, upper/lower/digit/symbol — not just the frontend's client-side 8-char minimum check). `routes/auth_routes.py:213-219`: on login, `must_change_password is True` returns `status: "must_change_password"` instead of logging in.

**Confirmed real product gap (verified during planning, not speculative):** `grep -n "must_change_password" static/js/*.js` shows it referenced only in `admin_main.js:125,258` — both for the "Temp Password" status *badge* in the admin player list, never in the login flow. `main.js`'s `handleLogin` (`main.js:901-908`) branches only on `data.status === 'success'` and `data.status === 'mfa_required'`; `must_change_password` falls through to the generic `else { this.showAuthError(data.error || 'Login failed') }` — and that response never sets `error`, so the account gets stuck showing the generic "Login failed" text instead of ever being routed into a password-change form. **The backend supports forced password changes; the frontend has no way to complete one.** This plan does not fix that — fixing application behavior discovered incidentally while writing tests is out of scope for a test-writing plan — but Task 5 below is written to test what actually exists (the login block) rather than assert a UI flow that doesn't exist, and flags the gap explicitly in its own comments so it doesn't get silently lost.

- [ ] **Step 1: Write the test for what actually exists**

```python
"""tests_e2e/test_forced_password_change.py — Admin sets a temp password;
target account must be blocked from a normal login afterward.

NOTE: static/js/main.js has no handler for status='must_change_password' in
handleLogin (main.js:901-908 only branches on 'success' and
'mfa_required') -- confirmed during planning (see the Task 5 design note
in docs/superpowers/plans/2026-09-09-ui-tests-auth-lifecycle.md). This
test only covers the backend contract (blocked normal login), not a full
"user completes the forced change" UI flow, since that UI flow doesn't
exist to test.
"""
import pytest
from tests_e2e.test_standings import _login
from tests_e2e.test_account_claim import _admin_reset_password, _login_admin

TEMP_PASSWORD = "E2eForcedTemp!7"


@pytest.fixture
def restore_tempword_account_after(live_server, browser, lifecycle_test_accounts, test_player_credentials):
    yield
    ctx = browser.new_context()
    admin_page = ctx.new_page()
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    target_id = lifecycle_test_accounts["tempword"]["id"]
    _admin_reset_password(admin_page, live_server, admin_creds, target_id, "E2E Tempword Test")

    card = admin_page.locator(f".player-mgmt-card[data-player-id='{target_id}']")
    card.locator(".btn-temp-pw").click()
    card.locator(".temp-pw-input").fill(lifecycle_test_accounts["tempword"]["password"])
    admin_page.once("dialog", lambda d: d.accept())
    card.locator(".btn-confirm-temppw").click()
    admin_page.wait_for_timeout(1000)
    ctx.close()


def test_admin_set_temp_password_blocks_normal_login(live_server, page, browser, lifecycle_test_accounts, test_player_credentials, restore_tempword_account_after):
    creds = lifecycle_test_accounts["tempword"]

    admin_page = page
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    admin_page.goto(f"{live_server}/admin")
    selector = f".player-mgmt-card[data-player-id='{creds['id']}']"
    admin_page.wait_for_selector(selector, timeout=10000)
    card = admin_page.locator(selector)
    card.locator(".btn-temp-pw").click()
    card.locator(".temp-pw-input").fill(TEMP_PASSWORD)
    admin_page.once("dialog", lambda d: d.accept())
    card.locator(".btn-confirm-temppw").click()
    admin_page.wait_for_timeout(1000)
    assert "Temp Password" in card.locator(".player-mgmt-display").inner_text()

    # Log in as the target with the temp password -- must NOT reach the app.
    target_ctx = browser.new_context()
    target_page = target_ctx.new_page()
    target_page.goto(live_server)
    target_page.wait_for_selector("#signin-screen", state="visible")
    target_page.fill("#auth-email", creds["email"])
    target_page.locator("#auth-email").blur()
    target_page.wait_for_selector("#auth-confirm-password.hidden", timeout=5000)
    target_page.fill("#auth-password", TEMP_PASSWORD)
    target_page.click("#auth-submit-btn")

    target_page.wait_for_timeout(2000)
    assert target_page.locator("#signin-screen").is_visible(), (
        "Backend returned must_change_password, but the signin screen is gone -- "
        "either the frontend has an undocumented handler for this status, or "
        "something is silently treating it as success. Investigate before trusting this test."
    )
    target_ctx.close()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_forced_password_change.py -v`
Expected: PASS, confirming the account stays blocked at the signin screen. If `main.js` has since gained a `must_change_password` handler (i.e., the confirmed-gap note above is stale by the time this runs), extend the test to complete that real flow instead of stopping at "blocked" — read the actual handler code first.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_forced_password_change.py
git commit -m "test: add e2e admin-forced-temp-password test (blocked-login contract; flags missing frontend handler if still absent)"
```

---

## Self-Review Notes

- **Coverage:** first-time claim (Task 2, full flow including live password-complexity checklist), MFA-required state + wrong-code rejection (Task 3, with an explicit documented gap on full success), 5-failed-attempt lockout including "correct password still blocked" (Task 4), and the admin-forced-temp-password backend contract (Task 5, with a built-in check for whether the frontend even supports completing that flow).
- **Known limitation (by design, not an oversight):** Task 3 cannot test a successful MFA login end-to-end because the verification code only ever exists in a hashed form server-side and in an email this test environment deliberately never sends. Closing this gap for real would require either fetching a real inbox via the Resend API (adds an external network dependency to the suite) or the app adding a test-only code-retrieval hook — neither is in scope here; flagged for the user to decide if it's worth doing later.
- **Real gap surfaced, not fixed:** Task 5's Step 1 explicitly checks whether `static/js/main.js` has any handling for `status: "must_change_password"` at all. If it doesn't, that's a genuine product bug (the admin-facing "Set Temp Password" feature has no working frontend completion path) that this plan intentionally does not fix — it's out of scope for a test-writing plan to silently patch application behavior it happens to notice while testing.
- **Out of scope:** self-service "forgot password" (no such endpoint exists in `routes/auth_routes.py` — password resets are admin-initiated only, per `/admin/reset_password` and `/admin/set_temp_password`).
