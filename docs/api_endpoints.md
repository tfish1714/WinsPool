# API Endpoint Documentation

## Overview

All JSON API endpoints are defined in `routes/api_routes.py` under the `/api` prefix. Page-serving routes (SSR) are defined in `standings_routes.py`, `history_routes.py`, and `draft_routes.py`. The WebSocket endpoint is defined in `draft_routes.py`.

Authorization for admin endpoints is enforced via Role-Based Access Control (RBAC). The `playerId` parameter is validated against `db_service.get_player_role()` to confirm `admin` access.

---

## Public Data Endpoints

### `GET /api/progress/{season}/{week}`

Returns cumulative player wins chart data for a given season and week.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | path | `string` | Season year or `"latest"` |
| `week` | path | `string` | Week number or `"latest"` |

**Response**: JSON object with `players`, `teams`, `best_overall`, `best_by_round` fields.

---

### `GET /api/progress/draft_summary`

Returns the best-picks summary for the current season. Used by the draft board UI tab.

**Response**: `{ season, week, best_overall, best_by_round }`

---

### `GET /api/standings`

Returns calculated standings data for the given season.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `year` | query | `int` | Season year |

**Response**: Array of player standing objects with `fullName`, `TotalWins`, `Rank`, tiebreaker fields, and per-team wins.

---

### `GET /api/schedule`

Returns the enriched schedule for a season, including draft owner mappings, odds, and global records.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `year` | query | `int` | Season year |

**Response**: Array of enriched game objects.

---

## Prediction Engine Endpoints

### `GET /api/predictions/game`

Returns the blended win probability for a single NFL matchup using the Elo + Pythagorean model.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `home_team` | query | `string` | Home team abbreviation (e.g. `"KC"`) |
| `away_team` | query | `string` | Away team abbreviation (e.g. `"BUF"`) |
| `season` | query | `int` | Season year for Elo context (default 2025) |

**Response**: `{ home_team, away_team, home_win_prob, away_win_prob, elo_home_prob, pyth_home_prob, home_elo, away_elo, adjustments, travel_miles, elo_weight, predicted_spread }`

---

### `GET /api/predictions/portfolio`

Monte Carlo projected cumulative wins for a player's 3-team portfolio.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | query | `int` | Season year |
| `playerId` | query | `int` | Player whose drafted teams to project |

**Response**: `{ mean_wins, std_wins, min_wins, max_wins, actual_wins, projected_additional, simulations, season_complete }`

---

### `GET /api/predictions/ratings`

Current Elo power ratings for all NFL teams, sorted by rating descending.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | query | `int` | Season year for Elo context (default 2025) |

**Response**: JSON object mapping team abbreviation to Elo rating.

---

### `GET /api/admin/predictions/confidence`

Admin-only: Confidence scores ranking all NFL teams for the live draft room. Excludes already-drafted teams.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `season` | query | `int` | Season year |
| `playerId` | query | `string` | Admin player ID (for auth) |

**Response**: Array of `{ team, elo, projected_wins, confidence, rank }` sorted by confidence descending.

---

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

### `GET /api/admin/predictions/config`

Admin-only: Read the current model blend weights.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `playerId` | query | `string` | Admin player ID |

**Response**: `{ elo_weight, simulations }`

---

### `POST /api/admin/predictions/config`

Admin-only: Update the Elo/Pythagorean blend weights. Persisted to Firestore `metadata/prediction_config`.

**Request Body**:
```json
{ "playerId": "string", "elo_weight": 0.7, "simulations": 1000 }
```

**Constraints**: `elo_weight` must be in [0.0, 1.0]. `simulations` must be in [100, 10000].

**Response**: `{ message, elo_weight, pythagorean_weight, simulations }`

---

## Authentication Endpoints

### `GET /api/check_player`

Checks if a player exists by email and whether they have a password configured.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `email` | query | `string` | Player email address |

**Response**: `{ exists, has_password, playerId }`

---

### `POST /api/set_password`

Sets or resets a player's password. Enforces rate limiting (5 failed attempts triggers lockout).

**Request Body**:
```json
{ "playerId": "string", "password": "string", "confirm_password": "string" }
```

**Response**: `{ success, message }` or `{ error }` with status 400/429.

---

### `POST /api/login`

Authenticates a player using email and password. Returns MFA challenge if MFA is enabled.

**Request Body**:
```json
{ "email": "string", "password": "string" }
```

**Response**: `{ success, playerId, fullName, nickName, role }` or `{ mfa_required, playerId }`.

---

### `POST /api/verify_mfa`

Verifies a one-time MFA code for two-factor authentication.

**Request Body**:
```json
{ "playerId": "string", "code": "string" }
```

**Response**: `{ success, playerId, fullName, nickName, role }` or `{ error }`.

---

### `POST /api/record_failed_setup`

Rate-limiting endpoint fired by frontend JavaScript when password setup validation fails.

**Request Body**:
```json
{ "playerId": "string" }
```

**Response**: `{ success }` or `{ error, locked_until }` with status 429.

---

## Profile Endpoints

### `GET /api/profile`

Fetches the current player's profile data for pre-filling forms.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `playerId` | query | `string` | Player ID |

**Response**: `{ playerId, fullName, nickName, email, cell, mfa_enabled }`

---

### `POST /api/profile`

Updates non-credential profile fields (nickname, email, MFA toggle).

**Request Body**:
```json
{ "playerId": "string", "nickName": "string", "email": "string", "mfa_enabled": "bool" }
```

**Response**: `{ success }` or `{ error }`.

---

## Admin Endpoints

All admin endpoints require `playerId` in the request body. The server validates the player's `role` is `"admin"` before processing.

### `GET /api/admin/players`

