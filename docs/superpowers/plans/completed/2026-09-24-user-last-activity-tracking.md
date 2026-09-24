# User Last Activity Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record and show `last_active` (most recent authenticated request) next to `last_login` in the admin Season Members table and Player Management cards.

**Architecture:** `require_auth`/`require_admin` call a throttled `record_user_activity(player_id)` (in-memory, 15 minute window per player). The Firestore write runs on a background thread through a lean `db_service.record_player_activity()` that writes only `last_active` and patches the in-memory players frame; it does not clear or signal the static cache. Admin routes expose the field; `admin_main.js` formats it relatively.

**Tech Stack:** Python, FastAPI, pandas, Firestore, pytest, vanilla JS.

**Spec:** `docs/superpowers/specs/2026-09-23-user-last-activity-tracking-design.md`

## Global Constraints

- No emojis in code, comments, docs, or commit messages.
- Zero-deletion policy: `last_login` and everything existing stay as is.
- Throttle window is 900 seconds (15 minutes) per player.
- Activity recording must never raise into, or measurably delay, the request.
- Relative time labels: "Just now" (<1m), "Nm ago" (<60m), "Nh ago" (<24h), "Nd ago" (<7d), otherwise an absolute date like "Sep 18, 2026"; exact time in a `title` tooltip.

## Deviations from the spec (with reasons)

1. **Player id comes from `payload["sub"]`, not `payload["player_id"]`.** `create_token()` stores the id as the string claim `sub`; there is no `player_id` claim.
2. **No `update_player_profile()`.** It does a synchronous Firestore write plus `clear_data_cache(DOMAIN_STATIC)` and `signal_data_update(DOMAIN_STATIC)`, invalidating the static bundle (players, draft tables) on every instance every time any player crosses the 15 minute mark, and rewrites the local pickle. That is the cache churn spec section 2.2 sets out to avoid. Instead: `db_service.record_player_activity(player_id, ts)` writes only `last_active` and patches the cached players frame in place. The static bucket has no TTL, so without patching, the admin panel would never see the new value. Other warm instances pick it up on their next static refresh; `last_active` is a coarse indicator so that lag is acceptable.
3. **Background thread**, so the Firestore round trip never sits in the request path (spec 2.4 allows "non-blockingly").
4. `GET /admin/members/{season}` returns `{"members": [...]}`, not a bare list; tests use the real shape.
5. HTML page routes are not behind `require_auth` (the login wall is client-side), so activity is recorded through the authenticated API calls a page load triggers, not the HTML request itself.

## File Structure

- Modify `services/db_service.py`: add `record_player_activity(player_id, ts)`.
- Modify `services/session_service.py`: `_LAST_ACTIVE_CACHE`, `record_user_activity()`, hooks in `require_auth`/`require_admin`.
- Modify `routes/admin_routes.py`: `last_active` in both responses.
- Modify `static/js/admin_main.js`: `_formatRelative()`, Season Members and Player Management display.
- Tests: `tests/test_session_service.py`, `tests/test_admin_routes.py`, `tests/test_db_service_activity.py` (new).

---

### Task 1: Lean persistence in `db_service`

**Files:** Modify `services/db_service.py`; Test `tests/test_db_service_activity.py`

**Interfaces:** Produces `record_player_activity(player_id: int, ts: float) -> None`. Writes `{"last_active": ts}` to `players/{id}` when Firestore is configured; always patches the cached static players frame in place if present. Never clears or signals any cache domain.

- [ ] Tests: (a) with a mocked `get_db()` the doc `update` is called once with `{"last_active": ts}`; (b) `clear_data_cache` and `signal_data_update` are not called; (c) the cached players frame row gets `last_active == ts` (column created if missing); (d) unknown player id is a no-op; (e) `get_db()` returning None (local mode) skips Firestore but still patches memory.
- [ ] Implement: fetch bucket via `cache_service.get_domain(DOMAIN_STATIC)`; if `players` frame present, set `loc[mask, "last_active"] = ts`.
- [ ] Run `pytest tests/test_db_service_activity.py -v`; commit.

### Task 2: Throttled `record_user_activity` and auth hooks

**Files:** Modify `services/session_service.py`; Test `tests/test_session_service.py`

**Interfaces:** Produces `record_user_activity(player_id: int) -> None`, `_LAST_ACTIVE_CACHE: dict[int, float]`, `_ACTIVITY_THROTTLE_SECONDS = 900`, `_run_in_background(fn, *args)` (tests replace it with a synchronous call). Consumes Task 1's `record_player_activity`.

- [ ] Tests: first call writes; second call within 14 minutes does not; call after 15 minutes writes again and updates the cache; DB exception is swallowed and does not raise; `require_auth` with a valid token calls `record_user_activity(int(sub))`; invalid/expired/missing token does not; `require_admin` records for admin, and a non-admin 403 does not; non-numeric `sub` does not raise.
- [ ] Implement per spec 3.2 with the deviations above; the cache is stamped before the write is submitted so concurrent requests do not double write. Add an autouse fixture clearing `_LAST_ACTIVE_CACHE` between tests.
- [ ] Run `pytest tests/test_session_service.py tests/test_auth.py tests/test_draft_auth.py -v`; commit.

### Task 3: Admin endpoints expose `last_active`

**Files:** Modify `routes/admin_routes.py` (`fetch_admin_players`, `get_season_members`); Test `tests/test_admin_routes.py`

- [ ] Tests: `GET /admin/players` returns `last_active` float when present and `None` when absent or NaN; `GET /admin/members/{season}` does the same inside `members`.
- [ ] Implement with the same cast expression already used for `last_login`.
- [ ] Run `pytest tests/test_admin_routes.py -v`; commit.

### Task 4: Admin panel display

**Files:** Modify `static/js/admin_main.js` (near `_formatDate`, lines ~133 and ~270); `templates/admin_main.html` only if the members table header needs a column.

- [ ] Add `_formatRelative(ts)` returning `{text, title, fresh}` per the constraints; `fresh` is true under 15 minutes.
- [ ] Season Members: an Activity cell with a dot for `fresh`, falling back to `last_login`, then "Never"; tooltip `Last active: <date> | Last login: <date>`.
- [ ] Player Management: meta row `Active: <rel> · Login: <rel>`.
- [ ] Verify in a browser at desktop and ~390px widths (an admin login is required; if unavailable, state that the check was DOM/unit level only).
- [ ] Commit.

### Task 5: Verify, document, archive

- [ ] `pytest tests/ -n auto`; compare any failures against the same tests on a clean checkout.
- [ ] Document `last_active` in `CLAUDE.md` (Auth section) and `docs/database.md` if it lists player fields.
- [ ] Move this plan and the spec into `completed/`; use superpowers:verification-before-completion and superpowers:finishing-a-development-branch.
