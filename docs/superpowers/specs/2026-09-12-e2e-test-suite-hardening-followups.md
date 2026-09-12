# E2E Test Suite: Hardening Follow-Ups

**Date:** 2026-09-12
**Status:** Not designed — backlog, split out of the UI-tests implementation (`docs/superpowers/plans/2026-09-09-ui-tests-playwright.md`) so these don't get lost. Needs its own pass when picked up, not decided here.

## Origin

While implementing and finally reviewing the Playwright e2e test suite (harness + login/mock-draft/standings/schedule/admin/nav-parity/live-draft tests), a number of real things surfaced that were deliberately not fixed inline — either because they were genuinely out of scope for the task that found them, or because fixing them well needed more investigation than a fix-loop round allows. The cache-invalidation gap found during this same work already has its own dedicated follow-up (`docs/superpowers/specs/2026-09-12-cache-invalidation-gap-followup.md`) since it's a distinct, higher-severity production bug; everything below is smaller/lower-severity and bundled together.

## 1. Possible real UX bug: chat overlay may cover the pick-confirmation button (UNVERIFIED — check this first)

`tests_e2e/test_live_draft.py`'s viewport-choice comment states that at Playwright's 1280×720 default, `#selection-preview` sits below the fold and the fixed bottom-right `#chat-overlay` (plus the sticky nav rail) sits on top of `#confirm-pick-btn` wherever the page scrolls it to — "so a real click can never land." The test worked around this with a 1600×1200 viewport plus a programmatic collapse of the chat panel.

This was flagged, not verified: `templates/index.html` does give `#chat-overlay` `position:fixed; bottom:1.5rem; right:1.5rem; z-index:9999; width:320px`, expanded by default, and `#selection-preview`/`#confirm-pick-btn` do live at the top of the right-hand `.available-teams-section` column. 1280×720 is a very common real laptop viewport, and the live draft room is the app's single highest-stakes screen.

**Action:** open a real browser at 1280×720 during an active draft and confirm whether the confirm button is actually obstructed/unclickable. If it reproduces, this is a real production bug on the most important screen in the app, more urgent than anything else in this file.

## 2. Nav-parity test: background-sync race (parked, Task 9 ruling)

`tests_e2e/test_nav_parity.py` captures desktop nav hrefs, then resizes to mobile and captures drawer hrefs — two snapshots in time with no guard against `static/js/main.js`'s async `_backgroundSync()` firing a second `updateNav()` in between. If `draft_active`/`mock_draft_active` diverges from a fresh browser context's localStorage default mid-test (e.g. during the real Aug–Feb scheduled-jobs window when `draft_active` is legitimately `true` server-side), the two captures could reflect different underlying nav states and the test could fail spuriously.

This was deliberately parked rather than fixed during the plan: it fails safe (spurious failure only, never a false pass), the same latent pattern exists in every login-based test in this suite (not unique to nav-parity), and a proper fix (e.g. waiting for `_backgroundSync` to settle before either snapshot) is suite-wide scope.

**Action, if picked up:** add a shared wait helper (e.g. poll `document.readyState` plus a short settle window, or better, expose a JS-side "nav is stable" signal) that every test needing a settled nav state can use, rather than patching this one test in isolation.

## 3. Nav-parity: desktop/drawer year sources can still diverge in the offseason

