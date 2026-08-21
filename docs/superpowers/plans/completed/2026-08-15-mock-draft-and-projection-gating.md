# Mock Draft & Projection Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop leaking win-projection numbers to non-admin sockets in the live draft room, and ship a standalone, no-login solo mock draft page where a player drafts against 9 model/consensus-driven bots and gets ranked at the end.

**Architecture:** Part A adds per-connection role tracking to the live draft room's `ConnectionManager` so `preseason_predictions` is stripped from every WebSocket send that isn't going to a verified admin. Part B is an entirely new, stateless subsystem (`services/mock_draft_service.py` + `routes/mock_draft_routes.py` + a standalone template/JS pair) that never touches the live draft's singleton state, Firestore writes, or auth wall — it reuses `draft_order_rules` for the pick sequence and `get_season_projection_legacy_shape` for projections, both already used by the real draft.

**Tech Stack:** FastAPI + Jinja2 + vanilla ES6 JS (existing stack, no new dependencies).

**Spec:** `docs/superpowers/specs/completed/2026-08-15-mock-draft-and-projection-gating-design.md`

## Global Constraints

- Win-projection numbers (`preseason_predictions` / `projected_wins` / `totalProjectedWins`) must never be serialized into a response or WebSocket message reaching a non-admin — enforced server-side, never left to client-side rendering choices alone.
- The mock draft page (`/mock-draft`) requires no login and writes nothing to Firestore or `.local_db`.
- The mock draft's bot-pick endpoint is fully stateless — no server-side session storage. The client is the source of truth for `wildcardsSoFar` / `botPicksRemaining` across calls.
- At least 2 of a mock draft's 27 bot picks must be wildcard (non-ranked) picks, guaranteed via a pity mechanic, not merely a per-pick probability.
- The mock draft's pick sequence comes from whichever season currently has `draft_order_rules` rows (decoupled from whichever season supplies team projections) — see spec's "Draft order" section.
- New Pydantic request models use camelCase field names (`availableTeams`, `wildcardsSoFar`, etc.), matching every existing model in `routes/models.py`.
- Route error responses use `services/response_helpers.py` (`error_response`, `server_error`), matching every existing router.

---

## Part A — Server-side projection gating on the live draft room

### Task 1: `strip_admin_only_fields()` helper

**Files:**
- Modify: `services/draft_service.py`
- Test: `tests/test_draft_service.py`

