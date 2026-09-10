# UI Tests (Admin Flows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Playwright e2e suite with coverage for admin-only workflows not touched by the base plan (`docs/superpowers/plans/2026-09-09-ui-tests-playwright.md`): player management (create/edit/reset-password/set-temp-password), member/paid tracking, the weekly recap workflow (up to its real-external-API boundary), in-draft admin overrides, and smoke coverage for the read-heavy admin dashboards.

**Architecture:** New `tests_e2e/test_admin_*.py` files built on the existing harness (`live_server`, `browser`, `page`, `test_player_credentials` fixtures from `tests_e2e/conftest.py`, `_login` helper from `tests_e2e/test_standings.py`). All admin flows run as `test_player_credentials[0]` (`e2e-test-01`, role=admin). The in-draft-overrides test reuses the season-3000 setup/cleanup/pick helpers from the base plan's `tests_e2e/test_live_draft.py` (Task 10) rather than reimplementing them.

**Tech Stack:** Same as the base plan — Playwright Python sync API via `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §1 (UI Tests). Sibling plans: `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` (harness + core flows + live draft), `docs/superpowers/plans/2026-09-09-ui-tests-player-flows.md`, `docs/superpowers/plans/2026-09-09-ui-tests-auth-lifecycle.md`.

## Global Constraints

- Every test interacts through the real UI only — typed input, real clicks — never a shortcut via internal Python functions or direct API calls, matching the base plan's constraint.
- `DISABLE_OUTBOUND_EMAIL=true` (base plan Task 2) is already set for the whole `live_server` session — confirmed below (Task 3) that the recap-broadcast path actually routes through it before this plan relies on that as a safety net.
- **New constraint this plan introduces:** the recap workflow's "Generate AI Summary" step calls the real Gemini API (`services/ai_service.py::generate_generic_content`, no test-mode gate exists for it today, unlike the email path). This plan does **not** automate past that boundary — seeTask 4's explicit scope cut and rationale. Automating it would mean a real external API call (cost + nondeterministic output) on every pre-deploy run.
- The Elo/ML-Accuracy/Consensus/Betting tabs are read-heavy, low-risk admin dashboards — per explicit user decision, these get **smoke coverage only** (tab loads, content becomes visible, no server-error text), not deep interaction testing.

---

## Task 1: Admin dashboard tab-navigation smoke tests

**Files:**
- Create: `tests_e2e/test_admin_tabs.py`

**Interfaces:**
- Consumes: `live_server`, `page`, `test_player_credentials` fixtures; `_login` helper from `tests_e2e/test_standings.py`.

The admin dashboard (`templates/admin.html`) is a single page with 8 tabs, switched client-side by `admin_main.js::setupTabHandlers()`: clicking a `.admin-tabs .tab-btn[data-tab="<id>"]` button removes `.hidden` from `#<id>` and adds it to every other `.tab-content`. `player-section` is the only tab visible by default on page load (it has no `hidden` class in the template) — every other tab requires a click before its content is visible. This matters because the base plan's Task 8 admin-dashboard smoke test waits for `#player-grid` (which lives inside `#draft-section`, hidden by default) without clicking the "Draft" tab first — see this plan's Self-Review Notes for that flag.

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_admin_tabs.py — Admin dashboard tab navigation smoke tests.

Covers the read-heavy dashboards (Elo Ratings Explorer, ML Accuracy,
Consensus, Betting) at smoke-test depth per explicit scoping decision:
tab loads, its content becomes visible, no server-error text appears.
Deep interaction with these tools' filters/charts is out of scope.
"""
import pytest
from tests_e2e.test_standings import _login

TABS = [
    "draft-section",
    "members-section",
    "recap-section",
    "elo-section",
    "accuracy-section",
    "consensus-section",
    "betting-section",
]


@pytest.mark.parametrize("tab_id", TABS)
def test_admin_tab_loads(live_server, page, test_player_credentials, tab_id):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector(f'.admin-tabs .tab-btn[data-tab="{tab_id}"]', timeout=10000)
    page.click(f'.admin-tabs .tab-btn[data-tab="{tab_id}"]')

    page.wait_for_selector(f"#{tab_id}:not(.hidden)", timeout=10000)
    assert "Internal Server Error" not in page.content()


def test_admin_player_management_tab_is_default_visible(live_server, page, test_player_credentials):
    """player-section has no `hidden` class in templates/admin.html -- it's
    the tab shown on first load, with no click required."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)
    assert "Internal Server Error" not in page.content()


