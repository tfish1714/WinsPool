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


class TestEloHistoryCache:
    """Tests for get_elo_history_season / get_all_elo_history / write_elo_history_season."""

    def test_write_then_read_local_season(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_elo_history_season, get_elo_history_season

        rows = [{"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF", "home_elo_post": 1520.0}]
        write_elo_history_season(2025, rows, use_local=True)

        result = get_elo_history_season(2025)
        assert result == rows

    def test_get_nonexistent_season_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import get_elo_history_season
        assert get_elo_history_season(2099) is None

    def test_get_all_combines_and_sorts_seasons(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_elo_history_season, get_all_elo_history

        write_elo_history_season(2025, [{"season": 2025, "week": 1, "home_team": "KC"}], use_local=True)
        write_elo_history_season(2006, [{"season": 2006, "week": 1, "home_team": "PIT"}], use_local=True)

        all_rows = get_all_elo_history()
        assert [r["season"] for r in all_rows] == [2006, 2025]

    def test_write_firestore_writes_document(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)

        from services.cache_service import write_elo_history_season

        rows = [{"season": 2025, "week": 1, "home_team": "KC"}]
        write_elo_history_season(2025, rows, use_local=False)

        mock_firestore.collection.assert_called_with("elo_history")
        mock_firestore.collection.return_value.document.assert_called_with("2025")
        mock_firestore.collection.return_value.document.return_value.set.assert_called_with({
            "season": 2025, "rows": rows,
        })

    def test_get_all_firestore_streams_every_doc(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", False)

        from services.cache_service import get_all_elo_history

        doc_2006 = MagicMock()
        doc_2006.to_dict.return_value = {"season": 2006, "rows": [{"season": 2006, "week": 1}]}
        doc_2025 = MagicMock()
        doc_2025.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        mock_firestore.collection.return_value.stream.return_value = [doc_2025, doc_2006]

        all_rows = get_all_elo_history()
        assert [r["season"] for r in all_rows] == [2006, 2025]


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


class TestMergeThinGamePredictions:
    """Tests for merge_thin_game_predictions -- the fix for cache_builder.py
    silently overwriting the richer explanation/model_spread/edge_vs_vegas/locked
    fields that scripts/backfill_schedule_predictions.py writes."""

    def test_preserves_explanation_from_existing(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {
            "W01_KC_SF": {
                "pred_winner": "KC", "pred_su_conf": 60.0, "pred_ats_pick": "KC",
                "pred_prob": 0.6, "model_spread": 3.0, "edge_vs_vegas": 0.5,
                "locked": True,
                "explanation": {"elo_diff": 42.0, "vegas_line": 2.5},
            }
        }
        fresh = {
            "W01_KC_SF": {
                "pred_winner": "KC", "pred_su_conf": 61.0,
                "pred_ats_pick": "KC", "pred_prob": 0.61,
            }
        }

        merged = merge_thin_game_predictions(existing, fresh)

        assert merged["W01_KC_SF"]["pred_su_conf"] == 61.0  # fresh value wins
        assert merged["W01_KC_SF"]["explanation"] == {"elo_diff": 42.0, "vegas_line": 2.5}  # preserved
        assert merged["W01_KC_SF"]["model_spread"] == 3.0  # preserved
        assert merged["W01_KC_SF"]["locked"] is True  # preserved

    def test_creates_new_entry_when_key_not_in_existing(self):
        from services.cache_service import merge_thin_game_predictions

        merged = merge_thin_game_predictions(
            {}, {"W02_BUF_MIA": {"pred_winner": "BUF", "pred_prob": 0.7}}
        )
        assert merged == {"W02_BUF_MIA": {"pred_winner": "BUF", "pred_prob": 0.7}}

    def test_preserves_existing_keys_not_touched_by_fresh(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {"W01_KC_SF": {"pred_winner": "KC"}, "W02_BUF_MIA": {"pred_winner": "BUF"}}
        merged = merge_thin_game_predictions(existing, {"W01_KC_SF": {"pred_winner": "SF"}})

        assert merged["W01_KC_SF"]["pred_winner"] == "SF"
        assert merged["W02_BUF_MIA"]["pred_winner"] == "BUF"  # untouched, still present

    def test_does_not_mutate_inputs(self):
        from services.cache_service import merge_thin_game_predictions

        existing = {"W01_KC_SF": {"pred_winner": "KC", "explanation": {"elo_diff": 1.0}}}
        fresh = {"W01_KC_SF": {"pred_winner": "SF"}}
        merge_thin_game_predictions(existing, fresh)

        assert existing["W01_KC_SF"]["pred_winner"] == "KC"  # original untouched