**Interfaces:**
- Produces: `strip_admin_only_fields(payload: dict) -> dict` — returns `payload` unchanged if it has no `preseason_predictions` key, otherwise a shallow copy with that key replaced by `{}`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_draft_service.py`:

```python
class TestStripAdminOnlyFields:

    def test_strips_preseason_predictions(self):
        from services.draft_service import strip_admin_only_fields
        payload = {"season": 2026, "preseason_predictions": {"KC": {"projected_wins": 11.2}}}
        result = strip_admin_only_fields(payload)
        assert result["preseason_predictions"] == {}
        assert result["season"] == 2026

    def test_leaves_payload_without_predictions_key_untouched(self):
        from services.draft_service import strip_admin_only_fields
        payload = {"season": 2026, "draft_board": []}
        result = strip_admin_only_fields(payload)
        assert result == payload

    def test_does_not_mutate_original_payload(self):
        from services.draft_service import strip_admin_only_fields
        payload = {"preseason_predictions": {"KC": {"projected_wins": 11.2}}}
        strip_admin_only_fields(payload)
        assert payload["preseason_predictions"] == {"KC": {"projected_wins": 11.2}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_draft_service.py::TestStripAdminOnlyFields -v`
Expected: FAIL with `ImportError: cannot import name 'strip_admin_only_fields'`

- [ ] **Step 3: Write minimal implementation**

Add to `services/draft_service.py`, after `sanitize_state()`:

```python
def strip_admin_only_fields(payload: dict) -> dict:
    """Returns a copy of a draft-state payload with admin-only projection
    data removed. A no-op (returns the same object) if the payload has no
    preseason_predictions key, so callers never accidentally add a key that
    wasn't there — see draft_routes.ConnectionManager.broadcast().
    """
    if "preseason_predictions" not in payload:
        return payload
    return {**payload, "preseason_predictions": {}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_draft_service.py::TestStripAdminOnlyFields -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/draft_service.py tests/test_draft_service.py
git commit -m "feat: add strip_admin_only_fields helper for draft state payloads"
```

---

### Task 2: `ConnectionManager` admin tracking + stripped broadcast

**Files:**
- Modify: `routes/draft_routes.py`
- Test: `tests/test_draft_websocket.py`

**Interfaces:**
- Consumes: `strip_admin_only_fields(payload: dict) -> dict` from Task 1.
- Produces: `ConnectionManager.set_admin(ws, is_admin: bool) -> None`; `ConnectionManager.admin_sockets: set`. `broadcast()`'s behavior change (per-recipient stripping) is consumed by Task 3, which drives admin status.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_draft_websocket.py`, inside `TestConnectionManager`:

```python
    def test_set_admin_true_adds_to_admin_sockets(self):
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.set_admin(ws, True)
        assert ws in mgr.admin_sockets

    def test_set_admin_false_removes_from_admin_sockets(self):
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.admin_sockets.add(ws)
        mgr.set_admin(ws, False)
        assert ws not in mgr.admin_sockets

    def test_disconnect_also_clears_admin_sockets(self):
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.active_connections.append(ws)
        mgr.admin_sockets.add(ws)
        mgr.disconnect(ws)
        assert ws not in mgr.admin_sockets

    def test_broadcast_state_strips_predictions_for_non_admin_only(self):
        """A 'state' broadcast with preseason_predictions goes full to admins, stripped to everyone else."""
        mgr = ConnectionManager()
        admin_ws, player_ws = AsyncMock(), AsyncMock()
        mgr.active_connections = [admin_ws, player_ws]
        mgr.set_admin(admin_ws, True)
        payload = {"season": 2026, "preseason_predictions": {"KC": {"projected_wins": 11.2}}}
        asyncio.run(mgr.broadcast({"type": "state", "payload": payload}))

        admin_sent = admin_ws.send_json.call_args[0][0]
        player_sent = player_ws.send_json.call_args[0][0]
        assert admin_sent["payload"]["preseason_predictions"] == {"KC": {"projected_wins": 11.2}}
        assert player_sent["payload"]["preseason_predictions"] == {}

    def test_broadcast_state_without_predictions_key_is_unaffected(self):
        """Existing behavior (no predictions key) must be unchanged for every recipient."""
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        mgr.active_connections = [ws1, ws2]
        asyncio.run(mgr.broadcast({"type": "state", "payload": {}}))
        ws1.send_json.assert_awaited_once_with({"type": "state", "payload": {}})
        ws2.send_json.assert_awaited_once_with({"type": "state", "payload": {}})

    def test_broadcast_non_state_message_is_unaffected(self):
        """Non-'state' messages (chat, errors) are sent identically to everyone regardless of admin status."""
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        mgr.active_connections = [ws1, ws2]
        asyncio.run(mgr.broadcast({"type": "chat_message", "text": "hi"}))
        ws1.send_json.assert_awaited_once_with({"type": "chat_message", "text": "hi"})
        ws2.send_json.assert_awaited_once_with({"type": "chat_message", "text": "hi"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_draft_websocket.py::TestConnectionManager -v`
Expected: FAIL — `set_admin` and `admin_sockets` don't exist yet; the strip tests fail because `broadcast()` doesn't differentiate recipients.

- [ ] **Step 3: Write minimal implementation**

In `routes/draft_routes.py`, add the import and replace the `ConnectionManager` class:

```python
from services.draft_service import load_draft_state, save_pick, undo_pick, reset_pick, strip_admin_only_fields
```

```python
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.admin_sockets: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)
        self.admin_sockets.discard(ws)

    def set_admin(self, ws: WebSocket, is_admin: bool):
        """Record whether this connection has authenticated as an admin.

        Drives per-recipient stripping in broadcast() — new connections
        default to non-admin (the safe default) until this is called.
        """
        if is_admin:
            self.admin_sockets.add(ws)
        else:
            self.admin_sockets.discard(ws)

    async def broadcast(self, message: dict):
        stripped = message
        if message.get("type") == "state" and "preseason_predictions" in message.get("payload", {}):
            stripped = {**message, "payload": strip_admin_only_fields(message["payload"])}
        for conn in self.active_connections:
            out = message if conn in self.admin_sockets else stripped
            try:
                await conn.send_json(out)
            except Exception:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_draft_websocket.py -v`
Expected: PASS (all tests, including the pre-existing `TestConnectionManager`/`TestWebSocketVerifyCode`/etc. suites — this step must not regress them)

- [ ] **Step 5: Commit**

```bash
git add routes/draft_routes.py tests/test_draft_websocket.py
git commit -m "feat: strip draft projections from non-admin WebSocket broadcasts"
```

---

### Task 3: Wire admin detection into the live socket lifecycle

**Files:**
- Modify: `routes/draft_routes.py`
- Test: `tests/test_draft_websocket.py`

**Interfaces:**
- Consumes: `ConnectionManager.set_admin()` and `strip_admin_only_fields()` from Tasks 1-2. `_get_authenticated_admin(socket_player_id, all_players)` (existing).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_draft_websocket.py`, a new class:

```python
PROJECTION_FAKE_STATE = {
    "season": 2025,
    "active_pick": 3,
    "preseason_predictions": {"KC": {"projected_wins": 11.2}},
    "all_players": [
        {"playerId": 1, "fullName": "Admin User", "role": "admin"},
        {"playerId": 2, "fullName": "Regular Player", "role": "user"},
    ],
    "picks": [],
    "draft_order": [],
}


@pytest.fixture
def ws_projection_client():
    with patch("routes.draft_routes.load_draft_state", return_value=PROJECTION_FAKE_STATE), \
         patch("routes.draft_routes.save_pick"), \
         patch("routes.draft_routes.undo_pick"), \
         patch("routes.draft_routes.reset_pick"):
        from main import app
        yield TestClient(app)


class TestWebSocketProjectionGating:

    def test_initial_state_before_auth_has_predictions_stripped(self, ws_projection_client):
        """A brand-new connection (not yet verified) must never see raw predictions."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
        assert msg["payload"]["preseason_predictions"] == {}

    def test_admin_verify_code_receives_full_predictions(self, ws_projection_client):
        """After verifying as an admin player, the resulting state broadcast is unstripped."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state (stripped)
            ws.receive_json()  # chat_history
            ws.send_json({"action": "verify_code", "playerId": 1, "code": "test"})
            msgs = [ws.receive_json(), ws.receive_json()]  # verified + state broadcast
        state_msg = next(m for m in msgs if m["type"] == "state")
        assert state_msg["payload"]["preseason_predictions"] == {"KC": {"projected_wins": 11.2}}

    def test_player_verify_code_receives_stripped_predictions(self, ws_projection_client):
        """A non-admin player's post-verify state broadcast is still stripped."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state (stripped)
            ws.receive_json()  # chat_history
            ws.send_json({"action": "verify_code", "playerId": 2, "code": "test"})
            msgs = [ws.receive_json(), ws.receive_json()]  # verified + state broadcast
        state_msg = next(m for m in msgs if m["type"] == "state")
        assert state_msg["payload"]["preseason_predictions"] == {}

    def test_switch_season_direct_send_respects_admin_status(self, ws_projection_client):
        """switch_season bypasses broadcast() with a direct send — must also be gated."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state
            ws.receive_json()  # chat_history
            ws.send_json({"action": "verify_code", "playerId": 2, "code": "test"})
            ws.receive_json()  # verified
            ws.receive_json()  # state broadcast after verify
            ws.send_json({"action": "switch_season", "year": 2025})
            msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["payload"]["preseason_predictions"] == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_draft_websocket.py::TestWebSocketProjectionGating -v`
Expected: FAIL — initial state and post-verify broadcasts are currently unstripped for everyone.

- [ ] **Step 3: Write minimal implementation**

In `routes/draft_routes.py`, inside `websocket_endpoint`:

Replace the initial state send:

```python
        initial_state = load_draft_state(connected_players)
        await websocket.send_json({"type": "state", "payload": initial_state})
```

with:

```python
        initial_state = load_draft_state(connected_players)
        await websocket.send_json({"type": "state", "payload": strip_admin_only_fields(initial_state)})
```

Replace the `switch_season` handler:

```python
            if action == "switch_season":
                yr = msg.get("year")
                if yr:
                    current_view_year = int(yr)
                    await websocket.send_json({"type": "state", "payload": load_draft_state(connected_players, year=current_view_year)})
                continue
```

with:

```python
            if action == "switch_season":
                yr = msg.get("year")
                if yr:
                    current_view_year = int(yr)
                    switched_state = load_draft_state(connected_players, year=current_view_year)
                    payload = switched_state if websocket in manager.admin_sockets else strip_admin_only_fields(switched_state)
                    await websocket.send_json({"type": "state", "payload": payload})
                continue
```

Replace the `verify_code` handler's success branch:

```python
                    if str(code).strip().lower() == ROOM_CODE:
                        connected_players.add(pid)
                        socket_player_id = pid
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
```

with:

```python
                    if str(code).strip().lower() == ROOM_CODE:
                        connected_players.add(pid)
                        socket_player_id = pid
                        new_state = load_draft_state(connected_players, year=target_year)
                        is_admin = _get_authenticated_admin(pid, new_state["all_players"]) is not None
                        manager.set_admin(websocket, is_admin)
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": new_state})
```

Replace the `reauthenticate` handler's success branch. Note this snippet's
last 4 lines are textually identical to the `verify_code` snippet above —
apply this edit **after** the `verify_code` edit above (so it's the only
remaining match), or if using a string-replace tool that requires
uniqueness, include the `if not player or not player.get("password_hash")`
line above it as extra context to disambiguate:

```python
                        if not player or not player.get("password_hash"):
                            await websocket.send_json({"type": "error", "message": "Session expired. Please log in again."})
                            continue
                        connected_players.add(pid)
                        socket_player_id = pid
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
```

with:

```python
                        if not player or not player.get("password_hash"):
                            await websocket.send_json({"type": "error", "message": "Session expired. Please log in again."})
                            continue
                        connected_players.add(pid)
                        socket_player_id = pid
                        new_state = load_draft_state(connected_players, year=target_year)
                        is_admin = _get_authenticated_admin(pid, new_state["all_players"]) is not None
                        manager.set_admin(websocket, is_admin)
                        await websocket.send_json({"type": "verified", "playerId": pid})
                        await manager.broadcast({"type": "state", "payload": new_state})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_draft_websocket.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: PASS (no unrelated regressions)

- [ ] **Step 6: Commit**

```bash
git add routes/draft_routes.py tests/test_draft_websocket.py
git commit -m "feat: gate live draft projections behind verified admin status"
```

---

## Part B — Solo mock draft

### Task 4: Non-raising admin-session check

**Files:**
- Modify: `services/session_service.py`
- Test: `tests/test_session_service.py`

**Interfaces:**
- Produces: `get_is_admin(authorization: str = Header(default=None), session_token: str = Cookie(default=None)) -> bool` — a FastAPI dependency that never raises; `True` only for a valid, non-expired admin JWT.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_session_service.py`:

```python
# ── get_is_admin() — non-raising admin check for anonymous-friendly endpoints ─

def test_get_is_admin_true_for_valid_admin_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=1, role="admin")
    assert session_service.get_is_admin(authorization=f"Bearer {token}", session_token=None) is True


def test_get_is_admin_false_for_valid_non_admin_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=2, role="user")
    assert session_service.get_is_admin(authorization=f"Bearer {token}", session_token=None) is False


