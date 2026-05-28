"""tests/test_set_member_paid.py — Unit tests for db_service.set_member_paid()."""
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

from services.db_service import set_member_paid


def _draft_order_df(season=2024, player_id=1, draft_order=2, paid=False):
    """Minimal draft_order DataFrame matching set_member_paid's expected schema."""
    return pd.DataFrame([{
        "season": season,
        "playerId": player_id,
        "draftOrder": draft_order,
        "paid": paid,
    }])


class TestSetMemberPaid:

    def test_happy_path_returns_true_and_calls_firestore(self):
        """Happy path: player found → returns True and calls Firestore update."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df()), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, True)
        assert result is True
        mock_db.collection("draft_order").document("2024_2").update.assert_called_once_with({"paid": True})

    def test_sets_paid_false(self):
        """paid=False is passed correctly to Firestore."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(paid=True)), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, False)
        assert result is True
        mock_db.collection("draft_order").document("2024_2").update.assert_called_once_with({"paid": False})

    def test_returns_false_when_player_not_in_draft_order(self):
        """Player ID not in draft_order → returns False without touching Firestore."""
        mock_db = MagicMock()
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(player_id=99)), \
             patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.db_service._save_df_to_local") as mock_save:
            result = set_member_paid(2024, 1, True)  # player_id=1 not in df
        assert result is False
        mock_db.collection.assert_not_called()
        mock_save.assert_not_called()

    def test_returns_false_when_draft_order_empty(self):
        """Empty draft_order → returns False immediately."""
        with patch("services.db_service.get_collection_df", return_value=pd.DataFrame()):
            result = set_member_paid(2024, 1, True)
        assert result is False

    def test_local_pkl_updated(self):
        """_save_df_to_local is called with the updated DataFrame."""
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df(paid=False)), \
             patch("services.db_service.get_db", return_value=MagicMock()), \
             patch("services.db_service._save_df_to_local") as mock_save:
            set_member_paid(2024, 1, True)
        assert mock_save.call_count == 1
        saved_df = mock_save.call_args[0][1]  # second positional arg is the DataFrame
        assert bool(saved_df.loc[0, "paid"]) is True

    def test_no_db_call_when_firestore_unavailable(self):
        """If get_db() returns None, Firestore update is skipped but returns True."""
        with patch("services.db_service.get_collection_df", return_value=_draft_order_df()), \
             patch("services.db_service.get_db", return_value=None), \
             patch("services.db_service._save_df_to_local"):
            result = set_member_paid(2024, 1, True)
        assert result is True
