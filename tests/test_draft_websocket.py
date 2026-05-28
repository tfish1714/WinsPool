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
