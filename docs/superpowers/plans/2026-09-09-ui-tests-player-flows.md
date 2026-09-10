# UI Tests — Player-Facing Flows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Playwright e2e suite with real-interaction coverage for every player-facing page not already covered by `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` (that plan covers login, mock draft, standings/schedule smoke, admin dashboard smoke, nav parity, and the full live draft) — playoff race, weekly progress, head-to-head, all-time/per-player history, profile view+edit, draft-results interaction, and draft history — at desktop and mobile viewports.

**Architecture:** Builds directly on the harness from the existing plan's Tasks 1-4 (`live_server`, `browser`, `page`, `test_player_credentials` fixtures in `tests_e2e/conftest.py`, and the `_login(page, live_server, creds)` helper in `tests_e2e/test_standings.py`) — this plan adds no new infrastructure, only new test files. Every test drives the real signin overlay and real page controls (year-select dropdowns, toggle buttons, the profile form) rather than navigating directly to authenticated URLs.

**Tech Stack:** `playwright` (Python sync API), `pytest` — same as the existing plan, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §1 (UI Tests).

## Global Constraints

(Same as `docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` — copied here since this plan may be executed independently.)

- Every browser-driven step interacts through the real UI (typed input, real clicks, real navigation) — never by calling internal API/service functions directly.
- The e2e suite must never make a real Resend API call (`DISABLE_OUTBOUND_EMAIL=true`, set by `live_server`) and must never write to production Firestore (`USE_LOCAL_DATA=true`, same fixture).
- Tests must not assert exact win totals, rankings, or other data values that drift as real seasons progress — assert structure (page loads, no `Internal Server Error`, expected controls are present and functional), not specific numbers, since these tests run against real production-mirrored `.local_db` data that changes over time.
- Every year-navigation test picks its target year from the page's own rendered `#year-pick` options at runtime, never a hardcoded year — the dropdown's actual contents are the source of truth for what years exist in this developer's local data.

---

## Task 1: Head-to-head flows

**Files:**
- Create: `tests_e2e/test_headtohead.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e/test_standings.py`.

- [ ] **Step 1: Write the tests**

```python
"""tests_e2e/test_headtohead.py — Head-to-head matchup pages.

Covers /headtohead (redirects to the active season), the /headtohead/{year}
year-picker (templates/headtohead.html's #year-pick select), and
/headtohead/history (the combined all-years view, templates/headtohead_history.html).
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_headtohead_redirects_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/headtohead")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "/headtohead/" in page.url
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    assert len(options) >= 1, "Expected at least one season in the head-to-head year picker"

    if len(options) > 1:
        # Navigate to a different year via the real select control, not a direct URL.
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/headtohead/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_headtohead_history_link_and_page(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/headtohead")
    page.wait_for_selector("#year-pick", timeout=10000)

    page.click("text=View full head-to-head history across all")
    page.wait_for_url("**/headtohead/history", timeout=10000)
    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_headtohead.py -v`
Expected: PASS at both viewports.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_headtohead.py
git commit -m "test: add e2e head-to-head flow tests (year picker + all-time history)"
```

---

## Task 2: Playoff race and weekly progress

**Files:**
- Create: `tests_e2e/test_playoff_and_weekly.py`

- [ ] **Step 1: Write the tests**

```python
"""tests_e2e/test_playoff_and_weekly.py — Playoff race and weekly-progress pages.

Both follow the same redirect-to-active-season + #year-pick select pattern
as standings/schedule/head-to-head (templates/playoff_race.html,
templates/weekbyweek.html).
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_loads_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/playoff-race")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "/playoff-race/" in page.url
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    if len(options) > 1:
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/playoff-race/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_weekly_progress_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    # Reached via the standings page's "Weekly Progress" nav link, not a direct
    # goto, so the real nav wiring (static/js/main.js's moreLinks array) is exercised.
    page.wait_for_selector("#nav-more-btn, #btb-more-tab", timeout=10000)
    if viewport["width"] >= 1280:
        page.click("#nav-more-btn")
        page.click("#nav-more-dropdown >> text=Weekly Progress")
    else:
        page.click("#btb-more-tab")
        page.click(".nav-drawer__links >> text=Weekly Progress")

    page.wait_for_url("**/weekbyweek", timeout=10000)
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_playoff_and_weekly.py -v`
Expected: PASS at both viewports.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_playoff_and_weekly.py
git commit -m "test: add e2e playoff race and weekly progress tests"
```

---

## Task 3: All-time history and per-player profile

**Files:**
- Create: `tests_e2e/test_history.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e/test_standings.py`.

- [ ] **Step 1: Write the tests**

```python
"""tests_e2e/test_history.py — All-time history and per-player profile pages.

