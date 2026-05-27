import pytest
import pandas as pd
import time
from unittest.mock import patch, MagicMock
from services.cache_service import get_cached, write_cache, clear_data_cache, _DATA_CACHE

def test_local_cache_read_write(tmp_path, monkeypatch):
    """Round-trip: write analytics cache entry, read it back, assert equal."""
    monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
    monkeypatch.setattr("services.cache_service._LOCAL_CACHE_DIR", tmp_path)
    from services.cache_service import write_cache, get_cached
    payload = {"test_key": 42, "nested": {"a": 1}}
    write_cache("test_metric", 2024, 1, payload)
    result = get_cached("test_metric", 2024, 1)
    assert result == payload

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


class TestPredictionFeaturesCache:
    """Tests for get_prediction_features / write_prediction_features."""

    def test_write_then_read_local(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_prediction_features, get_prediction_features

        games_data = {
            "W08_KC_SF": {
                "game_key": "W08_KC_SF", "season": 2025, "week": 8,
                "away_team": "KC", "home_team": "SF",
                "nn_prob": 0.62, "xgb_prob": 0.58, "lr_prob": 0.60, "blended_prob": 0.61,
                "features": {"tm_elo_pre": 1550.0}, "scaled_features": {"tm_elo_pre": 0.81},
                "feature_importance": [{"feature": "tm_elo_pre", "score": 0.31, "direction": "home"}],
            }
        }
        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", games_data)

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc is not None
        assert doc["season"] == 2025
        assert doc["ensemble_version"] == "nn_v10+xgb_v4+lr_v2"
        assert "W08_KC_SF" in doc["games"]

    def test_get_latest_returns_most_recent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_prediction_features, get_prediction_features

        write_prediction_features(2025, "nn_v9+xgb_v3+lr_v1", {"W01_BUF_MIA": {}})
        time.sleep(0.01)  # ensure different mtime
        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_BUF_MIA": {"newer": True}})

        doc = get_prediction_features(2025)  # no version -> latest by mtime
        assert doc is not None
        assert doc["ensemble_version"] == "nn_v10+xgb_v4+lr_v2"
        assert doc["games"]["W01_BUF_MIA"].get("newer") is True

    def test_get_nonexistent_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import get_prediction_features
        assert get_prediction_features(2099, "nn_v1+xgb_v1+lr_v1") is None


def test_merge_game_predictions_includes_edge_vs_vegas():
    """merge_game_predictions must propagate edge_vs_vegas from prediction dict."""
    import pandas as pd
    from unittest.mock import patch
    from services.cache_service import merge_game_predictions

    df = pd.DataFrame([{
        'week': 3, 'home_team': 'KC', 'away_team': 'BUF', 'season': 2024,
    }])
    mock_preds = {
        'W03_KC_BUF': {
            'pred_winner': 'KC',
            'pred_su_conf': 68.0,
            'pred_ats_pick': 'KC',
            'pred_prob': 0.68,
            'edge_vs_vegas': 4.5,
            'model_spread': 7.0,
        }
    }
    with patch('services.cache_service.get_game_predictions', return_value=mock_preds):
        result = merge_game_predictions(df, 2024)

    assert 'edge_vs_vegas' in result.columns
    assert result.iloc[0]['edge_vs_vegas'] == 4.5
