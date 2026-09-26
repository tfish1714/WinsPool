# Consolidated Tech Debt, Cleanup, and Test Hardening Follow-Ups

**Date:** 2026-09-26  
**Status:** Unified Backlog and Design Specification.  
**Purpose:** Single authoritative, 100% exhaustive source of truth consolidating all uncompleted follow-ups, minor cleanups, architectural notes, security observations, and test hardening items across all partially completed specifications.

### Consolidated Source Specifications Superseded by This Document
The following partial specifications have had all of their remaining open items extracted into this document and are eligible for archiving in `docs/superpowers/specs/completed/` once reviewed:
1. `docs/superpowers/specs/2026-09-12-e2e-test-suite-hardening-followups.md`
2. `docs/superpowers/specs/2026-09-13-admin-flows-e2e-followups.md`
3. `docs/superpowers/specs/2026-09-17-qb-availability-followup-cleanup.md`
4. `docs/superpowers/specs/2026-09-18-feature-version-stamp-followup-cleanup.md`
5. `docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md` (Item 5: RESIMULATE_LEAD_MINUTES timing)
6. `docs/superpowers/specs/2026-09-26-auth-hardening-followups.md` (Section 3: VAPID Secret Manager migration)
7. `docs/superpowers/specs/2026-09-26-cleanup-followups.md` (Sections 2 and 3: push service error isolation and small tidy-ups)
8. `docs/superpowers/specs/2026-09-26-auth-arch-leftovers.md` (All sections 0 through 6)

---

## 0. Owner Release Housekeeping (Operator Actions - No Code)

1. **Push Local Commits & Open PRs:**
   * Push `main` commits from recent merges (`72712cd`, `b38b3db`, `23421a5`, `425c59f`).
2. **Issue Tracking:**
   * Close or comment on GitHub issues #101, #56, and #64.
   * Update GitHub issue #43 thread to record that `/api/profile/update` and `/api/mfa/verify` now share the login rate limiter bucket.
3. **Firebase Hosting Verification Record:**
   * Document in deployment notes that on 2026-09-26 `https://winspool.web.app` returned HTTP 404 ("Site Not Found") and `firebase.json` was reduced to `{}` (Hosting retired in favor of Cloud Run).
4. **Worktree Cleanup:**
   * Prune stale git worktrees (`auth-hardening`, `arch-tech-debt`, `auth-security-and-push`, `.claude/worktrees/_tmp_main_check`, `.claude/worktrees/empty-standings-fix`) once OneDrive file locks release.
5. **Baseline Test Suite Triage:**
   * Run test suite on main checkout with complete `.local_db` and pytest virtualenv to isolate the 29 failures / 5 errors caused by partial worktree state vs true application defects (e.g., investigate `test_draft_history_route_renders`).
   * Evaluate making test fixtures self-sufficient (e.g. building minimal `.local_db` in `tmp_path`) so worktrees stop generating false test failures.

---

## 1. Track 1: Security, Infrastructure & Deployment

### 1.1 VAPID Private Key Migration to Secret Manager
* **Origin:** `2026-09-26-auth-hardening-followups.md` Section 3; `2026-09-26-auth-arch-leftovers.md` Section 1.
* **Problem:** In `deploy/deploy.ps1:80`, `VAPID_PRIVATE_KEY` is currently passed in `$envVars` via `--update-env-vars` as plaintext.
* **Requirements:**
  1. Operator provisions `vapid-private-key` in Google Cloud Secret Manager.
  2. Modify `deploy/deploy.ps1` to bind `VAPID_PRIVATE_KEY` via `--set-secrets="VAPID_PRIVATE_KEY=vapid-private-key:latest"`.
  3. One-time Cloud Run command `--remove-env-vars="VAPID_PRIVATE_KEY"` to remove plaintext environment variable. Do not run `deploy.ps1` from an automated session.
  4. Update `tests/test_deploy_config.py` to assert secret binding.
  5. Document rotation decision (rotate now vs defer) in `DEPLOY.md`.

### 1.2 Session Revocation on Password Change
* **Origin:** `2026-09-26-auth-arch-leftovers.md` Section 4.3.
* **Problem:** Existing JWT tokens minted before a password reset remain valid until expiration.
* **Requirements:**
  * Add an integer `token_version` to player documents in Firestore.
  * Embed `token_version` in minted JWT payload; increment upon password change/reset.
  * In `services/session_service.py::require_auth`, verify `token_version` matches stored player record.

