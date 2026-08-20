from unittest.mock import patch
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
    from scripts.cache_builder import _run_with_alerting


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
