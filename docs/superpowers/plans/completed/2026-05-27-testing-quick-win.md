# Testing Quick Win Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add test coverage for the WebSocket draft flow (`ConnectionManager` + pick/admin actions) and the `set_member_paid()` payment path.

**Architecture:** Two new test files. WebSocket tests use FastAPI's `TestClient.websocket_connect()` with mocked draft state. Payment unit tests mock `get_collection_df`, `get_db`, and `_save_df_to_local` directly on `services.db_service`. Three additional test cases are appended to the existing `tests/test_admin_routes.py`.

**Tech Stack:** pytest, `starlette.testclient.TestClient`, `unittest.mock.patch`, `pandas`

---

## Files

| Action | Path | Change |
|---|---|---|
| Create | `tests/test_draft_websocket.py` | WebSocket + ConnectionManager tests |
| Create | `tests/test_set_member_paid.py` | `set_member_paid()` unit tests |
| Modify | `tests/test_admin_routes.py` | Add 3 payment endpoint tests |

---

## Task 1: `ConnectionManager` unit tests

**Files:**
- Create: `tests/test_draft_websocket.py`

`ConnectionManager` lives in `routes/draft_routes.py`. It manages a list of active WebSocket connections and broadcasts JSON messages. These unit tests verify the list bookkeeping in isolation — no HTTP server needed.

- [x] **Step 1: Write the failing tests**

Create `tests/test_draft_websocket.py` with:

```python
"""tests/test_draft_websocket.py — WebSocket draft flow tests.

Covers:
  - ConnectionManager bookkeeping (connect/disconnect)
  - WebSocket message flow: verify_code, pick, admin undo
"""
import asyncio
import os
os.environ.setdefault("ROOM_CODE", "test")  # must be set before app import

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient

from routes.draft_routes import ConnectionManager


# ---------------------------------------------------------------------------
# ConnectionManager unit tests (no HTTP server needed)
# ---------------------------------------------------------------------------

class TestConnectionManager:

    def test_connect_adds_to_active_connections(self):
        """connect() should register the WebSocket in active_connections."""
        mgr = ConnectionManager()
        ws = AsyncMock()
        asyncio.run(mgr.connect(ws))
        assert ws in mgr.active_connections
        ws.accept.assert_awaited_once()

    def test_disconnect_removes_from_active_connections(self):
        """disconnect() should remove the WebSocket from active_connections."""
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.active_connections.append(ws)
        mgr.disconnect(ws)
        assert ws not in mgr.active_connections

    def test_disconnect_unknown_ws_is_safe(self):
        """disconnect() on an unknown WebSocket should not raise."""
        mgr = ConnectionManager()
        ws = MagicMock()
        mgr.disconnect(ws)  # not in list — should be a no-op

    def test_broadcast_sends_to_all_connections(self):
        """broadcast() should call send_json on every active connection."""
        mgr = ConnectionManager()
        ws1, ws2 = AsyncMock(), AsyncMock()
        mgr.active_connections = [ws1, ws2]
        asyncio.run(mgr.broadcast({"type": "state", "payload": {}}))
        ws1.send_json.assert_awaited_once_with({"type": "state", "payload": {}})
        ws2.send_json.assert_awaited_once_with({"type": "state", "payload": {}})

    def test_broadcast_skips_failed_connection(self):
        """broadcast() should not raise if one connection throws."""
        mgr = ConnectionManager()
        ws_bad = AsyncMock()
        ws_bad.send_json.side_effect = Exception("disconnected")
        ws_good = AsyncMock()
        mgr.active_connections = [ws_bad, ws_good]
        asyncio.run(mgr.broadcast({"type": "ping"}))
        ws_good.send_json.assert_awaited_once()
```

- [x] **Step 2: Run tests — confirm they pass**

```bash
pytest tests/test_draft_websocket.py::TestConnectionManager -v
```

Expected: 5 passed. (These don't depend on the WebSocket endpoint — they're pure unit tests on the class.)

- [x] **Step 3: Commit**

```bash
git add tests/test_draft_websocket.py
git commit -m "test: add ConnectionManager unit tests"
```

---

## Task 2: WebSocket endpoint integration tests

**Files:**
- Modify: `tests/test_draft_websocket.py`

