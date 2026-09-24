"""tests_e2e/test_admin_player_management.py — Player CRUD-ish admin actions.

Known gap (see this plan's Task 2 docstring above): there is no
is_test_account field on the create-player form and no delete-player
endpoint at all, so a player created by this test is NOT cleaned up --
it becomes a permanent row in whatever backend the live_server subprocess
is pointed at (local .local_db/players.pkl in the normal dev/CI setup,
since live_server always sets USE_LOCAL_DATA=true). A fixed email keeps
reruns from accumulating unbounded duplicate-looking rows; test_create_player
waits for the (async-fetched) `.player-mgmt-card` list to actually render,
then checks whether one of those cards already contains the fixed email,
to skip re-creating on a rerun -- see that test for why a plain `.count()`
without the wait is not reliable.

Two more live-DOM gaps discovered while writing these tests (not present in
the original plan, which was checked against source but not run live):

1. All 10 seeded e2e test players (scripts/seed_e2e_test_players.py) are
   flagged is_test_account=True, and fetchInitialData() (admin_main.js)
   fetches with includeTest=false by default, so a target player's
   `.player-mgmt-card` simply isn't in the DOM until the "Show test
   accounts" checkbox (`#show-test-accounts-toggle`) is checked. That
   checkbox itself lives in #draft-section (templates/admin.html), not
   #player-section, so a real click() on it fails Playwright's visibility
   check whenever #player-section is the active tab. `_refetch_players()`
   below switches to the Draft tab (a real click on its
   `_open_admin_tab(page, "draft-section")`) to unhide the checkbox's
   container, clicks the checkbox for real, then switches back to the Players
   tab (`_open_admin_tab(page, "player-section")`) -- tab switching only toggles a `hidden`
   class (admin_main.js's setupTabHandlers()), so the Players-tab DOM/state
   persists underneath the whole time.
2. resetPlayerPassword() and setTempPassword() (admin_main.js) never call
   fetchInitialData() after their action completes -- unlike
   savePlayerEdit(), which does -- so a card's password-status badge is
   stale until something re-fetches. `_refetch_players()` is reused after
   each action for this too.
"""
import pytest

from tests_e2e.helpers import (
    _assert_no_failure_dialogs,
    _click_and_wait_for_dialogs,
    _logout,
    _open_admin_tab,
    _record_dialogs,
)
from tests_e2e.test_standings import _login

NEW_PLAYER_EMAIL = "e2e-created-player@winspool.internal"


@pytest.fixture
def restore_player_9(live_server, page, test_player_credentials):
    """Guarantee test_player_credentials[9]'s password is restored to its
    seeded working value even if the test body fails partway through a
    destructive mutation (reset-password or set-temp-password).

    The restore runs in fixture teardown, which Python's generator-fixture
    semantics run even when the test body raises (a finally-equivalent) --
    unlike the inline end-of-function restore this replaces, which was
    simply skipped on any earlier failure (a selector timeout, a flaky
    dialog, "Setup Account" never appearing), leaving account 9 with no
    working password for every later test in the session (notably
    test_live_draft.py's 10-account login flow, which runs after this file
    alphabetically).

    Safe to run regardless of what state the test left the account in:
    resetPlayerPassword() unconditionally clears whatever password/temp
    password currently exists, so forcing a reset before reclaiming is a
    no-op if the test already did so, and still correct if the test failed
    before ever touching the account.
    """
    target = test_player_credentials[9]
    yield target
    _reclaim_player_password(page, live_server, target)


