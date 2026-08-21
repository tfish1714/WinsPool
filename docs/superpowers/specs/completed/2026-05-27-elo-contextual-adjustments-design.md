# Elo Contextual Adjustments + Log Loss Metric

**Date:** 2026-05-27  
**Status:** Approved  
**Scope:** `scripts/compute_elo.py`, `scripts/weekly_model_eval.py`, `services/constants.py`, `services/nn_prediction_service.py`, `services/nn_projection_engine.py`, `services/nn_feature_engine.py`, `services/prediction_service.py`, `scripts/backfill_schedule_predictions.py`, `scripts/generate_weekly_predictions.py`, `scripts/predict_season.py`

## Overview

Four improvements to the NFL prediction pipeline:

1. **Shared prediction constants** — centralize duplicated magic numbers (`0.02/0.98` clip bounds, `25.0` Elo→spread divisor, `7.5` spread↔prob scale) into `services/constants.py`
2. **Bye week Elo bonus** — teams coming off a bye get a temporary Elo boost for that game's win probability calculation
3. **Short rest Elo penalty** — teams with ≤4 days rest (Thursday Night Football) get a temporary Elo penalty
4. **Log loss metric** — added alongside Brier score in the weekly evaluation tracker

Items 2 and 3 feed into `home_exp` in `elo_computed.csv`, which is a feature used in model training. A full pipeline re-run (Elo recompute → model retrain → backfill) is required after implementation.

---

## Component 1 — `services/constants.py` shared prediction constants

Four values appear identically in multiple prediction files with no named home. Moving them to `services/constants.py` (the established home for cross-service config) eliminates silent divergence risk if a value ever needs tuning.

```python
PROB_CLIP_MIN = 0.02       # prevents model from predicting < 2% win probability
PROB_CLIP_MAX = 0.98       # prevents model from predicting > 98% win probability
ELO_TO_SPREAD = 25.0       # Elo point difference ÷ this = point spread equivalent
SPREAD_TO_PROB_SCALE = 7.5 # logistic scale: spread ÷ this → win probability
```

**Files that consume these constants and require updating:**

| Constant | Files |
|---|---|
| `PROB_CLIP_MIN` / `PROB_CLIP_MAX` | `nn_prediction_service.py`, `nn_projection_engine.py`, `backfill_schedule_predictions.py`, `weekly_model_eval.py` |
| `ELO_TO_SPREAD` | `nn_feature_engine.py`, `nn_projection_engine.py`, `prediction_service.py`, `generate_weekly_predictions.py`, `predict_season.py` |
| `SPREAD_TO_PROB_SCALE` | `nn_prediction_service.py`, `nn_projection_engine.py`, `backfill_schedule_predictions.py` |

---

## Component 3 — `scripts/compute_elo.py` constants block

`BYE_BONUS` and `SHORT_REST_PENALTY` live alongside `K`, `HFA`, and the other Elo parameters at the top of `compute_elo.py`. They are Elo computation parameters used only by this script — consistent with how `K`, `HFA`, `REGRESSION`, etc. are handled. They do not belong in `services/constants.py`, which is reserved for values shared across multiple services.

```python
BYE_BONUS = 8.0          # temporary Elo boost for team coming off bye (week_gap >= 2)
SHORT_REST_PENALTY = 3.0  # temporary Elo penalty for team with <= 4 days rest (TNF)
```

**Rationale for values:** Proportionally scaled from FiveThirtyEight's +25 bye bonus (calibrated for HFA=65) down to WinsPool's HFA=15 scale (~15/65 × 25 ≈ 6, rounded to 8 as a starting point). `SHORT_REST_PENALTY` set to 3 as a conservative starting point. Both are tunable after evaluating Brier/log loss impact via `weekly_model_eval.py`.

At a 50/50 matchup: `BYE_BONUS=8` → ~+1.1% win probability shift. `SHORT_REST_PENALTY=3` → ~-0.4% shift.

---

## Component 3 — `scripts/compute_elo.py`

### Why `rest` days alone can't detect byes

`games.csv` has `away_rest` / `home_rest` (calendar days since last game). These are unreliable for bye detection:
- A Thursday game followed by the next Sunday produces `rest=10` — no bye, just a short turnaround
- A Monday game → bye → Thursday produces `rest=10` — genuine bye, same rest count as above
- `rest=14` has 23 confirmed false positives (week_gap=1) in the historical data

True byes (week_gap ≥ 2) span rest values from 10 to 21 days depending on surrounding game days.

**Conclusion:** Use **week number gap** to detect byes; use calendar rest days only for short rest (TNF), where `rest ≤ 4` is unambiguous.

### Data loading