def test_get_is_admin_false_when_no_token_present(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    assert session_service.get_is_admin(authorization=None, session_token=None) is False


def test_get_is_admin_false_for_malformed_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    assert session_service.get_is_admin(authorization="Bearer not-a-real-jwt", session_token=None) is False


def test_get_is_admin_reads_session_cookie_when_no_header(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-is-admin")
    from services import session_service
    token = session_service.create_token(player_id=1, role="admin")
    assert session_service.get_is_admin(authorization=None, session_token=token) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_session_service.py -k get_is_admin -v`
Expected: FAIL with `AttributeError: module 'services.session_service' has no attribute 'get_is_admin'`

- [ ] **Step 3: Write minimal implementation**

Add to `services/session_service.py`, after `require_admin`:

```python
def get_is_admin(
    authorization: str = Header(default=None),
    session_token: str = Cookie(default=None),
) -> bool:
    """FastAPI dependency: True if the request carries a valid, non-expired
    admin JWT (Bearer header or session cookie). Never raises — for
    endpoints that must work for anonymous callers and only conditionally
    include admin-only data (e.g. the mock draft).
    """
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ")
    elif session_token:
        token = session_token
    if not token:
        return False
    try:
        payload = decode_token(token)
    except Exception:
        return False
    return payload.get("role") == "admin"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_session_service.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add services/session_service.py tests/test_session_service.py
git commit -m "feat: add non-raising get_is_admin session dependency"
```

---

### Task 5: Mock draft pick-sequence derivation

**Files:**
- Create: `services/mock_draft_service.py`
- Test: `tests/test_mock_draft_service.py`

**Interfaces:**
- Produces: `NFL_TEAMS: list[str]` (32 team codes); `get_pick_sequence() -> list[dict]` (30 entries `{"pick": int, "slot": int}`, sorted by `pick`); `get_projection_season() -> int`. Both raise `ValueError` when their source collection is empty.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mock_draft_service.py`:

```python
"""Tests for services/mock_draft_service.py — pick sequence, bot picks, rankings."""
import pandas as pd
import pytest
from unittest.mock import patch


class TestGetPickSequence:

    def test_returns_30_entries_sorted_by_pick(self):
        from services.mock_draft_service import get_pick_sequence
        rules_df = pd.DataFrame([
            {"season": 2025, "draftOrder": 1, "pickOne": 1, "pickTwo": 20, "pickThree": 26},
            {"season": 2025, "draftOrder": 2, "pickOne": 2, "pickTwo": 19, "pickThree": 27},
            {"season": 2026, "draftOrder": 1, "pickOne": 3, "pickTwo": 18, "pickThree": 28},
        ])
        with patch("services.mock_draft_service.get_collection_df", return_value=rules_df):
            seq = get_pick_sequence()
        # Uses the most recent season present (2026) — only 1 slot there, 3 picks.
        assert len(seq) == 3
        assert [e["pick"] for e in seq] == sorted(e["pick"] for e in seq)
        assert all(e["slot"] == 1 for e in seq)

    def test_uses_most_recent_season_with_rules(self):
        from services.mock_draft_service import get_pick_sequence
        rules_df = pd.DataFrame([
            {"season": 2024, "draftOrder": 1, "pickOne": 99, "pickTwo": 98, "pickThree": 97},
            {"season": 2025, "draftOrder": 1, "pickOne": 1, "pickTwo": 20, "pickThree": 26},
        ])
        with patch("services.mock_draft_service.get_collection_df", return_value=rules_df):
            seq = get_pick_sequence()
        assert {e["pick"] for e in seq} == {1, 20, 26}

    def test_raises_value_error_when_no_rules_configured(self):
        from services.mock_draft_service import get_pick_sequence
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            with pytest.raises(ValueError):
                get_pick_sequence()


class TestGetProjectionSeason:

    def test_returns_max_season_in_draft_order(self):
        from services.mock_draft_service import get_projection_season
        order_df = pd.DataFrame([{"season": 2024, "playerId": 1}, {"season": 2026, "playerId": 2}])
        with patch("services.mock_draft_service.get_collection_df", return_value=order_df):
            assert get_projection_season() == 2026

    def test_raises_value_error_when_no_draft_order_configured(self):
        from services.mock_draft_service import get_projection_season
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            with pytest.raises(ValueError):
                get_projection_season()


class TestNflTeams:

    def test_has_32_unique_teams(self):
        from services.mock_draft_service import NFL_TEAMS
        assert len(NFL_TEAMS) == 32
        assert len(set(NFL_TEAMS)) == 32
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mock_draft_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.mock_draft_service'`

- [ ] **Step 3: Write minimal implementation**

Create `services/mock_draft_service.py`:

```python
"""services/mock_draft_service.py — Solo mock draft: pick sequencing and bot picks.

Fully stateless — every function here is a pure read against Firestore/pkl
(via get_collection_df / get_season_projection_legacy_shape) plus in-memory
computation. Nothing here writes to the database.
"""
import random
from typing import Dict, List, Tuple

from services.data_service import get_season_projection_legacy_shape
from services.db_service import get_collection_df

NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB",
    "TEN", "WAS",
]

WILDCARD_PROBABILITY = 0.08
MIN_WILDCARDS_PER_DRAFT = 2


def get_pick_sequence() -> List[Dict[str, int]]:
    """Derive the 30-pick sequence (pick number -> draft slot 1-10) from
    draft_order_rules, using whichever season currently has rows.

    Deliberately decoupled from the season used for team projections: the
    pickOne/pickTwo/pickThree pattern is copied forward season to season
    (see routes/admin_routes.py::create_new_season), so any available
    season's rules produce the same slot structure, and the mock draft
    keeps working even if the target season's rules get wiped.
    """
    rules_df = get_collection_df("draft_order_rules")
    if rules_df.empty:
        raise ValueError("No draft_order_rules configured for any season.")

    season = int(rules_df["season"].max())
    season_rules = rules_df[rules_df["season"] == season]

    entries = []
    for _, row in season_rules.iterrows():
        slot = int(row["draftOrder"])
        for pick_col in ("pickOne", "pickTwo", "pickThree"):
            entries.append({"pick": int(row[pick_col]), "slot": slot})
    entries.sort(key=lambda e: e["pick"])
    return entries


def get_projection_season() -> int:
    """The season whose team win projections the mock draft should use —
    the most recent season present in draft_order.
    """
    order_df = get_collection_df("draft_order")
    if order_df.empty:
        raise ValueError("No draft_order configured for any season.")
    return int(order_df["season"].max())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mock_draft_service.py -v`
Expected: PASS (all tests in `TestGetPickSequence`, `TestGetProjectionSeason`, `TestNflTeams`)

- [ ] **Step 5: Commit**

```bash
git add services/mock_draft_service.py tests/test_mock_draft_service.py
git commit -m "feat: derive mock draft pick sequence from draft_order_rules"
```

---

### Task 6: Bot pick algorithm with guaranteed minimum wildcards

**Files:**
- Modify: `services/mock_draft_service.py`
- Test: `tests/test_mock_draft_service.py`

**Interfaces:**
- Consumes: `get_season_projection_legacy_shape(season)` (existing, from `services/data_service.py`).
- Produces: `bot_pick(season: int, available_teams: list[str], wildcards_so_far: int, bot_picks_remaining: int) -> tuple[str, bool]` — `(team, was_wildcard)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mock_draft_service.py`:

```python
class TestBotPick:

    def test_returns_team_from_available_teams(self):
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            for _ in range(50):
                team, _ = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=5, bot_picks_remaining=10)
                assert team in ["KC", "DAL"]

    def test_falls_back_to_uniform_random_when_no_projections(self):
        from services.mock_draft_service import bot_pick
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={}):
            team, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=5, bot_picks_remaining=10)
        assert team in ["KC", "DAL"]
        assert was_wildcard is False

    def test_forces_wildcard_when_shortfall_equals_remaining_picks(self):
        """wildcardsSoFar=0, botPicksRemaining=1 with MIN=2 -> needed(2) >= remaining(1) -> forced."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=0, bot_picks_remaining=1)
        assert was_wildcard is True

    def test_forces_wildcard_at_exact_boundary(self):
        """wildcardsSoFar=1, botPicksRemaining=1 -> needed(1) >= remaining(1) -> forced."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=1, bot_picks_remaining=1)
        assert was_wildcard is True

    def test_does_not_force_wildcard_once_minimum_already_met(self):
        """wildcardsSoFar=2 (minimum already hit) -> needed=0 -> never forced; disable the random roll to prove it."""
        from services.mock_draft_service import bot_pick
        projections = {"KC": {"projected_wins": 11.2}, "DAL": {"projected_wins": 9.1}}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections), \
             patch("services.mock_draft_service.random.random", return_value=0.99):
            _, was_wildcard = bot_pick(2026, ["KC", "DAL"], wildcards_so_far=2, bot_picks_remaining=1)
        assert was_wildcard is False

    def test_full_draft_simulation_hits_minimum_wildcards(self):
        """Simulate 27 bot picks (a full mock draft's bot slots) many times; every run has >= 2 wildcards."""
        from services.mock_draft_service import bot_pick, MIN_WILDCARDS_PER_DRAFT
        projections = {t: {"projected_wins": 32 - i} for i, t in enumerate(
            ["KC", "DAL", "SF", "BUF", "PHI", "BAL", "DET", "MIA", "GB", "LA",
             "CIN", "HOU", "MIN", "NYJ", "LAC", "PIT", "SEA", "TB", "IND", "DEN",
             "NO", "ATL", "CHI", "ARI", "WAS", "CLE", "NYG", "TEN", "JAX", "CAR",
             "NE", "LV"]
        )}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            for _ in range(20):  # repeat to cover the probabilistic (non-forced) path too
                available = list(projections.keys())
                wildcards_so_far = 0
                total_bot_picks = 27
                for i in range(total_bot_picks):
                    remaining = total_bot_picks - i
                    team, was_wildcard = bot_pick(2026, available, wildcards_so_far, remaining)
                    available.remove(team)
                    if was_wildcard:
                        wildcards_so_far += 1
                assert wildcards_so_far >= MIN_WILDCARDS_PER_DRAFT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mock_draft_service.py::TestBotPick -v`
Expected: FAIL with `ImportError: cannot import name 'bot_pick'`

- [ ] **Step 3: Write minimal implementation**

Add to `services/mock_draft_service.py`:

```python
def _weighted_rank_pick(ranked_teams: List[str]) -> str:
    """Weighted-random pick from a projection-ranked list: the top team is
    most likely, decaying geometrically down the list, rather than always
    taking the single best team (so 9 bots don't draft identically).
    """
    weights = [0.6 ** i for i in range(len(ranked_teams))]
    total = sum(weights)
    roll = random.random() * total
    cumulative = 0.0
    for team, weight in zip(ranked_teams, weights):
        cumulative += weight
        if roll <= cumulative:
            return team
    return ranked_teams[-1]


def bot_pick(
    season: int,
    available_teams: List[str],
    wildcards_so_far: int,
    bot_picks_remaining: int,
) -> Tuple[str, bool]:
    """Choose a team for a bot-controlled mock draft slot.

    Returns (team, was_wildcard). Guarantees at least MIN_WILDCARDS_PER_DRAFT
    wildcard picks across a full draft's worth of calls via a pity mechanic:
    once the remaining bot picks can no longer make up the shortfall against
    the minimum, this pick is forced to be a wildcard.
    """
    if not available_teams:
        raise ValueError("available_teams must not be empty.")

    needed = max(0, MIN_WILDCARDS_PER_DRAFT - wildcards_so_far)
    forced = needed >= bot_picks_remaining
    was_wildcard = forced or random.random() < WILDCARD_PROBABILITY

    if was_wildcard:
        return random.choice(available_teams), True

    projections = get_season_projection_legacy_shape(season)
    if not projections:
        return random.choice(available_teams), False

    ranked = sorted(
        available_teams,
        key=lambda t: (projections.get(t) or {}).get("projected_wins", 0) or 0,
        reverse=True,
    )
    return _weighted_rank_pick(ranked), False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mock_draft_service.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add services/mock_draft_service.py tests/test_mock_draft_service.py
git commit -m "feat: add mock draft bot pick algorithm with guaranteed wildcards"
```

---

### Task 7: End-of-draft roster ranking

**Files:**
- Modify: `services/mock_draft_service.py`
- Test: `tests/test_mock_draft_service.py`

**Interfaces:**
- Consumes: `get_season_projection_legacy_shape(season)` (existing).
- Produces: `rank_rosters(season: int, rosters: dict[str, list[str]]) -> list[dict]` — each entry `{"slot": int, "totalProjectedWins": float, "rank": int}`, sorted by `rank` ascending (1 = highest total).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mock_draft_service.py`:

```python
class TestRankRosters:

    def test_ranks_highest_total_first(self):
        from services.mock_draft_service import rank_rosters
        projections = {
            "KC": {"projected_wins": 11.0}, "DAL": {"projected_wins": 9.0},
            "NE": {"projected_wins": 4.0}, "LV": {"projected_wins": 3.0},
        }
        rosters = {"1": ["KC", "DAL"], "2": ["NE", "LV"]}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value=projections):
            result = rank_rosters(2026, rosters)
        by_slot = {r["slot"]: r for r in result}
        assert by_slot[1]["rank"] == 1
        assert by_slot[1]["totalProjectedWins"] == 20.0
        assert by_slot[2]["rank"] == 2
        assert by_slot[2]["totalProjectedWins"] == 7.0

    def test_missing_projection_counts_as_zero(self):
        from services.mock_draft_service import rank_rosters
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={}):
            result = rank_rosters(2026, {"1": ["KC", "DAL"]})
        assert result[0]["totalProjectedWins"] == 0.0
        assert result[0]["rank"] == 1

    def test_result_length_matches_roster_count(self):
        from services.mock_draft_service import rank_rosters
        rosters = {str(i): ["KC"] for i in range(1, 11)}
        with patch("services.mock_draft_service.get_season_projection_legacy_shape", return_value={"KC": {"projected_wins": 5.0}}):
            result = rank_rosters(2026, rosters)
        assert len(result) == 10
        assert {r["rank"] for r in result} == set(range(1, 11))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mock_draft_service.py::TestRankRosters -v`
Expected: FAIL with `ImportError: cannot import name 'rank_rosters'`

- [ ] **Step 3: Write minimal implementation**

Add to `services/mock_draft_service.py`:

```python
def rank_rosters(season: int, rosters: Dict[str, List[str]]) -> List[Dict]:
    """Rank each mock draft slot's 3-team roster by total projected wins.

    Returns one entry per slot: {"slot", "totalProjectedWins", "rank"},
    sorted by rank ascending (1 = highest total). Teams with no projection
    on record contribute 0.0, never an error.
    """
    projections = get_season_projection_legacy_shape(season)

    def total_wins(teams: List[str]) -> float:
        return sum((projections.get(t) or {}).get("projected_wins", 0) or 0 for t in teams)

    totals = [
        {"slot": int(slot), "totalProjectedWins": round(total_wins(teams), 1)}
        for slot, teams in rosters.items()
    ]
    totals.sort(key=lambda r: r["totalProjectedWins"], reverse=True)
    for idx, row in enumerate(totals):
        row["rank"] = idx + 1
    return totals
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mock_draft_service.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add services/mock_draft_service.py tests/test_mock_draft_service.py
git commit -m "feat: add mock draft end-of-draft roster ranking"
```

---

### Task 8: Mock draft API routes

**Files:**
- Modify: `routes/models.py`
- Create: `routes/mock_draft_routes.py`
- Modify: `main.py`
- Test: `tests/test_mock_draft.py`

**Interfaces:**
- Consumes: `get_is_admin` (Task 4), `NFL_TEAMS` / `get_pick_sequence` / `get_projection_season` / `bot_pick` / `rank_rosters` (Tasks 5-7), `get_season_projection_legacy_shape` (existing), `error_response` / `server_error` (existing `services/response_helpers.py`).
- Produces: `GET /api/mock-draft/setup`, `POST /api/mock-draft/pick`, `POST /api/mock-draft/results` — consumed by the frontend in Tasks 9-10.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mock_draft.py`:

