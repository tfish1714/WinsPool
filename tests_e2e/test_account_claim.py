"""tests_e2e/test_account_claim.py — First-time account claim (no password set yet).

Also the shared-helpers module for the other lifecycle test files
(test_lockout.py, test_forced_password_change.py import from here).
"""
import pytest

from tests_e2e.helpers import _open_admin_tab
from tests_e2e.test_standings import _login


def _fetch_admin_player_state(page, live_server, target_player_id):
    """Fetches the live player record via the exact same endpoint the admin
    page itself uses to render the player-management list (GET
    /api/admin/players?include_test_accounts=true — static/js/api.js's
    fetchPlayers()), reusing `page`'s existing session cookie. Returns the
    dict with has_password / must_change_password / etc.

    Used instead of parsing the rendered status-badge text: admin_main.js's
    renderPlayerList() picks the badge with must_change_password checked
    FIRST (`if (p.must_change_password) -> 'Temp Password'`), so an account
    whose password_hash was genuinely just cleared but which still carries a
    stale must_change_password=True from an earlier admin action (exactly
    what /admin/reset_password itself deliberately leaves untouched — see
    _admin_reset_password's docstring) keeps showing 'Temp Password', not
    'No Password'. Confirmed empirically: e2e-test-13-lockout and
    e2e-test-14-tempword were both found already stuck in exactly this state
    from an earlier, less-verified version of these fixtures, and a
    badge-text assertion of 'No Password' here would wrongly fail forever on
    such an account even after a fully successful reset.
    """
    resp = page.request.get(f"{live_server}/api/admin/players?include_test_accounts=true")
    assert resp.ok, f"GET /api/admin/players failed: HTTP {resp.status}"
    for p in resp.json():
        if int(p.get("playerId", -1)) == int(target_player_id):
            return p
    raise AssertionError(f"Player {target_player_id} not found in /api/admin/players response")


def _admin_reset_password(page, live_server, target_player_id):
    """Restores a test account to password_hash=None via the real Admin
    Portal 'Reset Password' button (routes/admin_routes.py:269's
    /admin/reset_password — clears password_hash, lockout_until, and
    failed_setup_attempts; deliberately does NOT touch must_change_password).
    Requires the admin to already be logged in on `page`.

    Verifies the reset actually took effect by re-fetching the live player
    record afterward (_fetch_admin_player_state) and asserting
    has_password is False, rather than trusting the alert dialog alone —
    admin_main.js's resetPlayerPassword() alerts on both success
    (`alert(data.message)`) AND failure (`alert('Reset failed: ' +
    e.message)`), so a dialog handler that blindly accepts cannot tell the
    two apart.
    """
    page.goto(f"{live_server}/admin")
    # The lifecycle test accounts are all is_test_account=True, and
    # /api/admin/players excludes test accounts by default
    # (routes/admin_routes.py's fetch_admin_players) — admin_main.js's
    # fetchInitialData() only passes include_test_accounts=true when this
    # checkbox is checked, and re-fetches (both the player-selection grid and
    # this player-management card list) on its change event. The checkbox
    # itself lives in the "Draft" tab (#draft-section), which is hidden
    # (class="hidden") behind the default-active "Players" tab
    # (#player-section, which holds #admin-player-list) — admin_main.js's
    # setupTabHandlers() hides every .tab-content and shows only the clicked
    # one, so the toggle isn't clickable until that tab is selected, and we
    # have to switch back to "Players" afterward to see the re-rendered card.
    _open_admin_tab(page, "draft-section")
    page.check("#show-test-accounts-toggle")
    _open_admin_tab(page, "player-section")
    page.wait_for_selector(f".player-mgmt-card[data-player-id='{target_player_id}']", timeout=10000)
    card = page.locator(f".player-mgmt-card[data-player-id='{target_player_id}']")

    def _accept(dialog):
        dialog.accept()

    # A single persistent listener handles BOTH sequential dialogs the click
    # produces (confirm("Reset password for ...?") then alert(data.message))
    # — two page.once() registrations before one click raised "Dialog is
    # already handled" on the second dialog (harmless/swallowed before, but
    # now shared by 3 test files via import, so worth doing cleanly).
    page.on("dialog", _accept)
    card.locator(".btn-reset-pw").click()
    page.wait_for_timeout(500)
    page.remove_listener("dialog", _accept)

    state = _fetch_admin_player_state(page, live_server, target_player_id)
    assert state["has_password"] is False, (
        f"Expected has_password=False for player {target_player_id} after "
        f"Reset Password, got {state}"
    )


