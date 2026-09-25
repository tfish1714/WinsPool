"""tests_e2e/helpers.py -- shared Playwright helpers for the e2e suite.

Rule: this module never imports from a test_*.py file (import cycles).
"""
import time


def _wait_for_config(page, key, want, timeout_s=15):
    """Poll GET /api/config/settings until `config[key] is want`. Verifying
    through a fresh GET proves the server PERSISTED the change, which a toggle's
    optimistic aria-pressed flip or a POST-response listener does not (and the
    listener form needed a retry loop because it can miss the response event).
    """
    deadline = time.time() + timeout_s
    last = None
    last_error = None
    while time.time() < deadline:
        try:
            last = page.evaluate(
                "() => fetch('/api/config/settings').then(r => r.json())"
            ).get(key)
            if last is want:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.4)
    raise AssertionError(
        f"config {key!r} never became {want!r} "
        f"(last seen: {last!r}, last error: {last_error!r})"
    )


def _login_via_admin(page, live_server, creds):
    """Sign in through the /admin page's own signin overlay and stay on /admin.

    Unlike test_standings._login (which opens `/`, redirecting to the newest
    season's standings page), this never touches the site root: once season
    3000 is fully drafted that page can return HTTP 500 and has no signin
    overlay, which would make fixture cleanup impossible after a leak.
    """
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="visible", timeout=15000)
    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)


def _open_admin_tab(page, tab_id):
    """Click the /admin dashboard tab whose `data-tab` is `tab_id` (for example
    "draft-section" or "members-section") and wait until its section is shown.

    Precondition: `page` is already on /admin and signed in. This is the one
    canonical tab-click for the whole suite: the tab buttons carry both the
    `tab-btn` and `admin-tab-btn` classes and `data-tab` is unique on /admin,
    so anchoring on `.admin-tabs .tab-btn[data-tab=...]` keeps every test
    coupled to a single selector instead of several drifting ones.
    """
    selector = f'.admin-tabs .tab-btn[data-tab="{tab_id}"]'
    page.wait_for_selector(selector, timeout=10000)
    page.click(selector)
    page.wait_for_selector(f"#{tab_id}:not(.hidden)", timeout=10000)


def _record_dialogs(page):
    """Accept every native dialog while keeping a record of what it said.

    Both the Admin Portal (confirm() before generate/wipe, alert() with the
    result) and the draft room (alert() on a WebSocket `error` message, e.g.
    "It is not your turn to pick!") use native dialogs. Playwright
    auto-dismisses unhandled ones, which would silently hide a rejected pick
    or a failed admin action, so every page gets a recorder and the test
    asserts on its contents.
    """
    existing = getattr(page, "_e2e_dialog_log", None)
    if existing is not None:
        return existing  # one handler per page: a second would double-accept
    seen = []

    def _handle(dialog):
        seen.append((dialog.type, dialog.message))
        dialog.accept()

    page.on("dialog", _handle)
    page._e2e_dialog_log = seen
    return seen


def _click_and_wait_for_dialogs(page, dialogs, locator, count, timeout_ms=8000):
    """Click `locator` and wait until `count` NEW dialogs have been recorded in
    `dialogs` (a list returned by _record_dialogs). Polls rather than sleeping
    a fixed time: the alert() in these handlers only fires after an awaited
    backend call, so a fixed wait races it under load. Returns only the dialogs
    recorded by this click, and asserts there were exactly `count` of them.
    """
    start = len(dialogs)
    locator.click()
    waited = 0
    while len(dialogs) - start < count and waited < timeout_ms:
        page.wait_for_timeout(100)
        waited += 100
    fresh = dialogs[start:]
    assert len(fresh) == count, f"expected {count} dialog(s), saw {len(fresh)}: {fresh}"
    return fresh


def _assert_no_failure_dialogs(dialogs):
    """Fail if any recorded dialog message reads like a failure. The admin UI
    reports failures as alert('... failed: ...') / alert('Failed: ...'), which
    Playwright would otherwise auto-dismiss without a trace."""
    bad = [(kind, msg) for kind, msg in dialogs if "fail" in msg.lower() or "error" in msg.lower()]
    assert not bad, f"unexpected failure dialog(s): {bad}"


def _logout(page):
    """Sign out through the real UI (desktop layout): open the avatar popover,
    click Logout, and wait for the signin overlay. The handler POSTs
    /api/logout (clearing the httpOnly session_token cookie), clears
    localStorage credentials, and reloads -- unlike wiping localStorage
    from the test, which leaves the cookie claiming the old session.
    """
    page.click("#nav-avatar-btn")
    page.click("#nav-ap-logout-btn")
    page.wait_for_selector("#signin-screen", state="visible", timeout=10000)
