"""tests_e2e/test_standings.py — Standings and schedule page smoke tests."""
import pytest


def _login(page, live_server, creds):
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_standings_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.wait_for_url("**/wins-pool/**", timeout=10000)
    assert page.locator(".app-container").count() > 0
    # No unhandled server error text leaking through Jinja's default error page
    assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_schedule_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/schedule")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    assert "Internal Server Error" not in page.content()
