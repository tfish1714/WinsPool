"""tests_e2e/test_admin_dashboard.py — Admin dashboard loads for an admin test account."""
from tests_e2e.test_standings import _login


def test_admin_dashboard_loads(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]  # e2e-test-01, role=admin
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)

    # Players tab (data-tab="player-section") is the default-visible tab on
    # load; #player-grid lives inside the Draft tab's section
    # (id="draft-section", hidden by default) — must switch tabs first.
    page.wait_for_selector("#player-section:not(.hidden)", timeout=10000)
    page.click(".admin-tab-btn[data-tab='draft-section']")
    page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)
    page.wait_for_selector("#player-grid", timeout=10000)

    assert "Internal Server Error" not in page.content()
