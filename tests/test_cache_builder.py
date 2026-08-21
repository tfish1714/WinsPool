import sys
from unittest.mock import patch, MagicMock
import pytest

# scripts/cache_builder.py always talks to Firestore directly (never the local
# pkl cache — see its module docstring) and performs a module-level Firebase
# Admin SDK init that calls sys.exit(1) if no firebase_credentials.json is
# present. That file is gitignored and isn't guaranteed to exist in every dev/
# CI checkout (e.g. an isolated git worktree), so fake an already-initialized
# app just for the duration of the import to skip that guard. Restored
# immediately after import; nothing in these tests exercises real Firestore
# calls since main() is always mocked.
import firebase_admin as _firebase_admin
with patch.object(_firebase_admin, "_apps", {"__test__": object()}), \
     patch("firebase_admin.firestore.client"):
    from scripts.cache_builder import _run_with_alerting, _sync_rawdata, main


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main", side_effect=RuntimeError("model load failed"))
def test_main_failure_sends_alert_and_reraises(mock_main, mock_alert):
    with pytest.raises(RuntimeError):
        _run_with_alerting()

    mock_alert.assert_called_once()
    subject, message = mock_alert.call_args[0]
    assert "winspool-predict-daily" in subject
    assert "model load failed" in message


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main")
def test_main_success_does_not_alert(mock_main, mock_alert):
    _run_with_alerting()
    mock_alert.assert_not_called()


# winspool-predict-daily runs in its own, separate Cloud Run Job container
# from winspool-sync-daily -- no shared filesystem between them, so whatever
# rawdata/ winspool-sync-daily downloaded is gone by the time this job
# starts. _sync_rawdata() closes that gap.
class TestSyncRawdata:
    @patch("scripts.cache_builder.subprocess.run")
    def test_success_prints_no_warning(self, mock_run, capsys):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _sync_rawdata()
        assert "[warn]" not in capsys.readouterr().out

    @patch("scripts.cache_builder.subprocess.run")
    def test_failure_is_non_fatal(self, mock_run, capsys):
        """Must not raise -- a sync failure shouldn't abort the whole job;
        any resulting missing-file error surfaces naturally downstream."""
        mock_run.return_value = MagicMock(returncode=1, stderr="404 not found")
        _sync_rawdata()  # should not raise
        assert "[warn]" in capsys.readouterr().out


class TestMainSyncWiring:
    @patch("scripts.cache_builder.load_data", side_effect=RuntimeError("stop after sync check"))
    @patch("scripts.cache_builder._sync_rawdata")
    def test_syncs_rawdata_by_default(self, mock_sync, mock_load, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cache_builder.py"])
        with pytest.raises(RuntimeError):
            main()
        mock_sync.assert_called_once()

    @patch("scripts.cache_builder.load_data", side_effect=RuntimeError("stop after sync check"))
    @patch("scripts.cache_builder._sync_rawdata")
    def test_skip_sync_flag_skips_it(self, mock_sync, mock_load, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        with pytest.raises(RuntimeError):
            main()
        mock_sync.assert_not_called()
