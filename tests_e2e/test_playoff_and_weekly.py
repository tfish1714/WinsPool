"""tests_e2e/test_playoff_and_weekly.py — Playoff race and weekly-progress pages.

Both follow the same redirect-to-active-season + #year-pick select pattern
as standings/schedule/head-to-head (templates/playoff_race.html,
templates/weekbyweek.html).
"""
import pytest
from playwright.sync_api import Error as PlaywrightError
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_loads_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/playoff-race")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "/playoff-race/" in page.url
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    if len(options) > 1:
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/playoff-race/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_weekly_progress_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    # Reached via the standings page's "Weekly Progress" nav link, not a direct
    # goto, so the real nav wiring (static/js/main.js's moreLinks array) is exercised.
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    if viewport["width"] >= 1280:
        page.wait_for_selector("#nav-more-btn", timeout=10000)
        page.click("#nav-more-btn")
        page.click("#nav-more-dropdown >> text=Weekly Progress")
    else:
        page.wait_for_selector("#btb-more-tab", timeout=10000)
        page.click("#btb-more-tab")
        page.click(".nav-drawer__links >> text=Weekly Progress")

    page.wait_for_url("**/weekbyweek", timeout=10000)
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "Internal Server Error" not in page.content()


# ---------------------------------------------------------------------------
# Playoff Race nav gating (static/js/nav_gating.js, wired in static/js/main.js
# from /api/config/settings latest_week). The stub keeps these independent of
# whatever week the local data set is currently on.
# ---------------------------------------------------------------------------
def _stub_latest_week(page, week):
    def handle(route):
        # The page can close while a poll is in flight (teardown); a failed
        # fetch/fulfill then is not a test failure.
        try:
            resp = route.fetch()
            body = resp.json()
            body["latest_week"] = week
            route.fulfill(response=resp, json=body)
        except PlaywrightError:
            pass
    page.route("**/api/config/settings", handle)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_link_hidden_before_week_10(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _stub_latest_week(page, 9)
    _login(page, live_server, test_player_credentials[1])
    page.wait_for_url("**/wins-pool/**", timeout=10000)
    if viewport["width"] >= 1024:
        # The desktop nav is rendered client-side by updateNav(); wait for it
        # so a count of 0 means "gated out", not "not rendered yet".
        page.wait_for_selector("#nav-primary-links a", timeout=10000)
        assert page.locator("#nav-primary-links a[href='/playoff-race']").count() == 0
    else:
        page.wait_for_selector("#btb-more-tab", timeout=10000)
        assert page.locator("#btb-playoff-tab").is_hidden()
        page.click("#btb-more-tab")
        page.wait_for_selector("#nav-drawer.open", timeout=5000)
        assert page.locator("#drawer-playoff-race-link").is_hidden()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_playoff_race_link_visible_from_week_10(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _stub_latest_week(page, 10)
    _login(page, live_server, test_player_credentials[1])
    page.wait_for_url("**/wins-pool/**", timeout=10000)
    if viewport["width"] >= 1024:
        page.wait_for_selector("#nav-primary-links a[href='/playoff-race']", timeout=10000)
    else:
        page.wait_for_selector("#btb-playoff-tab:not(.hidden)", timeout=10000)
        page.click("#btb-more-tab")
        page.wait_for_selector("#nav-drawer.open", timeout=5000)
        assert page.locator("#drawer-playoff-race-link").is_visible()


def test_playoff_race_direct_url_reachable_when_link_hidden(live_server, page, test_player_credentials):
    page.set_viewport_size({"width": 1280, "height": 800})
    _stub_latest_week(page, 9)
    _login(page, live_server, test_player_credentials[1])
    page.wait_for_selector("#nav-primary-links a", timeout=10000)
    assert page.locator("#nav-primary-links a[href='/playoff-race']").count() == 0

    # Gating is nav-only: the route itself must stay reachable. /playoff-race
    # redirects to the active season, and goto follows it to the final 200.
    response = page.goto(f"{live_server}/playoff-race")
    assert response is not None and response.status == 200, (
        f"{page.url} returned HTTP {response.status if response else 'no response'}"
    )
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "/playoff-race/" in page.url
