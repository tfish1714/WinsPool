# Weekly Betting Edge Alert Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a weekly scheduled job that runs the existing betting screener/pattern-scanner logic against the upcoming week's games and emails a summary (validated angle matches + raw Vegas-edge outliers) to the project owner when something's worth looking at.

**Architecture:** A new pure service module (`services/betting_edge_alert_service.py`) orchestrates the two existing read-only services (`pattern_scanner_service.scan_angles`, `betting_screener_service.screen_games`) into a single `week_summary` dict. A new `send_betting_edge_email()` in `email_service.py` formats and sends it via the existing Resend `_send()` path. A new script (`scripts/betting_edge_alert_weekly.py`) is the Cloud Run Job entrypoint, following the established `USE_LOCAL_DATA=False` + `initialize_firebase()` + `_run_with_alerting()` pattern already used by `sync_live_scores.py`/`cache_builder.py`.

**Tech Stack:** Python, pandas, existing `services/betting_screener_service.py` + `services/pattern_scanner_service.py` (already implemented, read-only), Resend (via `services/email_service.py`), pytest + `unittest.mock`.

**Spec:** `docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md`

## Global Constraints

- Personal alert only (project owner, one recipient) — never player-facing, never changes what players see.
- Two tiers, both surfaced: (1) validated-angle matches from `pattern_scanner_service.scan_angles`'s walk-forward-validated combos, ranked first; (2) raw `|edge_vs_vegas|` outliers, threshold default **3.0** points, tunable via env var — labeled clearly as unvalidated in the email.
- **No spam**: if both tiers are empty for the week, skip sending entirely (log instead).
- New recipient env var: `BETTING_ALERT_EMAIL` — deliberately separate from `ALERT_EMAIL`.
- Subject line: `[WinsPool] Week {N} betting edges` — visually distinct from `[WinsPool Alert]` job-failure emails.
- Script must never touch the NN+XGB+LR ensemble or write anything — read-only, same guarantee as the two services it composes.
- `os.environ["USE_LOCAL_DATA"] = "False"` must be set before importing anything from `services.db_service` (CLAUDE.md gotcha), and the script must call `_run_with_alerting()`-style wrapping so a crash still fires the existing `[WinsPool Alert]` failure path via `send_alert_email()`.
- New Cloud Run Job `winspool-betting-alert` uses the existing `Dockerfile.sync` image (no ML deps needed) — not `Dockerfile.predict`.
- New job needs its own `MAX_RETRIES` env var (matching `--max-retries`, default 3) per the CLAUDE.md alerting gotcha, or `send_alert_email()` fails open.
- Out of scope: tuning `min_sample`/`top_n`/edge-threshold exact values (ship with existing defaults), any UI for alert history, any change to what players see.

---

### Task 1: Move `_load_predictions_by_season` into `betting_screener_service.py`

The route-level helper that loads `{season: {game_key: pred_dict}}` for the backtestable season range needs to be callable from a script (`scripts/betting_edge_alert_weekly.py`), which cannot import from `routes/`. Move it into `services/betting_screener_service.py` as a public function; `routes/prediction_routes.py` becomes a thin caller. Pure refactor — no behavior change.

**Files:**
- Modify: `services/betting_screener_service.py` (add function, after `find_next_upcoming_week`, before `screen_games`)
- Modify: `routes/prediction_routes.py:218-239` (delete the function, update 2 call sites at lines ~297, ~346)
- Test: `tests/test_betting_screener_service.py`

**Interfaces:**
- Produces: `services.betting_screener_service.load_predictions_by_season(all_games: pd.DataFrame) -> tuple[dict, int, int]` — returns `(predictions_by_season, min_season, max_season)`, identical contract to the old `_load_predictions_by_season`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_betting_screener_service.py` (new class, alongside `TestScreenGames`):

```python
class TestLoadPredictionsBySeason:
    def test_skips_seasons_before_backtest_min_season(self):
        from services.betting_screener_service import BACKTEST_MIN_SEASON
        games_df = pd.DataFrame([
            {"season": BACKTEST_MIN_SEASON - 1, "week": 1, "home_team": "KC", "away_team": "SF"},
            {"season": BACKTEST_MIN_SEASON, "week": 1, "home_team": "KC", "away_team": "SF"},
        ])
        with patch("services.betting_screener_service.get_game_predictions") as mock_get:
            mock_get.side_effect = lambda yr: {"W01_KC_SF": {}} if yr == BACKTEST_MIN_SEASON else {}
            preds, min_season, max_season = load_predictions_by_season(games_df)
        assert min_season == BACKTEST_MIN_SEASON
        assert max_season == BACKTEST_MIN_SEASON
        assert set(preds.keys()) == {BACKTEST_MIN_SEASON}

    def test_omits_seasons_with_no_predictions(self):
        games_df = pd.DataFrame([
            {"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF"},
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "SF"},
        ])
        with patch("services.betting_screener_service.get_game_predictions") as mock_get:
            mock_get.side_effect = lambda yr: {"W01_KC_SF": {}} if yr == 2026 else {}
            preds, min_season, max_season = load_predictions_by_season(games_df)
        assert set(preds.keys()) == {2026}
        assert min_season == 2025
        assert max_season == 2026