### 1.3 Profile Update Error Handling & MFA Code Enumeration Hardening
* **Origin:** `2026-09-26-auth-arch-leftovers.md` Section 4.1, 4.2.
* **Requirements:**
  * In `templates/profile.html` (~line 173), update error handling to inspect `result.detail` in addition to `result.error`, and redirect to `/login` upon 401. Response shape stays as-is.
  * In `routes/auth_routes.py::verify_mfa`, normalize response for unknown `playerId` from 404 to 401 ("MFA code expired or invalid.") to prevent player ID enumeration. Update `tests/test_auth.py::test_mfa_verify_unknown_player_returns_404` to reflect the 401 response. Verify frontend cannot submit malformed/unknown IDs.

### 1.4 Recap Email HTML Injection Guard
* **Origin:** `2026-09-26-auth-arch-leftovers.md` Section 4.6.
* **Requirements:**
  * In `scripts/generate_weekly_summary.py` (`build_recap_html`), wrap model-generated `summary_text` in `html.escape` while preserving `white-space: pre-wrap` styling. Verify against real recap output.

### 1.5 Security & Architecture Invariants (Informational / Future Scaling)
* **Origin:** `2026-09-26-auth-arch-leftovers.md` Section 4.4, 4.5, 4.7.
* **Invariants:**
  * **MFA Attempt Counter Atomicity:** The non-atomic read-modify-write on MFA attempt counter is safe while production is pinned to `--max-instances=1` and `async def`. If instance count is ever increased, migrate to Firestore transactions or `firestore.Increment`.
  * **`update_profile` Limiter Ordering:** `require_auth` runs before the per-IP limiter, allowing unauthenticated floods a cheap 401 without consuming the bucket. Harmless, but departs from "limiter is the first statement". Document or accept.
  * **Brute Force Headroom:** 4 MFA guesses per issued code, with login + verify sharing 5 req/min/IP. Revisit if pool size or external exposure increases.

---

## 2. Track 2: Core Data & Push Services

### 2.1 Push Subscription Error Isolation
* **Origin:** `2026-09-26-cleanup-followups.md` Section 2; `2026-09-26-auth-arch-leftovers.md` Section 1.
* **Problem:** In `services/push_service.py::save_push_subscription()`, `_invalidate_players_cache()` runs inside the primary try block. If cache invalidation raises, it logs an error and returns `False` despite a successful Firestore write. Same issue exists in `_deliver()` pruning.
* **Requirements:**
  1. Isolate `_invalidate_players_cache()` in its own `try/except Exception` logging a WARNING with player ID; return value strictly reflects write success.
  2. In `_deliver()` pruning, treat invalidation failure after successful deletion as still successfully `"pruned"`.
  3. Add unit test coverage in `tests/test_push_service.py`: write succeeds / invalidation raises returns `True`; write raises returns `False`; delete succeeds / invalidation raises returns `"pruned"`.

### 2.2 Database Service Initialization Contract & Script Deduplication
* **Origin:** `2026-09-26-auth-arch-leftovers.md` Section 5.1, 5.2, 5.3, 5.4.
* **Problem:** Scripts catching `ValueError` on missing credentials; `get_db()` raises `ValueError` from Firestore client instead of returning `None`; redundant 8-line Firestore init wrappers duplicated across 5 scripts.
* **Requirements:**
  1. Make `services/db_service.py` Firebase initialization lazy (defer `_init_firebase()` to first `get_db()` call) so `--dry-run` and test imports without credentials do not raise on module import. Run test pass over all importers.
  2. Standardize `get_db()` contract: return `None` when credentials are missing or initialization fails, rather than raising `ValueError`. Verify callers handle `None`. Delete individual `try/except ValueError` shims in scripts.
  3. Introduce `services/db_service.py::require_db(exit_on_missing=True)` helper to collapse redundant setup blocks across `scripts/seed_consensus.py`, `scripts/migrate_consensus.py`, `scripts/backfill_schedule_predictions.py`, `scripts/generate_weekly_summary.py`, and `scripts/refresh_local_pkls.py`. Keep existing script function names as one-line delegators for test patching.
  4. Promote `email_service._app_base_url` to a public helper or move to `services/config.py::app_base_url()`, updating three existing call sites.

---

## 3. Track 3: E2E and Test Suite Hardening

