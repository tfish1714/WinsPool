# Prediction Pipeline Consolidation and Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One canonical implementation of the ATS-pick, edge-vs-Vegas, and ATS-grading rules; distinct PUSH handling in the "Why TEAM?" modal; a cheaper explain endpoint; one stale docstring fixed.

**Architecture:** Extend `services/utils.py` (already home to `derive_prediction_scalars`) with `edge_vs_vegas()`, `pick_ats_team()` and `prob_to_model_spread()`; add `grade_ats_pick()` next to `grade_bet()` in `services/betting_screener_service.py`. Route and legacy-path call sites adopt them. The explain endpoint returns `is_correct_ats` as `true | false | "push" | null`; a new pure `static/js/grade_badge.js` renders it.

**Tech Stack:** Python, FastAPI, pandas, pytest, vanilla JS (ES modules), Node for the JS test.

**Specs:** `docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md` (Rollout item 4), `docs/superpowers/specs/2026-09-20-model-prediction-pipeline-tail-followups.md`

## Global Constraints

- No emojis in code, comments, docs, or commit messages.
- Zero-deletion policy: no function, test, or file is removed. Dead-but-present functions are rewired, not deleted.
- Sign convention (nflverse): positive `spread_line` / `model_spread` means the home team is favored.
- ATS pick rule stays exactly: with a valid Vegas line, home if `model_spread > vegas_line` else away (a tie goes to away); with no valid line, the SU winner. `edge_vs_vegas = round(model_spread - vegas_line, 1)`.
- `is_correct_ats` on `/api/predictions/explain` becomes `true | false | "push" | null` (`null` = not gradable/unplayed). `/api/admin/predictions/games` keeps `true | false | null` (its accuracy counters must not see a string).
- Services and routes never read `rawdata/`.

## Findings that reshape the brief

1. **The named duplication no longer exists between `prediction_service.py` and `nn_prediction_service.py`.** `nn_prediction_service.py::build_ensemble_lookup`, `nn_projection_engine.py::build_mc_prediction_entry`, `cache_builder.py` and the backfill script already share `derive_prediction_scalars()` in `services/utils.py`.
2. **What is still duplicated:**
   - `services/prediction_service.py::enrich_schedule_with_predictions` has its own ATS heuristic ("underdog if |spread| <= 3, else favorite"), which contradicts the sign convention (`away if spread < 0` picks the favorite). It has no production caller (only `tests/test_portfolio.py`).
   - `services/nn_projection_engine.py::enrich_schedule_with_nn_predictions` re-implements the canonical ATS rule (using an unrounded implied spread). No callers at all.
   - `routes/api_routes.py` (explain fallback) and `routes/admin_routes.py` (per-game table) each recompute `round(model_spread - vegas_line, 1)`; the admin route also has its own ATS win/loss/push formula instead of `grade_bet()`.
3. **`_gradeIcon("push")` would render a check mark**, because a non-empty string is truthy. The backend and frontend push changes must ship together.
4. The task brief names `tests/test_prediction_service.py`, `tests/test_api_routes.py` and `tests/test_explain.py`; none exist. Tests go where the code under test already has them: `tests/test_utils.py`, `tests/test_betting_screener_service.py`, `tests/test_api_endpoints.py` (`TestPredictionExplainGrading`), `tests/test_admin_routes.py`, `tests/test_portfolio.py`, plus new `tests/test_grade_badge_js.py` and `tests/test_legacy_enrichment.py`.
5. The tail spec's "pick normalizes to neither team silently grades as away" item is resolved as a by-product: `grade_ats_pick()` returns `None` for a pick that matches neither team.

## File Structure

- Modify `services/utils.py`: `edge_vs_vegas`, `pick_ats_team`, `prob_to_model_spread`; `derive_prediction_scalars` uses them.
- Modify `services/betting_screener_service.py`: `grade_ats_pick`.
- Modify `services/nn_prediction_service.py`: use `prob_to_model_spread`.
- Modify `services/prediction_service.py`, `services/nn_projection_engine.py`: legacy enrich functions call `derive_prediction_scalars`.
- Modify `routes/api_routes.py`, `routes/admin_routes.py`.
- Create `static/js/grade_badge.js`; modify `static/js/schedule_explain.js`.
- Modify `services/nn_feature_engine.py` (docstring), `docs/api_endpoints.md`.

---

### Task 1: Canonical scalar helpers

**Files:** Modify `services/utils.py`; Test `tests/test_utils.py`

**Interfaces:** Produces
- `edge_vs_vegas(model_spread, vegas_line) -> float | None` (None when either is None, NaN, or non-numeric)
- `pick_ats_team(home_team, away_team, winner, model_spread, vegas_line) -> str`
- `prob_to_model_spread(home_prob: float) -> float` (clip to `[PROB_CLIP_MIN, PROB_CLIP_MAX]`, `round(SPREAD_TO_PROB_SCALE * ln(p/(1-p)), 1)`)