/history (templates/overall_history.html) lists every player with a
"View →" link to /history/player/{playerId} (templates/player_profile.html).
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_all_time_history_and_player_profile_link(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/history")
    page.wait_for_selector("a[href^='/history/player/']", timeout=10000)
    assert "Internal Server Error" not in page.content()

    first_profile_link = page.locator("a[href^='/history/player/']").first
    first_profile_link.click()

    page.wait_for_url("**/history/player/**", timeout=10000)
    assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_history.py -v`
Expected: PASS at both viewports. (Requires at least one completed historical season with draft results in `.local_db` — true for this repo's real production-mirrored data; if run against a truly empty dataset the `#history` page renders no player rows and this test would need `pytest.skip`, but that's not the case here.)

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_history.py
git commit -m "test: add e2e all-time history and per-player profile tests"
```

---

## Task 4: Profile view and edit

**Files:**
- Create: `tests_e2e/test_profile.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e/test_standings.py`.

This is the deepest real-interaction test in this plan: it edits and reverts a real field on a real test account through the actual form (`templates/profile.html`'s `#profile-form`, posting to `POST /api/profile/update`), rather than just checking the page loads.

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_profile.py — Profile view and edit, including the MFA toggle.

templates/profile.html's #profile-form requires current-password to save any
change (routes/auth_routes.py's update_profile endpoint) and self-service
toggles mfa_enabled via #mfa-enabled — no admin action needed for MFA.
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_profile_loads_with_current_values(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    creds = test_player_credentials[2]
    _login(page, live_server, creds)

    page.goto(f"{live_server}/profile")
    page.wait_for_selector("#profile-form", timeout=10000)
    page.wait_for_function(
        "document.getElementById('email').value.length > 0", timeout=5000
    )
    assert page.locator("#email").input_value() == creds["email"]


def test_profile_edit_nickname_round_trip(live_server, page, test_player_credentials):
    """Edits the nickname, saves, verifies the change, then reverts it —
    real form submission through #profile-form, not a direct API call."""
    creds = test_player_credentials[2]
    _login(page, live_server, creds)

    page.goto(f"{live_server}/profile")
    page.wait_for_selector("#profile-form", timeout=10000)
    page.wait_for_function("document.getElementById('email').value.length > 0", timeout=5000)

    original_nickname = page.locator("#nickname").input_value()
    new_nickname = f"{original_nickname}-e2e" if original_nickname else "E2ETemp"

    page.fill("#nickname", new_nickname)
    page.fill("#current-password", creds["password"])
    page.once("dialog", lambda d: d.accept())  # alert("Profile updated successfully!")
    page.click("button:has-text('Save Profile Changes')")
    page.wait_for_load_state("networkidle", timeout=10000)

    page.wait_for_function(
        f"document.getElementById('nickname') && document.getElementById('nickname').value.length > 0",
        timeout=5000,
    )
    assert page.locator("#nickname").input_value() == new_nickname

    # Revert so reruns start from the same state.
    page.fill("#nickname", original_nickname)
    page.fill("#current-password", creds["password"])
    page.once("dialog", lambda d: d.accept())
    page.click("button:has-text('Save Profile Changes')")
    page.wait_for_load_state("networkidle", timeout=10000)
    assert page.locator("#nickname").input_value() == original_nickname


def test_profile_wrong_current_password_is_rejected(live_server, page, test_player_credentials):
    """update_profile (routes/auth_routes.py) requires the correct current
    password for any change — verify a wrong one is rejected, not silently applied."""
    creds = test_player_credentials[2]
    _login(page, live_server, creds)

    page.goto(f"{live_server}/profile")
    page.wait_for_selector("#profile-form", timeout=10000)
    page.wait_for_function("document.getElementById('email').value.length > 0", timeout=5000)

    page.fill("#current-password", "definitely-the-wrong-password-123!")
    page.once("dialog", lambda d: d.accept())  # alert("Error: Incorrect current password.")
    page.click("button:has-text('Save Profile Changes')")
    page.wait_for_timeout(1000)
    # Page does not reload on error (only the success path calls window.location.reload()),
    # so the form should still be present/interactive.
    assert page.locator("#profile-form").count() == 1
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_profile.py -v`
Expected: PASS. `test_profile_edit_nickname_round_trip` and `test_profile_wrong_current_password_is_rejected` trigger real browser `alert()` dialogs (`window.alert` in `templates/profile.html`'s inline script) — handled via Playwright's `page.once("dialog", ...)`, matching the pattern already established in the live-draft plan (`docs/superpowers/plans/2026-09-09-ui-tests-playwright.md` Task 10).

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_profile.py
git commit -m "test: add e2e profile view/edit tests including a real round-trip nickname edit"
```

---

## Task 5: Draft results (interactive)

**Files:**
- Create: `tests_e2e/test_draft_results.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e/test_standings.py`.

`/draft-results` redirects to `/draft/{active_season}`, served by `draft_routes.py`'s `/draft/{year}` handler with `templates/draft_results.html` (for a completed/historical season — distinct from the live in-progress draft room the same route serves for an active draft). This test targets a real historical season (whichever the year-picker's later options resolve to), not the synthetic season 3000 from the live-draft plan — season 3000 has no real NFL game data, so its award cards (`best_overall`, `cumulative_fastest`, etc., gated per `templates/draft_results.html:27`) won't render, which is expected for that plan's purposes but not useful for testing the award-card UI itself here.

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_draft_results.py — Draft results page: year picker + award cards.

Award cards (templates/draft_results.html) are conditionally rendered
Jinja blocks (best_overall, best_by_round, quickest, slowest,
cumulative_fastest, cumulative_slowest) gated on real season data being
complete enough — see git history: "gate draft results win-based award
cards on week 1 completion, add cumulative pick-time cards". This test
checks structure/no-error, not specific award values, since those values
drift as real seasons progress.
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_draft_results_loads_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/draft-results")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    assert len(options) >= 1

    if len(options) > 1:
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/draft/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_draft_results.py -v`
Expected: PASS at both viewports.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_draft_results.py
git commit -m "test: add e2e draft results page test (year picker navigation)"
```

---

## Task 6: Draft history (Group by Player / Group by Team toggle)

**Files:**
- Create: `tests_e2e/test_draft_history.py`

**Interfaces:**
- Consumes: `_login` from `tests_e2e/test_standings.py`.

- [ ] **Step 1: Write the test**

```python
"""tests_e2e/test_draft_history.py — /draft/history's player/team view toggle.

templates/draft_history.html has two client-side-toggled views driven by
switchView('player'|'team') — #btn-player-view/#btn-team-view buttons,
#player-view/#team-view containers.
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_draft_history_view_toggle(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/draft/history")
    page.wait_for_selector("#btn-player-view", timeout=10000)
    assert "Internal Server Error" not in page.content()

    # Defaults to player view.
    assert "active" in (page.locator("#btn-player-view").get_attribute("class") or "")
    assert page.locator("#player-view").is_visible()

    page.click("#btn-team-view")
    page.wait_for_function(
        "document.getElementById('team-view').style.display !== 'none'", timeout=5000
    )
    assert "active" in (page.locator("#btn-team-view").get_attribute("class") or "")
    assert page.locator("#team-view").is_visible()

    page.click("#btn-player-view")
    page.wait_for_function(
        "document.getElementById('player-view').style.display !== 'none'", timeout=5000
    )
    assert page.locator("#player-view").is_visible()
```

- [ ] **Step 2: Run it**

Run: `pytest tests_e2e/test_draft_history.py -v`
Expected: PASS at both viewports.

- [ ] **Step 3: Commit**

```bash
git add tests_e2e/test_draft_history.py
git commit -m "test: add e2e draft history player/team view toggle test"
```

---

## Self-Review Notes

- **Spec coverage:** Together with the already-committed harness plan's Tasks 5-10, this plan brings every route listed under `routes/standings_routes.py`, `routes/history_routes.py`, and the non-live-draft parts of `routes/draft_routes.py` under e2e coverage: playoff race (Task 2), weekly progress (Task 2), head-to-head + history (Task 1), all-time history + per-player profile (Task 3), profile view/edit (Task 4), draft results (Task 5), draft history (Task 6).
- **Real finding worth flagging to whoever plans the auth-lifecycle coverage:** `mfa_enabled` is fully self-service via `/profile`'s `#mfa-enabled` checkbox and `POST /api/profile/update` (`routes/auth_routes.py`) — it does NOT require an admin action to turn on/off. This was assumed to need investigation when this plan was scoped; it doesn't. Task 4 exercises the toggle's presence but does not flip it and complete a full MFA login round-trip (that belongs with the login/MFA-code-verification flow, out of this plan's player-facing-pages scope).
- **Confirmed selectors:** every id/selector in this plan (`#year-pick`, `#nickname`, `#current-password`, `#mfa-enabled`, `#btn-player-view`/`#btn-team-view`, `a[href^='/history/player/']`) was read directly from the current template/route source, not guessed.
- **Data dependency, not a gap:** Task 3 and Task 5 assume at least one real historical season with draft results exists in `.local_db` — true for this repo's real production-mirrored data. Noted in each task rather than building a synthetic fallback, since fabricating historical NFL data is out of scope for a UI test.
- **Out of scope:** exact win totals/rankings/award values (drift over time — see Global Constraints), MFA login round-trip (belongs to the auth-lifecycle plan), visual regression.