Use `TestClient.websocket_connect("/ws")` — the WebSocket lives at `/ws` with no prefix. Mock `load_draft_state` and `save_pick`/`undo_pick` so no Firestore calls are made.

The `ROOM_CODE` defaults to `"test"` (see `draft_routes.py` line 409: `os.environ.get("ROOM_CODE", "test")`), so no env setup is needed.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_draft_websocket.py`:

```python
# ---------------------------------------------------------------------------
# WebSocket endpoint integration tests
# ---------------------------------------------------------------------------

FAKE_STATE = {
    "season": 2025,
    "active_pick": 3,
    "all_players": [
        {"playerId": 1, "fullName": "Admin User", "role": "admin"},
        {"playerId": 2, "fullName": "Regular Player", "role": "user"},
    ],
    "picks": [],
    "draft_order": [],
}


@pytest.fixture
def ws_client():
    """TestClient with mocked draft state — no Firestore calls."""
    with patch("routes.draft_routes.load_draft_state", return_value=FAKE_STATE), \
         patch("routes.draft_routes.save_pick") as mock_save, \
         patch("routes.draft_routes.undo_pick") as mock_undo, \
         patch("routes.draft_routes.reset_pick") as mock_reset:
        from main import app
        client = TestClient(app)
        yield client, mock_save, mock_undo, mock_reset


