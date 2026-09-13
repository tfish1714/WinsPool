"""tests_e2e/test_login.py — Real login through the signin overlay."""
import pytest


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},   # desktop
    {"width": 390, "height": 844},    # mobile (iPhone 14-class width, per CLAUDE.md's ~390px breakpoint)
])
def test_login_reaches_standings(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    creds = test_player_credentials[1]  # a non-admin test player

    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")

    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")

    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    page.wait_for_url("**/wins-pool/**", timeout=10000)

    assert "wins-pool" in page.url
