# Code Quality Quick Wins — Issues #27, #35, #37

**Date:** 2026-05-28
**Status:** Approved
**Closes:** GitHub issues #27, #35, #37
**Scope:** `services/constants.py`, `services/analysis_service.py`, `services/cache_service.py`, `services/db_service.py`, `services/nn_feature_engine.py`, `routes/draft_routes.py`, `README.md`

---

## Overview

Three mechanical improvements with no behavior changes:

1. **Issue #35** — Move hardcoded tiebreaker sort columns and round pick-ranges to named constants
2. **Issue #37** — Document undocumented env vars and explain ML constant values
3. **Issue #27** — Add block comments to complex algorithms

---

## Component 1 — Named Constants (Issue #35)

### `services/constants.py`

Add two new constants:

```python
# Tiebreaker sort cascade for final standings.
# Applied in order: first rule wins on ties, next rule breaks ties within that, etc.
# Source: league rules established at founding; see docs/scoring.md.
TIEBREAKER_RULES = [
    {"col": "TotalWins",         "ascending": False, "label": "Total wins"},
    {"col": "MinWins",           "ascending": False, "label": "Worst-team wins"},
    {"col": "MaxWins",           "ascending": False, "label": "Best-team wins"},
    {"col": "TotalPointDiff",    "ascending": True,  "label": "Total point differential"},
    {"col": "H2HRecord",         "ascending": False, "label": "Head-to-head record"},
    {"col": "ProjectedWins",     "ascending": False, "label": "Preseason projected wins"},
    {"col": "playerName",        "ascending": True,  "label": "Name (final tiebreak)"},
]

# Pick number boundaries for each draft round (inclusive on both ends).
# 10 players × 3 rounds = 30 total picks. Update if pool size ever changes.
DRAFT_ROUNDS = {
    1: (1, 10),
    2: (11, 20),
    3: (21, 30),
}
```

### `services/analysis_service.py` — `apply_tiebreakers()`

Replace the hardcoded `sort_values(by=[...], ascending=[...])` call with a loop over `TIEBREAKER_RULES`:

```python
from services.constants import TIEBREAKER_RULES

cols = [r["col"] for r in TIEBREAKER_RULES]
asc  = [r["ascending"] for r in TIEBREAKER_RULES]
return reshaped_df.sort_values(by=cols, ascending=asc).reset_index(drop=True)
```

### `routes/draft_routes.py` — round detection

Replace the hardcoded `if pick <= 10 / elif pick <= 20` logic with:

```python
from services.constants import DRAFT_ROUNDS

def _pick_to_round(pick: int) -> int:
    for rnd, (lo, hi) in DRAFT_ROUNDS.items():
        if lo <= pick <= hi:
            return rnd
    return len(DRAFT_ROUNDS)
```

---

## Component 2 — Env Var + Constant Documentation (Issue #37)

### `README.md`

Add `DEBUG_PAGE_LOAD` to the environment variable table:

```
| `DEBUG_PAGE_LOAD` | `False` | Log page-load timing to console when `True`. Dev-only. |
```

### `services/cache_service.py`

Add inline comment to `_CACHE_TTL_SECONDS`:

```python
_CACHE_TTL_SECONDS = 300  # 5-minute TTL; balances freshness vs. Firestore read cost
```

### `services/constants.py`

Add inline comments to existing constants:

```python
# Sentinel win-total used for undrafted/unowned teams in standings calculations.
# Chosen to sort below any realistic win total (max 17 wins in a season).
UNDRAFTED_SENTINEL = -1000

# Ensemble blend weights — tuned empirically; see reports/nn_weekly_accuracy.csv.
# NN captures non-linear interactions; LR provides stability; XGB fills the gap.
NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35
```

---

## Component 3 — Algorithm Block Comments (Issue #27)

### `services/analysis_service.py`

Add block comments immediately before:

**`reshape_wins_pool_standings()`** — explain the long-to-wide pivot: each player has 3 drafted teams, so the function pivots `(player, team, wins, ptDiff, record)` rows into a single row per player with `wins1/wins2/wins3`, `ptDiff1/2/3`, `global_record1/2/3` columns ordered by draft pick number.

**`apply_tiebreakers()`** — reference `TIEBREAKER_RULES` and explain that ascending=True for point differential because lower (more negative) is worse, so we sort ascending to rank worse differentials lower.

**`get_enriched_schedule()`** — describe the 5-join merge sequence: games → draft_results (ownership) → players (names) → standings (season record) → predictions (ML win prob). Note the empty-string suffixes on the first merge to avoid column name collisions.

### `services/db_service.py`

Add block comment before the bcrypt migration block (around line 37–58): explain that legacy passwords are 64-character hex strings (SHA-256), bcrypt hashes start with `$2b$`, and the migration upgrades transparently on successful login.

### `services/nn_feature_engine.py`

Add block comment before `FEATURE_COLUMNS` explaining the three groupings: schedule-context features (Elo, rest, HFA), recent-form features (rolling stats, momentum), and season-context features (win rate, point differential). Note that `spread_line` was intentionally removed to prevent data leakage from Vegas into model training.

### `services/cache_service.py`

Add block comment before the prediction key format section (lines 154–176) explaining the key schema: `{season}_{week}_{home}_{away}` — all four components needed to uniquely identify a game since teams can play each other multiple times across weeks in edge cases.

---

## What This Does NOT Include

- Making tiebreaker rules or round boundaries configurable at runtime (Firestore)
- Changes to algorithm behavior
- New tests (documentation only)
