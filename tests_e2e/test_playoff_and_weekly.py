"""tests_e2e/test_playoff_and_weekly.py — Playoff race and weekly-progress pages.

Both follow the same redirect-to-active-season + #year-pick select pattern
as standings/schedule/head-to-head (templates/playoff_race.html,
templates/weekbyweek.html).
"""
import pytest
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
