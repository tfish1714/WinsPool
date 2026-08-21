# Elo Contextual Adjustments + Log Loss Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add bye-week and short-rest Elo adjustments to `compute_elo.py` and log loss tracking to `weekly_model_eval.py`, then re-run the full prediction pipeline.

**Architecture:** A new `_rest_adj()` pure function computes a temporary per-team Elo offset based on week-gap (bye detection) and calendar rest (short rest). This offset shifts the pre-game expected win probability `E` used for both the Elo delta and the `home_exp` feature stored in `elo_computed.csv`. Stored Elo ratings carry no memory of the adjustment. Log loss is added as a one-liner alongside the existing Brier score in the weekly eval tracker.

**Tech Stack:** Python, pandas, scikit-learn (`log_loss`), pytest

---

## Files

| Action | Path | Change |
|---|---|---|
| Modify | `services/constants.py` | Add `PROB_CLIP_MIN`, `PROB_CLIP_MAX`, `ELO_TO_SPREAD`, `SPREAD_TO_PROB_SCALE` |
| Modify | `services/nn_prediction_service.py` | Replace `0.02`, `0.98`, `7.5` with constants |
| Modify | `services/nn_projection_engine.py` | Replace `0.02`, `0.98`, `25.0`, `7.5` with constants |
| Modify | `services/nn_feature_engine.py` | Replace `25.0` with `ELO_TO_SPREAD` |
| Modify | `services/prediction_service.py` | Replace `25.0` (×3) with `ELO_TO_SPREAD` |
| Modify | `scripts/backfill_schedule_predictions.py` | Replace `0.02`, `0.98`, `7.5` (×2) with constants |
| Modify | `scripts/generate_weekly_predictions.py` | Replace `25.0` with `ELO_TO_SPREAD` |
| Modify | `scripts/predict_season.py` | Replace `25.0` with `ELO_TO_SPREAD` |
| Modify | `scripts/compute_elo.py` | Add `BYE_BONUS`, `SHORT_REST_PENALTY`, `_rest_adj()`, update `_update_game()` + loop |
| Modify | `scripts/weekly_model_eval.py` | Add `log_loss` field + computation; replace `0.02`, `0.98` with constants |
| Create | `tests/test_compute_elo_adjustments.py` | Unit + integration tests for new Elo logic |

---

## Task 1: Centralize shared prediction constants

**Files:**
- Modify: `services/constants.py`
- Modify: `services/nn_prediction_service.py`
- Modify: `services/nn_projection_engine.py`
- Modify: `services/nn_feature_engine.py`
- Modify: `services/prediction_service.py`
- Modify: `scripts/backfill_schedule_predictions.py`
- Modify: `scripts/generate_weekly_predictions.py`
- Modify: `scripts/predict_season.py`

- [x] **Step 1: Add constants to `services/constants.py`**

Append after the ensemble weights block:

```python
# Probability clipping — prevents model from predicting < 2% or > 98% win probability.
PROB_CLIP_MIN = 0.02
PROB_CLIP_MAX = 0.98

# Elo/spread conversion — used identically across prediction services and scripts.
ELO_TO_SPREAD = 25.0        # Elo point difference ÷ this = point spread equivalent
SPREAD_TO_PROB_SCALE = 7.5  # logistic scale: spread ÷ this → win probability
```

- [x] **Step 2: Update `services/nn_prediction_service.py`**

Add to imports at top of file:
```python
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX, SPREAD_TO_PROB_SCALE
```

Replace line 90:
```python
# before
blended = np.clip(NN_WEIGHT * nn_p + XGB_WEIGHT * xgb_p + LR_WEIGHT * lr_p, 0.02, 0.98)
# after
blended = np.clip(NN_WEIGHT * nn_p + XGB_WEIGHT * xgb_p + LR_WEIGHT * lr_p, PROB_CLIP_MIN, PROB_CLIP_MAX)
```

Replace line 101:
```python
# before
hp_clip = np.clip(hp, 0.02, 0.98)
# after
hp_clip = np.clip(hp, PROB_CLIP_MIN, PROB_CLIP_MAX)
```