def test_admin_predictions_debug_page_loads(live_server, page, test_player_credentials):
    """Separate page route (routes/admin_routes.py's admin_predictions_page,
    templates/admin_predictions.html) -- not one of the 8 dashboard tabs."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin/predictions")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_admin_tabs.py -v`
Expected: PASS for all 7 tabs plus the two dedicated tests.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_admin_tabs.py
git commit -m "test: add e2e smoke tests for all admin dashboard tabs"
```

---

## Task 2: Player management (create, edit, reset password, set temp password)

**Files:**
- Create: `tests_e2e/test_admin_player_management.py`

**Interfaces:**
- Consumes: `live_server`, `page`, `browser`, `test_player_credentials`; `_login` helper.

`renderPlayerList` (`static/js/admin_main.js:241-353`) renders one `.player-mgmt-card[data-player-id="<id>"]` per player, each with `.btn-edit-player`, `.btn-reset-pw`, `.btn-temp-pw` buttons and hidden `.player-mgmt-edit`/`.player-mgmt-temppw` panels that toggle open. `createPlayer()` (line 477) has no `is_test_account` field in its form (`#new-player-name`/`#new-player-nick`/`#new-player-email`/`#new-player-phone`) — this plan's test therefore creates its player, then immediately flips `is_test_account` via the same player's `.btn-edit-player` panel is **not** possible either, since `updates` in `POST /api/admin/update_player` only allows `{fullName, nickName, email, cell}` (`routes/admin_routes.py:240`) — `is_test_account` isn't editable through this form at all. **Flagged gap, not worked around**: a player created through this admin UI today has no way to be marked as a test account after the fact except the one-time seed script's direct `update_player_profile` call (base plan Task 3). This test's created player is therefore cleaned up by deleting it is **also not possible** — there is no delete-player endpoint in `admin_routes.py` (only `delete_season`/`delete_draft_results_for_season`, which delete season/draft data, not player rows). This test accepts that its created player is a permanent addition to the local `.local_db/players.pkl` (or Firestore, when NOT run with `USE_LOCAL_DATA=true`) and uses a fixed, recognizable email so repeat runs create at most one duplicate-looking row rather than an unbounded number — see Step 1's idempotency note.

- [ ] **Step 1: Write the create-player test**

```python
"""tests_e2e/test_admin_player_management.py — Player CRUD-ish admin actions.

