# Performance, Data Deduplication and ML Ops Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the duplicate `compute_team_records()` scan on the standings page (GitHub #95), replace the `.iterrows()` result-lookup loops with one shared vectorized helper (GitHub #97, #102), and make the model promotion gate's decisions observable in logs (follow-up cleanup spec).

**Architecture:** `calculate_wins_pool_standings()` and `get_enriched_schedule()` gain an optional precomputed `team_records` argument that the standings route fills once per request. `build_result_lookup()` and `get_candidate_seasons()` become shared helpers in `services/prediction_service.py` consumed by both prediction routes. `services/model_promotion.py` emits one parseable `promotion_gate ...` log line per decision (PROMOTED, REJECTED, SKIPPED_NO_BASELINE) plus one schema-check line, without changing gate semantics.

**Tech Stack:** pandas, FastAPI, pytest with `caplog`.

**Spec:** `docs/superpowers/specs/2026-09-18-feature-version-stamp-followup-cleanup.md` (Observability / durability gaps) for Task 3; GitHub issues #95, #97, #102 for Tasks 1-2.

## Global Constraints

- No emojis anywhere: code, comments, docs, commit messages.
- Zero-deletion policy: no existing function, parameter, response key, log message that a test asserts on, or test is removed. New parameters default to the old behavior.
- Match surrounding style; comments explain why.
- `docs/` is gitignored: commit plan/doc files with `git add -f <exact path>`. Never `git add -f tests`.
- Commit messages end with the two attribution lines given by the session harness.
- Known baseline failures (not regressions): `tests/test_loaded_version.py` (2), `test_firebase_schema`/`test_data_alignment` errors (5), flaky `tests/test_player_analytics.py::test_api_player_analytics_returns_200`. Record the baseline before Task 1.

## Design Rulings (made up front so no task stalls)

1. **Task 0 (rebase):** this worktree branches from `main` before the standings-UX and auth tracks merge. Before Task 1, run `git merge main` (or `git rebase main`) so `tests/test_standings_routes.py`, `services/analysis_service.py` and `routes/standings_routes.py` include Track 1's changes. Resolve conflicts additively; never drop the other track's code.
2. **Audit result for #95 (verified against the code):** the only request that truly computes `compute_team_records()` twice is `wins_pool_by_year` (`calculate_wins_pool_standings(..., games)` and `get_enriched_schedule(...)` each call it for the same season). `wins_pool_weekbyweek` calls `calculate_wins_pool_standings` WITHOUT `games`, so it does not compute records there; `headtohead_history` and `history_routes` call `get_enriched_schedule` once per DIFFERENT year, so those are not duplicates. The plan therefore fixes `wins_pool_by_year` and adds the optional parameter to both functions so other callers can share a precomputed dict; it does not invent work at the other sites. Report this finding in the task report.
3. **`team_records=None` means "compute as before"; an empty dict `{}` is a valid precomputed value** (a season with no played games) and must NOT trigger recomputation. Use `is None` checks, never truthiness.
4. **`build_result_lookup` return shape:** `dict[str, dict]` keyed `W{week:02d}_{HOME}_{AWAY}` (normalized abbreviations) with value `{"winner": str | None, "home_score": int | None, "away_score": int | None, "spread_line": float | None}`. A tie stores `winner: None` (the old API loop skipped ties; consumers there treat `winner is None` as "skip", which is equivalent). The API accuracy route now builds the lookup PER SEASON (`build_result_lookup(all_games, season)`): the old code merged all seasons into one key space with no season component, so the same matchup in the same week of two different seasons could grade one season's prediction against another season's result. Per-season lookups fix that latent collision and cost one vectorized pass in total. Call this behavior change out in the commit message and the task report.
5. **Where helpers live:** `services/prediction_service.py` (the user-approved home), with lazy imports of `services.nn_feature_engine._normalize_team`, `services.db_service.get_db` inside the functions to avoid import cycles and heavy import cost at module load. `get_candidate_seasons()` is extracted even though only `get_prediction_accuracy` currently contains the discovery branch, so the duplication cannot reappear.
6. **Gate logging format:** one line per event, `promotion_gate <event> key=value ...`, values formatted to 4 decimals, `n/a` for missing. INFO for PROMOTED and SKIPPED_NO_BASELINE, WARNING for REJECTED. Existing substrings that tests assert on ("promotion gate skipped", "regressed") are preserved.

## Review Focus

