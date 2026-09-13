"""tests_e2e/test_forced_password_change.py — Admin sets a temp password;
target account must be blocked from a normal login afterward.

Re-verified during implementation (2026-09-13), not just carried over from
planning: `grep -n "must_change_password" static/js/*.js` still shows it
referenced only in admin_main.js (the "Temp Password" status pill in the
admin player list), never in the login flow. static/js/main.js's
handleLogin (main.js:885-925) branches only on data.status === 'success'
(912) and 'mfa_required' (915); 'must_change_password' falls through to the
generic `else { this.showAuthError(data.error || 'Login failed') }` (918),
and routes/auth_routes.py's must_change_password branch (213-219) never sets
`error` in its response body, so the account is stuck showing the generic
"Login failed" text with no route into a password-change form. The backend
supports forced password changes (routes/admin_routes.py's
/admin/set_temp_password + the must_change_password check in
routes/auth_routes.py's /login); the frontend still has no way to complete
one. This test only covers the backend contract (blocked normal login), not
a full "user completes the forced change" UI flow, since that UI flow
doesn't exist to test.
"""
import pytest
from tests_e2e.test_account_claim import _admin_reset_password, _login_admin

TEMP_PASSWORD = "E2eForcedTemp!7"


@pytest.fixture
def restore_tempword_account_after(live_server, browser, lifecycle_test_accounts, test_player_credentials):
    """Ensures e2e-test-14-tempword always ends the test back in a normal,
    known-password, must_change_password=False state, regardless of whether
    the test body raises.

    Deviates from the original plan snippet on purpose: that snippet
    restored by re-using the admin "Set Temp Password" button (.btn-temp-pw
    / .temp-pw-input / .btn-confirm-temppw) with the account's real
    password. That would NOT actually restore a working account --
    routes/admin_routes.py's /admin/set_temp_password unconditionally sets
    must_change_password=True (line 296), and routes/admin_routes.py's
    /admin/reset_password (used first, via _admin_reset_password) never
    clears that field -- it only clears password_hash / failed_setup_attempts
    / lockout_until / mfa_secret / mfa_enabled. Given the confirmed main.js
    gap above (no frontend handler for must_change_password), re-setting it
    to True during cleanup would permanently strand the account: unable to
    log in through the normal UI ever again, including for the next run of
    this very test.

    The actual fix: restore through the real account-claim flow
    (POST /set_password via the signin screen's "Setup Account" path
    instead of the admin temp-password button). auth_routes.py's
    /set_password explicitly sets must_change_password=False on success
    (line 151), and it requires password_hash to be falsy first (line 126)
    -- which is exactly what _admin_reset_password produces. This exactly
    mirrors the account's original seeded state
    (scripts/seed_e2e_test_players.py creates e2e-test-14-tempword WITH a
    password_hash and must_change_password left unset/False).
    """
    yield
    creds = lifecycle_test_accounts["tempword"]

    # Step 1: clear password_hash via the real Admin Portal "Reset Password"
    # flow so the account becomes claimable again.
    admin_ctx = browser.new_context()
    admin_page = admin_ctx.new_page()
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    _admin_reset_password(admin_page, live_server, admin_creds, creds["id"], "E2E Tempword Test")
    admin_ctx.close()

    # Step 2: re-claim the account through the real signin-screen "Setup
    # Account" flow with the known shared password. This is what actually
    # clears must_change_password (unlike the admin temp-password button --
    # see the docstring above).
    claim_ctx = browser.new_context()
    claim_page = claim_ctx.new_page()
    claim_page.goto(live_server)
    claim_page.wait_for_selector("#signin-screen", state="visible")
    claim_page.fill("#auth-email", creds["email"])
    claim_page.locator("#auth-email").blur()
    claim_page.wait_for_selector("#auth-confirm-password:not(.hidden)", timeout=5000)
    claim_page.fill("#auth-password", creds["password"])
    claim_page.fill("#auth-confirm-password", creds["password"])
    claim_page.wait_for_selector("#pw-match-hint.match", timeout=5000)
    claim_page.click("#auth-submit-btn")
    claim_page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    claim_ctx.close()

    # Step 3: independently verify a completely fresh normal login now
    # succeeds. This is the invariant that actually matters given the
    # stuck-account risk -- don't just trust that Step 2 didn't throw.
    verify_ctx = browser.new_context()
    verify_page = verify_ctx.new_page()
    verify_page.goto(live_server)
    verify_page.wait_for_selector("#signin-screen", state="visible")
    verify_page.fill("#auth-email", creds["email"])
    verify_page.locator("#auth-email").blur()
    # #auth-confirm-password carries the "hidden" class before any email is
    # even typed, so waiting on ".hidden" with Playwright's default
    # state="visible" times out -- there's no visible->hidden transition to
    # observe. #auth-submit-btn's default markup text is already "Log In"
    # (templates/base.html:73), so waiting on that text is a no-op that
    # resolves immediately, before handleEmailBlur's fetch has any chance to
    # complete -- no real synchronization. #auth-title, by contrast, only
    # flips to "Sign In" once handleEmailBlur (main.js:869) resolves with
    # has_password=True, which is a real transition -- wait on that instead
    # (same pattern as tests_e2e/test_mfa.py).
    verify_page.wait_for_selector("#auth-title:has-text('Sign In')", timeout=5000)
    verify_page.fill("#auth-password", creds["password"])
    verify_page.click("#auth-submit-btn")
    verify_page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    verify_ctx.close()