Replace line 104:
```python
# before
model_spread = round(7.5 * float(np.log(hp_clip / (1.0 - hp_clip))), 1)
# after
model_spread = round(SPREAD_TO_PROB_SCALE * float(np.log(hp_clip / (1.0 - hp_clip))), 1)
```

Replace line 133:
```python
# before
vegas_home_prob = round(1 / (1 + np.exp(-vegas_spread / 7.5)), 4)
# after
vegas_home_prob = round(1 / (1 + np.exp(-vegas_spread / SPREAD_TO_PROB_SCALE)), 4)
```

- [x] **Step 3: Update `services/nn_projection_engine.py`**

Add to imports:
```python
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE
```

Replace line 154:
```python
# before
features[col] = abs(features.get("elo_diff", 0.0)) / 25.0
# after
features[col] = abs(features.get("elo_diff", 0.0)) / ELO_TO_SPREAD
```

Replace lines 220–222:
```python
# before
NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
0.02, 0.98,
# after
NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
PROB_CLIP_MIN, PROB_CLIP_MAX,
```

Replace lines 389–390:
```python
# before
hp_clip = min(0.98, max(0.02, home_prob))
implied = 7.5 * np.log(hp_clip / (1.0 - hp_clip))
# after
hp_clip = min(PROB_CLIP_MAX, max(PROB_CLIP_MIN, home_prob))
implied = SPREAD_TO_PROB_SCALE * np.log(hp_clip / (1.0 - hp_clip))
```

- [x] **Step 4: Update `services/nn_feature_engine.py`**

Add to imports:
```python
from services.constants import ELO_TO_SPREAD
```

Replace line 1043:
```python
# before
sched["elo_confidence"] = np.abs(sched["elo_diff"] / 25.0)
# after
sched["elo_confidence"] = np.abs(sched["elo_diff"] / ELO_TO_SPREAD)
```

- [x] **Step 5: Update `services/prediction_service.py`**

Add `ELO_TO_SPREAD` to its existing constants import line.

Replace line 595:
```python
# before
"predicted_spread": round(elo_diff / 25.0, 1),
# after
"predicted_spread": round(elo_diff / ELO_TO_SPREAD, 1),
```

Replace line 807:
```python
# before
projected_wins = 8.5 + (elo_above_mean / 25.0) * 0.5
# after
projected_wins = 8.5 + (elo_above_mean / ELO_TO_SPREAD) * 0.5
```

Replace line 892:
```python
# before
projected = 8.5 + (elo_above_mean / 25.0) * 0.5
# after
projected = 8.5 + (elo_above_mean / ELO_TO_SPREAD) * 0.5
```

- [x] **Step 6: Update `scripts/backfill_schedule_predictions.py`**

Add to imports:
```python
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX, SPREAD_TO_PROB_SCALE
```

Replace line 100:
```python
# before
hp_clip = min(0.98, max(0.02, hp))
# after
hp_clip = min(PROB_CLIP_MAX, max(PROB_CLIP_MIN, hp))
```

Replace line 101:
```python
# before
model_spread = round(7.5 * float(np.log(hp_clip / (1.0 - hp_clip))), 1)
# after
model_spread = round(SPREAD_TO_PROB_SCALE * float(np.log(hp_clip / (1.0 - hp_clip))), 1)
```

Replace line 111:
```python
# before
vhp = round(1 / (1 + np.exp(-sl_val / 7.5)), 4) if sl_val is not None else None
# after
vhp = round(1 / (1 + np.exp(-sl_val / SPREAD_TO_PROB_SCALE)), 4) if sl_val is not None else None
```

- [x] **Step 7: Update `scripts/generate_weekly_predictions.py`**

Add to imports:
```python
from services.constants import ELO_TO_SPREAD
```

Replace line 96:
```python
# before
features[col] = abs((elo_diff / 25.0) - hp.get("spread_line", 0))
# after
features[col] = abs((elo_diff / ELO_TO_SPREAD) - hp.get("spread_line", 0))
```

- [x] **Step 8: Update `scripts/predict_season.py`**

Add to imports:
```python
from services.constants import ELO_TO_SPREAD
```

Replace line 141:
```python
# before
features[col] = abs((elo_diff / 25.0) - hp.get("spread_line", 0))
# after
features[col] = abs((elo_diff / ELO_TO_SPREAD) - hp.get("spread_line", 0))
```

