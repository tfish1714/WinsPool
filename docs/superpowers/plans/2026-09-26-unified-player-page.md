# Unified Player Page and Slim Pool Chip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge the account profile page (`/profile`) and the player history page (`/history/player/{id}`) into one page at `/player/{id}`: career history and Season Outlook visible to every player, pool status and the Account and Security form only on your own page. Replace the large standings-page pool banner with a slim one-line chip.

**Architecture:** `/player/{id}` is the history template extended with an outlook card (fed by a new per-player endpoint) and own-page-only sections whose forms are the existing profile form (same element ids). `/history/player/{id}` and `/profile` become redirects. The outlook computation is refactored so `/api/profile/portfolio` (own) and a new `/api/players/{id}/portfolio` (any player) share one function.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, pytest, Playwright e2e (not runnable here).

## Global Constraints

- NO emojis in anything you write (code, comments, UI strings, commits, docs). Existing emoji in `templates/player_profile.html` (trophy) is pre-existing: leave it untouched.
- Zero deletion of existing features/tests: additive or exact replacement. `/api/profile/portfolio` and all its keys/tests stay working. `templates/profile.html` may be removed only if its every element id and behavior lives on the new page and `/profile` redirects.
- Visibility rules (decided by the product owner): the Season Outlook is visible to every logged-in player on any player's page (same projection gating as today: non-admins get `draft_in_progress` while `draft_active`). Paid status is NEVER exposed for other players (own `my_paid` only). Pool status/payout section and Account and Security form appear only on your own page. The server already returns 403 when editing another player's profile; keep it.
- `/player/{id}` is the canonical URL. `/history/player/{id}` must 301-redirect to it; update the All-Time History link to point to `/player/{id}` directly.
- `/profile` must keep working as an entry point (nav link and account menu both point to it; do not change nav markup so desktop/mobile nav parity is untouched): server-side redirect to `/player/{me}` using the `session_token` cookie when it is valid, otherwise render a tiny template whose script redirects using localStorage `nfl_wins_my_player_id`, or to `/wins-pool` if absent.
- Existing element ids of the profile form (`profile-form`, `full-name`, `nickname`, `email`, `mfa-enabled`, `current-password`, `new-password`, `confirm-new-password`) and the `alert` messages must be preserved exactly so `tests_e2e/test_profile.py`, `test_mfa.py` and `test_forced_password_change.py` keep working (update their `goto` paths only if needed; note they cannot be run here).
- The outlook card fetch stays an un-awaited async IIFE; all DOM via textContent; the profile form's submit handler must be attached without waiting on any outlook or pool fetch.
- Never deploy, never write Firestore, never `git add` anything under `.superpowers/`, `.local_db/` or `*.png`. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Docs need `git add -f`.
- A demo server (port 8010, untracked `.local_db/` and `.superpowers/demo/`) is running from this worktree. Do not touch those files or run the full test suite while it exists; run targeted tests only. Say so if a full run is needed.

## Review Focus

- A player page for a player with no draft history in the active season must render (outlook says no teams) rather than 500.
- Another player's page must never render the security form, pool status or `my_paid`, and the API for another player's outlook must not include paid data.
- A stale localStorage player id that does not match the logged-in session must not reveal the security form to the wrong person (the form still requires the current password and the server 403s).
- Old bookmarks: `/history/player/{id}` and `/profile` land on the right page for logged-in users and do not loop.

---

## Task 1: Unified `/player/{id}` page

**Files:**
- Modify: `routes/history_routes.py`, `routes/standings_routes.py` (`/profile`), `routes/api_routes.py` (outlook endpoints), `routes/auth_routes.py` only if needed, `templates/player_profile.html`, `templates/overall_history.html`, `static/js/player_profile.js` only if needed
- Remove after merging: `templates/profile.html` content moves into the new page (keep a minimal `templates/profile_redirect.html` for the no-cookie fallback)
- Test: `tests/test_player_page.py` (new), adjust `tests/test_portfolio_projection.py` only by appending

