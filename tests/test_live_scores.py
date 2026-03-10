import pytest
import pandas as pd
from unittest.mock import patch
from services.live_score_service import sync_live_scores_to_df

def test_sync_live_scores_handles_empty_df():
    """Verify that an empty DataFrame returns safely without KeyError."""
    empty_df = pd.DataFrame()
    result = sync_live_scores_to_df(empty_df)
    assert result.empty

@patch("services.live_score_service.get_live_updates")
@patch("services.db_service.signal_data_update")
def test_sync_live_scores_triggers_cache_rebuild_on_final(mock_signal, mock_get_live):
    """
    Verify that when an ESPN game transitions to 'STATUS_FINAL', the 
    service natively triggers a metadata cache rebuild via signal_data_update.
    """
    # Create a dummy DataFrame where a game is currently 'in progress'
    df = pd.DataFrame([
        {"home_team": "BUF", "away_team": "MIA", "is_live": True, "home_score": 0, "away_score": 0}
    ])
    
    # Mock ESPN returning the game as FINAL
    mock_get_live.return_value = {
        ("BUF", "MIA"): {
            "status": "STATUS_FINAL",
            "home_score": 24,
            "away_score": 21,
            "clock": "0:00",
            "period": 4
        }
    }
    
    result_df = sync_live_scores_to_df(df)
    
    # Verify the DataFrame mutation
    assert result_df.at[0, "is_live"] == False
    assert result_df.at[0, "is_final_live"] == True
    assert result_df.at[0, "home_score"] == 24
    assert result_df.at[0, "result"] == 3  # 24 - 21
    
    # CRITICAL: Verify the cache rebuild signal was fired
    mock_signal.assert_called_once()
