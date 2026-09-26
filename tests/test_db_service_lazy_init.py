"""db_service must not initialize Firebase at import time; get_db() returns None
without credentials; require_db() centralizes the script init/fail-loud pattern."""
import importlib
import os
from unittest.mock import MagicMock, patch

import firebase_admin
import pytest

import services.db_service as db_service


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_DATA", "False")
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    monkeypatch.setattr(firebase_admin, "_apps", {})
    with patch("pathlib.Path.exists", return_value=False):
        yield


def test_get_db_returns_none_without_credentials(no_creds):
    assert db_service.get_db() is None


def test_import_does_not_initialize_firebase(no_creds):
    with patch("firebase_admin.initialize_app") as mock_init:
        importlib.reload(db_service)
    mock_init.assert_not_called()


def test_require_db_exits_when_missing(monkeypatch):
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    with pytest.raises(SystemExit) as info:
        db_service.require_db(exit_on_missing=True)
    assert info.value.code == 1


def test_require_db_raises_exc_type_when_missing(monkeypatch):
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    with pytest.raises(FileNotFoundError):
        db_service.require_db(exit_on_missing=False, exc_type=FileNotFoundError)


def test_require_db_returns_client_and_forces_remote(monkeypatch):
    fake = MagicMock()
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.setattr(db_service, "get_db", lambda: fake)
    assert db_service.require_db() is fake
    assert os.environ["USE_LOCAL_DATA"] == "False"


def test_require_db_honors_getter(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    assert db_service.require_db(getter=lambda: fake) is fake
