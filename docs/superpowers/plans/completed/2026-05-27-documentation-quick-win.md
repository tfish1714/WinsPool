# Documentation Quick Win Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add docstrings to ~16 public functions across 3 service files, document 4 missing API endpoints in `docs/api_endpoints.md`, and fix the SHA-256 vs bcrypt discrepancy in `docs/database.md`.

**Architecture:** Pure documentation — no behavior changes. Each task touches one file. Tests are not needed (docstrings aren't tested), but the test suite must still pass after each commit to confirm nothing was accidentally broken.

**Tech Stack:** Python docstrings, Markdown

---

## Files

| Action | Path | Change |
|---|---|---|
| Modify | `services/analysis_service.py` | Add docstrings to 8 functions |
| Modify | `services/db_service.py` | Add docstrings to 6 functions |
| Modify | `services/session_service.py` | Add docstrings to 2 functions |
| Modify | `docs/api_endpoints.md` | Add 4 missing endpoint entries |
| Modify | `docs/database.md` | Fix password_hash field description |

---

## Task 1: Docstrings — `services/analysis_service.py`

**Files:**
- Modify: `services/analysis_service.py`

The 8 functions below currently have no docstring. Add a short docstring immediately after each `def` line. Use the signatures and existing variable names to infer what each function does — don't guess at implementation details.

- [x] **Step 1: Add docstrings to all 8 functions**

Read `services/analysis_service.py` first to see the exact signatures. Then add these docstrings:

```python
def get_remaining_games(player: str, schedule: pd.DataFrame) -> int:
    """Return the count of unplayed games remaining for a player's drafted teams."""

def player_winsbyWeek(schedule: pd.DataFrame, sorted_players: List[str] = None) -> pd.DataFrame:
    """Return a DataFrame of cumulative wins per player broken down by week."""

def create_what_if_scenario_matrix(schedule: pd.DataFrame, record_by_week: pd.DataFrame, step: float = 0.166666666666) -> pd.DataFrame:
    """Build a matrix of hypothetical final-standings outcomes across remaining games.

    Iterates over win-probability steps to estimate how often each player
    finishes in each rank given uncertain remaining results.
    """

def player_winlossmatrix(schedule: pd.DataFrame) -> pd.DataFrame:
    """Return a head-to-head win/loss record matrix between every player pair."""

def reshape_wins_pool_standings(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot per-team win rows into a wide-format standings DataFrame (one row per player).

    Each player row gets wins1/wins2/wins3, ptDiff1/2/3, and global_record1/2/3
    columns corresponding to their three drafted teams.
    """

def apply_tiebreakers(reshaped_df: pd.DataFrame) -> pd.DataFrame:
    """Sort standings using the 6-tier tiebreaker cascade.

    Tiers (highest to lowest priority): total wins, worst-team wins,
    best-team wins, total point differential, head-to-head record,
    preseason projected wins. Ascending sort flags vary per tier.
    """

def get_enriched_schedule(games, draft_results, players, season):
    """Join games with draft ownership, player metadata, standings, and predictions.

    Performs a 5-way merge so each game row carries the owning player's name,
    their team's season record, and ML win-probability for display in the
    schedule tab.
    """

def calculate_wins_pool_standings(standings, draft_results, players, season, games=None):
    """Compute per-player cumulative win totals from game results and draft assignments.

    Merges standings with draft_results and players to produce a DataFrame
    with one row per (player, team) pair and columns for wins, point
    differential, and global record.
    """
```

- [x] **Step 2: Confirm test suite still passes**

```bash
pytest tests/ -q --tb=short
```

Expected: same pass count as before (300 passed, 5 Firebase errors).

- [x] **Step 3: Commit**

```bash
git add services/analysis_service.py
git commit -m "docs: add docstrings to analysis_service public functions"
```

---

## Task 2: Docstrings — `services/db_service.py`

**Files:**
- Modify: `services/db_service.py`

6 functions are missing docstrings. Read the file around each function to understand what it does before writing.

- [x] **Step 1: Add docstrings to all 6 functions**

```python
def update_player_cell(player_id: int, cell: str):
    """Update the phone number (cell) field on a player's document."""

def add_draft_result(season: int, draft_pick: int, player_id: int, team: str, executed_by: str = None, time_taken_seconds: float = None):
    """Write a single draft pick result to Firestore and the local pkl cache."""

def delete_draft_pick(season: int, draft_pick: int):
    """Delete a specific draft pick document from Firestore and local pkl."""

def delete_draft_results_for_season(season: int):
    """Delete all draft_results documents for the given season from Firestore and local pkl."""

def add_draft_order(season: int, draft_order: int, player_id: int):
    """Create a draft_order entry mapping a player to their draft position for a season."""

def add_draft_rule(season: int, draft_order: int, pick_one: int, pick_two: int, pick_three: int):
    """Persist the three pick-slot assignments for a player's draft order rule."""
```

- [x] **Step 2: Confirm test suite still passes**

```bash
pytest tests/ -q --tb=short
```

- [x] **Step 3: Commit**

```bash
git add services/db_service.py
git commit -m "docs: add docstrings to db_service public functions"
```

---

## Task 3: Docstrings — `services/session_service.py`

**Files:**
- Modify: `services/session_service.py`

2 public functions lack docstrings. The private `_resolve_token` already has one; `require_auth` and `require_admin` already have docstrings too — only `create_token` and `decode_token` need them.

- [x] **Step 1: Add docstrings**

```python
def create_token(player_id: int, role: str) -> str:
    """Create a signed JWT containing player_id, role, issued-at, and expiry (7 days)."""

def decode_token(token: str) -> dict:
    """Decode and verify a JWT, returning the payload dict.

    Raises jwt.ExpiredSignatureError if the token is expired, and
    jwt.InvalidTokenError for any other verification failure.
    """
```

- [x] **Step 2: Confirm test suite still passes**

```bash
pytest tests/ -q --tb=short
```

- [x] **Step 3: Commit**

```bash
git add services/session_service.py
git commit -m "docs: add docstrings to session_service create_token and decode_token"
```

---

## Task 4: API endpoint docs — `docs/api_endpoints.md`

**Files:**
- Modify: `docs/api_endpoints.md`

4 endpoints exist in the codebase but are absent from the docs. Read `docs/api_endpoints.md` first to understand the exact Markdown format used, then add entries that match the existing style.

- [x] **Step 1: Read the current file format**

Read `docs/api_endpoints.md` — note the heading level, table format, and response example style for existing entries.

- [x] **Step 2: Add the 4 missing entries**

Add these entries in the appropriate sections (Prediction Engine Endpoints for the predictions entries; Admin Endpoints section for the admin entry; Progress section for draft_summary if not already there):

```markdown
### `GET /api/predictions/accuracy`

Returns ML ensemble prediction accuracy versus actual game results.

**Auth:** Required (any role)

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | query | `int` | Season year |
| `week` | query | `int` | Optional — filters to a specific week |

**Response**: Array of `{ season, week, correct, total, accuracy_pct, brier_score, log_loss }` objects.

---

### `GET /api/predictions/explain`

Returns a per-game prediction explanation with feature breakdown and Vegas line comparison.

**Auth:** Required (any role)

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | query | `int` | Season year |
| `week` | query | `int` | Week number |
| `home` | query | `string` | Home team abbreviation (e.g. `"KC"`) |
| `away` | query | `string` | Away team abbreviation |

**Response**: `{ home_team, away_team, pred_home_wp, model_spread, vegas_line, features: { ... } }`

---

### `POST /admin/recap/preview_draft_prompt`

Generates and returns the data prompt that would be sent to Gemini for a draft recap, without calling the AI.

**Auth:** Required (admin)

**Request body**: `{ "year": 2025 }`

**Response**: `{ "prompt": "..." }`

---
```

Note: `/api/progress/draft_summary` is already documented at line 26 of the file — verify before adding to avoid a duplicate.

- [x] **Step 3: Commit**

```bash
git add docs/api_endpoints.md
git commit -m "docs: add 4 missing API endpoints to api_endpoints.md"
```

---

## Task 5: Fix database.md password_hash description

**Files:**
- Modify: `docs/database.md`

- [x] **Step 1: Read the file around line 28**

Read `docs/database.md` lines 20–35 to find the exact text to replace.

- [x] **Step 2: Update the password_hash row**

Replace:
```
| `password_hash` | `string` | SHA-256 hash of the player's password |
```

With:
```
| `password_hash` | `string` | bcrypt hash (12 rounds) of the player's password. Legacy SHA-256 hashes (64-char hex) are supported for migration — verified and automatically upgraded on next login. |
```

- [x] **Step 3: Commit and close issue**

```bash
git add docs/database.md
git commit -m "docs: fix password_hash description (bcrypt not SHA-256); closes #26"
```