```

Add `from unittest.mock import patch` and `load_predictions_by_season` to the existing top-of-file imports in `tests/test_betting_screener_service.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_betting_screener_service.py::TestLoadPredictionsBySeason -v`
Expected: FAIL — `ImportError: cannot import name 'load_predictions_by_season'`

- [ ] **Step 3: Add the function to `services/betting_screener_service.py`**

Insert after `find_next_upcoming_week` (currently ends around line 158), before `def screen_games(`:

```python
def load_predictions_by_season(all_games) -> tuple[dict, int, int]:
    """{season: {game_key: pred_dict}} for every season from BACKTEST_MIN_SEASON
    (or the games data's own floor, if later) through the latest season present
    in `all_games`. Shared by the betting screener/pattern-scanner admin routes
    and the weekly betting-edge-alert job.

    BACKTEST_MIN_SEASON=2006 is when Elo data starts, but games_df (used to
    grade ATS/SU outcomes) only goes back to 2013 -- looping earlier seasons
    would fetch prediction docs that can never be graded (no matching game
    result), wasting Firestore reads and silently misrepresenting how much
    history actually backs the reported n.
    """
    from services.cache_service import get_game_predictions

    max_season = int(all_games["season"].max())
    min_season = max(BACKTEST_MIN_SEASON, int(all_games["season"].min()))
    predictions_by_season = {}
    for yr in range(min_season, max_season + 1):
        preds = get_game_predictions(yr)
        if preds:
            predictions_by_season[yr] = preds
    return predictions_by_season, min_season, max_season
```

Note: the test patches `services.betting_screener_service.get_game_predictions`, which requires the import to be resolvable at that path even though it's a local import inside the function — `unittest.mock.patch` patches the name in the module's namespace at call time, and a local `from services.cache_service import get_game_predictions` inside the function looks it up fresh on every call, so this only works if the patch target already exists as a module-level name. **Change the import to module-level** instead (top of `betting_screener_service.py`, alongside the existing `from services.nn_feature_engine import _normalize_team`):

```python
from services.cache_service import get_game_predictions
```

Then reference it directly inside `load_predictions_by_season` (drop the local import).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_betting_screener_service.py -v`
Expected: PASS (all tests in the file, not just the new class — confirms the module-level import didn't break anything via circular-import at module load time)

- [ ] **Step 5: Update `routes/prediction_routes.py` to use the moved function**

Delete lines 218-239 (`def _load_predictions_by_season(all_games): ...` in full). Add to the existing import block near the top of the two route handlers (or a single import near the top of the file if one already groups `services.betting_screener_service` imports):

```python
from services.betting_screener_service import load_predictions_by_season
```

Replace both call sites:
- Line ~297: `predictions_by_season, min_season, max_season = _load_predictions_by_season(all_games)` → `load_predictions_by_season(all_games)`
- Line ~346: same replacement

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `pytest tests/ -q`
Expected: all tests pass (same count as before this task, since this is a pure rename/move)

- [ ] **Step 7: Commit**

```bash
git add services/betting_screener_service.py routes/prediction_routes.py tests/test_betting_screener_service.py
git commit -m "refactor: move predictions-by-season loader into betting_screener_service

Makes it importable from a script (scripts/ cannot import routes/), needed
for the upcoming weekly betting-edge-alert job."
```

---

### Task 2: `services/betting_edge_alert_service.py` — orchestration logic

Pure logic, no Firestore/network, matching the style of `betting_screener_service.py`/`pattern_scanner_service.py`. Composes `scan_angles` + `screen_games` into validated-angle matches, and reads `edge_vs_vegas` directly from prediction data for raw outliers.

**Files:**
- Create: `services/betting_edge_alert_service.py`
- Test: `tests/test_betting_edge_alert_service.py`

**Interfaces:**
- Consumes: `services.pattern_scanner_service.scan_angles(predictions_by_season, games_df, **kwargs) -> dict` (keys: `ats_leaderboard`, `su_leaderboard`, each a list of `{"conditions": [...], "train_rate": float, "train_n": int, "test_rate": float|None, "test_n": int, "held_up": bool|None}`); `services.betting_screener_service.screen_games(predictions_by_season, games_df, *, target_season, target_week, side="any", favorite_or_dog="any", filters=None) -> dict` (key `candidates`: list of `{"season", "week", "home_team", "away_team", "matched_sides", "already_played", ...}`).
- Produces: `find_raw_edge_outliers(predictions_by_season, *, target_season, target_week, edge_threshold=3.0) -> list[dict]`; `find_validated_angle_matches(predictions_by_season, games_df, *, target_season, target_week, scan_kwargs=None, max_matches=10) -> list[dict]`; `build_week_summary(predictions_by_season, games_df, *, target_season, target_week, edge_threshold=3.0, scan_kwargs=None) -> dict` with keys `season`, `week`, `validated_angle_matches`, `raw_edge_outliers` — this exact dict is what `email_service.send_betting_edge_email()` (Task 3) consumes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_betting_edge_alert_service.py`:

```python
"""Tests for services/betting_edge_alert_service.py -- orchestrates the
existing betting screener/pattern scanner into a weekly alert-email summary.
Pure logic, no Firestore/network; scan_angles/screen_games are mocked since
their own behavior is already covered by test_pattern_scanner_service.py /
test_betting_screener_service.py."""
from unittest.mock import patch

import pandas as pd
import pytest

from services.betting_edge_alert_service import (
    find_raw_edge_outliers,
    find_validated_angle_matches,
    build_week_summary,
)


class TestFindRawEdgeOutliers:
    def _predictions(self):
        return {
            2026: {
                "W03_KC_SF": {"pred_ats_pick": "KC", "model_spread": 4.0,
                               "explanation": {"edge_vs_vegas": 5.0, "vegas_line": -1.0}},
                "W03_BUF_MIA": {"pred_ats_pick": "MIA", "model_spread": -1.0,
                                 "explanation": {"edge_vs_vegas": 1.0, "vegas_line": -2.0}},
                "W02_DAL_PHI": {"pred_ats_pick": "DAL", "model_spread": 6.0,
                                 "explanation": {"edge_vs_vegas": 6.0, "vegas_line": 0.0}},
            },
        }

    def test_filters_to_target_week_and_threshold(self):
        result = find_raw_edge_outliers(
            self._predictions(), target_season=2026, target_week=3, edge_threshold=3.0,
        )
        # BUF_MIA's edge (1.0) is below threshold; DAL_PHI is week 2, not week 3
        assert len(result) == 1
        assert result[0]["home_team"] == "KC"
        assert result[0]["away_team"] == "SF"
        assert result[0]["edge_vs_vegas"] == 5.0

    def test_sorted_by_absolute_edge_descending(self):
        preds = {
            2026: {
                "W01_AAA_BBB": {"pred_ats_pick": "AAA", "model_spread": 3.0,
                                 "explanation": {"edge_vs_vegas": 3.5, "vegas_line": 0.0}},
                "W01_CCC_DDD": {"pred_ats_pick": "DDD", "model_spread": -8.0,
                                 "explanation": {"edge_vs_vegas": -8.0, "vegas_line": 0.0}},
            },
        }
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert [r["home_team"] for r in result] == ["CCC", "AAA"]

    def test_missing_edge_is_excluded(self):
        preds = {2026: {"W01_AAA_BBB": {"pred_ats_pick": None, "model_spread": None,
                                          "explanation": {"edge_vs_vegas": None, "vegas_line": 0.0}}}}
        result = find_raw_edge_outliers(preds, target_season=2026, target_week=1, edge_threshold=3.0)
        assert result == []

    def test_no_predictions_for_season_returns_empty(self):
        result = find_raw_edge_outliers({}, target_season=2026, target_week=1, edge_threshold=3.0)
        assert result == []


class TestFindValidatedAngleMatches:
    def _scan_result(self, held_up=True):
        return {
            "ats_leaderboard": [
                {"conditions": [{"feature": "elo_diff", "label": "Elo Diff", "min": 50.0}],
                 "train_rate": 0.62, "train_n": 120, "test_rate": 0.58, "test_n": 30,
                 "held_up": held_up},
            ],
            "su_leaderboard": [],
        }

    def _screen_result(self, with_candidate=True):
        candidates = [{
            "season": 2026, "week": 3, "home_team": "KC", "away_team": "SF",
            "matched_sides": ["home"], "already_played": False,
        }] if with_candidate else []
        return {"backtest": {}, "candidates": candidates, "filterable_features": {}}

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_matched_angle_with_upcoming_game_is_included(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        mock_screen.return_value = self._screen_result(with_candidate=True)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert len(result) == 1
        assert result[0]["metric"] == "ats"
        assert result[0]["games"][0]["home_team"] == "KC"

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_angle_that_did_not_hold_up_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=False)
        mock_screen.return_value = self._screen_result(with_candidate=True)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []
        mock_screen.assert_not_called()

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_held_up_angle_with_no_matching_game_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        mock_screen.return_value = self._screen_result(with_candidate=False)

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []

    @patch("services.betting_edge_alert_service.screen_games")
    @patch("services.betting_edge_alert_service.scan_angles")
    def test_already_played_candidate_is_excluded(self, mock_scan, mock_screen):
        mock_scan.return_value = self._scan_result(held_up=True)
        screen_result = self._screen_result(with_candidate=True)
        screen_result["candidates"][0]["already_played"] = True
        mock_screen.return_value = screen_result

        result = find_validated_angle_matches(
            {}, pd.DataFrame(), target_season=2026, target_week=3,
        )
        assert result == []


class TestBuildWeekSummary:
    @patch("services.betting_edge_alert_service.find_validated_angle_matches")
    @patch("services.betting_edge_alert_service.find_raw_edge_outliers")
    def test_combines_both_tiers(self, mock_outliers, mock_matches):
        mock_outliers.return_value = [{"home_team": "KC"}]
        mock_matches.return_value = [{"metric": "ats"}]

        result = build_week_summary({}, pd.DataFrame(), target_season=2026, target_week=3)

        assert result["season"] == 2026
        assert result["week"] == 3
        assert result["raw_edge_outliers"] == [{"home_team": "KC"}]
        assert result["validated_angle_matches"] == [{"metric": "ats"}]

    @patch("services.betting_edge_alert_service.find_validated_angle_matches")
    @patch("services.betting_edge_alert_service.find_raw_edge_outliers")
    def test_both_empty_is_a_valid_quiet_week(self, mock_outliers, mock_matches):
        mock_outliers.return_value = []
        mock_matches.return_value = []

        result = build_week_summary({}, pd.DataFrame(), target_season=2026, target_week=3)

        assert result["raw_edge_outliers"] == []
        assert result["validated_angle_matches"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_betting_edge_alert_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.betting_edge_alert_service'`

- [ ] **Step 3: Write the implementation**

Create `services/betting_edge_alert_service.py`:

```python
"""services/betting_edge_alert_service.py -- orchestrates the existing
betting screener (services.betting_screener_service.screen_games) and
pattern scanner (services.pattern_scanner_service.scan_angles) into a single
weekly summary for the winspool-betting-alert Cloud Run Job. Pure logic, no
Firestore/network -- callers pass in already-loaded predictions_by_season /
games_df, same as both services it composes. Never touches the NN+XGB+LR
ensemble.

Two tiers, both computed independently and never gating each other:
1. Validated-angle matches (find_validated_angle_matches): reuses
   scan_angles's walk-forward-validated leaderboard entries (held_up=True
   only -- see pattern_scanner_service's module docstring for what that
   means) and checks which currently match the upcoming week's games via
   screen_games, using each entry's own `conditions` as screen_games'
   `filters` -- the same {"feature", "min"|"max"} shape both already share.
2. Raw edge outliers (find_raw_edge_outliers): any upcoming game where
   |edge_vs_vegas| exceeds a threshold, read directly from the same
   explanation dict screen_games/scan_angles use -- not backtest-validated
   by itself, so kept in a clearly separate list for the email to label.
"""
from __future__ import annotations

from typing import Optional

from services.betting_screener_service import screen_games
from services.pattern_scanner_service import scan_angles

DEFAULT_EDGE_THRESHOLD = 3.0
DEFAULT_MAX_ANGLE_MATCHES = 10


def find_raw_edge_outliers(
    predictions_by_season: dict, *,
    target_season: int, target_week: int,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> list[dict]:
    """Upcoming games where |edge_vs_vegas| >= edge_threshold, sorted by
    magnitude descending. Reads edge_vs_vegas/vegas_line straight from each
    game's `explanation` dict (the source both betting_screener_service and
    pattern_scanner_service already read), and pred_ats_pick from the
    top-level pred_dict (which side the model favors ATS)."""
    preds = predictions_by_season.get(target_season, {})
    outliers = []
    for game_key, pred in preds.items():
        parts = game_key.split("_")
        if len(parts) != 3:
            continue
        wk_str, ht, at = parts
        try:
            wk = int(wk_str.lstrip("W"))
        except ValueError:
            continue
        if wk != target_week:
            continue

        ex = pred.get("explanation") or {}
        edge = ex.get("edge_vs_vegas")
        if edge is None or abs(edge) < edge_threshold:
            continue

        outliers.append({
            "season": target_season, "week": wk,
            "home_team": ht, "away_team": at,
            "edge_vs_vegas": edge,
            "model_spread": ex.get("model_spread"),
            "vegas_line": ex.get("vegas_line"),
            "ats_pick": pred.get("pred_ats_pick"),
        })

    outliers.sort(key=lambda o: abs(o["edge_vs_vegas"]), reverse=True)
    return outliers


def find_validated_angle_matches(
    predictions_by_season: dict, games_df, *,
    target_season: int, target_week: int,
    scan_kwargs: Optional[dict] = None,
    max_matches: int = DEFAULT_MAX_ANGLE_MATCHES,
) -> list[dict]:
    """Walk-forward-validated (held_up=True) leaderboard entries from
    scan_angles that currently match at least one of the upcoming week's
    games. Checks both the ATS and SU leaderboards. Each entry's own
    `conditions` list is reused verbatim as screen_games' `filters` -- both
    already share the {"feature", "min"|"max"} shape (see
    pattern_scanner_service._condition / betting_screener_service.matches_filters).
    """
    scan_kwargs = scan_kwargs or {}
    scan = scan_angles(predictions_by_season, games_df, **scan_kwargs)

    matches = []
    for metric, leaderboard in (("ats", scan["ats_leaderboard"]), ("su", scan["su_leaderboard"])):
        for entry in leaderboard:
            if not entry.get("held_up"):
                continue

            result = screen_games(
                predictions_by_season, games_df,
                target_season=target_season, target_week=target_week,
                filters=entry["conditions"],
            )
            upcoming = [c for c in result["candidates"] if not c["already_played"]]
            if not upcoming:
                continue

            matches.append({
                "metric": metric,
                "conditions": entry["conditions"],
                "train_rate": entry["train_rate"], "train_n": entry["train_n"],
                "test_rate": entry["test_rate"], "test_n": entry["test_n"],
                "games": upcoming,
            })
            if len(matches) >= max_matches:
                return matches
    return matches


def build_week_summary(
    predictions_by_season: dict, games_df, *,
    target_season: int, target_week: int,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
    scan_kwargs: Optional[dict] = None,
) -> dict:
    """Both tiers for one target week -- what scripts/betting_edge_alert_weekly.py
    hands to email_service.send_betting_edge_email(). Both lists can be empty
    (a normal quiet week); the caller decides whether that means skip sending."""
    return {
        "season": target_season,
        "week": target_week,
        "validated_angle_matches": find_validated_angle_matches(
            predictions_by_season, games_df,
            target_season=target_season, target_week=target_week,
            scan_kwargs=scan_kwargs,
        ),
        "raw_edge_outliers": find_raw_edge_outliers(
            predictions_by_season,
            target_season=target_season, target_week=target_week,
            edge_threshold=edge_threshold,
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_betting_edge_alert_service.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add services/betting_edge_alert_service.py tests/test_betting_edge_alert_service.py
git commit -m "feat: add betting_edge_alert_service orchestrating scan_angles + screen_games

Composes the existing walk-forward-validated pattern scanner and betting
screener into a single weekly summary (validated angle matches + raw
edge_vs_vegas outliers) for the upcoming betting-edge-alert job."
```

---

### Task 3: `send_betting_edge_email` in `services/email_service.py`

**Files:**
- Modify: `services/email_service.py` (add function, after `send_on_the_clock_email`, before `send_alert_email`)
- Test: `tests/test_email_service.py`

**Interfaces:**
- Consumes: the `week_summary` dict shape produced by `build_week_summary` (Task 2) — `{"season": int, "week": int, "validated_angle_matches": [...], "raw_edge_outliers": [...]}`.
- Produces: `send_betting_edge_email(to_email: str, week_summary: dict) -> bool`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_email_service.py`:

```python
from services.email_service import send_betting_edge_email


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_betting_edge_email_includes_both_tiers(mock_getenv, mock_send):
    mock_send.return_value = {"id": "bet123"}
    week_summary = {
        "season": 2026, "week": 3,
        "validated_angle_matches": [{
            "metric": "ats",
            "conditions": [{"feature": "elo_diff", "label": "Elo Diff", "min": 50.0}],
            "train_rate": 0.62, "train_n": 120, "test_rate": 0.58, "test_n": 30,
            "games": [{"home_team": "KC", "away_team": "SF", "matched_sides": ["home"]}],
        }],
        "raw_edge_outliers": [{
            "home_team": "BUF", "away_team": "MIA", "edge_vs_vegas": 4.5,
            "model_spread": 3.0, "vegas_line": -1.5, "ats_pick": "BUF",
        }],
    }

    result = send_betting_edge_email("owner@x.com", week_summary)

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["owner@x.com"]
    assert call_params["subject"] == "[WinsPool] Week 3 betting edges"
    assert "Elo Diff" in call_params["html"]
    assert "KC" in call_params["html"] and "SF" in call_params["html"]
    assert "BUF" in call_params["html"] and "MIA" in call_params["html"]
    assert "4.5" in call_params["html"]


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv", return_value="re_test_key")
def test_send_betting_edge_email_handles_empty_angle_matches(mock_getenv, mock_send):
    """Caller (the script) decides whether to send at all when both tiers are
    empty -- this function itself must not crash if given an empty tier."""
    mock_send.return_value = {"id": "bet124"}
    week_summary = {
        "season": 2026, "week": 3,
        "validated_angle_matches": [],
        "raw_edge_outliers": [{
            "home_team": "BUF", "away_team": "MIA", "edge_vs_vegas": -4.5,
            "model_spread": -3.0, "vegas_line": 1.5, "ats_pick": "MIA",
        }],
    }
    result = send_betting_edge_email("owner@x.com", week_summary)
    assert result is True
    call_params = mock_send.call_args[0][0]
    assert "Validated angle matches" not in call_params["html"]
    assert "Raw edge outliers" in call_params["html"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_service.py -k betting_edge -v`
Expected: FAIL — `ImportError: cannot import name 'send_betting_edge_email'`

- [ ] **Step 3: Implement**

Add to `services/email_service.py`, after `send_on_the_clock_email` (before `send_alert_email`):

```python
def send_betting_edge_email(to_email: str, week_summary: dict) -> bool:
    """Send the weekly betting-edge summary (validated angle matches + raw
    Vegas-edge outliers, from services.betting_edge_alert_service.build_week_summary)
    to a single recipient. Caller (scripts/betting_edge_alert_weekly.py)
    already decided both tiers aren't empty -- this never itself decides
    whether to send, so it renders whatever it's given, including an empty
    tier (defensive -- lets this function be tested/reused independently of
    that no-spam decision).
    """
    season = week_summary["season"]
    week = week_summary["week"]

    sections = []

    angle_matches = week_summary.get("validated_angle_matches") or []
    if angle_matches:
        rows = []
        for m in angle_matches:
            cond_text = " AND ".join(
                f"{c['label']} {'>=' if 'min' in c else '<='} {c.get('min', c.get('max'))}"
                for c in m["conditions"]
            )
            games_text = "; ".join(
                f"{g['away_team']} @ {g['home_team']} ({'/'.join(g['matched_sides'])})"
                for g in m["games"]
            )
            test_rate_text = (
                f"{m['test_rate']:.1%} (n={m['test_n']})" if m["test_rate"] is not None else "n/a"
            )
            rows.append(
                f"<li><strong>{html.escape(cond_text)}</strong> "
                f"({m['metric'].upper()}, train {m['train_rate']:.1%} n={m['train_n']}, "
                f"held-out {test_rate_text})<br>{html.escape(games_text)}</li>"
            )
        sections.append(f"<h3>Validated angle matches</h3><ul>{''.join(rows)}</ul>")

    outliers = week_summary.get("raw_edge_outliers") or []
    if outliers:
        rows = "".join(
            f"<li>{html.escape(str(o['away_team']))} @ {html.escape(str(o['home_team']))}: "
            f"model favors {html.escape(str(o['ats_pick']))} by {o['edge_vs_vegas']:+.1f} vs Vegas "
            f"(model {o['model_spread']}, Vegas {o['vegas_line']})</li>"
            for o in outliers
        )
        sections.append(
            f"<h3>Raw edge outliers (unvalidated -- not backtested)</h3><ul>{rows}</ul>"
        )

    html_body = f"<p>Week {week}, {season} season:</p>" + "".join(sections)
    return _send(to_email, f"[WinsPool] Week {week} betting edges", html_body)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add services/email_service.py tests/test_email_service.py
git commit -m "feat: add send_betting_edge_email for the weekly betting-alert job"
```

---

### Task 4: `scripts/betting_edge_alert_weekly.py` — the Cloud Run Job entrypoint

**Files:**
- Create: `scripts/betting_edge_alert_weekly.py`
- Test: `tests/test_betting_edge_alert_weekly.py`

**Interfaces:**
- Consumes: `services.data_service.load_data() -> tuple` (7-tuple; only `all_games` at index 2 is used); `services.betting_screener_service.find_next_upcoming_week(games_df, season) -> int | None`; `services.betting_screener_service.load_predictions_by_season(all_games) -> tuple[dict, int, int]` (Task 1); `services.betting_edge_alert_service.build_week_summary(...) -> dict` (Task 2); `services.email_service.send_betting_edge_email(to_email, week_summary) -> bool` (Task 3); `services.email_service.send_alert_email(subject, message) -> bool`.
- Produces: `main()` (no return value — prints status); `_run_with_alerting()` (the `if __name__` entrypoint).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_betting_edge_alert_weekly.py`:

```python
"""Tests for scripts/betting_edge_alert_weekly.py -- the winspool-betting-alert
Cloud Run Job entrypoint. Mirrors the mocking style of test_sync_live_scores.py:
mock every service call, verify the orchestration (no real Firestore/network)."""
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from scripts.betting_edge_alert_weekly import main


def _games_df():
    return pd.DataFrame([
        {"season": 2026, "week": 3, "home_team": "KC", "away_team": "SF",
         "home_score": None, "away_score": None, "result": None},
    ])


class TestMain:
    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    @patch("scripts.betting_edge_alert_weekly.os.environ.get")
    def test_sends_when_summary_has_edges(
        self, mock_env_get, mock_init_firebase, mock_load_data, mock_find_week,
        mock_load_preds, mock_build_summary, mock_send,
    ):
        mock_env_get.side_effect = lambda key, default=None: {
            "BETTING_ALERT_EMAIL": "owner@x.com",
            "BETTING_EDGE_THRESHOLD": "3.0",
        }.get(key, default)
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [{"metric": "ats"}],
            "raw_edge_outliers": [],
        }
        mock_send.return_value = True

        main()

        mock_init_firebase.assert_called_once()
        mock_send.assert_called_once_with("owner@x.com", mock_build_summary.return_value)

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    def test_skips_send_when_both_tiers_empty(
        self, mock_init_firebase, mock_load_data, mock_find_week, mock_load_preds,
        mock_build_summary, mock_send,
    ):
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [], "raw_edge_outliers": [],
        }

        main()

        mock_send.assert_not_called()

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    def test_no_upcoming_week_skips_entirely(self, mock_init_firebase, mock_load_data, mock_send):
        mock_load_data.return_value = (None, None, pd.DataFrame(), None, None, None, None)
        main()
        mock_send.assert_not_called()

    @patch("scripts.betting_edge_alert_weekly.send_betting_edge_email")
    @patch("scripts.betting_edge_alert_weekly.build_week_summary")
    @patch("scripts.betting_edge_alert_weekly.load_predictions_by_season")
    @patch("scripts.betting_edge_alert_weekly.find_next_upcoming_week")
    @patch("scripts.betting_edge_alert_weekly.load_data")
    @patch("scripts.betting_edge_alert_weekly.initialize_firebase")
    @patch("scripts.betting_edge_alert_weekly.os.environ.get")
    def test_missing_recipient_env_var_skips_send(
        self, mock_env_get, mock_init_firebase, mock_load_data, mock_find_week,
        mock_load_preds, mock_build_summary, mock_send,
    ):
        mock_env_get.side_effect = lambda key, default=None: {
            "BETTING_EDGE_THRESHOLD": "3.0",
        }.get(key, default)  # BETTING_ALERT_EMAIL deliberately absent
        mock_load_data.return_value = (None, None, _games_df(), None, None, None, None)
        mock_find_week.return_value = 3
        mock_load_preds.return_value = ({2026: {}}, 2020, 2026)
        mock_build_summary.return_value = {
            "season": 2026, "week": 3,
            "validated_angle_matches": [{"metric": "ats"}], "raw_edge_outliers": [],
        }

        main()

        mock_send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_betting_edge_alert_weekly.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.betting_edge_alert_weekly'`

- [ ] **Step 3: Write the implementation**

Create `scripts/betting_edge_alert_weekly.py`. Note `initialize_firebase()` is
imported but called lazily *inside* `main()`, not at module import time --
matching `sync_live_scores.py`'s pattern (not `cache_builder.py`'s, which
initializes at import time). This matters for testability:
`initialize_firebase()` calls `sys.exit(1)` when no Firebase credentials are
configured (the normal state of a test/CI environment), so calling it at
import time would make this module unimportable under pytest without a real
credentials file or `FIREBASE_CREDENTIALS` env var -- exactly what
`tests/test_sync_live_scores.py` already avoids by importing that script's
functions directly without issue.

```python
"""scripts/betting_edge_alert_weekly.py -- winspool-betting-alert Cloud Run
Job entrypoint. Runs weekly (Tuesdays, shortly after winspool-schedule-kickoffs'
10:00 UTC run -- see docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md),
in-season only (gated by the Cloud Scheduler trigger's own cron window, not
in-script -- matching every other scheduled job in this repo).

Personal alert only (project owner, one recipient via BETTING_ALERT_EMAIL) --
never player-facing. Read-only: composes the existing betting screener
(services.betting_screener_service) and pattern scanner
(services.pattern_scanner_service) via services.betting_edge_alert_service,
never touches the NN+XGB+LR ensemble, never writes anything.

No spam: if both tiers (validated angle matches, raw edge_vs_vegas outliers)
are empty for the week, this skips sending entirely and just logs -- a quiet
week is normal, not a failure.
"""
import os
import sys
import pathlib

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from scripts.daily_nfl_sync import initialize_firebase
from services.data_service import load_data
from services.betting_screener_service import find_next_upcoming_week, load_predictions_by_season
from services.betting_edge_alert_service import build_week_summary
from services.email_service import send_betting_edge_email, send_alert_email


def main():
    initialize_firebase()

    _, _, all_games, _, _, _, _ = load_data()
    if all_games.empty:
        print("[betting_edge_alert] No schedule data available; nothing to do.")
        return

    target_season = int(all_games["season"].max())
    target_week = find_next_upcoming_week(all_games, target_season)
    if target_week is None:
        print(f"[betting_edge_alert] No upcoming week found for season {target_season}; nothing to do.")
        return

    predictions_by_season, _, _ = load_predictions_by_season(all_games)

    edge_threshold = float(os.environ.get("BETTING_EDGE_THRESHOLD", "3.0"))
    summary = build_week_summary(
        predictions_by_season, all_games,
        target_season=target_season, target_week=target_week,
        edge_threshold=edge_threshold,
    )

    if not summary["validated_angle_matches"] and not summary["raw_edge_outliers"]:
        print(f"[betting_edge_alert] No edges found for {target_season} week {target_week}. Skipping email.")
        return

    to_email = os.environ.get("BETTING_ALERT_EMAIL")
    if not to_email:
        print("[betting_edge_alert] BETTING_ALERT_EMAIL not set; skipping email.")
        return

    sent = send_betting_edge_email(to_email, summary)
    print(
        f"[betting_edge_alert] {target_season} week {target_week}: "
        f"{len(summary['validated_angle_matches'])} validated angle match(es), "
        f"{len(summary['raw_edge_outliers'])} raw edge outlier(s). Email sent: {sent}"
    )


def _run_with_alerting():
    try:
        main()
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-betting-alert' failed",
            f"betting_edge_alert_weekly.py raised an unhandled exception:\n\n{traceback.format_exc()}",
        )
        raise


if __name__ == "__main__":
    _run_with_alerting()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_betting_edge_alert_weekly.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -q`
Expected: all pass, no regressions

- [ ] **Step 6: Manual local smoke test (optional but recommended)**

With `.env`'s `USE_LOCAL_DATA=True` temporarily irrelevant (the script forces `False` itself) and real Firestore credentials available locally:

```bash
BETTING_ALERT_EMAIL=your_own_email@example.com DISABLE_OUTBOUND_EMAIL=True python scripts/betting_edge_alert_weekly.py
```

Expected: prints a summary line ending in `Email sent: True` (or the "no edges" / "no upcoming week" skip message) without raising. `DISABLE_OUTBOUND_EMAIL=True` no-ops the actual Resend call per `email_service._outbound_email_disabled()` — safe to run against real data without sending a real email.

- [ ] **Step 7: Commit**

```bash
git add scripts/betting_edge_alert_weekly.py tests/test_betting_edge_alert_weekly.py
git commit -m "feat: add betting_edge_alert_weekly.py Cloud Run Job entrypoint

Ties together betting_edge_alert_service + send_betting_edge_email into the
winspool-betting-alert job script. No-spam: skips sending when both tiers
are empty for the week."
```

---

### Task 5: Deploy wiring, CLAUDE.md, and documented (not executed) GCP provisioning

Code-complete after Task 4. This task wires the new job into the existing deploy tooling and documents the one-time infra commands needed to actually run it in production — following the exact pattern the other 4 scheduled jobs used (`docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md` Task 9). **The `gcloud run jobs create` / `gcloud scheduler jobs create` commands below are documentation only — do not run them as part of this task.** Actually provisioning a new live Cloud Run Job + Cloud Scheduler trigger against the production `fishbone-wins-pool` project is a real infrastructure change with billing/deploy implications; it should be run deliberately (e.g. via `/deploy` or manually) with the user watching, not as an unattended plan step.

**Files:**
- Modify: `deploy/deploy.ps1` (add `winspool-betting-alert` to the `$syncJobs` array)
- Modify: `Dockerfile.sync` (update the top comment listing which scripts use this image)
- Modify: `CLAUDE.md` (Scripts section, Key Environment Variables section, Scheduled Jobs table)
- No test (infra/docs only — verified by review, not pytest)

- [ ] **Step 1: Update `deploy/deploy.ps1`**

Change line 130 from:
```powershell
$syncJobs = @("winspool-sync-daily", "winspool-live-scores", "winspool-schedule-kickoffs")
```
to:
```powershell
$syncJobs = @("winspool-sync-daily", "winspool-live-scores", "winspool-schedule-kickoffs", "winspool-betting-alert")
```

This makes future `deploy.ps1` runs redeploy `winspool-betting-alert`'s image alongside the other sync-image jobs (once the job exists — `gcloud run jobs update` no-ops... actually errors... on a job that doesn't exist yet, so this line has no effect until Step 4 below is actually run once).

Also add a `$bettingAlertEmail` read-from-.env line near the other optional env reads (after line 40's `$alertEmail`), for documentation/future use even though `deploy.ps1` doesn't currently set per-job env vars on `update` (only on `create` — see Step 4):
```powershell
$bettingAlertEmail = Get-DotEnvValue "BETTING_ALERT_EMAIL"  # used only by the one-time `gcloud run jobs create` in Task 5's docs -- deploy.ps1 itself only updates job images, not their env vars
```

- [ ] **Step 2: Update `Dockerfile.sync`'s top comment**

Change:
```dockerfile
# winspool-sync: lean image for scheduled jobs that don't need ML deps
# (run_cron.py / sync_live_scores.py / schedule_kickoffs.py / compute_elo.py)
```
to:
```dockerfile
# winspool-sync: lean image for scheduled jobs that don't need ML deps
# (run_cron.py / sync_live_scores.py / schedule_kickoffs.py / compute_elo.py /
# betting_edge_alert_weekly.py)
```

- [ ] **Step 3: Update `CLAUDE.md`**

In the **Scripts** section's `# Consensus benchmark` area or a new comment block, add near the other job-entrypoint scripts (after `python scripts/schedule_kickoffs.py`'s line):
```
python scripts/betting_edge_alert_weekly.py                # winspool-betting-alert Cloud Run Job entrypoint: composes scan_angles + screen_games into a weekly validated-angle-match + raw-edge-outlier summary, emails BETTING_ALERT_EMAIL if either tier is non-empty. Read-only, never touches the NN+XGB+LR ensemble.
```

In **Key Environment Variables**, add after the `ALERT_EMAIL=...` line:
```
BETTING_ALERT_EMAIL=...     # Recipient for the weekly betting-edge-alert email (scripts/betting_edge_alert_weekly.py) -- deliberately separate from ALERT_EMAIL (job-failure alerts), even though they'll likely be the same address
BETTING_EDGE_THRESHOLD=...  # Optional; |edge_vs_vegas| points above which a game is flagged as a raw (unvalidated) edge outlier in the weekly alert email. Default 3.0.
```

In the **Scheduled Jobs** table, add a row after `winspool-schedule-kickoffs`:
```
| `winspool-betting-alert` | `scripts/betting_edge_alert_weekly.py` | Weekly, Tuesdays shortly after `winspool-schedule-kickoffs-trigger`'s 10:00 UTC run (exact offset tuned once it's clear how quickly lines stabilize) | Composes `pattern_scanner_service.scan_angles` + `betting_screener_service.screen_games` into a weekly validated-angle-match + raw-`edge_vs_vegas`-outlier summary; emails `BETTING_ALERT_EMAIL` only if either tier is non-empty (no-spam). Personal alert, never player-facing. Uses `Dockerfile.sync` (no ML deps). |
```

Update the "**Two Docker images**" paragraph's `Dockerfile.sync` bullet to add `winspool-betting-alert` to the list of jobs it serves:
```
- `Dockerfile.sync` (`python:3.10-slim`, `requirements.txt` only) — used by `winspool-sync-daily`, `winspool-live-scores`, `winspool-schedule-kickoffs`, `winspool-betting-alert`.
```

- [ ] **Step 4: Document the one-time GCP provisioning commands**

Append a new section to `docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md`, right before its existing "Out of scope" section, titled `## One-time GCP provisioning (not yet run)`:

```markdown
## One-time GCP provisioning (not yet run)

Mirrors `docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md` Task 9.
Requires `gcloud` authenticated against `fishbone-wins-pool`. Run manually
(not as an unattended step) once `Dockerfile.sync` has been rebuilt with this
job's code (a normal `deploy.ps1` run handles the rebuild+push once
`winspool-betting-alert` is in `$syncJobs`, but the job itself must exist
before an `update` will find it):

```bash
# 1. Create the Cloud Run Job (reuses the existing winspool-sync image and
#    winspool-scheduler service account/secrets from the original 4-job setup)
gcloud run jobs create winspool-betting-alert \
  --image=us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-sync:latest \
  --command=python --args=scripts/betting_edge_alert_weekly.py \
  --set-secrets=FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest \
  --set-env-vars=ALERT_EMAIL=fischerthomasg@gmail.com,BETTING_ALERT_EMAIL=fischerthomasg@gmail.com,MAX_RETRIES=3 \
  --region=us-east1 --project=fishbone-wins-pool

# 2. Grant the existing scheduler service account run.invoker (same account
#    the other 4 jobs already use)
gcloud run jobs add-iam-policy-binding winspool-betting-alert \
  --member="serviceAccount:winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region=us-east1 --project=fishbone-wins-pool

# 3. Create the Cloud Scheduler trigger -- Tuesdays, 10:30 UTC (30min after
#    winspool-schedule-kickoffs-trigger's 10:00 UTC run), Sept 1 - Feb 10
#    in-season window (two-job split for the year-wrap, same pattern as the
#    original 4 jobs' Task 9)
gcloud scheduler jobs create http winspool-betting-alert-trigger \
  --schedule="30 10 * 9-12 2" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-betting-alert:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" --location=us-east1 --project=fishbone-wins-pool

gcloud scheduler jobs create http winspool-betting-alert-trigger-feb \
  --schedule="30 10 1-10 2 2" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-betting-alert:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" --location=us-east1 --project=fishbone-wins-pool
```

Verify: `gcloud run jobs list --region=us-east1 --project=fishbone-wins-pool` shows
`winspool-betting-alert`; `gcloud scheduler jobs list --location=us-east1` shows
both triggers, state `ENABLED`. Smoke-test with
`gcloud run jobs execute winspool-betting-alert --region=us-east1 --project=fishbone-wins-pool`
and confirm either an email lands at `BETTING_ALERT_EMAIL` or the execution
logs show the no-edges skip message.
```

- [ ] **Step 5: Review the diff**

Run: `git diff deploy/deploy.ps1 Dockerfile.sync CLAUDE.md docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md`
Expected: matches Steps 1-4 above exactly; no unrelated changes.

- [ ] **Step 6: Commit**

```bash
git add deploy/deploy.ps1 Dockerfile.sync CLAUDE.md docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md
git commit -m "docs: wire winspool-betting-alert into deploy tooling and CLAUDE.md

Adds the job to deploy.ps1's sync-image redeploy list and documents the
one-time GCP provisioning commands (not run here -- see spec's new
'One-time GCP provisioning' section for the manual/deliberate follow-up)."
```

---

## After this plan

The code is fully implemented, tested, and deploy-wired after Task 5, but the
job does not exist in production yet — that requires deliberately running
Task 5 Step 4's `gcloud` commands (or folding them into a `/deploy` run),
which was intentionally left as a manual, confirmed action rather than an
automated plan step. Update the spec's `**Status:** Draft` line to `**Status:**
Implemented, pending GCP provisioning` once Task 5 is committed.
