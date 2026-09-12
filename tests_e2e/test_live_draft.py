"""tests_e2e/test_live_draft.py — Full 10-player live draft, season 3000.

Runs through the real Admin Portal UI (not direct API calls) to create the
season and open the draft, then drives 10 real Playwright browser contexts —
one per seeded test player — through the actual live-draft WebSocket flow,
one real pick at a time, in turn order, for all 30 picks.

This is the shape that produced the bugs this suite exists for:
  * d79fc42 — the draft room's round label and the admin running-portfolio
    win math derived the pool size from `all_players.length` (every
    registered account) instead of the draft's own `draft_board.length / 3`,
    so every pick landed in the wrong round.
  * bce24bc — /wins-pool 500'd mid-draft while pick counts were uneven.
Both need the real 10-player / 30-pick scale: `draft_routes.py`'s pick
handler hardcodes `active_pick > 30` and `admin_main.js::generateSeason`
refuses anything but `selectedPlayerIds.size === 10`.

Real draft-room selectors (verified against static/js/ui_renderer.js +
static/js/main.js + templates/index.html, NOT the mock-draft page):
  * the live draft room is `/draft` — plain, no season segment. `/draft/3000`
    is the *draft results* page (`route_draft_results_by_year`).
  * team grid   → `#teams-grid button.team-btn[data-team="XXX"]`
  * selection   → `#selection-preview` (`.hidden` until a team is clicked),
                  `#selected-team-name` (bare abbr when it is genuinely your
                  turn, `"XXX (ADMIN OVERRIDE)"` / `"XXX (Not Your Turn!)"`
                  otherwise), `#confirm-pick-btn` (disabled until allowed)
  * pick queue  → `#pick-queue .q-row[data-pick-num]`
  * full board  → `#draft-list li.draft-item(.active)` with `.pick-number`
                  and `.pick-player` (which carries " (YOU)" for the
                  connected player's own rows — this is the turn signal)
  * round label → `#round-label` (renders "/round N")
  * clock card  → `#shame-timer-card` (`.cc-sub` = "Round N · Pick P of 30",
                  or "Draft complete 🎉" once the board is full)
There is no `.team-card`, no `.disabled` state on team buttons, and no
"Confirm"-labelled button text match — the grid renders identically on every
connected page regardless of whose turn it is, so turn detection must come
from the board, not from which teams are clickable.
"""
import math
import re
import time

import pytest

from tests_e2e.test_standings import _login

SEASON = 3000
POOL_SIZE = 10
TEAMS_PER_PLAYER = 3
TOTAL_PICKS = POOL_SIZE * TEAMS_PER_PLAYER


# ── Admin Portal helpers ─────────────────────────────────────────────────────

def _record_dialogs(page):
    """Accept every native dialog while keeping a record of what it said.

    Both the Admin Portal (confirm() before generate/wipe, alert() with the
    result) and the draft room (alert() on a WebSocket `error` message, e.g.
    "It is not your turn to pick!") use native dialogs. Playwright
    auto-dismisses unhandled ones, which would silently hide a rejected pick,
    so every page gets a recorder and the test asserts on its contents.
    """
    seen = []

    def _handle(dialog):
        seen.append((dialog.type, dialog.message))
        dialog.accept()

    page.on("dialog", _handle)
    return seen


