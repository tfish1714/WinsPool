"""tests_e2e/test_draft_history.py — /draft/history's player/team view toggle.

templates/draft_history.html has two client-side-toggled views driven by
switchView('player'|'team') — #btn-player-view/#btn-team-view buttons,
#player-view/#team-view containers.
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_draft_history_view_toggle(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/draft/history")
    page.wait_for_selector("#btn-player-view", timeout=10000)
    assert "Internal Server Error" not in page.content()

    # Defaults to player view.
    assert "active" in (page.locator("#btn-player-view").get_attribute("class") or "")
    assert page.locator("#player-view").is_visible()

    page.click("#btn-team-view")
    page.wait_for_function(
        "document.getElementById('team-view').style.display !== 'none'", timeout=5000
    )
    assert "active" in (page.locator("#btn-team-view").get_attribute("class") or "")
    assert page.locator("#team-view").is_visible()

    page.click("#btn-player-view")
    page.wait_for_function(
        "document.getElementById('player-view').style.display !== 'none'", timeout=5000
    )
    assert page.locator("#player-view").is_visible()
