import os
import pandas as pd
from services.draft_service import load_draft_state, CONFIG_PATH

def test_load_draft_state():
    """Verify that the draft state resolves successfully against the CSVs."""
    mock_connected_players = {1, 2, 3}
    state = load_draft_state(mock_connected_players)
    
    assert "draft_board" in state
    assert "available_teams" in state
    assert "draft_ready" in state
    assert "all_players" in state
    
    # We passed 3, but there are 10 players, so draft_ready should be False
    assert state["draft_ready"] is False
    
    # Check all_players length
    players_df = pd.read_csv(os.path.join(CONFIG_PATH, "WinsPoolPlayers.csv"))
    assert len(state["all_players"]) == len(players_df)
