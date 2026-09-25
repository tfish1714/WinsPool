"""tests_e2e/test_admin_members_and_recap.py — Member/paid tracking and the
weekly-recap workflow up to its real-Gemini-API boundary (see this plan's
Task 4 for why full recap generation is out of scope for automation)."""
from tests_e2e.helpers import _open_admin_tab
from tests_e2e.test_live_draft import _record_dialogs
from tests_e2e.test_standings import _login


def test_toggle_member_paid_status(live_server, page, test_player_credentials):
    """togglePaid() (admin_main.js) flips `data-paid` optimistically before
    awaiting the real POST, and only reverts + alert()s on failure -- so
    reading the DOM right after a click proves nothing about whether the
    write actually reached the server. Force a real re-fetch through the UI
    (re-selecting the members-tab season, whose `change` listener calls
    loadMembers() -> a fresh GET) and assert against that freshly rendered
    state instead. A dialog recorder is attached so a failure alert (which
    this page has no handler for otherwise, and Playwright would silently
    auto-dismiss) surfaces as a real assertion failure."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    dialogs = _record_dialogs(page)

    page.goto(f"{live_server}/admin")
    _open_admin_tab(page, "members-section")

    page.wait_for_selector(".paid-toggle", timeout=10000)
    season_select = page.locator("#members-season-select")
    season_value = season_select.input_value()

    first_row = page.locator(".members-row").first
    player_id = first_row.get_attribute("data-player-id")
    before = first_row.locator(".paid-toggle").get_attribute("data-paid")

    def _reload_via_season_select():
        # loadMembers() rebuilds #members-rows from scratch, so any earlier
        # row/button handle is stale afterwards -- re-locate by player id.
        season_select.select_option(value=season_value)
        page.wait_for_selector(".paid-toggle", timeout=10000)
        row = page.locator(f'.members-row[data-player-id="{player_id}"]')
        row.wait_for(state="visible", timeout=10000)
        return row.locator(".paid-toggle")

    first_row.locator(".paid-toggle").click()
    page.wait_for_timeout(1000)  # togglePaid()'s awaited POST

    after = _reload_via_season_select().get_attribute("data-paid")
    assert after != before, "paid-toggle change did not persist server-side (re-fetch shows the old value)"

    # Toggle back so this test is idempotent across reruns, again verified
    # through a real re-fetch rather than the optimistic DOM state.
    _reload_via_season_select().click()
    page.wait_for_timeout(1000)

    restored = _reload_via_season_select().get_attribute("data-paid")
    assert restored == before, "paid-toggle restore did not persist server-side (re-fetch shows the toggled value)"

    assert dialogs == [], f"unexpected dialog(s) during paid-toggle test (likely a failure alert): {dialogs}"


def test_recap_prompt_preview_populates(live_server, page, test_player_credentials):
    """Covers the free, deterministic part of the recap workflow: entering a
    year/week and generating the data prompt (pure data extraction, no
    external API call -- see routes/admin_routes.py's preview_recap_prompt,
    which only calls recap_service.extract_weekly_data + ai_service's plain
    string-template get_recap_prompt, not the Gemini API).

    Deliberately does NOT click 'Generate AI Summary' or 'Broadcast' --
    both require real completed game data for the chosen year/week to
    return non-404, and 'Generate' calls the real Gemini API with no
    test-mode gate (see this plan's Task 4)."""
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    _open_admin_tab(page, "recap-section")

    # A real, definitely-complete past week -- avoids the 404 branch for
    # "no game results found yet" that a future/in-progress week would hit.
    page.fill("#recap-year", "2024")
    page.fill("#recap-week", "1")

    page.click("#recap-preview-prompt-btn")
    page.wait_for_selector("#recap-prompt-preview-container:not(.hidden)", timeout=10000)

    prompt_text = page.locator("#recap-prompt-text").input_value()
    assert len(prompt_text) > 0, "recap prompt preview returned empty text"
