"""scripts/sync_live_scores.py::is_live_score_window_active() and main()'s
fast exit outside NFL game windows."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import scripts.sync_live_scores as sls

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


def test_no_games_today_is_inactive():
    games = _games([("2026-09-22", "13:00", None)])
    assert sls.is_live_score_window_active(_now(21, 15, 0), games) is False


def test_before_window_opens_is_inactive():
    assert sls.is_live_score_window_active(_now(20, 10, 0), _games(SUNDAY_GAMES)) is False


def test_fifteen_minutes_before_kickoff_is_active():
    assert sls.is_live_score_window_active(_now(20, 12, 45), _games(SUNDAY_GAMES)) is True


def test_exactly_twenty_minutes_before_kickoff_is_active():
    assert sls.is_live_score_window_active(_now(20, 12, 40), _games(SUNDAY_GAMES)) is True


def test_twenty_one_minutes_before_kickoff_is_inactive():
    assert sls.is_live_score_window_active(_now(20, 12, 39), _games(SUNDAY_GAMES)) is False


def test_international_early_game_is_active():
    games = _games([(SUNDAY, "09:30", None), (SUNDAY, "13:00", None)])
    assert sls.is_live_score_window_active(_now(20, 9, 15), games) is True


def test_snf_in_progress_is_active():
    assert sls.is_live_score_window_active(_now(20, 21, 30), _games(SUNDAY_GAMES)) is True


def test_all_final_after_midnight_is_inactive():
    games = _games([(SUNDAY, "13:00", 3), (SUNDAY, "16:25", -7), (SUNDAY, "20:20", 10)])
    assert sls.is_live_score_window_active(_now(21, 0, 30), games) is False


def test_snf_spilling_past_midnight_not_final_is_active():
    """Kickoff was yesterday 8:20 PM; the game is still unfinal at 12:30 AM."""
    assert sls.is_live_score_window_active(_now(21, 0, 30), _games(SUNDAY_GAMES)) is True


def test_not_final_window_closes_four_and_half_hours_after_last_kickoff():
    games = _games([(SUNDAY, "20:20", None)])
    # 20:20 + 4h30 = 00:50
    assert sls.is_live_score_window_active(_now(21, 0, 45), games) is True
    assert sls.is_live_score_window_active(_now(21, 0, 51), games) is False


def test_yesterday_game_older_than_carryover_is_ignored():
    games = _games([(SUNDAY, "09:30", None)])
    assert sls.is_live_score_window_active(_now(21, 8, 0), games) is False


def test_rows_missing_gametime_are_ignored():
    games = _games([(SUNDAY, None, None)])
    assert sls.is_live_score_window_active(_now(20, 13, 0), games) is False


def test_broken_schedule_fails_open():
    assert sls.is_live_score_window_active(_now(20, 13, 0), pd.DataFrame({"x": [1]})) is True


def test_schedule_loader_prefers_local_rawdata(monkeypatch, tmp_path):
    (tmp_path / "schedules").mkdir()
    (tmp_path / "schedules" / "games.csv").write_text("gameday,gametime,result\n2026-09-20,13:00,\n")
    monkeypatch.setattr(sls, "RAWDATA_DIR", tmp_path)
    monkeypatch.setattr(sls.requests, "get",
                        lambda *a, **k: pytest.fail("must not download when a local copy exists"))

    df = sls._load_schedule_for_window()

    assert list(df["gametime"]) == ["13:00"]


def test_schedule_loader_downloads_when_no_local_copy(monkeypatch, tmp_path):
    """The Cloud Run Job container starts with no rawdata/ at all."""
    monkeypatch.setattr(sls, "RAWDATA_DIR", tmp_path)

    class _Resp:
        text = "gameday,gametime,result\n2026-09-20,13:00,\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(sls.requests, "get", lambda url, timeout: _Resp())

    df = sls._load_schedule_for_window()

    assert list(df["gameday"]) == ["2026-09-20"]


def test_download_failure_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(sls, "RAWDATA_DIR", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(sls.requests, "get", boom)

    assert sls.is_live_score_window_active(_now(20, 3, 0)) is True


def test_main_exits_zero_outside_window_without_touching_firebase(monkeypatch):
    monkeypatch.setattr(sls, "is_live_score_window_active", lambda *a, **k: False)
    monkeypatch.setattr(sls, "initialize_firebase",
                        lambda: pytest.fail("must not init firebase outside the window"))

    with pytest.raises(SystemExit) as exc:
        sls.main([])

    assert exc.value.code == 0


def test_main_force_bypasses_window(monkeypatch):
    monkeypatch.setattr(sls, "is_live_score_window_active", lambda *a, **k: False)
    called = {}

    def fake_init():
        called["init"] = True
        return object()

    monkeypatch.setattr(sls, "initialize_firebase", fake_init)
    monkeypatch.setattr(sls, "sync_authoritative", lambda db: pd.DataFrame())
    monkeypatch.setattr(sls, "run_espn_overlay_safely", lambda db, g: 0)
    monkeypatch.setattr("services.db_service.signal_data_update", lambda *a, **k: None)

    sls.main(["--force"])

    assert called.get("init") is True
