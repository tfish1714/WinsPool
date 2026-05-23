import pytest
from unittest.mock import patch, MagicMock
import sys
import pathlib

# Ensure the workspace root is injected into the Python path dynamically
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """
    Globally ensures the test suite runs offline and does not
    hit live production databases or APIs.
    """
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test_mock_key")
    # Provide a deterministic JWT secret so session_service doesn't raise in tests.
    # This is intentionally weak — test-only, never used outside the test suite.
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-for-winspool-tests-only")


@pytest.fixture
def auth_token(monkeypatch):
    """A valid JWT Bearer token for a regular player (role='user')."""
    from services.session_service import create_token
    return f"Bearer {create_token(player_id=1, role='user')}"


@pytest.fixture
def admin_token(monkeypatch):
    """A valid JWT Bearer token for an admin player (role='admin')."""
    from services.session_service import create_token
    return f"Bearer {create_token(player_id=1, role='admin')}"

@pytest.fixture
def mock_firestore():
    """
    Centralized mock for Google Cloud Firestore.
    Replaces get_db() across all services.
    """
    with patch("services.db_service.get_db") as mock_get_db:
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        yield mock_db

@pytest.fixture
def mock_gemini():
    """
    Centralized mock for the Google Generative AI Gemini API.
    Isolates AI logic from internet dependencies.
    Patches the google.generativeai module that ai_service imports at call time.
    """
    with patch("google.generativeai.configure"), \
         patch("google.generativeai.GenerativeModel") as mock_model:
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Mocked AI Weekly Summary Result."
        mock_instance.generate_content.return_value = mock_response
        mock_model.return_value = mock_instance
        yield mock_model