def _restore_account_via_claim(browser, live_server, test_player_credentials, target_id, email, password):
    """Restores a test account to a known-good, FULLY claimed state via real
    UI actions: admin Reset Password, then a real account-claim (POST
    /set_password via the signin screen's "Setup Account" path), then an
    independent fresh login to verify the account actually works afterward.

    This is the only combination that reaches must_change_password=False
    together with a known password: /admin/set_temp_password
    (routes/admin_routes.py) unconditionally sets must_change_password=True
    with no UI path to ever clear it again, while /set_password (driven by
    the "Setup Account" flow) explicitly sets must_change_password=False on
    success and requires password_hash to be falsy first — exactly what the
    Reset Password step produces. The final login (Step 3) also has the
    side effect of zeroing failed_login_attempts, since only a successful
    password verification does that (routes/auth_routes.py's login()).

    Shared by test_lockout.py's restore_lockout_account_after and
    test_forced_password_change.py's restore_tempword_account_after — both
    fixtures need to fully restore an account touched by an admin-side
    password action, and duplicating this sequence in each file previously
    caused test_lockout.py's cleanup to miss the must_change_password reset
    entirely.
    """
    # Step 1: clear password_hash via the real Admin Portal "Reset Password" flow.
    admin_ctx = browser.new_context()
    admin_page = admin_ctx.new_page()
    _login_admin(admin_page, live_server, test_player_credentials)
    _admin_reset_password(admin_page, live_server, target_id)
    admin_ctx.close()

    # Step 2: re-claim through the real signin-screen "Setup Account" flow
    # with the known shared password.
    claim_ctx = browser.new_context()
    claim_page = claim_ctx.new_page()
    claim_page.goto(live_server)
    claim_page.wait_for_selector("#signin-screen", state="visible")
    claim_page.fill("#auth-email", email)
    claim_page.locator("#auth-email").blur()
    claim_page.wait_for_selector("#auth-confirm-password:not(.hidden)", timeout=5000)
    claim_page.fill("#auth-password", password)
    claim_page.fill("#auth-confirm-password", password)
    claim_page.wait_for_selector("#pw-match-hint.match", timeout=5000)
    claim_page.click("#auth-submit-btn")
    claim_page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    claim_ctx.close()

    # Step 3: independently verify a completely fresh normal login now
    # succeeds. This is the invariant that actually matters given the
    # stuck-account risk — don't just trust that Step 2 didn't throw.
    verify_ctx = browser.new_context()
    verify_page = verify_ctx.new_page()
    verify_page.goto(live_server)
    verify_page.wait_for_selector("#signin-screen", state="visible")
    verify_page.fill("#auth-email", email)
    verify_page.locator("#auth-email").blur()
    # #auth-title only flips to "Sign In" once handleEmailBlur (main.js:869)
    # resolves with has_password=True — a real transition worth waiting on,
    # unlike #auth-submit-btn's default markup text ("Log In" already,
    # templates/base.html:73) which would resolve immediately with no real
    # synchronization.
    verify_page.wait_for_selector("#auth-title:has-text('Sign In')", timeout=5000)
    verify_page.fill("#auth-password", password)
    verify_page.click("#auth-submit-btn")
    verify_page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    verify_ctx.close()

    # Step 4: confirm the server-side state actually matches "restored to
    # starting condition" -- has_password=True and, crucially,
    # must_change_password=False (Step 2's whole point; a successful login
    # alone doesn't prove this since main.js has no handler for
    # must_change_password and would show the same "signin-screen hidden"
    # outcome either way -- see the module docstrings referencing this gap).
    # failed_login_attempts=0 and unlocked are covered by Step 3's login
    # having succeeded at all (a locked or attempt-limited account would
    # have rejected it).
    admin_verify_ctx = browser.new_context()
    admin_verify_page = admin_verify_ctx.new_page()
    _login_admin(admin_verify_page, live_server, test_player_credentials)
    final_state = _fetch_admin_player_state(admin_verify_page, live_server, target_id)
    admin_verify_ctx.close()
    assert final_state["has_password"] is True, (
        f"Expected has_password=True for player {target_id} after re-claim, got {final_state}"
    )
    assert final_state["must_change_password"] is False, (
        f"Expected must_change_password=False for player {target_id} after re-claim, got {final_state}"
    )


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
    _login_admin(admin_page, live_server, test_player_credentials)
    _admin_reset_password(admin_page, live_server, lifecycle_test_accounts["claim"]["id"])
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
