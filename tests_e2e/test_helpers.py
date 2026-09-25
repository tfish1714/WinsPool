"""tests_e2e/test_helpers.py -- the shared e2e helpers, exercised against the real app."""
import pytest

from tests_e2e.helpers import (
    _assert_no_failure_dialogs,
    _click_and_wait_for_dialogs,
    _login_via_admin,
    _logout,
    _open_admin_tab,
    _record_dialogs,
    _wait_for_config,
)
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


def test_record_dialogs_records_type_and_message(live_server, page):
    seen = _record_dialogs(page)
    page.goto("about:blank")

    page.evaluate("() => { alert('hello'); }")

    assert seen == [("alert", "hello")]


def test_record_dialogs_is_idempotent_per_page(live_server, page):
    first = _record_dialogs(page)
    second = _record_dialogs(page)
    page.goto("about:blank")

    page.evaluate("() => { alert('once'); }")  # a double handler would raise here

    assert first is second
    assert first == [("alert", "once")]


def test_click_and_wait_for_dialogs_returns_only_the_new_dialogs(live_server, page):
    page.goto("about:blank")
    page.set_content(
        "<button id='b' onclick=\"if (confirm('sure?')) alert('done')\">go</button>"
    )
    seen = _record_dialogs(page)
    seen.append(("alert", "earlier"))  # pre-existing entry must not be returned

    fresh = _click_and_wait_for_dialogs(page, seen, page.locator("#b"), count=2)

    assert fresh == [("confirm", "sure?"), ("alert", "done")]


def test_click_and_wait_for_dialogs_fails_when_too_few_arrive(live_server, page):
    page.goto("about:blank")
    page.set_content("<button id='b' onclick=\"alert('only one')\">go</button>")
    seen = _record_dialogs(page)

    with pytest.raises(AssertionError, match="expected 2 dialog"):
        _click_and_wait_for_dialogs(page, seen, page.locator("#b"), count=2, timeout_ms=500)


def test_assert_no_failure_dialogs():
    _assert_no_failure_dialogs([("alert", "Player created!")])
    with pytest.raises(AssertionError):
        _assert_no_failure_dialogs([("alert", "Reset failed: boom")])
    with pytest.raises(AssertionError):
        _assert_no_failure_dialogs([("alert", "Server ERROR")])


def test_logout_clears_the_session_cookie_and_shows_signin(live_server, page, test_player_credentials):
    _login(page, live_server, test_player_credentials[0])
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)

    _logout(page)

    assert page.locator("#signin-screen").is_visible()
    assert not [c for c in page.context.cookies() if c["name"] == "session_token" and c["value"]]
    # The server agrees: a protected call without credentials is rejected.
    status = page.evaluate("() => fetch('/api/admin/players').then(r => r.status)")
    assert status in (401, 403)


def test_login_via_admin_signs_in_and_stays_on_admin(live_server, page, test_player_credentials):
    _login_via_admin(page, live_server, test_player_credentials[0])

    assert not page.locator("#signin-screen").is_visible()
    assert page.evaluate("() => location.pathname").rstrip("/") == "/admin"
    status = page.evaluate("() => fetch('/api/admin/players').then(r => r.status)")
    assert status == 200


def test_wait_for_config_returns_when_the_value_matches(live_server, page):
    page.goto(live_server)
    current = page.evaluate("() => fetch('/api/config/settings').then(r => r.json())")
    key = "draft_active"

    _wait_for_config(page, key, current.get(key) is True, timeout_s=5)


def test_wait_for_config_times_out_with_the_last_seen_value(live_server, page):
    page.goto(live_server)
    current = page.evaluate("() => fetch('/api/config/settings').then(r => r.json())")
    opposite = current.get("draft_active") is not True

    with pytest.raises(AssertionError, match="draft_active") as excinfo:
        _wait_for_config(page, "draft_active", opposite, timeout_s=1)
    assert f"last seen: {current.get('draft_active')!r}" in str(excinfo.value)
