# E2E Suite Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One canonical way to open an admin tab, record and assert dialogs, verify admin settings, and clean up browser contexts across the Playwright e2e suite, without changing what any test verifies.

**Architecture:** A new `tests_e2e/helpers.py` holds the shared primitives (`_open_admin_tab`, `_record_dialogs`, `_click_and_wait_for_dialogs`, `_assert_no_failure_dialogs`, `_wait_for_config`). Existing test modules import from it; names other modules already import (`_record_dialogs` from `test_live_draft`) stay importable from their old location. Each task migrates a slice of the suite and is verified by running the affected e2e files.

**Tech Stack:** Python, pytest, Playwright (sync API, Chromium).

**Specs:** `docs/superpowers/specs/2026-09-12-e2e-test-suite-hardening-followups.md` (items 5, 6), `docs/superpowers/specs/2026-09-13-admin-flows-e2e-followups.md` (items 1-6)

## Global Constraints

- No emojis in code, comments, docs, or commit messages.
- Zero-deletion policy: no test is removed or weakened; a test may only become stricter. Removing a helper is allowed only where this plan says "retire" (Task 2), and every call site must be migrated first.
- Every browser-driven step keeps going through the real UI; no API shortcuts are introduced for state changes. Reading `GET /api/config/settings` to verify persistence is allowed (the suite already does this).
- `tests_e2e/helpers.py` must not import from any `test_*.py` module (avoids import cycles). `test_standings._login` stays where it is; other files import it from there.
- `test_live_draft._record_dialogs` must remain importable from `tests_e2e.test_live_draft` (`test_admin_members_and_recap.py` and `test_admin_draft_overrides.py` import it from there today).
- Do not touch application code (`static/`, `templates/`, `routes/`, `services/`).
- Running the suite: the e2e tests need seeded accounts. In this worktree they are already seeded in the local `.local_db/` copy. Set these env vars in the shell before running any e2e command (values are local-only fixtures):

```
E2E_TEST_PLAYER_IDS=14,15,16,17,18,19,20,21,22,23
E2E_TEST_PLAYER_PASSWORD=E2eLocalOnly!2026xyz
E2E_CLAIM_TEST_PLAYER_ID=24
E2E_MFA_TEST_PLAYER_ID=25
E2E_LOCKOUT_TEST_PLAYER_ID=26
E2E_TEMPWORD_TEST_PLAYER_ID=27
```

  In PowerShell: `$env:E2E_TEST_PLAYER_IDS="14,..."` etc., then `python -m pytest tests_e2e/<file> -q -p no:cacheprovider`. Never run `scripts/seed_e2e_test_players.py` (it writes to production Firestore) and never read the repo's `.env`.
- The e2e suite writes to the worktree's local `.local_db/` (season 3000, admin settings). That is expected and gitignored.

## Findings that shape the plan

1. `tests_e2e/helpers.py` does not exist yet; every helper lives inside a `test_*.py` file.
2. The mock-draft toggle verification (`expect_response` plus a 3-attempt retry) and the live-draft one (`GET /api/config/settings` polling) are two idioms for one job; the GET-reread form is the more robust and needs no retry.
3. `test_admin_player_management.py` has one `localStorage.clear()` call today (inside `_reclaim_player_password`), not two as the spec says; the plan replaces every occurrence it finds.
4. The logout control sits inside the avatar popover: click `#nav-avatar-btn`, then `#nav-ap-logout-btn` (desktop). The mobile drawer button `#drawer-logout-btn` is only visible when the drawer is open at narrow widths; the e2e default viewport is desktop.
5. Dialog wording (from `static/js/admin_main.js`): reset password confirms with `Reset password for <name>? They will be prompted...` then alerts `data.message` on success or `Reset failed: ...`; set temp password alerts `data.message` or `Failed: ...`; player edit alerts `Player updated successfully.` or `Update failed: ...`; create player alerts `Player created!` or `Creation failed: ...`.
6. Out of scope (other items in the same specs, not requested): `_poll`'s exception swallowing, the vacuous season-3000 win-math assertion, module-scoped login in `test_admin_tabs.py`, the recap skip-on-404, the seed script's admin password, the `slow` marker question, `test_edit_player_profile`'s phone restore.