Known gap (see this plan's Task 2 docstring above): there is no
is_test_account field on the create-player form and no delete-player
endpoint at all, so a player created by this test is NOT cleaned up --
it becomes a permanent row in whatever backend the live_server subprocess
is pointed at (local .local_db/players.pkl in the normal dev/CI setup,
since live_server always sets USE_LOCAL_DATA=true). A fixed email keeps
reruns from accumulating unbounded duplicate-looking rows; check_player's
"exists" response is used to skip re-creating on a rerun.
"""
from tests_e2e.test_standings import _login

NEW_PLAYER_EMAIL = "e2e-created-player@winspool.internal"


def test_create_player(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    already_exists = page.locator(f'.player-mgmt-card:has-text("{NEW_PLAYER_EMAIL}")').count() > 0
    if already_exists:
        return  # idempotent: a prior run already created this fixture player

    page.fill("#new-player-name", "E2E Created Player")
    page.fill("#new-player-nick", "E2ECreated")
    page.fill("#new-player-email", NEW_PLAYER_EMAIL)
    page.fill("#new-player-phone", "555-0100")

    page.once("dialog", lambda d: d.accept())  # alert('Player created!')
    page.click("#create-player-btn")
    page.wait_for_timeout(1000)

    page.wait_for_selector(f'.player-mgmt-card:has-text("{NEW_PLAYER_EMAIL}")', timeout=10000)
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_admin_player_management.py::test_create_player -v`
Expected: PASS on first run (creates the player) and on rerun (finds it already exists, returns early).

- [ ] **Step 3: Write the edit-player test**

Targets a dedicated, already-seeded e2e test player rather than a real drafting account, so field edits don't disturb the base plan's live-draft test — `test_player_credentials[9]` (`e2e-test-10`, the last of the 10, never used for anything else across any of the plans in this series).

```python
def test_edit_player_profile(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    target = test_player_credentials[9]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)
    card.locator(".btn-edit-player").click()

    edit_panel = card.locator(".player-mgmt-edit")
    edit_panel.wait_for(state="visible", timeout=5000)

    phone_input = edit_panel.locator(".edit-phone")
    phone_input.fill("555-0199")

    page.once("dialog", lambda d: d.accept())  # alert('Player updated successfully.')
    edit_panel.locator(".btn-save-edit").click()
    page.wait_for_timeout(1000)

    # Page re-fetches and re-renders the whole list (fetchInitialData()) --
    # re-locate the card rather than reusing the (now-detached) old handle.
    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("555-0199")', timeout=10000)
```

- [ ] **Step 4: Run it**

Run: `pytest tests_e2e/test_admin_player_management.py::test_edit_player_profile -v`
Expected: PASS.

- [ ] **Step 5: Write the reset-password test**

`resetPlayerPassword()` clears `password_hash` entirely (`routes/admin_routes.py:255-271`) — this is destructive to the target's ability to log in until they re-claim. Use the same `test_player_credentials[9]` target and immediately re-claim the account within the test (via the real signin overlay's "Setup Account" flow) so the account is left in a working, logged-in-capable state for any other test that might run after this one in the same session.

```python
def test_reset_password_and_reclaim(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    target = test_player_credentials[9]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)

    page.once("dialog", lambda d: d.accept())  # confirm('Reset password for ...?')
    page.once("dialog", lambda d: d.accept())  # alert(data.message)
    card.locator(".btn-reset-pw").click()
    page.wait_for_timeout(1000)

    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("No Password")', timeout=10000)

    # Re-claim through the real signin overlay so target's credentials still
    # work for any later test in this session.
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", target["email"])
    page.locator("#auth-email").blur()
    page.wait_for_selector("#auth-submit-btn:has-text('Setup Account')", timeout=5000)

    page.fill("#auth-password", target["password"])
    page.fill("#auth-confirm-password", target["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
```

- [ ] **Step 6: Run it**

Run: `pytest tests_e2e/test_admin_player_management.py::test_reset_password_and_reclaim -v`
Expected: PASS, and `target`'s original password (from `E2E_TEST_PLAYER_PASSWORD`) works again afterward since the re-claim step sets it back to the same value.

- [ ] **Step 7: Write the set-temp-password test**

Covers the admin-side form and success confirmation only (the login-side consequence — `must_change_password` forcing a change screen — is the sibling auth-lifecycle plan's responsibility, `docs/superpowers/plans/2026-09-09-ui-tests-auth-lifecycle.md`). Uses the same `test_player_credentials[9]` target; ends by resetting the password back via the same reset-and-reclaim pattern as Step 5 so this test is independently idempotent regardless of run order relative to Step 5's test.

```python
def test_set_temp_password(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    target = test_player_credentials[9]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)
    card.locator(".btn-temp-pw").click()

    temppw_panel = card.locator(".player-mgmt-temppw")
    temppw_panel.wait_for(state="visible", timeout=5000)
    temppw_panel.locator(".temp-pw-input").fill("TempPass123!")

    page.once("dialog", lambda d: d.accept())  # alert(data.message)
    temppw_panel.locator(".btn-confirm-temppw").click()
    page.wait_for_timeout(1000)

    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("Temp Password")', timeout=10000)

    # Restore target to its normal, non-temp password so later tests in this
    # session (and the live-draft test's 10-account login flow) aren't broken.
    page.once("dialog", lambda d: d.accept())  # confirm('Reset password for ...?')
    page.once("dialog", lambda d: d.accept())  # alert(data.message)
    page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"] .btn-reset-pw').click()
    page.wait_for_timeout(1000)

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", target["email"])
    page.locator("#auth-email").blur()
    page.wait_for_selector("#auth-submit-btn:has-text('Setup Account')", timeout=5000)
    page.fill("#auth-password", target["password"])
    page.fill("#auth-confirm-password", target["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
```

- [ ] **Step 8: Run it**

Run: `pytest tests_e2e/test_admin_player_management.py -v`
Expected: PASS for all four tests in this file.

- [ ] **Step 9: Commit**

```bash
git add tests_e2e/test_admin_player_management.py
git commit -m "test: add e2e admin player management tests (create/edit/reset-password/temp-password)"
```

---

## Task 3: Member/paid tracking + recap-broadcast email-gate verification

**Files:**
- Create: `tests_e2e/test_admin_members_and_recap.py`

**Interfaces:**
- Consumes: `live_server`, `page`, `test_player_credentials`.

**Email-gate verification (done during planning, not a test step):** traced `save_and_broadcast_recap` (`routes/admin_routes.py:359-390`) → `email_service.send_weekly_recap_email(emails, subject, html_body)` → `services/email_service.py::send_weekly_recap_email` → `all(_send(email, subject, html_content) for email in to_emails)` → `_send()`, which is the exact function the base plan's Task 2 gates with `_outbound_email_disabled()`. **Confirmed**: the recap-broadcast path is fully covered by the existing `DISABLE_OUTBOUND_EMAIL` gate. No additional gate needed for this plan.

- [ ] **Step 1: Write the member paid-toggle test**

`initMembersTab()` (`static/js/admin_main.js:61-84`) auto-fetches `GET /api/admin/members/{season}` the moment the Members tab is clicked (no extra interaction needed to trigger the real API call). Each row's `.paid-toggle` button flips paid status via `POST /api/admin/members/paid`.

```python
"""tests_e2e/test_admin_members_and_recap.py — Member/paid tracking and the
weekly-recap workflow up to its real-Gemini-API boundary (see this plan's
Task 4 for why full recap generation is out of scope for automation)."""
from tests_e2e.test_standings import _login


def test_toggle_member_paid_status(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.click('.admin-tabs .tab-btn[data-tab="members-section"]')
    page.wait_for_selector("#members-section:not(.hidden)", timeout=10000)

    page.wait_for_selector(".paid-toggle", timeout=10000)
    first_toggle = page.locator(".paid-toggle").first
    before = first_toggle.get_attribute("data-paid")

    first_toggle.click()
    page.wait_for_timeout(1000)  # togglePaid() is async; no dialog, just a POST

    after = first_toggle.get_attribute("data-paid")
    assert after != before, "paid-toggle's data-paid attribute did not flip"

    # Toggle back so this test is idempotent across reruns.
    first_toggle.click()
    page.wait_for_timeout(1000)
    assert first_toggle.get_attribute("data-paid") == before
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_admin_members_and_recap.py::test_toggle_member_paid_status -v`
Expected: PASS if at least one season with enrolled members exists in the test data (it will — the base plan's live-draft test creates season 3000 with all 10 test players, though this test doesn't depend on that specific season and works against whichever season the Members tab defaults to loading, per `initMembersTab`'s "load the most recent season by default").

- [ ] **Step 3: Write the recap-prompt-preview test (the automatable boundary)**

```python
def test_recap_prompt_preview_populates(live_server, page, test_player_credentials):
    """Covers the free, deterministic part of the recap workflow: entering a
    year/week and generating the data prompt (pure data extraction, no
    external API call -- see routes/admin_routes.py's preview_recap_prompt,
    which only calls recap_service.extract_weekly_data + ai_service's plain
    string-template get_recap_prompt, not the Gemini API).

    Deliberately does NOT click 'Generate AI Summary' or 'Broadcast' --
    both require real completed game data for the chosen year/week to
    return non-404, and 'Generate' calls the real Gemini API with no
    test-mode gate (see this plan's Task 4)."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.click('.admin-tabs .tab-btn[data-tab="recap-section"]')
    page.wait_for_selector("#recap-section:not(.hidden)", timeout=10000)

    # A real, definitely-complete past week -- avoids the 404 branch for
    # "no game results found yet" that a future/in-progress week would hit.
    page.fill("#recap-year", "2024")
    page.fill("#recap-week", "1")

    page.click("#recap-preview-prompt-btn")
    page.wait_for_selector("#recap-prompt-preview-container:not(.hidden)", timeout=10000)

    prompt_text = page.locator("#recap-prompt-text").input_value()
    assert len(prompt_text) > 0, "recap prompt preview returned empty text"
```

- [ ] **Step 4: Run it**

Run: `pytest tests_e2e/test_admin_members_and_recap.py -v`
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```bash
git add tests_e2e/test_admin_members_and_recap.py
git commit -m "test: add e2e member paid-toggle test and recap-prompt-preview test"
```

---

## Task 4: Recap AI-generation — explicit scope cut (no test written)

**Files:** none — this task documents a decision, not code.

`generateRecapAI()` (`static/js/admin_main.js:539-561`) → `POST /api/admin/recap/generate` → `routes/admin_routes.py:346-356`'s `generate_admin_recap` → `ai_service.generate_generic_content(prompt_data)` (`services/ai_service.py:25-45`) → a **real** `genai.GenerativeModel('gemini-1.5-flash').generate_content(...)` call. Unlike the email path, there is no environment-gated bypass for this today.

- [ ] **Step 1: Record the gap (no code change in this plan)**

Automating past `test_recap_prompt_preview_populates` (Task 3) into clicking "Generate AI Summary" or "Save & Broadcast" would mean every pre-deploy e2e run makes a real, billed Gemini API call with nondeterministic output — unlike everything else in this test suite, which is fully local and free. That's a different risk profile than this plan should silently accept.

**Recommendation for a future, separate task** (not built here): add a `DISABLE_AI_GENERATION` (or similarly-named) env gate to `ai_service.py::generate_generic_content`, mirroring `email_service.py`'s `_outbound_email_disabled()` pattern from the base plan's Task 2 — return a fixed canned string when set, so the full recap → broadcast flow becomes safely automatable. Until that exists, `save_and_broadcast_recap` and `generate_admin_recap` remain manually-tested-only.

- [ ] **Step 2: No commit — this task is documentation-only, folded into this plan file itself.**

---

## Task 5: In-draft admin overrides (undo pick, reset timer)

**Files:**
- Create: `tests_e2e/test_admin_draft_overrides.py`

**Interfaces:**
- Consumes: `_create_season_3000`, `_activate_draft`, `_make_pick_when_it_is_my_turn`, `clean_season_3000` from `tests_e2e/test_live_draft.py` (base plan, Task 10) — imported, not reimplemented.

**Important finding, not worked around:** `draft_routes.py` defines a server-side `force_pick` WebSocket action (around line 672, read during this conversation's earlier research), but no current frontend code (`static/js/main.js`, `ui_renderer.js`) ever sends `{action: "force_pick"}` — the "admin picks on someone else's turn" behavior visible in `updateSelectionPreview()` (main.js:711-735, labelled "ADMIN OVERRIDE" in the UI) sends the ordinary `{action: "pick"}` message instead, which the server's `pick` handler already permits for an authenticated admin socket regardless of whose turn it is (`routes/draft_routes.py`'s `pick` handler, read earlier: `is_admin` bypasses the turn-ownership check). **`force_pick` therefore appears to be dead server code with no reachable UI trigger.** This plan does not write a test for it — a Playwright test can only drive what a real user can reach, and nothing reaches `force_pick`. Flagging this for the codebase owner rather than silently skipping it.

What **is** reachable and gets tested: admin picking out-of-turn (the actual "override" path), undo-last-pick (`.q-undo-btn`), and reset-timer (`.q-timer-btn`) — both rendered by `ui_renderer.js::renderPickQueue` (lines 240-310-ish) only for an admin viewer, on the just-completed pick row (`.q-row-admin .q-undo-btn`/`.q-reset-btn`) and the active pick row respectively, and wired via a delegated click listener in `main.js::initGlobalUI` (`.q-undo-btn` → `undoPick()`, `.q-reset-btn` → `resetPick()`, `.q-timer-btn` → `resetTimer()`).

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_admin_draft_overrides.py — In-draft admin controls.

Reuses season-3000 setup from the base plan's live-draft test rather than
reimplementing it. Runs a short draft (not the full 30 picks) since this
test only needs a couple of completed picks to exercise undo/reset-timer,
not a complete draft.
"""
from tests_e2e.test_standings import _login
from tests_e2e.test_live_draft import (
    SEASON, clean_season_3000, _create_season_3000, _activate_draft,
    _make_pick_when_it_is_my_turn,
)


def test_admin_can_undo_last_pick_and_reset_timer(live_server, browser, test_player_credentials, clean_season_3000):
    admin_creds = test_player_credentials[0]

    setup_context = browser.new_context()
    setup_page = setup_context.new_page()
    _login(setup_page, live_server, admin_creds)
    _create_season_3000(setup_page, live_server, test_player_credentials)
    _activate_draft(setup_page, live_server)

    # Player 2 (non-admin) makes the draft's first real pick.
    player_context = browser.new_context()
    player_page = player_context.new_page()
    _login(player_page, live_server, test_player_credentials[1])
    player_page.goto(f"{live_server}/draft/{SEASON}")
    player_page.wait_for_selector("#pick-queue, .pick-queue", timeout=10000)
    _make_pick_when_it_is_my_turn(player_page)

    # Admin views the same draft room and undoes that pick.
    admin_draft_page = setup_context.new_page()
    _login(admin_draft_page, live_server, admin_creds)
    admin_draft_page.goto(f"{live_server}/draft/{SEASON}")
    admin_draft_page.wait_for_selector(".q-row-admin .q-undo-btn", timeout=10000)

    admin_draft_page.once("dialog", lambda d: d.accept())  # confirm('Permanently undo the last pick?...')
    admin_draft_page.click(".q-row-admin .q-undo-btn")
    admin_draft_page.wait_for_timeout(1000)

    # After undo, pick #1 should be open again -- re-pick to get back to a
    # known state, then exercise reset-timer on the (again) active pick.
    _make_pick_when_it_is_my_turn(player_page)
    admin_draft_page.wait_for_selector(".q-timer-btn", timeout=10000)

    admin_draft_page.once("dialog", lambda d: d.accept())  # confirm('Reset the timer...?')
    admin_draft_page.click(".q-timer-btn")
    admin_draft_page.wait_for_timeout(500)

    assert "Internal Server Error" not in admin_draft_page.content()

    player_context.close()
    setup_context.close()
```

- [ ] **Step 2: Run it against the real element structure and fix selectors**

Run: `E2E_TEST_PLAYER_IDS=<ids> E2E_TEST_PLAYER_PASSWORD=<pw> pytest tests_e2e/test_admin_draft_overrides.py -v -s`

`.q-row-admin`, `.q-undo-btn`, `.q-timer-btn` are confirmed class names from `ui_renderer.js` source, but the base plan's own Task 10 (which this test imports helpers from) flags that its pick-loop selectors (`.team-card:not(.disabled)`) are not yet confirmed against live rendered DOM — if the base plan's Task 10 selectors needed correction during its own Step 5, apply the same corrections here since `_make_pick_when_it_is_my_turn` is shared code.

Expected once selectors are correct: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_admin_draft_overrides.py
git commit -m "test: add e2e admin in-draft override tests (undo pick, reset timer)"
```

---

## Self-Review Notes

- **Coverage:** Task 1 — 7 tab smoke tests + player-section default + `/admin/predictions` page. Task 2 — create/edit/reset-password/set-temp-password, all through real UI. Task 3 — member paid-toggle (real API, real UI) + recap-prompt-preview (the automatable half of the recap flow). Task 4 — explicit, documented scope cut for real-Gemini-API generation (not silently skipped). Task 5 — undo-pick and reset-timer, the two admin draft controls actually reachable from the UI.
- **Recap-broadcast email-gate: CONFIRMED respects `DISABLE_OUTBOUND_EMAIL`.** Traced the full call path in Task 3's docstring; no gap.
- **Flagged inconsistency in the base plan (not fixed here, out of this plan's scope):** `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` Task 8's admin-dashboard smoke test waits for `#player-grid` without first clicking the "Draft" tab — `#player-grid` lives inside `#draft-section`, which is `hidden` by default (only `#player-section` is visible on load). That test will very likely time out as written. Worth a fix when Task 8 is executed.
- **Flagged dead code, not a test gap:** `draft_routes.py`'s `force_pick` WebSocket action has no reachable UI trigger anywhere in the current frontend — see Task 5's finding.
- **Known follow-up, not built here:** `ai_service.py` needs a `DISABLE_AI_GENERATION`-style test-mode gate (mirroring the email gate) before the recap workflow can be automated past prompt-preview — see Task 4.
- **Known selector risk (flagged, not hidden):** all selectors in this plan were confirmed directly against `templates/admin.html` / `static/js/admin_main.js` / `static/js/ui_renderer.js` source during planning (not guessed), except the exact hover/visibility behavior of `.q-row-actions` (which wraps `.q-undo-btn`/`.q-reset-btn`) — if those buttons turn out to require a hover or expand interaction before being clickable, Task 5 Step 2's live run will surface it.
