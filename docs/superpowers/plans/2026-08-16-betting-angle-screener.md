# Betting Angle Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the `game_predictions` clobber bug that hides prediction-explain data, then add a read-only "Betting" admin tab that backtests Elo/spread angle filters (e.g. "home dog", "big favorite 10+") against 20 seasons of history and shows which of an upcoming week's games currently match.

**Architecture:** No new data ingestion or Firestore collections. The screener is a pure filter/grading service (`services/betting_screener_service.py`) layered over two already-existing, already-sanctioned data sources: `services.cache_service.get_game_predictions()` (per-game `elo_diff` + Vegas `spread_line`, stored in the `explanation` sub-dict for every game 2006–2026) and `services.data_service.load_data()` (actual `home_score`/`away_score` for grading). A prerequisite bug fix makes `scripts/cache_builder.py`'s nightly/manual write merge into existing per-game records instead of overwriting them, which is what currently destroys the `explanation` data the screener (and the admin prediction-explain tooltip) both depend on.

**Tech Stack:** FastAPI route (`routes/prediction_routes.py`), a new pure-Python service module, vanilla-JS admin tab (`static/js/admin_betting.js`, self-contained like `admin_elo.js`), Jinja2 template addition (`templates/admin.html`). pytest for all new logic.

**Spec:** `docs/superpowers/specs/2026-08-16-betting-angle-screener-design.md`

## Global Constraints

