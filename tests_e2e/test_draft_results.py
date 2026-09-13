"""tests_e2e/test_draft_results.py — Draft results page: year picker + award cards.

Award cards (templates/draft_results.html) are conditionally rendered
Jinja blocks (best_overall, best_by_round, quickest, slowest,
cumulative_fastest, cumulative_slowest) gated on real season data being
complete enough — see git history: "gate draft results win-based award
cards on week 1 completion, add cumulative pick-time cards". This test
checks structure/no-error, not specific award values, since those values
drift as real seasons progress.
"""
import pytest
from tests_e2e.test_standings import _login


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_draft_results_loads_and_year_picker_navigates(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/draft-results")
    page.wait_for_selector("#year-pick", timeout=10000)
    assert "Internal Server Error" not in page.content()

    options = page.locator("#year-pick option").all_text_contents()
    assert len(options) >= 1

    if len(options) > 1:
        target_value = page.locator("#year-pick option").nth(1).get_attribute("value")
        page.select_option("#year-pick", value=target_value)
        page.wait_for_url(f"**/draft/{target_value}", timeout=10000)
        assert "Internal Server Error" not in page.content()