- [x] **Step 9: Run existing tests to confirm no regressions**

```bash
pytest tests/ -v
```

Expected: full suite passes. These are pure renames — no behaviour change.

- [x] **Step 10: Commit**

```bash
git add services/constants.py services/nn_prediction_service.py services/nn_projection_engine.py services/nn_feature_engine.py services/prediction_service.py scripts/backfill_schedule_predictions.py scripts/generate_weekly_predictions.py scripts/predict_season.py
git commit -m "refactor: centralize PROB_CLIP, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE in constants.py"
```

---

## Task 2: Write failing unit tests for `_rest_adj()` and `_update_game()`

**Files:**
- Create: `tests/test_compute_elo_adjustments.py`

- [x] **Step 1: Create test file**

```python
"""tests/test_compute_elo_adjustments.py

Unit and integration tests for the bye-week / short-rest Elo adjustments
added to scripts/compute_elo.py.
"""
import sys
import pathlib
import pytest
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import scripts.compute_elo as elo_module
from scripts.compute_elo import _rest_adj, _update_game, _expected, BYE_BONUS, SHORT_REST_PENALTY


# ---------------------------------------------------------------------------
# _rest_adj
# ---------------------------------------------------------------------------

class TestRestAdj:

    def test_bye_week_returns_bonus(self):
        """week_gap >= 2 (skipped a schedule week) → BYE_BONUS."""
        assert _rest_adj(week_gap=2, rest=14) == BYE_BONUS
        assert _rest_adj(week_gap=3, rest=21) == BYE_BONUS

    def test_bye_with_low_rest_still_returns_bonus(self):
        """Monday game → bye → Thursday is week_gap=2 but rest≈10; bye wins."""
        assert _rest_adj(week_gap=2, rest=10) == BYE_BONUS

    def test_short_rest_returns_penalty(self):
        """rest <= 4, no bye → -SHORT_REST_PENALTY."""
        assert _rest_adj(week_gap=1, rest=4) == -SHORT_REST_PENALTY
        assert _rest_adj(week_gap=1, rest=3) == -SHORT_REST_PENALTY

    def test_normal_rest_returns_zero(self):
        """Standard 7-day rest with no bye → no adjustment."""
        assert _rest_adj(week_gap=1, rest=7) == 0.0

    def test_thursday_after_monday_no_bye(self):
        """Thursday game after Monday Night Football is rest=10, week_gap=1 → no adjustment."""
        assert _rest_adj(week_gap=1, rest=10) == 0.0


# ---------------------------------------------------------------------------
# _update_game with rest params
# ---------------------------------------------------------------------------

class TestUpdateGameWithRest:

    def _baseline_E(self):
        """Expected win prob for two even teams with no rest adjustment."""
        _, _, E = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=7, away_rest=7,
        )
        return E

    def test_home_bye_increases_expected(self):
        """Home team coming off bye → higher E than no adjustment."""
        E_base = self._baseline_E()
        _, _, E_bye = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=2, away_week_gap=1,
            home_rest=14, away_rest=7,
        )
        assert E_bye > E_base

    def test_away_bye_decreases_expected(self):
        """Away team coming off bye → lower E (away is stronger) for home team."""
        E_base = self._baseline_E()
        _, _, E_away_bye = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=2,
            home_rest=7, away_rest=14,
        )
        assert E_away_bye < E_base

    def test_both_short_rest_cancels(self):
        """Both teams on 4-day rest (Thanksgiving) → E unchanged."""
        E_base = self._baseline_E()
        _, _, E_both = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=4, away_rest=4,
        )
        assert abs(E_both - E_base) < 0.001

    def test_home_short_rest_decreases_expected(self):
        """Home team on 4-day rest → lower E."""
        E_base = self._baseline_E()
        _, _, E_short = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=4, away_rest=7,
        )
        assert E_short < E_base

    def test_bye_win_earns_less_elo(self):
        """Post-bye home win earns fewer Elo points (win was more expected)."""
        home_normal, _, _ = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=7, away_rest=7,
        )
        home_bye, _, _ = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=2, away_week_gap=1,
            home_rest=14, away_rest=7,
        )
        assert home_bye < home_normal

    def test_defaults_match_baseline(self):
        """Calling _update_game without rest kwargs should behave as no adjustment."""
        _, _, E_default = _update_game(1500.0, 1500.0, 28, 21, None)
        E_base = self._baseline_E()
        assert abs(E_default - E_base) < 0.001


# ---------------------------------------------------------------------------
# Integration: compute_elo() week-gap detection
# ---------------------------------------------------------------------------

class TestComputeEloWeekGap:

    def _make_games_csv(self, file_path, rows):
        """Write a minimal games CSV at the given file path."""
        import pathlib
        p = pathlib.Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        required_cols = [
            "game_id", "season", "game_type", "week",
            "home_team", "away_team", "home_score", "away_score",
            "home_rest", "away_rest",
        ]
        df = pd.DataFrame(rows)
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
        df.to_csv(p, index=False)
        return p

    def test_bye_week_changes_home_exp(self, tmp_path, monkeypatch):
        """
        Two identical 3-game schedules: one where KC has a bye before week 3,
        one where KC plays every week. The bye-week game should have a higher
        home_exp for KC (home team) than the non-bye version.
        """
        base_rows = [
            # Week 1: KC (home) beats BUF — same in both schedules
            dict(game_id="w1", season=2024, game_type="REG", week=1,
                 home_team="KC", away_team="BUF",
                 home_score=28, away_score=21, home_rest=7, away_rest=7),
        ]

        # Schedule A: KC plays week 2 then week 3 (no bye, week_gap=1 at week 3)
        rows_no_bye = base_rows + [
            dict(game_id="w2a", season=2024, game_type="REG", week=2,
                 home_team="BUF", away_team="KC",
                 home_score=17, away_score=24, home_rest=7, away_rest=7),
            dict(game_id="w3a", season=2024, game_type="REG", week=3,
                 home_team="KC", away_team="BUF",
                 home_score=21, away_score=17, home_rest=7, away_rest=7),
        ]

        # Schedule B: KC skips week 2 (bye), then plays week 3 (week_gap=2)
        rows_with_bye = base_rows + [
            dict(game_id="w2b", season=2024, game_type="REG", week=2,
                 home_team="BUF", away_team="DET",   # KC not in this game
                 home_score=17, away_score=14, home_rest=7, away_rest=7),
            dict(game_id="w3b", season=2024, game_type="REG", week=3,
                 home_team="KC", away_team="BUF",
                 home_score=21, away_score=17, home_rest=14, away_rest=7),
        ]

        csv_no_bye  = self._make_games_csv(tmp_path / "no_bye_games.csv",  rows_no_bye)
        csv_with_bye = self._make_games_csv(tmp_path / "with_bye_games.csv", rows_with_bye)
        # Point QUARTER_CSV at a non-existent path so quarter updates are skipped
        monkeypatch.setattr(elo_module, "QUARTER_CSV", tmp_path / "quarter_scores.csv")

        monkeypatch.setattr(elo_module, "GAMES_CSV", csv_no_bye)
        df_no_bye, _ = elo_module.compute_elo(min_season=2024, max_season=2024)
        E_no_bye = df_no_bye[df_no_bye["week"] == 3]["home_exp"].iloc[0]

        monkeypatch.setattr(elo_module, "GAMES_CSV", csv_with_bye)
        df_with_bye, _ = elo_module.compute_elo(min_season=2024, max_season=2024)
        E_with_bye = df_with_bye[df_with_bye["week"] == 3]["home_exp"].iloc[0]

        assert E_with_bye > E_no_bye, (
            f"Bye-week home_exp ({E_with_bye:.4f}) should exceed "
            f"no-bye home_exp ({E_no_bye:.4f})"
        )
```

