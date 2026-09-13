"""tests_e2e/test_admin_tabs.py — Admin dashboard tab navigation smoke tests.

Covers the read-heavy dashboards (Elo Ratings Explorer, ML Accuracy,
Consensus, Betting) at smoke-test depth per explicit scoping decision:
tab loads, its content becomes visible, no server-error text appears.
Deep interaction with these tools' filters/charts is out of scope.
"""
import pytest
from tests_e2e.test_standings import _login

TABS = [
    "draft-section",
    "members-section",
    "recap-section",
    "elo-section",
    "accuracy-section",
    "consensus-section",
    "betting-section",
]


@pytest.mark.parametrize("tab_id", TABS)
def test_admin_tab_loads(live_server, page, test_player_credentials, tab_id):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector(f'.admin-tabs .tab-btn[data-tab="{tab_id}"]', timeout=10000)
    page.click(f'.admin-tabs .tab-btn[data-tab="{tab_id}"]')

    page.wait_for_selector(f"#{tab_id}:not(.hidden)", timeout=10000)
    assert "Internal Server Error" not in page.content()


def test_admin_player_management_tab_is_default_visible(live_server, page, test_player_credentials):
    """player-section has no `hidden` class in templates/admin.html -- it's
    the tab shown on first load, with no click required."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)
    assert "Internal Server Error" not in page.content()


def test_admin_predictions_debug_page_loads(live_server, page, test_player_credentials):
    """Separate page route (routes/admin_routes.py's admin_predictions_page,
    templates/admin_predictions.html) -- not one of the 8 dashboard tabs."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin/predictions")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    assert "Internal Server Error" not in page.content()
