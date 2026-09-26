# Sprint: Features and Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship six workstreams: reliability quick wins, live-draft 500 guard, Tuesday model-accuracy automation, pool fee tracker (#87), player portfolio widget (#83), and an ML accuracy season filter.

**Architecture:** Additive changes on branch `worktree-worktree-sprint-features-and-hardening`. New logic lives in small service functions (`services/pool_service.py`, `analysis_service.compute_portfolio_projection`) with thin route wrappers; frontends fetch JSON and render into existing templates using existing CSS tokens.

**Tech Stack:** FastAPI, pandas, Firestore/pickle, pytest, vanilla JS + Jinja2.

**Spec:** `docs/superpowers/specs/2026-09-26-consolidated-followups-and-tech-debt.md` (Tracks 1.1, 2.1, 2.2, 3.1, 4.1) plus the sprint brief (Tasks 3-6).

## Global Constraints

- NO emojis anywhere (code, comments, commits, docs, UI strings).
- Zero deletion: strictly additive changes or exact replacements. Never remove existing features or tests. Where an existing test must change because a contract changed (get_db no longer raises), replace it with an equivalent assertion, do not delete coverage.
- TDD: failing test first, watch it fail, then implement.
- Never run `deploy/deploy.ps1`. Never write to Firestore.
- Run tests from the worktree root. The worktree lacks `.local_db/` and `models/`; failures in tests that need them are pre-existing. Compare against a baseline run (Task 0).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`
- `docs/` may be gitignored: use `git add -f` for docs files.

## Review Focus

- `get_db()` with no credentials must return `None` (not raise) and importing `services.db_service` with no credentials and `USE_LOCAL_DATA=False` must not raise.
- `save_push_subscription` returns True when Firestore write succeeds even if cache invalidation raises; False only on write failure.
- Portfolio endpoint for a player with 0 drafted teams returns zeros/empty (not 500); non-admin must not receive projections while `draft_active` is True.
- Pool status must expose only counts plus the caller's own `paid`, never other players' paid flags; entry fee of 0 or unset must not divide by zero or render a banner.
- `season` query filter for accuracy: a season with no predictions returns an empty result (not 500); no `season` keeps the exact existing response.
- Tuesday eval step: no completed week yet (early season / offseason) skips silently; subprocess failure never stops predictions.

---

## Task 0: Baseline

- [ ] Run `python -m pytest tests/ -n auto -q -p no:cacheprovider 2>&1 | tail -40` (fall back to serial if xdist missing). Save the list of failing test ids to `$SCRATCH/baseline_failures.txt`. These are pre-existing (missing `.local_db`/models) and are the comparison baseline.

## Task 1: Reliability and infrastructure quick wins

**Files:**
- Modify: `services/push_service.py`, `services/db_service.py`, `deploy/deploy.ps1`, `Dockerfile.sync`, `Dockerfile.predict`
- Modify (delegate to `require_db`): `scripts/daily_nfl_sync.py`, `scripts/backfill_schedule_predictions.py`, `scripts/predict_season.py`, `scripts/generate_weekly_predictions.py`, `scripts/upload_configfiles.py` (these are the 5 scripts that actually contain the duplicated init block; `seed_consensus`, `migrate_consensus`, `generate_weekly_summary` and `refresh_local_pkls` have no such block and only call service functions or `get_db()` directly, so they are left unchanged except `refresh_local_pkls` which needs no change)
- Test: `tests/test_push_service.py`, `tests/test_db_service_lazy_init.py` (new), `tests/test_script_firebase_init.py`, `tests/test_deploy_config.py`, `tests/test_dockerfile_layer_order.py` (new)

**Interfaces:**
- Produces: `services.db_service.require_db(exit_on_missing: bool = True, missing_message: str | None = None, exc_type=None)`. Forces `os.environ["USE_LOCAL_DATA"]="False"`, calls `get_db()`, returns the client. If None: when `exit_on_missing` logs/prints the message and `sys.exit(1)`; otherwise raises `exc_type` (default `FileNotFoundError`) with the message.
- Produces: `get_db()` returns `None` when creds are missing/uninitialized; `_init_firebase()` is called lazily (no import-time call).

- [ ] **1a push_service tests first** in `tests/test_push_service.py` (append; keep existing tests): (1) write succeeds + `_invalidate_players_cache` raises -> `save_push_subscription` returns True and logs a WARNING containing the player id (use `caplog`); (2) `.update` raises -> returns False; (3) `_deliver` with `webpush` raising an exception whose `response.status_code == 410`, `_prune_subscription` patched to return True path where the inner invalidation raises: patch `services.db_service.get_db` to a MagicMock db whose stored endpoint matches, patch `push_service._invalidate_players_cache` with `side_effect=RuntimeError`, patch `firebase_admin.firestore` DELETE_FIELD access as needed; assert `_deliver(...) == "pruned"`. Run, confirm fail.
- [ ] **1b implement**: in `save_push_subscription`, after the `.update(...)`, wrap `_invalidate_players_cache()` in its own `try/except Exception: logger.warning("push_service: players cache invalidation failed after saving subscription for player %s", player_id, exc_info=True)`, then `return True`. In `_prune_subscription`, wrap the `_invalidate_players_cache()` call the same way (WARNING) so it still returns True. Run tests, pass.
- [ ] **1c db_service tests first** (`tests/test_db_service_lazy_init.py`): with `USE_LOCAL_DATA=False`, `FIREBASE_CREDENTIALS` unset, `pathlib.Path.exists` patched False for the credentials file, and `firebase_admin._apps` monkeypatched to `{}`: `get_db()` returns None; `importlib.reload(services.db_service)` does not call `firebase_admin.initialize_app` (patch it with a MagicMock and assert not called); `require_db(exit_on_missing=True)` raises `SystemExit` with code 1 when `get_db` returns None; `require_db(exit_on_missing=False, exc_type=FileNotFoundError)` raises FileNotFoundError; `require_db()` returns the fake client and sets `USE_LOCAL_DATA` to "False" when `get_db` patched to return a MagicMock. Run, fail.
- [ ] **1d implement** in `services/db_service.py`: remove the module-level `if not use_local_env: _init_firebase()` (keep the `use_local_env` variable if referenced elsewhere: grep first). Rewrite `get_db()`:
  ```python
  def get_db():
      if os.environ.get("USE_LOCAL_DATA", "False").lower() == "true":
          return None
      import firebase_admin
      from firebase_admin import firestore
      if not firebase_admin._apps:
          if _init_firebase() is None:
              return None
      return firestore.client()
  ```
  wrapping `firestore.client()` in `try/except ValueError: return None`. Add `require_db`. Grep every `get_db()` caller in `services/`, `routes/`, `scripts/` and confirm each already handles `None` (most do `if db:`/`if not db`); fix any that would `AttributeError` on None only by adding a guard that preserves the prior behavior when a client exists.
- [ ] **1e scripts**: replace the body of `_init_firestore` (backfill), `initialize_firebase` (daily_nfl_sync), `_init_firebase` (predict_season, generate_weekly_predictions, upload_configfiles) with a one-line delegation to `require_db(...)` preserving each script's existing failure mode (backfill: `FileNotFoundError`; the other four: `SystemExit(1)` with their existing message). Keep the function names. Each script must still expose `get_db` at module level (tests monkeypatch `mod.get_db`), so `require_db` must be invoked so that the monkeypatched module-level `get_db` is honored: implement the delegation as `db = get_db()` inside the script wrapper is NOT allowed to remain duplicated; instead give `require_db` a `getter` kwarg (default `None` -> `get_db`) and pass `getter=lambda: get_db()` from the script so the script-module patch still works. Remove the `try/except ValueError` shims.
- [ ] **1f update `tests/test_script_firebase_init.py`**: the `test_init_fails_loudly_on_real_no_credentials_path` test still passes (get_db now returns None). Rename `test_init_fails_loudly_without_credentials` to `test_init_fails_loudly_when_get_db_returns_none` (Track 5.14). Add no deletions.
- [ ] **1g deploy**: in `deploy/deploy.ps1` remove `VAPID_PRIVATE_KEY` from `$envVars` and add `--set-secrets="VAPID_PRIVATE_KEY=vapid-private-key:latest"` to the `gcloud run deploy` invocation (read the file to match how other args are passed). Extend `tests/test_deploy_config.py` with assertions: the string `vapid-private-key:latest` appears with `--set-secrets`, and `VAPID_PRIVATE_KEY=` does not appear inside the `$envVars` construction. Add a short DEPLOY.md note: operator must create the `vapid-private-key` secret, grant the Cloud Run service account `secretAccessor`, and run a one-time `gcloud run services update ... --remove-env-vars=VAPID_PRIVATE_KEY`; record rotation as a deferred decision.
- [ ] **1h Dockerfiles**: test first (`tests/test_dockerfile_layer_order.py`): for `Dockerfile.sync` and `Dockerfile.predict`, index of `RUN pip install` line < index of `ARG GIT_SHA` line < index of `ENV GIT_SHA` line < index of `COPY . .` line. Then move the two lines (exact text unchanged) directly above `COPY . .`. Also add the same ARG/ENV pair above `COPY . .` in `Dockerfile` (spec 4.1) and include it in the test.
- [ ] **1i** Run `pytest tests/test_push_service.py tests/test_db_service_lazy_init.py tests/test_script_firebase_init.py tests/test_deploy_config.py tests/test_dockerfile_layer_order.py tests/test_db_service_init_firebase.py -q`; fix `test_db_service_init_firebase.py` only if the contract change requires an equivalent-assertion replacement. Commit: `feat: push error isolation, lazy firebase init, require_db, secret manager, docker cache order`.

## Task 2: Live-draft 500 guard and slow marker

**Files:** Modify `services/analysis_service.py` (docstring only if guard already present), `pytest.ini`, `tests_e2e/test_live_draft.py`; Test `tests/test_wins_pool_missing_standings.py` (append)

Finding: `calculate_wins_pool_standings` already fills a missing standings frame with zeros (fix from the empty-standings work). This task pins the zero-played-games contract and adds the explicit guard for the `games`-provided path.

- [ ] Write tests (append): drafted season, `standings` empty DataFrame, `games` a DataFrame with zero rows with `season/week/result` columns, `team_records=None` -> returns non-empty frame, all wins 0, `global_record` "0-0", no exception; same with `games` having only unplayed rows (`result` NaN); same with `games=None`. Run; if any fails, add guard in `calculate_wins_pool_standings`: when `team_records is None` and `games` non-empty, compute inside `try/except Exception` falling back to `{}` after logging; ensure `format_team_record(t, {})` returns "0-0" (verify). Also document the `team_records` parameter in the docstring (Track 5.1).
- [ ] Register marker in `pytest.ini`: add `markers =\n    slow: long-running tests (deselect with -m "not slow")` (append; keep existing content).
- [ ] Add `pytestmark = pytest.mark.slow` at module level in `tests_e2e/test_live_draft.py` (check for an existing `pytestmark` and combine into a list).
- [ ] Verify `python -m pytest tests_e2e/test_live_draft.py --collect-only -m "not slow"` deselects it (playwright may be missing: if collection errors on import, verify with `python -c` AST check that `pytestmark` is present instead). Commit.

## Task 3: Tuesday weekly model-accuracy eval

**Files:** Modify `scripts/cache_builder.py`; Test `tests/test_cache_builder.py` (append)

**Interfaces:**
- Produces: `cache_builder._latest_completed_week(games, year) -> int | None` (highest REG week for which `week_is_complete` is True; None if none) and `cache_builder._run_weekly_eval_if_tuesday(games, current_year) -> None`.

- [ ] Tests first: `_latest_completed_week` with a games frame of weeks 1-3 complete and week 4 partly NaN -> 3; no complete week -> None; only REG games considered (a `game_type` column with a POST row). `_run_weekly_eval_if_tuesday`: patch `cache_builder.datetime` so `now(timezone.utc).weekday()` is 1 and `_run_subprocess_step` mocked: assert called once with a cmd ending `["--season", "2026", "--week", "3", "--firestore"]` (script path `weekly_model_eval.py`), `swallow_errors=True`; on a Wednesday (weekday 2) not called; on Tuesday with no completed week not called. `main()` ordering test following the existing pattern at `tests/test_cache_builder.py:692`: eval step called before `build_year`.
- [ ] Implement helpers next to `_run_weekly_backfill_if_tuesday`; filter `games` to `game_type == "REG"` when that column exists; timeout 900. In `main()`, call `_run_weekly_eval_if_tuesday(games, current_year)` immediately after the `Years to process` print and before "Loading ML models" (so it runs before forward-looking prediction regeneration). Add a docstring explaining Tuesday rationale and that it is wholly non-fatal.
- [ ] Run `pytest tests/test_cache_builder.py -q`. Commit.

## Task 4: Pool fee and prize pot tracker (#87)

**Files:** Create `services/pool_service.py`, `tests/test_pool_service.py`; Modify `routes/api_routes.py` (or a new `routes/pool_routes.py` registered in `main.py`: prefer adding to `api_routes.py` router to avoid registration changes), `routes/admin_routes.py`, `routes/models.py`, `templates/wins_pool.html`, `static/style.css` (bump `?v=N` in `base.html`)

**Interfaces:**
- Produces: `pool_service.build_pool_status(order_df, settings: dict, season: int, player_id: int | None) -> dict` returning
  `{"season": int, "entry_fee": float, "total_count": int, "paid_count": int, "total_pot": float, "collected": float, "payouts": [{"place": int, "pct": float, "amount": float}], "my_paid": bool | None}`.
  `total_pot = entry_fee * total_count`, `collected = entry_fee * paid_count`, payout `amount = total_pot * pct / 100` rounded to cents. Defaults when unset: `entry_fee=0`, `payouts=[{"place":1,"pct":100}]`. `my_paid` is None when the player is not in that season's order. Invalid config (negative fee, pcts not numeric) is clamped/ignored to defaults, never raises.
- Produces: `GET /api/pool/status?season=<int optional>` (auth required; default season = `get_active_season`), returns the dict above.
- Produces: `POST /api/admin/pool/config` body `{season?, entryFee: float>=0, payouts: [{place, pct}]}` (admin), stores `pool_entry_fee` and `pool_payouts` via `set_config_settings`.

- [ ] Tests first (`tests/test_pool_service.py`): 10 players, 7 paid, fee 100 -> total_pot 1000, collected 700, paid_count 7; payouts 60/30/10 -> 600/300/100; `my_paid` True/False/None; fee unset -> pot 0 and payouts default; draft_order empty -> counts 0, no exception; route test with TestClient and dependency override on `require_auth` proving the response never contains other players' ids or a per-player paid list; admin config route rejects negative fee (422/400) and non-admin (403).
- [ ] Implement service, routes, pydantic models (`routes/models.py`). Note `get_config_settings` defaults only two keys; read the new keys with `.get`.
- [ ] Frontend: in `templates/wins_pool.html` add a hidden `<div id="pool-fee-banner" class="pool-fee-card">` in the same region as the standings header, plus a small inline module/script (or in the JS file that already fetches standings: check `static/js` for the wins pool script and follow its fetch/auth style) that calls `/api/pool/status` and shows: "Prize pot: $X (paid: N of M)", payout split list, and "Your entry: Paid / Not yet paid". Hide the whole banner when `entry_fee` is 0 or the fetch fails. Use existing CSS tokens (`var(--ink-*)`, `var(--pos)`, `var(--line-strong)`, glass card class used elsewhere). Add `.pool-fee-card` styles to `static/style.css` and bump the `?v=` in `templates/base.html`. Check narrow (390px) layout.
- [ ] Run new tests plus `pytest tests/test_admin_routes.py -q`. Commit.

## Task 5: Player portfolio projection widget (#83)

**Files:** Modify `services/analysis_service.py`, `routes/api_routes.py`, `templates/profile.html`; Test `tests/test_portfolio_projection.py` (new)

**Interfaces:**
- Produces: `analysis_service.compute_portfolio_projection(team_projections: dict, player_teams: list[str], playoff_wins_threshold: float = 9.5) -> dict`. `team_projections` is `{team: {"projected_wins": float, "std_dev": float}}`. Returns
  `{"teams": [{"team": str, "projected_wins": float, "std_dev": float, "playoff_prob": float}], "expected_wins": float, "std_dev": float, "floor": float, "ceiling": float, "playoff_prob_any": float, "expected_playoff_teams": float, "team_count": int}`.
  Math: per-team `playoff_prob = 1 - Phi((threshold - mean)/sd)` (sd floor 0.5 to avoid division by zero; `math.erf`). `expected_wins = sum(mean)`. Portfolio `std_dev = sqrt(sum(sd^2))` (independence approximation, documented). `floor = max(0, expected - 1.645*std)`, `ceiling = min(17*n, expected + 1.645*std)` (5th/95th). `playoff_prob_any = 1 - prod(1 - p)`. Teams missing from projections are skipped; zero teams returns all-zero values with `team_count` 0 and empty `teams`.
- Produces: `GET /api/profile/portfolio` (auth): resolves the caller's picks for `get_active_season`, reads `get_season_projection_legacy_shape(season)`, returns `{"season", "available": bool, "reason": str | None, ...compute_portfolio_projection}`. `available` False with reason "draft_in_progress" when `get_config_settings()["draft_active"]` is True and caller is not admin (projection gating rule in CLAUDE.md), "no_projections" when the projection dict is empty, "no_teams" when the player has no picks. Response is HTTP 200 in all these cases.

- [ ] Tests first: three teams (10.5/2.0, 8.0/2.0, 6.0/2.0) -> expected 24.5, std sqrt(12), floor < expected < ceiling, playoff probs in (0,1) and ordered by projected wins, `playoff_prob_any` between max single prob and 1; zero teams -> zeros, no exception; team missing projection skipped; sd 0 does not crash; route tests (TestClient, override `require_auth`, patch loaders): draft_active + non-admin -> `available` False `draft_in_progress`; player with no picks -> `no_teams`; empty projections -> `no_projections`; normal -> `available` True with 3 teams.
- [ ] Implement service function and route (use `load_data()` draft_results filter by season and playerId; reuse `get_active_season(games, draft_results, rules)` exactly as other routes do; admin detection via the role in the auth payload or `_player_role`).
- [ ] Frontend `templates/profile.html`: add a "Season Outlook" card above the form (`id="portfolio-card"`, hidden until data loads) rendering expected wins, floor-ceiling range, playoff chance (any team), and a per-team list with projected wins. Show a neutral message for `draft_in_progress`/`no_teams`/`no_projections` (e.g. "Season outlook appears once the draft is complete."). Fetch `/api/profile/portfolio` inside the existing `DOMContentLoaded` handler, wrapped in its own try/catch so a failure cannot break the profile form. Do not modify existing form logic. Reuse theme tokens; check 390px width.
- [ ] Run new tests plus `pytest tests/test_auth.py -q`. Commit.

## Task 6: ML accuracy season filter

**Files:** Modify `routes/api_routes.py`, `static/js/admin_accuracy.js`, `templates/admin.html`; Test `tests/test_prediction_accuracy_season_filter.py` (new; look at existing accuracy route tests via `grep -rn "predictions/accuracy" tests/` to reuse their patching)

- [ ] Tests first: with `get_candidate_seasons` patched to `[2022, 2023, 2024]` and `get_game_predictions` patched with a call recorder: no `season` param -> all three requested and response shape unchanged; `?season=2023` -> `get_game_predictions` called only with 2023, response `seasons` holds only 2023, `overall` totals equal that season; `?season=1999` (no predictions) -> 200 with empty `seasons` and overall zeros. The response should also include `available_seasons` (list of candidate seasons, always the full list, so the dropdown can populate) in both modes: add as a new key (additive).
- [ ] Implement `season: Optional[int] = Query(None)` (import `Query`/`Optional` if missing); when set, `candidate_seasons = [season]`. Compute `available_seasons` from `get_candidate_seasons()` before filtering.
- [ ] Frontend: add `<select id="accuracy-season-filter">` with an "All seasons" option to the overall banner in `templates/admin.html`; in `static/js/admin_accuracy.js` populate options from `available_seasons` after first load, and on change refetch `/api/predictions/accuracy?season=N` (omit param for all) and rerender. Keep existing rendering untouched; preserve selection across rerender.
- [ ] Run new tests. Commit.

## Task 7: Docs and final verification

- [ ] Update docs: `CLAUDE.md` (Scripts list unchanged; add: `require_db`, lazy `get_db()` contract returning None, `/api/pool/status`, `/api/profile/portfolio`, Tuesday weekly eval step in cache_builder and the `nn_weekly_accuracy` line no longer "nothing schedules it", `GIT_SHA` placement, VAPID secret), `DEPLOY.md` (Task 1g note), `docs/prediction_model.md` or `docs/` accuracy note for the season filter and the portfolio math (independence approximation, 9.5-win playoff proxy). Add a short `docs/superpowers/plans/completed/` move of this plan after merge only if the repo convention does so (it does for finished plans).
- [ ] Run `python -m pytest tests/ -n auto -q`; compare failures with the Task 0 baseline: zero new failures.
- [ ] Run `graphify update .` if graphify-out exists.
- [ ] Merge to main (fast-forward or merge commit), remove worktree, report.