- [x] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/test_compute_elo_adjustments.py -v
```

Expected: `ImportError` or `TypeError` on `_rest_adj` (not yet defined) and `_update_game` (wrong signature).

---

## Task 3: Implement `_rest_adj()` and update `_update_game()`

**Files:**
- Modify: `scripts/compute_elo.py`

- [x] **Step 1: Add `BYE_BONUS` and `SHORT_REST_PENALTY` to the Elo parameters block**

In `scripts/compute_elo.py`, locate the existing Elo parameters block (around line 38). Append the two new constants immediately after `REGRESSION`:

```python
REGRESSION = 1.0 / 3.0  # fraction of distance to 1500 pulled back each off-season
BYE_BONUS = 8.0          # temporary Elo boost for team coming off bye (week_gap >= 2)
SHORT_REST_PENALTY = 3.0  # temporary Elo penalty for team with <= 4 days rest (TNF)
```

- [x] **Step 2: Add `_rest_adj()` after `_quarter_actual()`**

Insert the following function after the `_quarter_actual()` function (around line 78):

```python
def _rest_adj(week_gap: int, rest: int) -> float:
    """Temporary Elo offset for bye week or short rest.

    Bye detection uses week number gap (not calendar days) because calendar
    rest varies with surrounding game days — a Monday game before a bye can
    produce as few as 10 rest days, indistinguishable from a Thursday→Sunday
    turnaround.

    Not stored in ratings: the adjustment affects E for this game only.
    """
    if week_gap >= 2:
        return BYE_BONUS
    if rest <= 4:
        return -SHORT_REST_PENALTY
    return 0.0
