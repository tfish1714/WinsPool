"""tests_e2e/test_standings.py — Standings and schedule page smoke tests."""
import pytest


def _login(page, live_server, creds):
    page.goto(live_server)
    page.wait_for_selector("#signin-screen", state="visible")
    page.fill("#auth-email", creds["email"])
    page.fill("#auth-password", creds["password"])
    page.click("#auth-submit-btn")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_standings_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.wait_for_url("**/wins-pool/**", timeout=10000)

    # The load-bearing 500 detector for THIS route is the status code, not the
    # body. wins_pool_by_year() (routes/standings_routes.py) wraps itself in a
    # try/except returning server_error() — a JSONResponse with status 500 and
    # body {"error": "An internal error occurred."} — so Starlette's default
    # "Internal Server Error" page is never rendered here and the string check
    # below is a no-op for this route. Same reasoning as
    # tests_e2e/test_live_draft.py's mid-draft check (commit bc377c3). We
    # re-navigate explicitly because arriving here via the post-login
    # client-side redirect gives us no Response object to inspect.
    # Do not drop this status assertion.
    response = page.goto(page.url)
    assert response is not None and response.status == 200, (
        f"{page.url} returned HTTP "
        f"{response.status if response else 'no response'}"
    )
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)

    assert page.locator(".app-container").count() > 0
    # Kept as cheap defence in depth; provably blind to this route's own 500s
    # (see comment above), so it is not what catches a failure here.
    assert "Internal Server Error" not in page.content()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_schedule_page_loads(live_server, page, test_player_credentials, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, test_player_credentials[1])

    page.goto(f"{live_server}/schedule")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    assert "Internal Server Error" not in page.content()


# ---------------------------------------------------------------------------
# Tiebreaker highlight + tooltip (static/js/tiebreaker_explain.js).
#
# The DOM layer reads the rendered data-role cells, so instead of depending on
# whether local data happens to contain ties, these tests rewrite the stacked
# cards' cells in the browser and fire `standings:patched` (the same event
# standings_refresh.js dispatches after a live patch).
# ---------------------------------------------------------------------------
_INJECT_JS = """
([tbA, tbB]) => {
    // Distinct totals for every card (1000, 990, ...) with all tiebreakers 0,
    // then force cards 1 and 2 (second and third ranked) level on wins and
    // give them the caller's tiebreaker values. No other adjacent pair ties.
    const cards = Array.from(document.querySelectorAll('.standings-stacked-card'));
    const set = (card, role, v) => {
        const el = card.querySelector(`[data-role="${role}"]`);
        if (el) el.textContent = v;
    };
    cards.forEach((card, i) => {
        set(card, 'total', String(1000 - i * 10));
        for (let t = 1; t <= 6; t++) set(card, 'tb' + t, '0');
    });
    set(cards[1], 'total', '500');
    set(cards[2], 'total', '500');
    tbA.forEach((v, i) => set(cards[1], 'tb' + (i + 1), v));
    tbB.forEach((v, i) => set(cards[2], 'tb' + (i + 1), v));
    document.dispatchEvent(new CustomEvent('standings:patched'));
    return [1, 2].map((i) => ({
        id: cards[i].dataset.playerId,
        name: cards[i].querySelector('.standings-stacked-card__name').textContent.trim(),
    }));
}
"""

_DECISIVE_JS = """() => Array.from(document.querySelectorAll('.tb-decisive')).map(
    el => [el.closest('[data-player-id]').dataset.playerId, el.dataset.role])"""


def _open_standings(page, live_server, creds, viewport):
    page.set_viewport_size(viewport)
    _login(page, live_server, creds)
    page.wait_for_url("**/wins-pool/**", timeout=10000)
    page.wait_for_selector("#signin-screen", state="hidden", timeout=10000)
    page.wait_for_selector(".standings-stacked-card", state="attached", timeout=10000)
    if page.locator(".standings-stacked-card").count() < 4:
        pytest.skip("Need at least 4 players in local standings data to inject a tie")


def _visible_decisive(page, player_id, tier):
    return page.locator(
        f'[data-player-id="{player_id}"] [data-role="{tier}"].tb-decisive:visible'
    ).first


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_tiebreaker_tooltip_exists_and_hidden_by_default(live_server, page, test_player_credentials, viewport):
    _open_standings(page, live_server, test_player_credentials[1], viewport)
    assert page.locator("#tb-tooltip").count() == 1
    assert page.locator("#tb-tooltip").is_hidden()


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_tiebreaker_decisive_cell_highlighted_and_explained(live_server, page, test_player_credentials, viewport):
    _open_standings(page, live_server, test_player_credentials[1], viewport)

    # Tied on wins and TB1; TB2 is the first differing tier (4 vs 2).
    a, b = page.evaluate(_INJECT_JS, [["0", "4", "0", "0", "0", "0"], ["0", "2", "0", "0", "0", "0"]])

    # Exactly the two tied players' TB2 cells are decisive; nothing else is.
    assert {tuple(x) for x in page.evaluate(_DECISIVE_JS)} == {(a["id"], "tb2"), (b["id"], "tb2")}
    assert page.locator(".tb-decisive:not([data-tb-explain])").count() == 0

    cell = _visible_decisive(page, a["id"], "tb2")
    assert cell.get_attribute("tabindex") == "0"

    tooltip = page.locator("#tb-tooltip")
    assert tooltip.is_hidden()
    cell.focus()
    tooltip.wait_for(state="visible", timeout=3000)
    text = tooltip.text_content()
    assert a["name"] in text and b["name"] in text
    assert "TB2" in text and "4 vs 2" in text and "500 wins" in text

    page.keyboard.press("Escape")
    tooltip.wait_for(state="hidden", timeout=3000)


@pytest.mark.parametrize("viewport", [
    {"width": 1280, "height": 800},
    {"width": 390, "height": 844},
])
def test_tiebreaker_signed_point_diff_tier_and_fully_level_pair(live_server, page, test_player_credentials, viewport):
    _open_standings(page, live_server, test_player_credentials[1], viewport)

    # TB1-TB3 level; TB4 (worst-team point differential) decides: "+7" vs "-3".
    a, b = page.evaluate(_INJECT_JS, [["0", "0", "0", "+7", "0", "0"], ["0", "0", "0", "-3", "0", "0"]])
    assert {tuple(x) for x in page.evaluate(_DECISIVE_JS)} == {(a["id"], "tb4"), (b["id"], "tb4")}
    _visible_decisive(page, b["id"], "tb4").focus()
    tooltip = page.locator("#tb-tooltip")
    tooltip.wait_for(state="visible", timeout=3000)
    assert "TB4" in tooltip.text_content() and "7 vs -3" in tooltip.text_content()
    page.keyboard.press("Escape")
    tooltip.wait_for(state="hidden", timeout=3000)

    # Level on wins and all six tiebreakers: nothing to highlight, and stale
    # highlights from the previous pass must be cleared.
    page.evaluate(_INJECT_JS, [["1"] * 6, ["1"] * 6])
    assert page.locator(".tb-decisive").count() == 0
    assert page.locator("[data-tb-explain]").count() == 0