class TestWebSocketVerifyCode:

    def test_initial_state_sent_on_connect(self, ws_client):
        """Server should send a 'state' message immediately on connect."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert "payload" in msg

    def test_verify_code_correct_sends_verified(self, ws_client):
        """Correct room code → server responds with type='verified'."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume initial state
            ws.send_json({"action": "verify_code", "playerId": 1, "code": "test"})
            # May receive a state broadcast first; find the verified message
            msgs = [ws.receive_json() for _ in range(2)]
            types = {m["type"] for m in msgs}
            assert "verified" in types

    def test_verify_code_wrong_sends_error(self, ws_client):
        """Wrong room code → server responds with type='error'."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume initial state
            ws.send_json({"action": "verify_code", "playerId": 1, "code": "wrongcode"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Invalid Room Code" in msg["message"]


class TestWebSocketAdminActions:

    def _verify_as_admin(self, ws):
        """Helper: perform the verify_code handshake as player 1 (admin)."""
        ws.receive_json()  # consume initial state
        ws.send_json({"action": "verify_code", "playerId": 1, "code": "test"})
        # Drain verified + state broadcast
        for _ in range(2):
            ws.receive_json()

    def test_undo_pick_without_auth_returns_error(self, ws_client):
        """Unauthenticated socket (no verify_code) cannot undo a pick."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume initial state
            ws.send_json({"action": "undo_pick"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Unauthorized" in msg["message"]

    def test_admin_undo_pick_calls_undo(self, ws_client):
        """Admin player after verify_code can trigger undo_pick."""
        client, _, mock_undo, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            self._verify_as_admin(ws)
            ws.send_json({"action": "undo_pick"})
            # Should broadcast updated state (not an error)
            msg = ws.receive_json()
            assert msg["type"] == "state"
            mock_undo.assert_called_once()
```

- [x] **Step 2: Run tests — confirm they pass**

```bash
pytest tests/test_draft_websocket.py -v
```

Expected: all 9 tests pass.

- [x] **Step 3: Confirm no regressions**

```bash
pytest tests/ -q --tb=short
```

- [x] **Step 4: Commit**

```bash
git add tests/test_draft_websocket.py
git commit -m "test: add WebSocket endpoint integration tests for verify_code and admin actions"
```

---

## Task 3: `set_member_paid()` unit tests

**Files:**
- Create: `tests/test_set_member_paid.py`

Test `services.db_service.set_member_paid()` directly. Mock `get_collection_df`, `get_db`, and `_save_df_to_local` at the `services.db_service` namespace (where they're defined).

- [x] **Step 1: Write the failing tests**

Create `tests/test_set_member_paid.py`:

```python
"""tests/test_set_member_paid.py — Unit tests for db_service.set_member_paid()."""
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from services.db_service import set_member_paid


def _draft_order_df(season=2024, player_id=1, draft_order=2, paid=False):
    """Minimal draft_order DataFrame matching set_member_paid's expected schema."""
    return pd.DataFrame([{
        "season": season,
        "playerId": player_id,
        "draftOrder": draft_order,
        "paid": paid,
    }])


class TestSetMemberPaid:

    def test_happy_path_returns_true_and_calls_firestore(self):
        """Happy path: player found → returns True and calls Firestore update."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df()), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, True)
        assert result is True
        mock_db.collection("draft_order").document("2024_2").update.assert_called_once_with({"paid": True})

    def test_sets_paid_false(self):
        """paid=False is passed correctly to Firestore."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(paid=True)), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, False)
        assert result is True
        mock_db.collection("draft_order").document("2024_2").update.assert_called_once_with({"paid": False})

    def test_returns_false_when_player_not_in_draft_order(self):
        """Player ID not in draft_order → returns False without touching Firestore."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(player_id=99)), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local") as mock_save:
            result = set_member_paid(2024, 1, True)  # player_id=1 not in df
        assert result is False
        mock_db.collection.assert_not_called()
        mock_save.assert_not_called()

    def test_returns_false_when_draft_order_empty(self):
        """Empty draft_order → returns False immediately."""
        with patch("services.db_service.get_collection_df", return_value=pd.DataFrame()):
            result = set_member_paid(2024, 1, True)
        assert result is False

    def test_local_pkl_updated(self):
        """_save_df_to_local is called with the updated DataFrame."""
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(paid=False)), \
             patch("services.db_service.get_db", return_value=MagicMock()), \
             patch("services.db_service._save_df_to_local") as mock_save:
            set_member_paid(2024, 1, True)
        assert mock_save.call_count == 1
        saved_df = mock_save.call_args[0][1]  # second positional arg is the DataFrame
        assert bool(saved_df.loc[0, "paid"]) is True

    def test_no_db_call_when_firestore_unavailable(self):
        """If get_db() returns None, Firestore update is skipped but returns True."""
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df()), \
             patch("services.db_service.get_db", return_value=None), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, True)
        assert result is True
```

- [x] **Step 2: Run tests — confirm they pass**

```bash
pytest tests/test_set_member_paid.py -v
```

Expected: 6 passed.

- [x] **Step 3: Commit**

```bash
git add tests/test_set_member_paid.py
git commit -m "test: add unit tests for set_member_paid"
```

---

## Task 4: Add payment endpoint tests to `test_admin_routes.py`

**Files:**
- Modify: `tests/test_admin_routes.py`

The existing file already tests admin routes. Append a `TestUpdateMemberPaid` class at the bottom. Patch at `routes.admin_routes.set_member_paid` (where it's imported), not `services.db_service.set_member_paid`.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_admin_routes.py`:

```python
# ── /api/admin/members/paid ───────────────────────────────────────────────────

class TestUpdateMemberPaid:

    def test_happy_path_returns_ok_true(self, admin_token):
        """Happy path: set_member_paid returns True → response is {"ok": true}."""
        with patch("routes.admin_routes.set_member_paid", return_value=True):
            resp = client.post(
                "/api/admin/members/paid",
                json={"season": 2024, "targetPlayerId": 1, "paid": True},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_requires_admin_role(self, auth_token):
        """Non-admin token → 403 Forbidden."""
        resp = client.post(
            "/api/admin/members/paid",
            json={"season": 2024, "targetPlayerId": 1, "paid": True},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 403

    def test_player_not_found_returns_ok_false(self, admin_token):
        """set_member_paid returns False (player missing) → response is {"ok": false}."""
        with patch("routes.admin_routes.set_member_paid", return_value=False):
            resp = client.post(
                "/api/admin/members/paid",
                json={"season": 2024, "targetPlayerId": 999, "paid": True},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": False}
```

- [x] **Step 2: Run tests — confirm they pass**

```bash
pytest tests/test_admin_routes.py::TestUpdateMemberPaid -v
```

Expected: 3 passed.

- [x] **Step 3: Run full suite**

```bash
pytest tests/ -q --tb=short
```

Expected: all tests pass (5 pre-existing Firebase errors are acceptable).

- [x] **Step 4: Commit and close issue**

```bash
git add tests/test_admin_routes.py
git commit -m "test: add payment endpoint tests for update_member_paid; closes #39"
```