```python
"""tests/test_mock_draft.py — /api/mock-draft/* route tests."""
import pandas as pd
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app

client = TestClient(app)

RULES_DF = pd.DataFrame([
    {"season": 2026, "draftOrder": i, "pickOne": i, "pickTwo": 21 - i, "pickThree": 20 + i}
    for i in range(1, 11)
])
ORDER_DF = pd.DataFrame([{"season": 2026, "playerId": i, "draftOrder": i} for i in range(1, 11)])
PROJECTIONS = {"KC": {"projected_wins": 11.2, "std_dev": 1.1}, "DAL": {"projected_wins": 9.0, "std_dev": 1.4}}


def _mock_collection_df(name, filters=None):
    if name == "draft_order_rules":
        return RULES_DF
    if name == "draft_order":
        return ORDER_DF
    return pd.DataFrame()


class TestMockDraftSetup:

    def test_non_admin_setup_has_no_projections_key(self):
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df), \
             patch("routes.mock_draft_routes.get_season_projection_legacy_shape", return_value=PROJECTIONS):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 200
        data = resp.json()
        assert "projections" not in data
        assert len(data["pickSequence"]) == 30
        assert data["season"] == 2026
        assert len(data["teams"]) == 32

    def test_admin_setup_includes_projections(self, admin_token):
        with patch("services.mock_draft_service.get_collection_df", side_effect=_mock_collection_df), \
             patch("routes.mock_draft_routes.get_season_projection_legacy_shape", return_value=PROJECTIONS):
            resp = client.get("/api/mock-draft/setup", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        assert resp.json()["projections"] == PROJECTIONS

    def test_setup_returns_400_when_no_rules_configured(self):
        with patch("services.mock_draft_service.get_collection_df", return_value=pd.DataFrame()):
            resp = client.get("/api/mock-draft/setup")
        assert resp.status_code == 400


class TestMockDraftPick:

    def test_pick_returns_team_from_available_teams(self):
        with patch("routes.mock_draft_routes.bot_pick", return_value=("KC", False)):
            resp = client.post("/api/mock-draft/pick", json={
                "season": 2026, "availableTeams": ["KC", "DAL"],
                "wildcardsSoFar": 0, "botPicksRemaining": 10,
            })
        assert resp.status_code == 200
        assert resp.json() == {"team": "KC", "wasWildcard": False}

    def test_pick_rejects_empty_available_teams(self):
        resp = client.post("/api/mock-draft/pick", json={
            "season": 2026, "availableTeams": [], "wildcardsSoFar": 0, "botPicksRemaining": 10,
        })
        assert resp.status_code == 400


class TestMockDraftResults:

    def test_non_admin_results_have_rank_only(self):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1},
            {"slot": 2, "totalProjectedWins": 7.0, "rank": 2},
        ]):
            resp = client.post("/api/mock-draft/results", json={
                "season": 2026, "rosters": {"1": ["KC", "DAL"], "2": ["NE", "LV"]},
            })
        assert resp.status_code == 200
        rankings = resp.json()["rankings"]
        assert all("totalProjectedWins" not in r for r in rankings)
        assert {r["rank"] for r in rankings} == {1, 2}

    def test_admin_results_include_totals(self, admin_token):
        with patch("routes.mock_draft_routes.rank_rosters", return_value=[
            {"slot": 1, "totalProjectedWins": 20.2, "rank": 1},
        ]):
            resp = client.post(
                "/api/mock-draft/results",
                json={"season": 2026, "rosters": {"1": ["KC", "DAL"]}},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json()["rankings"][0]["totalProjectedWins"] == 20.2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mock_draft.py -v`