## File Structure

- Create `tests_e2e/helpers.py`: shared helpers.
- Create `tests_e2e/test_helpers.py`: e2e tests for the helpers themselves.
- Modify `tests_e2e/test_admin_tabs.py`, `test_admin_dashboard.py`, `test_admin_members_and_recap.py`, `test_admin_player_management.py`, `test_admin_draft_overrides.py`, `test_live_draft.py`, `test_mock_draft.py`.

---

### Task 1: `tests_e2e/helpers.py` with `_open_admin_tab`, migrate all tab clicks

**Files:**
- Create: `tests_e2e/helpers.py`, `tests_e2e/test_helpers.py`
- Modify: `tests_e2e/test_admin_tabs.py`, `tests_e2e/test_admin_members_and_recap.py`, `tests_e2e/test_admin_player_management.py`, `tests_e2e/test_live_draft.py`, `tests_e2e/test_admin_dashboard.py`

**Interfaces:**
- Produces: `_open_admin_tab(page, tab_id: str) -> None` in `tests_e2e/helpers.py`. Precondition: `page` is on `/admin` and signed in. Postcondition: the tab's section (`#<tab_id>`) is visible.

- [ ] **Step 1: Write the failing test** (`tests_e2e/test_helpers.py`)

```python
"""tests_e2e/test_helpers.py -- the shared e2e helpers, exercised against the real app."""
import pytest

from tests_e2e.helpers import _open_admin_tab
from tests_e2e.test_standings import _login


def _admin_page(live_server, page, test_player_credentials):
    _login(page, live_server, test_player_credentials[0])
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)


@pytest.mark.parametrize("tab_id", ["draft-section", "members-section", "player-section"])
def test_open_admin_tab_reveals_the_section(live_server, page, test_player_credentials, tab_id):
    _admin_page(live_server, page, test_player_credentials)

    _open_admin_tab(page, tab_id)

    assert page.locator(f"#{tab_id}").is_visible()


def test_open_admin_tab_switches_between_tabs(live_server, page, test_player_credentials):
    _admin_page(live_server, page, test_player_credentials)

    _open_admin_tab(page, "members-section")
    _open_admin_tab(page, "draft-section")

    assert page.locator("#draft-section").is_visible()
    assert not page.locator("#members-section").is_visible()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests_e2e/test_helpers.py -q -p no:cacheprovider`
Expected: FAIL (`ModuleNotFoundError: tests_e2e.helpers`).

- [ ] **Step 3: Create `tests_e2e/helpers.py`**

```python
"""tests_e2e/helpers.py -- shared Playwright helpers for the e2e suite.

Rule: this module never imports from a test_*.py file (import cycles).
"""


def _open_admin_tab(page, tab_id):
    """Click the /admin dashboard tab whose `data-tab` is `tab_id` (for example
    "draft-section" or "members-section") and wait until its section is shown.

    Precondition: `page` is already on /admin and signed in. This is the one
    canonical tab-click for the whole suite: the tab buttons carry both the
    `tab-btn` and `admin-tab-btn` classes and `data-tab` is unique on /admin,
    so anchoring on `.admin-tabs .tab-btn[data-tab=...]` keeps every test
    coupled to a single selector instead of several drifting ones.
    """
    selector = f'.admin-tabs .tab-btn[data-tab="{tab_id}"]'
    page.wait_for_selector(selector, timeout=10000)
    page.click(selector)
    page.wait_for_selector(f"#{tab_id}:not(.hidden)", timeout=10000)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests_e2e/test_helpers.py -q -p no:cacheprovider`
Expected: 4 passed.

