# Code Quality Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Mechanical code quality improvements — named constants, documentation, and block comments — with zero behavior changes.

**Architecture:** Three independent tasks, each touching 1-3 files. No new behavior, no new endpoints, no schema changes. Each task produces a clean commit; existing tests must pass throughout.

**Tech Stack:** Python, FastAPI, pandas; no new dependencies.

---

### Task 1: Named Constants — Issue #35

**Files:**
- Modify: `services/constants.py`
- Modify: `services/analysis_service.py:405-408`
- Modify: `routes/draft_routes.py:325-327`

- [x] **Step 1: Add DRAFT_ROUNDS and TIEBREAKER_SORT_COLS to constants.py**

Append to `services/constants.py` (after the existing content):

```python
# Pick boundaries for each draft round (inclusive both ends).
# 10 players × 3 rounds = 30 total picks. Update if pool size ever changes.
DRAFT_ROUNDS = {
    1: (1, 10),
    2: (11, 20),
    3: (21, 30),
}

# Ordered sort columns for apply_tiebreakers().
# TotalWins is primary; the six derived columns break ties in priority order.
# All columns sort descending — more wins / better differential = higher rank.
TIEBREAKER_SORT_COLS = [
    "TotalWins",
    "Tiebreaker1_WorstTeamWins",
    "Tiebreaker2_2ndWorstTeamWins",
    "Tiebreaker3_BestTeamWins",
    "Tiebreaker4_WorstTeamPtDiff",
    "Tiebreaker5_2ndWorstTeamPtDiff",
    "Tiebreaker6_BestTeamPtDiff",
]
```

- [x] **Step 2: Update apply_tiebreakers() to use TIEBREAKER_SORT_COLS**

In `services/analysis_service.py`, add the import at the top of the file (with the other `from services.constants import ...` statements):

```python
from services.constants import TIEBREAKER_SORT_COLS
```

Then replace the `sort_values` call at lines 405-408:

```python
# Before:
    sorted_df = reshaped_df.sort_values(
        ['TotalWins','Tiebreaker1_WorstTeamWins', 'Tiebreaker2_2ndWorstTeamWins', 'Tiebreaker3_BestTeamWins',
         'Tiebreaker4_WorstTeamPtDiff', 'Tiebreaker5_2ndWorstTeamPtDiff', 'Tiebreaker6_BestTeamPtDiff'],
        ascending=[False,False, False, False, False, False, False]
    )

# After:
    sorted_df = reshaped_df.sort_values(
        TIEBREAKER_SORT_COLS,
        ascending=[False] * len(TIEBREAKER_SORT_COLS),
    )
```

- [x] **Step 3: Update draft_routes.py round detection to use DRAFT_ROUNDS**

In `routes/draft_routes.py`, add the import (top of file with other constants imports):

```python
from services.constants import DRAFT_ROUNDS
```

Replace the hardcoded round-range tuple list at lines 325-327:

```python
# Before:
        for rnum, label, lo, hi in [(1, "Round 1 (Picks 1-10)", 1, 10),
                                     (2, "Round 2 (Picks 11-20)", 11, 20),
                                     (3, "Round 3 (Picks 21-30)", 21, 30)]:

# After:
        for rnum, (lo, hi) in DRAFT_ROUNDS.items():
            label = f"Round {rnum} (Picks {lo}-{hi})"
```

Note: the loop body at lines 328-334 uses `rnum`, `lo`, `hi`, and `label` — all still defined, just sourced from the constant.

- [x] **Step 4: Run tests to confirm no regressions**

```
pytest tests/ -v
```

Expected: all tests pass.

- [x] **Step 5: Commit**

```bash
git add services/constants.py services/analysis_service.py routes/draft_routes.py
git commit -m "refactor: extract DRAFT_ROUNDS and TIEBREAKER_SORT_COLS as named constants (#35)"
```

---

### Task 2: Env Var + Constant Documentation — Issue #37

