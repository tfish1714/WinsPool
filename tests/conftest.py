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
    Mocks the lazy-loaded _load_genai helper in ai_service.
    """
    mock_model = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Mocked AI Weekly Summary Result."
    mock_model.generate_content.return_value = mock_response

    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    with patch("services.ai_service._load_genai") as mock_loader:
        mock_loader.return_value = {"sdk": "old", "mod": mock_genai}
        yield mock_genai