**Interfaces:**
- Produces: `GET /player/{player_id}` (HTML), 301 from `GET /history/player/{player_id}`, `GET /profile` redirect behavior above.
- Produces: `analysis_service` or a route-level helper `build_player_outlook(player_id: int, is_admin: bool) -> dict` returning exactly what `/api/profile/portfolio` returns today for that player; `GET /api/profile/portfolio` calls it with the caller's id; new `GET /api/players/{player_id}/portfolio` (require_auth, any authenticated player) calls it for any player id. Same `available`/`reason` semantics; unknown player id -> `available: false, reason: "no_teams"` (200).

- [ ] Tests first: `/player/{id}` returns 200 for a player with history and for one without any draft history (analytics may be None today: it 404s in `history_routes.player_profile`; keep 404 only for unknown player ids, but a known player with no history renders with zeroed career); `/history/player/5` -> 301 Location `/player/5`; `/profile` with a valid `session_token` cookie -> 302/307 to `/player/{sub}`, without cookie -> 200 fallback template containing the redirect script; `/api/players/{id}/portfolio` returns the same payload shape as own, works for another player's id, contains no `my_paid`/paid keys, and enforces the draft_active gate for non-admins; `/api/profile/portfolio` unchanged (existing tests still pass); the rendered HTML of another player's page (request without matching identity is not knowable server-side, so assert on the template: the security form and pool sections are wrapped in an element with `hidden` and `id` markers that the script un-hides only when localStorage id equals the page's player id) contains those sections hidden by default.
- [ ] Implement: routes and redirects; refactor the outlook code into the shared helper; template: keep every existing section of `player_profile.html`, add a Season Outlook card (reuse the rendering JS from `profile.html`, generalized to fetch `/api/players/{id}/portfolio` for the page's player id; embed the page's player id in a data attribute), add a hidden-by-default own-page block (`id="own-page-only"`) containing the Account and Security form copied from `profile.html` (same ids and handlers, including the current-password requirement and alerts) and a Pool status block placeholder (filled by Task 2); the script un-hides the block only if `localStorage nfl_wins_my_player_id` equals the page player id, then loads `/api/profile` to pre-fill the form as the old page did. Update the back link/title text to fit ("Player Profile"). Update `overall_history.html` link to `/player/{{ stat.playerId }}`.
- [ ] Docs/tests hygiene: update `tests_e2e/test_profile.py` (and other e2e files) only if paths or selectors must change, noting they were not run; `CLAUDE.md` endpoint list and module notes updated; add to `CLAUDE.md` a line that `/profile` and `/history/player/{id}` redirect to `/player/{id}`.
- [ ] Run targeted tests (`tests/test_player_page.py tests/test_portfolio_projection.py tests/test_pool_odds_games.py tests/test_auth.py tests/test_api_endpoints.py -q`; note pre-existing environment failures in test_api_endpoints). Commit.

## Task 2: Slim pool chip and own-page pool block

**Files:** Modify `templates/wins_pool.html`, `static/js/pool_fee.js`, `static/style.css`, `templates/player_profile.html` (own-page pool block), `tests/` and `tests_e2e/` references to `pool-fee-banner`.

- [ ] Replace the large `#pool-fee-banner` card with a slim single-line chip placed in the standings page header next to the Week and Season controls (inspect `templates/wins_pool.html` header markup): text like `Pot $2,000 | You: Paid` (or `You: Not yet paid`, styled with the existing positive/warning tokens), a link to `/player/{me}` (id from localStorage `nfl_wins_my_player_id`; no link if absent). Hidden when `entry_fee` is 0 or the fetch fails; no payout list and no other player's paid data on this chip. Keep the id `pool-fee-banner` on the chip element so existing references keep working, or update all references consistently.
- [ ] On the own-page block of `/player/{id}` add the Pool card: pot, per-place payouts (server labels), paid count of total, and "Your entry: Paid/Not yet paid" from `/api/pool/status` (season = the page's active season), un-hidden together with the security form and only for the owner.
- [ ] Mobile: chip must not overflow at 390px (wraps below the controls); check the CSS at both widths by reading it.
- [ ] Tests for any server-side piece, then targeted test run. Update `CLAUDE.md`. Commit.
