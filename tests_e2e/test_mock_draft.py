"""tests_e2e/test_mock_draft.py — Login-free solo practice draft smoke test.

Selectors here were confirmed against the real rendered markup
(templates/mock_draft.html + static/js/mock_draft.js), not guessed:
  - Slot picker buttons:  .mock-slot-btn[data-slot="N"]  (inside #mock-slot-grid)
  - Team buttons:         .team-btn[data-team="XXX"]      (inside #mock-team-grid)
  - Confirm button:       #mock-confirm-pick-btn           ("Confirm Pick")
  - Pick queue:            #mock-pick-queue
There is no `.team-card`/`[data-team]`-on-a-card or `.pick-queue`/`#pick-queue`
as the original brief guessed -- see task-6-report.md for details.

/mock-draft is gated behind the `mock_draft_active` config flag (off by
default locally) -- see routes/mock_draft_routes.py::_mock_draft_active.
The `mock_draft_enabled` fixture below flips it on for the test via the
real admin-UI toggle (#mock-draft-active-toggle in templates/admin.html,
wired up by initMockDraftActiveToggle() in static/js/admin_main.js), the
same way a human admin would, per the plan's global constraint that every
browser-driven step goes through the real UI rather than shortcutting
around it. That setup runs in a SEPARATE browser context that logs in as
an admin test player -- the actual test's `page` fixture stays a fresh,
unauthenticated session throughout, preserving the "login-free" intent of
this smoke test.
"""
import pytest
from playwright.sync_api import expect

from tests_e2e.helpers import _open_admin_tab, _wait_for_config
from tests_e2e.test_standings import _login

MOCK_TOGGLE_SELECTOR = "#mock-draft-active-toggle"


@pytest.fixture
def mock_draft_enabled(live_server, browser, test_player_credentials):
    """Ensure mock_draft_active is on for the test, then restore its original
    state -- both transitions driven through the real admin toggle, not a
    config-file or internal-function shortcut.
    """
    admin_creds = test_player_credentials[0]  # index 0 is always the admin, per conftest.py
    context = browser.new_context()
    admin_page = context.new_page()

    try:
        _login(admin_page, live_server, admin_creds)

        # Read the true pre-test state via the same public GET the page itself
        # calls -- reading over HTTP isn't a shortcut, only mutating state
        # outside the real UI would be.
        original_active = admin_page.evaluate(
            "() => fetch('/api/config/settings').then(r => r.json())"
        ).get("mock_draft_active") is True

        admin_page.goto(f"{live_server}/admin")
        admin_page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
        _open_admin_tab(admin_page, "draft-section")
        toggle = admin_page.locator(MOCK_TOGGLE_SELECTOR)
        toggle.wait_for(state="visible", timeout=10000)
        # initMockDraftActiveToggle() syncs the toggle's aria-pressed from the
        # same GET above, asynchronously, right after page load -- wait for it
        # to settle to the value we already know is true, so the click below
        # (whose handler reads aria-pressed itself to decide what to send)
        # isn't racing the toggle's own initial-state fetch.
        expect(toggle).to_have_attribute(
            "aria-pressed", "true" if original_active else "false", timeout=5000
        )

        def set_active(desired: bool):
            current = toggle.get_attribute("aria-pressed") == "true"
            if current == desired:
                return
            toggle.click()
            _wait_for_config(admin_page, "mock_draft_active", desired)

        try:
            set_active(True)
            yield
        finally:
            set_active(original_active)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},   # desktop
    {"width": 390, "height": 844},    # mobile (iPhone 14-class width, per CLAUDE.md's ~390px breakpoint)
])
def test_mock_draft_loads_and_allows_a_pick(live_server, page, mock_draft_enabled, viewport):
    page.set_viewport_size(viewport)
    page.goto(f"{live_server}/mock-draft")

    # No signin overlay -- mock_draft.html deliberately doesn't extend base.html,
    # so #signin-screen (main.js's login wall) never even exists on this page.
    assert page.locator("#signin-screen").count() == 0

    page.wait_for_selector("#mock-slot-grid .mock-slot-btn", timeout=10000)

    # Pick the slot that goes first in the real pick order so our turn comes
    # up immediately, avoiding a wait through bot picks (each bot pick is a
    # real fetch + a 700ms UI delay in mock_draft.js).
    setup = page.evaluate("() => fetch('/api/mock-draft/setup').then(r => r.json())")
    first_slot = setup["pickSequence"][0]["slot"]
    page.click(f'.mock-slot-btn[data-slot="{first_slot}"]')

    page.wait_for_selector("#mock-team-grid .team-btn", timeout=10000)
    first_team_btn = page.locator("#mock-team-grid .team-btn").first
    team_code = first_team_btn.get_attribute("data-team")
    first_team_btn.click()

    # select-then-confirm pattern per CLAUDE.md's Mock Draft section
    confirm_btn = page.locator("#mock-confirm-pick-btn")
    confirm_btn.wait_for(state="visible", timeout=5000)
    confirm_btn.click()

    # Pick registered: the pick queue's first row now shows our picked team
    # instead of a "picking" placeholder.
    expect(page.locator("#mock-pick-queue")).to_contain_text(team_code, timeout=10000)
