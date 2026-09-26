"""services.db_service._init_firebase owns credential bootstrap for app and scripts (#64)."""
import base64
from unittest.mock import patch

import firebase_admin

from services import db_service


def test_already_initialized_returns_client_without_reinitializing(monkeypatch):
    monkeypatch.setattr(firebase_admin, "_apps", {"[DEFAULT]": object()})
    sentinel = object()
    with patch("firebase_admin.firestore.client", return_value=sentinel), \
         patch("firebase_admin.initialize_app") as mock_init:
        assert db_service._init_firebase() is sentinel
    mock_init.assert_not_called()


def test_env_var_credentials_are_decoded_to_temp_file(monkeypatch):
    monkeypatch.setattr(firebase_admin, "_apps", {})
    monkeypatch.setenv(
        "FIREBASE_CREDENTIALS", base64.b64encode(b'{"project_id": "test"}').decode()
    )
    sentinel = object()
    captured = {}

    def fake_cert(path):
        # The temp file is unlinked right after Certificate(); read it now.
        with open(path) as f:
            captured["content"] = f.read()
        return "cred"

    with patch("firebase_admin.credentials.Certificate", side_effect=fake_cert) as mock_cert, \
         patch("firebase_admin.initialize_app") as mock_init, \
         patch("firebase_admin.firestore.client", return_value=sentinel):
        assert db_service._init_firebase() is sentinel
    mock_cert.assert_called_once()
    mock_init.assert_called_once_with("cred")
    assert captured["content"] == '{"project_id": "test"}'


def test_local_file_fallback_when_no_env_var(monkeypatch):
    monkeypatch.setattr(firebase_admin, "_apps", {})
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    sentinel = object()
    with patch("pathlib.Path.exists", return_value=True), \
         patch("firebase_admin.credentials.Certificate") as mock_cert, \
         patch("firebase_admin.initialize_app") as mock_init, \
         patch("firebase_admin.firestore.client", return_value=sentinel):
        assert db_service._init_firebase() is sentinel
    mock_init.assert_called_once()
    assert mock_cert.call_args[0][0].endswith("firebase_credentials.json")


def test_returns_none_without_env_var_or_file(monkeypatch):
    monkeypatch.setattr(firebase_admin, "_apps", {})
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    with patch("pathlib.Path.exists", return_value=False), \
         patch("firebase_admin.initialize_app") as mock_init:
        assert db_service._init_firebase() is None
    mock_init.assert_not_called()