- [ ] **Step 5: Migrate the call sites.** Replace every direct admin-tab click with `_open_admin_tab(page, "<tab-id>")` and delete the now-redundant explicit `wait_for_selector("#<tab>:not(.hidden)")` that immediately followed it (the helper waits for it). Sites:
  - `test_admin_tabs.py::test_admin_tab_loads`: the `wait_for_selector(...tab-btn...)`, `click`, and section wait become one `_open_admin_tab(page, tab_id)` call.
  - `test_admin_dashboard.py`: `page.click(".admin-tab-btn[data-tab='draft-section']")` plus the following `wait_for_selector("#draft-section:not(.hidden)")`.
  - `test_admin_members_and_recap.py`: both tests (`members-section`, `recap-section`).
  - `test_admin_player_management.py::_refetch_players`: both clicks (`draft-section`, `player-section`) and their waits.
  - `test_live_draft.py::_open_admin_draft_tab`: keep the function (other modules import it) but its body uses `_open_admin_tab(page, "draft-section")` after the `goto` and signin wait.
  - `test_mock_draft.py`'s `ADMIN_DRAFT_TAB_SELECTOR` click is migrated in Task 5, not here.
  Import with `from tests_e2e.helpers import _open_admin_tab`.

- [ ] **Step 6: Verify nothing else uses the old idioms**

Run: `git grep -n "admin-tab-btn\[data-tab\|\[data-tab=" -- tests_e2e`
Expected: only `tests_e2e/helpers.py` and (until Task 5) `tests_e2e/test_mock_draft.py`.

- [ ] **Step 7: Run the affected files**

Run: `python -m pytest tests_e2e/test_helpers.py tests_e2e/test_admin_tabs.py tests_e2e/test_admin_dashboard.py tests_e2e/test_admin_members_and_recap.py tests_e2e/test_admin_player_management.py -q -p no:cacheprovider`
Expected: all pass. (`test_live_draft.py` is exercised at the end of Task 6.)

- [ ] **Step 8: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): canonical _open_admin_tab helper, migrate all admin tab clicks"
```

---

### Task 2: One dialog idiom (`_record_dialogs`), retire `_click_through_two_dialogs`

**Files:**
- Modify: `tests_e2e/helpers.py`, `tests_e2e/test_live_draft.py`, `tests_e2e/test_admin_player_management.py`, `tests_e2e/test_admin_tabs.py`, `tests_e2e/test_helpers.py`

**Interfaces:**
- Consumes: Task 1's `tests_e2e/helpers.py`.
- Produces in `tests_e2e/helpers.py`:
  - `_record_dialogs(page) -> list[tuple[str, str]]`: moved verbatim from `test_live_draft.py`; accepts every dialog and records `(dialog.type, dialog.message)`.
  - `_click_and_wait_for_dialogs(page, dialogs, locator, count, timeout_ms=8000) -> list[tuple[str, str]]`: clicks `locator`, polls until `count` new dialogs were recorded in `dialogs` (or the timeout), asserts exactly `count` arrived, returns just those new entries.
  - `_assert_no_failure_dialogs(dialogs) -> None`: fails if any recorded message contains `fail` or `error` (case-insensitive).
- `test_live_draft.py` keeps `_record_dialogs` importable: `from tests_e2e.helpers import _record_dialogs  # re-exported: other modules import it from here`.

- [ ] **Step 1: Write the failing tests** (append to `tests_e2e/test_helpers.py`)