- Standings page for a season with zero played games: `compute_team_records` returns `{}`; the page must render with 0-0 records and must not recompute or crash.
- `team_records` passed but `games` empty/None in `calculate_wins_pool_standings`: global_record must use the passed records (or fall back to "0-0" cleanly), never raise.
- Result lookup with: ties (`result == 0`), the -1000 sentinel, NaN result, week 0 or NaN, empty or NaN team strings, aliased abbreviations (e.g. `LA` and `LAR`), a missing `spread_line` column, NaN scores. Each must match what the old loop produced or be an explicitly documented improvement.
- Two rows producing the same key: the LAST one wins, as in the old sequential loop.
- `get_prediction_accuracy` with no `.local_db` and Firestore unreachable: returns an empty accuracy payload (200), not a 500.
- Gate log lines must never raise on `None` metrics (NN has no `test_auc`) or on a baseline missing one of the two metrics.
- `--force-promote` path: the REJECTED line is still logged before the service overrides it, so the override is traceable.

## File Structure

- Modify `services/analysis_service.py`: `team_records` parameter on `get_enriched_schedule` and `calculate_wins_pool_standings`.
- Modify `routes/standings_routes.py`: compute once in `wins_pool_by_year`.
- Modify `services/prediction_service.py`: `build_result_lookup`, `get_candidate_seasons`.
- Modify `routes/api_routes.py` (`get_prediction_accuracy`) and `routes/admin_routes.py` (`get_predictions_games`): consume the helpers.
- Modify `services/model_promotion.py`: structured logging; `find_same_schema_best` gains optional `model_name`.
- Modify `services/xgb_prediction_service.py`, `services/lr_prediction_service.py`, `services/nn_prediction_service.py`: pass `model_name` to `find_same_schema_best` (one-argument change each).
- Tests: `tests/test_analysis_service.py`, `tests/test_standings_routes.py`, `tests/test_prediction_results.py` (create), `tests/test_api_endpoints.py`, `tests/test_admin_routes.py`, `tests/test_model_promotion.py`.

---

### Task 1: Compute team records once per standings request (#95)

**Files:**
- Modify: `services/analysis_service.py`
- Modify: `routes/standings_routes.py`
- Test: `tests/test_analysis_service.py`, `tests/test_standings_routes.py`

**Interfaces:**
- Consumes: existing `compute_team_records(games, season) -> Dict[str, Dict[str, int]]`.
- Produces: `get_enriched_schedule(games, draft_results, players, season, team_records=None)` and `calculate_wins_pool_standings(standings, draft_results, players, season, games=None, team_records=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis_service.py`:

```python
from unittest.mock import patch


def _games_two_teams(season=3000):
    return pd.DataFrame([
        {"season": season, "week": 1, "game_type": "REG", "home_team": "KC", "away_team": "BAL",
         "result": 3, "home_score": 27, "away_score": 24, "gameday": "2000-09-10"},
        {"season": season, "week": 2, "game_type": "REG", "home_team": "BAL", "away_team": "KC",
         "result": -1000, "home_score": None, "away_score": None, "gameday": "2000-09-17"},
    ])


def _draft_players(season=3000):
    draft = pd.DataFrame([
        {"season": season, "team": "KC", "playerId": 1, "draftPick": 1},
        {"season": season, "team": "BAL", "playerId": 2, "draftPick": 2},
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Ann"}, {"playerId": 2, "fullName": "Bo"}])
    return draft, players


def test_get_enriched_schedule_uses_precomputed_records_without_recomputing():
    from services import analysis_service as a
    draft, players = _draft_players()
    games = _games_two_teams()
    baseline = a.get_enriched_schedule(games, draft, players, 3000)
    records = a.compute_team_records(games, 3000)
    with patch.object(a, "compute_team_records", side_effect=AssertionError("must not recompute")):
        result = a.get_enriched_schedule(games, draft, players, 3000, team_records=records)
    pd.testing.assert_frame_equal(result.reset_index(drop=True), baseline.reset_index(drop=True))


def test_get_enriched_schedule_empty_dict_records_is_not_recomputed():
    from services import analysis_service as a
    draft, players = _draft_players()
    with patch.object(a, "compute_team_records", side_effect=AssertionError("must not recompute")):
        out = a.get_enriched_schedule(_games_two_teams(), draft, players, 3000, team_records={})
    assert set(out["home_record"]) == {"0-0"}


def test_calculate_wins_pool_standings_uses_precomputed_records():
    from services import analysis_service as a
    draft, players = _draft_players()
    games = _games_two_teams()
    standings = pd.DataFrame([
        {"team": "KC", "season": 3000, "wins": 1, "losses": 0, "ties": 0, "scored": 27, "allowed": 24},
        {"team": "BAL", "season": 3000, "wins": 0, "losses": 1, "ties": 0, "scored": 24, "allowed": 27},
    ])
    baseline = a.calculate_wins_pool_standings(standings, draft, players, 3000, games)
    records = a.compute_team_records(games, 3000)
    with patch.object(a, "compute_team_records", side_effect=AssertionError("must not recompute")):
        result = a.calculate_wins_pool_standings(standings, draft, players, 3000, games, team_records=records)
    drop = ["refreshTime"]
    pd.testing.assert_frame_equal(result.drop(columns=drop), baseline.drop(columns=drop))


def test_calculate_wins_pool_standings_records_without_games_frame():
    from services import analysis_service as a
    draft, players = _draft_players()
    standings = pd.DataFrame()
    out = a.calculate_wins_pool_standings(standings, draft, players, 3000, games=None,
                                          team_records={"KC": {"W": 1, "L": 0, "T": 0}})
    kc_rows = out.filter(like="global_record")
    assert not out.empty and kc_rows is not None
```

