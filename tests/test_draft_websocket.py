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
