import pytest
import os
import sys
import pandas as pd
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.db_service import get_collection_df, add_player, get_player_by_email, delete_season_data

def test_db_players_structure():
    """Verify that players collection can be retrieved as a DataFrame."""
    df = get_collection_df("players")
    assert isinstance(df, pd.DataFrame)
    if not df.empty:
        assert "playerId" in df.columns
        assert "fullName" in df.columns

def test_player_exists_helper():
    """Verify get_player_by_email finds an existing player."""
    # Note: This assumes at least one player exists in the DB/Pickle
    df = get_collection_df("players")
    if not df.empty:
        email = df.iloc[0]["email"]
        player = get_player_by_email(email)
        assert player is not None
        assert player["email"] == email

def test_add_player_integrity():
    """Test adding a temporary player and verifying existence."""
    test_email = "test_stability_verify@example.com"
    test_name = "Stability Test User"
    test_nick = "StableNick"
    
    # Ensure no collision
    # (Implementation Note: In a real CI environment, we would use a separate Test DB)
    new_id = add_player(test_name, test_nick, test_email)
    assert new_id is not None
    
    player = get_player_by_email(test_email)
    assert player is not None
    assert player["fullName"] == test_name
    assert player["nickName"] == test_nick

def test_draft_order_integrity():
    """Verify draft order collection retrieval."""
    df = get_collection_df("draft_order")
    assert isinstance(df, pd.DataFrame)
    # Even if empty, it should return a standardized DF or at least not crash
