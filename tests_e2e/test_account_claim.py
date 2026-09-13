"""tests_e2e/test_account_claim.py — First-time account claim (no password set yet)."""
import pytest

from tests_e2e.test_standings import _login


def _admin_reset_password(page, live_server, admin_creds, target_player_id, target_player_name):
    """Restores a test account to password_hash=None via the real Admin
    Portal 'Reset Password' button (routes/admin_routes.py:255's
    /admin/reset_password — clears password_hash, lockout_until, and
    failed_setup_attempts). Requires the admin to already be logged in
    on `page`."""
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
    page.click(".admin-tabs .tab-btn[data-tab='draft-section']")
    page.check("#show-test-accounts-toggle")
    page.click(".admin-tabs .tab-btn[data-tab='player-section']")
    page.wait_for_selector(f".player-mgmt-card[data-player-id='{target_player_id}']", timeout=10000)
    card = page.locator(f".player-mgmt-card[data-player-id='{target_player_id}']")
    page.once("dialog", lambda d: d.accept())  # confirm("Reset password for ...?")
    page.once("dialog", lambda d: d.accept())  # alert(data.message)
    card.locator(".btn-reset-pw").click()
    page.wait_for_timeout(1000)


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
    admin_creds = _login_admin(admin_page, live_server, test_player_credentials)
    _admin_reset_password(
        admin_page, live_server, admin_creds,
        lifecycle_test_accounts["claim"]["id"], "E2E Claim Test",
    )
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
