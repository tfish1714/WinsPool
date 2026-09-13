"""tests_e2e/test_admin_draft_overrides.py — In-draft admin controls.

Reuses season-3000 setup and turn-detection helpers from test_live_draft.py
rather than reimplementing them, per the base plan's Task 5. The plan's own
example code imports three names (`_create_season_3000`, `_activate_draft`,
`_make_pick_when_it_is_my_turn`) that do not exist in test_live_draft.py --
this test uses what's actually there instead:
`_create_season_via_admin_ui`, `_set_draft_active`, `_find_turn_page`,
`_make_pick`, `_wait_for_board_advance`.

Only ONE real pick is driven to completion here (not the plan example's
implied multi-pick sequence, and nowhere near test_live_draft.py's full
30-pick draft): ui_renderer.js's renderPickQueue marks the admin-only "undo"
row on `item.pick === active_pick - 1` and the admin-only "reset timer" row
on `item.pick === active_pick` (the currently active pick) simultaneously.
So the instant pick #1 lands and pick #2 becomes active, both admin controls
are already on screen at once -- a second pick is not needed to reach either
one. This test does re-make pick #1 after undoing it, purely to get back to
that same two-controls-visible state so reset-timer can be exercised too,
not because reset-timer requires a second *distinct* completed pick.

Turn order (which of the 10 seeded players is actually first on the clock)
is server-side (draft_order_rules), not caller-chosen, so this connects all
10 real seeded player contexts -- exactly as test_full_ten_player_live_draft
does -- and lets `_find_turn_page` discover who is on the clock, rather than
guessing an index. That is more browser contexts than the plan's 2-context
example, but it's the only way to reliably find pick #1's real owner without
reading draft_order state out-of-band; season creation itself
(`_create_season_via_admin_ui`) already hard-requires all 10 credentials
regardless.

Also, unlike the plan's example (which assumed `.q-undo-btn`/`.q-timer-btn`
are directly clickable), `static/style.css` hides `.q-row-actions` (which
wraps both buttons) behind `.q-row-admin.expanded` -- `display: none` until
that class is present -- and `main.js`'s delegated pick-queue click handler
only adds `.expanded` when the row itself (not the hidden action buttons) is
clicked first. So each admin action here is two real clicks: one on the row
to reveal its buttons, then one on the revealed button.
"""
from tests_e2e.test_standings import _login
from tests_e2e.test_live_draft import (
    clean_season_3000,
    _create_season_via_admin_ui,
    _ensure_live_clock,
    _find_turn_page,
    _make_pick,
    _record_dialogs,
    _set_draft_active,
    _wait_for_board_advance,
)


def _expand_and_click(row, action_btn_selector, dialog_page):
    """Click an admin `.q-row-admin` row to reveal its `.q-row-actions`
    (display:none until `.expanded` is added by main.js's delegated click
    handler), then click the now-visible action button, accepting the
    native confirm() it triggers."""
    row.locator(".q-row-main").click()
    btn = row.locator(action_btn_selector)
    btn.wait_for(state="visible", timeout=5000)
    dialog_page.once("dialog", lambda d: d.accept())
    btn.click()


