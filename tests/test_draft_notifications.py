"""routes/draft_routes.py: the on-the-clock notification helpers.

_send_push_sync used to have a bare `except: pass` -- an unexpected error
(not a normal push failure, which push_service.py already handles and
returns False for) vanished with zero trace. _send_on_the_clock_email_sync
is the new parallel email channel, added because push reliability varies
too much by browser/OS/PWA-install-state to be the sole "you're on the
clock" alert.
"""
import logging
from unittest.mock import patch

from routes.draft_routes import _send_push_sync, _send_on_the_clock_email_sync


def test_send_push_sync_logs_unexpected_errors_instead_of_swallowing(caplog):
    with patch("services.push_service.send_push_notification", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.ERROR):
            _send_push_sync(1, "title", "body")  # must not raise
    assert any("unexpected error sending push to player 1" in r.message for r in caplog.records)


def test_send_on_the_clock_email_sync_skips_player_with_no_email(caplog):
    with patch("routes.draft_routes.get_player_by_id", return_value={"playerId": 1, "email": None}), \
         patch("services.email_service.send_on_the_clock_email") as mock_send:
        with caplog.at_level(logging.INFO):
            _send_on_the_clock_email_sync(1, 2026, 14)
    mock_send.assert_not_called()
    assert any("no email on file for player 1" in r.message for r in caplog.records)


def test_send_on_the_clock_email_sync_skips_unknown_player(caplog):
    with patch("routes.draft_routes.get_player_by_id", return_value=None), \
         patch("services.email_service.send_on_the_clock_email") as mock_send:
        with caplog.at_level(logging.INFO):
            _send_on_the_clock_email_sync(1, 2026, 14)
    mock_send.assert_not_called()
    assert any("no player record for 1" in r.message for r in caplog.records)


def test_send_on_the_clock_email_sync_sends_to_player_email():
    player = {"playerId": 1, "email": "alice@x.com", "nickName": "Alice", "fullName": "Alice Anderson"}
    with patch("routes.draft_routes.get_player_by_id", return_value=player), \
         patch("services.email_service.send_on_the_clock_email") as mock_send:
        _send_on_the_clock_email_sync(1, 2026, 14)
    mock_send.assert_called_once_with("alice@x.com", "Alice", 2026, 14)


def test_send_on_the_clock_email_sync_falls_back_to_full_name_when_no_nickname():
    player = {"playerId": 1, "email": "alice@x.com", "nickName": None, "fullName": "Alice Anderson"}
    with patch("routes.draft_routes.get_player_by_id", return_value=player), \
         patch("services.email_service.send_on_the_clock_email") as mock_send:
        _send_on_the_clock_email_sync(1, 2026, 14)
    mock_send.assert_called_once_with("alice@x.com", "Alice Anderson", 2026, 14)


def test_send_on_the_clock_email_sync_logs_unexpected_errors_instead_of_swallowing(caplog):
    with patch("routes.draft_routes.get_player_by_id", side_effect=RuntimeError("db down")):
        with caplog.at_level(logging.ERROR):
            _send_on_the_clock_email_sync(1, 2026, 14)  # must not raise
    assert any("unexpected error emailing player 1" in r.message for r in caplog.records)