def _reclaim_player_password(page, live_server, target):
    """Admin-reset target's password (idempotent regardless of its current
    state) then reclaim it through the real signin overlay's Setup Account
    flow, restoring target["password"] as its working password."""
    dialogs = _record_dialogs(page)  # idempotent: returns the test's own list
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)
    _refetch_players(page)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)
    fresh = _click_and_wait_for_dialogs(page, dialogs, card.locator(".btn-reset-pw"), count=2)
    assert [kind for kind, _ in fresh] == ["confirm", "alert"]

    # The admin's own session is still live (localStorage credentials plus the
    # httpOnly session cookie), which would keep the signin overlay hidden --
    # sign out through the real avatar-popover Logout control. It reloads onto
    # the signin overlay and waits for it to be visible.
    _logout(page)
    page.fill("#auth-email", target["email"])
    page.locator("#auth-email").blur()
    page.wait_for_selector("#auth-submit-btn:has-text('Setup Account')", timeout=5000)
    page.fill("#auth-password", target["password"])
    page.fill("#auth-confirm-password", target["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)


def _refetch_players(page):
    """Force AdminApp.fetchInitialData() to re-run with the "Show test
    accounts" toggle checked -- see the module docstring's gaps 1 and 2.

    The checkbox lives in #draft-section, not #player-section, so it isn't
    visible while the Players tab is active. Switch to the Draft tab (real
    click), toggle the box with real clicks (firing its native `change`
    listener, which is what actually triggers fetchInitialData() --
    admin_main.js line ~238), then switch back to the Players tab -- tab
    switching only toggles a `hidden` class (admin_main.js's
    setupTabHandlers()), so the Players-tab DOM/state persists underneath
    the whole time.

    This helper is called more than once per test (e.g. once before an
    action, once after, to pick up a stale badge -- see gap 2), so the
    checkbox may already be checked from a prior call. A `change` event
    only fires on an actual state transition, so if it's already checked,
    click it off first -- the final click that leaves it checked is then
    guaranteed to fire `change` and trigger a fresh fetch every time,
    rather than silently no-op on the second-and-later call.
    """
    _open_admin_tab(page, "draft-section")

    checkbox = page.locator("#show-test-accounts-toggle")
    if checkbox.is_checked():
        checkbox.click()
    checkbox.click()

    _open_admin_tab(page, "player-section")
    page.wait_for_timeout(500)


def test_create_player(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)
    dialogs = _record_dialogs(page)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    # fetchInitialData() is async -- #player-section loses its "hidden" class
    # immediately, well before the player list's network round trip resolves,
    # so a bare `.count()` here would almost always race an empty DOM and
    # fall through to creating a duplicate. Wait for the list to actually
    # render (any card, not necessarily the target one) before checking.
    page.wait_for_selector(".player-mgmt-card", timeout=10000)
    already_exists = page.locator(f'.player-mgmt-card:has-text("{NEW_PLAYER_EMAIL}")').count() > 0
    if already_exists:
        return  # idempotent: a prior run already created this fixture player

    page.fill("#new-player-name", "E2E Created Player")
    page.fill("#new-player-nick", "E2ECreated")
    page.fill("#new-player-email", NEW_PLAYER_EMAIL)
    page.fill("#new-player-phone", "555-0100")

    fresh = _click_and_wait_for_dialogs(page, dialogs, page.locator("#create-player-btn"), count=1)
    assert fresh == [("alert", "Player created!")]

    page.wait_for_selector(f'.player-mgmt-card:has-text("{NEW_PLAYER_EMAIL}")', timeout=10000)
    _assert_no_failure_dialogs(dialogs)


def test_edit_player_profile(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    target = test_player_credentials[9]
    _login(page, live_server, admin_creds)
    dialogs = _record_dialogs(page)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    _refetch_players(page)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)
    card.locator(".btn-edit-player").click()

    edit_panel = card.locator(".player-mgmt-edit")
    edit_panel.wait_for(state="visible", timeout=5000)

    phone_input = edit_panel.locator(".edit-phone")
    phone_input.fill("555-0199")

    fresh = _click_and_wait_for_dialogs(page, dialogs, edit_panel.locator(".btn-save-edit"), count=1)
    assert fresh == [("alert", "Player updated successfully.")]

    # savePlayerEdit() calls fetchInitialData() itself (unlike the reset/temp
    # password actions -- see module docstring gap 2), but that refetch uses
    # whatever includeTest the checkbox holds at the time, which is still
    # true from _refetch_players() above -- re-locate the card rather than
    # reusing the (now-detached) old handle.
    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("555-0199")', timeout=10000)
    _assert_no_failure_dialogs(dialogs)


def test_reset_password_and_reclaim(live_server, page, test_player_credentials, restore_player_9):
    admin_creds = test_player_credentials[0]
    target = restore_player_9
    _login(page, live_server, admin_creds)
    dialogs = _record_dialogs(page)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    _refetch_players(page)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)

    fresh = _click_and_wait_for_dialogs(page, dialogs, card.locator(".btn-reset-pw"), count=2)
    assert [kind for kind, _ in fresh] == ["confirm", "alert"]
    assert fresh[0][1].startswith("Reset password for"), fresh

    _refetch_players(page)  # resetPlayerPassword() doesn't self-refresh -- see gap 2
    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("No Password")', timeout=10000)
    _assert_no_failure_dialogs(dialogs)

    # Reclaiming target's credentials (so they still work for any later test
    # in this session) now happens unconditionally in the restore_player_9
    # fixture's teardown -- see that fixture -- rather than inline here, so
    # a failure above still leaves the account restored.


def test_set_temp_password(live_server, page, test_player_credentials, restore_player_9):
    admin_creds = test_player_credentials[0]
    target = restore_player_9
    _login(page, live_server, admin_creds)
    dialogs = _record_dialogs(page)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)

    _refetch_players(page)

    card = page.locator(f'.player-mgmt-card[data-player-id="{target["id"]}"]')
    card.wait_for(state="visible", timeout=10000)
    card.locator(".btn-temp-pw").click()

    temppw_panel = card.locator(".player-mgmt-temppw")
    temppw_panel.wait_for(state="visible", timeout=5000)
    temppw_panel.locator(".temp-pw-input").fill("TempPass123!")

    fresh = _click_and_wait_for_dialogs(page, dialogs, temppw_panel.locator(".btn-confirm-temppw"), count=1)
    assert [kind for kind, _ in fresh] == ["alert"]

    _refetch_players(page)  # setTempPassword() doesn't self-refresh -- see gap 2
    page.wait_for_selector(f'.player-mgmt-card[data-player-id="{target["id"]}"]:has-text("Temp Password")', timeout=10000)
    _assert_no_failure_dialogs(dialogs)

    # Restoring target to its normal, non-temp password now happens
    # unconditionally in the restore_player_9 fixture's teardown -- see that
    # fixture -- rather than inline here, so a failure above still leaves
    # the account restored.