- Routes must never read `rawdata/` CSVs directly — the screener reads exclusively through `services.cache_service.get_game_predictions()` and `services.data_service.load_data()`.
- The new route is admin-only: `Depends(require_admin)`, matching every other `/api/admin/*` endpoint in `routes/prediction_routes.py`.
- No new Firestore collections, no new local `.local_db` files.
- Backtest floor is season 2006 (the first season with computed Elo data — see `services/cache_service.py`'s `get_all_elo_history()` docstring from the prior Elo-history fix).
- `spread_line`/`model_spread`/`elo_diff` are all stored **signed home-minus-away** (positive = home favored / home stronger by Elo) — this convention is already established in `services/nn_prediction_service.py::build_ensemble_lookup` and must be preserved, not reinvented, by any new code that reads or flips these values.
- `game_key` format is `W{week:02d}_{home_team}_{away_team}` (zero-padded week, normalized team abbreviations) — already established in `services/cache_service.py` and `services/nn_feature_engine.py::_normalize_team`.

---

### Task 1: Fix the `game_predictions` clobber bug — merge helper in `cache_service.py`

**Files:**
- Modify: `services/cache_service.py`
- Test: `tests/test_cache_service.py`

**Interfaces:**
- Produces: `merge_thin_game_predictions(existing: dict, fresh: dict) -> dict` — a pure function (no I/O), used by Task 2's fix in `scripts/cache_builder.py`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_service.py` (near the other `game_predictions`-adjacent tests, e.g. after `test_merge_game_predictions_includes_edge_vs_vegas`):

```python
class TestMergeThinGamePredictions:
    """Tests for merge_thin_game_predictions -- the fix for cache_builder.py
    silently overwriting the richer explanation/model_spread/edge_vs_vegas/locked
    fields that scripts/backfill_schedule_predictions.py writes."""

    def test_preserves_explanation_from_existing(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {
            "W01_KC_SF": {
                "pred_winner": "KC", "pred_su_conf": 60.0, "pred_ats_pick": "KC",
                "pred_prob": 0.6, "model_spread": 3.0, "edge_vs_vegas": 0.5,
                "locked": True,
                "explanation": {"elo_diff": 42.0, "vegas_line": 2.5},
            }
        }
        fresh = {
            "W01_KC_SF": {
                "pred_winner": "KC", "pred_su_conf": 61.0,
                "pred_ats_pick": "KC", "pred_prob": 0.61,
            }
        }

        merged = merge_thin_game_predictions(existing, fresh)

        assert merged["W01_KC_SF"]["pred_su_conf"] == 61.0  # fresh value wins
        assert merged["W01_KC_SF"]["explanation"] == {"elo_diff": 42.0, "vegas_line": 2.5}  # preserved
        assert merged["W01_KC_SF"]["model_spread"] == 3.0  # preserved
        assert merged["W01_KC_SF"]["locked"] is True  # preserved

    def test_creates_new_entry_when_key_not_in_existing(self):
        from services.cache_service import merge_thin_game_predictions

        merged = merge_thin_game_predictions(
            {}, {"W02_BUF_MIA": {"pred_winner": "BUF", "pred_prob": 0.7}}
        )
        assert merged == {"W02_BUF_MIA": {"pred_winner": "BUF", "pred_prob": 0.7}}

    def test_preserves_existing_keys_not_touched_by_fresh(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {"W01_KC_SF": {"pred_winner": "KC"}, "W02_BUF_MIA": {"pred_winner": "BUF"}}
        merged = merge_thin_game_predictions(existing, {"W01_KC_SF": {"pred_winner": "SF"}})

        assert merged["W01_KC_SF"]["pred_winner"] == "SF"
        assert merged["W02_BUF_MIA"]["pred_winner"] == "BUF"  # untouched, still present

    def test_does_not_mutate_inputs(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {"W01_KC_SF": {"pred_winner": "KC", "explanation": {"elo_diff": 1.0}}}
        fresh = {"W01_KC_SF": {"pred_winner": "SF"}}
        merge_thin_game_predictions(existing, fresh)

        assert existing["W01_KC_SF"]["pred_winner"] == "KC"  # original untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_service.py::TestMergeThinGamePredictions -v`
Expected: FAIL with `ImportError: cannot import name 'merge_thin_game_predictions'`

- [ ] **Step 3: Implement the merge function**

Add to `services/cache_service.py`, directly after `write_game_predictions` (before the `merge_game_predictions` dataframe-merge function, so it stays next to the dict-shaped `game_predictions` helpers it's related to):

```python
def merge_thin_game_predictions(existing: dict, fresh: dict) -> dict:
    """Merge a thin per-game predictions map into an existing one, preserving any
    richer fields (explanation, model_spread, edge_vs_vegas, locked) already stored
    for a game that `fresh` doesn't know about.

    Used by scripts/cache_builder.py, which recomputes only pred_winner/pred_su_conf/
    pred_ats_pick/pred_prob on every run. Without this merge, write_game_predictions'
    whole-document overwrite would silently destroy whatever
    scripts/backfill_schedule_predictions.py --features previously computed for every
    game in the season -- including the elo_diff/vegas_line data the admin
    prediction-explain tooltip and the betting screener both depend on.

    Pure function -- does not mutate `existing` or `fresh`.
    """
    merged = {k: dict(v) for k, v in existing.items()}
    for key, thin in fresh.items():
        merged[key] = {**merged.get(key, {}), **thin}
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_service.py::TestMergeThinGamePredictions -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add services/cache_service.py tests/test_cache_service.py
git commit -m "fix: add merge_thin_game_predictions to stop cache_builder from clobbering explanation data"
```

---

### Task 2: Wire the merge fix into `cache_builder.py`

**Files:**
- Modify: `scripts/cache_builder.py`

**Interfaces:**
- Consumes: `merge_thin_game_predictions` from Task 1, `get_game_predictions` (already exists in `services/cache_service.py`).

This file initializes real Firebase credentials at import time (`firebase_admin.initialize_app(...)` runs unconditionally at module load), so it has no existing automated test coverage and none is being added here — Task 1's unit tests on the pure merge function are what actually prove this fix works. This task is a small, mechanical two-line change plus one import.

- [ ] **Step 1: Update the import**

In `scripts/cache_builder.py`, change:

```python
from services.cache_service import write_cache, is_cache_final, write_game_predictions
```

to:

```python
from services.cache_service import (
    write_cache, is_cache_final, write_game_predictions,
    get_game_predictions, merge_thin_game_predictions,
)
```

- [ ] **Step 2: Merge before writing**

In `scripts/cache_builder.py`, find this block (inside `build_year`, in the `schedule_enriched` analytic section):

```python
                if pmap:
                    write_game_predictions(year, pmap)
                    print(f"  [ok]   game_predictions year={year} ({len(pmap)} games)")
```

Replace it with:

```python
                if pmap:
                    existing = get_game_predictions(year)
                    merged = merge_thin_game_predictions(existing, pmap)
                    write_game_predictions(year, merged)
                    print(f"  [ok]   game_predictions year={year} "
                          f"({len(pmap)} refreshed, {len(merged)} total)")
```

- [ ] **Step 3: Verify by reading the diff**

Run `git diff scripts/cache_builder.py` and confirm: the import includes both new names, and the write site now reads-merges-writes instead of writing `pmap` directly. There is no automated test for this call site (see rationale above) — this step is a manual read-through, not a command with expected output.

- [ ] **Step 4: Commit**

```bash
git add scripts/cache_builder.py
git commit -m "fix: cache_builder merges into existing game_predictions instead of overwriting"
```

---

### Task 3: Betting screener service — filter matching, grading, screening

**Files:**
- Create: `services/betting_screener_service.py`
- Test: `tests/test_betting_screener_service.py`

**Interfaces:**
- Consumes: nothing beyond `services.nn_feature_engine._normalize_team` (already exists, used throughout the codebase for team-abbreviation normalization).
- Produces (used by Task 4's route):
  - `PREBUILT_ANGLES: dict[str, dict]` — angle name → filter kwargs.
  - `find_next_upcoming_week(games_df, season: int) -> int | None`
  - `screen_games(predictions_by_season: dict[int, dict], games_df, *, target_season: int, target_week: int, side: str = "any", favorite_or_dog: str = "any", spread_min: float | None = None, spread_max: float | None = None, elo_diff_min: float | None = None, elo_diff_max: float | None = None) -> dict` — returns `{"backtest": {...}, "candidates": [...]}` per the spec's API shape.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_betting_screener_service.py`:

```python
"""Tests for services/betting_screener_service.py -- the Elo/spread angle
backtester behind the admin Betting tab. Pure logic, no Firestore/network."""
import math
import pandas as pd
import pytest

from services.betting_screener_service import (
    PREBUILT_ANGLES,
    _favorite_or_dog,
    _side_view,
    matches_filter,
    grade_bet,
    find_next_upcoming_week,
    screen_games,
)


class TestFavoriteOrDog:
    def test_positive_spread_is_favorite(self):
        assert _favorite_or_dog(3.5) == "favorite"

    def test_negative_spread_is_dog(self):
        assert _favorite_or_dog(-3.5) == "dog"

    def test_zero_spread_is_pickem_none(self):
        assert _favorite_or_dog(0) is None

    def test_none_spread_is_none(self):
        assert _favorite_or_dog(None) is None


class TestSideView:
    def test_flips_sign_for_away(self):
        views = _side_view(spread_line=6.0, elo_diff=42.0)
        assert views == [("home", 6.0, 42.0), ("away", -6.0, -42.0)]

    def test_handles_none_values(self):
        views = _side_view(spread_line=None, elo_diff=10.0)
        assert views == [("home", None, 10.0), ("away", None, -10.0)]


class TestMatchesFilter:
    def test_side_filter_excludes_other_side(self):
        assert not matches_filter(
            side="away", spread_for_side=3.0, elo_diff_for_side=10.0,
            f_side="home", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_side_any_matches_both(self):
        for side in ("home", "away"):
            assert matches_filter(
                side=side, spread_for_side=3.0, elo_diff_for_side=10.0,
                f_side="any", f_favorite_or_dog="any",
                f_spread_min=None, f_spread_max=None,
                f_elo_diff_min=None, f_elo_diff_max=None,
            )

    def test_favorite_or_dog_filter(self):
        # side favored by 3 -> "favorite"
        assert matches_filter(
            side="home", spread_for_side=3.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="favorite",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=3.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="dog",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_spread_range(self):
        # |spread| = 10, range [10, None] should match; [11, None] should not
        assert matches_filter(
            side="home", spread_for_side=10.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=10.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=10.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=11.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_elo_diff_range(self):
        assert matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=50.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=25.0, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=10.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=25.0, f_elo_diff_max=None,
        )

    def test_null_spread_fails_a_spread_bound(self):
        assert not matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=10.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=3.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )


class TestGradeBet:
    def test_home_loss_matches_real_2025_week1_no_ari_game(self):
        # Verified against .local_db/nfl_games.pkl: NO 13 - ARI 20, spread_line=-6.0
        # (home underdog by 6). Home lost by 7, worse than getting 6 -> home does NOT cover.
        assert grade_bet("home", home_score=13, away_score=20, spread_line=-6.0) == "loss"
        assert grade_bet("away", home_score=13, away_score=20, spread_line=-6.0) == "win"

    def test_push(self):
        # home favored by 6 (spread_line=6), wins by exactly 6 -> push
        assert grade_bet("home", home_score=20, away_score=14, spread_line=6.0) == "push"

    def test_none_when_unplayed(self):
        assert grade_bet("home", home_score=None, away_score=None, spread_line=3.0) is None

    def test_none_when_spread_unknown(self):
        assert grade_bet("home", home_score=20, away_score=14, spread_line=None) is None

    def test_none_on_nan_score(self):
        assert grade_bet("home", home_score=float("nan"), away_score=14, spread_line=3.0) is None


class TestPrebuiltAngles:
    def test_all_angles_have_valid_shape(self):
        for name, angle in PREBUILT_ANGLES.items():
            assert angle["side"] in ("home", "away", "any")
            assert angle["favorite_or_dog"] in ("favorite", "dog", "any")

    def test_home_dog_and_away_favorite_are_opposite_sides_of_same_game(self):
        assert PREBUILT_ANGLES["home_dog"]["side"] == "home"
        assert PREBUILT_ANGLES["home_dog"]["favorite_or_dog"] == "dog"
        assert PREBUILT_ANGLES["away_favorite"]["side"] == "away"
        assert PREBUILT_ANGLES["away_favorite"]["favorite_or_dog"] == "favorite"

    def test_big_favorite_and_underdog_use_spread_min_10(self):
        assert PREBUILT_ANGLES["big_favorite"]["spread_min"] == 10.0
        assert PREBUILT_ANGLES["big_underdog"]["spread_min"] == 10.0


class TestFindNextUpcomingWeek:
    def _games_df(self, rows):
        return pd.DataFrame(rows)

    def test_finds_earliest_unplayed_week(self):
        df = self._games_df([
            {"season": 2026, "week": 1, "result": 3.0},
            {"season": 2026, "week": 2, "result": None},
            {"season": 2026, "week": 3, "result": None},
        ])
        assert find_next_upcoming_week(df, 2026) == 2

    def test_none_when_season_fully_complete(self):
        df = self._games_df([{"season": 2026, "week": 1, "result": 3.0}])
        assert find_next_upcoming_week(df, 2026) is None

    def test_none_when_season_missing(self):
        df = self._games_df([{"season": 2025, "week": 1, "result": 3.0}])
        assert find_next_upcoming_week(df, 2026) is None

    def test_none_when_empty(self):
        assert find_next_upcoming_week(pd.DataFrame(), 2026) is None


class TestScreenGames:
    def _predictions(self):
        return {
            2025: {
                # home favored by 6, home wins by 10 -> home covers (win)
                "W01_KC_SF": {"explanation": {"elo_diff": 80.0, "vegas_line": 6.0}},
                # home underdog by 3 (away favorite), away wins by 1 -> away does not cover (loss for away side)
                "W01_BUF_MIA": {"explanation": {"elo_diff": -20.0, "vegas_line": -3.0}},
            },
            2026: {
                # future game, no result yet -- candidate only, ungraded
                "W01_DAL_PHI": {"explanation": {"elo_diff": 15.0, "vegas_line": -2.0}},
            },
        }

    def _games_df(self):
        return pd.DataFrame([
            {"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF",
             "home_score": 30, "away_score": 20},
            {"season": 2025, "week": 1, "home_team": "BUF", "away_team": "MIA",
             "home_score": 20, "away_score": 21},
            {"season": 2026, "week": 1, "home_team": "DAL", "away_team": "PHI",
             "home_score": None, "away_score": None},
        ])

    def test_backtest_tallies_only_historical_games(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        # side="any" -> KC (home, favorite, covers=win) and BUF (home, dog) both graded;
        # away sides (SF favorite-loss, MIA dog-win) also graded since side defaults to "any"
        assert result["backtest"]["n"] == 4  # 2 historical games x 2 sides each
        assert result["backtest"]["wins"] + result["backtest"]["losses"] == 4

    def test_candidates_only_from_target_week(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        seasons_weeks = {(c["season"], c["week"]) for c in result["candidates"]}
        assert seasons_weeks == {(2026, 1)}

    def test_side_any_does_not_double_count_a_single_favorite(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            side="any", favorite_or_dog="favorite",
        )
        # Exactly one favorite per historical game -> 2 games, 2 backtest entries, not 4
        assert result["backtest"]["n"] == 2

    def test_elo_filter_narrows_candidates(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            elo_diff_min=50.0,
        )
        # Only KC's home elo_diff=80 clears 50; DAL/PHI (target week) has elo_diff=15/-15 for each side
        assert result["candidates"] == []

    def test_ungraded_future_candidate_flagged_not_already_played(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        assert all(c["already_played"] is False for c in result["candidates"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_betting_screener_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.betting_screener_service'`

- [ ] **Step 3: Implement the service**

Create `services/betting_screener_service.py`:

```python
"""services/betting_screener_service.py — Elo/spread angle backtesting for the
admin Betting tab.

Reads already-computed per-game data (elo_diff + Vegas spread_line, from
services.cache_service.get_game_predictions) and actual game results (from
services.data_service.load_data) so an admin can build simple filter rules
("home dog", "elo favors home by 50+", etc.), see their historical
against-the-spread (ATS) record, and see which of an upcoming week's games
currently match. Read-only -- never writes anything, and does not touch the
NN+XGB+LR ensemble.

Sign convention (matches services/nn_prediction_service.py::build_ensemble_lookup):
spread_line and elo_diff are both stored signed home-minus-away. Positive
spread_line = home favored; positive elo_diff = home stronger by Elo.
"""
from __future__ import annotations

import math
from typing import Optional

from services.nn_feature_engine import _normalize_team

# Elo data starts in 2006 (see services/cache_service.py's get_all_elo_history);
# earlier seasons have Vegas spreads but no elo_diff, so they'd only ever match
# spread-only filters and would understate Elo-filtered backtest sample sizes.
BACKTEST_MIN_SEASON = 2006

PREBUILT_ANGLES: dict[str, dict] = {
    "home_dog":      {"side": "home", "favorite_or_dog": "dog"},
    "away_favorite": {"side": "away", "favorite_or_dog": "favorite"},
    "big_favorite":  {"side": "any", "favorite_or_dog": "favorite", "spread_min": 10.0},
    "big_underdog":  {"side": "any", "favorite_or_dog": "dog", "spread_min": 10.0},
}


def _favorite_or_dog(spread_for_side: Optional[float]) -> Optional[str]:
    """'favorite' if this side is favored (positive spread), 'dog' if not,
    None if the spread is unknown or a pick'em (0 -- no favorite exists)."""
    if spread_for_side is None:
        return None
    if spread_for_side > 0:
        return "favorite"
    if spread_for_side < 0:
        return "dog"
    return None


def _side_view(spread_line: Optional[float], elo_diff: Optional[float]):
    """Return [("home", spread, elo), ("away", spread, elo)] with the away
    tuple's values sign-flipped to away's perspective."""
    flip = lambda v: -v if v is not None else None
    return [
        ("home", spread_line, elo_diff),
        ("away", flip(spread_line), flip(elo_diff)),
    ]


def matches_filter(
    *, side: str, spread_for_side: Optional[float], elo_diff_for_side: Optional[float],
    f_side: str, f_favorite_or_dog: str,
    f_spread_min: Optional[float], f_spread_max: Optional[float],
    f_elo_diff_min: Optional[float], f_elo_diff_max: Optional[float],
) -> bool:
    """True if one team-side candidate satisfies the given filter bounds.
    All non-null bounds are AND-combined."""
    if f_side != "any" and side != f_side:
        return False

    if f_favorite_or_dog != "any" and _favorite_or_dog(spread_for_side) != f_favorite_or_dog:
        return False

    magnitude = abs(spread_for_side) if spread_for_side is not None else None
    if f_spread_min is not None and (magnitude is None or magnitude < f_spread_min):
        return False
    if f_spread_max is not None and (magnitude is None or magnitude > f_spread_max):
        return False

    if f_elo_diff_min is not None and (elo_diff_for_side is None or elo_diff_for_side < f_elo_diff_min):
        return False
    if f_elo_diff_max is not None and (elo_diff_for_side is None or elo_diff_for_side > f_elo_diff_max):
        return False

    return True


def grade_bet(side: str, home_score, away_score, spread_line) -> Optional[str]:
    """Returns 'win' | 'loss' | 'push' for a bet on `side`, or None if the game
    hasn't been played yet or the spread is unknown/invalid."""
    if home_score is None or away_score is None or spread_line is None:
        return None
    try:
        home_score = float(home_score)
        away_score = float(away_score)
        spread_line = float(spread_line)
    except (TypeError, ValueError):
        return None
    if math.isnan(home_score) or math.isnan(away_score) or math.isnan(spread_line):
        return None

    result = home_score - away_score
    margin_for_side = result if side == "home" else -result
    line_for_side = spread_line if side == "home" else -spread_line

    if margin_for_side > line_for_side:
        return "win"
    if margin_for_side < line_for_side:
        return "loss"
    return "push"


def find_next_upcoming_week(games_df, season: int) -> Optional[int]:
    """Earliest week in `season` with at least one unplayed game (null result),
    or None if the season is missing or fully complete."""
    if games_df is None or games_df.empty or "season" not in games_df.columns:
        return None
    season_games = games_df[games_df["season"] == season]
    if season_games.empty:
        return None
    unplayed = season_games[season_games["result"].isna()]
    if unplayed.empty:
        return None
    return int(unplayed["week"].min())


def screen_games(
    predictions_by_season: dict,
    games_df,
    *,
    target_season: int,
    target_week: int,
    side: str = "any",
    favorite_or_dog: str = "any",
    spread_min: Optional[float] = None,
    spread_max: Optional[float] = None,
    elo_diff_min: Optional[float] = None,
    elo_diff_max: Optional[float] = None,
) -> dict:
    """Backtest a filter across every season in predictions_by_season, and list
    the target week's currently-matching candidates.

    predictions_by_season: {season: {game_key: pred_dict}}, as returned per-season
        by services.cache_service.get_game_predictions() -- pred_dict's
        `explanation` sub-dict carries elo_diff and vegas_line.
    games_df: full multi-season games dataframe (services.data_service.load_data()),
        used only to look up actual home_score/away_score for grading.
    """
    results_by_key = {}
    if games_df is not None and not games_df.empty:
        for row in games_df.itertuples(index=False):
            ht = _normalize_team(str(getattr(row, "home_team", "") or ""))
            at = _normalize_team(str(getattr(row, "away_team", "") or ""))
            wk = getattr(row, "week", None)
            season = getattr(row, "season", None)
            if ht and at and wk is not None and season is not None:
                results_by_key[(int(season), int(wk), ht, at)] = (
                    getattr(row, "home_score", None), getattr(row, "away_score", None),
                )

    wins = losses = pushes = 0
    candidates = []

    for season, preds in predictions_by_season.items():
        for game_key, pred in preds.items():
            parts = game_key.split("_")
            if len(parts) != 3:
                continue
            wk_str, ht, at = parts
            try:
                wk = int(wk_str.lstrip("W"))
            except ValueError:
                continue

            ex = pred.get("explanation") or {}
            spread_line = ex.get("vegas_line")
            elo_diff = ex.get("elo_diff")

            for cand_side, spread_for_side, elo_for_side in _side_view(spread_line, elo_diff):
                if not matches_filter(
                    side=cand_side, spread_for_side=spread_for_side, elo_diff_for_side=elo_for_side,
                    f_side=side, f_favorite_or_dog=favorite_or_dog,
                    f_spread_min=spread_min, f_spread_max=spread_max,
                    f_elo_diff_min=elo_diff_min, f_elo_diff_max=elo_diff_max,
                ):
                    continue

                home_score, away_score = results_by_key.get((season, wk, ht, at), (None, None))
                outcome = grade_bet(cand_side, home_score, away_score, spread_line)
                if outcome == "win":
                    wins += 1
                elif outcome == "loss":
                    losses += 1
                elif outcome == "push":
                    pushes += 1

                if season == target_season and wk == target_week:
                    candidates.append({
                        "season": season, "week": wk,
                        "home_team": ht, "away_team": at,
                        "side": cand_side,
                        "spread_line": spread_line,
                        "elo_diff": elo_for_side,
                        "already_played": outcome is not None,
                    })

    cover_pct = round(wins / (wins + losses), 4) if (wins + losses) > 0 else None

    return {
        "backtest": {
            "wins": wins, "losses": losses, "pushes": pushes,
            "n": wins + losses + pushes, "cover_pct": cover_pct,
        },
        "candidates": candidates,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_betting_screener_service.py -v`
Expected: all tests pass (24 tests across the 7 classes above)

- [ ] **Step 5: Commit**

```bash
git add services/betting_screener_service.py tests/test_betting_screener_service.py
git commit -m "feat: add betting angle screener service (filter matching, ATS grading, backtest)"
```

---

### Task 4: `GET /api/admin/betting/screen` route

**Files:**
- Modify: `routes/prediction_routes.py`
- Test: `tests/test_betting_screen_route.py`

**Interfaces:**
- Consumes: `services.betting_screener_service.screen_games`, `.find_next_upcoming_week`, `services.cache_service.get_game_predictions`, `services.data_service.load_data` (all already exist after Task 3).
- Produces: the route itself, consumed by Task 6's frontend.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_betting_screen_route.py`:

```python
"""Tests for GET /api/admin/betting/screen."""
from unittest.mock import patch
import pandas as pd

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _games_df():
    return pd.DataFrame([
        {"season": 2026, "week": 1, "home_team": "KC", "away_team": "SF",
         "home_score": None, "away_score": None, "result": None},
        {"season": 2026, "week": 2, "home_team": "BUF", "away_team": "MIA",
         "home_score": None, "away_score": None, "result": None},
    ])


def _predictions_for(season):
    if season != 2026:
        return {}
    return {
        "W01_KC_SF": {"explanation": {"elo_diff": 40.0, "vegas_line": 3.0}},
        "W02_BUF_MIA": {"explanation": {"elo_diff": -10.0, "vegas_line": -2.0}},
    }


def test_requires_admin():
    response = client.get("/api/admin/betting/screen")
    assert response.status_code == 401


def test_rejects_invalid_side(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/screen?side=sideways",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_rejects_invalid_favorite_or_dog(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)):
        response = client.get(
            "/api/admin/betting/screen?favorite_or_dog=maybe",
            headers={"Authorization": admin_token},
        )
    assert response.status_code == 400


def test_default_season_and_week_resolve_from_schedule(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get("/api/admin/betting/screen", headers={"Authorization": admin_token})

    assert response.status_code == 200
    data = response.json()
    assert data["target_season"] == 2026
    assert data["target_week"] == 1  # earliest unplayed week


def test_explicit_week_used_over_default(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get(
            "/api/admin/betting/screen?season=2026&week=2",
            headers={"Authorization": admin_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["target_week"] == 2
    weeks = {c["week"] for c in data["candidates"]}
    assert weeks == {2}


def test_filter_narrows_candidates(admin_token):
    with patch("routes.prediction_routes.load_data", return_value=(None, None, _games_df(), None, None, None, None)), \
         patch("services.cache_service.get_game_predictions", side_effect=_predictions_for):
        response = client.get(
            "/api/admin/betting/screen?season=2026&week=1&elo_diff_min=100",
            headers={"Authorization": admin_token},
        )

    assert response.status_code == 200
    assert response.json()["candidates"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_betting_screen_route.py -v`
Expected: FAIL — `404 Not Found` for every request (route doesn't exist yet)

- [ ] **Step 3: Implement the route**

In `routes/prediction_routes.py`, add after `get_elo_history` (reuses the same `pathlib`/`logger`/`require_admin` imports already present in this file — no new imports needed at the top of the file):

```python
@router.get("/admin/betting/screen")
async def get_betting_screen(
    season: int | None = None,
    week: int | None = None,
    side: str = "any",
    favorite_or_dog: str = "any",
    spread_min: float | None = None,
    spread_max: float | None = None,
    elo_diff_min: float | None = None,
    elo_diff_max: float | None = None,
    _: dict = Depends(require_admin),
):
    """Admin-only: backtest an Elo/spread angle and list a week's matching games.

    Never touches the NN+XGB+LR ensemble -- reads only the elo_diff/vegas_line
    already stored per game (services.cache_service.get_game_predictions) plus
    actual results (services.data_service.load_data) for grading.
    """
    try:
        from services.betting_screener_service import (
            screen_games, find_next_upcoming_week, BACKTEST_MIN_SEASON,
        )
        from services.cache_service import get_game_predictions

        if side not in ("home", "away", "any"):
            return JSONResponse(status_code=400, content={"error": "side must be home, away, or any"})
        if favorite_or_dog not in ("favorite", "dog", "any"):
            return JSONResponse(status_code=400, content={"error": "favorite_or_dog must be favorite, dog, or any"})

        _, _, all_games, _, _, _, _ = load_data()
        if all_games.empty:
            return JSONResponse(status_code=404, content={"error": "No schedule data available."})

        target_season = season if season is not None else int(all_games["season"].max())

        target_week = week
        if target_week is None:
            target_week = find_next_upcoming_week(all_games, target_season)
            if target_week is None:
                target_week = 1

        max_season = int(all_games["season"].max())
        predictions_by_season = {}
        for yr in range(BACKTEST_MIN_SEASON, max_season + 1):
            preds = get_game_predictions(yr)
            if preds:
                predictions_by_season[yr] = preds

        result = screen_games(
            predictions_by_season, all_games,
            target_season=target_season, target_week=target_week,
            side=side, favorite_or_dog=favorite_or_dog,
            spread_min=spread_min, spread_max=spread_max,
            elo_diff_min=elo_diff_min, elo_diff_max=elo_diff_max,
        )
        result["target_season"] = target_season
        result["target_week"] = target_week
        return JSONResponse(content=result)
    except Exception as e:
        logger.exception("Unhandled error in get_betting_screen")
        return server_error()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_betting_screen_route.py -v`
Expected: all 6 tests pass

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run: `pytest tests/ -q`
Expected: all tests pass (no regressions in existing prediction_routes tests)

- [ ] **Step 6: Commit**

```bash
git add routes/prediction_routes.py tests/test_betting_screen_route.py
git commit -m "feat: add GET /api/admin/betting/screen route"
```

---

### Task 5: Betting tab markup in `templates/admin.html`

**Files:**
- Modify: `templates/admin.html`

**Interfaces:**
- Produces: the DOM elements Task 6's `admin_betting.js` binds to (exact IDs below — Task 6 depends on these matching exactly).

- [ ] **Step 1: Add the tab button**

In `templates/admin.html`, find:

```html
        <button class="admin-tab-btn tab-btn" data-tab="consensus-section">Consensus</button>
    </div>
```

Replace with:

```html
        <button class="admin-tab-btn tab-btn" data-tab="consensus-section">Consensus</button>
        <button class="admin-tab-btn tab-btn" data-tab="betting-section">Betting</button>
    </div>
```

- [ ] **Step 2: Add the tab content**

Find the end of the Consensus section:

```html
        <div id="consensus-table-wrap" style="margin-top: 1.25rem; overflow-x: auto;"></div>
    </div>

    <p id="admin-message" style="margin-top: 1rem; text-align: center; font-weight: 600;"></p>
```

Replace with (inserting the new section between Consensus and the closing `admin-message` paragraph):

```html
        <div id="consensus-table-wrap" style="margin-top: 1.25rem; overflow-x: auto;"></div>
    </div>

    <div id="betting-section" class="tab-content card-glass hidden" style="height: auto;">
        <h2>Betting Angle Screener</h2>
        <p>Backtest simple Elo/spread rules against the spread (ATS), then see which
           games in a chosen week currently match. Goes beyond the NN+XGB+LR model --
           this reads raw Elo and Vegas-line signals directly.</p>

        <!-- Prebuilt angles -->
        <div style="display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 1rem;">
            <span style="font-size: 0.8rem; color: var(--text-secondary); align-self: center;">Prebuilt angles:</span>
            <button class="betting-angle-btn btn-secondary" data-angle="home_dog" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Home Dog</button>
            <button class="betting-angle-btn btn-secondary" data-angle="away_favorite" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Away Favorite</button>
            <button class="betting-angle-btn btn-secondary" data-angle="big_favorite" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Big Favorite (10+)</button>
            <button class="betting-angle-btn btn-secondary" data-angle="big_underdog" style="padding: 0.25rem 0.75rem; font-size: 0.8rem;">Big Underdog (10+)</button>
        </div>

        <!-- Custom filter builder -->
        <div style="display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap; margin-top: 1.25rem;">
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Side</label>
                <select id="betting-side" class="admin-input">
                    <option value="any">Either</option>
                    <option value="home">Home</option>
                    <option value="away">Away</option>
                </select>
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Favorite / Dog</label>
                <select id="betting-fav-dog" class="admin-input">
                    <option value="any">Either</option>
                    <option value="favorite">Favorite</option>
                    <option value="dog">Underdog</option>
                </select>
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Spread min</label>
                <input id="betting-spread-min" type="number" step="0.5" class="admin-input" style="width: 90px;">
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Spread max</label>
                <input id="betting-spread-max" type="number" step="0.5" class="admin-input" style="width: 90px;">
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Elo diff min</label>
                <input id="betting-elo-min" type="number" step="1" class="admin-input" style="width: 90px;">
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Elo diff max</label>
                <input id="betting-elo-max" type="number" step="1" class="admin-input" style="width: 90px;">
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Season</label>
                <input id="betting-season" type="number" class="admin-input" style="width: 90px;">
            </div>
            <div>
                <label style="display: block; font-size: 0.8rem; color: var(--text-secondary); margin-bottom: 4px;">Week</label>
                <input id="betting-week" type="number" class="admin-input" style="width: 70px;">
            </div>
            <button id="betting-run-btn" class="btn-primary" style="padding: 0.5rem 1.25rem;">Run Screen</button>
        </div>

        <!-- Results -->
        <div id="betting-backtest-summary" style="margin-top: 1.25rem;"></div>
        <div id="betting-candidates-wrap" style="margin-top: 1.25rem; overflow-x: auto;"></div>
    </div>

    <p id="admin-message" style="margin-top: 1rem; text-align: center; font-weight: 600;"></p>
```

- [ ] **Step 3: Add the script tag**

In `templates/admin.html`, find:

```html
<script type="module" src="/static/js/admin_accuracy.js"></script>
```

Add immediately after it:

```html
<script type="module" src="/static/js/admin_accuracy.js"></script>
<script type="module" src="/static/js/admin_betting.js"></script>
```

- [ ] **Step 4: Verify the tab switches**

This step has no automated test (it's markup only, wired up in Task 6). Verified as part of Task 7's manual browser check.

- [ ] **Step 5: Commit**

```bash
git add templates/admin.html
git commit -m "feat: add Betting tab markup to admin panel"
```

---

### Task 6: `admin_betting.js` — filter builder, prebuilt angles, results rendering

**Files:**
- Create: `static/js/admin_betting.js`

**Interfaces:**
- Consumes: `GET /api/admin/betting/screen` (Task 4), the DOM element IDs from Task 5, `AuthService` from `./auth_service.js` (already used identically by `admin_elo.js`).

This file is self-contained (not part of the `AdminApp` class in `admin_main.js`) — it follows the exact same lazy-init-on-tab-click pattern as `static/js/admin_elo.js`.

- [ ] **Step 1: Implement the module**

Create `static/js/admin_betting.js`:

```javascript
/**
 * admin_betting.js — Betting Angle Screener for the Admin panel.
 *
 * Backtests simple Elo/spread filters (angles) against history and shows
 * which games in a chosen week currently match. Fetches
 * GET /api/admin/betting/screen. Self-contained, like admin_elo.js --
 * lazy-loads its data on first click of the Betting tab.
 */

import { AuthService } from './auth_service.js';

const PREBUILT_ANGLES = {
    home_dog:      { side: 'home', fav_dog: 'dog' },
    away_favorite: { side: 'away', fav_dog: 'favorite' },
    big_favorite:  { side: 'any',  fav_dog: 'favorite', spread_min: 10 },
    big_underdog:  { side: 'any',  fav_dog: 'dog',       spread_min: 10 },
};

class BettingScreener {
    constructor() {
        this._loaded = false;
        this._sortKey = 'week';
        this._sortDir = 1;
        this._candidates = [];

        this._side = document.getElementById('betting-side');
        this._favDog = document.getElementById('betting-fav-dog');
        this._spreadMin = document.getElementById('betting-spread-min');
        this._spreadMax = document.getElementById('betting-spread-max');
        this._eloMin = document.getElementById('betting-elo-min');
        this._eloMax = document.getElementById('betting-elo-max');
        this._season = document.getElementById('betting-season');
        this._week = document.getElementById('betting-week');
        this._runBtn = document.getElementById('betting-run-btn');
        this._summaryEl = document.getElementById('betting-backtest-summary');
        this._candidatesEl = document.getElementById('betting-candidates-wrap');

        this._runBtn?.addEventListener('click', () => this._run());

        document.querySelectorAll('.betting-angle-btn').forEach(btn => {
            btn.addEventListener('click', () => this._applyAngle(btn.dataset.angle));
        });

        const tabBtn = document.querySelector('[data-tab="betting-section"]');
        if (tabBtn) {
            tabBtn.addEventListener('click', () => {
                if (!this._loaded) { this._loaded = true; this._run(); }
            });
        }
    }

    _applyAngle(name) {
        const angle = PREBUILT_ANGLES[name];
        if (!angle) return;
        this._side.value = angle.side || 'any';
        this._favDog.value = angle.fav_dog || 'any';
        this._spreadMin.value = angle.spread_min ?? '';
        this._spreadMax.value = angle.spread_max ?? '';
        this._eloMin.value = angle.elo_min ?? '';
        this._eloMax.value = angle.elo_max ?? '';
        this._run();
    }

    _buildQuery() {
        const params = new URLSearchParams();
        if (this._side.value !== 'any') params.set('side', this._side.value);
        if (this._favDog.value !== 'any') params.set('favorite_or_dog', this._favDog.value);
        if (this._spreadMin.value !== '') params.set('spread_min', this._spreadMin.value);
        if (this._spreadMax.value !== '') params.set('spread_max', this._spreadMax.value);
        if (this._eloMin.value !== '') params.set('elo_diff_min', this._eloMin.value);
        if (this._eloMax.value !== '') params.set('elo_diff_max', this._eloMax.value);
        if (this._season.value !== '') params.set('season', this._season.value);
        if (this._week.value !== '') params.set('week', this._week.value);
        return params.toString();
    }

    async _run() {
        this._summaryEl.innerHTML = '<p style="color: var(--text-secondary);">Loading…</p>';
        this._candidatesEl.innerHTML = '';

        try {
            const token = AuthService.getToken();
            const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
            const qs = this._buildQuery();
            const resp = await fetch(`/api/admin/betting/screen${qs ? `?${qs}` : ''}`, { headers });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                this._summaryEl.innerHTML = `<p style="color: var(--accent-red);">${err.error || 'Failed to load.'}</p>`;
                return;
            }
            const data = await resp.json();
            this._season.value = data.target_season;
            this._week.value = data.target_week;
            this._candidates = data.candidates;
            this._renderSummary(data);
            this._renderCandidates();
        } catch (e) {
            this._summaryEl.innerHTML = `<p style="color: var(--accent-red);">Failed to load: ${e.message}</p>`;
        }
    }

    _renderSummary(data) {
        const b = data.backtest;
        const pct = b.cover_pct != null ? `${(b.cover_pct * 100).toFixed(1)}%` : '—';
        this._summaryEl.innerHTML = `
            <div style="display: flex; gap: 1.5rem; flex-wrap: wrap; align-items: center;">
                <div><strong>${b.wins}-${b.losses}-${b.pushes}</strong> ATS record</div>
                <div>Cover rate <strong>${pct}</strong></div>
                <div><strong>${b.n}</strong> historical bets</div>
                <div style="color: var(--text-secondary); font-size: 0.85rem;">
                    Week ${data.target_week}, ${data.target_season}
                </div>
            </div>`;
    }

    _renderCandidates() {
        if (!this._candidates.length) {
            this._candidatesEl.innerHTML = '<p style="color: var(--text-secondary);">No games in this week match the filter.</p>';
            return;
        }

        const columns = [
            { label: 'Matchup', key: 'matchup', render: c => `${c.away_team} @ ${c.home_team}` },
            { label: 'Side', key: 'side', render: c => c.side === 'home' ? c.home_team : c.away_team },
            { label: 'Spread', key: 'spread_line', render: c => c.spread_line == null ? '—' : c.spread_line.toFixed(1) },
            { label: 'Elo Diff', key: 'elo_diff', render: c => c.elo_diff == null ? '—' : c.elo_diff.toFixed(1) },
            { label: 'Status', key: 'already_played', render: c => c.already_played ? 'Played' : 'Upcoming' },
        ];

        const sorted = [...this._candidates].sort((a, b) => {
            const av = a[this._sortKey], bv = b[this._sortKey];
            if (av === bv) return 0;
            if (av === null || av === undefined) return 1;
            if (bv === null || bv === undefined) return -1;
            return (av > bv ? 1 : -1) * this._sortDir;
        });

        const head = columns.map(c => {
            const arrow = c.key === this._sortKey ? (this._sortDir === 1 ? ' ▲' : ' ▼') : '';
            return `<th data-key="${c.key}" style="cursor:pointer; user-select:none;">${c.label}${arrow}</th>`;
        }).join('');
        const body = sorted.map(c => `<tr>${columns.map(col => `<td>${col.render(c)}</td>`).join('')}</tr>`).join('');

        this._candidatesEl.innerHTML = `<table class="admin-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;

        this._candidatesEl.querySelectorAll('th[data-key]').forEach(th => {
            th.onclick = () => {
                const key = th.dataset.key;
                this._sortDir = (this._sortKey === key) ? this._sortDir * -1 : 1;
                this._sortKey = key;
                this._renderCandidates();
            };
        });
    }
}

document.addEventListener('DOMContentLoaded', () => new BettingScreener());
```

- [ ] **Step 2: Manual sanity check (no automated JS test suite in this repo)**

This project has no automated frontend test suite (`pytest` covers routes/services only, per `CLAUDE.md`). Verified as part of Task 7's manual browser check.

- [ ] **Step 3: Commit**

```bash
git add static/js/admin_betting.js
git commit -m "feat: add admin_betting.js — filter builder + results rendering"
```

---

### Task 7: Regenerate prediction data and manually verify in-browser

**Files:** none (verification only)

This is a UI-visible change, so per `CLAUDE.md`'s Frontend Testing section it must be manually verified in-browser before being considered done. It also needs real (not synthetic) `explanation` data in the local dev cache, since Task 1/2's fix only stops *future* clobbering — it doesn't retroactively repair 2026's already-thinned local data from this session.

- [ ] **Step 1: Regenerate 2026 (and current-year-adjacent) prediction data locally**

Run: `python scripts/backfill_schedule_predictions.py --seasons 2025 2026`

Expected: console output showing predictions written for both seasons, each with an `explanation` field (confirm by re-running the inspection check: `python -c "import json; d=json.load(open('.local_db/game_predictions_2026.json')); print(list(d['predictions'].values())[0].keys())"` should now include `explanation`, not just the 4 thin fields).

- [ ] **Step 2: Start the local dev server**

Run: `uvicorn main:app --port 8202 --env-file .env` (background)

- [ ] **Step 3: Verify the tooltip fix**

In a browser (using an admin-authenticated session — see this repo's established pattern of generating a local JWT via `services.session_service.create_token` and injecting it into `localStorage`, used earlier this session for the mock-draft and Elo-explorer verifications), navigate to the Schedule page for 2026, click a game's "?" explain button, and confirm the modal now shows real factor rows (Team Strength / Elo, Roster Quality, etc.) instead of all "—" placeholders.

- [ ] **Step 4: Verify the Betting tab**

Navigate to `/admin` → Betting tab. Confirm:
- The tab loads with a default backtest record and a non-empty candidates list for the current week (assuming 2026 has scheduled games with spread data).
- Clicking each of the four prebuilt angle buttons changes the filter inputs and reruns the screen, showing a plausible backtest record for each (e.g. "Home Dog" and "Away Favorite" should show *complementary* records — one's win count should roughly equal the other's loss count, since they're the same games graded from opposite sides).
- Manually entering a narrow custom filter (e.g. `elo_diff_min=100`) meaningfully shrinks the candidates list.
- Clicking a candidates-table column header sorts it (ascending, then descending on a second click).

- [ ] **Step 5: Stop the dev server**

Run: `Get-NetTCPConnection -LocalPort 8202 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }` (PowerShell)

- [ ] **Step 6: Run the full test suite one more time**

Run: `pytest tests/ -q`
Expected: all tests pass, no regressions.

- [ ] **Step 7: Commit** (only if Step 1's regenerated local data needs to be reflected anywhere tracked — it does not, `.local_db/` is gitignored; this step is a no-op unless manual verification surfaced a bug requiring a code fix, in which case that fix gets its own commit)
