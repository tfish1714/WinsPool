# Client Auth Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop the "admin page errors until I sign out and back in" failure. The client treats a saved player id as logged in and never reacts when the server rejects the session token (expired after 7 days, invalid, or revoked), so pages show errors. Add one shared client guard that signs the user out cleanly on a rejected session, and make the admin page show real error details.

**Architecture:** A tiny non-module script `static/js/auth_guard.js` (loaded from `templates/base.html` before `main.js`, so it applies to every page that extends base and to all raw `fetch` calls) wraps `window.fetch`. It inspects responses to same-origin `/api/` requests; on a 401 whose body indicates a dead session it clears the saved login keys and sends the user to the sign-in screen once. Admin error surfaces show `status: message`.

**Tech Stack:** vanilla JS, Jinja2, pytest (source and markup contract tests only; no JS runner exists).

**Spec:** Product owner report in this conversation: after deploys the admin screen errors and needs sign-out/sign-in; has happened for a while (not from recent changes).

## Global Constraints

- NO emojis. Zero deletion of existing features/tests (additive or exact replacement). Never deploy, never write Firestore, never `git add` `.superpowers/`, `.local_db/`, `*.png`. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Docs need `git add -f`.
- Work only in `C:\Users\fisch\OneDrive\Documents\Code\WinsPool\.claude\worktrees\auth-guard` (branch `sprint-auth-guard`). Run targeted tests only plus one final full suite: `python -m pytest tests/ -n auto -q -p no:cacheprovider --color=no`, compare failing test ids with the 34 known environment-caused ids in the SDD workspace `baseline_failure_ids.txt` (ids only, strip FAILED/ERROR prefixes).
- Server 401 details today (`services/session_service.py`): "Missing or invalid Authorization header.", "Session expired. Please log in again.", "Invalid session token.", and the revoked-session detail (`_REVOKED_DETAIL`). A 401 from login/MFA/password endpoints means wrong credentials, NOT a dead session: never trigger the guard for `/api/login`, `/api/mfa/verify`, `/api/set_password`, `/api/check_player`, `/api/profile/update` (its 401-with-`error` = wrong current password; a 401 with only `detail` = dead session, already handled by `player_profile.html`, keep that behavior compatible), or anonymous public endpoints.
- Storage keys used by the client (verify in `static/js/auth_service.js` and `main.js` and clear ALL session-related ones consistently): `nfl_wins_token`, `nfl_wins_my_player_id`, and the related cached user keys (`nfl_wins_playerName`, `nfl_wins_nickName`, `nfl_wins_user_email`, role/admin flags): clear exactly what `AuthService.logout()`/sign-out already clears, by reusing the same list.
- The httpOnly `session_token` cookie cannot be cleared from JS; the guard should call the existing logout endpoint (`POST /api/logout`) best-effort before redirecting so the cookie is cleared server-side.

## Review Focus

- No redirect loops: after clearing, the sign-in screen must load without calling a protected API that 401s again; a page reload while logged out must not trigger the guard repeatedly (guard runs at most once per page load).
- A 401 that is not a dead session (wrong password, MFA code) must never sign the user out.
- Requests made before login (no token, anonymous public pages such as the standings page when signed out) must not redirect.
- The fetch wrapper must preserve behavior for all callers: same response object, streams, options, AbortSignal, non-API URLs untouched, errors rethrown.
- Concurrency: several 401s at once cause one sign-out.

---

## Task 1: auth guard and admin error detail

**Files:** Create `static/js/auth_guard.js`, `tests/test_auth_guard.py`; Modify `templates/base.html`, `static/js/admin_main.js` and the other admin scripts' generic error rendering (grep `static/js/admin_*.js` for `Unknown API Error`, `.catch(`, `throw new Error(err.error` patterns and admin panel error text), `static/js/api.js` (surface `status` and server `detail`/`error` in the thrown Error message), `CLAUDE.md`.

- [ ] Tests first (`tests/test_auth_guard.py`, source/markup contract tests): `templates/base.html` loads `auth_guard.js` as a classic script before `main.js`; `auth_guard.js` exists and (source assertions) wraps `window.fetch`, checks `response.status === 401`, restricts to same-origin `/api/` URLs, excludes the auth endpoints listed above, reads the body via a cloned response, matches dead-session details (expired / invalid session token / missing or invalid Authorization header / no longer valid), clears the storage keys, and guards against repeat runs; `api.js` error message includes the HTTP status and server detail; the admin script error rendering shows status and message rather than a generic string. Also test any server-side piece you add.
- [ ] Implement `auth_guard.js` (self-contained, defensive with try/catch so a guard bug can never break fetch), wire it in `base.html`, improve error detail in `api.js` and admin scripts. Server change only if needed to make dead-session 401s machine-recognizable (for example add a stable response header `X-Session-State: expired|invalid|revoked|missing` on those 401s in `session_service.py` and have the guard prefer the header over text matching); if you add it, test it in `tests/`.
- [ ] Update `CLAUDE.md` (a short "Client auth guard" note: what it does, that login state is otherwise only the saved player id, and the 7-day token lifetime). Run targeted tests, then the full suite comparison. Commit.