**Files:**
- Modify: `README.md:58-61`
- Modify: `services/cache_service.py:118`
- Modify: `services/cache_service.py:145-149`
- Modify: `services/constants.py`

- [x] **Step 1: Add DEBUG_PAGE_LOAD to README env vars**

In `README.md`, the env vars section currently reads (around line 58):

```
3. Set up your environment variables:
   - `GEMINI_API_KEY`: Your Google AI key.
   - `USE_LOCAL_DATA`: Set to `True` for development without Firestore.
   - `FIREBASE_CREDENTIALS`: Path to your service account JSON (if not in root).
```

Add the `DEBUG_PAGE_LOAD` entry:

```
3. Set up your environment variables:
   - `GEMINI_API_KEY`: Your Google AI key.
   - `USE_LOCAL_DATA`: Set to `True` for development without Firestore.
   - `FIREBASE_CREDENTIALS`: Path to your service account JSON (if not in root).
   - `DEBUG_PAGE_LOAD`: Set to `True` to log page-load timing to console. Dev-only; defaults to `False`.
```

- [x] **Step 2: Enhance _CACHE_TTL_SECONDS comment in cache_service.py**

Line 118 currently reads:

```python
_CACHE_TTL_SECONDS = 3600  # 1 hour - long-lived persistence
```

Replace with:

```python
_CACHE_TTL_SECONDS = 3600  # 1-hour TTL; long enough to avoid Firestore spam on every request, short enough to catch same-day data changes
```

- [x] **Step 3: Add prediction key schema comment in cache_service.py**

Lines 145-149 currently read:

```python
# ---------------------------------------------------------------------------
# Game-level ML predictions (separate from analytics_cache)
# ---------------------------------------------------------------------------
# One document per season; game_key = "W{wk:02d}_{home}_{away}"
# Each value: {pred_prob, pred_winner, pred_su_conf, pred_ats_pick}
```

Replace with:

```python
# ---------------------------------------------------------------------------
# Game-level ML predictions (separate from analytics_cache)
# ---------------------------------------------------------------------------
# Key schema: "W{wk:02d}_{home}_{away}" — zero-padded week + both team abbrs.
# Three components needed because teams play each other in multiple weeks
# across a full season (e.g. conference championship rematches of regular
# season matchups). Season is implicit: one document per season in Firestore
# (keyed by season int) and one JSON file per season locally.
# Each value: {pred_prob, pred_winner, pred_su_conf, pred_ats_pick}
```

- [x] **Step 4: Enhance constants.py comments for UNDRAFTED_SENTINEL and ensemble weights**

`services/constants.py` currently has:

```python
# Sentinel used in the games DataFrame to indicate an undrafted team or unplayed game result.
UNDRAFTED_SENTINEL = -1000

# Ensemble blend weights (NN+XGB+LR). Must sum to 1.0.
NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35
```

Replace with:

```python
# Sentinel win-total for undrafted/unowned teams in standings calculations.
# Chosen to sort below any realistic win total (max 17 wins in a season),
# so undrafted slots always appear at the bottom without special-case logic.
UNDRAFTED_SENTINEL = -1000

# Ensemble blend weights — tuned empirically; see reports/nn_weekly_accuracy.csv.
# NN captures non-linear interactions; LR provides stability on sparse data;
# XGB fills the gap on structured tabular signals. Must sum to 1.0.
NN_WEIGHT  = 0.45
XGB_WEIGHT = 0.20
LR_WEIGHT  = 0.35
```

- [x] **Step 5: Run tests to confirm no regressions**

```
pytest tests/ -v
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add README.md services/cache_service.py services/constants.py
git commit -m "docs: add DEBUG_PAGE_LOAD to README + improve constant/cache comments (#37)"
```

---

### Task 3: Algorithm Block Comments — Issue #27

**Files:**
- Modify: `services/analysis_service.py:356,385,416`
- Modify: `services/db_service.py:37`
- Modify: `services/nn_feature_engine.py:54`