### 3.1 Live Draft Suite HTTP 500 & Robustness Gaps
* **Origin:** `2026-09-12-e2e-test-suite-hardening-followups.md` Items 6, 8.
* **Problem:** `tests_e2e/test_live_draft.py::test_full_ten_player_live_draft` hits HTTP 500 on `/wins-pool/3000` after pick #30 because `services/analysis_service.py::calculate_wins_pool_standings` merges an empty standings frame for a fully drafted season with no games played (`GET /` redirects to `/wins-pool/3000`).
* **Requirements:**
  1. Add defensive guard in `calculate_wins_pool_standings` when schedule contains zero played games so a fully drafted season displays empty/zero standings without raising a 500.
  2. Fix vacuous win-math assertion in `test_live_draft.py`: either seed preseason predictions for season 3000 or explicitly scope the assertion's guarantees via comment.
  3. In `tests_e2e/test_live_draft.py::_poll()`, do not swallow exceptions with blanket `except Exception: pass`; capture and surface the last exception on timeout.
  4. Add explicit assertion that the live draft room is actually on season 3000.
  5. Decouple end-of-draft `"R3" in last_row` pick-queue check from the undocumented window-size constant in `static/js/ui_renderer.js` (`#pick-queue`).
  6. Add `@pytest.mark.slow` marker to `test_live_draft.py` to allow skipping the 2-3 minute test during rapid local iteration while running in deploy pre-flight gates and CI.

### 3.2 Dialog-Handling Standardization Across E2E Tests
* **Origin:** `2026-09-13-admin-flows-e2e-followups.md` Item 2; `2026-09-12-e2e-test-suite-hardening-followups.md` Item 6.
* **Requirements:**
  * Standardize on `_record_dialogs` everywhere; retire `_click_through_two_dialogs` and replace lingering one-shot `page.once("dialog", ...)` handlers in `tests_e2e/test_forced_password_change.py` and `tests_e2e/test_profile.py`.
  * Add message-content assertions where a dialog's specific wording distinguishes success from failure.

### 3.3 Nav Parity Background-Sync Settling & Offseason Documentation
* **Origin:** `2026-09-12-e2e-test-suite-hardening-followups.md` Items 2, 3.
* **Requirements:**
  * In `tests_e2e/test_nav_parity.py`, wait for `static/js/main.js`'s asynchronous `_backgroundSync()` to settle before capturing drawer navigation links.
  * Add docstring note documenting that drawer navigation uses `get_active_season()` while desktop navigation renders the calendar year (`new Date().getFullYear()`).

### 3.4 Admin E2E Test Suite Optimization & Test Accounts
* **Origin:** `2026-09-12-e2e-test-suite-hardening-followups.md` Items 4, 7; `2026-09-13-admin-flows-e2e-followups.md` Items 7, 8, 9, 10, 11.
* **Requirements:**
  * Move `#show-test-accounts-toggle` in admin portal to a persistent header control or the Players tab itself, and add a visual badge/tag on test-account rows.
  * In `scripts/seed_e2e_test_players.py`, generate a separate, distinct password for admin account `e2e-test-01` via `secrets.token_urlsafe()`.
  * In `tests_e2e/test_admin_player_management.py`, restore player 9's phone number in `restore_player_9` fixture teardown (currently leaves `555-0199`).
  * In `tests_e2e/test_admin_members_and_recap.py`, skip with `pytest.skip()` if 2024 Week 1 data is missing in `.local_db/`.
  * In `tests_e2e/test_admin_tabs.py`, optimize by reusing a module-scoped authenticated browser context across the 7 parametrized tab tests to eliminate 9 redundant logins.
  * Deduplicate the 10-player connection loop between `test_admin_draft_overrides.py` and `test_live_draft.py` into a shared `_connect_all_players` helper.
  * Housekeeping: check off completed tasks in `docs/superpowers/plans/2026-09-09-ui-tests-admin-flows.md` and move to `plans/completed/`.
  * Add `DISABLE_AI_GENERATION` test-mode gate (mirroring email-sending gate) so recap generation can be tested beyond prompt preview.

---

## 4. Track 4: Feature Engine & ML Prediction Pipeline

### 4.1 Docker Build Layer Caching Optimization
* **Origin:** `2026-09-18-feature-version-stamp-followup-cleanup.md` Docker build efficiency section.
* **Requirements:**
  * In `Dockerfile.sync` and `Dockerfile.predict`, relocate `ARG GIT_SHA`/`ENV GIT_SHA` below the `RUN pip install` layer (immediately above `COPY . .`) so git commit SHA changes do not invalidate the pip dependency layer cache during builds.
  * Add `ARG GIT_SHA`/`ENV GIT_SHA` to the main web-service `Dockerfile` for preventive consistency.

