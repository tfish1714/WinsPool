# Offseason Hardening Backlog

**Date:** 2026-09-27
**Status:** Specification. Not started. Deliberately deferred: none of this affects the running 2026 season, and the draft is over.
**Purpose:** One authoritative, ordered spec for the work intentionally deferred to the offseason, so it can be picked up cold. Items already completed and shipped in the 2026-09-26/27 sprints (session revocation, client auth guard, per-player session cache, kickoff queue IAM/retry/alert, pool config, team and player pages, etc.) are not repeated here.

**Companion documents:**
- `docs/superpowers/specs/2026-09-26-consolidated-followups-and-tech-debt.md` (Tracks 3, 4, 5, 6 remain open; referenced below by section)
- `DEPLOY.md` (operator steps)

## How to use this spec

Work is grouped into phases by "must land before" milestones. Each item lists: problem, required change, tests, effort (S under 2h, M half day, L 1+ days), and risk. Every item follows the repo rules: TDD, no deletions of existing tests except exact replacements, no emojis, targeted tests during work plus one full-suite id comparison at the end (the full suite passes completely, 1881 tests, on a machine with refreshed local data; in a checkout without `.local_db/` the known 34 environment failures apply).

---

## Phase A: Before the 2027 draft (security)

### A1. Draft websocket must authenticate from the session token (L, security)

* **Problem:** In `routes/draft_routes.py::websocket_endpoint` (~line 598), the `reauthenticate` action accepts a client-supplied `playerId` (~line 626-639) and sets `socket_player_id` after only checking that the player exists and has a password. It never verifies the connecting browser's session token. Anyone who can reach the socket and knows a player id can act as that player (including admin-only actions gated by `_get_authenticated_admin`) during a live draft, which decides the pool. This is the documented "known gap" in `CLAUDE.md` (Real-Time Draft).
* **Required change:**
  1. On the WebSocket handshake read the `session_token` httpOnly cookie (browsers attach cookies to WS upgrades) and, as a fallback, an `Authorization`-style subprotocol/query token only if the client demonstrably cannot send cookies (prefer cookie only).
  2. Decode it with `services.session_service.decode_current_token` (already validates signature, expiry and `token_version`, and returns None on failure or when the session check is unavailable).
  3. Derive `socket_player_id` and the admin flag from the verified payload and the player's stored role, never from a client message. `reauthenticate` becomes a no-op or is removed after the client is updated; if kept for compatibility it must require that the claimed id equals the token's `sub`.
  4. Unauthenticated sockets may connect read-only (projection gating already strips admin-only fields via `strip_admin_only_fields` for non-admins); any pick, override, or chat write from an unauthenticated or mismatched socket is rejected with a structured error and the socket is left open.
  5. A revoked or expired token mid-connection: re-validate on each state-changing action (cheap: the per-player session cache) and close the socket with an application close code on failure.
  6. Client (`static/js/websocket_service.js`, `main.js::onWsOpen`): stop relying on the client-asserted id; on a close code meaning "session invalid" let the client auth guard (`static/js/auth_guard.js`) sign the user out.
* **Tests:** unit tests with the FastAPI websocket test client: no cookie -> read-only; valid cookie -> identity from token even when the client claims another id; token for player A while claiming player B -> rejected; admin action with a non-admin token -> rejected; expired/revoked token -> rejected/closed; `reauthenticate` with a mismatched id does not change identity; projection gating unchanged for non-admin sockets (existing tests stay green).
* **Risk:** high blast radius during a draft; ship at least two weeks before the 2027 draft and rehearse with the mock draft and a staging deployment. Keep a feature flag (`WS_STRICT_AUTH`, default on) for a fast rollback.

### A2. Server-side session state gaps (M, security/UX)

