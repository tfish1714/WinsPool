"""tests_e2e/test_lockout.py — 5 failed logins trigger a 30-minute lockout."""
import pytest

from tests_e2e.test_account_claim import _restore_account_via_claim


def _attempt_login(page, live_server, email, password):
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", email)
    page.locator("#auth-email").blur()
    # handleEmailBlur (main.js:865-883) resolves has_password and flips both
    # #auth-title to "Sign In" and #auth-submit-btn's text to "Log In" -- wait
    # on the title (a real transition, same pattern as test_mfa.py /
    # test_forced_password_change.py) instead of a fixed timeout, THEN
    # confirm the button actually landed on the real login path before
    # submitting. If this account were ever left passwordless (e.g. by a
    # failed cleanup), the button would read "Setup Account" instead and
    # clicking it would hit /set_password's own, DIFFERENT lockout mechanism
    # (_record_setup_failure's "Too many failed attempts..."), which happens
    # to produce text matching this test's assertions ("locked", "30
    # minutes") -- a false pass exercising the wrong code path entirely.
    page.wait_for_selector("#auth-title:has-text('Sign In')", timeout=5000)
    assert page.locator("#auth-submit-btn").inner_text() == "Log In", (
        "Expected the real login path (button reads 'Log In') but got "
        f"{page.locator('#auth-submit-btn').inner_text()!r} -- the account "
        "may be unexpectedly passwordless, routing through Setup Account "
        "instead of /login."
    )
    page.fill("#auth-password", password)
    page.click("#auth-submit-btn")
    page.wait_for_selector("#auth-error:not(.hidden)", timeout=10000)
    return page.locator("#auth-error").inner_text()


@pytest.fixture
def restore_lockout_account_after(live_server, browser, lifecycle_test_accounts, test_player_credentials):
    """Cleanup: unconditionally restores e2e-test-13-lockout to a fully
    working starting state, regardless of whether the test body raises --
    unlocked, must_change_password=False, a known working password, and
    failed_login_attempts=0.

    Routes through _restore_account_via_claim (shared with
    test_forced_password_change.py's restore_tempword_account_after) rather
    than the admin "Set Temp Password" action: /admin/set_temp_password
    unconditionally sets must_change_password=True with no UI path to clear
    it again, which would permanently strand this account outside the
    normal login flow (main.js has no handler for must_change_password --
    see test_forced_password_change.py's module docstring). Re-claiming
    through the real "Setup Account" flow is what actually clears that flag,
    and the fixture's final real login (inside the helper) also zeroes
    failed_login_attempts -- neither /admin/reset_password nor
    /admin/set_temp_password ever does that; only a successful password
    verification does (routes/auth_routes.py's login()).
    """
    yield
    creds = lifecycle_test_accounts["lockout"]
    _restore_account_via_claim(
        browser, live_server, test_player_credentials,
        creds["id"], creds["email"], creds["password"],
    )


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