Expected: FAIL — `/api/mock-draft/*` routes don't exist (404s), `routes.mock_draft_routes` doesn't exist.

- [ ] **Step 3: Write minimal implementation**

Add to `routes/models.py`:

```python
class MockDraftPickRequest(BaseModel):
    season: int
    availableTeams: List[str]
    wildcardsSoFar: int = 0
    botPicksRemaining: int = 1


class MockDraftResultsRequest(BaseModel):
    season: int
    rosters: Dict[str, List[str]]
```

(`List` and `Dict` are already imported at the top of `routes/models.py`.)

Create `routes/mock_draft_routes.py`:

```python
"""routes/mock_draft_routes.py — Solo mock draft: setup, bot picks, end-of-draft ranking.

Fully stateless and unauthenticated -- no session, no DB writes. Team win
projections are only ever included in a response when the requester's
session resolves to an admin (services.session_service.get_is_admin);
everyone else gets picks/ranks with the underlying numbers never
serialized, not merely hidden client-side.
"""
import logging

from fastapi import APIRouter, Depends

from routes.models import MockDraftPickRequest, MockDraftResultsRequest
from services.data_service import get_season_projection_legacy_shape
from services.mock_draft_service import (
    NFL_TEAMS, bot_pick, get_pick_sequence, get_projection_season, rank_rosters,
)
from services.response_helpers import error_response, server_error
from services.session_service import get_is_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mock-draft")


@router.get("/setup")
async def mock_draft_setup(is_admin: bool = Depends(get_is_admin)):
    try:
        pick_sequence = get_pick_sequence()
        season = get_projection_season()
    except ValueError as e:
        return error_response(str(e), 400)
    except Exception:
        logger.exception("Unhandled error building mock draft setup")
        return server_error("Failed to build mock draft setup.")

    content = {"pickSequence": pick_sequence, "teams": NFL_TEAMS, "season": season}
    if is_admin:
        content["projections"] = get_season_projection_legacy_shape(season)
    return content


@router.post("/pick")
async def mock_draft_pick(body: MockDraftPickRequest):
    if not body.availableTeams:
        return error_response("availableTeams must not be empty.", 400)
    try:
        team, was_wildcard = bot_pick(
            body.season, body.availableTeams, body.wildcardsSoFar, body.botPicksRemaining
        )
        return {"team": team, "wasWildcard": was_wildcard}
    except Exception:
        logger.exception("Unhandled error computing mock draft bot pick")
        return server_error("Failed to compute bot pick.")


@router.post("/results")
async def mock_draft_results(body: MockDraftResultsRequest, is_admin: bool = Depends(get_is_admin)):
    try:
        rankings = rank_rosters(body.season, body.rosters)
    except Exception:
        logger.exception("Unhandled error ranking mock draft rosters")
        return server_error("Failed to rank rosters.")

    if not is_admin:
        rankings = [{"slot": r["slot"], "rank": r["rank"]} for r in rankings]
    return {"rankings": rankings}
```

