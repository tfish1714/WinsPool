"""Tests for WebSocket admin-action authorization (issue #11).

The fix: admin actions (undo_pick, reset_pick, force_pick, pick-as-admin)
must use socket_player_id (the server-validated identity) to check admin role,
NOT the playerId supplied in the WebSocket message (client-controlled).
"""
import pytest

# Import the helper we will create in draft_routes.py
from routes.draft_routes import _get_authenticated_admin


PLAYERS = [
    {"playerId": 1, "role": "admin",  "playerName": "Admin Alice"},
    {"playerId": 2, "role": "user",   "playerName": "Regular Bob"},
    {"playerId": 3, "role": "user",   "playerName": "Regular Carol"},
]


# ── _get_authenticated_admin helper ────────────────────────────────────────

def test_returns_none_when_socket_player_id_is_none():
    """Unauthenticated sockets (socket_player_id=None) never get admin access."""
    assert _get_authenticated_admin(None, PLAYERS) is None


def test_returns_none_when_player_is_not_admin():
    """A regular user's socket never grants admin access regardless of message content."""
    assert _get_authenticated_admin(2, PLAYERS) is None


def test_returns_none_when_player_not_found_in_state():
    """socket_player_id that doesn't match any player in state returns None."""
    assert _get_authenticated_admin(999, PLAYERS) is None


def test_returns_player_dict_for_authenticated_admin():
    """An authenticated admin socket returns the player dict."""
    result = _get_authenticated_admin(1, PLAYERS)
    assert result is not None
    assert result["playerId"] == 1
    assert result["role"] == "admin"


def test_does_not_grant_admin_via_spoofed_message_id():
    """
    Core security invariant: a user authenticated as player 2 (non-admin)
    cannot obtain admin access even if they forge a message with player 1's ID.

    The function only inspects socket_player_id (server-controlled), not any
    client-supplied value.  The caller is responsible for passing socket_player_id
    directly — this test verifies the contract.
    """
    # socket_player_id=2 (user) — this is what the server knows
    # The client might send {"playerId": 1} in the message, but the caller
    # must pass socket_player_id, not msg.get("playerId").
    result = _get_authenticated_admin(2, PLAYERS)   # socket says user 2
    assert result is None                            # user 2 is NOT admin → denied
