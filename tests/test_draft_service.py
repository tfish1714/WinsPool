import os
import pandas as pd
from unittest.mock import patch, mock_open
from services.draft_service import load_draft_state

@patch("services.draft_service.pd.read_csv")
def test_load_draft_state(mock_read_csv):
    """Verify that the draft state resolves successfully against the CSVs without needing local file paths."""
    mock_connected_players = {1, 2, 3}
    
    mock_read_csv.return_value = pd.DataFrame([{"playerId": i} for i in range(10)])
    
    state = load_draft_state(mock_connected_players)
    
    assert "draft_board" in state
    assert "available_teams" in state
    assert "draft_ready" in state
    assert "all_players" in state
    
    # The draft engine now loads True automatically if an order exists.
    assert state["draft_ready"] is True
    assert len(state["all_players"]) > 0
