"""tests_e2e/test_helpers.py -- the shared e2e helpers, exercised against the real app."""
import pytest

from tests_e2e.helpers import _open_admin_tab
from tests_e2e.test_standings import _login


def _admin_page(live_server, page, test_player_credentials):
    _login(page, live_server, test_player_credentials[0])
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)


@pytest.mark.parametrize("tab_id", ["draft-section", "members-section", "player-section"])
def test_open_admin_tab_reveals_the_section(live_server, page, test_player_credentials, tab_id):
    _admin_page(live_server, page, test_player_credentials)

    _open_admin_tab(page, tab_id)

    assert page.locator(f"#{tab_id}").is_visible()


def test_open_admin_tab_switches_between_tabs(live_server, page, test_player_credentials):
    _admin_page(live_server, page, test_player_credentials)

    _open_admin_tab(page, "members-section")
    _open_admin_tab(page, "draft-section")

    assert page.locator("#draft-section").is_visible()
    assert not page.locator("#members-section").is_visible()
