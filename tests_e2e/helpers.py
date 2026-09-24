"""tests_e2e/helpers.py -- shared Playwright helpers for the e2e suite.

Rule: this module never imports from a test_*.py file (import cycles).
"""


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