def _poll(fn, timeout_s=15, interval_s=0.25, what="condition"):
    """Poll a zero-arg callable until it returns truthy. Used instead of
    page.wait_for_function for anything that needs a network round trip (its
    requestAnimationFrame polling would re-issue the fetch every frame) or
    whose JS body contains an arrow function (which Playwright's
    function-vs-expression heuristic can misread)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(interval_s)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {what}")


def _season_options(page):
    return page.eval_on_selector_all(
        "#delete-season-select option", "els => els.map(e => e.value)"
    )


def _open_admin_draft_tab(page, live_server):
    """Everything this test drives on /admin lives in the Draft tab's section
    (`#draft-section`), which is `.hidden` behind the default Players tab."""
    page.goto(f"{live_server}/admin")
    page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
    page.click(".admin-tab-btn[data-tab='draft-section']")
    page.wait_for_selector("#draft-section:not(.hidden)", timeout=5000)


def _delete_season_via_admin_ui(page, live_server):
    """Wipe season 3000 through the real delete-season control, if present."""
    _open_admin_draft_tab(page, live_server)
    page.wait_for_selector("#delete-season-select", timeout=15000)
    # The dropdown is populated asynchronously by fetchInitialData().
    _poll(lambda: len(_season_options(page)) > 1, 15, what="season dropdown to populate")
    if str(SEASON) not in _season_options(page):
        return False
    page.select_option("#delete-season-select", str(SEASON))
    page.click("#delete-season-btn")
    # deleteSeason() re-runs fetchInitialData() on success, so wait for the
    # option to actually disappear rather than sleeping.
    _poll(
        lambda: str(SEASON) not in _season_options(page),
        15,
        what=f"season {SEASON} to disappear from the wipe dropdown",
    )
    return True


def _create_season_via_admin_ui(page, live_server, creds_list):
    _open_admin_draft_tab(page, live_server)
    page.wait_for_selector("#player-grid", timeout=15000)

    # The e2e accounts are is_test_account=True, excluded from the picker by
    # default; the toggle re-runs fetchInitialData() and re-renders the grid.
    page.check("#show-test-accounts-toggle")
    page.wait_for_selector(
        f".player-checkbox[value='{creds_list[0]['id']}']", timeout=15000
    )

    for creds in creds_list:
        page.check(f".player-checkbox[value='{creds['id']}']")

    # generateSeason() hard-refuses unless exactly 10 are selected.
    page.wait_for_selector("#player-count:has-text('10/10')", timeout=5000)

    page.fill("#season-input", str(SEASON))
    assert page.input_value("#season-input") == str(SEASON)

    page.click("#generate-btn")
    # Success is observable: the new season shows up in the wipe dropdown once
    # the page refetches. generateSeason() does not refetch itself, so reopen
    # the tab (a real navigation) to pick up the server's new state.
    _poll(
        lambda: _reload_and_check_season(page, live_server),
        30,
        interval_s=1.0,
        what=f"season {SEASON} to appear in the wipe dropdown",
    )


def _reload_and_check_season(page, live_server):
    _open_admin_draft_tab(page, live_server)
    _poll(lambda: len(_season_options(page)) > 1, 10, what="season dropdown to populate")
    return str(SEASON) in _season_options(page)


def _draft_active_state(page):
    return page.locator("#draft-active-toggle").get_attribute("aria-pressed") == "true"


def _set_draft_active(page, live_server, want):
    """#draft-active-toggle is a <button aria-pressed> (admin_main.js's
    initDraftActiveToggle), not a checkbox — read aria-pressed, not
    is_checked(). Non-admin picks are refused server-side unless
    config/settings.draft_active is True."""
    _open_admin_draft_tab(page, live_server)
    page.wait_for_selector("#draft-active-toggle", timeout=15000)
    # initDraftActiveToggle() applies the fetched state asynchronously.
    _poll(
        lambda: page.locator("#draft-active-toggle").get_attribute("aria-pressed")
        is not None,
        10,
        what="#draft-active-toggle to initialise",
    )
    current = _draft_active_state(page)
    if current == want:
        return current
    page.click("#draft-active-toggle")
    _poll(
        lambda: _draft_active_state(page) == want,
        10,
        what=f"#draft-active-toggle aria-pressed={want}",
    )
    # The click handler's POST is fire-and-forget; the optimistic aria-pressed
    # flip above does not prove the server accepted it, and a non-admin pick is
    # refused server-side unless config/settings.draft_active is True.
    _poll(
        lambda: page.evaluate(
            "fetch('/api/config/settings').then(r => r.json()).then(c => c.draft_active)"
        )
        is want,
        15,
        interval_s=0.5,
        what=f"server config draft_active={want}",
    )
    return current


# ── Draft-room helpers ───────────────────────────────────────────────────────

def _active_board_row(page):
    """(pick_number, player_label) of this page's currently active board row.

    Read from `#draft-list`, which lives inside the collapsed
    `#draft-board-section` — hidden, but `text_content()` does not require
    visibility, and this is the only place the renderer marks the connected
    player's own rows (" (YOU)"), which is what makes turn detection possible
    without reaching into JS state.
    """
    try:
        rows = page.locator("#draft-list li.draft-item.active")
        if rows.count() == 0:
            return None
        row = rows.first
        raw_num = (row.locator(".pick-number").text_content() or "").strip()
        who = (row.locator(".pick-player").text_content() or "").strip()
        return int(raw_num.lstrip("#")), who
    except Exception:
        # #draft-list is re-rendered wholesale on every broadcast; a handle
        # resolved a moment ago can vanish mid-read. Callers poll.
        return None


def _clock_text(page):
    try:
        return page.locator("#shame-timer-card").text_content() or ""
    except Exception:
        return ""


def _ensure_live_clock(pages, timeout_s=40):
    """Make sure every page is showing the real clock card, not the
    "draft hasn't opened yet" placeholder.

    main.js seeds `this.draftActive` from localStorage in the App constructor
    and only refreshes it from /api/config/settings in `_backgroundSync()`,
    which can land *after* the first `state` broadcast has already rendered the
    placeholder for a non-admin. A reload picks up the now-cached flag.
    """
    for i, pg in enumerate(pages):
        deadline = time.time() + timeout_s
        reload_at = time.time() + 8
        reloaded = False
        while time.time() < deadline:
            if f"Pick 1 of {TOTAL_PICKS}" in _clock_text(pg):
                break
            if not reloaded and time.time() > reload_at:
                pg.reload()
                pg.wait_for_selector("#teams-grid .team-btn", timeout=30000)
                reloaded = True
            time.sleep(0.3)
        else:
            raise AssertionError(
                f"page {i} never rendered the live clock card; last text: "
                f"{_clock_text(pg)!r}"
            )


def _find_turn_page(pages, expected_pick, timeout_s=45):
    """Poll every connected page until exactly one reports that `expected_pick`
    is both the active pick and its own."""
    deadline = time.time() + timeout_s
    last_seen = None
    while time.time() < deadline:
        mine = []
        seen = []
        for idx, pg in enumerate(pages):
            row = _active_board_row(pg)
            seen.append((idx, row))
            if row and row[0] == expected_pick and "(YOU)" in row[1]:
                mine.append((idx, pg, row[1]))
        last_seen = seen
        if len(mine) == 1:
            return mine[0][0], mine[0][1], mine[0][2]
        if len(mine) > 1:
            raise AssertionError(
                f"pick #{expected_pick}: {len(mine)} pages each claim the turn: "
                f"{[(i, label) for i, _, label in mine]}"
            )
        time.sleep(0.3)
    raise AssertionError(
        f"No player's page showed an active turn for pick #{expected_pick} "
        f"within {timeout_s}s. Active rows seen: {last_seen}"
    )


def _assert_round_label(page, pick):
    """The d79fc42 regression, asserted on every single pick: the round label
    must be ceil(pick / 10), derived from the draft's own 30-pick board and
    not from the number of registered accounts."""
    expected_round = math.ceil(pick / POOL_SIZE)
    label = (page.locator("#round-label").text_content() or "").strip()
    assert label == f"/round {expected_round}", (
        f"pick #{pick}: #round-label was {label!r}, expected '/round {expected_round}'"
    )
    clock = _clock_text(page)
    assert f"Round {expected_round}" in clock, (
        f"pick #{pick}: clock card missing 'Round {expected_round}': {clock!r}"
    )
    assert f"Pick {pick} of {TOTAL_PICKS}" in clock, (
        f"pick #{pick}: clock card missing 'Pick {pick} of {TOTAL_PICKS}': {clock!r}"
    )


def _make_pick(page, pick):
    """Click a team in the real grid, then confirm through the real button.

    Nothing is drafted on a single click (see the rules drawer copy in
    templates/index.html) — selection populates #selection-preview and enables
    #confirm-pick-btn, and only that button sends the `pick` WS action.
    """
    page.wait_for_selector("#teams-grid .team-btn", timeout=15000)
    team = page.locator("#teams-grid .team-btn").first.get_attribute("data-team")
    assert team, f"pick #{pick}: first .team-btn had no data-team attribute"

    # Click by attribute, not by .first — #teams-grid is re-rendered wholesale
    # on every broadcast, so a positional handle can go stale mid-turn.
    page.click(f"#teams-grid .team-btn[data-team='{team}']")
    page.wait_for_selector("#selection-preview:not(.hidden)", timeout=5000)
    page.locator("#selection-preview").scroll_into_view_if_needed()

    # Guard against picking from the wrong page: an admin sees an *enabled*
    # confirm button on someone else's turn, labelled "(ADMIN OVERRIDE)", and
    # a non-admin sees "(Not Your Turn!)". A bare abbreviation means this
    # really is the connected player's own pick.
    selected = (page.locator("#selected-team-name").text_content() or "").strip()
    assert selected == team, (
        f"pick #{pick}: #selected-team-name was {selected!r}, expected {team!r} "
        "— this page is not genuinely on the clock"
    )
    confirm = page.locator("#confirm-pick-btn")
    assert confirm.is_enabled(), f"pick #{pick}: #confirm-pick-btn was disabled"
    confirm.click()
    return team


def _wait_for_board_advance(pages, next_pick, timeout_s=45):
    """Every connected socket gets the same `state` broadcast after a pick;
    wait until they have all rendered it before hunting the next turn."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if next_pick > TOTAL_PICKS:
            if all("Draft complete" in _clock_text(pg) for pg in pages):
                return
        else:
            rows = [_active_board_row(pg) for pg in pages]
            if all(r is not None and r[0] == next_pick for r in rows):
                return
        time.sleep(0.25)
    raise AssertionError(
        f"board did not advance to pick #{next_pick} on all 10 pages within {timeout_s}s; "
        f"saw {[_active_board_row(pg) for pg in pages]}"
    )


# ── Fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_season_3000(live_server, browser, test_player_credentials):
    """Wipe season 3000 before and after the test through the real Admin
    Portal delete-season control, so reruns never accumulate stale picks, and
    restore the draft_active flag the test flips (it is app-global state, and
    other tests in this session read it)."""
    original_draft_active = {"value": None}

    def _admin_page():
        context = browser.new_context()
        page = context.new_page()
        _record_dialogs(page)
        _login(page, live_server, test_player_credentials[0])
        return context, page

    context, page = _admin_page()
    _delete_season_via_admin_ui(page, live_server)
    context.close()

    yield original_draft_active

    context, page = _admin_page()
    try:
        if original_draft_active["value"] is not None:
            _set_draft_active(page, live_server, original_draft_active["value"])
        _delete_season_via_admin_ui(page, live_server)
    finally:
        context.close()


# ── The test ─────────────────────────────────────────────────────────────────

def test_full_ten_player_live_draft(
    live_server, browser, test_player_credentials, clean_season_3000
):
    assert len(test_player_credentials) == 10, (
        "This test requires all 10 seeded e2e test players"
    )
    admin_creds = test_player_credentials[0]

    # ── Setup, entirely through the real Admin Portal ────────────────────────
    setup_context = browser.new_context()
    setup_page = setup_context.new_page()
    setup_dialogs = _record_dialogs(setup_page)
    _login(setup_page, live_server, admin_creds)
    _create_season_via_admin_ui(setup_page, live_server, test_player_credentials)
    clean_season_3000["value"] = _set_draft_active(setup_page, live_server, True)
    assert not any(
        "failed" in msg.lower() for _, msg in setup_dialogs
    ), f"admin setup reported a failure: {setup_dialogs}"
    print(f"[setup] season {SEASON} created, draft opened; dialogs={setup_dialogs}")

    # ── 10 real browser contexts, each a signed-in player in the draft room ──
    contexts, pages, dialog_logs = [], [], []
    wp_context = None
    try:
        for creds in test_player_credentials:
            ctx = browser.new_context()
            pg = ctx.new_page()
            dialog_logs.append(_record_dialogs(pg))
            # A desktop-tall viewport. At Playwright's 1280x720 default,
            # #selection-preview sits below the fold and the fixed
            # bottom-right #chat-overlay (plus the sticky nav rail) sits on top
            # of #confirm-pick-btn wherever Playwright scrolls it to, so a real
            # click can never land. Real users get a taller window and collapse
            # the chat; do both.
            pg.set_viewport_size({"width": 1600, "height": 1200})
            _login(pg, live_server, creds)
            pg.goto(f"{live_server}/draft")
            pg.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
            # Rendered only once the WebSocket's first `state` broadcast lands.
            pg.wait_for_selector("#pick-queue .q-row", timeout=30000)
            pg.wait_for_selector("#teams-grid .team-btn", timeout=30000)
            pg.click("#chat-collapse-btn")
            pg.wait_for_selector("#chat-body", state="hidden", timeout=5000)
            contexts.append(ctx)
            pages.append(pg)
        _ensure_live_clock(pages)
        print(f"[setup] all {len(pages)} players connected to /draft")

        # One reused browser context for the mid-draft /wins-pool regression
        # check — a real page load per pick, without re-authenticating 30 times.
        wp_context = browser.new_context()
        wp_page = wp_context.new_page()
        _record_dialogs(wp_page)
        _login(wp_page, live_server, admin_creds)

        # ── 30 real picks, in turn order ────────────────────────────────────
        drafted = []
        for pick in range(1, TOTAL_PICKS + 1):
            idx, pg, label = _find_turn_page(pages, pick)
            _assert_round_label(pg, pick)
            team = _make_pick(pg, pick)
            _wait_for_board_advance(pages, pick + 1)
            drafted.append(team)
            print(f"[pick {pick:2d}/{TOTAL_PICKS}] {label} (page {idx}) -> {team}")

            # bce24bc: /wins-pool 500'd during a live draft with uneven pick
            # counts, so check on every pick rather than only at the end.
            #
            # CAVEAT, established by running this test (see task-10-report.md):
            # on a single warm process the page's *pick count* is stale for the
            # whole draft, because db_service.add_draft_result() invalidates
            # only the year-keyed data cache (clear_data_cache(season)) while
            # standings_routes reads through load_data() — the 'all' key, with
            # a 1-hour TTL. So this asserts that the route renders season 3000
            # without a 500 and in one of its two legitimate shapes; it cannot
            # assert that the rendered progress matches `pick`.
            wp_response = wp_page.goto(f"{live_server}/wins-pool/{SEASON}")
            # The load-bearing 500 detector for THIS route is the status code,
            # not the body. wins_pool_by_year() wraps itself in a try/except
            # that returns server_error() — a JSONResponse with status 500 and
            # body {"error": "An internal error occurred."} — so Starlette's
            # default "Internal Server Error" page is never rendered here and
            # the string check below is a no-op for this route. (It is kept as
            # cheap defence in depth, and it *is* load-bearing for
            # /draft-results and /draft/{year} further down, which have no
            # try/except.) Do not drop this status assertion.
            assert wp_response is not None and wp_response.status == 200, (
                f"/wins-pool/{SEASON} returned HTTP "
                f"{wp_response.status if wp_response else 'no response'} "
                f"after pick #{pick}"
            )
            wp_page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
            wp_page.wait_for_selector(".app-container", timeout=15000)
            wp_html = wp_page.content()
            assert "Internal Server Error" not in wp_html, (
                f"/wins-pool/{SEASON} returned a server error after pick #{pick}"
            )
            assert (
                f"The {SEASON} Wins Pool standings" in wp_html  # draft-pending card
                or 'id="winsChart"' in wp_html                  # full standings table
            ), f"/wins-pool/{SEASON} rendered neither shape after pick #{pick}"

        assert len(set(drafted)) == TOTAL_PICKS, (
            f"expected 30 distinct teams, got {len(set(drafted))}: {drafted}"
        )

        # ── Draft complete: the full board holds all 30 picks, in order ─────
        for i, pg in enumerate(pages):
            rows = pg.locator("#draft-list li.draft-item")
            assert rows.count() == TOTAL_PICKS, (
                f"page {i}: board had {rows.count()} rows, expected {TOTAL_PICKS}"
            )
            board_teams = pg.locator("#draft-list .pick-team").all_text_contents()
            assert [t.strip() for t in board_teams] == drafted, (
                f"page {i}: board teams {board_teams} != picks made {drafted}"
            )
            # The pick queue's own per-row round tag ("R3·P30") is rendered from
            # the same board-derived pool size as #round-label.
            last_row = (
                pg.locator(f"#pick-queue .q-row[data-pick-num='{TOTAL_PICKS}']")
                .text_content()
                or ""
            )
            assert "R3" in last_row and f"P{TOTAL_PICKS}" in last_row, (
                f"page {i}: last pick-queue row was {last_row!r}, expected R3/P{TOTAL_PICKS}"
            )

        # ── The round label must read 3, never 4 ────────────────────────────
        for i, pg in enumerate(pages):
            label = (pg.locator("#round-label").text_content() or "").strip()
            assert label == "/round 3", f"page {i}: #round-label was {label!r}"
            clock = _clock_text(pg)
            assert "Draft complete" in clock, f"page {i}: clock card was {clock!r}"
            # A 4th round can never exist in a 10-player / 3-teams-each pool —
            # the off-by-one in d79fc42 produced exactly that. Check every
            # surface that derives a round number, not just the header label.
            round_surfaces = " ".join([
                label,
                clock,
                pg.locator("#pick-queue").text_content() or "",
                pg.locator("#draft-list").text_content() or "",
            ])
            assert not re.search(r"(round|\bR)\s*4\b", round_surfaces, re.I), (
                f"page {i}: a 4th round leaked into the draft room: {round_surfaces!r}"
            )

        # d79fc42's other half: the admin running portfolio must be keyed off
        # the draft's own pool size, so exactly the 10 drafters appear with
        # 3 teams each and no NaN leaking out of the win math.
        admin_page = pages[0]
        portfolio_rows = admin_page.locator("#admin-portfolio-content tbody tr")
        assert portfolio_rows.count() == POOL_SIZE, (
            f"admin portfolio showed {portfolio_rows.count()} rows, expected {POOL_SIZE}"
        )
        portfolio_text = admin_page.locator("#admin-portfolio-content").text_content() or ""
        assert "NaN" not in portfolio_text and "undefined" not in portfolio_text, (
            f"admin portfolio win math leaked a bad value: {portfolio_text!r}"
        )
        for team in drafted:
            assert team in portfolio_text, (
                f"admin portfolio is missing drafted team {team}: {portfolio_text!r}"
            )

        # No WebSocket `error` alert (e.g. "It is not your turn to pick!")
        # reached any player at any point.
        for i, log in enumerate(dialog_logs):
            assert not log, f"page {i} raised unexpected dialog(s): {log}"

        # ── Post-draft pages render for the completed season ────────────────
        results_page = pages[0]
        # /draft-results redirects via get_active_season(), which also reads the
        # stale 'all' cache described above, so the landing season is not
        # asserted here — only that whatever it lands on renders.
        results_page.goto(f"{live_server}/draft-results")
        results_page.wait_for_selector(".app-container", timeout=15000)
        assert "Internal Server Error" not in results_page.content()
        print(f"[post] /draft-results landed on {results_page.url}")

        # The just-completed season's own results page: 30 picks, zero games
        # played, no preseason projections. (Its pick table is fed by the same
        # stale 'all' cache, so this is a render/no-500 check, not a data one —
        # the authoritative all-30-picks assertion is the draft board above,
        # which reads the freshly-invalidated year-keyed cache.)
        results_page.goto(f"{live_server}/draft/{SEASON}")
        results_page.wait_for_selector(".app-container", timeout=15000)
        assert "Internal Server Error" not in results_page.content()
    finally:
        if wp_context is not None:
            wp_context.close()
        for ctx in contexts:
            ctx.close()
        setup_context.close()
