import pytest
import os
import sys
import uuid
import pandas as pd
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.db_service import get_collection_df, add_player, get_player_by_email, delete_season_data


def _test_email():
    """Generate a unique test email to prevent Firestore collision on re-runs."""
    return f"test_{uuid.uuid4().hex[:8]}@example.com"

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
    test_email = _test_email()
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


# ── Write operation tests ────────────────────────────────────────────────────

class TestDeleteDraftResults:
    """delete_draft_results_for_season removes the season's rows."""

    def test_removes_season_rows(self):
        from services.db_service import delete_draft_results_for_season

        initial_df = pd.DataFrame({
            "season": [2024, 2024, 2025],
            "playerId": [1, 2, 1],
            "team": ["KC", "SF", "BUF"],
        })

        with patch("services.db_service.get_collection_df", return_value=initial_df.copy()) as mock_get, \
             patch("services.db_service._save_df_to_local") as mock_save, \
             patch("services.db_service.clear_data_cache"):
            delete_draft_results_for_season(2024)

        assert mock_save.call_args[0][0] == "draft_results"
        # _save_df_to_local(collection_name, df) — second positional arg is the DataFrame
        saved_df = mock_save.call_args[0][1]
        assert len(saved_df) == 1
        assert (saved_df["season"] == 2024).sum() == 0
        assert (saved_df["season"] == 2025).sum() == 1


class TestDeleteSeasonData:
    """delete_season_data wipes draft_order and draft_order_rules for a season.

    delete_season_data iterates directly over the three collections; it does NOT
    call delete_draft_results_for_season internally — so a single _save_df_to_local
    mock captures all three saves.
    """

    def test_wipes_all_season_collections(self):
        from services.db_service import delete_season_data

        order_df = pd.DataFrame({
            "season": [2024, 2025],
            "player_id": [1, 2],
        })
        rules_df = pd.DataFrame({
            "season": [2024, 2025],
            "rule": ["a", "b"],
        })
        results_df = pd.DataFrame({
            "season": [2024, 2025],
            "player_id": [1, 2],
            "team": ["KC", "BUF"],
        })

        def _get_df(collection, *args, **kwargs):
            if collection == "draft_order":
                return order_df.copy()
            elif collection == "draft_order_rules":
                return rules_df.copy()
            elif collection == "draft_results":
                return results_df.copy()
            return pd.DataFrame()

        saved = {}

        def _save_df(collection, df, *args, **kwargs):
            saved[collection] = df.copy()

        with patch("services.db_service.get_collection_df", side_effect=_get_df), \
             patch("services.db_service._save_df_to_local", side_effect=_save_df), \
             patch("services.db_service.clear_data_cache"), \
             patch("services.db_service.signal_data_update"):
            delete_season_data(2024)

        for collection in ("draft_order", "draft_order_rules", "draft_results"):
            assert collection in saved, f"{collection} was never saved"
            assert (saved[collection]["season"] == 2024).sum() == 0, \
                f"{collection} still contains 2024 rows"


class TestIncrementFailedSetupAttempts:
    """increment_failed_setup_attempts calls update_player_profile with correct keys."""

    def test_sets_attempt_count(self):
        from services.db_service import increment_failed_setup_attempts

        with patch("services.db_service.update_player_profile") as mock_update:
            increment_failed_setup_attempts(player_id=42, new_count=3)

        mock_update.assert_called_once()
        # update_player_profile(player_id, update_data) — both positional
        args, kwargs = mock_update.call_args
        assert args[0] == 42  # player_id is forwarded correctly
        update_data = kwargs.get("update_data") or (args[1] if len(args) > 1 else args[0])
        assert update_data.get("failed_setup_attempts") == 3

    def test_sets_lockout_until_when_provided(self):
        from services.db_service import increment_failed_setup_attempts
        from datetime import datetime, timezone

        lockout = datetime(2026, 1, 1, tzinfo=timezone.utc)

        with patch("services.db_service.update_player_profile") as mock_update:
            increment_failed_setup_attempts(player_id=42, new_count=5, lockout_until=lockout)

        args, kwargs = mock_update.call_args
        assert args[0] == 42  # player_id is forwarded correctly
        update_data = kwargs.get("update_data") or (args[1] if len(args) > 1 else args[0])
        assert update_data.get("failed_setup_attempts") == 5
        assert update_data.get("lockout_until") == lockout


class TestSetMemberPaid:
    """set_member_paid updates the paid flag and returns True/False."""

    def test_returns_true_on_success(self):
        from services.db_service import set_member_paid

        # set_member_paid uses "playerId" and "draftOrder" (camelCase) column names
        order_df = pd.DataFrame({
            "season": [2025, 2025],
            "playerId": [1, 2],
            "draftOrder": [1, 2],
            "paid": [False, False],
        })

        with patch("services.db_service.get_collection_df", return_value=order_df.copy()), \
             patch("services.db_service._save_df_to_local") as mock_save:
            result = set_member_paid(season=2025, player_id=1, paid=True)

        assert result is True
        saved_df = mock_save.call_args[0][1]
        row = saved_df[(saved_df["season"] == 2025) & (saved_df["playerId"] == 1)]
        assert bool(row.iloc[0]["paid"]) is True

    def test_returns_false_when_player_not_found(self):
        from services.db_service import set_member_paid

        order_df = pd.DataFrame({
            "season": [2025],
            "playerId": [99],
            "draftOrder": [1],
            "paid": [False],
        })

        with patch("services.db_service.get_collection_df", return_value=order_df.copy()), \
             patch("services.db_service._save_df_to_local") as mock_save:
            result = set_member_paid(season=2025, player_id=1, paid=True)

        assert result is False
        mock_save.assert_not_called()