```python
from tests_e2e.helpers import (
    _assert_no_failure_dialogs,
    _click_and_wait_for_dialogs,
    _record_dialogs,
)


def test_record_dialogs_records_type_and_message(live_server, page):
    seen = _record_dialogs(page)
    page.goto("about:blank")

    page.evaluate("() => { alert('hello'); }")

    assert seen == [("alert", "hello")]


def test_click_and_wait_for_dialogs_returns_only_the_new_dialogs(live_server, page):
    page.goto("about:blank")
    page.set_content(
        "<button id='b' onclick=\"if (confirm('sure?')) alert('done')\">go</button>"
    )
    seen = _record_dialogs(page)
    seen.append(("alert", "earlier"))  # pre-existing entry must not be returned

    fresh = _click_and_wait_for_dialogs(page, seen, page.locator("#b"), count=2)

    assert fresh == [("confirm", "sure?"), ("alert", "done")]


def test_click_and_wait_for_dialogs_fails_when_too_few_arrive(live_server, page):
    page.goto("about:blank")
    page.set_content("<button id='b' onclick=\"alert('only one')\">go</button>")
    seen = _record_dialogs(page)

    with pytest.raises(AssertionError, match="expected 2 dialog"):
        _click_and_wait_for_dialogs(page, seen, page.locator("#b"), count=2, timeout_ms=500)


def test_assert_no_failure_dialogs():
    _assert_no_failure_dialogs([("alert", "Player created!")])
    with pytest.raises(AssertionError):
        _assert_no_failure_dialogs([("alert", "Reset failed: boom")])
    with pytest.raises(AssertionError):
        _assert_no_failure_dialogs([("alert", "Server ERROR")])
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests_e2e/test_helpers.py -q -p no:cacheprovider`
Expected: FAIL (ImportError on the new names).

- [ ] **Step 3: Implement.** Append to `tests_e2e/helpers.py` (move the docstring from `test_live_draft._record_dialogs` with it):

```python
def _record_dialogs(page):
    """Accept every native dialog while keeping a record of what it said.

    Both the Admin Portal (confirm() before generate/wipe, alert() with the
    result) and the draft room (alert() on a WebSocket `error` message, e.g.
    "It is not your turn to pick!") use native dialogs. Playwright
    auto-dismisses unhandled ones, which would silently hide a rejected pick
    or a failed admin action, so every page gets a recorder and the test
    asserts on its contents.
    """
    seen = []

    def _handle(dialog):
        seen.append((dialog.type, dialog.message))
        dialog.accept()

    page.on("dialog", _handle)
    return seen


def _click_and_wait_for_dialogs(page, dialogs, locator, count, timeout_ms=8000):
    """Click `locator` and wait until `count` NEW dialogs have been recorded in
    `dialogs` (a list returned by _record_dialogs). Polls rather than sleeping
    a fixed time: the alert() in these handlers only fires after an awaited
    backend call, so a fixed wait races it under load. Returns only the dialogs
    recorded by this click, and asserts there were exactly `count` of them.
    """
    start = len(dialogs)
    locator.click()
    waited = 0
    while len(dialogs) - start < count and waited < timeout_ms:
        page.wait_for_timeout(100)
        waited += 100
    fresh = dialogs[start:]
    assert len(fresh) == count, f"expected {count} dialog(s), saw {len(fresh)}: {fresh}"
    return fresh


def _assert_no_failure_dialogs(dialogs):
    """Fail if any recorded dialog message reads like a failure. The admin UI
    reports failures as alert('... failed: ...') / alert('Failed: ...'), which
    Playwright would otherwise auto-dismiss without a trace."""
    bad = [(kind, msg) for kind, msg in dialogs if "fail" in msg.lower() or "error" in msg.lower()]
    assert not bad, f"unexpected failure dialog(s): {bad}"
```

In `test_live_draft.py`, delete the local `_record_dialogs` definition and add `from tests_e2e.helpers import _record_dialogs  # re-exported: other modules import it from here` (keep a `# noqa: F401` only if the file no longer uses it; it does use it).

