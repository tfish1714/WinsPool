"""Scripts share services.db_service.get_db() instead of decoding credentials themselves (#64)."""
import importlib
import os
import pathlib
from unittest.mock import MagicMock, patch

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# (module, init function name, exception raised when no client is available)
CASES = [
    ("scripts.daily_nfl_sync", "initialize_firebase", SystemExit),
    ("scripts.backfill_schedule_predictions", "_init_firestore", FileNotFoundError),
    ("scripts.predict_season", "_init_firebase", SystemExit),
    ("scripts.generate_weekly_predictions", "_init_firebase", SystemExit),
    ("scripts.upload_configfiles", "_init_firebase", SystemExit),
]


@pytest.mark.parametrize("module_name,_func,_exc", CASES)
def test_script_source_has_no_duplicated_credential_bootstrap(module_name, _func, _exc):
    path = ROOT / (module_name.replace(".", "/") + ".py")
    text = path.read_text(encoding="utf-8")
    assert "initialize_app" not in text
    assert "b64decode" not in text


@pytest.mark.parametrize("module_name,func,_exc", CASES)
def test_init_returns_shared_db_client_and_forces_remote_mode(monkeypatch, module_name, func, _exc):
    mod = importlib.import_module(module_name)
    fake_db = MagicMock(name="db")
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.setattr(mod, "get_db", lambda: fake_db)
    assert getattr(mod, func)() is fake_db
    assert os.environ["USE_LOCAL_DATA"].lower() == "false"


@pytest.mark.parametrize("module_name,func,exc", CASES)
def test_init_fails_loudly_without_credentials(monkeypatch, module_name, func, exc):
    mod = importlib.import_module(module_name)
    monkeypatch.setattr(mod, "get_db", lambda: None)
    with pytest.raises(exc):
        getattr(mod, func)()


@pytest.mark.parametrize("module_name,func,exc", CASES)
def test_init_fails_loudly_on_real_no_credentials_path(monkeypatch, module_name, func, exc):
    """Drive the real get_db(): with no credentials it raises ValueError from
    firestore.client(), which the scripts must translate into their failure mode."""
    import firebase_admin

    mod = importlib.import_module(module_name)
    monkeypatch.setattr(firebase_admin, "_apps", {})
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(exc) as info:
            getattr(mod, func)()
    if exc is SystemExit:
        assert info.value.code == 1
