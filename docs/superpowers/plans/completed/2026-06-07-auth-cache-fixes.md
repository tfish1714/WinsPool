# Auth & Cache Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two high-priority bugs — an unauthenticated `/api/profile` endpoint (IDOR, issue #42) and player lookups that bypass the in-memory cache on every auth request (issue #60).

**Architecture:** Task 1 adds a `_DATA_CACHE` check to `db_service.get_player_by_id` and `get_player_by_email` before falling back to Firestore. Task 2 adds `require_auth` to the profile GET endpoint, removes the `playerId` query param (deriving it from the JWT `sub` claim instead), and updates the JS client to send the auth header.

**Tech Stack:** FastAPI `Depends`, PyJWT (`session_service.create_token`), `cache_service._DATA_CACHE`, `unittest.mock.patch`, `fastapi.testclient.TestClient`.

---

## File Structure

| File | Change |
|---|---|
| `services/db_service.py` | Add `_DATA_CACHE, _cache_key` import; update `get_player_by_id` and `get_player_by_email` to check warm cache first |
| `routes/auth_routes.py` | Add `require_auth` dep to `get_profile`; remove `playerId` query param; derive player ID from `_auth["sub"]` |
| `static/js/auth_service.js` | Remove `playerId` param from `syncProfile`; drop query string; add `Authorization` header |
| `static/js/main.js` | Update `syncProfile(this.user.playerId)` call site to `syncProfile()` |
| `tests/test_db.py` | Add 4 tests for cache-hit and cold-cache paths on both lookup functions |
| `tests/test_auth.py` | Replace `test_api_profile_undefined_handling` with correct post-fix expectations; add authenticated success test |

---

## Task 1: Fix player lookup cache bypass (Issue #60)

**Files:**
- Modify: `services/db_service.py:1-11` (imports), `services/db_service.py:179-195` (both functions)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write four failing tests**

Add to `tests/test_db.py` (after the existing imports):

```python
import pandas as pd
from unittest.mock import patch, MagicMock


def _make_players_df():
    return pd.DataFrame([
        {"playerId": 42, "fullName": "Cache Hit",  "email": "hit@example.com"},
        {"playerId": 99, "fullName": "Other Player","email": "other@example.com"},
    ])


def test_get_player_by_id_uses_warm_cache(monkeypatch):
    """get_player_by_id reads from _DATA_CACHE when it is warm — no Firestore call."""
    from services import cache_service
    from services.db_service import get_player_by_id

    bundle = MagicMock()
    bundle.players = _make_players_df()
    monkeypatch.setitem(cache_service._DATA_CACHE, 'all', bundle)

    with patch('services.db_service.get_collection_df') as mock_gcd:
        result = get_player_by_id('42')
        mock_gcd.assert_not_called()

    assert result is not None
    assert result['fullName'] == 'Cache Hit'


def test_get_player_by_id_cold_cache_falls_back(monkeypatch):
    """get_player_by_id calls get_collection_df when _DATA_CACHE is empty."""
    from services import cache_service
    from services.db_service import get_player_by_id

    monkeypatch.setattr(cache_service, '_DATA_CACHE', {})

    with patch('services.db_service.get_collection_df') as mock_gcd:
        mock_gcd.return_value = _make_players_df()
        result = get_player_by_id('99')
        mock_gcd.assert_called_once_with('players')

    assert result['fullName'] == 'Other Player'


def test_get_player_by_email_uses_warm_cache(monkeypatch):
    """get_player_by_email reads from _DATA_CACHE when it is warm — no Firestore call."""
    from services import cache_service
    from services.db_service import get_player_by_email

    bundle = MagicMock()
    bundle.players = _make_players_df()
    monkeypatch.setitem(cache_service._DATA_CACHE, 'all', bundle)

    with patch('services.db_service.get_collection_df') as mock_gcd:
        result = get_player_by_email('hit@example.com')
        mock_gcd.assert_not_called()

    assert result is not None
    assert result['playerId'] == 42


def test_get_player_by_email_cold_cache_falls_back(monkeypatch):
    """get_player_by_email calls get_collection_df when _DATA_CACHE is empty."""
    from services import cache_service
    from services.db_service import get_player_by_email

    monkeypatch.setattr(cache_service, '_DATA_CACHE', {})

    with patch('services.db_service.get_collection_df') as mock_gcd:
        mock_gcd.return_value = _make_players_df()
        result = get_player_by_email('HIT@EXAMPLE.COM')  # case-insensitive
        mock_gcd.assert_called_once_with('players')

    assert result['fullName'] == 'Cache Hit'
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_db.py::test_get_player_by_id_uses_warm_cache tests/test_db.py::test_get_player_by_id_cold_cache_falls_back tests/test_db.py::test_get_player_by_email_uses_warm_cache tests/test_db.py::test_get_player_by_email_cold_cache_falls_back -v
```