- [ ] **Step 4: Migrate `test_admin_player_management.py`.**
  - Each test that pops dialogs registers `dialogs = _record_dialogs(page)` right after `_login(...)`. For `_reclaim_player_password(page, live_server, target)` (called from the fixture teardown with the fixture's `page`) create the recorder inside the function.
  - Replace `_click_through_two_dialogs(page, card.locator(".btn-reset-pw"))` with:

    ```python
    fresh = _click_and_wait_for_dialogs(page, dialogs, card.locator(".btn-reset-pw"), count=2)
    assert [kind for kind, _ in fresh] == ["confirm", "alert"]
    assert fresh[0][1].startswith("Reset password for"), fresh
    ```
  - Replace the `page.once("dialog", ...)` handlers around the create-player, edit-player and set-temp-password clicks with `_click_and_wait_for_dialogs(page, dialogs, <the click locator>, count=1)` and assert the message: `"Player created!"`, `"Player updated successfully."`, and (temp password) that the single dialog is an `alert`. Replace the trailing `page.wait_for_timeout(1000)` that only existed to let that alert land.
  - At the end of each test body: `_assert_no_failure_dialogs(dialogs)`.
  - Delete `_click_through_two_dialogs` (retired; no call sites remain).
  - Fixture teardown path: its dialogs must not raise on the happy path; keep it as an assertion on dialog kinds only.

- [ ] **Step 5: Migrate `test_admin_tabs.py`.** In `test_admin_tab_loads`, `test_admin_player_management_tab_is_default_visible` and `test_admin_predictions_debug_page_loads`, add `dialogs = _record_dialogs(page)` immediately after `_login(...)` and `assert dialogs == [], f"unexpected dialog(s) while loading the tab: {dialogs}"` as the last line.

- [ ] **Step 6: Verify no loose handlers remain**

Run: `git grep -n "page.once(\"dialog\"\|_click_through_two_dialogs" -- tests_e2e`
Expected: only `test_admin_draft_overrides.py` (migrated in Task 4).

- [ ] **Step 7: Run the affected files**

Run: `python -m pytest tests_e2e/test_helpers.py tests_e2e/test_admin_tabs.py tests_e2e/test_admin_player_management.py tests_e2e/test_admin_members_and_recap.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): standardize on _record_dialogs, retire _click_through_two_dialogs"
```

---

### Task 3: Real logout instead of `localStorage.clear()`

**Files:**
- Modify: `tests_e2e/helpers.py`, `tests_e2e/test_helpers.py`, `tests_e2e/test_admin_player_management.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e.test_standings`.
- Produces: `_logout(page) -> None` in `tests_e2e/helpers.py`: on a signed-in desktop page, click `#nav-avatar-btn`, then `#nav-ap-logout-btn`, then wait for `#signin-screen` to become visible (the handler POSTs `/api/logout`, clears credentials and reloads).

- [ ] **Step 1: Write the failing test** (append to `tests_e2e/test_helpers.py`)

```python
from tests_e2e.helpers import _logout


def test_logout_clears_the_session_cookie_and_shows_signin(live_server, page, test_player_credentials):
    _login(page, live_server, test_player_credentials[0])
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)

    _logout(page)

    assert page.locator("#signin-screen").is_visible()
    assert not [c for c in page.context.cookies() if c["name"] == "session_token" and c["value"]]
    # The server agrees: a protected call without credentials is rejected.
    status = page.evaluate("() => fetch('/api/admin/players').then(r => r.status)")
    assert status in (401, 403)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests_e2e/test_helpers.py::test_logout_clears_the_session_cookie_and_shows_signin -q -p no:cacheprovider`
Expected: FAIL (ImportError for `_logout`).

- [ ] **Step 3: Implement** (append to `tests_e2e/helpers.py`)

```python
def _logout(page):
    """Sign out through the real UI (desktop layout): open the avatar popover,
    click Logout, and wait for the signin overlay. The handler POSTs
    /api/logout (clearing the httpOnly session_token cookie), clears
    localStorage credentials, and reloads -- unlike localStorage.clear(),
    which leaves the cookie claiming the old session.
    """
    page.click("#nav-avatar-btn")
    page.click("#nav-ap-logout-btn")
    page.wait_for_selector("#signin-screen", state="visible", timeout=10000)
```

- [ ] **Step 4: Migrate.** In `test_admin_player_management.py::_reclaim_player_password`, replace the `page.evaluate("() => localStorage.clear()")` line, the following `page.goto(live_server)`, and update the comment above them: call `_logout(page)` while still on `/admin` (after the reset-password step), then continue with the `#signin-screen` visible flow (fill email, blur, wait for the "Setup Account" button, fill both passwords, submit). `_logout` already leaves the page on the reloaded signin screen, so the explicit `page.goto(live_server)` and the visible-wait become redundant only if the reload lands on a page with `#signin-screen`; keep whichever wait is still needed so the flow stays deterministic. `git grep -n "localStorage.clear" -- tests_e2e` must return nothing afterwards.

- [ ] **Step 5: Run**

Run: `python -m pytest tests_e2e/test_helpers.py tests_e2e/test_admin_player_management.py -q -p no:cacheprovider`
Expected: all pass, including `test_reset_password_and_reclaim` and `test_set_temp_password` (both go through the reclaim path in fixture teardown).

- [ ] **Step 6: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): sign out through the real logout control instead of clearing localStorage"
```

---

### Task 4: `test_admin_draft_overrides.py` dialog assertions and context cleanup

**Files:**
- Modify: `tests_e2e/test_admin_draft_overrides.py`

**Interfaces:**
- Consumes: `_record_dialogs`, `_assert_no_failure_dialogs` from `tests_e2e.helpers`.

- [ ] **Step 1: Make the vacuous assertion strict.** Replace

```python
        assert not timer_dialogs[1:], (
            f"unexpected dialog(s) after the reset confirm() -- likely a server-side error alert: {timer_dialogs[1:]}"
        )
