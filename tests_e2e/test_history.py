"""tests_e2e/test_history.py — All-time history and per-player profile pages.

/history (templates/overall_history.html) lists every player with a
"View →" link to /history/player/{playerId} (templates/player_profile.html).
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_all_time_history_and_player_profile_link(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/history")
    page.wait_for_selector("a[href^='/history/player/']", timeout=10000)
    assert "Internal Server Error" not in page.content()

    first_profile_link = page.locator("a[href^='/history/player/']").first
    first_profile_link.click()

    page.wait_for_url("**/history/player/**", timeout=10000)
    assert "Internal Server Error" not in page.content()