- [x] **Step 1: Add block comment before reshape_wins_pool_standings()**

`services/analysis_service.py` line 356. Insert immediately before `def reshape_wins_pool_standings`:

```python
# Pivot from long format (one row per player-team pair) to wide format
# (one row per player). Each player has 3 drafted teams, so the function
# groups by player and flattens (team, wins, ptDiff, global_record) × 3
# into columns wins1/wins2/wins3, ptDiff1/2/3, global_record1/2/3.
# Teams are ordered by draft pick number because the DataFrame is already
# sorted that way by the caller. TotalWins is derived here as the sum.
def reshape_wins_pool_standings(df: pd.DataFrame) -> pd.DataFrame:
```

- [x] **Step 2: Add block comment before apply_tiebreakers()**

`services/analysis_service.py` line 385. Insert immediately before `def apply_tiebreakers`:

```python
# Build six derived tiebreaker columns from the three per-team win and
# point-differential columns, then sort by all seven columns descending.
# Point-differential columns also sort descending: a higher (less negative)
# differential is better, so descending puts the best differential first.
# Column names and sort order are defined in TIEBREAKER_SORT_COLS
# (services/constants.py) so the cascade can be audited in one place.
def apply_tiebreakers(reshaped_df: pd.DataFrame) -> pd.DataFrame:
```

- [x] **Step 3: Add block comment before get_enriched_schedule()**

`services/analysis_service.py` line 416. Insert immediately before `def get_enriched_schedule`:

```python
# Five-way merge sequence:
#   1. today_games         — current-season REG games from nfl_games
#   2. away draft_results  — maps away_team → playerId (who owns that team)
#   3. players             — maps playerId → fullName_away
#   4. home draft_results  — maps home_team → playerId_home_draft
#   5. players again       — maps playerId_home_draft → fullName_home
# Empty-string suffixes on merge 4 avoid column-name collisions with the
# columns already added in merges 2-3. The final frame has one row per
# game with both owners' names, their team season records, and ML predictions.
def get_enriched_schedule(games, draft_results, players, season):
```

- [x] **Step 4: Add block comment before verify_password() bcrypt migration**

`services/db_service.py` line 37. Insert immediately before `def verify_password`:

```python
# Password verification supports two hash formats transparently:
#   Legacy: 64-character lowercase hex string (SHA-256, no salt). These were
#           created before bcrypt was introduced. Identified by _is_legacy_sha256().
#   Modern: bcrypt hash starting with "$2b$" (12 rounds).
# When a legacy hash is verified successfully, the caller (auth_routes.py)
# is responsible for calling update_player_credentials() to upgrade the
# stored hash to bcrypt — the migration is transparent to the user.
def verify_password(plain_password: str, hashed_password: str) -> bool:
```

- [x] **Step 5: Add block comment before FEATURE_COLUMNS in nn_feature_engine.py**

`services/nn_feature_engine.py` line 54. Insert immediately before `FEATURE_COLUMNS = [`:

```python
# Features fed to the NN, XGB, and LR models. Three conceptual groups:
#   Schedule-context (Elo, rest, home-field, travel): captures structural
#     advantages that exist before the game starts.
#   Recent-form (EPA matchup, turnover margin, point differential, pressure):
#     rolling expanding-mean stats shifted 1 game to prevent data leakage.
#   Season-context (roster talent, win rate, week, surface, playoff flag):
#     longer-horizon signals that stabilise over the course of a season.
# spread_line was intentionally removed to prevent Vegas-line leakage:
# including it caused the model to back-solve the spread rather than
# independently predict outcomes, which inflated accuracy artificially.
FEATURE_COLUMNS = [
```

- [x] **Step 6: Run tests to confirm no regressions**

```
pytest tests/ -v
```

Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add services/analysis_service.py services/db_service.py services/nn_feature_engine.py
git commit -m "docs: add block comments to complex algorithms in analysis, db, and feature engine (#27)"
```
