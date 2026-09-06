"""tests/test_draft_websocket.py — WebSocket draft flow tests.

Covers:
  - ConnectionManager bookkeeping (connect/disconnect)
  - WebSocket message flow: reauthenticate, pick, admin undo
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from starlette.testclient import TestClient

from routes.draft_routes import ConnectionManager

# Default get_player_by_id stub for reauthenticate — any playerId resolves to
# a player with a password_hash set, so the handshake succeeds; each test's
# state fixture (FAKE_STATE/PICK_FAKE_STATE/PROJECTION_FAKE_STATE) is what
# actually determines admin vs. non-admin via role.
_REAUTH_OK = lambda pid: {"playerId": int(pid), "password_hash": "hash"}


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
         patch("routes.draft_routes.reset_pick") as mock_reset, \
         patch("services.db_service.get_player_by_id", side_effect=_REAUTH_OK):
        from main import app
        client = TestClient(app)
        yield client, mock_save, mock_undo, mock_reset


class TestWebSocketConnect:

    def test_initial_state_sent_on_connect(self, ws_client):
        """Server should send a 'state' message immediately on connect."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "state"
            assert "payload" in msg


class TestWebSocketAdminActions:

    def _verify_as_admin(self, ws):
        """Helper: perform the reauthenticate handshake as player 1 (admin)."""
        ws.receive_json()  # consume initial state
        ws.send_json({"action": "reauthenticate", "playerId": 1})
        # Drain verified + state broadcast
        for _ in range(2):
            ws.receive_json()

    def test_undo_pick_without_auth_returns_error(self, ws_client):
        """Unauthenticated socket (no reauthenticate) cannot undo a pick."""
        client, _, _, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # consume initial state
            ws.receive_json()  # consume chat_history
            ws.send_json({"action": "undo_pick"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Unauthorized" in msg["message"]

    def test_admin_undo_pick_calls_undo(self, ws_client):
        """Admin player after reauthenticate can trigger undo_pick."""
        client, _, mock_undo, _ = ws_client
        with client.websocket_connect("/ws") as ws:
            self._verify_as_admin(ws)
            ws.send_json({"action": "undo_pick"})
            # Should broadcast updated state (not an error)
            msg = ws.receive_json()
            assert msg["type"] == "state"
            mock_undo.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #48: pick action validation
# ---------------------------------------------------------------------------

PICK_FAKE_STATE = {
    "season": 2025,
    "active_pick": 3,
    "draft_ready": True,
    "available_teams": ["KC", "BUF", "PHI"],
    "all_players": [
        {"playerId": 1, "fullName": "Admin User", "role": "admin"},
        {"playerId": 2, "fullName": "Regular Player", "role": "user"},
    ],
    # pick 3 belongs to player 1; player 2 is out of turn
    "draft_board": [
        {"pick": 1, "playerId": 2, "playerName": "Regular Player"},
        {"pick": 2, "playerId": 1, "playerName": "Admin User"},
        {"pick": 3, "playerId": 1, "playerName": "Admin User"},
    ],
    "picks": [],
    "draft_order": [],
}


@pytest.fixture
def ws_pick_client():
    """TestClient with a complete PICK_FAKE_STATE (includes draft_ready, available_teams, draft_board).

    Defaults draft_active=True so these tests exercise their own specific
    guard (turn order, team availability, etc.) rather than depending on
    whatever the real local config_settings.json happens to hold at the
    time -- tests that specifically want draft_active=False override this
    with their own nested patch.
    """
    with patch("routes.draft_routes.load_draft_state", return_value=PICK_FAKE_STATE), \
         patch("routes.draft_routes.save_pick") as mock_save, \
         patch("routes.draft_routes.undo_pick"), \
         patch("routes.draft_routes.reset_pick"), \
         patch("routes.draft_routes.get_config_settings", return_value={"draft_active": True}), \
         patch("services.db_service.get_player_by_id", side_effect=_REAUTH_OK):
        from main import app
        yield TestClient(app), mock_save


class TestWebSocketPick:

    def _verify_as(self, ws, player_id):
        """Consume initial state, send reauthenticate, drain resulting messages."""
        ws.receive_json()  # initial state
        ws.send_json({"action": "reauthenticate", "playerId": player_id})
        ws.receive_json()  # chat_history (sent on connect, still in queue)
        ws.receive_json()  # verified
        ws.receive_json()  # state broadcast after verify

    def test_pick_out_of_turn_returns_error(self, ws_pick_client):
        """Non-admin picking out of turn receives an error."""
        client, _ = ws_pick_client
        with client.websocket_connect("/ws") as ws:
            self._verify_as(ws, player_id=2)  # player 2; pick 3 belongs to player 1
            ws.send_json({"action": "pick", "team": "KC"})
            msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "your turn" in msg["message"].lower()

    def test_pick_unavailable_team_returns_error(self, ws_pick_client):
        """Picking a team not in available_teams returns an error before the turn check."""
        client, _ = ws_pick_client
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state
            ws.receive_json()  # chat_history
            ws.send_json({"action": "pick", "team": "INVALID"})
            msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "not available" in msg["message"].lower()

    def test_pick_draft_complete_returns_error(self, ws_pick_client):
        """active_pick > 30 triggers draft-complete guard before any other check."""
        client, _ = ws_pick_client
        complete_state = {**PICK_FAKE_STATE, "active_pick": 31}
        with patch("routes.draft_routes.load_draft_state", return_value=complete_state):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # state (complete_state)
                ws.receive_json()  # chat_history
                ws.send_json({"action": "pick", "team": "KC"})
                msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "complete" in msg["message"].lower()

    def test_pick_blocked_for_non_admin_when_draft_not_active(self, ws_pick_client):
        """Non-admin whose turn it is still gets rejected while draft_active is False."""
        client, _ = ws_pick_client
        # Pick 3 now belongs to player 2 (non-admin) so this isolates the
        # draft_active gate from the separate "not your turn" check.
        own_turn_state = {
            **PICK_FAKE_STATE,
            "draft_board": [
                {"pick": 1, "playerId": 1, "playerName": "Admin User"},
                {"pick": 2, "playerId": 1, "playerName": "Admin User"},
                {"pick": 3, "playerId": 2, "playerName": "Regular Player"},
            ],
        }
        with patch("routes.draft_routes.load_draft_state", return_value=own_turn_state), \
             patch("routes.draft_routes.get_config_settings", return_value={"draft_active": False}):
            with client.websocket_connect("/ws") as ws:
                self._verify_as(ws, player_id=2)
                ws.send_json({"action": "pick", "team": "KC"})
                msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "hasn't opened" in msg["message"].lower()

    def test_admin_can_pick_when_draft_not_active(self, ws_pick_client):
        """Admins are exempt from the draft_active gate (setup/testing before opening)."""
        client, mock_save = ws_pick_client
        with patch("routes.draft_routes.get_config_settings", return_value={"draft_active": False}):
            with client.websocket_connect("/ws") as ws:
                self._verify_as(ws, player_id=1)  # pick 3 belongs to player 1 (admin)
                ws.send_json({"action": "pick", "team": "KC"})
                msg = ws.receive_json()
        assert msg["type"] == "state"
        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #49: reauthenticate action
# ---------------------------------------------------------------------------

class TestWebSocketReauthenticate:

    def test_reauthenticate_player_without_password_hash_returns_error(self, ws_client):
        """Player whose password_hash is missing cannot reauthenticate."""
        client, _, _, _ = ws_client
        player_no_hash = {"playerId": 2, "email": "p2@test.com"}  # no password_hash
        with patch("services.db_service.get_player_by_id", return_value=player_no_hash):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # initial state
                ws.receive_json()  # chat_history
                ws.send_json({"action": "reauthenticate", "playerId": 2})
                msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "session expired" in msg["message"].lower()

    def test_reauthenticate_valid_player_returns_verified(self, ws_client):
        """Player with password_hash set is verified and receives a verified message."""
        client, _, _, _ = ws_client
        valid_player = {"playerId": 2, "email": "p2@test.com", "password_hash": "hash"}
        with patch("services.db_service.get_player_by_id", return_value=valid_player):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()  # initial state
                ws.receive_json()  # chat_history
                ws.send_json({"action": "reauthenticate", "playerId": 2})
                msgs = [ws.receive_json(), ws.receive_json()]  # verified + state broadcast
        types = {m["type"] for m in msgs}
        assert "verified" in types


# ---------------------------------------------------------------------------
# Mock Draft & Projection Gating — Task 3: admin detection wired into lifecycle
# ---------------------------------------------------------------------------

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
         patch("routes.draft_routes.reset_pick"), \
         patch("services.db_service.get_player_by_id", side_effect=_REAUTH_OK):
        from main import app
        yield TestClient(app)


class TestWebSocketProjectionGating:

    def test_initial_state_before_auth_has_predictions_stripped(self, ws_projection_client):
        """A brand-new connection (not yet verified) must never see raw predictions."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            msg = ws.receive_json()
        assert msg["payload"]["preseason_predictions"] == {}

    def test_admin_reauthenticate_receives_full_predictions(self, ws_projection_client):
        """After reauthenticating as an admin player, the resulting state broadcast is unstripped."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state (stripped)
            ws.receive_json()  # chat_history
            ws.send_json({"action": "reauthenticate", "playerId": 1})
            msgs = [ws.receive_json(), ws.receive_json()]  # verified + state broadcast
        state_msg = next(m for m in msgs if m["type"] == "state")
        assert state_msg["payload"]["preseason_predictions"] == {"KC": {"projected_wins": 11.2}}

    def test_player_reauthenticate_receives_stripped_predictions(self, ws_projection_client):
        """A non-admin player's post-reauthenticate state broadcast is still stripped."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state (stripped)
            ws.receive_json()  # chat_history
            ws.send_json({"action": "reauthenticate", "playerId": 2})
            msgs = [ws.receive_json(), ws.receive_json()]  # verified + state broadcast
        state_msg = next(m for m in msgs if m["type"] == "state")
        assert state_msg["payload"]["preseason_predictions"] == {}

    def test_switch_season_direct_send_respects_admin_status(self, ws_projection_client):
        """switch_season bypasses broadcast() with a direct send — must also be gated."""
        with ws_projection_client.websocket_connect("/ws") as ws:
            ws.receive_json()  # initial state
            ws.receive_json()  # chat_history
            ws.send_json({"action": "reauthenticate", "playerId": 2})
            ws.receive_json()  # verified
            ws.receive_json()  # state broadcast after reauthenticate
            ws.send_json({"action": "switch_season", "year": 2025})
            msg = ws.receive_json()
        assert msg["type"] == "state"
        assert msg["payload"]["preseason_predictions"] == {}
