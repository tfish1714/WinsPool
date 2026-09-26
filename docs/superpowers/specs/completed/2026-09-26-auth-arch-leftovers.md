# Leftovers from the Auth Hardening and Arch Tech Debt Run

**Date:** 2026-09-26
**Status:** Design, not yet planned or implemented.
**Origin:** The run that merged `worktree-auth-hardening` (`72712cd`) and
`worktree-arch-tech-debt` (`b38b3db`) into local `main`. This spec collects
everything that run deliberately did not do, plus findings and deferred minors
raised by its task and whole-branch reviews. Nothing here blocks the merged work.

Companion specs (still open where noted):
`2026-09-26-auth-hardening-followups.md` (section 3 open),
`2026-09-26-cleanup-followups.md` (sections 2 and 3 open).

## 0. Release housekeeping (not code)

Nothing from the run has left the machine. Owner actions:

1. Push `main` (or open PRs) for the two merges. The GitHub MCP server failed to
   connect during the run, so no PR was created and no issue was commented.
2. Close or comment GitHub #101, #56, #64 after the push. Record in the #43
   thread that profile update and MFA verify now share the login limiter.
3. Put the Firebase Hosting verification result in whatever PR/commit note is
   used: on 2026-09-26 `https://winspool.web.app` returned 404 "Site Not Found";
   `firebase.json` was reduced to `{}` (Retire disposition).
4. Prune stale worktrees. `git worktree prune` failed on OneDrive locks for
   `.claude/worktrees/_tmp_main_check` and `.claude/worktrees/empty-standings-fix`.
   Merged worktrees `auth-hardening`, `arch-tech-debt` and `auth-security-and-push`
   remain on disk; remove them and their branches once OneDrive releases the locks.
5. Baseline test health: the suite has 29 failures and 5 errors on `main` in this
   environment (worktrees carry only a partial `.local_db`; the main checkout's
   `.venv` also lacks pytest, so tests run with plain `python -m pytest`). The
   failing set is unchanged by the run, but it should be triaged so a real
   regression is not hidden inside it (see section 6).

## 1. Still-open items from the original specs (not attempted)

| Source | Item | Notes |
|--------|------|-------|
| auth spec section 3 | VAPID private key is a plaintext Cloud Run env var | Operator creates Secret Manager secret; `deploy/deploy.ps1` moves it to `--set-secrets`, one-time `--remove-env-vars VAPID_PRIVATE_KEY`; extend `tests/test_deploy_config.py`; decide rotate-now vs defer and record in DEPLOY.md. Do not run `deploy.ps1` from an automated session. |
| cleanup spec section 2 | `save_push_subscription` reports failure after a successful write when `_invalidate_players_cache()` raises; same pattern in the prune path of `_deliver` | Guard the invalidation in its own try/except (WARNING with player id); return value follows the write only. Tests in the cleanup spec. |
| cleanup spec 3a-3f | Docstring for `team_records` param; unused `import inspect` in `tests/test_admin_routes.py`; `_reference_lookup` docstring; `model_promotion._fmt` accept `numbers.Real` (bool still `n/a`); `get_limiter` docstring; unused `import pytest` in `tests/test_rate_limit_service.py` | One commit. Check each "only if genuinely unused" condition first. |

## 2. Docs and comment drift introduced or left by the run

1. `DEPLOY.md` (~line 98): the `AUTH_RATE_LIMIT_PER_MINUTE` description lists
   login, set_password and profile/update but not `/api/mfa/verify`. One line.
2. `routes/auth_routes.py` (~lines 40-43): the comment on `_login_limiter` says only
   login and set_password share the bucket; it now also covers profile/update and
   mfa/verify. `_MFA_MAX_ATTEMPTS` sits between the two limiter definitions;
   move it below them.
3. `docs/deployment.md` (~line 141) still says the default URL is
   `https://YOUR_PROJECT_ID.web.app`. Add one sentence that Firebase Hosting is not
   used and the app is served from the Cloud Run URL in `APP_BASE_URL`. (`docs/` is
   gitignored and was not present in the worktree, so the run skipped this.)
4. `scripts/generate_weekly_summary.py`: stale comment `# Create a simple HTML
   wrapper` above the `build_recap_html` call in `main()`.
5. `CLAUDE.md`: consider documenting that `normalize_team_abbr` is the only team
   normalizer, `TEAM_ABBR_MAP` lives in `services/constants.py`, and that the five
   Firestore scripts get their client via `get_db()` after forcing
   `USE_LOCAL_DATA=False`.

## 3. Test-quality follow-ups (deferred minors)