```

- [x] **Step 3: Update `_update_game()` signature and E computation**

Replace the existing `_update_game` signature and `E = _expected(...)` line. The full updated function header and first few lines become:

```python
def _update_game(
    home_elo: float,
    away_elo: float,
    home_score: int,
    away_score: int,
    quarters: dict | None,
    home_week_gap: int = 1,
    away_week_gap: int = 1,
    home_rest: int = 7,
    away_rest: int = 7,
) -> tuple[float, float, float]:
    """
    Apply all Elo updates for one game.

    quarters: dict with keys home_q1..q4, home_ot, away_ot
              or None if quarter scores unavailable.
    home_week_gap / away_week_gap: schedule weeks since team's last game.
                                   >= 2 means the team had a bye.
    home_rest / away_rest: calendar days since last game (for short-rest detection).

    Returns (new_home_elo, new_away_elo, pre_game_expected_home).
    """
    home_adj = _rest_adj(home_week_gap, home_rest)
    away_adj = _rest_adj(away_week_gap, away_rest)
    E = _expected(home_elo + home_adj, away_elo + away_adj)
```

The remainder of the function body (quarter loop, final delta) is unchanged.

- [x] **Step 4: Run unit tests — confirm they pass**

```bash
pytest tests/test_compute_elo_adjustments.py::TestRestAdj tests/test_compute_elo_adjustments.py::TestUpdateGameWithRest -v
```

Expected: all 9 tests pass. The integration test (`TestComputeEloWeekGap`) will still fail — that's expected.

- [x] **Step 5: Confirm existing tests still pass**

```bash
pytest tests/ -v --ignore=tests/test_compute_elo_adjustments.py
```

Expected: same pass/fail as before this change.

- [x] **Step 6: Commit**

```bash
git add scripts/compute_elo.py tests/test_compute_elo_adjustments.py
git commit -m "feat: add _rest_adj() and update _update_game() with bye/short-rest Elo adjustments"
```

---

## Task 4: Update `compute_elo()` loop to track week gaps

**Files:**
- Modify: `scripts/compute_elo.py`

- [x] **Step 1: Add `last_week` tracking and rest loading to the main loop**

In `compute_elo()`, locate the line `elo: dict[str, float] = {}` (currently around line 196). Add `last_week` immediately after it:

```python
elo: dict[str, float] = {}
last_week: dict[tuple, int] = {}  # (team, season) → last week number played
current_season = None
rows = []
```

- [x] **Step 2: Add week-gap computation inside the loop**

Locate the block inside the `for _, game in all_seasons_games.iterrows():` loop where `home_pre` and `away_pre` are set (currently around line 219). Add the week-gap and rest extraction immediately after `home = str(game["home_team"])` / `away = str(game["away_team"])`:

```python
home = str(game["home_team"])
away = str(game["away_team"])

# Week-gap: how many schedule weeks since this team last played?
# Defaults to 1 (no bye) for Week 1 when no prior entry exists.
home_week_gap = week - last_week.get((home, season), week - 1)
away_week_gap = week - last_week.get((away, season), week - 1)