### 4.2 Force-Promotion Durability and Audit Logging
* **Origin:** `2026-09-18-feature-version-stamp-followup-cleanup.md` Observability section.
* **Requirements:**
  * In `services/nn_prediction_service.py`, `services/xgb_prediction_service.py`, and `services/lr_prediction_service.py`, replace bare `except ValueError:` around the gate check with `except ValueError as e: logger.warning("... saving anyway: %s", e)` so overridden failure metrics are preserved in logs.
  * In `services/model_promotion.py::save_versioned()`, persist `"force_promoted": True` onto registry entries when the override fires.

### 4.3 Pipeline Script Wiring & Deduplication
* **Origin:** `2026-09-18-feature-version-stamp-followup-cleanup.md` Minor wiring gaps section.
* **Requirements:**
  * Pass computed `feature_version` into `write_prediction_features()` in `scripts/backfill_schedule_predictions.py::main()`.
  * In `scripts/predict_season.py`, replace `_model_version_string()` with `services.model_version.build_ensemble_version_string()`.
  * Align guard styles between `scripts/cache_builder.py::_publish_game_probs` (`if ensemble_version:`) and `scripts/backfill_schedule_predictions.py::_build_predictions_map` (unconditional assign).

### 4.4 Roster Ingestion & Starter Deduplication Defensive Guards
* **Origin:** `2026-09-17-qb-availability-followup-cleanup.md` Test coverage and observability sections.
* **Requirements:**
  * Guard against malformed `weekly_rosters/roster_weekly_{year}.csv` missing `week` column by degrading gracefully to `{}` instead of raising `KeyError` in `compute_preseason_player_profiles`. Add dedicated test.
  * Enforce deterministic ordering/deduplication in `_load_declared_starters()` (`services/nn_feature_engine.py`) if old and new nflverse schema rows ever overlap for the same season, and verify `new["team"]` exists.
  * Complete docstring in `compute_qb_availability_flags()`: document 20% snap-share unavailability threshold and whole-season fallback when no declared starter exists.
  * In `docs/prediction_model.md`'s "Top coefficients (LR v3)" table, annotate `qb_injury_flag` as a historical coefficient snapshot.

### 4.5 Resimulation Lead Timing Profiling
* **Origin:** `2026-09-18-model-prediction-e2e-review-design.md` Rollout Item 5.
* **Requirements:**
  * Profile `RESIMULATE_LEAD_MINUTES` execution window to ensure scheduled kickoff resimulation completes before game lock.

### 4.6 Feature Version and Promotion Pipeline Testing Gaps
* **Origin:** `2026-09-18-feature-version-stamp-followup-cleanup.md` Test coverage section.
* **Requirements:**
  * Document single-class test split behavior in `services/model_promotion.py::find_same_schema_best`.
  * In `tests/test_promotion_gate_xgb.py`, add filesystem assertion (`assert not (tmp_path / "xgb_v4.json").exists()`) proving the gate raises before any file write happens.
  * In `tests/test_cache_service.py`, test `feature_version` through `get_prediction_features()` latest-for-season glob lookup path.
  * In `scripts/backfill_schedule_predictions.py`, add integration test for `main()` ensemble version string usage.
  * Surface `feature_version` in admin forecast/explain UI (`routes/admin_routes.py`).

### 4.7 Model Retrain & Registry Behavioral Invariants (Informational)
* **Origin:** `2026-09-18-feature-version-stamp-followup-cleanup.md` Decisions section; `2026-09-17-qb-availability-followup-cleanup.md` Minor behavior section.
* **Invariants:**
  * **NN Registry `feature_columns`:** Do not retroactively backfill `feature_columns` onto historical NN v1-v15 entries using today's 27-column list (schema evolved 33 → 32 → 27). The gate will evaluate forward from v16.
  * **LR Promotion Gate Fire:** Expect the promotion gate to fire for real on the next LR retrain (LR best is v5 with accuracy 0.5625; latest v7 sits at 0.50). Treat as intended behavior, not false positive.
  * **Whole-Document Overwrite in Backfill:** `backfill_schedule_predictions.py` writes via whole-document overwrite, which self-heals on subsequent runs.
  * **ATS-Pick Fallback:** Shared `derive_prediction_scalars` uses `ats = winner` when a game has no Vegas line.
  * **Tuesday Backfill Scope:** Scoped to `<current_year> <current_year>`, intentionally skipping upcoming undrafted seasons during the August window.