* **Problem:** (a) Role is read from the JWT: a demoted admin keeps admin rights until the token expires or their password changes. (b) `POST /api/logout` clears the cookie but does not revoke the token. (c) Other open browser tabs are not told when one tab signs out and show errors until reload. (d) After an auth-guard sign-out the user lands on `/` and loses their deep link.
* **Required change:**
  1. `require_admin`/`get_is_admin`: after the token check, use the role from the cached per-player record (`services/session_service.py` session cache already holds the player row's token version; extend the cached tuple with `role`) and reject when the token claims admin but the stored role is not admin. Keep the token role only as a fast negative filter.
  2. Logout: bump `token_version` (same `update_player_profile(..., bump_token_version=True)` path) so a captured token dies; keep the cookie clear. Do not bump on every 401-triggered guard logout (the token is already dead); only on explicit user logout.
  3. Client: listen for the `storage` event on `nfl_wins_token`/`nfl_wins_my_player_id` removal and run the same sign-out UI in other tabs.
  4. Client: the auth guard should preserve the current path (`?next=`, validated as same-origin path) and return there after sign-in.
* **Tests:** demoted admin with a still-valid token gets 403 within one cache TTL (30 s) and immediately in the same process after the role change path invalidates the cache; logout revokes; source-level tests for the storage listener and the `next` validation (open-redirect rejection: only paths beginning with a single `/`).

### A3. Small auth wording and safety items (S)

* MFA verify: a known player with a pending code and a wrong guess returns "Incorrect verification code." while unknown ids and expired codes return "MFA code expired or invalid.". Decide and implement one policy: either keep (document that pending-code existence is observable only by someone who already logged in with the correct password) or unify both to the generic message and adjust the frontend to show "Code incorrect or expired".
* Firestore document `get()` in the session cache has no explicit timeout; add a bounded timeout (for example 3 s) so a Firestore brownout degrades to fast 503s instead of slow auth.
* `_reset_session_cache` and expired-entry eviction: cap the session cache size (LRU or periodic sweep) to make growth bounded even against a valid-token flood.

---

## Phase B: Data consistency and correctness (small, low risk)

### B1. Cross-instance cache signals for draft-order writers (S)

* **Problem:** `services/db_service.py::delete_draft_results_for_season` (~line 368) and `add_draft_order` (~line 420) call `clear_data_cache(DOMAIN_STATIC)` but not `signal_data_update(DOMAIN_STATIC)`, unlike sibling writers (`delete_season_data`, `add_draft_rule`, the player writers). Other server instances can serve stale draft data until an unrelated signal.
* **Required change:** add the signal after the write in both; consider one small helper (`_invalidate_static()` = clear + signal) and replace the duplicated pairs across the file only where the change is a pure refactor with identical behavior.
* **Tests:** patch `signal_data_update` and assert it is called once for each writer; existing writer tests unchanged.

### B2. Cache-fill race guard for the remaining domains (M)

* **Problem:** `services/data_service.py::_get_static_bucket` got a generation-counter guard so a fill that started before a `clear_domain` is not stored (see `tests/test_static_cache_race.py`). The active-season and historical bootstrap paths use the same "read, then store with a later timestamp" pattern and are unguarded.
* **Required change:** extract the guard into one shared helper in `services/cache_service.py` and apply it to every domain fill; no public signature changes.
* **Tests:** a deterministic interleaving test per domain mirroring the existing static one.

### B3. Tests must not write into a developer's `.local_db/` (M)

* **Problem:** when `.local_db/` exists in the working directory, some tests write to it (observed: a test overwrote a pick in `.local_db/draft_results.pkl`, corrupting local demo data and skewing results). Tests also create an empty `.local_db/` in fresh checkouts.
* **Required change:** an autouse fixture in `tests/conftest.py` that runs each test in a temp working directory or monkeypatches the local-db path resolution (`pathlib.Path(".local_db")` usages in `services/db_service.py`, `services/cache_service.py`, `services/data_service.py`) to a per-test `tmp_path`; a guard test that fails if a test run modifies a sentinel file placed in a real `.local_db/`. Coordinate with the 34 currently environment-dependent tests: after this change they should either skip cleanly without local data or build minimal fixtures in `tmp_path` (consolidated spec section 0, item 5).
* **Tests:** the sentinel guard; the full suite run twice back-to-back produces identical results and leaves `.local_db/` unchanged.

### B4. Admin Pool tab default season (S)

* **Problem:** `static/js/admin_pool.js` defaults to the first season returned by `/api/admin/seasons` (newest with draft data), not the active season.
* **Required change:** include `active_season` in the `/api/admin/seasons` response (`routes/admin_routes.py::fetch_admin_seasons` already has access to games/draft data; use `data_service.get_active_season`) and have the tab default to it when present in the list.
* **Tests:** route test for the new key; source-level test that the script prefers it.

### B5. Housekeeping items noticed in production output (S)

* `SMTP_USER` on the live service is the placeholder `your_email@gmail.com` (legacy path; Resend is primary). Either remove the legacy SMTP env vars and code path if unused, or set real values. Confirm with a grep for `SMTP_` usage before removing.

---

## Phase C: Test and code hygiene (consolidated spec Tracks 3 and 5)

Carry over unchanged from `2026-09-26-consolidated-followups-and-tech-debt.md`; do them as batched mechanical work, one worktree, reviewed per batch:

* **Track 3 (e2e hygiene):** 3.1 items 2-5 (vacuous win-math assertion, `_poll()` swallowing exceptions, season 3000 assertion, pick-queue window constant), 3.2 (dialog handling standardization), 3.3 (nav-parity `_backgroundSync` settling and the season note), 3.4 (test-accounts toggle placement and badge, distinct admin e2e password, restore player 9 phone, skip when 2024 W1 data missing, module-scoped admin login across tab tests, shared `_connect_all_players`, plan housekeeping, `DISABLE_AI_GENERATION` gate). The e2e suite has not been run since the 2026-09-26 sprint edits (`tests_e2e/test_history.py` link selector, `test_profile.py` comments, Teams nav entry): run it once first and fix any drift before starting the hygiene batch.
* **Track 5 (17 hygiene items):** the checklist table in the consolidated spec (5.1 through 5.17). Note 5.14 is already done; 5.16 is done; 5.17 (emoji in the recap heading) needs a product decision.
* **Acceptance:** full suite id comparison plus one Playwright run.

## Phase D: Model pipeline and documentation (Tracks 4 and 6)

* **Track 4:** 4.2 force-promotion durability and audit logging (`services/*_prediction_service.py`, `services/model_promotion.py::save_versioned`), 4.3 pipeline script wiring (feature_version into `write_prediction_features`, `_model_version_string` replacement, guard style alignment), 4.4 roster ingestion guards (missing `week` column in `weekly_rosters`, deterministic starter dedupe, QB availability docstring, LR coefficient table annotation), 4.6 feature-version and promotion test gaps. 4.5 (resimulation lead profiling) is now partly answered: the resimulation had never executed because of a missing IAM permission (fixed 2026-09-27); once real resim executions exist, measure their runtimes with `gcloud run jobs executions list --job=winspool-predict-daily` and confirm the 30 minute lead (`RESIMULATE_LEAD_MINUTES` in `scripts/schedule_kickoffs.py`) leaves at least 2x the measured runtime.
* **Track 6 (docs):** items 1-4 of the consolidated spec (DEPLOY.md rate-limit wording, deployment.md Firebase Hosting note, CLAUDE.md canonical `normalize_team_abbr`/`TEAM_ABBR_MAP` and `--force-promote`/`GIT_SHA` notes, `auth_routes.py` comment fixes and `_MFA_MAX_ATTEMPTS` placement). Add: document `deploy/alerts/kickoff-queue-attempt-failures.json` and the queue retry settings in `CLAUDE.md` Scheduled Jobs (currently only in DEPLOY.md).

---

## Phase E: Operator actions (no code)

* Push commits and tidy stale worktree registrations (`.claude/worktrees/empty-standings-fix` is an untracked leftover directory; other worktrees were removed).
* Close or update GitHub issues #101, #56, #64, #83, #87 (#83 and #87 were implemented: player outlook and pool fee tracker).
* Verify after each deploy: admin screen without sign-out cycle; resim executions appear in the job list at kickoff minus 30 minutes; the kickoff queue has no tasks with non-zero failed dispatches.
* Decide on VAPID keypair rotation (the old private key lived in a plain env var; rotation forces every browser to re-subscribe to push).

## Explicit non-goals

* Changing the JWT lifetime (7 days), the single-instance deployment assumption (`--max-instances=1`), or the auth-guard's broad `localStorage.clear()` on sign-out.
* Moving off the static-cache architecture.

## Suggested order

A1 (security, before the 2027 draft) -> B1, B3 (quick correctness and test safety) -> A2, A3 -> B2, B4, B5 -> Phase C -> Phase D -> Phase E throughout.
