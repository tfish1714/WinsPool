# User Last Activity Tracking in Admin Panel Design

**Date:** 2026-09-23  
**Status:** Draft  
**Target Areas:** `services/session_service.py`, `routes/admin_routes.py`, `static/js/admin_main.js`, `templates/admin_main.html`, `tests/test_admin_routes.py`, `tests/test_session_service.py`

---

## 1. Problem Statement & Motivation

Currently, WinsPool tracks only a single authentication timestamp on player records: `last_login`.
`last_login` is updated solely when a user explicitly submits credentials:
1. `POST /api/login` (entering email and password)
2. `POST /api/verify-mfa` (completing two-factor authentication)
3. `POST /api/set-password` (initial account claim or password reset)

Because authentication sessions persist via an HTTP-only `session_token` cookie (or stored JWT Bearer token), active users routinely interact with the platform for days or weeks across drafts, pool standings checks, and weekly matchups without ever re-entering their password. Conversely, an inactive user may have logged in once three weeks ago and never visited the site again.

As a result, administrators cannot distinguish between:
- A user who is actively visiting the app daily using an existing session.
- A user who logged in once and has abandoned the platform.
- A user who hasn't entered a password in two weeks but made picks yesterday.

The goal of this feature is to track and surface **`last_active`** independently of **`last_login`** in the Admin Panel across both the **Season Members** grid and the **Player Management** directory.

---

## 2. Core Architectural Decisions

### 2.1. Defining "Activity" vs. "Login"
- **`last_login` (Existing):** The exact timestamp of the most recent credential verification (password/MFA). Preserved without changes.
- **`last_active` (New):** The timestamp of the user's most recent authenticated interaction with the application (page navigation or authenticated API call).

### 2.2. Write-Throttling & Database Performance Guard
Updating Firestore on *every* authenticated HTTP request would introduce severe bottlenecks:
- Excessive Firestore write operations and API billing.
- Lock contention and write rate limits on individual player documents (Firestore limits document updates to ~1 write/sec sustained).
- Unnecessary local database / pickle cache churn and invalidation triggers (`DOMAIN_PLAYERS`).

**Solution: In-Memory Write Debounce / Throttle Window (15 Minutes)**
- Maintain an in-memory dictionary `_LAST_ACTIVE_CACHE: dict[int, float]` mapping `player_id` to the timestamp of the last database write.
- When an authenticated request is processed:
  - If `player_id` is not in cache or `current_time - _LAST_ACTIVE_CACHE[player_id] >= 900` (15 minutes):
    - Update the memory cache with `current_time`.
    - Persist `last_active = current_time` to Firestore via `update_player_profile(str(player_id), {"last_active": current_time})`.
  - If `current_time - _LAST_ACTIVE_CACHE[player_id] < 900`:
    - Skip the database write entirely.

### 2.3. Exclusion of Background Polling and Static Assets
To ensure `last_active` reflects genuine user presence rather than abandoned background tabs:
- Exclude unauthenticated endpoints and static assets (`/static/*`, `/sw.js`, `/favicon.ico`).
- Exclude background automated pollers if invoked without user interaction:
  - Specifically, background polling on `/api/live-scores` does not require auth and already pauses on `document.hidden`.
  - Authenticated API requests (`/api/standings`, `/api/schedule`, navigation to any HTML route) count as user activity.

### 2.4. Where to Intercept Activity
FastAPI dependency injection in `services/session_service.py`:
- `require_auth` and `require_admin` are already the centralized bottlenecks through which every authenticated request passes.
- When `require_auth(authorization, session_token)` successfully decodes a valid token:
  - Extract `player_id = payload.get("player_id")`.
  - Trigger the throttled `record_user_activity(player_id)` helper.
  - Run the update non-blockingly (or via background task) so API response latency is unaffected.

---

## 3. Data Model & Backend Changes

### 3.1. Player Document Schema
Add `last_active` (nullable float timestamp) to the `players` collection in Firestore:
```json
{
  "playerId": 4,
  "fullName": "Jane Doe",
  "email": "jane@example.com",
  "last_login": 1726000000.0,
  "last_active": 1727130000.0
}
```