Expected: all 4 FAIL (the cache check doesn't exist yet, so warm-cache tests will call `get_collection_df` unexpectedly).

- [ ] **Step 3: Update the import line in `services/db_service.py`**

Current line 10:
```python
from services.cache_service import clear_data_cache
```

Replace with:
```python
from services.cache_service import clear_data_cache, _DATA_CACHE, _cache_key
```

- [ ] **Step 4: Replace `get_player_by_email` (lines 179-186)**

```python
def get_player_by_email(email: str):
    """Retrieve a single player directly by their standardized email address."""
    bundle = _DATA_CACHE.get(_cache_key(None))
    players_df = bundle.players if bundle is not None else get_collection_df("players")
    if not players_df.empty and "email" in players_df.columns:
        match = players_df[players_df["email"].astype(str).str.lower() == email.lower()]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None
```

- [ ] **Step 5: Replace `get_player_by_id` (lines 188-195)**

```python
def get_player_by_id(player_id: str):
    """Retrieve a single player directly by their ID."""
    bundle = _DATA_CACHE.get(_cache_key(None))
    players_df = bundle.players if bundle is not None else get_collection_df("players")
    if not players_df.empty and "playerId" in players_df.columns:
        match = players_df[players_df["playerId"].astype(str) == str(player_id)]
        if not match.empty:
            return match.iloc[0].to_dict()
    return None
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_db.py::test_get_player_by_id_uses_warm_cache tests/test_db.py::test_get_player_by_id_cold_cache_falls_back tests/test_db.py::test_get_player_by_email_uses_warm_cache tests/test_db.py::test_get_player_by_email_cold_cache_falls_back -v
```

Expected: all 4 PASS.

- [ ] **Step 7: Run full test suite to check for regressions**

```
pytest tests/ -q
```

Expected: no new failures.

- [ ] **Step 8: Commit**

```
git add services/db_service.py tests/test_db.py
git commit -m "fix: player lookups check in-memory cache before Firestore (#60)"
```

---

## Task 2: Add authentication to `/api/profile` (Issue #42)

**Files:**
- Modify: `routes/auth_routes.py:212-227`
- Modify: `static/js/auth_service.js:63-82`
- Modify: `static/js/main.js:53`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests and update the existing broken test**

In `tests/test_auth.py`, replace the existing `test_api_profile_undefined_handling` test and add a new authenticated success test. The file already has `from main import app` and `client = TestClient(app)` at the top.

Replace:
```python
def test_api_profile_undefined_handling():
    """Verify that 'undefined' as a playerId returns 404 gracefully."""
    response = client.get("/api/profile?playerId=undefined")
    assert response.status_code == 404
```

With:
```python
def test_api_profile_requires_auth():
    """GET /api/profile with no token must return 401."""
    response = client.get("/api/profile")
    assert response.status_code == 401
```

Then add at the end of the file:
```python
def test_api_profile_returns_own_data(auth_token):
    """Authenticated player receives their own profile data."""
    from unittest.mock import patch

    fake_player = {
        "playerId": 1,
        "fullName": "Test Player",
        "nickName": "TP",
        "email": "test@example.com",
        "role": "user",
        "mfa_enabled": False,
        "password_hash": "should_not_appear",
    }
    with patch("services.db_service.get_player_by_id", return_value=fake_player):
        response = client.get(
            "/api/profile",
            headers={"Authorization": auth_token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["fullName"] == "Test Player"
    assert data["playerId"] == "1"
    assert "password_hash" not in data
```

Note: `auth_token` is the `conftest.py` fixture that creates a token for `player_id=1, role='user'`. The JWT `sub` claim will be `"1"`.

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_auth.py::test_api_profile_requires_auth tests/test_auth.py::test_api_profile_returns_own_data -v
```

Expected: `test_api_profile_requires_auth` FAILS (currently returns 404 without auth because the endpoint accepts any request). `test_api_profile_returns_own_data` FAILS (401 because endpoint has no auth guard yet and thus the mock isn't reached, OR a parameter error).

- [ ] **Step 3: Update imports in `routes/auth_routes.py`**

`auth_routes.py` does not yet import `Depends` or `require_auth`. Make two changes:

Line 9 — add `Depends`:
```python
from fastapi import APIRouter, Depends, Response
```

Line 20 — add `require_auth`:
```python
from services.session_service import create_token, _TOKEN_EXPIRY_SECONDS, require_auth
```

- [ ] **Step 4: Update `get_profile` in `routes/auth_routes.py`**

Replace lines 212-227:
```python
@router.get("/profile")
async def get_profile(_auth: dict = Depends(require_auth)):
    """Fetch current player profile data for pre-filling the form."""
    from services.db_service import get_player_by_id
    player = get_player_by_id(_auth["sub"])
    if not player:
        return JSONResponse(status_code=404, content={"error": "Player not found."})

    return {
        "playerId": str(_auth["sub"]),
        "fullName": player.get("fullName"),
        "nickName": player.get("nickName"),
        "email": player.get("email"),
        "role": player.get("role", "user"),
        "mfa_enabled": bool(player.get("mfa_enabled"))
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_auth.py::test_api_profile_requires_auth tests/test_auth.py::test_api_profile_returns_own_data -v
```

Expected: both PASS.

- [ ] **Step 6: Update `syncProfile` in `static/js/auth_service.js`**

Replace lines 63-82:
```javascript
async syncProfile() {
    const token = this.getToken();
    if (!token) return null;
    try {
        const resp = await fetch('/api/profile', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (resp.ok) {
            const data = await resp.json();
            this.setCredentials({
                playerId: data.playerId,
                playerName: data.fullName,
                nickName: data.nickName,
                email: data.email,
                role: data.role
            });
            return data;
        }
    } catch (e) {
        console.error('[Auth] Profile sync failed', e);
    }
    return null;
}
```

- [ ] **Step 7: Update the `syncProfile` call site in `static/js/main.js`**

Current line 53:
```javascript
AuthService.syncProfile(this.user.playerId),
```

Replace with:
```javascript
AuthService.syncProfile(),
```

- [ ] **Step 8: Run full test suite**

```
pytest tests/ -q
```

Expected: no failures.

- [ ] **Step 9: Commit**

```
git add routes/auth_routes.py static/js/auth_service.js static/js/main.js tests/test_auth.py
git commit -m "fix: require auth on /api/profile, derive player from JWT sub (#42)"
```

---

## Verification

After both tasks are committed, run the full suite once more:

```
pytest tests/ -q
```

Expected output ends with something like `X passed, 0 failed`.

Close issues on GitHub:
```
gh issue close 42 --comment "Fixed: added require_auth dependency; player ID now derived from JWT sub claim."
gh issue close 60 --comment "Fixed: get_player_by_id and get_player_by_email now check _DATA_CACHE before hitting Firestore."
```