1. `tests/test_auth.py`: several multi-line `with patch(...), \` statements were
   joined onto one line with a run of spaces (in `TestProfileUpdateAuth` and the
   edited `TestMfaVerify` test, ~line 158). Reflow only; behavior identical.
2. `tests/test_hosting_cleanup.py::test_firebase_json_has_no_rewrites_to_missing_service`
   returns early when no `hosting` block exists, so it now asserts nothing and
   duplicates `test_firebase_json_has_no_hosting_block`. Either make it meaningful
   (fail if any `run.serviceId` other than `winspool` appears anywhere in the JSON)
   or fold it into the other test.
3. `tests/test_team_abbr_consolidation.py::test_no_second_abbreviation_dict_outside_constants`
   detects only the literal `"JAC": "JAX"` spelling. Replace the substring scan with
   an AST scan for dict literals whose keys include `"WSH"` and `"JAC"`, so
   reformatted or multi-line duplicates are caught.
4. `tests/test_historical_projection_routes.py`:
   `test_player_profile_shows_consensus_projection_for_historical_season` and
   `test_service_get_player_analytics_data_uses_consensus_projection` now exercise
   the same code. Keep both (zero-deletion) but note the overlap in one docstring,
   or make the older one assert the route-level response.
5. `tests/test_player_analytics.py` (~lines 165-177): patches on
   `routes.history_routes.load_data` / `get_season_projection_legacy_shape` are inert
   for the analytics path. They exist only so old decorators resolve. Once the owner
   is comfortable relaxing zero-deletion for dead patches, drop the inert patches and
   the dead `get_season_projection_legacy_shape` import in `routes/history_routes.py:8`.
6. `tests/test_db_service_init_firebase.py` and the script no-credential tests patch
   `pathlib.Path.exists` globally inside their `with` block. Narrow the patch to the
   credentials path (`patch.object` on a helper, or `monkeypatch` a module-level
   path constant) so an unrelated `exists()` call cannot be affected.
7. `tests/test_script_firebase_init.py::test_init_fails_loudly_without_credentials`
   stubs `get_db` to `None`; that name overstates it now that a real-path test
   exists. Rename to `..._when_get_db_returns_none`.
8. The new script no-credential tests set `os.environ["USE_LOCAL_DATA"]` through the
   scripts themselves. The autouse `mock_env_vars` fixture restores it via
   monkeypatch, but confirm no leak by running the file in isolation and in random
   order (`-p random_order` if available).

## 4. Security and behavior observations (pre-existing or design-accepted)

Raised by the whole-branch auth review; none regress the merged work, all are
worth a decision.

1. **Profile update error message.** `templates/profile.html` reads `result.error`,
   but `require_auth` returns `{"detail": ...}` on 401, so a stale session shows the
   generic "Error: Update failed." Fix in the page (fall back to `result.detail`, and
   ideally redirect to login on 401). Response shape stays as is.
2. **`/api/mfa/verify` returns 404 for an unknown `playerId`**, letting a caller
   enumerate player ids (now rate limited). Consider returning the same 401 "MFA code
   expired or invalid." for unknown ids. Existing test
   `test_mfa_verify_unknown_player_returns_404` would need to be updated, so this is a
   deliberate behavior change; decide before doing it.
3. **Sessions are not revoked after a password change.** A token minted before a
   password reset remains valid until expiry. Options: store a `token_version` on the
   player and check it in `require_auth`, or shorten the expiry. Needs its own design.
4. **MFA attempt counter is a non-atomic read-modify-write.** Acceptable with the
   single-instance pin and `async def` serialization (auth spec review focus). If
   `--max-instances` is ever raised, move the counter to a Firestore transaction or
   `firestore.Increment`.
5. **`update_profile` ordering.** `require_auth` runs before the per-IP limiter, so
   unauthenticated floods get a cheap 401 without consuming the bucket. Harmless (no
   password oracle without a session) but it departs from "limiter is the first
   statement". Either accept and record it, or move auth inside the handler after the
   limiter.
6. **Recap email HTML interpolates `summary_text` unescaped.** The summary is
   model-generated text sent to pool members; wrap it in `html.escape` (preserving
   the `white-space: pre-wrap` styling) unless intentional markup is expected.
   Verify against a real recap before changing.
7. **Brute force headroom.** An attacker holding a password gets about 4 MFA guesses
   per issued code, and login plus verify share 5 requests/min/IP. Accepted in the
   auth spec; revisit if pool size or exposure changes.

## 5. Refactor follow-ups

1. **Private helper import.** `scripts/generate_weekly_summary.py` imports the
   private `email_service._app_base_url`. Promote it to a public name (keep the
   underscore alias) or add a small `services/config.py::app_base_url()`; update the
   three existing users.
2. **Import-time Firebase initialization in scripts.** The five scripts now import
   `services.db_service` at module load, which runs `_init_firebase()` when
   `USE_LOCAL_DATA` is not `true`. With valid credentials a `--dry-run` therefore
   initializes the Firebase app (no network call); with malformed
   `FIREBASE_CREDENTIALS` it raises at import even on a dry run. Consider making
   `db_service`'s module-level `_init_firebase()` lazy (first `get_db()` call). That
   is a change to a core module, so it needs its own test pass over every importer.
3. **`get_db()` no-credentials contract.** With no credentials and
   `USE_LOCAL_DATA=False`, `get_db()` raises `ValueError` (from `firestore.client()`)
   rather than returning `None`, while `_init_firebase()` returns `None`. The five
   scripts now catch `ValueError` individually. Decide on one contract for `get_db()`
   (return `None` when initialization returned `None`; every current caller already
   checks for `None`), then delete the five `try/except ValueError` shims. Web-app
   behavior with missing credentials would change from a 500 to the existing
   `None` handling, so verify the callers first.
4. **Five near-identical script init wrappers.** Each of the five scripts still has
   its own 8-line `get_db()` wrapper (force `USE_LOCAL_DATA`, call, handle missing).
   If item 3 lands, collapse these into one helper such as
   `services.db_service.require_db(exit_on_missing=True)` and keep the old function
   names as one-line delegators (tests patch the names).
5. **Team abbreviations in stored data.** `draft_results` for 2017-2019 still stores
   `OAK` while consensus data and `normalize_team_abbr` use `LV`. Pre-existing and
   outside the run, but joins between the two will miss those rows. Decide between a
   backfill of `draft_results` and a normalize-on-read in the join path; check
   `scripts/seed_consensus.py` and `scripts/migrate_consensus.py`, which now map
   OAK to LV.
6. **Emoji policy.** The run's zero-emoji rule conflicts with one pre-existing emoji
   in the recap email heading in `scripts/generate_weekly_summary.py`
   (`build_recap_html`). It was preserved byte-for-byte on purpose. If the rule
   should apply repo-wide, replace it and update the `test_hosting_cleanup.py`
   assertions only if they reference the heading (they do not today).

## 6. Baseline test failures to triage

29 failures and 5 errors exist on `main` in this environment. The first review of
the merged work showed the set is identical before and after, and one baseline
failure (`test_api_player_analytics_returns_200`) was fixed as a side effect of the
patch-target change. Triage should:

1. Run the suite in the main checkout (full `.local_db`, real `.venv` with pytest)
   and record which failures remain; many are expected to be `.local_db`-partial
   worktree artifacts (see `reference_worktree_workflow_gotchas`).
2. For the real remainder, file issues or fix. A recurring example:
   `test_draft_history_route_renders`.
3. Consider making the test suite self-sufficient (fixtures build a minimal
   `.local_db` in a tmp dir) so worktrees stop producing false failures.

## Suggested sequencing

1. Section 0 items 1-4 (owner actions, no code).
2. One doc/comment/test-hygiene commit: sections 2, 3 (items 1, 2, 3, 6, 7, 8) and
   cleanup spec 3a-3f. Zero risk.
3. Cleanup spec section 2 (push save result) plus its tests. Small, code, independent.
4. Auth spec section 3 (VAPID). Infrastructure; operator involved.
5. Section 4 items 1 and 6 (profile.html error text, recap escaping). User-visible,
   small.
6. Section 5 items 3, 4, 2 as one design-first change (get_db contract, wrapper
   collapse, lazy init), because they touch the same core module.
7. Section 4 items 2, 3 (enumeration, session revocation) each need a short design
   decision before planning.
8. Section 6 triage in parallel with any of the above.

## Review focus

- Section 5 item 3: any web-app code path that today relies on `get_db()` raising
  when credentials are missing (for example startup or health checks) would silently
  change if `get_db()` returns `None`.
- Section 4 item 2: changing 404 to 401 alters an existing test's expectation and
  the front end's message for a mistyped id; confirm the UI path cannot send an
  unknown `playerId`.
- Section 3 item 5 and 6: relaxing zero-deletion for inert patches needs the owner's
  explicit approval; do not do it in a hygiene commit by default.
- Section 5 item 5: a backfill of `draft_results` is a data change on production
  Firestore; treat it as an operator action with a dry run first.
