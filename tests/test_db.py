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
    """Test that add_player creates a new player record with correct fields.

    All storage calls (Firestore, local pkl) are patched so this test never
    touches the real database or the .local_db/ pickle files.
    """
    test_email = "test_stability_verify@example.com"
    test_name = "Stability Test User"
    test_nick = "StableNick"

    # Seed: one existing player so we can verify the ID-increment logic.
    existing = pd.DataFrame([{
        "playerId": 5, "fullName": "Real Player", "nickName": "Real",
        "email": "real@example.com", "cell": "", "role": "user",
        "failed_setup_attempts": 0,
    }])

    # In-memory store that captures what _save_df_to_local writes so that
    # subsequent get_collection_df calls see the newly-added player.
    saved_dfs = {}

    def fake_save(collection, df):
        saved_dfs[collection] = df.copy()

    def fake_get_collection(collection, **kwargs):
        if collection in saved_dfs:
            return saved_dfs[collection]
        if collection == "players":
            return existing.copy()
        return pd.DataFrame()

    with patch("services.db_service.get_collection_df", side_effect=fake_get_collection), \
         patch("services.db_service._save_df_to_local", side_effect=fake_save), \
         patch("services.db_service.clear_data_cache"), \
         patch("services.db_service.signal_data_update"):

        new_id = add_player(test_name, test_nick, test_email)
        assert new_id is not None
        assert new_id == 6  # max existing playerId was 5 → new = 6

        player = get_player_by_email(test_email)
        assert player is not None
        assert player["fullName"] == test_name
        assert player["nickName"] == test_nick

def test_draft_order_integrity():
    """Verify draft order collection retrieval."""
    df = get_collection_df("draft_order")
    assert isinstance(df, pd.DataFrame)
    # Even if empty, it should return a standardized DF or at least not crash
