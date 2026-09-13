"""tests_e2e/test_headtohead.py — Head-to-head matchup pages.

Covers /headtohead (redirects to the active season), the /headtohead/{year}
year-picker (templates/headtohead.html's #year-pick select), and
/headtohead/history (the combined all-years view, templates/headtohead_history.html).
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_headtohead_redirects_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/headtohead")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "/headtohead/" in page.url
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    assert len(options) >= 1, "Expected at least one season in the head-to-head year picker"

    if len(options) > 1:
        # Navigate to a different year via the real select control, not a direct URL.
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/headtohead/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_headtohead_history_link_and_page(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/headtohead")
    page.wait_for_selector("#year-pick", timeout=10000)

    page.click("text=View full head-to-head history across all")
    page.wait_for_url("**/headtohead/history", timeout=10000)
    assert "Internal Server Error" not in page.content()
