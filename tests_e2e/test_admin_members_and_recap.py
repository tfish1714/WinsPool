"""tests_e2e/test_admin_members_and_recap.py — Member/paid tracking and the
weekly-recap workflow up to its real-Gemini-API boundary (see this plan's
Task 4 for why full recap generation is out of scope for automation)."""
from tests_e2e.test_standings import _login


def test_toggle_member_paid_status(live_server, page, test_player_credentials):
    admin_creds = test_player_credentials[0]
    _login(page, live_server, admin_creds)

    page.goto(f"{live_server}/admin")
    page.click('.admin-tabs .tab-btn[data-tab="members-section"]')
    page.wait_for_selector("#members-section:not(.hidden)", timeout=10000)

    page.wait_for_selector(".paid-toggle", timeout=10000)
    first_toggle = page.locator(".paid-toggle").first
    before = first_toggle.get_attribute("data-paid")

    first_toggle.click()
    page.wait_for_timeout(1000)  # togglePaid() is async; no dialog, just a POST

    after = first_toggle.get_attribute("data-paid")
    assert after != before, "paid-toggle's data-paid attribute did not flip"

    # Toggle back so this test is idempotent across reruns.
    first_toggle.click()
    page.wait_for_timeout(1000)
    assert first_toggle.get_attribute("data-paid") == before


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
    page.click('.admin-tabs .tab-btn[data-tab="recap-section"]')
    page.wait_for_selector("#recap-section:not(.hidden)", timeout=10000)

    # A real, definitely-complete past week -- avoids the 404 branch for
    # "no game results found yet" that a future/in-progress week would hit.
    page.fill("#recap-year", "2024")
    page.fill("#recap-week", "1")

    page.click("#recap-preview-prompt-btn")
    page.wait_for_selector("#recap-prompt-preview-container:not(.hidden)", timeout=10000)

    prompt_text = page.locator("#recap-prompt-text").input_value()
    assert len(prompt_text) > 0, "recap prompt preview returned empty text"