In `main.py`, add the import alongside the other routers:

```python
from routes.prediction_routes import router as prediction_router
from routes.mock_draft_routes import router as mock_draft_router
```

and register it alongside the others:

```python
app.include_router(prediction_router)
app.include_router(mock_draft_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mock_draft.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add routes/models.py routes/mock_draft_routes.py main.py tests/test_mock_draft.py
git commit -m "feat: add mock draft API routes (setup, pick, results)"
```

---

### Task 9: Mock draft page template + route

**Files:**
- Create: `templates/mock_draft.html`
- Modify: `routes/mock_draft_routes.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `GET /mock-draft` HTML page, served from a page-only router (mirrors `admin_routes.py`'s `_page_router` pattern — kept separate from the `/api/mock-draft` prefix used by Task 8's JSON routes).

This page deliberately does **not** extend `templates/base.html`: `base.html` loads `main.js`, whose default state adds a `show-signin` class that hides all page content behind a login wall (`templates/base.html:14-21`, `static/style.css:535-544`) until `AuthService` resolves a logged-in player. That's the opposite of the mock draft's "share a link, no login" requirement, so this page ships its own minimal `<head>`/`<body>` and only loads the new `mock_draft.js` (Task 10) — never `main.js`, `websocket_service.js`, or `auth_service.js`.

- [ ] **Step 1: Create the template**

Create `templates/mock_draft.html`:

```html
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mock Draft — WinsPool</title>
    <link rel="icon" href="/static/fishbone.png" type="image/png">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="/static/style.css?v=23">
    <style>
        .mock-shell { max-width: 720px; margin: 0 auto; padding: 24px 16px 80px; }
        .mock-slot-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin: 20px 0; }
        .mock-slot-btn {
            padding: 14px 0; border-radius: 10px; border: 1px solid var(--glass-border, rgba(255,255,255,0.15));
            background: rgba(255,255,255,0.06); color: inherit; font-weight: 600; cursor: pointer;
        }
        .mock-slot-btn.selected { background: var(--leader, #7c9fff); color: #10131c; }
        .mock-bot-turn { text-align: center; padding: 32px 0; opacity: 0.8; }
        .mock-rank-row { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.08); }
        .mock-rank-row.is-you { font-weight: 700; color: var(--leader, #7c9fff); }
        #mock-error { color: #ff8080; text-align: center; padding: 24px; }
    </style>
</head>

<body>
    <div class="glass-bg"></div>
    <div class="mock-shell">
        <header class="wp-top">
            <div>
                <div class="eyebrow">WinsPool · Practice</div>
                <h1 class="wp-h1">Mock Draft</h1>
            </div>
        </header>

        <div id="mock-error" class="hidden"></div>

        <section id="mock-slot-select">
            <p>Pick which draft spot you want, or let it be random.</p>
            <div class="mock-slot-grid" id="mock-slot-grid"></div>
            <button id="mock-random-slot" class="btn">Random Spot</button>
        </section>

        <section id="mock-draft-board" class="hidden">
            <div class="eyebrow" id="mock-status"></div>
            <div id="mock-bot-turn" class="mock-bot-turn hidden"></div>
            <div class="mock-team-grid" id="mock-team-grid" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;"></div>
        </section>

        <section id="mock-results" class="hidden">
            <h2>Draft Complete!</h2>
            <div id="mock-your-teams"></div>
            <div id="mock-rankings"></div>
            <button id="mock-again" class="btn">Draft Again</button>
        </section>
    </div>

    <script type="module" src="/static/js/mock_draft.js"></script>
</body>

</html>
```

- [ ] **Step 2: Add the page route**

Add to `routes/mock_draft_routes.py`, after the existing imports:

```python
from fastapi import Request
from fastapi.templating import Jinja2Templates

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")


@page_router.get("/mock-draft", include_in_schema=False)
async def serve_mock_draft(request: Request):
    return templates.TemplateResponse(request, "mock_draft.html", {})
```

- [ ] **Step 3: Register the page router in `main.py`**

```python
from routes.mock_draft_routes import router as mock_draft_router, page_router as mock_draft_page_router
```

```python
app.include_router(mock_draft_router)
app.include_router(mock_draft_page_router)
```

- [ ] **Step 4: Manually verify the page loads**

Run: `uvicorn main:app --reload`
Visit `http://localhost:8000/mock-draft` in a browser (no login).
Expected: page renders with the "Mock Draft" header and a 5x2 slot-selection grid, no sign-in wall, no console errors about `main.js` (it isn't loaded). The team grid and results sections stay hidden until Task 10 wires up the JS.

- [ ] **Step 5: Commit**

```bash
git add templates/mock_draft.html routes/mock_draft_routes.py main.py
git commit -m "feat: add standalone mock draft page (no login required)"
```

---

### Task 10: Mock draft frontend logic

**Files:**
- Create: `static/js/mock_draft.js`

**Interfaces:**
- Consumes: `GET /api/mock-draft/setup`, `POST /api/mock-draft/pick`, `POST /api/mock-draft/results` (Task 8). DOM elements from `templates/mock_draft.html` (Task 9): `#mock-slot-grid`, `#mock-random-slot`, `#mock-slot-select`, `#mock-draft-board`, `#mock-status`, `#mock-bot-turn`, `#mock-team-grid`, `#mock-results`, `#mock-your-teams`, `#mock-rankings`, `#mock-again`, `#mock-error`.

- [ ] **Step 1: Write the module**

Create `static/js/mock_draft.js`:

```javascript
/**
 * WinsPool Mock Draft — standalone, login-free solo draft simulator.
 * Drives the entire 30-pick loop client-side against /api/mock-draft/*.
 * Deliberately independent of main.js / websocket_service.js / auth_service.js.
 */

const TEAM_LOGO_OVERRIDES = { LA: 'LAR', WAS: 'WSH' };
function teamLogo(code) {
    return `https://a.espncdn.com/i/teamlogos/nfl/500/${TEAM_LOGO_OVERRIDES[code] || code}.png`;
}

const BOT_PICK_DELAY_MS = 600;
const TOTAL_PICKS = 30;

class MockDraft {
    constructor() {
        this.setup = null;       // { pickSequence, teams, season, projections? }
        this.mySlot = null;
        this.rosters = {};       // slot -> [team, team, team]
        this.picked = new Set(); // teams already taken
        this.pickIndex = 0;      // index into pickSequence
        this.wildcardsSoFar = 0;
        this.totalBotPicks = 0;
        this.botPicksDone = 0;

        this.$slotSelect = document.getElementById('mock-slot-select');
        this.$slotGrid = document.getElementById('mock-slot-grid');
        this.$randomSlotBtn = document.getElementById('mock-random-slot');
        this.$board = document.getElementById('mock-draft-board');
        this.$status = document.getElementById('mock-status');
        this.$botTurn = document.getElementById('mock-bot-turn');
        this.$teamGrid = document.getElementById('mock-team-grid');
        this.$results = document.getElementById('mock-results');
        this.$yourTeams = document.getElementById('mock-your-teams');
        this.$rankings = document.getElementById('mock-rankings');
        this.$again = document.getElementById('mock-again');
        this.$error = document.getElementById('mock-error');

        this.$randomSlotBtn.addEventListener('click', () => this.chooseSlot(null));
        this.$again.addEventListener('click', () => this.restart());
    }

    async init() {
        try {
            const res = await fetch('/api/mock-draft/setup');
            if (!res.ok) throw new Error((await res.json()).error || 'Setup failed.');
            this.setup = await res.json();
            this.renderSlotGrid();
        } catch (err) {
            this.showError(err.message);
        }
    }

    showError(message) {
        this.$error.textContent = message;
        this.$error.classList.remove('hidden');
    }

    renderSlotGrid() {
        const slots = [...new Set(this.setup.pickSequence.map(e => e.slot))].sort((a, b) => a - b);
        this.$slotGrid.innerHTML = slots.map(slot =>
            `<button class="mock-slot-btn" data-slot="${slot}">Spot ${slot}</button>`
        ).join('');
        this.$slotGrid.querySelectorAll('.mock-slot-btn').forEach(btn => {
            btn.addEventListener('click', () => this.chooseSlot(parseInt(btn.dataset.slot, 10)));
        });
    }

    chooseSlot(slot) {
        const slots = [...new Set(this.setup.pickSequence.map(e => e.slot))];
        this.mySlot = slot === null ? slots[Math.floor(Math.random() * slots.length)] : slot;
        this.totalBotPicks = this.setup.pickSequence.filter(e => e.slot !== this.mySlot).length;
        slots.forEach(s => { this.rosters[s] = []; });

        this.$slotSelect.classList.add('hidden');
        this.$board.classList.remove('hidden');
        this.advance();
    }

    availableTeams() {
        return this.setup.teams.filter(t => !this.picked.has(t));
    }

    async advance() {
        if (this.pickIndex >= this.setup.pickSequence.length) {
            return this.finish();
        }
        const entry = this.setup.pickSequence[this.pickIndex];
        this.$status.textContent = `Pick ${entry.pick} of ${TOTAL_PICKS}`;

        if (entry.slot === this.mySlot) {
            this.renderHumanTurn();
        } else {
            await this.runBotTurn(entry);
        }
    }

    renderHumanTurn() {
        this.$botTurn.classList.add('hidden');
        this.$teamGrid.classList.remove('hidden');
        const projections = this.setup.projections;
        this.$teamGrid.innerHTML = this.availableTeams().map(team => {
            const proj = projections && projections[team];
            const sub = proj ? `<div class="team-btn-sub">${proj.projected_wins}W</div>` : '';
            return `
                <button class="team-btn" data-team="${team}">
                    <img src="${teamLogo(team)}" alt="${team}" style="width:32px;height:32px;object-fit:contain;">
                    <div style="flex:1;min-width:0"><div class="team-btn-city">${team}</div>${sub}</div>
                </button>`;
        }).join('');
        this.$teamGrid.querySelectorAll('.team-btn').forEach(btn => {
            btn.addEventListener('click', () => this.applyPick(this.mySlot, btn.dataset.team, false));
        });
    }

    async runBotTurn(entry) {
        this.$teamGrid.classList.add('hidden');
        this.$botTurn.classList.remove('hidden');
        this.$botTurn.textContent = `Bot ${entry.slot} is picking…`;
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));

        const remaining = this.totalBotPicks - this.botPicksDone;
        const res = await fetch('/api/mock-draft/pick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                season: this.setup.season,
                availableTeams: this.availableTeams(),
                wildcardsSoFar: this.wildcardsSoFar,
                botPicksRemaining: remaining,
            }),
        });
        if (!res.ok) return this.showError((await res.json()).error || 'Bot pick failed.');
        const { team, wasWildcard } = await res.json();
        if (wasWildcard) this.wildcardsSoFar += 1;
        this.botPicksDone += 1;
        this.$botTurn.textContent = `Bot ${entry.slot} picks ${team}${wasWildcard ? ' (wildcard!)' : ''}`;
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));
        this.applyPick(entry.slot, team, wasWildcard);
    }

    applyPick(slot, team, _wasWildcard) {
        this.rosters[slot].push(team);
        this.picked.add(team);
        this.pickIndex += 1;
        this.advance();
    }

    async finish() {
        this.$board.classList.add('hidden');
        this.$results.classList.remove('hidden');

        this.$yourTeams.innerHTML = `<h3>Your teams</h3>` + this.rosters[this.mySlot].map(team =>
            `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:10px;">
                <img src="${teamLogo(team)}" style="width:20px;height:20px;">${team}
            </span>`
        ).join('');

        try {
            const res = await fetch('/api/mock-draft/results', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ season: this.setup.season, rosters: this.rosters }),
            });
            if (!res.ok) throw new Error((await res.json()).error || 'Ranking failed.');
            const { rankings } = await res.json();
            rankings.sort((a, b) => a.rank - b.rank);
            this.$rankings.innerHTML = `<h3>Final Rankings</h3>` + rankings.map(r => {
                const isYou = r.slot === this.mySlot;
                const label = isYou ? 'You' : `Bot ${r.slot}`;
                const totalText = 'totalProjectedWins' in r ? ` — ${r.totalProjectedWins}W` : '';
                return `<div class="mock-rank-row${isYou ? ' is-you' : ''}">#${r.rank} ${label}${totalText}</div>`;
            }).join('');
        } catch (err) {
            this.showError(err.message);
        }
    }

    restart() {
        this.$results.classList.add('hidden');
        this.$error.classList.add('hidden');
        this.mySlot = null;
        this.rosters = {};
        this.picked = new Set();
        this.pickIndex = 0;
        this.wildcardsSoFar = 0;
        this.botPicksDone = 0;
        this.$slotSelect.classList.remove('hidden');
        this.init();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new MockDraft().init();
});
```

- [ ] **Step 2: Manually verify the full draft flow**

Run: `uvicorn main:app --reload`
Visit `http://localhost:8000/mock-draft` in a browser.

