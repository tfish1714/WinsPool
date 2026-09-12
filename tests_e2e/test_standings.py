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

    # The load-bearing 500 detector for THIS route is the status code, not the
    # body. wins_pool_by_year() (routes/standings_routes.py) wraps itself in a
    # try/except returning server_error() — a JSONResponse with status 500 and
    # body {"error": "An internal error occurred."} — so Starlette's default
    # "Internal Server Error" page is never rendered here and the string check
    # below is a no-op for this route. Same reasoning as
    # tests_e2e/test_live_draft.py's mid-draft check (commit bc377c3). We
    # re-navigate explicitly because arriving here via the post-login
    # client-side redirect gives us no Response object to inspect.
    # Do not drop this status assertion.
    response = page.goto(page.url)
    assert response is not None and response.status == 200, (
        f"{page.url} returned HTTP "
        f"{response.status if response else 'no response'}"
    )
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)

    assert page.locator(".app-container").count() > 0
    # Kept as cheap defence in depth; provably blind to this route's own 500s
    # (see comment above), so it is not what catches a failure here.
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
