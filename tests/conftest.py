import os
import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import sys
import pathlib

# Set USE_LOCAL_DATA before any service modules are imported so that
# db_service.py's module-level Firebase init check reads the right value.
# Using setdefault so a real Firestore env (USE_LOCAL_DATA=False) is not
# overridden when running against production data intentionally.
os.environ.setdefault("USE_LOCAL_DATA", "true")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-winspool-tests-only")

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
def sample_games_df():
    """Minimal NFL games DataFrame (3 rows) for unit tests.
    Covers: 2 completed games (week 1) and 1 unplayed game (week 2, result=-1000).
    """
    return pd.DataFrame([
        {"season": 2024, "week": 1, "game_type": "REG", "home_team": "KC",  "away_team": "BUF",
         "home_score": 27, "away_score": 24, "result": 3.0,   "spread_line": -2.5},
        {"season": 2024, "week": 1, "game_type": "REG", "home_team": "PHI", "away_team": "DAL",
         "home_score": 22, "away_score": 16, "result": 6.0,   "spread_line":  3.5},
        {"season": 2024, "week": 2, "game_type": "REG", "home_team": "SF",  "away_team": "SEA",
         "home_score": 0,  "away_score": 0,  "result": -1000, "spread_line": -4.0},
    ])


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


@pytest.fixture
def prediction_games_df():
    """7-game DataFrame across 2 seasons (2023 + 2024) for Elo/prediction tests."""
    return pd.DataFrame([
        # 2023 Season
        {"game_id": "2023_01_KC_DET",  "season": 2023, "week": 1, "game_type": "REG",
         "home_team": "KC",  "away_team": "DET", "home_score": 21, "away_score": 20, "result": 1},
        {"game_id": "2023_01_BUF_NYJ", "season": 2023, "week": 1, "game_type": "REG",
         "home_team": "BUF", "away_team": "NYJ", "home_score": 22, "away_score": 16, "result": 6},
        {"game_id": "2023_02_DET_KC",  "season": 2023, "week": 2, "game_type": "REG",
         "home_team": "DET", "away_team": "KC",  "home_score": 31, "away_score": 17, "result": 14},
        {"game_id": "2023_02_NYJ_BUF", "season": 2023, "week": 2, "game_type": "REG",
         "home_team": "NYJ", "away_team": "BUF", "home_score": 10, "away_score": 24, "result": -14},
        # 2024 Season
        {"game_id": "2024_01_KC_BUF",  "season": 2024, "week": 1, "game_type": "REG",
         "home_team": "KC",  "away_team": "BUF", "home_score": 27, "away_score": 20, "result": 7},
        {"game_id": "2024_01_DET_NYJ", "season": 2024, "week": 1, "game_type": "REG",
         "home_team": "DET", "away_team": "NYJ", "home_score": 35, "away_score": 14, "result": 21},
        {"game_id": "2024_02_BUF_KC",  "season": 2024, "week": 2, "game_type": "REG",
         "home_team": "BUF", "away_team": "KC",  "home_score": None, "away_score": None, "result": None},
    ])