### 3.2. Activity Recording Service (`services/session_service.py`)
```python
_ACTIVITY_THROTTLE_SECONDS = 900  # 15 minutes
_LAST_ACTIVE_CACHE: dict[int, float] = {}

def record_user_activity(player_id: int) -> None:
    """Updates last_active in Firestore if at least 15 minutes have elapsed
    since the last recorded activity for this player.
    """
    now = time.time()
    last_recorded = _LAST_ACTIVE_CACHE.get(player_id, 0.0)
    if now - last_recorded >= _ACTIVITY_THROTTLE_SECONDS:
        _LAST_ACTIVE_CACHE[player_id] = now
        try:
            from services.db_service import update_player_profile
            update_player_profile(str(player_id), {"last_active": now})
        except Exception:
            logger.warning(f"Failed to record activity for player {player_id}", exc_info=True)
```

### 3.3. Admin API Endpoints (`routes/admin_routes.py`)
1. **`GET /admin/players` (`fetch_admin_players`):**
   - Read `last_active_val = r.get("last_active")`.
   - Cast to float if present and valid: `last_active = float(last_active_val) if pd.notna(last_active_val) and last_active_val is not None else None`.
   - Include `"last_active": last_active` in player response dictionaries.

2. **`GET /admin/members/{season}` (`get_season_members`):**
   - Read `last_active_val = p.get("last_active")`.
   - Cast to float if present and valid.
   - Include `"last_active": last_active` in member response dictionaries.

---

## 4. Admin Panel Frontend Presentation

### 4.1. Relative Time Formatting Helper (`static/js/admin_main.js`)
Add a human-friendly timestamp formatter alongside existing `_formatDate`:
- `< 1 minute`: `"Just now"`
- `< 60 minutes`: `"<N>m ago"`
- `< 24 hours`: `"<N>h ago"`
- `< 7 days`: `"<N>d ago"`
- Older: Absolute date formatted e.g. `"Sep 18, 2026"`
- Hover tooltip: Full formatted date and time (`title="2026-09-23 14:22:10"`).

### 4.2. Season Members Table (`static/js/admin_main.js`)
- Update the Season Members table header and rows:
  - Header: Split or adjust columns to display `Activity` alongside `Login`.
  - Format:
    - If `last_active` exists: Display relative time with indicator badge (e.g. green dot for `< 15m ago`, neutral text for older).
    - If `last_active` is null: Fallback to `last_login`, or `"Never"`.
  - Tooltip: Hovering shows both exact timestamps (`"Last active: <date> | Last login: <date>"`).

### 4.3. Player Management Cards (`static/js/admin_main.js`)
- In each player card meta row:
  - Update:
    ```html
    <span style="font-family:'JetBrains Mono',monospace; color:var(--ink-2);">
      Active: ${lastActiveText} · Login: ${lastLoginText}
    </span>
    ```
  - This immediately clarifies whether a user with an old `last_login` is currently active.

---

## 5. Non-Goals & Boundaries

1. **Not a Clickstream or Page Analytics Tracker:** This feature does not track which specific URLs, buttons, or features the user visited. It only tracks the coarse-grained timestamp of the most recent authenticated visit.
2. **No Persistent Session Store Migration:** The app will continue using stateless signed JWTs in `session_token`. We are not introducing Redis or a distributed server-side session table.
3. **No Retroactive Backfill:** Existing players will initially show `last_active = null` until their next authenticated request, falling back gracefully in the UI to their existing `last_login`.

---

## 6. Test and Verification Plan

### Automated Pytest Suite
1. **Activity Throttling Unit Tests (`tests/test_session_service.py`):**
   - Test that calling `record_user_activity(player_id)` updates Firestore on first invocation.
   - Test that calling `record_user_activity(player_id)` again within 14 minutes skips the Firestore write.
   - Test that calling after 15 minutes executes a subsequent Firestore write and updates the cache.
   - Test that exceptions during the database write do not raise or break the user's HTTP request.
2. **Endpoint Payload Tests (`tests/test_admin_routes.py`):**
   - Test `GET /admin/players` returns `last_active` as a float when present, and `None` when absent.
   - Test `GET /admin/members/{season}` returns `last_active` for each member.
3. **Auth Dependency Integration (`tests/test_auth.py`):**
   - Test that making an authenticated request via `require_auth` with a valid token invokes `record_user_activity`.
   - Test that unauthenticated / invalid token requests do not trigger activity tracking.

### Manual Verification Flow
1. Log in with a test user; verify `last_login` and `last_active` both reflect current timestamp.
2. Navigate between pages in WinsPool across several minutes.
3. Verify in Admin Panel (`/admin`) under both Season Members and Player Management:
   - `last_active` reflects the current visit time.
   - `last_login` remains frozen at the initial login time.
4. Verify relative formatting ("Just now", "5m ago") renders cleanly on desktop and mobile viewports.
