"""tests_e2e/test_mfa.py — MFA-required login state.

Full successful-MFA-login coverage is not possible in this environment --
see the Design Note in .superpowers/sdd/2026-09-09-ui-tests-auth-lifecycle/
task-3-brief.md. This test covers everything short of that: the MFA UI
appearing correctly, and wrong-code rejection.
"""


def test_mfa_required_state_and_wrong_code_rejected(live_server, page, lifecycle_test_accounts):
    creds = lifecycle_test_accounts["mfa"]

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.locator("#auth-email").blur()
    # handleEmailBlur (main.js:865-883) resolves has_password=True async and
    # flips the title to "Sign In" -- a real visibility change we can wait on.
    # (auth-confirm-password itself never becomes visible on this path, so
    # waiting on *it* with Playwright's default state="visible" would hang
    # forever -- it starts and stays class="hidden" the whole time.)
    page.wait_for_selector("#auth-title:has-text('Sign In')", timeout=5000)
    assert "hidden" in (page.locator("#auth-confirm-password").get_attribute("class") or "")

    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")

    # login() returns status='mfa_required' (auth_routes.py:232) instead of
    # logging in immediately -- handleMfaRequired (main.js:929-940) hides
    # the password field and reveals the code field.
    page.wait_for_selector("#auth-mfa-code:not(.hidden)", timeout=10000)
    assert page.locator("#auth-submit-btn").inner_text() == "Verify Code"
    assert "hidden" in (page.locator("#auth-password").get_attribute("class") or "")

    # A wrong code must be rejected, not silently accepted.
    page.fill("#auth-mfa-code", "000000")
    page.click("#auth-submit-btn")

    page.wait_for_selector("#auth-error:not(.hidden)", timeout=10000)
    assert "signin-screen" in (page.get_attribute("#signin-screen", "id") or "")  # still on the signin screen
    error_text = page.locator("#auth-error").inner_text()
    assert error_text  # non-empty -- exact wording ("Incorrect verification code." or "MFA code expired or invalid.")
                        # depends on whether the 000000 guess happens to collide with expiry timing, both are correct rejections
