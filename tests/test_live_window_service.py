"""services/live_window_service.py::is_within_live_window() -- pure port of
scripts/sync_live_scores.py's window-check algorithm, operating on an
already-loaded games DataFrame with no I/O."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from services.live_window_service import is_within_live_window

ET = ZoneInfo("America/New_York")


def _games(rows):
    return pd.DataFrame(rows, columns=["gameday", "gametime", "result"])


def _now(day, hour, minute):
    return datetime(2026, 9, day, hour, minute, tzinfo=ET)


SUNDAY = "2026-09-20"
SUNDAY_GAMES = [
    (SUNDAY, "13:00", None),
    (SUNDAY, "16:25", None),
    (SUNDAY, "20:20", None),
]


def test_none_games_fails_open():
    assert is_within_live_window(None, _now(20, 13, 0)) is True


def test_empty_games_fails_open():
    assert is_within_live_window(pd.DataFrame(), _now(20, 13, 0)) is True


def test_no_games_today_is_inactive():
    games = _games([("2026-09-22", "13:00", None)])
    assert is_within_live_window(games, _now(21, 15, 0)) is False


def test_before_window_opens_is_inactive():
    assert is_within_live_window(_games(SUNDAY_GAMES), _now(20, 10, 0)) is False


def test_inside_live_window_is_active():
    assert is_within_live_window(_games(SUNDAY_GAMES), _now(20, 21, 30)) is True


def test_all_final_after_tail_is_inactive():
    games = _games([(SUNDAY, "13:00", 3), (SUNDAY, "16:25", -7), (SUNDAY, "20:20", 10)])
    assert is_within_live_window(games, _now(21, 0, 30)) is False


def test_not_final_within_four_and_half_hour_tail_is_active():
    games = _games([(SUNDAY, "20:20", None)])
    # 20:20 + 4h30 = 00:50
    assert is_within_live_window(games, _now(21, 0, 45)) is True
    assert is_within_live_window(games, _now(21, 0, 51)) is False


def test_broken_schedule_fails_open():
    assert is_within_live_window(pd.DataFrame({"x": [1]}), _now(20, 13, 0)) is True