# Calendar rest days — already in games.csv, used only for short-rest detection.
home_rest = int(game["home_rest"]) if pd.notna(game["home_rest"]) else 7
away_rest = int(game["away_rest"]) if pd.notna(game["away_rest"]) else 7
```

- [x] **Step 3: Pass new params to `_update_game()`**

Replace the existing `_update_game(...)` call (currently around line 227):

```python
new_home, new_away, home_exp = _update_game(
    home_pre, away_pre,
    game["home_score"], game["away_score"],
    quarters,
    home_week_gap=home_week_gap,
    away_week_gap=away_week_gap,
    home_rest=home_rest,
    away_rest=away_rest,
)
```

- [x] **Step 4: Update `last_week` after each game**

Immediately after `elo[away] = new_away` (currently around line 233), add:

```python
last_week[(home, season)] = week
last_week[(away, season)] = week
```

- [x] **Step 5: Run all tests — confirm everything passes**

```bash
pytest tests/test_compute_elo_adjustments.py -v
```

Expected: all tests pass including `TestComputeEloWeekGap::test_bye_week_changes_home_exp`.

```bash
pytest tests/ -v
```

Expected: full suite passes.

- [x] **Step 6: Commit**

```bash
git add scripts/compute_elo.py
git commit -m "feat: track week-gap per team in compute_elo loop for bye detection"
```

---

## Task 5: Add log loss to `weekly_model_eval.py`

**Files:**
- Modify: `scripts/weekly_model_eval.py`
- Modify: `tests/test_compute_elo_adjustments.py` (add log loss tests)

- [x] **Step 0: Replace clip literals with constants (from Task 1)**

Add to imports in `scripts/weekly_model_eval.py`:
```python
from services.constants import NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT, PROB_CLIP_MIN, PROB_CLIP_MAX
```

Replace lines 101–103:
```python
# before
NN_WEIGHT * nn_probs + XGB_WEIGHT * xgb_probs + LR_WEIGHT * lr_probs,
0.02, 0.98,
# after
NN_WEIGHT * nn_probs + XGB_WEIGHT * xgb_probs + LR_WEIGHT * lr_probs,
PROB_CLIP_MIN, PROB_CLIP_MAX,
```

- [x] **Step 1: Write a failing test for the log loss field**

Append to `tests/test_compute_elo_adjustments.py`:

```python
# ---------------------------------------------------------------------------
# weekly_model_eval: log loss field
# ---------------------------------------------------------------------------

class TestLogLossField:

    def test_log_loss_in_csv_fields(self):
        """CSV_FIELDS must contain 'log_loss' for it to be written to the report."""
        from scripts.weekly_model_eval import CSV_FIELDS
        assert "log_loss" in CSV_FIELDS

    def test_log_loss_computation(self):
        """Validate sklearn log_loss behaves as expected at known values."""
        from sklearn.metrics import log_loss
        import numpy as np

        # Confident correct predictions → very low log loss
        actuals = np.array([1.0, 0.0, 1.0, 0.0])
        preds   = np.array([0.95, 0.05, 0.95, 0.05])
        ll = float(log_loss(actuals, preds))
        assert ll < 0.1

        # Random guessing (50/50) → ~0.693
        preds_random = np.array([0.5, 0.5, 0.5, 0.5])
        ll_random = float(log_loss(actuals, preds_random))
        assert abs(ll_random - 0.693) < 0.01

    def test_log_loss_positioned_after_brier(self):
        """log_loss should appear immediately after brier_score in CSV_FIELDS."""
        from scripts.weekly_model_eval import CSV_FIELDS
        brier_idx = CSV_FIELDS.index("brier_score")
        ll_idx = CSV_FIELDS.index("log_loss")
        assert ll_idx == brier_idx + 1
