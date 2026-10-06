# Admin Recap Tee-Up, iOS Push, Central Exception Handler, updateNav Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-populate and auto-preview the admin recap prompt, make iOS Web Push discoverable via a PWA manifest and install banner, replace repeated route 500 boilerplate with one global handler, and split `updateNav()` into focused helpers.

**Architecture:** Four independent, additive changes. Task 1 makes the preview route's `year`/`week` optional (server defaults to latest completed week) and has the admin Recap tab auto-call the existing preview. Task 2 adds a manifest, head tags and a client-side iOS guidance banner. Task 3 registers an `Exception` handler in `main.py` and removes only generic `except Exception: ... server_error()` wrappers. Task 4 is a behavior-preserving extraction in `main.js`.

**Tech Stack:** FastAPI, Jinja2, vanilla ES modules, pytest + Starlette TestClient, Playwright (e2e).

**Spec:** `docs/superpowers/specs/2026-08-21-weekly-recap-automation-followup.md` (Task 1), `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md` section 3 (Task 2). Tasks 3 and 4 follow GitHub issues #67 and #104.

## Global Constraints

- No emojis in code, comments, commit messages, or docs.
- Strictly additive or exact drop-in replacements; no feature, endpoint, or test-coverage deletion. Only redundant generic-500 `try/except` boilerplate may be removed (Task 3), and each removed block's 500 behavior stays covered by a test.
- Recap AI generation and email broadcast stay human-in-the-loop: no automated Gemini call, no automated email.
- Any code that writes under `.local_db` uses `services/local_paths.py::local_db_dir()`. Baseline guard: `python -m pytest tests/test_local_db_isolation.py -q` (13 passed at start).
- Nav changes must keep desktop `updateNav()` and the `base.html` drawer in parity (`tests_e2e/test_nav_parity.py`).
- New CSS uses theme tokens (`tests/test_theme_sweep.py` bans raw `rgba(255,255,255,...)`).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Reference issue numbers (#82, #67, #104) where applicable.
- Update `docs/api_endpoints.md`, `docs/frontend.md`, `docs/architecture.md`, and `CLAUDE.md` for anything that changes behavior.

## Review Focus

- Preview route called with `year` only, `week` only, or neither: must default the missing parts, not 422.
- No completed games yet (preseason): preview must return 404 with a clear message, and the tab must not alert on auto-load (silent failure on auto-fetch, alert only on a manual click).
- Commissioner has already typed a custom year/week or edited the prompt textarea: auto-populate must not overwrite non-empty inputs or an edited prompt.
- Starlette `TestClient` re-raises unhandled exceptions by default, even with a global `Exception` handler; tests asserting 500 must use `raise_server_exceptions=False`.
- Removing a `try/except` that also contained cleanup or a domain-specific `except` branch: must not change those branches.
- iOS banner on non-iOS, on iOS standalone, and after dismissal (persisted per-viewer, wrapped in try/catch for blocked storage).

---

## Spec Drift (verified 2026-10-05)

The specs and issues predate recent work (theme toggle, auth guard, playoff race nav, unpaid notice, ESPN-based jobs). Treat their line numbers and counts as stale; the code is authoritative. Every task starts with a re-verify step (grep the current code for the named symbols) and records any difference in the commit message.

- Task 1: `admin.html` already reads `default_year`/`default_week`, supplied only by the `/admin` route in `routes/draft_routes.py:116-120`. Partially done; server-side defaulting still adds value. The spec's "not designed" status means nothing there constrains the tee-up; the auto-preview is the whole deliverable.
- Task 2: `POST /api/push/client-error` (`routes/api_routes.py:479`) and the client `catch` reporting already exist, so the hardening spec's "instrument, don't assume" step is largely shipped. `static/manifest.json` and Apple tags are still absent. The spec's `main.js:477` guard reference is now around line 495; `onWsOpen` is the only caller of `initPushNotifications`, so the banner will only show on the live-draft page unless the check is also called from the player page notification area (decide in Step 6: call it from both `App.init` and the draft path, guarded by a once-per-page flag).
- Task 3: issue says ~40 sites; actual `server_error()` count is 49 (admin 27, api 17, prediction 4, standings 1). Re-run the AST candidate scan; do not trust the count.
- Task 4: issue says 9 regions in 120 lines at `main.js:116-239`; `updateNav()` is now `main.js:160-306` (~146 lines) with added playoff-race gating and bottom-tab swap. The five helpers named in the task cover primary links, More menu, avatar, drawer and bottom tabs; verify no region (e.g. theme toggles, which `wireThemeToggles()` handles separately) is left in `updateNav()` after the split.

## File Structure

- Modify `routes/models.py`: `RecapWeekRequest` year/week become optional.
- Modify `routes/admin_routes.py`: default year/week in `preview_recap_prompt`; later strip generic try/except.
- Modify `static/js/admin_main.js`: auto-populate and auto-preview on Recap tab open.
- Modify `main.py`: global exception handler.
- Modify `routes/api_routes.py`, `routes/prediction_routes.py`, `routes/standings_routes.py`: strip generic try/except.
- Create `static/manifest.json`; modify `templates/base.html`, `static/js/main.js`, `static/style.css`.
- Create `tests/test_templates.py`, `tests/test_global_exception_handler.py`, `tests/test_ios_push_banner_js.py`.

---

## Task 0: Baseline

- [x] **Step 1:** Run `python -m pytest tests/test_local_db_isolation.py -q`. Result: 13 passed.
- [ ] **Step 2:** Create branch `sprint/recap-ios-exceptions-nav` from `main` (`git switch -c sprint/recap-ios-exceptions-nav`). Note `cleanup-policy.json` is untracked and unrelated; never `git add -A`.

---

## Task 1: Admin Recap Prompt Auto-Tee-Up (#82)

**Files:**
- Modify: `routes/models.py:99-101`
- Modify: `routes/admin_routes.py:385-395`
- Modify: `static/js/admin_main.js` (`setupTabHandlers` near line 60; `previewRecapPrompt` line 546)
- Test: `tests/test_admin_routes.py` (new class `TestPreviewRecapPromptDefaults`)

**Interfaces:**
- Produces: `POST /api/admin/recap/preview_prompt` accepts `{year?: int, week?: int}`. Missing `year` defaults to `get_active_season(games, draft_results, rules)`; missing `week` defaults to `get_most_recent_completed_week(games, year)`. If no week can be determined: 404 `{"error": "No completed games found for <year>."}`. Response unchanged: `{"prompt": str}`.
- Produces: JS `AdminApp.initRecapTab()` and `previewRecapPrompt({silent})`.

Note: `templates/admin.html` already reads `default_year`/`default_week`, but the `/admin` route (`routes/draft_routes.py:116-120`) is the only one supplying them; the server-side default in this task makes the tab correct even if those are empty or stale.

- [ ] **Step 1: Write failing tests** in `tests/test_admin_routes.py`:

```python
class TestPreviewRecapPromptDefaults:
    def _games(self):
        return pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "home_score": 24, "away_score": 20},
        ])

    def test_defaults_year_and_week_when_omitted(self, admin_token):
        with patch("routes.admin_routes.load_data", return_value=(self._games(), None, pd.DataFrame(), None, None)), \
             patch("routes.admin_routes.get_active_season", return_value=2026), \
             patch("routes.admin_routes.get_most_recent_completed_week", return_value=3), \
             patch("routes.admin_routes.recap_service.extract_weekly_data", return_value=({"x": 1}, [])) as extract, \
             patch("routes.admin_routes.ai_service.get_recap_prompt", return_value="PROMPT"):
            resp = client.post("/api/admin/recap/preview_prompt", json={}, headers={"Authorization": admin_token})
        assert resp.status_code == 200
        assert resp.json() == {"prompt": "PROMPT"}
        extract.assert_called_once_with(2026, 3)

    def test_explicit_values_are_not_overridden(self, admin_token):
        with patch("routes.admin_routes.recap_service.extract_weekly_data", return_value=({"x": 1}, [])) as extract, \
             patch("routes.admin_routes.ai_service.get_recap_prompt", return_value="P"):
            resp = client.post("/api/admin/recap/preview_prompt", json={"year": 2025, "week": 9},
                               headers={"Authorization": admin_token})
        assert resp.status_code == 200
        extract.assert_called_once_with(2025, 9)

    def test_no_completed_week_returns_404(self, admin_token):
        with patch("routes.admin_routes.load_data", return_value=(pd.DataFrame(), None, pd.DataFrame(), None, None)), \
             patch("routes.admin_routes.get_active_season", return_value=2026), \
             patch("routes.admin_routes.get_most_recent_completed_week", return_value=None):
            resp = client.post("/api/admin/recap/preview_prompt", json={}, headers={"Authorization": admin_token})
        assert resp.status_code == 404
```

Before writing, read `load_data`'s actual tuple order in `routes/admin_routes.py` (see `TestNewSeason` for the pattern) and import/patch names accordingly; add `get_active_season` and `get_most_recent_completed_week` to the `routes.admin_routes` imports from `services.data_service` if absent.

- [ ] **Step 2:** Run `python -m pytest tests/test_admin_routes.py -k PreviewRecapPromptDefaults -v`. Expected: FAIL (422 for empty body).
- [ ] **Step 3: Implement.** In `routes/models.py` make `RecapWeekRequest` fields `year: Optional[int] = None`, `week: Optional[int] = None` (keep `RecapYearRequest` and other users of `RecapWeekRequest` working; grep for other users first and add per-route explicit checks if `generate`/`save_and_broadcast` reuse it - they use their own models). In `preview_recap_prompt`, resolve defaults via `load_data()` + `get_active_season` + `get_most_recent_completed_week` before `extract_weekly_data`; return the 404 when the week is `None`.
- [ ] **Step 4:** Re-run the new tests plus `python -m pytest tests/test_admin_routes.py -q`. Expected: PASS.
- [ ] **Step 5: Client.** In `admin_main.js`:
  - In `setupTabHandlers`, add `if (target === 'recap-section') { this.initRecapTab(); }`.
  - Add `initRecapTab()`: runs once per tab open; if `#recap-year` or `#recap-week` is empty fill from `GET /api/schedule`-free source: do not call new endpoints, instead call `previewRecapPrompt({silent: true})` with blank inputs allowed (server defaults), and on success set `#recap-year`/`#recap-week` from a new `year`/`week` echoed in the preview response (extend the response additively: `{"prompt": ..., "year": y, "week": w}`; update test 1 to assert those keys). Skip the fetch if `#recap-prompt-text` already has user-edited content (non-empty value).
  - Change `previewRecapPrompt()` to accept `{silent = false} = {}`: when `silent`, skip the "specify Year and Week" alert and swallow errors with `console.warn`. Manual button clicks still alert. Wire the button as `() => this.previewRecapPrompt()` (unchanged).
  - Do not touch `generateRecap`/broadcast handlers.
- [ ] **Step 6:** Update `tests/test_admin_routes.py` test 1 for the echoed `year`/`week`, run it, then `pytest tests_e2e/test_admin_members_and_recap.py -v` (needs e2e env vars; if unavailable, record that it was skipped and why).
- [ ] **Step 6b: Copy button (user request).** In `templates/admin.html` add `<button id="recap-copy-prompt-btn" class="btn-secondary" type="button">Copy prompt</button>` in the `#recap-prompt-preview-container` header row (the textarea is `readonly`, so no edited-prompt guard is needed in auto-preview; drop that guard from `initRecapTab`). In `admin_main.js` wire it to `copyRecapPrompt()`: `navigator.clipboard.writeText(textarea.value)` with a fallback (`textarea.select(); document.execCommand('copy')`) for non-secure contexts, then set the button label to "Copied" for 1.5s; on failure show "Copy failed". Disabled when the textarea is empty. Same button pattern for the draft recap preview if it reuses `#recap-prompt-text` (it does; one button covers both). Add a Playwright check to `tests_e2e/test_admin_members_and_recap.py`: after preview, click the button and assert the label changes to "Copied" (grant `clipboard-read`/`clipboard-write` permissions, then compare `navigator.clipboard.readText()` to the textarea value).
- [ ] **Step 7:** Docs: `docs/api_endpoints.md` (optional body, echo fields, 404), `docs/frontend.md` (Recap tab auto-preview), `CLAUDE.md` only if a convention changed. Mark #82 partially addressed in the commit (human-in-the-loop only).
- [ ] **Step 8: Commit** `feat: auto-populate and auto-preview admin recap prompt (#82)`.

---

## Task 2: iOS Web Push Support and PWA Manifest

**Files:**
- Create: `static/manifest.json`
- Modify: `templates/base.html` (head, near line 12), `static/js/main.js` (`initPushNotifications` ~line 494), `static/style.css`
- Test: `tests/test_templates.py` (new), `tests/test_ios_push_banner_js.py` (new, node behavioral test in the style of `tests/test_nav_gating_js.py`)

**Interfaces:**
- Produces: `static/manifest.json`; JS pure helper `shouldShowIosInstallHint({userAgent, standalone, dismissed})` exported from `main.js` or a small new module `static/js/ios_push_hint.js` (prefer the new module: `main.js` imports are heavy for node tests). Returns boolean. `renderIosInstallBanner()` inserts a dismissible `.ios-install-banner`.

- [ ] **Step 1: Failing template test** `tests/test_templates.py`: render `GET /` (or the standings page via TestClient) and assert the HTML contains `rel="manifest" href="/static/manifest.json"`, `rel="apple-touch-icon"`, `name="apple-mobile-web-app-capable" content="yes"`, and `name="apple-mobile-web-app-status-bar-style" content="black-translucent"`. Add a second test loading `static/manifest.json` and asserting `name == "WinsPool"`, `start_url == "/"`, `display == "standalone"`, and that every `icons[].src` exists on disk under `static/`.
- [ ] **Step 2:** Run; expected FAIL.
- [ ] **Step 3: Manifest.** `static/manifest.json` with `name`/`short_name` "WinsPool", `start_url` "/", `scope` "/", `display` "standalone", `background_color` "#14171d", `theme_color` "#14171d" (match `<meta name="theme-color">`), icons referencing the existing `/static/fishbone.png` (sizes `any`, type `image/png`). No new binary assets; if a 192/512 icon is wanted, flag it to the user rather than generating art.
- [ ] **Step 4: Head tags** in `base.html`: add `<link rel="manifest" href="/static/manifest.json">`, `<link rel="apple-touch-icon" href="/static/fishbone.png">`, and the two Apple meta tags from the task statement. `mock_draft.html` does not extend `base.html`; leave it alone.
- [ ] **Step 5: Failing JS test** `tests/test_ios_push_banner_js.py`: node-evaluated cases for `shouldShowIosInstallHint`: iPhone UA + not standalone + not dismissed -> true; iPhone UA + standalone -> false; desktop Chrome UA -> false; iPhone + dismissed -> false; iPad (UA `iPad`) -> true. Copy the node-skip-if-missing pattern from `tests/test_nav_gating_js.py`.
- [ ] **Step 6: Implement** `static/js/ios_push_hint.js`: UA test `/iPhone|iPad|iPod/`, standalone via `window.navigator.standalone`, dismissal in `localStorage['nfl_wins_ios_push_hint_dismissed']` wrapped in try/catch. Banner text exactly: "To enable push notifications on iOS, tap the Share button and select 'Add to Home Screen'". In `main.js::initPushNotifications`, before the `PushManager` guard returns, call the hint check and render the banner (the current guard exits silently on iOS Safari tabs, which is the bug). Add `.ios-install-banner` CSS using existing tokens (`--surface-sunken`, `--ink`, `--hairline`); dismiss button has an `aria-label`. Note iPadOS 13+ reports a Mac UA; also treat `navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1` as iOS in the helper and cover it in a test.
- [ ] **Step 7:** Run `pytest tests/test_templates.py tests/test_ios_push_banner_js.py tests/test_theme_sweep.py tests/test_theme.py -q`. Expected PASS. Check the banner at a ~390px viewport in the browser (manual, note result).
- [ ] **Step 8:** Docs: `docs/frontend.md` (manifest, banner), `CLAUDE.md` module layout line for `ios_push_hint.js`. Commit `feat: PWA manifest and iOS add-to-home-screen push guidance`.

---

## Task 3: Centralized Exception Handling (#67)

**Files:**
- Modify: `main.py` (after app creation), `routes/admin_routes.py` (27 `server_error()` sites), `routes/api_routes.py` (17), `routes/prediction_routes.py` (4), `routes/standings_routes.py` (1)
- Test: `tests/test_global_exception_handler.py` (new); adjust `TestClient` construction in tests that assert 500

**Interfaces:**
- Produces: global handler returning `JSONResponse(500, {"error": "An internal server error occurred."})`, logging `Unhandled error on %s %s`. `services/response_helpers.py::server_error` stays (still used by domain branches and pinned by `tests/test_response_helpers.py`).

Important: Starlette routes handlers registered for `Exception` through `ServerErrorMiddleware`, which sends the response and then re-raises. `TestClient(app)` defaults to `raise_server_exceptions=True`, so existing tests that assert `status_code == 500` (`tests/test_admin_routes.py:918`, `tests/test_api.py:88` is a service-failure branch and unaffected, `tests/test_live_standings_route.py:151`) would see the exception raised once their try/except is removed. Those tests must use `TestClient(app, raise_server_exceptions=False)`.

- [ ] **Step 1: Failing test** `tests/test_global_exception_handler.py`: build a throwaway route that raises, via `app.add_api_route` on a copy is invasive; instead patch a real route (e.g. `/api/live-standings?year=2026` with `routes.api_routes.load_data` raising `RuntimeError("boom")`) using `TestClient(app, raise_server_exceptions=False)` and assert 500, body `{"error": "An internal server error occurred."}`, and a log record from `main` containing the path (`caplog`). This fails today only on the body message once the route's try/except is gone, so also add a direct unit test: `asyncio.run(main.global_exception_handler(fake_request, RuntimeError("x")))` returns status 500 with the exact body. Expected FAIL (handler missing).
- [ ] **Step 2: Implement** the handler in `main.py` exactly as specified (import `JSONResponse` and `logging` as needed; main.py already imports `Request`).
- [ ] **Step 3:** Run the new test; PASS.
- [ ] **Step 4: Strip boilerplate, one file per commit.** For each `try: ... except Exception: logger.exception(...); return server_error()` whose only `except` clause is that generic one, dedent the body and delete the wrapper. Rules:
  - Keep blocks with any other `except` clause (ValueError, KeyError, HTTPException), `finally`, or extra logic in the generic handler (cleanup, partial result, different status code). Narrow blocks that mix a domain `except` and the generic one: delete only the generic `except Exception` clause.
  - A body that already `return`s on all paths keeps its returns; do not alter responses.
  - Generic handlers inside non-route helpers are out of scope.
  - Do this mechanically with `python -I` AST scripting in the scratchpad (locate `Try` nodes with exactly one handler of type `Exception` whose body is `logger.*` plus `return server_error(...)`), print the candidate list per file, review it, then apply. Remove now-unused `server_error`/`logger` imports only if flake-clean.
- [ ] **Step 5:** After each file: `python -m pytest tests/test_admin_routes.py tests/test_api.py tests/test_live_standings_route.py tests/test_response_helpers.py -q` (then the whole `tests/ -n auto` before the final commit). Update 500-asserting tests to `raise_server_exceptions=False` clients; do not delete them. Add `assert resp.json()["error"]` checks only where it adds value.
- [ ] **Step 6:** Docs: `docs/architecture.md` and `docs/api_endpoints.md` (global 500 contract and body text), `CLAUDE.md` (route error-handling convention: do not add generic try/except, rely on the global handler). Commits: `refactor: add global unhandled-exception handler (#67)`, then `refactor: drop generic 500 try/except in admin_routes (#67)`, and likewise per remaining file.

---

## Task 4: Decompose `updateNav()` (#104)

**Files:**
- Modify: `static/js/main.js` (`updateNav`, lines ~160-306)
- Test: `tests_e2e/test_nav_parity.py` (run), `tests/test_nav_gating_js.py` (run; extend if it parses `updateNav`)

**Interfaces:**
- Produces on the app class: `_updatePrimaryLinks(path, role)` (returns `showPlayoffRace` boolean), `_updateMoreDropdown(role)`, `_updateAvatar({nickName, playerName, role})`, `_updateDrawer({role, nickName, playerName, showPlayoffRace})` (admin link, live draft, mock draft, playoff race drawer/tab entries, footer + logout), `_updateBottomTabs(path)` (active state plus Drafts/Live Draft swap). `updateNav()` keeps its early `if (!playerId) return;` guard and calls them in the original order.

- [ ] **Step 1: Safety net first.** Run `pytest tests/test_nav_gating_js.py -q` and, if e2e env is available, `pytest tests_e2e/test_nav_parity.py -v` to record a green baseline. Check how `test_nav_gating_js.py` reads `main.js` (a regex over source could break on the rename; if so keep the pinned snippet intact inside the helper).
- [ ] **Step 2:** Extract each region verbatim into its helper (cut and paste, no logic edits). `showPlayoffRace` is computed in `_updatePrimaryLinks` and passed to `_updateDrawer`; the bottom-tab playoff toggle (`btb-playoff-tab`) moves with the drawer helper to preserve ordering. Keep link arrays unchanged so parity with `base.html` holds.
- [ ] **Step 3:** Rerun the baseline commands; expected identical results. Manually check desktop width and ~390px width (More menu, drawer, bottom tabs, draft-active swap).
- [ ] **Step 4:** Docs: `docs/frontend.md` nav section lists the helpers; `CLAUDE.md` nav gotcha paragraph mentions `_updateMoreDropdown` as the place to edit `moreLinks`. Commit `refactor: split updateNav into per-region helpers (#104)`.

---

## Final Verification

- [ ] `python -m pytest tests/ -n auto` (record failures; compare against the known flaky/env-failing list in memory `reference_worktree_workflow_gotchas`).
- [ ] `python -m pytest tests/test_local_db_isolation.py -q`.
- [ ] `pytest tests_e2e/test_admin_members_and_recap.py tests_e2e/test_nav_parity.py -v` if e2e env vars exist; otherwise report them as not run.
- [ ] Run `graphify update .`.
- [ ] Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch.

## Self-Review Notes

- Spec coverage: Task 1 req 1-3, Task 2 req 1-3, Task 3 req 1-2, Task 4 req 1-2 each map to a step above.
- Deviation to confirm: the global handler body text ("An internal server error occurred.") differs from `server_error()`'s default ("An internal error occurred."); both exist by design.
- Deviation to confirm: preview response gains `year`/`week` echo fields (additive) so the client can fill inputs without a new endpoint.
- Known risk: Task 3 touches about 49 handlers; per-file commits and the AST candidate review keep it reversible.