---

## 5. Track 5: Code Hygiene & Test Quality Checklist

| ID | Location | Item / Required Change |
|---|---|---|
| **5.1** | `services/analysis_service.py` | Document `team_records` parameter in `calculate_wins_pool_standings` docstring (reuse precomputed dict; empty dict valid). |
| **5.2** | `tests/test_admin_routes.py` | Remove unused `import inspect` in route test area if genuinely unused. |
| **5.3** | `tests/test_prediction_results.py` | Correct docstring for `_reference_lookup` (admin_routes version with NaN guards). |
| **5.4** | `services/model_promotion.py` | In `_fmt()`, accept `numbers.Real` (`isinstance(value, numbers.Real) and not isinstance(value, bool)`) so numpy scalar types format properly. Add test with `numpy.float32`/`numpy.int64`. |
| **5.5** | `services/rate_limit_service.py` | Update `get_limiter` docstring noting limits are immutable once initialized. |
| **5.6** | `tests/test_rate_limit_service.py` | Remove unused `import pytest` if confirmed unused. |
| **5.7** | Historical Data | Normalize `OAK` to `LV` in 2017-2019 `draft_results` or implement normalize-on-read in join paths. |
| **5.8** | `tests/test_team_abbr_consolidation.py` | Replace substring check for `"JAC": "JAX"` with an AST dictionary scan for dict literals with `"WSH"` and `"JAC"` keys. |
| **5.9** | `tests/test_db_service_init_firebase.py` | Narrow `patch("pathlib.Path.exists")` to specific credential paths to avoid affecting global path checks. |
| **5.10** | `tests/test_auth.py` | Reflow joined multi-line `with patch(...), \` statements in `TestProfileUpdateAuth` and `TestMfaVerify`. |
| **5.11** | `tests/test_hosting_cleanup.py` | `test_firebase_json_has_no_rewrites_to_missing_service` returns early when no `hosting` block exists; assert that no `run.serviceId` other than `winspool` appears or fold into neighboring test. |
| **5.12** | `tests/test_historical_projection_routes.py` | Note overlap in docstring between `test_player_profile_shows_consensus_projection_for_historical_season` and `test_service_get_player_analytics_data_uses_consensus_projection`. |
| **5.13** | `tests/test_player_analytics.py` | Drop inert patches on `routes.history_routes.load_data` / `get_season_projection_legacy_shape` and dead import in `routes/history_routes.py:8` (subject to zero-deletion approval). |
| **5.14** | `tests/test_script_firebase_init.py` | Rename `test_init_fails_loudly_without_credentials` to `test_init_fails_loudly_when_get_db_returns_none`. |
| **5.15** | Script Tests | Confirm no environment variable leaks from script tests setting `USE_LOCAL_DATA` by running in isolation and random order. |
| **5.16** | `scripts/generate_weekly_summary.py` | Remove stale comment `# Create a simple HTML wrapper` above `build_recap_html` call. |
| **5.17** | Recap Email Heading | Replace pre-existing emoji in recap email heading in `scripts/generate_weekly_summary.py` if zero-emoji policy is strictly repo-wide. |

---

## 6. Track 6: Documentation and Reference Alignment

1. **`DEPLOY.md`:** Update `AUTH_RATE_LIMIT_PER_MINUTE` description (~line 98) to list `/api/mfa/verify` alongside login, set_password, and profile/update.
2. **`docs/deployment.md`:** Clarify (~line 141) that Firebase Hosting is retired and the app is served directly from the Cloud Run URL in `APP_BASE_URL`.
3. **`CLAUDE.md`:**
   * Document that `normalize_team_abbr` is the canonical normalizer and `TEAM_ABBR_MAP` lives in `services/constants.py`.
   * Document that the 5 Firestore scripts obtain their client via `get_db()` after forcing `USE_LOCAL_DATA=False`.
   * Document `--force-promote` flag on all three `scripts/train_*.py` training scripts and the `GIT_SHA` Docker build arg contract.
4. **`routes/auth_routes.py`:**
   * Update comments on `_login_limiter` (~lines 40-43) to reflect that it covers `login`, `set_password`, `profile/update`, and `mfa/verify`.
   * Move `_MFA_MAX_ATTEMPTS` below the limiter definitions.