Fixed in this branch: `current_year` is now registered as a Jinja global (`main.py`) using the literal calendar year, matching desktop `updateNav()`'s `new Date().getFullYear()` exactly, closing the specific 4-page bug the nav-parity test found. But the drawer's *contextual* year (when a route explicitly passes one, e.g. viewing a specific season's standings) still comes from `get_active_season()` in some routes — a different concept ("most recent season with games and draft picks") that is not always the calendar year. Desktop nav always uses the calendar year. These two sources will agree most of the time but are not the same value by definition, and nothing currently documents this for a future reader of `tests_e2e/test_nav_parity.py`.

**Action:** add a one-line docstring note in the test explaining the two sources and that they're expected to coincide in practice, not guaranteed to. Low priority — not a live bug today, a documentation gap only.

## 4. Admin UI: `is_test_account` filtering has UX rough edges

Two related issues from Task 1 (`is_test_account` player flag):
- The `#show-test-accounts-toggle` checkbox lives inside the admin dashboard's **Draft** tab (next to the season-creation player picker), but toggling it also affects the **Players** tab's `#admin-player-list` (both are populated from the same `fetchInitialData()` call). The result: the 10 real e2e test player accounts are invisible in player management, and the only way to reveal them is a checkbox on a completely different tab labeled "Select Draft Entrants (Toggle 10)" — not an obvious place to look for "show test accounts everywhere."
- `is_test_account` is returned by `GET /api/admin/players` and asserted in `tests/test_admin_routes.py`, but no frontend code actually displays it (no badge, no distinct styling) — a real player and a test account look identical in the admin player list once test accounts are shown.

**Action:** move the toggle to a more global location (e.g. the Players tab itself, or a persistent admin-header control), and add a small visual badge/tag on test-account rows so they're distinguishable at a glance once shown.

## 5. Test suite: helper duplication and inconsistent verification idioms

- `tests_e2e/test_mock_draft.py` re-implements the login flow inline instead of importing `_login()` from `tests_e2e/test_standings.py` — a chronological artifact (Task 6's fix round landed before Task 7 defined the shared helper). It's now the only file that duplicates it.
- Two test files independently implement "log in as admin in a separate browser context, toggle a real admin UI control, verify the server accepted it" (`test_mock_draft.py`'s `mock_draft_enabled` fixture for `mock_draft_active`, `test_live_draft.py`'s `_set_draft_active` for `draft_active`) with genuinely different verification strategies: `test_mock_draft.py` asserts on the POST response via `expect_response` and needs a 3-attempt retry loop because "real clicks occasionally get missed by expect_response's listener window"; `test_live_draft.py` instead re-reads `GET /api/config/settings` until it reflects the desired value, which needs no retry and proves persistence rather than just request acceptance.

**Action:** (a) switch `test_mock_draft.py` to import `_login()` rather than duplicate it; (b) if a consolidation pass ever happens, standardize on `test_live_draft.py`'s GET-reread verification pattern — it's the more robust of the two.

## 6. `tests_e2e/test_live_draft.py` robustness gaps

Several small issues in the full 10-player live-draft test, all bounded/non-blocking today but worth cleaning up:
- One browser context can leak if `_login`/setup throws for a player mid-loop, since the context is appended to the cleanup list only after setup succeeds — not wrapped in `try/finally`. Same shape in the `clean_season_3000` fixture's pre-delete step.
- Teardown restores `draft_active` before deleting season 3000 rather than the other way around; if the toggle-restore step times out, season 3000 is never wiped, and — worse — the next run then records the leaked `draft_active` value as the "original" state and never restores the real one. (This was observed for real during this plan's work, when a controller run hit an unrelated timeout and left both season 3000 and `draft_active=true` behind — recovered manually at the time.) Swap the order: delete first, restore the flag second.
- The portfolio win-math assertion is vacuous for season 3000 specifically: with no `preseason_predictions` seeded for that sentinel year, every drafted team's `baseWins` renders as `0.0`, so the assertion only exercises the "10 rows, pool-size" half of the d79fc42 regression class, never the actual win-math half. Needs either seeded preseason data for season 3000 or an explicit comment scoping what the assertion does and doesn't prove.
- `_poll()`'s blanket `except Exception: pass` can hide the real failure reason behind a generic timeout — capturing and surfacing the last exception would meaningfully cut future debugging time.
- No explicit assertion that the live draft room is actually on season 3000 (correctness is inferred across three files today, not locally verified in the test itself).
- The end-of-draft `"R3" in last_row` pick-queue check depends on an undocumented window-size constant in `static/js/ui_renderer.js` (`#pick-queue` renders only a few picks around the active one) — correct today, silently broken if that window size ever changes.

**Action:** low priority, bundle into a single small hardening pass on this file if it's ever picked up (rather than one PR per bullet).

## 7. Seed script: admin test account shares a password with the 9 non-admin fixtures

`scripts/seed_e2e_test_players.py` gives all 10 test accounts (including the one `role=admin` account) the same generated password. The script's own docstring justification — "fixture accounts, not real credentials to protect individually" — holds for the 9 non-admin accounts, but the admin account has real elevated privileges in this app (can override picks, manage players/seasons, etc.), so treating its password identically to the other 9's is a slightly weaker posture than necessary.

**Action:** generate a second, distinct password for the admin account (`e2e-test-01`) — one extra `secrets.token_urlsafe()` call.

## 8. Open question: should `test_live_draft.py` run in the default `pytest tests_e2e/` invocation?

The full 30-pick live draft test takes ~2-3 minutes and dominates the e2e suite's total runtime — the other ~10 tests combined run in under a minute. It's currently wired into the `/deploy` pre-flight gate as-is (a deliberate choice per Task 11), but whether it should stay in the default `pytest tests_e2e/` invocation (e.g. for quick local iteration) or move behind a separate slow-test marker (`@pytest.mark.slow`, run only in the deploy gate and CI, not by default) is a call for whoever owns the day-to-day workflow around this suite — not decided here.

## Non-goals

- Not re-litigating the cache-invalidation gap — that has its own spec (`docs/superpowers/specs/2026-09-12-cache-invalidation-gap-followup.md`).
- Not a general audit of the whole e2e suite — scoped specifically to the items enumerated above, all surfaced during the UI-tests plan's implementation and final review.
