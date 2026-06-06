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


def test_draft_board_entries_include_time_taken():
    """Each draft board entry must have a time_taken_seconds key (float or None)."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    for entry in state['draft_board']:
        assert 'time_taken_seconds' in entry, f"missing time_taken_seconds on pick {entry['pick']}"
        assert entry['time_taken_seconds'] is None or isinstance(entry['time_taken_seconds'], float)


def test_state_includes_connected_count():
    """State must include connected_count equal to the size of the connected_players set."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    assert state['connected_count'] == 3

    state2 = load_draft_state(set())
    assert state2['connected_count'] == 0