def _check_show_test_accounts(page):
    """Reveals is_test_account=True players in the admin player-management
    list. routes/admin_routes.py's fetch_admin_players excludes them unless
    include_test_accounts=true is passed, and admin_main.js only does that
    when #show-test-accounts-toggle is checked. That checkbox lives on the
    "Draft" tab (hidden behind the default-active "Players" tab), so this
    switches tabs to reach it and switches back afterward -- same dance as
    tests_e2e/test_account_claim.py's _admin_reset_password, needed again
    here because a fresh `page.goto("/admin")` re-initializes admin_main.js
    with the toggle unchecked; it isn't carried over from a prior visit.
    """
    page.click(".admin-tabs .tab-btn[data-tab='draft-section']")
    page.check("#show-test-accounts-toggle")
    page.click(".admin-tabs .tab-btn[data-tab='player-section']")


def test_admin_set_temp_password_blocks_normal_login(live_server, page, browser, lifecycle_test_accounts, test_player_credentials, restore_tempword_account_after):
    creds = lifecycle_test_accounts["tempword"]

    admin_page = page
    _login_admin(admin_page, live_server, test_player_credentials)
    admin_page.goto(f"{live_server}/admin")
    _check_show_test_accounts(admin_page)
    selector = f".player-mgmt-card[data-player-id='{creds['id']}']"
    admin_page.wait_for_selector(selector, timeout=10000)
    card = admin_page.locator(selector)
    card.locator(".btn-temp-pw").click()
    card.locator(".temp-pw-input").fill(TEMP_PASSWORD)
    admin_page.once("dialog", lambda d: d.accept())  # alert(data.message) on success
    card.locator(".btn-confirm-temppw").click()
    admin_page.wait_for_timeout(1000)

    # admin_main.js's setTempPassword() doesn't re-render the card after a
    # successful call (static/js/admin_main.js:419-434) -- it just hides the
    # temp-pw panel -- so the "Temp Password" status pill on
    # .player-mgmt-display still reflects the data fetched before this
    # change. Reload and redo the toggle dance to confirm the backend really
    # flipped must_change_password before trusting the blocked-login
    # assertion below.
    admin_page.goto(f"{live_server}/admin")
    _check_show_test_accounts(admin_page)
    admin_page.wait_for_selector(selector, timeout=10000)
    card = admin_page.locator(selector)
    assert "Temp Password" in card.locator(".player-mgmt-display").inner_text()

    # Log in as the target with the temp password -- must NOT reach the app.
    target_ctx = browser.new_context()
    target_page = target_ctx.new_page()
    target_page.goto(live_server)
    target_page.wait_for_selector("#signin-screen", state="visible")
    target_page.fill("#auth-email", creds["email"])
    target_page.locator("#auth-email").blur()
    # See the matching comment in restore_tempword_account_after -- wait on
    # #auth-title flipping to "Sign In" (has_password=True), not on
    # #auth-submit-btn's text: its default markup text is already "Log In"
    # (templates/base.html:73), so a wait on that text resolves immediately
    # and doesn't actually synchronize on the blur/check_player fetch at all.
    target_page.wait_for_selector("#auth-title:has-text('Sign In')", timeout=5000)
    target_page.fill("#auth-password", TEMP_PASSWORD)
    target_page.click("#auth-submit-btn")

    target_page.wait_for_timeout(2000)
    assert target_page.locator("#signin-screen").is_visible(), (
        "Backend returned must_change_password, but the signin screen is gone -- "
        "either the frontend has an undocumented handler for this status, or "
        "something is silently treating it as success. Investigate before trusting this test."
    )
    target_ctx.close()
