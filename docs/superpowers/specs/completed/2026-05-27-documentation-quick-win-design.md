# Documentation Quick Win — Issue #26

**Date:** 2026-05-27
**Status:** Approved
**Closes:** GitHub issue #26
**Scope:** `services/analysis_service.py`, `services/draft_service.py`, `services/db_service.py`, `services/session_service.py`, `services/cache_service.py`, `docs/api_endpoints.md`, `docs/database.md`

## Overview

Three mechanical changes with no behavior impact:
1. Add docstrings to public functions missing them across 5 service files
2. Add 4 missing endpoints to `docs/api_endpoints.md`
3. Fix bcrypt vs SHA-256 discrepancy in `docs/database.md`

---

## Component 1 — Docstrings

Add a single-line or short docstring to each public function listed below. Docstrings describe **what the function does and what it returns** — not how it does it. One sentence is enough for simple functions; 2–3 lines for complex ones.

### `services/analysis_service.py`

| Function | What to document |
|---|---|
| `get_remaining_games` | Returns list of unplayed games for a team in a given season |
| `player_winsbyWeek` | Returns per-week win counts for each player's drafted teams |
| `create_what_if_scenario_matrix` | Builds matrix of possible win outcomes across remaining games |
| `player_winlossmatrix` | Returns head-to-head win/loss record between all player pairs |
| `reshape_wins_pool_standings` | Pivots raw standings data into a wide-format leaderboard DataFrame |
| `apply_tiebreakers` | Sorts standings using 6-tier tiebreaker cascade (wins → point diff → ...) |
| `get_enriched_schedule` | 5-way join: games + standings + predictions + Elo + team metadata |
| `calculate_wins_pool_standings` | Computes cumulative win totals per player from game results |
| `process_games_data` | Filters and normalizes raw games DataFrame for standings computation |

### `services/draft_service.py`

| Function | What to document |
|---|---|
| `sanitize_state` | Strips internal fields from draft state before broadcasting to clients |
| `save_pick` | Persists a validated pick to Firestore and updates in-memory draft state |
| `undo_pick` | Removes the most recent pick from state and Firestore |
| `reset_pick` | Clears a specific player's pick (admin override) |
| `update_player_phone` | Updates phone number on a player record in draft state |
| `wipe_draft_cache` | Clears the in-memory draft state cache, forcing reload from Firestore |

### `services/db_service.py`

| Function | What to document |
|---|---|
| `update_player_cell` | Updates a single field on a player document in Firestore |
| `get_player_role` | Returns the role string ('admin' or 'user') for a given player ID |
| `get_player_by_email` | Looks up a player document by email address |
| `get_player_by_id` | Looks up a player document by numeric player ID |
| `update_player_credentials` | Updates hashed password and resets failed-setup counter |
| `increment_failed_setup_attempts` | Increments the failed-credential-setup counter for a player |
| `add_player` | Creates a new player document in Firestore and local pkl |
| `add_draft_result` | Writes a single draft pick result to Firestore and local pkl |
| `delete_draft_pick` | Removes a specific draft pick by player and team from Firestore |
| `delete_draft_results_for_season` | Deletes all draft picks for a given season |
| `delete_season_data` | Deletes all games, standings, and predictions for a season |
| `add_draft_order` | Writes a player's draft order entry for a season |
| `add_draft_rule` | Persists a draft order rule document |
| `set_member_paid` | Sets the paid flag on a player's draft_order entry for a season |
| `update_player_profile` | Updates display name and/or email on a player document |
| `save_weekly_recap` | Persists a weekly AI recap string to Firestore |
| `get_weekly_recap` | Retrieves a stored weekly recap string from Firestore |
| `save_metadata` | Writes an arbitrary key/value pair to the metadata collection |
| `get_metadata` | Reads a stored metadata value by key |

### `services/session_service.py`

| Function | What to document |
|---|---|
| `create_token` | Creates a signed JWT with player_id, role, and expiry |
| `decode_token` | Decodes and validates a JWT; raises HTTPException on failure |

### `services/cache_service.py`

| Function | What to document |
|---|---|
| `write_cache` | Writes a value to the in-memory TTL cache under a given key |
| `is_cache_final` | Returns True if the cached entry is marked as finalized (no refresh needed) |

---

## Component 2 — API endpoint docs

Add these 4 entries to `docs/api_endpoints.md`. Follow the existing format in that file.

### `GET /api/progress/draft_summary`

- **Auth:** Required (player)
- **Description:** Returns the best-pick summary for the draft board progress tab.
- **Response:**
  ```json
  {
    "season": 2025,
    "week": 14,
    "best_overall": {"player_name": "...", "wins": 12},
    "best_by_round": [{"round": 1, "player_name": "...", "team": "KC", "wins": 5}, ...]
  }
  ```

### `GET /api/predictions/accuracy`

- **Auth:** Required (player)
- **Query params:** `season` (int), `week` (int, optional)
- **Description:** Returns ML ensemble prediction accuracy vs. actual game results.
- **Response:** Array of `{season, week, correct, total, accuracy_pct, brier_score}` objects.

### `GET /api/predictions/explain`

- **Auth:** Required (player)
- **Query params:** `season` (int), `week` (int), `home` (team abbrev), `away` (team abbrev)
- **Description:** Returns per-game prediction explanation including feature breakdown, model spread, and Vegas line comparison.
- **Response:** `{home_team, away_team, pred_home_wp, model_spread, vegas_line, features: {...}}`

### `POST /admin/recap/preview_draft_prompt`

- **Auth:** Required (admin)
- **Body:** `{"year": 2025}`
- **Description:** Generates and returns the data prompt that would be sent to Gemini for a draft recap, without actually calling the AI.
- **Response:** `{"prompt": "..."}`

---

## Component 3 — Database doc fix

In `docs/database.md`, update the `password_hash` field description from:

> `"SHA-256 hash of the player's password"`

To:

> `"bcrypt hash (12 rounds) of the player's password. Legacy SHA-256 hashes (64-char hex) are supported for migration — verified and automatically upgraded on next login."`

---

## What this does NOT include

- Changes to any Python source files other than adding docstrings
- New tests
- Changes to API behavior
