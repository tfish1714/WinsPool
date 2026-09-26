# Batch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close four small open items from the first sprint (Tuesday eval re-grade, fail-open projection gate, eval test gaps, db_service leftovers) and three security items from the consolidated backlog (session revocation on password change, MFA player-ID enumeration plus profile error handling, recap email HTML injection).

**Architecture:** Small targeted changes with tests first; no new subsystems. Session revocation uses an integer `token_version` on player documents embedded in the JWT as `tv`.

**Tech Stack:** FastAPI, PyJWT, pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-consolidated-followups-and-tech-debt.md` sections 1.2, 1.3, 1.4, 5.16 plus the sprint report's open items.

## Global Constraints

- NO emojis in anything written. (An existing emoji in the recap email heading is pre-existing: do not touch it.)
- Zero deletion of existing tests/features: additive or exact replacements only (a test whose contract changes is renamed/rewritten, never dropped).
- TDD: failing test first.
- Never deploy, never write Firestore, never `git add` anything under `.superpowers/`, `.local_db/` or `*.png`. Docs need `git add -f`. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Work only in `C:\Users\fisch\OneDrive\Documents\Code\WinsPool\.claude\worktrees\batch-hardening` (branch `sprint-batch-hardening`). Baseline: `python -m pytest tests/ -n auto -q -p no:cacheprovider --color=no` has 29 failed + 5 errors pre-existing (environment-caused; ids in the SDD workspace `baseline_failure_ids.txt`); compare test ids only. Tests must not write into a developer's `.local_db`: use `tmp_path`/monkeypatch in new tests.

## Review Focus

- Session revocation: tokens issued before this change (no `tv` claim) must keep working until the player's password changes (treat missing `tv` as 0 and stored missing version as 0); after a password change the OLD token is rejected with 401 but the user who just changed their own password stays logged in (fresh token and cookie in that response); admin-initiated password resets/temp-password sets also revoke; a token for a deleted player is rejected.
- The token-version check must apply in every place a token is trusted: `require_auth`, `require_admin`, `get_is_admin`, and any other `decode_token` consumer (grep for it, including websocket paths if any decode tokens).
- The MFA verify endpoint must return byte-identical status and body for an unknown player id and for a known player with a wrong or expired code, so player ids cannot be enumerated.
- Eval skip: a manual `weekly_model_eval.py` run without the new flag must still overwrite; the Tuesday step must not re-grade a week that already has a snapshot but must still grade a week that has none.
- The projection gate must fail closed (treated as draft active for non-admins) when the config document cannot be read in remote mode, but local dev mode (`USE_LOCAL_DATA=True`) must keep its current behavior.

---

## Task 1: Reliability items

**Files:** `scripts/weekly_model_eval.py`, `scripts/cache_builder.py`, `services/db_service.py`, `routes/api_routes.py` (outlook gate ~line 601), `routes/history_routes.py` (team page gate ~line 265), `tests/test_cache_builder.py`, new tests as needed.

**Interfaces:**
- Produces: `weekly_model_eval.py --skip-existing` flag: before any model or feature work, if the `nn_weekly_accuracy` store (see `services/cache_service.py`) already has a snapshot row for the requested season and week(s), print a skip line and exit 0 without touching the store; without the flag behavior is unchanged (overwrite/upsert). `cache_builder._run_weekly_eval_if_tuesday` passes `--skip-existing`.
- Produces: `db_service.is_draft_active_fail_closed() -> bool`: True when `config/settings.draft_active` is truthy; in remote mode (`USE_LOCAL_DATA` not true) also True if `get_db()` is None or the read raises; in local mode identical to `get_config_settings().get("draft_active")`. Used at both projection gates (api_routes outlook and history_routes team page); admins still bypass.

- [ ] Tests first for each: eval skip (existing snapshot -> skipped with exit 0 and the store not written; none -> proceeds; no flag -> proceeds even when a snapshot exists; cache_builder command includes `--skip-existing`); gate fail-closed (remote mode with `get_db` None -> True; read raising -> True; local mode -> mirrors config; both routes hide projections for non-admins in that state and still serve admins).
- [ ] Strengthen the Tuesday eval tests in `tests/test_cache_builder.py`: assert the 900 second timeout and the `"weekly eval"` label are passed to `_run_subprocess_step`, and make the ordering test prove the eval step runs BEFORE ML model loading (record call order via a marker inside the patched `NNPredictionService`, or a shared call log).
- [ ] `require_db`: log the message once and print it once (keep the console print for CLI visibility; drop the duplicate `logger.error` or make the logger the single source: choose one, update any test that asserted the other). Remove the unused module variable `use_local_env` in `db_service.py` after grepping the whole repo (including tests and scripts) to confirm nothing imports it; if something does, leave it and say so.
- [ ] Run targeted tests (`tests/test_cache_builder.py tests/test_db_service_lazy_init.py tests/test_script_firebase_init.py tests/test_portfolio_projection.py tests/test_team_page.py tests/test_player_page.py` plus new files), then commit.

## Task 2: Security items

**Files:** `services/session_service.py`, `services/db_service.py` (password-write functions), `routes/auth_routes.py`, `routes/admin_routes.py` (password reset/temp password), `templates/player_profile.html` (profile form error handling), `scripts/generate_weekly_summary.py`, `tests/test_auth.py` and new tests, `CLAUDE.md`.

**Interfaces:**
- Produces: `session_service.create_token(player_id, role, token_version: int = 0)` embedding `"tv": token_version`; a helper (for example `session_service.token_is_current(payload, player) -> bool`) comparing `int(payload.get("tv", 0))` with `int(player.get("token_version", 0) or 0)`; `db_service` password-changing writers bump `token_version` by 1 atomically with the password write (same document update) and keep the cached players frame in sync the way existing writers do.
- Produces: in `auth_routes`, every place a token is minted (`login`, MFA verify success, `set_password`, `profile/update`) passes the player's current version; `profile/update` and admin resets that change a password: for the user's own change, return a fresh token in the body and set the cookie via `_set_session_cookie`.

- [ ] 1.2 Session revocation: implement per the interfaces and Review Focus (grep every `decode_token` and every password write: `update_player_credentials`, `set_password`, profile password change, admin reset, forced change flow). Tests: old token rejected after a password change (401) on `require_auth`, `require_admin` and `get_is_admin`; a token without `tv` still works when stored version is 0; fresh token from profile/update works; admin reset revokes the target's tokens.
- [ ] 1.3a `routes/auth_routes.py::verify_mfa`: an unknown `playerId` returns the same status code and body as a wrong/expired code (401 with the existing "MFA code expired or invalid." message). Rename and rewrite `tests/test_auth.py::test_mfa_verify_unknown_player_returns_404` to assert 401 with the same body as the wrong-code case (do not delete the test's intent) and add an assertion that the two responses are identical.
- [ ] 1.3b Player page profile form (`templates/player_profile.html`, the submit handler that posts to `/api/profile/update`): show `result.error || result.detail || "Update failed"` and on HTTP 401 send the user to the app's sign-in entry (check what exists: prefer an existing login route; if none, clear the stored token and go to `/wins-pool`, which shows the login wall). Keep the existing alert wording for other cases and all element ids. Add a source-level test asserting the handler references `result.detail` and handles 401.
- [ ] 1.4 `scripts/generate_weekly_summary.py::build_recap_html`: wrap the model-generated `summary_text` in `html.escape` while keeping the `white-space: pre-wrap` styling; remove the stale `# Create a simple HTML wrapper` comment above the `build_recap_html` call. Tests: `<script>` and `&` in summary text are escaped in the output; newlines preserved.
- [ ] Update `CLAUDE.md` Auth section (token_version/tv contract and its one-minute multi-instance cache lag). Run targeted tests (`tests/test_auth.py tests/test_session_service*.py tests/test_admin_routes.py` plus new files), commit.
