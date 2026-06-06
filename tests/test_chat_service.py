"""tests/test_chat_service.py"""
import pytest
from unittest.mock import MagicMock, patch


class TestPostSystemMessage:
    def test_writes_system_type_to_firestore(self):
        from services.chat_service import post_system_message
        mock_col = MagicMock()
        mock_doc = MagicMock()
        mock_col.return_value.add.return_value = (None, mock_doc)
        mock_doc.get.return_value.to_dict.return_value = {
            "type": "system", "playerName": "System",
            "text": "Test msg", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection = mock_col
            result = post_system_message(2026, "Test msg")
        assert result["type"] == "system"
        assert result["playerName"] == "System"
        assert result["text"] == "Test msg"

    def test_returns_none_on_firestore_error(self):
        from services.chat_service import post_system_message
        with patch("services.chat_service.get_db", side_effect=Exception("db down")):
            result = post_system_message(2026, "Test msg")
        assert result is None


class TestPostChatMessage:
    def test_writes_chat_type_with_player_name(self):
        from services.chat_service import post_chat_message
        mock_col = MagicMock()
        mock_doc = MagicMock()
        mock_col.return_value.add.return_value = (None, mock_doc)
        mock_doc.get.return_value.to_dict.return_value = {
            "type": "chat", "playerName": "Alice",
            "text": "Hello", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection = mock_col
            result = post_chat_message(2026, "Alice", "Hello")
        assert result["type"] == "chat"
        assert result["playerName"] == "Alice"


class TestGetRecentMessages:
    def test_returns_list_of_dicts(self):
        from services.chat_service import get_recent_messages
        mock_snap = MagicMock()
        mock_snap.to_dict.return_value = {
            "type": "system", "playerName": "System",
            "text": "Pick made", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection.return_value\
                .order_by.return_value.limit.return_value\
                .stream.return_value = [mock_snap]
            result = get_recent_messages(2026, limit=50)
        assert isinstance(result, list)
        assert result[0]["type"] == "system"

    def test_returns_empty_list_on_error(self):
        from services.chat_service import get_recent_messages
        with patch("services.chat_service.get_db", side_effect=Exception("db down")):
            result = get_recent_messages(2026)
        assert result == []
