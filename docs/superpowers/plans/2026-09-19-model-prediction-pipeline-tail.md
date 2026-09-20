# Model Prediction Pipeline Tail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out Rollout Items 5, 6, and 7 from the model-prediction-e2e-review spec: give the resimulate scheduling constant real safety margin backed by a measured profiling breakdown, delete confirmed-dead code across three files, and make the "Why TEAM?" modal show SU/ATS grades for already-completed games like the admin per-game table already does.

**Architecture:** Three independent, small changes: (1) a one-line scheduling-constant bump plus a documentation/regression-test pair, justified by profiling already done during planning; (2) mechanical deletion of code confirmed (via grep across the whole repo, not just this file) to have zero remaining callers; (3) a backend field addition to an existing endpoint that reuses an existing grading helper (no new duplicate formula) plus a frontend rendering addition using the existing visual language from the admin page's equivalent feature.

**Tech Stack:** Python, FastAPI, pandas, pytest, vanilla ES6 modules (no framework) for the frontend piece.

**Spec:** `docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md` (Rollout Items 5, 6, 7 only — items 2/3/4/8/9 are separate, out-of-scope work items from the same spec's rollout list)

## Global Constraints

- No test in this plan may hit the network or a real Firestore instance.
- Item 6 is deletion-only: do not add, refactor, or "improve" anything beyond removing the confirmed-dead code named in each step. Do not delete `services/prediction_service.py`'s `PredictionService` class itself, or the file — the class is still targeted by existing unit tests (`tests/test_game_prediction.py`, `tests/test_elo.py`, `tests/test_portfolio.py`), and the file also exports `_get_travel_distance`, which is live (imported by `services/nn_projection_engine.py` and `services/nn_feature_engine.py`) — the file stays, only the dead routes/imports pointing at the class go.
- Item 7's ATS grading must reuse `services/betting_screener_service.py::grade_bet()` — do not write a second implementation of the ATS win/loss/push formula. This has already been verified to have identical semantics to the inline logic in `routes/admin_routes.py::get_predictions_games` (home covers when `home_score - away_score > vegas_line`).
- Run `pytest tests/ -n auto` after every task and confirm 0 regressions before moving to the next task. This suite has known, pre-existing, order-dependent flakes under parallel execution (different unrelated tests fail on different runs, always passing in isolation and on immediate re-run) — if a failure looks unrelated to the file(s) a task touched, re-run that specific test in isolation before treating it as a regression.

---

## Task 1: Bump `RESIMULATE_LEAD_MINUTES` with a measured justification

**Files:**
- Modify: `scripts/schedule_kickoffs.py:58-85` (the `SYNC_LEAD_MINUTES`/`PREDICT_LEAD_MINUTES`/`RESIMULATE_LEAD_MINUTES` block and its comment)
- Test: `tests/test_schedule_kickoffs.py` (create if it doesn't already exist — check first; if it exists, append)

**Interfaces:**
- Consumes: nothing new.
- Produces: `RESIMULATE_LEAD_MINUTES = 30` (was `20`) — no code elsewhere references the old value by number, only by name, so this is a pure constant change.

**Background (already done during planning, not part of the implementer's task):** `services/nn_projection_engine.py::NNProjectionEngine.initialize()` was profiled locally by timing its four sub-steps individually against the current season: `compute_qb_availability_flags()` took 18.4s, `build_master_feature_table(min_season=2020, max_season=season-1)` took 615.7s, `compute_roster_value()` took 4.7s, and `compute_preseason_player_profiles()` didn't run at all (this season's `snap_counts` file already has data, so the `snap_empty` branch is skipped). Total: 638.9s (10.6 min), with `build_master_feature_table()` alone accounting for 96% of it. This independently confirms the spec's own measurement (`docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md` Stage 3 finding 2: a 443.8s warm-environment run, ~428s of which was `engine.initialize()` + `simulate_season()` combined — 96%+ of that in the feature-table build specifically). `build_master_feature_table()` is a large, shared function also used by all three model-training scripts and `backfill_schedule_predictions.py`; narrowing its `min_season` for this one call site risks silently breaking any rolling/cross-season feature that needs multi-year history to compute correctly for the one season actually used afterward — that risk has NOT been verified in this planning pass, so it is out of scope here (see Self-Review Notes at the end of this plan for the follow-up). The safe, immediately-actionable fix is a scheduling-constant change: `RESIMULATE_LEAD_MINUTES` fires the resimulate step relative to kickoff and must stay less than `PREDICT_LEAD_MINUTES` (60) so it still fires after the routine predict run (see the existing comment at `scripts/schedule_kickoffs.py:60-61`); bumping it from 20 to 30 keeps that ordering (30 < 60) while roughly doubling the safety margin against the measured ~639-644s runtime plus an unknown Cloud Run cold-start penalty.

- [ ] **Step 1: Check whether `tests/test_schedule_kickoffs.py` already exists**

Run: `ls tests/test_schedule_kickoffs.py` (or equivalent). If it exists, read it first so your new test matches its existing style (imports, fixtures) before writing Step 2's test. If it doesn't exist, create it fresh with just this one test for now.

- [ ] **Step 2: Write the failing test**

Add this test (to the existing file, or to a new `tests/test_schedule_kickoffs.py`):

```python
def test_resimulate_lead_minutes_fires_after_routine_predict():
    """RESIMULATE_LEAD_MINUTES must stay strictly less than PREDICT_LEAD_MINUTES
    (smaller lead = closer to kickoff = fires later in absolute time), or the
    resimulate step could run before the routine predict step and get
    overwritten by it -- see the ordering comment in schedule_kickoffs.py.
    Also pins the specific value chosen after profiling engine.initialize()."""
    from scripts.schedule_kickoffs import RESIMULATE_LEAD_MINUTES, PREDICT_LEAD_MINUTES
    assert RESIMULATE_LEAD_MINUTES == 30
    assert RESIMULATE_LEAD_MINUTES < PREDICT_LEAD_MINUTES
```

If creating a new file, it needs no other imports or fixtures beyond this.

- [ ] **Step 3: Run the test to verify it fails**

Run: `pytest tests/test_schedule_kickoffs.py -k resimulate_lead_minutes -v`
Expected: FAIL — `assert 20 == 30`

- [ ] **Step 4: Update the constant and its comment**

In `scripts/schedule_kickoffs.py`, use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
#      passed here (unlike a local/manual invocation).
#   3. engine.initialize(year), which runs build_master_feature_table() across
#      SIX seasons (min_season=2020..year-1) plus compute_roster_value() --
#      simulate_season() being scoped to one season does NOT make
#      initialize() itself cheap; it runs before simulate_season() either way.
#   4. The Monte Carlo simulate_season() call itself (RESIMULATE_N_SIMS).
# NOT yet validated against a measured runtime of --resimulate (Task 6) in
# production: before relying on this in-season, time a real invocation (see
# Step 9 below) and adjust this constant if it runs longer than the margin
# allows. 20 is an unvalidated placeholder, not a measured value.
RESIMULATE_LEAD_MINUTES = 20
```

new_string:
```python
#      passed here (unlike a local/manual invocation).
#   3. engine.initialize(year), which runs build_master_feature_table() across
#      SIX seasons (min_season=2020..year-1) plus compute_roster_value() --
#      simulate_season() being scoped to one season does NOT make
#      initialize() itself cheap; it runs before simulate_season() either way.
#   4. The Monte Carlo simulate_season() call itself (RESIMULATE_N_SIMS).
# Measured (docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md,
# Stage 3 finding 2, and independently reconfirmed during planning): a real
# invocation of engine.initialize() takes roughly 440-640s depending on
# environment, and build_master_feature_table() alone is ~96% of that --
# compute_qb_availability_flags()/compute_roster_value() are single-digit
# seconds each. That's up to ~53% of the original 20-minute budget BEFORE any
# Cloud Run cold-start penalty (commonly 30-90s+ for a TensorFlow-loading
# image). 30 keeps real margin against that measured range while staying
# comfortably less than PREDICT_LEAD_MINUTES (60), so this step still fires
# after the routine predict run, not before it.
RESIMULATE_LEAD_MINUTES = 30
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_schedule_kickoffs.py -k resimulate_lead_minutes -v`
Expected: PASS

- [ ] **Step 6: Run the full file to confirm no regressions**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: PASS (all tests in the file, old and new)

- [ ] **Step 7: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py
git commit -m "fix: bump RESIMULATE_LEAD_MINUTES to 30 with a measured justification"
```

---

## Task 2: Delete dead code — `compute_preseason_roster_features()`

**Files:**
- Modify: `services/nn_feature_engine.py:375-481` (delete the function)
- Modify: `services/nn_projection_engine.py:19-27` (remove the now-dangling import)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — pure deletion. Confirmed via repo-wide grep that `compute_preseason_roster_features` has exactly one definition (`services/nn_feature_engine.py:375`), one import (`services/nn_projection_engine.py:24`), zero call sites anywhere (including inside `nn_projection_engine.py` itself — it's imported but never invoked), and zero test coverage.

- [ ] **Step 1: Confirm zero regressions before touching anything (baseline)**

Run: `pytest tests/ -n auto -q` and note the pass count. This task removes code with no test coverage, so there is no red-to-green cycle — the verification is that the count doesn't change (other than any pre-existing flakes, per this plan's Global Constraints).

- [ ] **Step 2: Delete the function**

In `services/nn_feature_engine.py`, use the Edit tool with this exact old_string/new_string pair (deletes the entire function, lines 375-481, leaving the surrounding blank-line spacing and the next function's section-header comment intact):

old_string:
```python
def compute_preseason_roster_features(target_season: int, rawdata_dir) -> dict:
    """Build per-team OL snap quality + DL performance from current roster + prior-season data.

    OL: snap count × age multiplier for each OL player on the target-season roster,
        using the prior season's snap data. Rookies/new signings get median × 0.5.
    DL: individual sacks×6 + pressures×1.5 + qb_hits×1 from prior-season advstats_def,
        age-adjusted and summed per team. Rookies get team-average prior-year contribution.

    Returns {team: {"ol_av": float, "dl_perf": float}}.
    """
```

Read the file at lines 375-481 first (`services/nn_feature_engine.py`) to see the function's full body before deleting — it is long (107 lines) and reproducing all of it here would just be re-typing what's already on disk; delete from the `def compute_preseason_roster_features(...)` line through its closing `return result` line (line 481) inclusive, leaving exactly one blank line before it (matching the existing spacing) and the two blank lines that already precede the next section (`# ---------------------------------------------------------------------------` / `# Player EPA Loading` at what is currently line 484). After deletion, `services/nn_feature_engine.py:375` should be the `# ---------------------------------------------------------------------------` / `# Player EPA Loading` section header that currently starts at line 484 — i.e. exactly 107 lines shorter, with no orphaned blank lines or comments left behind.

- [ ] **Step 3: Remove the dangling import**

In `services/nn_projection_engine.py`, use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
    compute_preseason_roster_features,
    compute_preseason_player_profiles,
    compute_qb_availability_flags,
)
```

new_string:
```python
from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
    compute_preseason_player_profiles,
    compute_qb_availability_flags,
)
```

- [ ] **Step 4: Run the full suite to confirm zero regressions**

Run: `pytest tests/ -n auto -q`
Expected: same pass count as Step 1's baseline (module still imports cleanly, nothing references the removed name).

- [ ] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py services/nn_projection_engine.py
git commit -m "chore: delete dead compute_preseason_roster_features()"
```

---

## Task 3: Delete dead code — the 5 unreachable prediction routes and 2 unused `PredictionService` imports

**Files:**
- Modify: `routes/prediction_routes.py` (delete 6 route function definitions across 5 URL paths, and the now-unused `PredictionConfigRequest` import)
- Modify: `routes/models.py:97-99` (delete the now-fully-dead `PredictionConfigRequest` class)
- Modify: `services/draft_service.py:87` (delete the dead import)
- Modify: `scripts/cache_builder.py:48` (delete the dead import)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing — pure deletion. Confirmed via repo-wide grep: none of the 5 URL paths (`/api/predictions/game`, `/api/predictions/portfolio`, `/api/predictions/ratings`, `/api/admin/predictions/confidence`, `/api/admin/predictions/config`) appear in any test file or any frontend JS file; `PredictionService` is imported in `services/draft_service.py:87` and `scripts/cache_builder.py:48` but never referenced again after the import line in either file; `PredictionConfigRequest` (`routes/models.py:97-99`) is used only by the one route being deleted here. `services/prediction_service.py`'s `PredictionService` class itself, and that module's `_get_travel_distance` (still live, imported by `nn_projection_engine.py`/`nn_feature_engine.py`), are untouched.

- [ ] **Step 1: Confirm zero regressions before touching anything (baseline)**

Run: `pytest tests/ -n auto -q` and note the pass count.

- [ ] **Step 2: Delete the 6 dead route functions from `routes/prediction_routes.py`**

Read the current file first (it's not long). Delete these six function definitions, each including its `@router...` decorator, docstring, and body, down to (but not including) the blank lines separating it from the next route:

1. `get_game_prediction` (`@router.get("/predictions/game")`)
2. `get_portfolio_projection` (`@router.get("/predictions/portfolio")`)
3. `get_elo_ratings` (`@router.get("/predictions/ratings")`)
4. `get_draft_confidence` (`@router.get("/admin/predictions/confidence")`)
5. `get_prediction_config` (`@router.get("/admin/predictions/config")`)
6. `update_prediction_config` (`@router.post("/admin/predictions/config")`)

These six are currently the first six route definitions in the file (everything between the `router = APIRouter(prefix="/api")` line and the `@router.get("/admin/elo_history")` route, which stays). After deletion, the file should start its route definitions with `@router.get("/admin/elo_history")` (the `get_elo_history` function) — the first surviving route — immediately after the `router = APIRouter(...)` line and its blank-line spacing.

Then remove the now-unused import at the top of the same file — use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
from routes.models import PredictionConfigRequest
from services.betting_screener_service import load_predictions_by_season
```

new_string:
```python
from services.betting_screener_service import load_predictions_by_season
```

- [ ] **Step 3: Delete the now-fully-dead `PredictionConfigRequest` class from `routes/models.py`**

Use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
class PredictionConfigRequest(BaseModel):
    elo_weight: float = 0.7
    simulations: int = 1000


# --- Mock Draft ---
```

new_string:
```python
# --- Mock Draft ---
```

- [ ] **Step 4: Delete the dead import in `services/draft_service.py`**

Use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
    from services.prediction_service import PredictionService
    
    # 2. Load Data (GRANULAR: Only fetches current season as we use cached analytics)
```

new_string:
```python
    # 2. Load Data (GRANULAR: Only fetches current season as we use cached analytics)
```

Leave the comment two lines above the deleted import (`# unless we actually need to initialize the PredictionService history.`, part of a larger comment block about avoiding an unnecessary full Firestore fetch) untouched — it's explaining a caching decision made for reasons unrelated to this dead import, and read on its own it still makes sense as-is; do not edit it.

- [ ] **Step 5: Delete the dead import in `scripts/cache_builder.py`**

Use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
import services.analysis_service as analysis
from services.prediction_service import PredictionService
from services.nn_projection_engine import (
```

new_string:
```python
import services.analysis_service as analysis
from services.nn_projection_engine import (
```

- [ ] **Step 6: Run the full suite to confirm zero regressions**

Run: `pytest tests/ -n auto -q`
Expected: same pass count as Step 1's baseline.

- [ ] **Step 7: Manually smoke-check the import chain**

Run: `python -c "import routes.prediction_routes; import services.draft_service; import scripts.cache_builder"` (the last one requires `USE_LOCAL_DATA=True` or real Firebase credentials to fully succeed at module scope per that script's own top-level `initialize_firebase()` call — if it fails for a credentials reason unrelated to this change, that's expected in this environment and not a regression; the goal of this check is confirming there's no `ImportError`/`NameError` from the deletions themselves, which would surface before the credentials check runs).

- [ ] **Step 8: Commit**

```bash
git add routes/prediction_routes.py routes/models.py services/draft_service.py scripts/cache_builder.py
git commit -m "chore: delete 5 dead prediction routes and 2 unused PredictionService imports"
```

---

## Task 4: Backend — add SU/ATS grading to `GET /api/predictions/explain`

**Files:**
- Modify: `routes/api_routes.py:226-279` (`get_prediction_explain`)
- Test: `tests/test_api_endpoints.py`

**Interfaces:**
- Consumes: `services.betting_screener_service.grade_bet(side: str, home_score, away_score, spread_line) -> Optional[str]` (already exists, returns `"win"`/`"loss"`/`"push"`/`None` — do not modify it).
- Produces: the endpoint's JSON response gains five new top-level keys: `actual_winner: str | None`, `home_score: int | None`, `away_score: int | None`, `is_correct: bool | None`, `is_correct_ats: bool | None`. All five are `None` for a game with no recorded result yet (future/unplayed game) — this is the signal Task 5's frontend work uses to decide whether to render a grade badge at all.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api_endpoints.py`:

```python
class TestPredictionExplainGrading:
    """Finding 3b: the /api/predictions/explain endpoint (feeds the "Why
    TEAM?" modal) never returned is_correct/is_correct_ats at all, unlike
    /admin/predictions/games (feeds the admin per-game table). This adds
    the same grading, reusing betting_screener_service.grade_bet() rather
    than re-implementing the ATS win/loss/push formula a third time."""

    def _mock_games_df(self, **overrides):
        import pandas as pd
        row = {
            "season": 2024, "week": 1, "home_team": "KC", "away_team": "BUF",
            "result": 3.0, "home_score": 20.0, "away_score": 17.0, "spread_line": -2.5,
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_completed_game_returns_grading_fields(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "KC", "pred_su_conf": 65, "pred_ats_pick": "KC",
            "model_spread": -3.0, "explanation": {"vegas_line": -2.5},
        }
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, self._mock_games_df(), None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["actual_winner"] == "KC"
        assert body["home_score"] == 20
        assert body["away_score"] == 17
        assert body["is_correct"] is True   # pred_winner=KC, actual_winner=KC
        assert body["is_correct_ats"] is True  # KC picked ATS; home margin 3 > vegas_line -2.5 -> home covers

    def test_future_game_returns_null_grading_fields(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "KC", "pred_su_conf": 65, "pred_ats_pick": "KC",
            "model_spread": -3.0, "explanation": {"vegas_line": -2.5},
        }
        unplayed = self._mock_games_df(result=None, home_score=None, away_score=None)
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, unplayed, None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["actual_winner"] is None
        assert body["home_score"] is None
        assert body["away_score"] is None
        assert body["is_correct"] is None
        assert body["is_correct_ats"] is None

    def test_wrong_su_pick_grades_as_incorrect(self, monkeypatch, auth_token):
        from unittest.mock import patch
        fake_pred = {
            "pred_winner": "BUF", "pred_su_conf": 55, "pred_ats_pick": "BUF",
            "model_spread": 1.0, "explanation": {"vegas_line": -2.5},
        }
        with patch("services.cache_service.get_game_predictions", return_value={"W01_KC_BUF": fake_pred}), \
             patch("routes.api_routes.load_data", return_value=(None, None, self._mock_games_df(), None, None, None, None)):
            resp = client.get(
                "/api/predictions/explain?season=2024&week=1&home=KC&away=BUF",
                headers={"Authorization": auth_token},
            )
        body = resp.json()
        assert body["is_correct"] is False   # pred_winner=BUF, actual_winner=KC
        assert body["is_correct_ats"] is False  # BUF picked ATS; away margin -3 < vegas_line(-away)=2.5 -> away does not cover
```

Note on the two different patch targets used above: `load_data` is imported at module level in `routes/api_routes.py` (`from services.data_service import load_data`, line 11), so `patch("routes.api_routes.load_data", ...)` works directly. `get_game_predictions` is imported *inside* `get_prediction_explain`'s own function body (`from services.cache_service import get_game_predictions`) — a local import means `routes.api_routes` never gets `get_game_predictions` as a module-level attribute, so it must be patched at its actual source, `services.cache_service.get_game_predictions`, instead.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_endpoints.py::TestPredictionExplainGrading -v`
Expected: FAIL — `KeyError: 'actual_winner'` (the response doesn't have these keys yet).

- [ ] **Step 3: Add the grading logic**

In `routes/api_routes.py`, use the Edit tool with this exact old_string/new_string pair:

old_string:
```python
@router.get("/predictions/explain")
def get_prediction_explain(season: int, week: int, home: str, away: str, _auth: dict = Depends(require_auth)):
    """Return the stored explanation (feature values) for a single game prediction."""
    try:
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team
        import math
        ht = _normalize_team(home)
        at = _normalize_team(away)
        key = f"W{week:02d}_{ht}_{at}"
        preds = get_game_predictions(season)
        pred = preds.get(key)
        if not pred:
            return not_found("No prediction found for this game.")

        # If the stored explanation has no vegas_line, fall back to nfl_games.spread_line.
        # This is the same fallback used by /admin/predictions/games.
        ex = dict(pred.get("explanation") or {})
        if ex.get("vegas_line") is None:
            _, _, all_games, _, _, _, _ = load_data()
            if not all_games.empty and "spread_line" in all_games.columns:
                mask = (
                    (all_games["season"] == season) &
                    (all_games["week"] == week) &
                    (all_games["home_team"].apply(_normalize_team) == ht) &
                    (all_games["away_team"].apply(_normalize_team) == at)
                )
                row = all_games[mask]
                if not row.empty:
                    sl = row.iloc[0].get("spread_line")
                    try:
                        sv = float(sl)
                        if not math.isnan(sv):
                            ex["vegas_line"] = round(sv, 1)
                            if ex.get("edge_vs_vegas") is None and pred.get("model_spread") is not None:
                                ex["edge_vs_vegas"] = round(pred["model_spread"] - sv, 1)
                    except (TypeError, ValueError):
                        pass
            pred = {**pred, "explanation": ex}
            # Also patch top-level edge_vs_vegas if still missing
            if pred.get("edge_vs_vegas") is None and ex.get("edge_vs_vegas") is not None:
                pred = {**pred, "edge_vs_vegas": ex["edge_vs_vegas"]}

        return JSONResponse(content={
            "key": key,
            "home_team": ht,
            "away_team": at,
            "season": season,
            "week": week,
            **{k: v for k, v in pred.items() if k != "locked"},
        })
    except Exception as e:
        logger.exception("Unhandled error in get_prediction_explain")
        return server_error()
```

new_string:
```python
@router.get("/predictions/explain")
def get_prediction_explain(season: int, week: int, home: str, away: str, _auth: dict = Depends(require_auth)):
    """Return the stored explanation (feature values) for a single game prediction."""
    try:
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team
        from services.betting_screener_service import grade_bet
        import math
        import pandas as pd
        ht = _normalize_team(home)
        at = _normalize_team(away)
        key = f"W{week:02d}_{ht}_{at}"
        preds = get_game_predictions(season)
        pred = preds.get(key)
        if not pred:
            return not_found("No prediction found for this game.")

        ex = dict(pred.get("explanation") or {})

        # One shared game-row lookup feeds both the existing vegas_line
        # fallback below and the new SU/ATS grading (Finding 3b: this
        # endpoint never returned is_correct/is_correct_ats at all, unlike
        # /admin/predictions/games's per-game table). Both grading fields
        # stay None for a future/unplayed game.
        actual_winner, home_score, away_score = None, None, None
        row = None
        _, _, all_games, _, _, _, _ = load_data()
        if not all_games.empty:
            mask = (
                (all_games["season"] == season) &
                (all_games["week"] == week) &
                (all_games["home_team"].apply(_normalize_team) == ht) &
                (all_games["away_team"].apply(_normalize_team) == at)
            )
            matched = all_games[mask]
            if not matched.empty:
                row = matched.iloc[0]
                res = row.get("result")
                if pd.notna(res):
                    actual_winner = ht if res > 0 else (at if res < 0 else None)
                    hs, aws = row.get("home_score"), row.get("away_score")
                    home_score = int(hs) if pd.notna(hs) else None
                    away_score = int(aws) if pd.notna(aws) else None

        # If the stored explanation has no vegas_line, fall back to nfl_games.spread_line.
        # This is the same fallback used by /admin/predictions/games.
        if ex.get("vegas_line") is None and row is not None:
            sl = row.get("spread_line")
            try:
                sv = float(sl)
                if not math.isnan(sv):
                    ex["vegas_line"] = round(sv, 1)
                    if ex.get("edge_vs_vegas") is None and pred.get("model_spread") is not None:
                        ex["edge_vs_vegas"] = round(pred["model_spread"] - sv, 1)
            except (TypeError, ValueError):
                pass
            pred = {**pred, "explanation": ex}
            # Also patch top-level edge_vs_vegas if still missing
            if pred.get("edge_vs_vegas") is None and ex.get("edge_vs_vegas") is not None:
                pred = {**pred, "edge_vs_vegas": ex["edge_vs_vegas"]}

        # SU grading -- only meaningful once the game has an actual winner.
        is_correct = None
        pw = pred.get("pred_winner")
        if actual_winner is not None and pw is not None:
            is_correct = (_normalize_team(str(pw)) == actual_winner)

        # ATS grading -- reuses grade_bet(), the same helper
        # services/betting_screener_service.py's backtesting already uses,
        # rather than a second copy of the win/loss/push formula.
        is_correct_ats = None
        pred_ats_pick = pred.get("pred_ats_pick")
        vegas_line = ex.get("vegas_line")
        if (pred_ats_pick is not None and vegas_line is not None
                and home_score is not None and away_score is not None):
            side = "home" if _normalize_team(str(pred_ats_pick)) == ht else "away"
            grade = grade_bet(side, home_score, away_score, vegas_line)
            if grade in ("win", "loss"):
                is_correct_ats = (grade == "win")

        return JSONResponse(content={
            "key": key,
            "home_team": ht,
            "away_team": at,
            "season": season,
            "week": week,
            **{k: v for k, v in pred.items() if k != "locked"},
            "actual_winner": actual_winner,
            "home_score": home_score,
            "away_score": away_score,
            "is_correct": is_correct,
            "is_correct_ats": is_correct_ats,
        })
    except Exception as e:
        logger.exception("Unhandled error in get_prediction_explain")
        return server_error()
```

Note the one behavior difference from the original: the original's vegas_line fallback was gated on `not all_games.empty and "spread_line" in all_games.columns`; the new version's shared lookup is gated only on `not all_games.empty` (the `"spread_line" in all_games.columns` check moved implicitly into the `row.get("spread_line")` call, which returns `None` safely if the column doesn't exist — `pandas.Series.get()` on a row, not `DataFrame.get()`, behaves like a dict `.get()` and returns `None` for a missing key rather than raising). Confirm this by running the existing (pre-Task-4) test `test_api_predictions_explain_requires_auth` still passes, plus write one more quick manual check if you have doubts: a `DataFrame` row without a `spread_line` column should not raise when `.get("spread_line")` is called on it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_api_endpoints.py -k "explain" -v`
Expected: PASS (all explain-related tests, old and new)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `pytest tests/ -n auto -q`
Expected: 0 unexplained failures (see Global Constraints for the known flake caveat).

- [ ] **Step 6: Commit**

```bash
git add routes/api_routes.py tests/test_api_endpoints.py
git commit -m "feat: add SU/ATS grading to GET /api/predictions/explain"
```

---

## Task 5: Frontend — render SU/ATS grade badges in the "Why TEAM?" modal

**Files:**
- Modify: `static/js/schedule_explain.js:60-119` (`renderExplanation`, specifically the "ML Pick" and "ATS Pick" cards)

**Interfaces:**
- Consumes: the five new response fields from Task 4 (`is_correct`, `is_correct_ats`, `actual_winner`, `home_score`, `away_score` — all `null` in JSON for an ungraded/future game, which `renderExplanation`'s destructuring will see as `undefined` after JSON parsing... no, `null` stays `null` through `JSON.parse` and `fetch().json()` — treat both `null` and `undefined` as "no grade available" in the render logic below, since `data.is_correct` will literally be `null` for a future game, not missing).
- Produces: no new interfaces — this is the final consumer of Task 4's fields, nothing downstream depends on this task.

There is no test framework for this project's frontend JS (per `CLAUDE.md`: "There is still no automated JS *unit* test suite"). Verification for this task is manual, in-browser, per `CLAUDE.md`'s standing instruction for UI-visible changes — do this at the end of the task, not skipped.

- [ ] **Step 1: Add a local grade-icon helper**

Read `static/js/schedule_explain.js`'s current top-of-file section (before `renderExplanation`) to find where its other small local helpers (`_bar`, `_fmtLine`, `_teamAdv` — whichever are defined at module scope before `renderExplanation`) live, and add this one alongside them, matching the file's existing function-declaration style:

```javascript
function _gradeIcon(isCorrect) {
    if (isCorrect === null || isCorrect === undefined) return '';
    return isCorrect
        ? '<span style="color:var(--accent-green); font-weight:700;">✓</span>'
        : '<span style="color:var(--accent-red); font-weight:700;">✗</span>';
}
```

(This mirrors `static/js/admin_accuracy.js`'s `_pgCorrectIcon` visual language exactly — same colors, same ✓/✗ characters — but is not imported from that file, since `admin_accuracy.js` doesn't export it and the two pages load separate, non-overlapping JS bundles. Unlike `_pgCorrectIcon`, this returns an empty string rather than an em-dash placeholder for the null case, since the badge should not render at all for an ungraded game rather than showing a visible "no grade yet" marker inline on the pick card.)

- [ ] **Step 2: Destructure the two new fields `renderExplanation` needs**

In `static/js/schedule_explain.js`, use the Edit tool with this exact old_string/new_string pair:

old_string:
```javascript
function renderExplanation(data) {
    const { home_team, away_team, pred_winner, pred_su_conf, pred_prob,
            pred_ats_pick, model_spread, edge_vs_vegas, explanation: ex,
            currentWeek = 0 } = data;
```

new_string:
```javascript
function renderExplanation(data) {
    const { home_team, away_team, pred_winner, pred_su_conf, pred_prob,
            pred_ats_pick, model_spread, edge_vs_vegas, explanation: ex,
            currentWeek = 0, is_correct = null, is_correct_ats = null } = data;
```

- [ ] **Step 3: Add the SU grade badge to the "ML Pick" card**

Use the Edit tool with this exact old_string/new_string pair:

old_string:
```javascript
    // Card 1: ML Pick
    const pickCard = `
        <div style="flex:1; min-width:130px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ML Pick</div>
            <div style="font-size:1.3rem; font-weight:800; color:${predColor};">${pred_winner}</div>
            <div style="font-size:0.75rem;">${isMcSimulation ? `wins ${pred_su_conf}% of simulations` : `${pred_su_conf}% confidence`}</div>
            ${confBar}
        </div>`;
```

new_string:
```javascript
    // Card 1: ML Pick
    const suGrade = _gradeIcon(is_correct);
    const pickCard = `
        <div style="flex:1; min-width:130px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ML Pick${suGrade ? ` · SU ${suGrade}` : ''}</div>
            <div style="font-size:1.3rem; font-weight:800; color:${predColor};">${pred_winner}</div>
            <div style="font-size:0.75rem;">${isMcSimulation ? `wins ${pred_su_conf}% of simulations` : `${pred_su_conf}% confidence`}</div>
            ${confBar}
        </div>`;
```

- [ ] **Step 4: Add the ATS grade badge to the "ATS Pick" card**

Use the Edit tool with this exact old_string/new_string pair:

old_string:
```javascript
    // Card 3: ATS pick (only if it differs from SU, or always show)
    const atsDiffers = pred_ats_pick && pred_ats_pick !== pred_winner;
    const atsCard = pred_ats_pick ? `
        <div style="flex:1; min-width:110px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ATS Pick</div>
            <div style="font-size:1.3rem; font-weight:800; color:${atsDiffers ? 'var(--accent-gold)' : predColor};">${pred_ats_pick}</div>
            <div style="font-size:0.72rem; color:var(--text-secondary);">${atsDiffers ? '⚡ differs from SU' : 'vs spread'}</div>
        </div>` : '';
```

new_string:
```javascript
    // Card 3: ATS pick (only if it differs from SU, or always show)
    const atsDiffers = pred_ats_pick && pred_ats_pick !== pred_winner;
    const atsGrade = _gradeIcon(is_correct_ats);
    const atsCard = pred_ats_pick ? `
        <div style="flex:1; min-width:110px; padding:10px 12px; border-radius:8px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);">
            <div style="font-size:0.7rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:0.05em;">ATS Pick${atsGrade ? ` · ${atsGrade}` : ''}</div>
            <div style="font-size:1.3rem; font-weight:800; color:${atsDiffers ? 'var(--accent-gold)' : predColor};">${pred_ats_pick}</div>
            <div style="font-size:0.72rem; color:var(--text-secondary);">${atsDiffers ? '⚡ differs from SU' : 'vs spread'}</div>
        </div>` : '';
```

- [ ] **Step 5: Manual in-browser verification (required — no automated JS test suite covers this)**

Start the dev server (`uvicorn main:app --reload`) and open the schedule page. Open the "Why TEAM?" modal for:
1. A completed game from an earlier week this season — confirm the "ML Pick" card's header now shows `· SU ✓` or `· SU ✗` next to "ML Pick", and (if an ATS pick differs or is shown) the "ATS Pick" card's header shows the ATS grade the same way, with green for correct and red for incorrect.
2. A future/upcoming game (later week than the currently active one) — confirm NEITHER card shows a grade badge (no `·` separator, no icon) — this is `is_correct`/`is_correct_ats` both being `null` for an ungraded game.
3. Check both a desktop-width and a narrow (~390px) mobile viewport — this is a small text addition, not new layout, but confirm the added `· SU ✓` text doesn't overflow or wrap awkwardly on the mobile card width, per `CLAUDE.md`'s standing UI-verification instruction.

- [ ] **Step 6: Commit**

```bash
git add static/js/schedule_explain.js
git commit -m "feat: show SU/ATS grade badges in the Why TEAM? modal for completed games"
```

---

## Self-Review Notes

**Spec coverage:**
- Rollout Item 5 (RESIMULATE_LEAD_MINUTES timing) → Task 1: profiled during planning (documented in Task 1's Background), constant bumped with a measured comment, regression test added.
- Rollout Item 6 (dead code) → Tasks 2 and 3: `compute_preseason_roster_features()` + its import (Task 2); the 5 dead route paths, `PredictionConfigRequest`, and the 2 dead `PredictionService` imports (Task 3).
- Rollout Item 7 (Finding 3b, "Why TEAM?" modal missing SU/ATS grade) → Tasks 4 and 5: backend grading fields reusing `grade_bet()` (Task 4), frontend badge rendering (Task 5).
- Explicitly out of scope per the plan's own framing (items 2, 3, 4, 8, 9 of the same spec's rollout list) — nothing in this plan touches feature-computation versioning, the promotion gate, the duplicated ATS/edge-vs-vegas *formula* (a different duplication than this plan's Task 4, which reuses `grade_bet()` rather than adding a new one), betting pick-type email/screener clarity, or the DVOA-style feature backlog item.

**Placeholder scan:** Task 3, Steps 4 and 5 intentionally ask the implementer to read the current file first before writing the exact old_string, rather than the plan guessing at exact surrounding lines it hasn't verified character-for-character (`services/draft_service.py`'s comment wording, `scripts/cache_builder.py`'s line-48 neighborhood) — this is flagged explicitly as "read first, then edit," not a vague "handle it" instruction, and every other step in the plan gives exact, verified old_string/new_string pairs. No other TBD/TODO-style placeholders present.

**Type consistency:** Task 4 introduces `actual_winner: str | None`, `home_score: int | None`, `away_score: int | None`, `is_correct: bool | None`, `is_correct_ats: bool | None` on the JSON response; Task 5's destructuring (`is_correct = null, is_correct_ats = null`) and `_gradeIcon()` helper consume exactly those two boolean-or-null fields by the same names, with no renaming across the task boundary. Task 4's `grade_bet()` call signature (`side, home_score, away_score, vegas_line`) matches the function's actual existing signature in `services/betting_screener_service.py`, verified by reading it during planning, not assumed.

**A note on scope discipline (not a task, just a planning observation):** while researching Task 4, `routes/admin_routes.py::get_predictions_games` was found to have its own inline ATS win/loss/push computation that is *provably* equivalent to `grade_bet()` (same comparison direction, same push handling) but was written before that helper existed. This plan deliberately does not touch it — refactoring an already-correct, already-tested admin endpoint to consume `grade_bet()` would be a legitimate future simplification, but it's not what Item 7 asked for, and touching working code outside this plan's stated scope is exactly what this plan's own Global Constraints say not to do. Worth a one-line mention to whoever picks up Item 4 (the *other* duplicated-formula item) as an adjacent, optional cleanup.