`games.csv` `away_rest` and `home_rest` columns are loaded for short rest detection only. Bye detection is derived from the schedule itself during the game loop.

Threshold definitions:
- **Bye**: `week_gap >= 2` — team's current week minus their last-played week in the same season
- **Short rest**: `rest <= 4` days (TNF / Thanksgiving)
- **Normal**: everything else — no adjustment

### `compute_elo()` loop change

Maintain a `last_week: dict[(team, season), int]` alongside the existing `elo` dict. Before processing each game, compute:

```python
home_week_gap = current_week - last_week.get((home, season), current_week - 1)
away_week_gap = current_week - last_week.get((away, season), current_week - 1)
```

After the game, update `last_week[(home, season)] = current_week` and same for away.

Teams with no prior game in the season default to `week_gap=1` (no bye adjustment for Week 1).

### `_update_game()` signature change

Replace any rest-day parameters with: `home_week_gap: int`, `away_week_gap: int`, `home_rest: int`, `away_rest: int`.

### Adjustment logic

Before computing `E`, calculate a temporary effective Elo for each team:

```python
def _rest_adj(week_gap: int, rest: int) -> float:
    if week_gap >= 2:
        return BYE_BONUS
    if rest <= 4:
        return -SHORT_REST_PENALTY
    return 0.0

home_adj = _rest_adj(home_week_gap, home_rest)
away_adj = _rest_adj(away_week_gap, away_rest)
E = _expected(home_elo + home_adj, away_elo + away_adj)
```

The adjusted `E` is used for:
- The stored `home_exp` output in `elo_computed.csv` (the ML feature)
- The Elo delta calculation: `delta = K × MOV × (actual − E)`

The stored `home_elo_post` / `away_elo_post` carry no memory of the adjustment — the adjustment is consumed by the game result and does not persist to future games.

### Edge cases

| Scenario | Result |
|---|---|
| Both teams on short rest (Thanksgiving) | Adjustments cancel, `E` unchanged — correct, both equally disadvantaged |
| Home has bye, away has short rest | Effects stack: net `+11` to home's effective advantage |
| Both teams have bye | Impossible — only one team can skip the same week |
| Week 1 (no prior game) | `week_gap` defaults to 1 — no adjustment |
| `home_rest` or `away_rest` is NaN | Treat as normal (0 short-rest adjustment); emit `logging.warning()` |

### Quarter-by-quarter updates

The quarter-level `E` used within `_update_game()` is already computed before the quarter loop. The rest adjustment applies to this same `E` — no change needed to quarter logic.

---

## Component 4 — `scripts/weekly_model_eval.py`

### CSV field addition

Add `log_loss` to `CSV_FIELDS` (after `brier_score`):

```python
"log_loss",   # lower is better; random guessing ~0.693
```

### Computation in `_evaluate_weeks()`

```python
from sklearn.metrics import log_loss as sklearn_log_loss

ll = float(sklearn_log_loss(
    week_data["home_win"].values,
    week_data["pred_home_wp"].values,
))
```

Probabilities are already clipped to `[0.02, 0.98]` upstream, so no log(0) edge cases.

**Reference values:**
- Random guessing: ~0.693
- Current model baseline: ~0.677 (from article benchmark context)
- Good model: < 0.65

---

## Pipeline re-run sequence

Run in order after implementation:

```bash
# 1. Recompute Elo with contextual adjustments
python scripts/compute_elo.py

# 2. Retrain all three models on updated features
python scripts/train_nn_model.py
python scripts/train_xgb_model.py
python scripts/train_lr_model.py

# 3. Backfill stored predictions — training window
python scripts/backfill_schedule_predictions.py --seasons 2020 2026 --force --firestore

# 4. Backfill older seasons for history tab (no model training dependency)
python scripts/backfill_schedule_predictions.py --seasons 2006 2019 --force --firestore

# 5. Evaluate before/after — compare brier_score and log_loss columns
python scripts/weekly_model_eval.py --season 2024 --week 1 18
python scripts/weekly_model_eval.py --season 2025 --week 1 18

# 6. Refresh local pickle cache
python scripts/refresh_local_pkls.py
```

### Acceptance criteria

Run `weekly_model_eval.py` over 2024 and 2025 with both old and new `elo_computed.csv`. The change is considered beneficial if aggregate Brier score and log loss are equal or lower. If metrics regress, tune `BYE_BONUS` and `SHORT_REST_PENALTY` in `services/constants.py` before committing.

---

## What this does NOT include

- Automated retraining / drift detection (separate spec)
- Travel distance or altitude Elo adjustments
- Changes to HFA or K-factor values
- Any changes to the Silver → Gold pipeline (Firestore write path, `data_service.py`, etc.)
