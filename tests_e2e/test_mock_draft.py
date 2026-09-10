"""tests_e2e/test_mock_draft.py — Login-free solo practice draft smoke test.

Selectors here were confirmed against the real rendered markup
(templates/mock_draft.html + static/js/mock_draft.js), not guessed:
  - Slot picker buttons:  .mock-slot-btn[data-slot="N"]  (inside #mock-slot-grid)
  - Team buttons:         .team-btn[data-team="XXX"]      (inside #mock-team-grid)
  - Confirm button:       #mock-confirm-pick-btn           ("Confirm Pick")
  - Pick queue:            #mock-pick-queue
There is no `.team-card`/`[data-team]`-on-a-card or `.pick-queue`/`#pick-queue`
as the original brief guessed -- see task-6-report.md for details.
"""
import json
import os

import pytest
from playwright.sync_api import expect

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, ".local_db", "config_settings.json")


@pytest.fixture
def mock_draft_enabled():
    """Force the mock_draft_active config flag on for the test, then restore.

    services.db_service.get_config_settings() re-reads .local_db/config_settings.json
    from disk on every call (no in-process cache for this doc), so the running
    live_server subprocess picks up this on-disk change on its very next request
    -- no restart needed. Without this, /mock-draft renders its "not currently
    available" branch (see routes/mock_draft_routes.py::_mock_draft_active) and
    none of the page's JS/selectors exist at all.
    """
    original_text = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            original_text = f.read()

    current = json.loads(original_text) if original_text else {"draft_active": False, "mock_draft_active": False}
    current["mock_draft_active"] = True
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(current, f)

    yield

    if original_text is not None:
        with open(CONFIG_PATH, "w") as f:
            f.write(original_text)
    else:
        os.remove(CONFIG_PATH)


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