Append to `tests/test_standings_routes.py` (after Track 1's content; reuse any helper defined there for stubbing `load_data`):

```python
def test_wins_pool_by_year_computes_team_records_exactly_once(monkeypatch):
    import routes.standings_routes as sr
    calls = []
    real = sr.analysis.compute_team_records

    def spy(games, season):
        calls.append(season)
        return real(games, season)

    monkeypatch.setattr(sr.analysis, "compute_team_records", spy)
    # Reuse the fixture-backed render helper from the tiebreaker route test in
    # this file, or stub load_data with a small season of games (see
    # tests/test_wins_pool_missing_standings.py for frame builders).
    resp = _render_wins_pool(monkeypatch, real_analysis=True)
    assert resp.status_code == 200
    assert calls == [3000]
```

If `_render_wins_pool` stubs out `calculate_wins_pool_standings`, add a `real_analysis=True` mode that leaves the real analysis functions in place and feeds `load_data` a consistent small season (games, draft results, players, standings) so both real functions run.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_analysis_service.py tests/test_standings_routes.py -k "precomputed or once or empty_dict or without_games" -v`
Expected: FAIL (`unexpected keyword argument 'team_records'`; spy sees 2 calls).

- [ ] **Step 3: Implement**

`services/analysis_service.py`:

`get_enriched_schedule`: change the signature to `def get_enriched_schedule(games, draft_results, players, season, team_records=None):` and replace

```python
    team_records = compute_team_records(games, season)
```

with

```python
    if team_records is None:
        team_records = compute_team_records(games, season)
```

Add a sentence to the docstring: `team_records`, when given, is reused instead of rescanning `games` (the standings page computes it once and shares it).

`calculate_wins_pool_standings`: change the signature to `def calculate_wins_pool_standings(standings, draft_results, players, season, games=None, team_records=None):` and replace the `# Optional: Attach global team records` block with:

```python
    # Optional: Attach global team records. A precomputed dict (even an empty
    # one: a season with no played games) is used as is; otherwise fall back
    # to computing from `games` when that frame was passed.
    if team_records is None and games is not None and not games.empty:
        team_records = compute_team_records(games, season)
    if team_records is not None:
        wins_pool_standings['global_record'] = wins_pool_standings['team'].apply(
            lambda t: format_team_record(t, team_records)
        )
    else:
        wins_pool_standings['global_record'] = "0-0"
```

`routes/standings_routes.py`, in `wins_pool_by_year` after `games = filter_season(all_games, year)` and the other filters, add:

```python
        # Both analysis calls below need the same per-team W-L-T table; scan
        # the season's games once and share it (GitHub #95).
        team_records = analysis.compute_team_records(games, year) if not games.empty else None
```

and pass it: `analysis.calculate_wins_pool_standings(standings, draft_results, players, year, games, team_records=team_records)` and `analysis.get_enriched_schedule(games, draft_results, players, year, team_records=team_records)`. Do not touch `wins_pool_weekbyweek`, `headtohead_history`, or `history_routes` (see Design Ruling 2).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analysis_service.py tests/test_standings_routes.py tests/test_wins_pool_missing_standings.py tests/test_standings_ordering.py tests/test_analysis_perf.py tests/test_live_standings_route.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/analysis_service.py routes/standings_routes.py tests/test_analysis_service.py tests/test_standings_routes.py
git commit -m "perf: compute team records once per standings request"
```

---

### Task 2: Shared vectorized result lookup and season discovery (#97, #102)

**Files:**
- Modify: `services/prediction_service.py`
- Modify: `routes/api_routes.py`, `routes/admin_routes.py`
- Test: `tests/test_prediction_results.py` (create), `tests/test_api_endpoints.py`, `tests/test_admin_routes.py`

**Interfaces:**
- Consumes: `services.nn_feature_engine._normalize_team`, `services.constants.UNDRAFTED_SENTINEL`.
- Produces:
  - `build_result_lookup(all_games: pd.DataFrame, season: Optional[int] = None) -> dict[str, dict]` (shape in Design Ruling 4)
  - `get_candidate_seasons() -> list[int]` (ints of seasons that have stored game predictions: local `.local_db/game_predictions_*.json` if any exist, else Firestore `game_predictions` collection doc ids; unparseable ids skipped; any Firestore failure returns `[]`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_prediction_results.py`:

```python
"""services/prediction_service.build_result_lookup / get_candidate_seasons --
the canonical replacement for the .iterrows() loops that used to be duplicated
in routes/api_routes.py and routes/admin_routes.py."""
import inspect
import time

import numpy as np
import pandas as pd
import pytest

from services.constants import UNDRAFTED_SENTINEL
from services.prediction_service import build_result_lookup, get_candidate_seasons


def _reference_lookup(all_games, season=None):
    """The original row-wise algorithm (admin_routes version), kept here as the
    oracle the vectorized helper must match."""
    from services.nn_feature_engine import _normalize_team
    out = {}
    if all_games is None or all_games.empty:
        return out
    mask = all_games['result'].notna() & (all_games['result'] != -1000)
    if season is not None:
        mask &= (all_games['season'] == season)
    for _, row in all_games[mask].iterrows():
        wk = row.get('week')
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        res = row.get('result', 0)
        if not wk or not ht or not at:
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        winner = ht if res > 0 else at if res < 0 else None
        sl = row.get('spread_line')
        out[key] = {
            "winner": winner,
            "home_score": int(row.get('home_score')) if row.get('home_score') is not None and not pd.isna(row.get('home_score')) else None,
            "away_score": int(row.get('away_score')) if row.get('away_score') is not None and not pd.isna(row.get('away_score')) else None,
            "spread_line": float(sl) if sl is not None and str(sl) not in ('', 'nan') else None,
        }
    return out


def _games():
    rows = [
        # season, week, home, away, result, hs, as, spread
        (3000, 1, "KC", "BAL", 3, 27, 24, -2.5),
        (3000, 1, "DEN", "LV", -7, 10, 17, 3.0),
        (3000, 2, "SF", "SEA", 0, 20, 20, -6.0),                 # tie
        (3000, 2, "DAL", "NYG", UNDRAFTED_SENTINEL, None, None, 1.0),   # unplayed sentinel
        (3000, 3, "MIA", "NE", np.nan, None, None, np.nan),      # NaN result
        (3000, 0, "GB", "CHI", 4, 14, 10, 1.0),                  # week 0 skipped
        (3000, 4, "", "CHI", 4, 14, 10, 1.0),                    # empty team skipped
        (3000, 5, "LA", "SF", 6, 24, 18, np.nan),                # alias team, NaN spread
        (3001, 1, "KC", "BAL", -3, 20, 23, -1.0),                # same key, other season
        (3000, 6, "KC", "BAL", 10, 30, 20, -1.0),
        (3000, 6, "KC", "BAL", -10, 20, 30, -1.0),               # duplicate key: last wins
    ]
    return pd.DataFrame(rows, columns=["season", "week", "home_team", "away_team", "result",
                                       "home_score", "away_score", "spread_line"])


def test_matches_reference_for_all_seasons():
    assert build_result_lookup(_games()) == _reference_lookup(_games())


def test_matches_reference_for_one_season():
    assert build_result_lookup(_games(), 3000) == _reference_lookup(_games(), 3000)
    assert build_result_lookup(_games(), 3001) == _reference_lookup(_games(), 3001)


def test_season_scoping_prevents_cross_season_key_collision():
    a = build_result_lookup(_games(), 3000)["W01_KC_BAL"]["winner"]
    b = build_result_lookup(_games(), 3001)["W01_KC_BAL"]["winner"]
    assert (a, b) == ("KC", "BAL")


def test_tie_has_none_winner_and_unplayed_rows_are_absent():
    lookup = build_result_lookup(_games(), 3000)
    assert lookup["W02_SF_SEA"]["winner"] is None
    assert "W02_DAL_NYG" not in lookup and "W03_MIA_NE" not in lookup


def test_duplicate_key_last_row_wins():
    assert build_result_lookup(_games(), 3000)["W06_KC_BAL"]["winner"] == "BAL"


def test_missing_spread_column_and_nan_scores_yield_none():
    df = _games().drop(columns=["spread_line"])
    lookup = build_result_lookup(df, 3000)
    assert lookup["W01_KC_BAL"]["spread_line"] is None
    df2 = _games()
    df2.loc[0, ["home_score", "away_score"]] = np.nan
    assert build_result_lookup(df2, 3000)["W01_KC_BAL"]["home_score"] is None


@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_empty_input_returns_empty_dict(empty):
    assert build_result_lookup(empty) == {}


def test_helper_source_has_no_iterrows():
    assert "iterrows" not in inspect.getsource(build_result_lookup)


def test_large_frame_is_fast():
    n = 20000
    df = pd.DataFrame({
        "season": np.repeat(np.arange(2000, 2000 + n // 300 + 1), 300)[:n],
        "week": np.tile(np.arange(1, 19), n // 18 + 1)[:n],
        "home_team": np.tile(["KC", "BAL", "SF", "DEN"], n // 4 + 1)[:n],
        "away_team": np.tile(["LV", "NE", "SEA", "MIA"], n // 4 + 1)[:n],
        "result": np.tile([3, -7, 0, 10], n // 4 + 1)[:n],
        "home_score": 20, "away_score": 17, "spread_line": -1.5,
    })
    start = time.perf_counter()
    build_result_lookup(df)
    assert time.perf_counter() - start < 3.0


def test_candidate_seasons_prefers_local_files(tmp_path, monkeypatch):
    (tmp_path / ".local_db").mkdir()
    for name in ("game_predictions_2024.json", "game_predictions_2023.json", "game_predictions_bad.json"):
        (tmp_path / ".local_db" / name).write_text("{}")
    monkeypatch.chdir(tmp_path)
    assert sorted(get_candidate_seasons()) == [2023, 2024]


def test_candidate_seasons_falls_back_to_firestore(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    monkeypatch.chdir(tmp_path)
    docs = []
    for doc_id in ("2024", "2023", "meta"):
        d = MagicMock(); d.id = doc_id; docs.append(d)
    db = MagicMock(); db.collection.return_value.stream.return_value = docs
    with patch("services.db_service.get_db", return_value=db):
        assert sorted(get_candidate_seasons()) == [2023, 2024]


def test_candidate_seasons_swallows_firestore_failure(tmp_path, monkeypatch):
    from unittest.mock import patch
    monkeypatch.chdir(tmp_path)
    with patch("services.db_service.get_db", side_effect=RuntimeError("down")):
        assert get_candidate_seasons() == []
```

Append to `tests/test_api_endpoints.py` (uses that file's `client`/`auth_token` pattern) a route-equivalence test:

```python
def test_prediction_accuracy_route_matches_expected_aggregation(auth_token, monkeypatch):
    import pandas as pd
    import routes.api_routes as api_routes
    games = pd.DataFrame([
        {"season": 3000, "week": 1, "home_team": "KC", "away_team": "BAL", "result": 3,
         "home_score": 27, "away_score": 24, "spread_line": -2.5},
        {"season": 3000, "week": 1, "home_team": "DEN", "away_team": "LV", "result": -7,
         "home_score": 10, "away_score": 17, "spread_line": 3.0},
    ])
    monkeypatch.setattr(api_routes, "load_data", lambda *a, **k: (None, None, games, None, None, None, None))
    monkeypatch.setattr("services.prediction_service.get_candidate_seasons", lambda: [3000])
    monkeypatch.setattr("services.cache_service.get_game_predictions", lambda s: {
        "W01_KC_BAL": {"locked": True, "pred_winner": "KC"},     # correct
        "W01_DEN_LV": {"locked": True, "pred_winner": "DEN"},    # wrong
        "W01_XX_YY": {"locked": True, "pred_winner": "XX"},      # no result -> ignored
        "W01_SF_SEA": {"locked": False, "pred_winner": "SF"},    # not locked -> ignored
    })
    resp = client.get("/api/predictions/accuracy", headers={"Authorization": auth_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall"] == {"total": 2, "correct": 1, "accuracy": 50.0}
    assert body["seasons"][0]["by_week"] == [{"week": 1, "total": 2, "correct": 1, "accuracy": 50.0}]


def test_prediction_accuracy_route_has_no_iterrows():
    import inspect
    import routes.api_routes as api_routes
    assert "iterrows" not in inspect.getsource(api_routes.get_prediction_accuracy)
```

The route imports `get_candidate_seasons` inside the function body (lazy import), so patching `services.prediction_service.get_candidate_seasons` takes effect; keep it that way.

Append to `tests/test_admin_routes.py` a test that `/api/admin/predictions/games` still returns `actual_winner`, `is_correct`, scores and `vegas_line` fallback for a fixture week, with `load_data` and `get_game_predictions` stubbed (assert one correct, one incorrect, one unplayed game with `actual_winner is None`), plus `assert "iterrows" not in inspect.getsource(admin_routes.get_predictions_games)`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_prediction_results.py tests/test_api_endpoints.py tests/test_admin_routes.py -k "lookup or candidate or iterrows or matches_expected or predictions_games" -v`
Expected: FAIL (helpers missing; `iterrows` still present).

- [ ] **Step 3: Implement**

Append to `services/prediction_service.py`:

```python
# ---------------------------------------------------------------------------
# Result lookup shared by the prediction accuracy / per-game admin routes
# ---------------------------------------------------------------------------

def build_result_lookup(all_games: pd.DataFrame, season: Optional[int] = None) -> Dict[str, dict]:
    """Map W{week:02d}_{HOME}_{AWAY} -> actual result for every completed game.

    Vectorized (no row-wise iteration). Completed means `result` is present and
    not the UNDRAFTED_SENTINEL "unplayed" marker. Rows with week 0/NaN or an
    empty team are skipped. A tie stores winner None. If two rows collide on
    a key the last one wins, as the old sequential loop did. Pass `season` to
    scope the lookup: the key has no season component, so an unscoped lookup
    over several seasons can grade one season against another's result.
    """
    from services.nn_feature_engine import _normalize_team

    if all_games is None or all_games.empty:
        return {}
    played = all_games[all_games['result'].notna() & (all_games['result'] != UNDRAFTED_SENTINEL)]
    if season is not None:
        played = played[played['season'] == season]
    if played.empty:
        return {}

    week = pd.to_numeric(played['week'], errors='coerce')
    home = played['home_team'].fillna('').astype(str).map(_normalize_team)
    away = played['away_team'].fillna('').astype(str).map(_normalize_team)
    keep = week.notna() & (week != 0) & (home != '') & (away != '')
    if not keep.any():
        return {}
    week, home, away = week[keep], home[keep], away[keep]
    result = played.loc[keep, 'result']

    keys = 'W' + week.astype(int).map('{:02d}'.format) + '_' + home + '_' + away
    winners = np.where(result > 0, home, np.where(result < 0, away, None))

    def _int_or_none(col: str) -> list:
        if col not in played.columns:
            return [None] * len(keys)
        s = pd.to_numeric(played.loc[keep, col], errors='coerce')
        return [None if pd.isna(v) else int(v) for v in s]

    if 'spread_line' in played.columns:
        spreads = pd.to_numeric(played.loc[keep, 'spread_line'], errors='coerce')
        spread_list = [None if pd.isna(v) else float(v) for v in spreads]
    else:
        spread_list = [None] * len(keys)

    return {
        k: {"winner": w, "home_score": hs, "away_score": as_, "spread_line": sl}
        for k, w, hs, as_, sl in zip(
            keys, winners, _int_or_none('home_score'), _int_or_none('away_score'), spread_list
        )
    }


def get_candidate_seasons() -> List[int]:
    """Seasons that have stored game predictions.

    Local mode: .local_db/game_predictions_<season>.json files. Production (no
    such files): the Firestore `game_predictions` collection's document ids.
    Unparseable names/ids are skipped; a Firestore failure yields [].
    """
    import pathlib

    local_db = pathlib.Path('.local_db')
    pred_files = sorted(local_db.glob('game_predictions_*.json')) if local_db.exists() else []
    seasons: List[int] = []
    if pred_files:
        for pfile in pred_files:
            try:
                seasons.append(int(pfile.stem.split('_')[-1]))
            except ValueError:
                pass
        return seasons
    try:
        from services.db_service import get_db
        db_client = get_db()
        if db_client:
            for doc in db_client.collection('game_predictions').stream():
                try:
                    seasons.append(int(doc.id))
                except ValueError:
                    pass
    except Exception:
        pass
    return seasons
```

`routes/api_routes.py::get_prediction_accuracy`: delete the local `result_lookup` build loop, the `local_db`/`pred_files`/`candidate_seasons` discovery block (replace, do not keep dead code), and keep everything else (the per-season aggregation, the `model_version` attachment, the response shape). Concretely:

```python
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team
        from services.prediction_service import build_result_lookup, get_candidate_seasons
        import pathlib, json, numpy as np

        _, _, all_games, _, _, _, _ = load_data()

        seasons_data = {}
        overall_correct = overall_total = 0
        candidate_seasons = get_candidate_seasons()

        for season in candidate_seasons:
            preds = get_game_predictions(season)
            if not preds:
                continue
            # Scoped per season: the key has no season component (see
            # build_result_lookup).
            result_lookup = build_result_lookup(all_games, season)

            by_week = {}
            s_correct = s_total = 0
            for key, pred in preds.items():
                if not pred.get('locked'):
                    continue
                entry = result_lookup.get(key)
                actual = entry["winner"] if entry else None
                if actual is None:
                    continue
                ...   # remainder of the loop body unchanged
```

`pathlib` is still used further down for the `prediction_features` glob (`local_db = pathlib.Path('.local_db')` is re-declared there or must be kept: keep a `local_db = pathlib.Path('.local_db')` assignment where the later version-map code reads it).

`routes/admin_routes.py::get_predictions_games`: replace the `result_lookup: dict = {}` block through the end of the `for _, row in played.iterrows()` loop with:

```python
        from services.prediction_service import build_result_lookup
        _, _, all_games, _, _, _, _ = load_data()
        result_lookup = build_result_lookup(all_games, season)
```

Keep the downstream code that reads `result_entry.get("winner")`, scores and `spread_line` unchanged (the entry shape is identical). Remove the now-unused local `_normalize_team` import only if nothing else in the function uses it (it is still used for `is_correct`; keep it).

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_prediction_results.py tests/test_api_endpoints.py tests/test_admin_routes.py tests/test_betting_screen_route.py tests/test_game_prediction.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/prediction_service.py routes/api_routes.py routes/admin_routes.py tests/test_prediction_results.py tests/test_api_endpoints.py tests/test_admin_routes.py
git commit -m "perf: vectorize result lookup and share it between prediction routes" -m "Behavior note: get_prediction_accuracy now builds its lookup per season; the previous all-season lookup had no season in its key and could grade a prediction against another season's result."
```

---

### Task 3: Promotion gate observability

**Files:**
- Modify: `services/model_promotion.py`
- Modify: `services/xgb_prediction_service.py`, `services/lr_prediction_service.py`, `services/nn_prediction_service.py` (pass `model_name`)
- Test: `tests/test_model_promotion.py`

**Interfaces:**
- Consumes: existing `PROMOTION_GATES`, `find_same_schema_best(entries, new_feature_columns)`, `assert_promotion_ready(new_metrics, best_metrics, model_name)`.
- Produces: `find_same_schema_best(entries, new_feature_columns, model_name: str = "")` (backward compatible); log lines:
  - `promotion_gate schema_check model=XGB entries=15 same_schema=3 feature_columns=27`
  - `promotion_gate decision=PROMOTED model=XGB candidate_accuracy=0.5800 baseline_accuracy=0.5625 candidate_auc=0.5900 baseline_auc=0.5764 tolerances=accuracy:-0.05,auc:-0.02`
  - `promotion_gate decision=REJECTED model=XGB ... failed=test_auc` (WARNING)
  - `promotion_gate decision=SKIPPED_NO_BASELINE model=XGB candidate_accuracy=... candidate_auc=n/a (XGB: no same-schema baseline in registry; promotion gate skipped)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_model_promotion.py`:

```python
class TestPromotionGateLogging:
    def _lines(self, caplog):
        return [r.getMessage() for r in caplog.records if r.getMessage().startswith("promotion_gate")
                or "promotion_gate" in r.getMessage()]

    def test_promoted_decision_logs_candidate_and_baseline(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.58, "test_auc": 0.59},
                                   {"test_accuracy": 0.5625, "test_auc": 0.5764}, "XGB")
        line = next(l for l in self._lines(caplog) if "decision=" in l)
        assert "decision=PROMOTED" in line and "model=XGB" in line
        assert "candidate_accuracy=0.5800" in line and "baseline_accuracy=0.5625" in line
        assert "candidate_auc=0.5900" in line and "baseline_auc=0.5764" in line

    def test_rejected_decision_is_a_warning_naming_failed_metrics(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"), pytest.raises(ValueError, match="regressed"):
            assert_promotion_ready({"test_accuracy": 0.50, "test_auc": 0.50},
                                   {"test_accuracy": 0.60, "test_auc": 0.60}, "LR")
        rec = next(r for r in caplog.records if "decision=REJECTED" in r.getMessage())
        assert rec.levelname == "WARNING"
        assert "model=LR" in rec.getMessage()
        assert "failed=test_accuracy,test_auc" in rec.getMessage()

    def test_skipped_no_baseline_decision_keeps_legacy_message(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.55}, None, "NN")
        msgs = [r.getMessage() for r in caplog.records]
        line = next(m for m in msgs if "decision=SKIPPED_NO_BASELINE" in m)
        assert "candidate_accuracy=0.5500" in line and "candidate_auc=n/a" in line
        assert "promotion gate skipped" in line          # existing tests assert this substring

    def test_missing_metrics_render_as_na_not_a_crash(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.6}, {"test_accuracy": 0.6}, "NN")
        line = next(r.getMessage() for r in caplog.records if "decision=PROMOTED" in r.getMessage())
        assert "candidate_auc=n/a" in line and "baseline_auc=n/a" in line

    def test_schema_check_line_reports_match_counts(self, caplog):
        from services.model_promotion import find_same_schema_best
        entries = [
            {"feature_columns": ["a", "b"], "metrics": {"test_accuracy": 0.5, "test_auc": 0.5}},
            {"feature_columns": ["a", "b"], "metrics": {"test_accuracy": 0.6, "test_auc": 0.6}},
            {"feature_columns": ["a", "c"], "metrics": {"test_accuracy": 0.7, "test_auc": 0.7}},
        ]
        with caplog.at_level("INFO"):
            best = find_same_schema_best(entries, ["a", "b"], model_name="XGB")
        assert best["test_auc"] == 0.6
        line = next(r.getMessage() for r in caplog.records if "schema_check" in r.getMessage())
        assert "model=XGB" in line and "entries=3" in line and "same_schema=2" in line and "feature_columns=2" in line

    def test_schema_check_with_no_match_says_zero(self, caplog):
        from services.model_promotion import find_same_schema_best
        with caplog.at_level("INFO"):
            assert find_same_schema_best([], ["a"], model_name="NN") is None
        line = next(r.getMessage() for r in caplog.records if "schema_check" in r.getMessage())
        assert "same_schema=0" in line and "entries=0" in line

    def test_find_same_schema_best_still_works_without_model_name(self):
        from services.model_promotion import find_same_schema_best
        assert find_same_schema_best([], ["a"]) is None

    def test_exactly_one_decision_line_per_call(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.6, "test_auc": 0.6},
                                   {"test_accuracy": 0.6, "test_auc": 0.6}, "XGB")
        assert sum("decision=" in r.getMessage() for r in caplog.records) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_model_promotion.py -v`
Expected: the new tests FAIL (`model_name` keyword unexpected; no `decision=` lines); existing tests PASS.

- [ ] **Step 3: Implement**

In `services/model_promotion.py` add helpers and update the two functions (keep every existing line of logic; only add logging and the optional argument):

```python
def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def _decision_fields(model_name: str, new_metrics: dict, best_metrics: Optional[dict]) -> str:
    best = best_metrics or {}
    return (
        f"model={model_name} "
        f"candidate_accuracy={_fmt(new_metrics.get('test_accuracy'))} "
        f"baseline_accuracy={_fmt(best.get('test_accuracy'))} "
        f"candidate_auc={_fmt(new_metrics.get('test_auc'))} "
        f"baseline_auc={_fmt(best.get('test_auc'))}"
    )
```

`find_same_schema_best(entries, new_feature_columns, model_name: str = "")`: after computing `candidates`, log

```python
    logger.info(
        "promotion_gate schema_check model=%s entries=%d same_schema=%d feature_columns=%d",
        model_name or "?", len(entries), len(candidates), len(new_feature_columns),
    )
```

before the `if not candidates: return None`.

`assert_promotion_ready`: 
- in the `best_metrics is None` branch, replace the log call with one that keeps the legacy substring:

```python
        logger.info(
            "promotion_gate decision=SKIPPED_NO_BASELINE %s (%s: no same-schema baseline in registry; promotion gate skipped)",
            _decision_fields(model_name, new_metrics, None), model_name,
        )
```

- when `failures` is non-empty, before raising:

```python
        logger.warning(
            "promotion_gate decision=REJECTED %s failed=%s",
            _decision_fields(model_name, new_metrics, best_metrics), ",".join(sorted(failures)),
        )
```

- after the `if failures:` block (the passing path):

```python
    logger.info(
        "promotion_gate decision=PROMOTED %s tolerances=%s",
        _decision_fields(model_name, new_metrics, best_metrics),
        ",".join(f"{m.replace('test_', '')}:{t}" for m, t in PROMOTION_GATES.items()),
    )
```

In each of the three training services change the call to `find_same_schema_best(entries, FEATURE_COLUMNS, model_name="XGB")` / `"LR"` / `"NN"` matching the name string each service already passes to `assert_promotion_ready`.

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_model_promotion.py tests/test_promotion_gate_xgb.py tests/test_promotion_gate_lr.py tests/test_promotion_gate_nn.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/model_promotion.py services/xgb_prediction_service.py services/lr_prediction_service.py services/nn_prediction_service.py tests/test_model_promotion.py
git commit -m "feat: structured logging for model promotion gate decisions"
```

---

### Task 4: Full verification

- [ ] **Step 1:** Run `pytest tests/ -n auto -q`. Expected: only the known baseline failures.
- [ ] **Step 2:** Confirm no `.iterrows()` remains in the two routes: `grep -n "iterrows" routes/api_routes.py routes/admin_routes.py` must show no hit inside `get_prediction_accuracy` or `get_predictions_games`.
- [ ] **Step 3:** Local smoke: `USE_LOCAL_DATA=True JWT_SECRET=<32+ chars> uvicorn main:app --port 8125`, load `/wins-pool/<year>` and `/api/predictions/accuracy` (mint an admin token per the repo notes) and confirm 200s; record timings if `DEBUG_PAGE_LOAD=True` output is available.
- [ ] **Step 4:** Update `docs/superpowers/specs/2026-09-18-feature-version-stamp-followup-cleanup.md`: add a dated note under "Observability / durability gaps" that gate decisions are now logged (`promotion_gate ...`), leaving all existing text intact. Commit with `git add -f <that exact path>`.