def test_admin_can_undo_last_pick_and_reset_timer(
    live_server, browser, test_player_credentials, clean_season_3000
):
    assert len(test_player_credentials) == 10, (
        "season setup requires all 10 seeded e2e test players"
    )
    admin_creds = test_player_credentials[0]

    # ── Setup: season 3000, draft opened -- entirely through the real Admin
    # Portal UI, same as test_live_draft.py's own setup. ────────────────────
    setup_context = browser.new_context()
    setup_page = setup_context.new_page()
    setup_dialogs = _record_dialogs(setup_page)
    _login(setup_page, live_server, admin_creds)
    _create_season_via_admin_ui(setup_page, live_server, test_player_credentials)
    clean_season_3000["value"] = _set_draft_active(setup_page, live_server, True)
    assert not any("failed" in msg.lower() for _, msg in setup_dialogs), (
        f"admin setup reported a failure: {setup_dialogs}"
    )
    print(f"[setup] season 3000 created, draft opened; dialogs={setup_dialogs}")

    contexts, pages = [], []
    try:
        # ── Connect all 10 real seeded player contexts. This is needed only
        # so _find_turn_page can discover, from the real board, which one is
        # actually on the clock for pick #1 -- turn order is server-side. ──
        for creds in test_player_credentials:
            ctx = browser.new_context()
            pg = ctx.new_page()
            _record_dialogs(pg)
            pg.set_viewport_size({"width": 1600, "height": 1200})
            _login(pg, live_server, creds)
            pg.goto(f"{live_server}/draft")
            pg.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
            pg.wait_for_selector("#pick-queue .q-row", timeout=30000)
            pg.wait_for_selector("#teams-grid .team-btn", timeout=30000)
            pg.click("#chat-collapse-btn")
            pg.wait_for_selector("#chat-body", state="hidden", timeout=5000)
            contexts.append(ctx)
            pages.append(pg)
        _ensure_live_clock(pages)
        print(f"[setup] all {len(pages)} players connected to /draft")

        # ── Pick #1, made for real by whichever player is actually on the
        # clock. This alone puts both admin-only rows (undo for pick #1,
        # reset-timer for the now-active pick #2) on screen. ────────────────
        idx, on_clock_page, label = _find_turn_page(pages, 1)
        team = _make_pick(on_clock_page, 1)
        _wait_for_board_advance(pages, 2)
        print(f"[pick 1] {label} (page {idx}) -> {team}")

        # ── Admin opens the same draft room in its own page and undoes
        # that pick. setup_context is already an authenticated admin session
        # (setup_page logged in there earlier) -- a new page in the same
        # context reuses that session cookie automatically, so this does NOT
        # call _login() again: _login() expects #signin-screen to still be
        # visible (a logged-out state), which it never is here. ─────────────
        admin_draft_page = setup_context.new_page()
        admin_draft_page.goto(f"{live_server}/draft")
        admin_draft_page.wait_for_selector("#signin-screen", state="hidden", timeout=15000)
        admin_draft_page.wait_for_selector("#pick-queue .q-row", timeout=30000)

        undo_row = admin_draft_page.locator(".q-row-admin:has(.q-undo-btn)")
        undo_row.wait_for(state="visible", timeout=10000)
        _expand_and_click(undo_row, ".q-undo-btn", admin_draft_page)  # confirm('Permanently undo the last pick?...')

        # Undo removes pick #1's result -- the board reverts to pick #1 active
        # on every connected player page.
        _wait_for_board_advance(pages, 1)
        assert "Internal Server Error" not in admin_draft_page.content()
        print("[undo] pick #1 undone; board back to pick #1 active")

        # ── Re-make pick #1 to get back to the same two-controls-visible
        # state, then exercise reset-timer on the (again) active pick #2. ───
        idx, on_clock_page, label = _find_turn_page(pages, 1)
        team = _make_pick(on_clock_page, 1)
        _wait_for_board_advance(pages, 2)
        print(f"[pick 1 again] {label} (page {idx}) -> {team}")

        admin_draft_page.wait_for_selector("#pick-queue .q-row", timeout=15000)
        timer_row = admin_draft_page.locator(".q-row-admin:has(.q-timer-btn)")
        timer_row.wait_for(state="visible", timeout=10000)
        _expand_and_click(timer_row, ".q-timer-btn", admin_draft_page)  # confirm('Reset the timer...?')
        admin_draft_page.wait_for_timeout(500)

        assert "Internal Server Error" not in admin_draft_page.content()
        print("[reset-timer] timer reset on active pick #2")
    finally:
        for ctx in contexts:
            ctx.close()
        setup_context.close()