```

- [x] **Step 2: Run test — confirm it fails**

```bash
pytest tests/test_compute_elo_adjustments.py::TestLogLossField -v
```

Expected: `AssertionError` — `log_loss` not yet in `CSV_FIELDS`.

- [x] **Step 3: Add `log_loss` to `CSV_FIELDS`**

In `scripts/weekly_model_eval.py`, locate `CSV_FIELDS` (around line 42). Insert `"log_loss"` after `"brier_score"`:

```python
CSV_FIELDS = [
    "evaluated_at",
    "model_version",
    "season",
    "week",
    "games",
    "correct",
    "accuracy_pct",
    "avg_prob_correct",
    "avg_prob_wrong",
    "brier_score",
    "log_loss",        # ← add this line
    "season_r2_ytd",
    "season_mae_ytd",
]
```

- [x] **Step 4: Add the log loss import and computation in `_evaluate_weeks()`**

At the top of `scripts/weekly_model_eval.py`, add to the existing imports:

```python
from sklearn.metrics import log_loss as sklearn_log_loss
```

In `_evaluate_weeks()`, locate the Brier score computation (currently around line 138):

```python
brier = float(np.mean((week_data["pred_home_wp"].values - week_data["home_win"].values) ** 2))
```

Add log loss immediately after it:

```python
# Log loss — exclude ties (home_win == 0.5); probabilities already clipped [0.02, 0.98]
non_tie = week_data[week_data["home_win"] != 0.5]
ll = float(sklearn_log_loss(
    non_tie["home_win"].values,
    non_tie["pred_home_wp"].values,
)) if len(non_tie) > 0 else None
```

- [x] **Step 5: Add `log_loss` to the `rows.append(...)` dict**

In `_evaluate_weeks()`, find the `rows.append({...})` block and add:

```python
"brier_score": round(brier, 4),
"log_loss": round(ll, 4) if ll is not None else None,
```

- [x] **Step 6: Update the print statement to include log loss**

Replace the existing print in the week loop:

```python
print(
    f"  Week {week:>2} | {n_correct}/{n_games} correct "
    f"({accuracy*100:.1f}%) | Brier: {brier:.4f} | "
    f"LogLoss: {ll:.4f} | "
    f"Season R2 YTD: {season_r2 if season_r2 is not None else 'N/A'}"
)
```

- [x] **Step 7: Run tests — confirm they pass**

```bash
pytest tests/test_compute_elo_adjustments.py::TestLogLossField -v
```

Expected: all 3 tests pass.

- [x] **Step 8: Commit**

```bash
git add scripts/weekly_model_eval.py tests/test_compute_elo_adjustments.py
git commit -m "feat: add log_loss metric to weekly model evaluation tracker"
```

---

## Task 6: Pipeline re-run

**Prerequisites:** Tasks 1–5 complete. Run these commands in order.

- [x] **Step 1: Recompute Elo with contextual adjustments**

```bash
python scripts/compute_elo.py
```

Expected output includes lines like `Computed XXXX rows` and a top-10 team ranking. Verify no errors.

- [x] **Step 2: Retrain all three models on updated features**

```bash
python scripts/train_nn_model.py
python scripts/train_xgb_model.py
python scripts/train_lr_model.py
```

Each script auto-increments the version in its registry. Note the new version numbers.

- [x] **Step 3: Backfill predictions — training window**

```bash
python scripts/backfill_schedule_predictions.py --seasons 2020 2026 --force --firestore
```

- [x] **Step 4: Backfill predictions — history tab**

```bash
python scripts/backfill_schedule_predictions.py --seasons 2006 2019 --force --firestore
```

- [x] **Step 5: Evaluate 2024 season**

```bash
python scripts/weekly_model_eval.py --season 2024 --week 1 18
```

Record the aggregate Brier and log loss from the output. Compare against prior entries in `reports/nn_weekly_accuracy.csv` for the same season.

- [x] **Step 6: Evaluate 2025 season**

```bash
python scripts/weekly_model_eval.py --season 2025 --week 1 18
```

Same — compare Brier and log loss before/after. If aggregate Brier or log loss worsened across both seasons, revisit `BYE_BONUS` and `SHORT_REST_PENALTY` in `services/constants.py`.

- [x] **Step 7: Refresh local pickle cache**

```bash
python scripts/refresh_local_pkls.py
```

- [x] **Step 8: Final commit**

```bash
git add reports/nn_weekly_accuracy.csv
git commit -m "chore: run full pipeline after Elo contextual adjustments + model retrain"
```
