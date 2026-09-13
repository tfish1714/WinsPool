"""tests_e2e/test_nav_parity.py — Desktop nav and mobile drawer must expose the same destinations.

Regression test for the exact bug class CLAUDE.md documents: updateNav()
(static/js/main.js) renders the desktop nav client-side, while the mobile
drawer (templates/base.html's .nav-drawer__links) is separate, hardcoded
markup — a link added to one has silently not appeared in the other before.
"""
import pytest

from tests_e2e.test_standings import _login


def _visible_hrefs(locator) -> set:
    hrefs = set()
    count = locator.count()
    for i in range(count):
        el = locator.nth(i)
        cls = el.get_attribute("class") or ""
        if "hidden" in cls.split() or "admin-hidden" in cls.split():
            continue
        href = el.get_attribute("href")
        if href:
            hrefs.add(href)
    return hrefs


@pytest.mark.parametrize("path", [
    # None = stay on wherever login lands (the /wins-pool/{year} redirect).
    # That page DOES pass current_year into the template context; the others
    # below deliberately do not, which is the whole point of covering them —
    # base.html's drawer falls back to a literal year while updateNav() uses
    # new Date().getFullYear(). Every one of these renders base.html, so the
    # drawer markup is present on all of them.
    None,
    "/draft",             # live draft room — highest-traffic page during a draft
    "/profile",           # player-facing, no admin role needed
    "/admin",
    "/admin/predictions",
])
def test_desktop_and_mobile_nav_expose_the_same_destinations(
    live_server, page, test_player_credentials, path
):
    admin_creds = test_player_credentials[0]  # role=admin, to exercise the largest link set

    page.set_viewport_size({"width": 1280, "height": 800})
    _login(page, live_server, admin_creds)
    if path is not None:
        page.goto(f"{live_server}{path}")
    page.wait_for_selector("#nav-primary-links a", timeout=10000)

    desktop_hrefs = _visible_hrefs(page.locator("#nav-primary-links a"))
    desktop_hrefs |= _visible_hrefs(page.locator("#nav-more-dropdown a"))

    # Resize to mobile and open the drawer (per static/js/responsive.js: the
    # "More" tab in the bottom tab bar opens #nav-drawer).
    page.set_viewport_size({"width": 390, "height": 844})
    page.click("#btb-more-tab")
    page.wait_for_selector("#nav-drawer.open", timeout=5000)

    drawer_hrefs = _visible_hrefs(page.locator(".nav-drawer__links a"))

    missing_from_drawer = desktop_hrefs - drawer_hrefs
    missing_from_desktop = drawer_hrefs - desktop_hrefs

    where = path or "(post-login landing page)"
    assert not missing_from_drawer, (
        f"On {where}: desktop nav has links the mobile drawer is missing: {missing_from_drawer}"
    )
    assert not missing_from_desktop, (
        f"On {where}: mobile drawer has links the desktop nav is missing: {missing_from_desktop}"
    )
