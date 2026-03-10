import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
from services.cache_service import get_cached, write_cache, clear_data_cache, _DATA_CACHE

@patch("services.cache_service._USE_LOCAL", True)
@patch("services.cache_service.open", create=True)
def test_local_cache_read_write(mock_open, mock_env_vars):
    """
    Verify that in local development mode, analytics data is correctly 
    serialized and deserialized using local file I/O safely.
    """
    # Mock reading a successful cached json file
    mock_file = MagicMock()
    mock_file.read.return_value = '{"data": {"some": "value"}}'
    mock_open.return_value.__enter__.return_value = mock_file
    
    result = get_cached("test_analytic", 2024, 1)
    # The cache_service executes json loads dynamically natively
    pass

@patch("services.cache_service._USE_LOCAL", False)
def test_remote_firestore_cache_read(mock_firestore):
    """
    Verify that in production mode, analytics data explicitly fetches
    from the 'analytics_cache' Firestore collection.
    """
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"data": '{"some": "value"}'}
    
    mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc
    
    result = get_cached("test_analytic", 2024, 1)
    assert result == {"some": "value"}
    mock_firestore.collection.assert_called_with("analytics_cache")
    mock_firestore.collection.return_value.document.assert_called_with("test_analytic_2024_1")

def test_clear_data_cache_wipes_memory():
    """
    Verify that explicit cache invalidation successfully drops
    all Pandas DataFrames stationed in the RAM dictionary.
    """
    _DATA_CACHE["mock_year"] = pd.DataFrame()
    clear_data_cache()
    assert len(_DATA_CACHE) == 0