- [ ] Tests: edge rounding and sign; None/NaN/string inputs; pick home when `model_spread > line`, away when less or equal, winner when no line; `prob_to_model_spread(0.5) == 0.0`, symmetric, clipped at the extremes; `derive_prediction_scalars` output unchanged for the existing cases.
- [ ] Implement; make `derive_prediction_scalars` call the helpers.
- [ ] `pytest tests/test_utils.py`; commit.

### Task 2: Canonical ATS grader

**Files:** Modify `services/betting_screener_service.py`; Test `tests/test_betting_screener_service.py`

**Interfaces:** Produces `grade_ats_pick(pick, home_team, away_team, home_score, away_score, vegas_line) -> "win" | "loss" | "push" | None`. Team names are compared after `nn_feature_engine._normalize_team`. Returns `None` for a missing pick, a pick matching neither team, or anything `grade_bet()` returns `None` for.

- [ ] Tests: win, loss, push for each side with the correct sign convention (home favored by 3, wins by 1: home pick loses, away pick wins); `LAR` vs `LA` normalization; pick matching neither team returns `None`; missing scores or line return `None`.
- [ ] Implement on top of `grade_bet()`; commit.

### Task 3: Routes adopt the helpers; explain handles pushes and filters first

**Files:** Modify `routes/api_routes.py::get_prediction_explain`, `routes/admin_routes.py` (per-game table); Tests `tests/test_api_endpoints.py`, `tests/test_admin_routes.py`

- [ ] Tests (explain): push returns `is_correct_ats == "push"`; unplayed returns `null`; win and loss unchanged; correctly signed SU-right/ATS-wrong divergence (home favored by 3 via `spread_line=3.0`, home wins by 1, pick home: `is_correct` true, `is_correct_ats` false); pick matching neither team returns `null`; the game lookup normalizes team names only for rows of the requested season and week (assert on the rows `_normalize_team` sees); edge fallback uses the shared helper.
- [ ] Tests (admin): push stays `null`, win/loss unchanged, edge fallback unchanged.
- [ ] Implement: pre-filter `all_games` by `season` and `week` before `.apply(_normalize_team)`; use `edge_vs_vegas()` and `grade_ats_pick()`; map `"push"` through unchanged in explain, to `None` in admin.
- [ ] `pytest tests/test_api_endpoints.py tests/test_admin_routes.py`; commit.

### Task 4: PUSH badge in the modal

**Files:** Create `static/js/grade_badge.js`; Modify `static/js/schedule_explain.js`; Test `tests/test_grade_badge_js.py`

**Interfaces:** Produces `gradeBadge(value) -> string` HTML: `true` check, `false` cross, `"push"` neutral `PUSH`, anything else `''`. `schedule_explain.js` uses it for the ATS badge only (the SU badge keeps its two-state icon; the SU value is never `"push"`).

- [ ] Node-driven tests (skipped without Node): the four states plus `undefined`; the push markup contains `PUSH` and no check or cross.
- [ ] Implement, wire into the ATS card; browser check of the modal at desktop and about 390px if a login can be minted locally; commit.

### Task 5: Legacy enrichment paths use the canonical rule

**Files:** Modify `services/prediction_service.py::enrich_schedule_with_predictions`, `services/nn_projection_engine.py::enrich_schedule_with_nn_predictions`, `services/nn_prediction_service.py`; Tests `tests/test_legacy_enrichment.py`, `tests/test_nn_prediction_service.py`, existing `tests/test_portfolio.py`

- [ ] Tests: with a stubbed win-probability source, the legacy functions produce the same `pred_ats_pick` as `derive_prediction_scalars` for home-favored, away-favored, no-line, and tie-to-away cases; `build_ensemble_lookup` model_spread unchanged (parity with the previous inline formula).
- [ ] Implement using `prob_to_model_spread` and `derive_prediction_scalars`; commit.

### Task 6: Docstring, docs, verification, archive

- [ ] Fix the `compute_preseason_player_profiles()` docstring in `services/nn_feature_engine.py` (past tense, no live reference to the deleted function).
- [ ] Update `docs/api_endpoints.md` (`is_correct_ats` tri-state plus `"push"` on the explain endpoint).
- [ ] `pytest tests/ -n auto`; compare any failure against a clean checkout.
- [ ] Move this plan and both specs to `completed/` (the e2e review spec only if nothing else in its Rollout remains open; otherwise record item 4 as done in its status line and leave it in place); use superpowers:verification-before-completion and superpowers:finishing-a-development-branch.