```

with

```python
        assert len(timer_dialogs) == 1 and timer_dialogs[0][0] == "confirm", (
            f"expected exactly one confirm() from the reset click and nothing else, saw: {timer_dialogs}"
        )
```

  Verify it can fail: temporarily change the expected length to `2` and confirm the test fails with that message, then restore (do not commit the temporary change).

- [ ] **Step 2: Undo step on `_record_dialogs`.** Change `_expand_and_click(row, action_btn_selector, dialog_page)` so it no longer registers a one-shot handler; the caller passes the recorder list: `_expand_and_click(row, action_btn_selector, dialogs)`. Create `admin_dialogs = _record_dialogs(admin_draft_page)` right after `admin_draft_page` is opened (before the undo), pass it to the undo click, and assert after the undo settles that `[kind for kind, _ in admin_dialogs] == ["confirm"]` and the message contains `undo`. Move the later `timer_dialogs = _record_dialogs(admin_draft_page)` to reuse `admin_dialogs` (a second recorder on the same page would double-record); the reset-timer assertion then checks the entries added since the undo (`len(admin_dialogs) == 2`, the second being a `confirm`). Add `_assert_no_failure_dialogs(admin_dialogs)` at the end.

- [ ] **Step 3: Context cleanup.** Move `setup_context = browser.new_context()` and the setup calls (`_login`, `_create_season_via_admin_ui`, `_set_draft_active`) inside the existing `try:` so the `finally:` that closes contexts also runs when setup raises. Append each per-player `ctx` to `contexts` immediately after `browser.new_context()` (before `_login`), so a failure mid-loop still closes it. Initialise `contexts, pages = [], []` and `setup_context = None` before the `try:`, and in `finally:` guard with `if setup_context is not None`.

- [ ] **Step 4: Run**

Run: `python -m pytest tests_e2e/test_admin_draft_overrides.py -q -p no:cacheprovider`
Expected: 1 passed (about 1-2 minutes; it connects 10 browser contexts).

- [ ] **Step 5: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): strict reset-timer dialog assertion, recorder for undo, leak-proof contexts"
```

---

### Task 5: Mock draft: shared `_login`, GET-reread verification

