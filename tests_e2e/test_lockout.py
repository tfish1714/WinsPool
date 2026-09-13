"""tests_e2e/test_lockout.py — 5 failed logins trigger a 30-minute lockout."""
import pytest

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
    (restores a known password), then a real sign-in with that password to
    zero failed_login_attempts (see comment below) -- all real UI actions."""
    yield
    ctx = browser.new_context()
    admin_page = ctx.new_page()
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    target_id = lifecycle_test_accounts["lockout"]["id"]
    _admin_reset_password(admin_page, live_server, admin_creds, target_id, "E2E Lockout Test")

    # _admin_reset_password leaves admin_page on /admin with the "show test
    # accounts" toggle already checked and the player-management card list
    # already rendered (its own .btn-reset-pw click doesn't re-fetch/re-render
    # the list -- see admin_main.js's resetPassword(), which only alerts). So
    # the card queried here is the same DOM node _admin_reset_password already
    # located; no extra navigation/toggle step is needed.
    card = admin_page.locator(f".player-mgmt-card[data-player-id='{target_id}']")
    card.locator(".btn-temp-pw").click()
    card.locator(".temp-pw-input").fill(lifecycle_test_accounts["lockout"]["password"])
    admin_page.once("dialog", lambda d: d.accept())  # alert(data.message)
    card.locator(".btn-confirm-temppw").click()
    admin_page.wait_for_timeout(1000)
    ctx.close()

    # Neither /admin/reset_password nor /admin/set_temp_password
    # (routes/admin_routes.py) ever clears failed_login_attempts -- only a
    # successful password verification does (reset_fields in
    # routes/auth_routes.py's login(), written on every branch after
    # verify_password succeeds, must_change_password included). Without this,
    # the account is left one wrong password away from an instant re-lock
    # (fails already >=4, so the very next failure re-trips it) instead of
    # requiring 5 fresh attempts -- discovered the hard way when this
    # fixture's earlier (admin-only) version passed once, then made the
    # *next* run fail: attempt 1 already tripped a fresh lockout, so the
    # loop's last (5th) attempt hit the "already locked" branch instead of
    # the fresh-lockout branch, whose message text differs ("Account locked.
    # Try again in N minutes" vs "Too many failed login attempts. Account
    # locked for 30 minutes.").
    # The temp password we just set logs in successfully here -- server-side
    # that's enough to zero the counter -- even though the client shows a
    # generic "Login failed" banner for the (unhandled by main.js)
    # must_change_password status; that's expected and irrelevant to us,
    # we only need the real POST /api/login side effect.
    user_ctx = browser.new_context()
    user_page = user_ctx.new_page()
    _attempt_login(user_page, live_server, lifecycle_test_accounts["lockout"]["email"], lifecycle_test_accounts["lockout"]["password"])
    user_ctx.close()


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