Walk through:
1. Click a draft spot (or "Random Spot") — the slot grid disappears, the board appears.
2. Watch several bot picks auto-resolve with the "Bot N is picking…" beat, then a team reveal.
3. On your turn, click a team from the grid — as a non-admin (no session cookie), confirm no win-total numbers are shown in the team grid.
4. Let the draft run to completion (30 picks) — confirm the results screen shows your 3 teams and a ranking list with `#rank Bot N` / `#rank You`, with **no** win totals next to any entry.
5. Log in as an admin in another tab, copy the `session_token` cookie into this browser (or log in as admin directly in this browser), reload `/mock-draft`, and repeat — confirm win totals now appear both during team selection and on the final rankings.
6. Click "Draft Again" — confirm it resets and starts a fresh draft without a page reload.

Expected: no console errors at any step; the non-admin run never displays a `projected_wins` or `totalProjectedWins` number anywhere in the DOM (verify via browser devtools' Elements/Network tabs, not just visually).

- [ ] **Step 3: Commit**

```bash
git add static/js/mock_draft.js
git commit -m "feat: implement mock draft frontend pick loop and results screen"
```

---

### Task 11: Nav link for logged-in users

**Files:**
- Modify: `static/js/main.js`

**Interfaces:**
- Consumes: none new. Modifies `App.updateNav()`'s existing `moreLinks` array (`static/js/main.js:156-169`).

The mock draft's primary distribution is the shared `/mock-draft` link itself (works for anonymous visitors, who never see the authenticated nav at all — `updateNav()` returns early when `!playerId`). This task only adds discoverability for players who are already logged in.

- [ ] **Step 1: Add the link**

In `static/js/main.js`, inside `updateNav()`, change:

```javascript
        moreLinks.push(
            { href: '/draft/history', label: 'Draft History' },
            { href: '/history',       label: 'All-Time History' },
            null,
            { href: '/profile',       label: 'Profile' },
        );
```

to:

```javascript
        moreLinks.push(
            { href: '/draft/history', label: 'Draft History' },
            { href: '/mock-draft',    label: 'Mock Draft' },
            { href: '/history',       label: 'All-Time History' },
            null,
            { href: '/profile',       label: 'Profile' },
        );
```

- [ ] **Step 2: Manually verify**

Run: `uvicorn main:app --reload`
Log in, open the "More" nav dropdown.
Expected: "Mock Draft" appears between "Draft History" and "All-Time History" and navigates to `/mock-draft`.

- [ ] **Step 3: Commit**

```bash
git add static/js/main.js
git commit -m "feat: add Mock Draft link to nav for logged-in users"
```

---

## Final verification

- [ ] Run the full backend test suite once more: `pytest tests/ -v` — expect all green, including every new file (`test_mock_draft_service.py`, `test_mock_draft.py`) and every modified one (`test_draft_service.py`, `test_draft_websocket.py`, `test_session_service.py`).
- [ ] Manually re-verify the live draft room (`/draft`) still works end-to-end for both an admin and a non-admin test player — Part A must not have broken picks, undo, reset, or chat.
- [ ] Bump `static/style.css?v=23` → `?v=24` in `templates/base.html` **only if** any shared CSS classes were edited (they weren't — the mock draft page adds its own `<style>` block instead of editing `style.css`). No action needed unless that changes during implementation.
