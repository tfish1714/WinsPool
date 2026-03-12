import pytest
import pandas as pd
from unittest.mock import patch
from services.recap_service import extract_draft_data

@patch("services.recap_service.load_data")
@patch("services.recap_service.get_preseason_predictions")
def test_extract_draft_data_filters_by_year(mock_preds, mock_load):
    """
    Verify that extract_draft_data only includes picks for the requested year
    when the load_data returns picks from multiple seasons.
    """
    # Setup: 2024 has 3 picks for Tom, 2023 has many more.
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "SEA", "playerId": 1},
        {"season": 2024, "team": "CHI", "playerId": 1},
        {"season": 2024, "team": "CIN", "playerId": 1},
        {"season": 2023, "team": "NYG", "playerId": 1},
        {"season": 2023, "team": "BUF", "playerId": 1},
        {"season": 2023, "team": "SF", "playerId": 1},
    ])
    
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Tom Fischer", "email": "tom@test.com"}
    ])
    
    # Other returned values don't matter much for this specific logic check
    mock_load.return_value = (None, None, None, players, None, draft_results, None)
    mock_preds.return_value = {} # No preds needed for this test

    # Act
    summary, emails = extract_draft_data(2024)

    # Assert
    assert "PLAYER: Tom Fischer" in summary
    assert "DRAFTED TEAMS: SEA, CHI, CIN" in summary
    assert "NYG" not in summary
    assert "BUF" not in summary
    assert "SF" not in summary
    
    # Verify exact match for drafted teams line to ensure no extra commas or items
    expected_line = "DRAFTED TEAMS: SEA, CHI, CIN"
    assert expected_line in summary