Lists all players in the pool.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `playerId` | query | `string` | Admin player ID (for auth check) |

**Response**: Array of player objects.

---

### `GET /api/admin/seasons`

Lists all seasons with draft data.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `playerId` | query | `string` | Admin player ID |

**Response**: `{ seasons: [int] }`

---

### `POST /api/admin/new_season`

Creates a new season with a randomized draft order and snake-draft rules.

**Request Body**:
```json
{ "playerId": "string", "season": "int", "playerIds": ["int"] }
```

**Response**: `{ success, draft_order: [...] }`

---

### `POST /api/admin/preview_draft_order`

Generates a randomized draft order preview without persisting to the database.

**Request Body**:
```json
{ "playerId": "string", "playerIds": ["int"] }
```

**Response**: `{ success, preview: [...] }`

---

### `POST /api/admin/create_player`

Creates a new player entrant in the pool.

**Request Body**:
```json
{ "playerId": "string", "fullName": "string", "nickName": "string", "email": "string" }
```

**Response**: `{ success, new_player_id }`

---

### `POST /api/admin/delete_season`

Wipes all draft data (order, rules, results) for a specific season.

**Request Body**:
```json
{ "playerId": "string", "season": "int" }
```

**Response**: `{ success }`

---

### `POST /api/admin/reset_draft`

Deletes only the draft results for a season (preserving order and rules). Used for sandbox testing.

**Request Body**:
```json
{ "playerId": "string", "season": "int" }
```

**Response**: `{ success }`

---

## AI Recap Endpoints

### `POST /api/admin/recap/preview_prompt`

Generates a data-only prompt for AI weekly recap review. Includes system instructions and weekly game data.

**Request Body**:
```json
{ "playerId": "string", "year": "int", "week": "int" }
```

**Response**: `{ success, prompt_data, system_instructions }`

---

### `POST /api/admin/recap/generate`

Sends the provided prompt data to Google Gemini and returns the AI-generated summary.

**Request Body**:
```json
{ "playerId": "string", "prompt_data": "string" }
```

**Response**: `{ success, summary }`

---

### `POST /api/admin/draft_recap/preview_prompt`

Generates the data-only prompt for a post-draft season preview recap. Combines user predictions, draft results, and consensus data.

**Request Body**:
```json
{ "playerId": "string", "year": "int" }
```

**Response**: `{ success, prompt_data, system_instructions }`

---

### `POST /admin/recap/preview_draft_prompt`

Generates and returns the data prompt that would be sent to Gemini for a draft recap, without calling the AI.

**Auth:** Required (admin)

**Request body**: `{ "year": 2025 }`

**Response**: `{ "prompt": "..." }`

---

### `POST /api/admin/recap/save_and_broadcast`

Saves the finalized AI summary to Firestore and emails it to all players.

**Request Body**:
```json
{ "playerId": "string", "year": "int", "week": "int", "summary": "string" }
```

**Response**: `{ success }` or `{ error }`.

---

## Server-Side Rendered (SSR) Page Routes

### `standings_routes.py`

| Method | Path | Template | Description |
|---|---|---|---|
| `GET` | `/` | (redirect) | Redirects to `/wins-pool/{active_season}` |
| `GET` | `/profile` | `profile.html` | User profile management page |
| `GET` | `/wins-pool` | (redirect) | Redirects to current season |
| `GET` | `/wins-pool/{year}` | `wins_pool.html` | Main standings and schedule page |
| `GET` | `/wins-pool/{year}/weekbyweek` | `weekbyweek.html` | Week-by-week wins breakdown |
| `GET` | `/playoff-race` | (redirect) | Redirects to current season |
| `GET` | `/playoff-race/{year}` | `playoff_race.html` | Playoff race elimination tracker |

### `history_routes.py`

| Method | Path | Template | Description |
|---|---|---|---|
| `GET` | `/history` | `overall_history.html` | All-time player statistics and records |
| `GET` | `/headtohead` | (redirect) | Redirects to current season |
| `GET` | `/headtohead/history` | `headtohead_history.html` | All-time head-to-head matrices |
| `GET` | `/headtohead/{year}` | `headtohead.html` | Season-specific head-to-head matrix |

### `draft_routes.py`

| Method | Path | Template | Description |
|---|---|---|---|
| `GET` | `/draft` | `index.html` | Live draft board page |
| `GET` | `/draft-results` | (redirect) | Redirects to current season draft results |
| `GET` | `/draft-results/{year}` | `draft_results.html` | Draft results with consensus and value columns |
| `GET` | `/draft/history` | `draft_history.html` | Multi-season draft history |
| `GET` | `/admin` | `admin.html` | Admin control panel |

---

## WebSocket Endpoint

### `WS /ws`

Real-time draft board synchronization.

**Connection Protocol**:
1. Client connects and sends `{ type: "join", code: "room_code", nickname: "name", year: 2024 }`.
2. Server validates `code` against `ROOM_CODE` env var.
3. On successful join, server broadcasts the full draft state to all connected clients.
4. Client sends `{ type: "pick", team: "KC" }` to make a draft selection.
5. Client sends `{ type: "undo" }` to undo the last pick (admin only, validated server-side).
6. Client sends `{ type: "force_pick", target_pid: 123, team: "KC" }` for admin-forced picks.
7. Server broadcasts updated state after every mutation.

**Broadcast Message Format**:
```json
{
  "type": "state",
  "state": {
    "season": 2024,
    "draft_started": true,
    "draft_complete": false,
    "current_pick": 5,
    "current_player": { "playerId": 1, "fullName": "..." },
    "timer_start": 1700000000,
    "rounds": [...],
    "available_teams": [...],
    "picks": [...],
    "connected": ["nickname1", "nickname2"]
  }
}
```
