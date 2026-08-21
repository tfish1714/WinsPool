# Auth & Cache Fixes Design — Issues #42 and #60

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix two high-priority bugs — an unauthenticated profile endpoint (IDOR) and player lookups that bypass the in-memory cache on every auth request.

**Architecture:** Both fixes are surgical changes to existing functions. No new abstractions, no new files. Combined they close a real security hole and eliminate redundant Firestore reads on every protected page load.

**Tech Stack:** FastAPI dependency injection (`Depends`), PyJWT (`session_service`), `cache_service._DATA_CACHE`.

---

## Fix 1: `/api/profile` Authentication (Issue #42)

### Problem

`GET /api/profile?playerId=X` in `routes/auth_routes.py:212-227` has no authentication guard. Any unauthenticated HTTP request can retrieve any player's email, role, full name, nickname, and MFA status by guessing or enumerating integer player IDs.

### Change

**File:** `routes/auth_routes.py`

- Add `_auth: dict = Depends(require_auth)` to `get_profile`.
- Remove the `playerId: str` query parameter.
- Derive player ID from `_auth["sub"]` (the JWT `sub` claim, set as `str(player_id)` in `create_token`).
- `require_auth` is already imported at the top of the file.

**Before:**
```python
@router.get("/profile")
async def get_profile(playerId: str):
    from services.db_service import get_player_by_id
    player = get_player_by_id(playerId)
```

**After:**
```python
@router.get("/profile")
async def get_profile(_auth: dict = Depends(require_auth)):
    from services.db_service import get_player_by_id
    player = get_player_by_id(_auth["sub"])
```

No client-side changes are needed. The frontend already sends the auth header/cookie on every request; it just stops appending `?playerId=X` to the URL. Verify `main.js` / `auth_service.js` to confirm the call site passes no `playerId` param after the fix.

### Error Behaviour

- Unauthenticated request → 401 (handled by `require_auth` raising `HTTPException`).
- Authenticated but player not found → 404 (unchanged).

---

## Fix 2: Player Lookup Cache Bypass (Issue #60)

### Problem

`get_player_by_id(player_id)` and `get_player_by_email(email)` in `services/db_service.py:179-195` both call `get_collection_df("players")` unconditionally. This bypasses the in-memory `_DATA_CACHE` maintained by `data_service.load_data()`, hitting Firestore (or a pkl read) on every invocation. Because these functions are called on every auth-required request (login, session validation, profile fetch), this is a hot path.

### Change

**File:** `services/db_service.py`

Add to imports (top of file, alongside existing imports):
```python
from services.cache_service import _DATA_CACHE, _cache_key
```

`cache_service` is a leaf module — it imports no application services — so this introduces no circular dependency.

Update both lookup functions to check the warm cache first:

**`get_player_by_id`:**
```python
def get_player_by_id(player_id: str):
    bundle = _DATA_CACHE.get(_cache_key(None))
    players_df = bundle.players if bundle is not None else get_collection_df("players")
    if not players_df.empty and "playerId" in players_df.columns:
        match = players_df[players_df["playerId"].astype(str) == str(player_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None
```

**`get_player_by_email`:**
```python
def get_player_by_email(email: str):
    bundle = _DATA_CACHE.get(_cache_key(None))
    players_df = bundle.players if bundle is not None else get_collection_df("players")
    if not players_df.empty and "email" in players_df.columns:
        match = players_df[players_df["email"].astype(str).str.lower() == email.lower()]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None
```

Cold-cache behaviour is identical to today — `get_collection_df("players")` is called as a fallback. Warm-cache behaviour eliminates the Firestore/pkl round-trip entirely.

### Why `_cache_key(None)` / `'all'`

`load_data()` without a `year` argument stores the full players DataFrame under the key `_cache_key(None)` → `'all'`. Auth lookups do not need year-filtered data, so this is the correct key to check.

---

## Tests

### Fix 1 — `/api/profile` auth

| Scenario | Expected |
|---|---|
| `GET /api/profile` with no token | 401 |
| `GET /api/profile` with valid token | 200, returns authed user's data |
| `GET /api/profile` — returned payload has no `password_hash` | 200, `password_hash` absent |

File: `tests/test_auth_routes.py`

### Fix 2 — Cache bypass

| Scenario | Expected |
|---|---|
| `_DATA_CACHE` warm: call `get_player_by_id` | Returns player; `get_collection_df` NOT called |
| `_DATA_CACHE` cold: call `get_player_by_id` | Falls back to `get_collection_df("players")` |
| `_DATA_CACHE` warm: call `get_player_by_email` | Returns player; `get_collection_df` NOT called |
| `_DATA_CACHE` cold: call `get_player_by_email` | Falls back to `get_collection_df("players")` |

File: `tests/test_db_service.py`

---

## Scope

These are the only changes. No refactoring of surrounding code, no changes to `update_profile`, no rate-limiting work (tracked separately as issue #43).
