# Admin-Flows E2E Tests: Minor Follow-Ups

**Date:** 2026-09-13
**Status (updated 2026-09-24, branch `worktree-e2e-suite-hardening`, plan `plans/completed/2026-09-24-e2e-suite-hardening.md`):** Items 1, 3, 4, 5 and 6 are resolved (canonical `_open_admin_tab`; real logout via `_logout` instead of clearing localStorage; strict reset-timer dialog assertion; undo step on the shared recorder; leak-proof contexts in `test_admin_draft_overrides.py` and `test_live_draft.py`). Item 3 turned out to have one call site, not two. Item 2 is resolved for the admin files (`_record_dialogs` everywhere, `_click_through_two_dialogs` retired, message checks added) except that one-shot `page.once("dialog", ...)` handlers remain in `test_forced_password_change.py` and `test_profile.py`, which were outside the requested scope. Items 7, 8, 9, 10 and 11 are untouched. Original status: Not designed — backlog, split out of `docs/superpowers/plans/2026-09-09-ui-tests-admin-flows.md`'s implementation so these don't get lost. Needs its own pass when picked up, not decided here.

## Origin

While implementing and reviewing the admin-flows Playwright e2e tests (dashboard tab smoke tests, player management, member/paid tracking + recap preview, in-draft admin overrides), a number of Minor-severity issues surfaced across per-task review and the final whole-branch review. All Critical/Important findings from that work were fixed inline before merge (see the plan file's own task history and commit log); everything below was deliberately deferred as genuinely non-blocking. This is the sibling of `docs/superpowers/specs/2026-09-12-e2e-test-suite-hardening-followups.md`, which covers the base UI-tests plan's own parked items — some categories below echo that doc's shape (helper duplication, dialog-handling inconsistency) because the same suite grew by the same process twice.

## 1. Four different idioms for clicking an admin tab

- `tests_e2e/test_admin_tabs.py`, `tests_e2e/test_admin_members_and_recap.py`: `.admin-tabs .tab-btn[data-tab="X"]`
- `tests_e2e/test_admin_player_management.py`: bare `[data-tab="X"]`
- `tests_e2e/test_live_draft.py`, `tests_e2e/test_admin_dashboard.py`: `.admin-tab-btn[data-tab='X']`

All three are correct today — `templates/admin.html`'s tab buttons carry both `admin-tab-btn` and `tab-btn` classes, and `data-tab` is unique on `/admin` — so this is style drift, not a bug. But it means the next admin-template change has three different places to break in three different ways instead of one.

**Action:** add a single `_open_admin_tab(page, tab_id)` helper (alongside the existing `_open_admin_draft_tab` in `test_live_draft.py`) and migrate all five files to it.

## 2. Dialog-handling has three idioms, and some tests register none at all

`test_live_draft.py` already exports a reusable `_record_dialogs(page)`. `test_admin_draft_overrides.py` imports and uses it (for undo and, after the final-review fix, for reset-timer too). `test_admin_player_management.py` rolls its own `_click_through_two_dialogs` plus scattered one-shot `page.once(...)` calls. `test_admin_tabs.py` registers nothing — if any tab's initial render ever alerted on a data-fetch failure, Playwright would silently auto-dismiss it and the test would fail 10s later on an unrelated selector timeout with no indication why.

Separately, `test_admin_player_management.py`'s `_click_through_two_dialogs` counts dialogs but never checks their message content — if the app ever surfaces an unexpected error dialog in place of the expected success alert, the helper would silently accept it as satisfying the "2 dialogs" count, and the test would only fail later on a downstream UI-state assertion with a more confusing symptom than necessary.

**Action:** standardize on `_record_dialogs` everywhere (retire `_click_through_two_dialogs` and the scattered `page.once` calls in favor of it), and add message-content assertions where a dialog's specific wording distinguishes success from failure.

## 3. `localStorage.clear()` used instead of the real logout button

`tests_e2e/test_admin_player_management.py` (the reclaim flow, two call sites) clears `localStorage` to force a logged-out state instead of clicking the real logout control (`#nav-ap-logout-btn` / `#drawer-logout-btn`, both wired to `POST /api/logout`). This isn't a Python/API shortcut and is a common, low-risk test-isolation technique — `localStorage.clear()` doesn't reach the `session_token` httpOnly cookie at all, so technically the browser context is left claiming admin via cookie and the target player via localStorage simultaneously until the next real login overwrites it. Harmless in practice (never observed to cause a test failure), but it skips the real UI path this suite otherwise insists on everywhere else.

**Action:** swap in the real logout button click. Low priority — not a live bug, a consistency gap.

## 4. `test_admin_draft_overrides.py`: reset-timer dialog assertion can pass vacuously

`admin_draft_page`'s reset-timer step asserts `not timer_dialogs[1:]` (i.e., "no dialogs after the first one"). If the expected `confirm()` were never recorded at all (handler attached a moment too late, dialog coalesced with a prior one), an error `alert()` could land at index 0 instead and the assertion would still pass, since it only checks what comes *after* index 0.

**Action:** assert the shape directly instead — `len(timer_dialogs) == 1 and timer_dialogs[0][0] == "confirm"`.

## 5. `test_admin_draft_overrides.py`: undo step and reset-timer step use different dialog-handling idioms in the same test

The undo-pick portion uses a one-shot `page.once(...)` (via `_expand_and_click`); the reset-timer portion (after the final-review fix) uses a persistent `_record_dialogs` recorder. This is inconsistency, not a hole today — a silently-swallowed dialog on the undo step would still be caught downstream by `_wait_for_board_advance(pages, 1)`, which requires the board to genuinely revert — but it's an easy trap for the next person editing this file to copy the weaker pattern.

**Action:** standardize both steps on `_record_dialogs`, consistent with the direction in item #2.

## 6. `test_admin_draft_overrides.py`: `setup_context` can leak on an early setup failure

`setup_context` is created and used for `_login`/`_create_season_via_admin_ui`/`_set_draft_active` before the `try:` block that owns the `finally: ...; setup_context.close()` cleanup begins. If any of those calls raises, `setup_context` (and its browser process) is never closed. This exactly mirrors a pre-existing pattern already in `test_live_draft.py` (not a new defect introduced by this plan), so it's a shared, suite-wide gap rather than something specific to this file.

**Action:** bundle with `docs/superpowers/specs/2026-09-12-e2e-test-suite-hardening-followups.md` item 6's identical context-leak note on `test_live_draft.py` — same fix, same file family, worth doing together.

## 7. `test_admin_draft_overrides.py`: 10-player connect loop duplicates `test_live_draft.py`

The per-player "new context, viewport, login, goto `/draft`, wait for selectors, collapse chat" sequence is copied rather than factored into a shared helper, since no such helper exists yet to reuse and extracting one was out of this task's create-only scope.

**Action:** if `test_live_draft.py` and `test_admin_draft_overrides.py` diverge further, extract a `_connect_all_players(browser, live_server, creds_list)` helper into `test_live_draft.py` (the de-facto shared module for live-draft setup) and have both files call it.

## 8. `test_admin_members_and_recap.py`: hardcoded 2024 Week 1 data dependency, no skip-on-404

The recap-prompt-preview test assumes `.local_db` has completed 2024 Week 1 game data. `.local_db/` is gitignored and rebuilt from Firestore via `scripts/refresh_local_pkls.py`, so this can vary by machine/environment. A missing-data failure currently surfaces as a bare `wait_for_selector` timeout rather than a clear skip or error message.

**Action:** if the preview endpoint 404s (no completed games for the chosen year/week), `pytest.skip()` with a message naming the missing data, rather than timing out opaquely.

## 9. `test_admin_tabs.py` performs 9 full logins to assert class toggles

7 parametrized tab tests plus 2 dedicated tests each spin up a fresh browser context, full page load, and auth round trip just to assert a `hidden` class flips. This is correct and matches the plan's "real UI only" intent, but it's a meaningful chunk of the deploy pre-flight gate's total runtime for what is, per test, a single DOM assertion.

**Action:** low priority. A module-scoped authenticated context (login once, reuse the session across the parametrized cases) would cut this file's runtime substantially without weakening what it verifies — each case still needs its own page navigation to `/admin`, just not its own login.

## 10. `test_admin_player_management.py`: `test_edit_player_profile` leaves player 9's phone permanently changed

The edit test sets `test_player_credentials[9]`'s phone to `555-0199` and never restores it. Idempotent on rerun (each run just sets the same value again) and harmless to any other test, but it's state this suite doesn't clean up, unlike the password mutations (which now do, via the `restore_player_9` fixture added in the final-review fix wave).

**Action:** fold a phone-restore into the same `restore_player_9` fixture teardown if it's ever touched again — deliberately not bundled into the fixture originally, to avoid coupling an unrelated test's cleanup into the password-restore fixture's scope.

## 11. Plan file left unticked and not moved to `plans/completed/`

`docs/superpowers/plans/2026-09-09-ui-tests-admin-flows.md` still has every `- [ ]` unchecked and sits in `plans/` rather than `plans/completed/` (where prior finished plans live). More substantively: Task 4's Gemini-API scope-cut rationale, the dead `force_pick` WebSocket-action finding, and the recommendation to add a `DISABLE_AI_GENERATION` test-mode gate (mirroring the email-sending gate) all currently live only inside that plan file's prose.

**Action:** check off the plan's tasks and move it to `plans/completed/` for consistency with the rest of the repo's plan history. Separately, promote the `DISABLE_AI_GENERATION` gate recommendation to its own tracked follow-up so it has a durable home independent of the plan file's location — it's the one item here with a real, if modest, ongoing cost (the recap-generation flow stays untestable past prompt-preview until it exists).

## Non-goals

- Not re-litigating anything already fixed before merge (the 3 Important findings from the final whole-branch review — player-9 fixture-scoped password restore, `test_create_player`'s idempotency race, and the paid-toggle test's server-round-trip verification — were all fixed and re-reviewed clean; they are not repeated here).
- Not a general audit of the whole e2e suite — scoped specifically to the items enumerated above, all surfaced during this plan's implementation and review.
