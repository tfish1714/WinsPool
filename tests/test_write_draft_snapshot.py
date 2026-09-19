"""scripts/write_draft_snapshot.py -- thin CLI wrapper around
services.db_service.sync_draft_snapshot_for_season(). All copy/lock logic
lives in that shared function (see tests/test_consensus_storage.py's
sync_draft_snapshot_for_season tests) -- this file only tests argument
parsing and the print branches."""
from unittest.mock import patch

from scripts.write_draft_snapshot import main


def test_main_parses_season_and_calls_sync(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 32, "locked": False}
        main()
    mock_sync.assert_called_once_with(2026)


def test_main_prints_locked_state(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 32, "locked": True}
        main()
    out = capsys.readouterr().out
    assert "locked" in out.lower()
    assert "32" in out


def test_main_prints_skip_when_nothing_written(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 0, "locked": False}
        main()
    out = capsys.readouterr().out
    assert "skip" in out.lower()