**Files:**
- Modify: `tests_e2e/helpers.py`, `tests_e2e/test_helpers.py`, `tests_e2e/test_mock_draft.py`, `tests_e2e/test_live_draft.py`

**Interfaces:**
- Produces: `_wait_for_config(page, key, want, timeout_s=15) -> None` in `tests_e2e/helpers.py`: polls `GET /api/config/settings` (via `page.evaluate(fetch)`, so it uses the page's origin) until `config[key] is want`, raising `AssertionError` on timeout naming the key and the last value seen.

- [ ] **Step 1: Write the failing test** (append to `tests_e2e/test_helpers.py`)

```python
from tests_e2e.helpers import _wait_for_config


def test_wait_for_config_returns_when_the_value_matches(live_server, page):
    page.goto(live_server)
    current = page.evaluate("() => fetch('/api/config/settings').then(r => r.json())")
    key = "draft_active"

    _wait_for_config(page, key, current.get(key) is True, timeout_s=5)


def test_wait_for_config_times_out_with_the_last_seen_value(live_server, page):
    page.goto(live_server)
    current = page.evaluate("() => fetch('/api/config/settings').then(r => r.json())")
    opposite = current.get("draft_active") is not True

    with pytest.raises(AssertionError, match="draft_active"):
        _wait_for_config(page, "draft_active", opposite, timeout_s=1)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests_e2e/test_helpers.py -q -p no:cacheprovider -k wait_for_config`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement** (append to `tests_e2e/helpers.py`; add `import time` at the top)

```python
def _wait_for_config(page, key, want, timeout_s=15):
    """Poll GET /api/config/settings until `config[key] is want`. Verifying
    through a fresh GET proves the server PERSISTED the change, which a toggle's
    optimistic aria-pressed flip or a POST-response listener does not (and the
    listener form needed a retry loop because it can miss the response event).
    """
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            last = page.evaluate(
                "() => fetch('/api/config/settings').then(r => r.json())"
            ).get(key)
            if last is want:
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise AssertionError(f"config {key!r} never became {want!r} (last seen: {last!r})")
```

- [ ] **Step 4: Use it in `test_live_draft.py::_set_draft_active`.** Replace the final `_poll(lambda: page.evaluate("fetch('/api/config/settings')...") is want, ...)` block with `_wait_for_config(page, "draft_active", want)`. Keep the earlier aria-pressed `_poll` steps unchanged.

- [ ] **Step 5: Rewrite the `mock_draft_enabled` fixture in `test_mock_draft.py`.**
  - Delete the inline login block (`admin_page.fill("#auth-email"...` through the `wait_for_selector("#signin-screen", state="hidden"...)`) and use `_login(admin_page, live_server, admin_creds)` (imported from `tests_e2e.test_standings`). Read `original_active` after `_login` through the same public GET (`page.evaluate(fetch)`), before touching the toggle; `goto(f"{live_server}/admin")` afterwards and open the tab with `_open_admin_tab(admin_page, "draft-section")` (this also retires `ADMIN_DRAFT_TAB_SELECTOR`; keep the constant defined only if something else still imports it, otherwise remove its use).
  - Replace `set_active(desired)` with a single click followed by `_wait_for_config(admin_page, "mock_draft_active", desired)`; no `expect_response`, no retry loop, no `PlaywrightTimeoutError` import. Keep the `expect(toggle).to_have_attribute("aria-pressed", ...)` initial-state wait (it prevents racing the toggle's own initial fetch). If the toggle is already in the desired state, return without clicking.
  - Wrap the `yield` in `try/finally` so `set_active(original_active)` and `context.close()` run even if the test body fails.

- [ ] **Step 6: Run**

Run: `python -m pytest tests_e2e/test_helpers.py tests_e2e/test_mock_draft.py -q -p no:cacheprovider`
Expected: all pass (`test_mock_draft_loads_and_allows_a_pick` runs at both viewports).

- [ ] **Step 7: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): mock-draft fixture uses shared _login and GET-reread config verification"
```

---

### Task 6: `test_live_draft.py` cleanup hardening

**Files:**
- Modify: `tests_e2e/test_live_draft.py`

**Interfaces:**
- Consumes: `_wait_for_config`, `_open_admin_tab`, `_record_dialogs` from `tests_e2e.helpers`.

- [ ] **Step 1: `clean_season_3000` pre-delete leak.** Wrap the pre-test `context, page = _admin_page()` / `_delete_season_via_admin_ui(...)` / `context.close()` in `try/finally` so the context closes when `_admin_page()`'s login or the delete raises. Because `_admin_page()` creates the context before it can fail, restructure it to return the context first (create the context, then `try: ... finally: context.close()` around the login and delete) so a failure in `_login` also closes it.

- [ ] **Step 2: Teardown order.** In the fixture's post-`yield` block, delete season 3000 first and restore `draft_active` second, with the restore in a `finally:` so it is attempted even when the delete raises:

```python
    context, page = _admin_page()
    try:
        try:
            _delete_season_via_admin_ui(page, live_server)
        finally:
            if original_draft_active["value"] is not None:
                _set_draft_active(page, live_server, original_draft_active["value"])
    finally:
        context.close()
```

  (`_admin_page()` must itself be leak-proof per Step 1, so the `context` here is always closed.)

- [ ] **Step 3: Test-body leaks.** In `test_full_ten_player_live_draft`: create `setup_context = None` and `contexts, pages, dialog_logs = [], [], []` before a single `try:` that starts at `setup_context = browser.new_context()`; move the setup calls (`_login`, `_create_season_via_admin_ui`, `_set_draft_active`, the setup-dialog assertion) inside it; append each player `ctx` to `contexts` immediately after `browser.new_context()` (before `_login`/`goto`); in the existing `finally:` guard `wp_context` and `setup_context` with `is not None`. Do not change any assertion or the draft-driving logic.

- [ ] **Step 4: Verify the leak fixes actually close contexts.** Add to `tests_e2e/test_helpers.py` nothing new; instead run a manual fault injection once (do not commit): temporarily make `_create_season_via_admin_ui` raise at its start, run `python -m pytest tests_e2e/test_live_draft.py -q -p no:cacheprovider`, confirm the failure is the injected error (not a hang or a teardown error) and that a following run of `tests_e2e/test_admin_tabs.py` still passes and `draft_active` in `GET /api/config/settings` is unchanged. Revert the injection.

- [ ] **Step 5: Run the real test**

Run: `python -m pytest tests_e2e/test_live_draft.py -q -p no:cacheprovider`
Expected: 1 passed (about 2-3 minutes).

- [ ] **Step 6: Commit**

```bash
git add tests_e2e
git commit -m "test(e2e): leak-proof live-draft contexts, delete season 3000 before restoring draft_active"
```

---

### Task 7: Verify, document, archive

- [ ] `python -m pytest tests/ -n auto -q -p no:cacheprovider` (expected: the same environment-only failures as `main` in a worktree: 2 `test_loaded_version`, 5 Firebase-schema errors; the intermittent `test_player_analytics` flake is known).
- [ ] `python -m pytest tests_e2e/ -q -p no:cacheprovider` (whole suite, with the e2e env vars set). Expected: all pass. Any failure must be compared against a run of the same test on the untouched base commit before it is called a regression.
- [ ] Check off this plan's steps; add a status line to both source specs listing what this branch resolved (spec 09-12 items 5 and 6; spec 09-13 items 1 through 6) and what remains open; move this plan to `plans/completed/`. Move a spec to `specs/completed/` only if every item in it is resolved (the 09-13 spec still has items 7 through 11; the 09-12 spec still has items 2, 3, 4, 7, 8), otherwise leave it in place with the updated status.
- [ ] Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch (do not merge or push without being asked).
