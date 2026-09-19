import pytest
import pandas as pd
import time
from unittest.mock import patch, MagicMock
from services.cache_service import clear_data_cache

def test_set_and_get_domain_round_trips():
    from services.cache_service import set_domain, get_domain, DOMAIN_ACTIVE, clear_domain
    clear_domain(DOMAIN_ACTIVE)  # isolate from other tests
    assert get_domain(DOMAIN_ACTIVE) is None
    set_domain(DOMAIN_ACTIVE, {"games": "placeholder"})
    assert get_domain(DOMAIN_ACTIVE) == {"games": "placeholder"}

def test_clear_domain_removes_only_that_domain():
    from services.cache_service import set_domain, clear_domain, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "active-value")
    set_domain(DOMAIN_STATIC, "static-value")
    clear_domain(DOMAIN_ACTIVE)
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) == "static-value"
    clear_domain(DOMAIN_STATIC)  # cleanup

def test_clear_data_cache_with_domain_clears_only_that_domain():
    from services.cache_service import set_domain, clear_data_cache, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "a")
    set_domain(DOMAIN_STATIC, "s")
    clear_data_cache(DOMAIN_ACTIVE)
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) == "s"
    clear_data_cache(DOMAIN_STATIC)  # cleanup

def test_clear_data_cache_with_no_domain_wipes_everything():
    from services.cache_service import set_domain, clear_data_cache, get_domain, DOMAIN_ACTIVE, DOMAIN_STATIC
    set_domain(DOMAIN_ACTIVE, "a")
    set_domain(DOMAIN_STATIC, "s")
    clear_data_cache()
    assert get_domain(DOMAIN_ACTIVE) is None
    assert get_domain(DOMAIN_STATIC) is None

def test_get_domain_timestamp_defaults_to_zero_when_unset():
    from services.cache_service import get_domain_timestamp, clear_domain, DOMAIN_HISTORICAL
    clear_domain(DOMAIN_HISTORICAL)
    assert get_domain_timestamp(DOMAIN_HISTORICAL) == 0

def test_domain_signal_fields_cover_every_domain_constant():
    """Every domain constant must have exactly one signal field -- catches a
    typo'd or missing entry before it ships."""
    from services.cache_service import (
        DOMAIN_SIGNAL_FIELDS, DOMAIN_ACTIVE, DOMAIN_HISTORICAL, DOMAIN_STATIC,
        DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL, DOMAIN_ADMIN_ANALYTICS,
    )
    expected = {DOMAIN_ACTIVE, DOMAIN_HISTORICAL, DOMAIN_STATIC,
                DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL, DOMAIN_ADMIN_ANALYTICS}
    assert set(DOMAIN_SIGNAL_FIELDS.keys()) == expected
    assert len(set(DOMAIN_SIGNAL_FIELDS.values())) == len(expected)  # no duplicate field names

def test_clear_data_cache_wipes_all_domains():
    from services.cache_service import set_domain, get_domain, clear_data_cache, DOMAIN_ACTIVE
    set_domain(DOMAIN_ACTIVE, pd.DataFrame())
    clear_data_cache()
    assert get_domain(DOMAIN_ACTIVE) is None


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

    def test_write_stamps_feature_version_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)
        from unittest.mock import patch
        from services.cache_service import write_prediction_features, get_prediction_features

        with patch("services.model_version.get_feature_version", return_value="abc1234"):
            write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_KC_SF": {}})

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc["feature_version"] == "abc1234"

    def test_write_accepts_explicit_feature_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)
        from services.cache_service import write_prediction_features, get_prediction_features

        write_prediction_features(2025, "nn_v10+xgb_v4+lr_v2", {"W01_KC_SF": {}},
                                   feature_version="deadbeef")

        doc = get_prediction_features(2025, "nn_v10+xgb_v4+lr_v2")
        assert doc["feature_version"] == "deadbeef"


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

    def test_get_all_firestore_is_cached_across_calls(self, mock_firestore, monkeypatch):
        """Task 7: repeated calls must not re-stream the whole collection."""
        import services.cache_service as cs
        monkeypatch.setattr(cs, "_USE_LOCAL", False)
        cs.clear_data_cache()

        from services.cache_service import get_all_elo_history

        doc = MagicMock()
        doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        mock_firestore.collection.return_value.stream.return_value = [doc]

        get_all_elo_history()
        get_all_elo_history()

        assert mock_firestore.collection.return_value.stream.call_count == 1
        cs.clear_data_cache()


class TestQuarterScoresCache:
    """Tests for get_quarter_scores_season / write_quarter_scores_season."""

    def test_write_then_read_local_season(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_quarter_scores_season, get_quarter_scores_season

        rows = [{"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF",
                  "home_q1": 7, "home_q2": 3, "home_q3": 0, "home_q4": 10,
                  "away_q1": 0, "away_q2": 7, "away_q3": 14, "away_q4": 0}]
        write_quarter_scores_season(2025, rows, use_local=True)

        result = get_quarter_scores_season(2025)
        assert result == rows

    def test_get_nonexistent_season_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import get_quarter_scores_season
        assert get_quarter_scores_season(2099) == []

    def test_write_firestore_writes_document(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)

        from services.cache_service import write_quarter_scores_season

        rows = [{"season": 2025, "week": 1, "home_team": "KC"}]
        write_quarter_scores_season(2025, rows, use_local=False)

        mock_firestore.collection.assert_called_with("quarter_scores")
        mock_firestore.collection.return_value.document.assert_called_with("2025")
        mock_firestore.collection.return_value.document.return_value.set.assert_called_with({
            "season": 2025, "rows": rows,
        })

    def test_get_firestore_returns_rows(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", False)

        from services.cache_service import get_quarter_scores_season

        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        mock_firestore.collection.return_value.document.return_value.get.return_value = doc

        result = get_quarter_scores_season(2025)
        assert result == [{"season": 2025, "week": 1}]


class TestNnWeeklyAccuracyCache:
    """Tests for get_nn_weekly_accuracy_season / get_all_nn_weekly_accuracy /
    write_nn_weekly_accuracy_rows."""

    def test_write_then_read_local_season(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_nn_weekly_accuracy_rows, get_nn_weekly_accuracy_season

        rows = [{"season": 2025, "week": 1, "accuracy_pct": 62.5}]
        write_nn_weekly_accuracy_rows(2025, rows, use_local=True)

        result = get_nn_weekly_accuracy_season(2025)
        assert result == rows

    def test_get_nonexistent_season_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import get_nn_weekly_accuracy_season
        assert get_nn_weekly_accuracy_season(2099) is None

    def test_write_upserts_by_week_without_erasing_other_weeks(self, tmp_path, monkeypatch):
        """A run evaluating only week 2 must not wipe out week 1's stored row --
        weekly_model_eval.py is only ever given the weeks on its command line,
        unlike compute_elo.py which recomputes a whole season every time."""
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_nn_weekly_accuracy_rows, get_nn_weekly_accuracy_season

        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 1, "accuracy_pct": 60.0}], use_local=True)
        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 2, "accuracy_pct": 70.0}], use_local=True)

        result = get_nn_weekly_accuracy_season(2025)
        assert [r["week"] for r in result] == [1, 2]

    def test_write_replaces_same_week_on_rerun(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_nn_weekly_accuracy_rows, get_nn_weekly_accuracy_season

        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 1, "accuracy_pct": 60.0}], use_local=True)
        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 1, "accuracy_pct": 80.0}], use_local=True)

        result = get_nn_weekly_accuracy_season(2025)
        assert len(result) == 1
        assert result[0]["accuracy_pct"] == 80.0

    def test_get_all_combines_and_sorts_seasons(self, tmp_path, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
        monkeypatch.setattr("services.cache_service._GAME_PRED_DIR", tmp_path)

        from services.cache_service import write_nn_weekly_accuracy_rows, get_all_nn_weekly_accuracy

        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 1}], use_local=True)
        write_nn_weekly_accuracy_rows(2006, [{"season": 2006, "week": 1}], use_local=True)

        all_rows = get_all_nn_weekly_accuracy()
        assert [r["season"] for r in all_rows] == [2006, 2025]

    def test_write_firestore_writes_merged_document(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", True)

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1, "accuracy_pct": 60.0}]}
        mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

        from services.cache_service import write_nn_weekly_accuracy_rows

        write_nn_weekly_accuracy_rows(2025, [{"season": 2025, "week": 2, "accuracy_pct": 70.0}], use_local=False)

        mock_firestore.collection.assert_called_with("nn_weekly_accuracy")
        mock_firestore.collection.return_value.document.assert_called_with("2025")
        mock_firestore.collection.return_value.document.return_value.set.assert_called_with({
            "season": 2025,
            "rows": [
                {"season": 2025, "week": 1, "accuracy_pct": 60.0},
                {"season": 2025, "week": 2, "accuracy_pct": 70.0},
            ],
        })

    def test_get_all_firestore_streams_every_doc(self, mock_firestore, monkeypatch):
        monkeypatch.setattr("services.cache_service._USE_LOCAL", False)

        from services.cache_service import get_all_nn_weekly_accuracy

        doc_2006 = MagicMock()
        doc_2006.to_dict.return_value = {"season": 2006, "rows": [{"season": 2006, "week": 1}]}
        doc_2025 = MagicMock()
        doc_2025.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        mock_firestore.collection.return_value.stream.return_value = [doc_2025, doc_2006]

        all_rows = get_all_nn_weekly_accuracy()
        assert [r["season"] for r in all_rows] == [2006, 2025]

    def test_get_all_firestore_is_cached_across_calls(self, mock_firestore, monkeypatch):
        """Task 7: repeated calls must not re-stream the whole collection."""
        import services.cache_service as cs
        monkeypatch.setattr(cs, "_USE_LOCAL", False)
        cs.clear_data_cache()

        from services.cache_service import get_all_nn_weekly_accuracy

        doc = MagicMock()
        doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        mock_firestore.collection.return_value.stream.return_value = [doc]

        get_all_nn_weekly_accuracy()
        get_all_nn_weekly_accuracy()

        assert mock_firestore.collection.return_value.stream.call_count == 1
        cs.clear_data_cache()

    def test_get_all_elo_and_nn_weekly_share_one_signal_domain(self, mock_firestore, monkeypatch):
        """Both collections are cached under the same DOMAIN_ADMIN_ANALYTICS
        bucket -- an elo write clearing the whole domain also forces a
        re-stream of nn_weekly_accuracy, an accepted low-cost trade for
        admin-only traffic (see design doc SS4)."""
        import services.cache_service as cs
        monkeypatch.setattr(cs, "_USE_LOCAL", False)
        cs.clear_data_cache()

        from services.cache_service import get_all_elo_history, get_all_nn_weekly_accuracy

        elo_doc = MagicMock()
        elo_doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}
        acc_doc = MagicMock()
        acc_doc.to_dict.return_value = {"season": 2025, "rows": [{"season": 2025, "week": 1}]}

        def fake_collection(name):
            col = MagicMock()
            col.stream.return_value = [elo_doc] if name == "elo_history" else [acc_doc]
            return col
        mock_firestore.collection.side_effect = fake_collection

        get_all_elo_history()
        bucket = cs.get_domain(cs.DOMAIN_ADMIN_ANALYTICS)
        assert "elo_history" in bucket and "nn_weekly_accuracy" not in bucket

        get_all_nn_weekly_accuracy()
        bucket = cs.get_domain(cs.DOMAIN_ADMIN_ANALYTICS)
        assert "elo_history" in bucket and "nn_weekly_accuracy" in bucket

        cs.clear_domain(cs.DOMAIN_ADMIN_ANALYTICS)  # simulate a single admin_analytics_updated signal
        get_all_elo_history()
        # both were wiped by the one signal -- elo re-streamed, nn_weekly not yet re-fetched
        bucket = cs.get_domain(cs.DOMAIN_ADMIN_ANALYTICS)
        assert "elo_history" in bucket and "nn_weekly_accuracy" not in bucket
        cs.clear_data_cache()


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


def test_get_game_predictions_is_cached_per_season(monkeypatch):
    """Task 6: get_game_predictions() must be cached per-season within the
    active/historical predictions domain, not re-fetched (disk or Firestore)
    on every call."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()  # resolves the active season
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.cache_service._fetch_game_predictions") as mock_fetch:
        mock_fetch.return_value = {"W01_KC_BUF": {"pred_winner": "KC"}}
        first = cs.get_game_predictions(active_season)
        second = cs.get_game_predictions(active_season)
        mock_fetch.assert_called_once()
    assert first == second == {"W01_KC_BUF": {"pred_winner": "KC"}}
    cs.clear_data_cache()


def test_get_game_predictions_shares_bucket_with_preseason_predictions(monkeypatch):
    """The per-season entry get_game_predictions() writes into must not
    clobber (or be clobbered by) the preseason_df/consensus_df entry
    data_service._get_predictions_bucket() writes for the same season."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.cache_service._fetch_game_predictions", return_value={"W01_KC_BUF": {}}), \
         patch("services.data_service.get_collection_df", return_value=pd.DataFrame()):
        cs.get_game_predictions(active_season)
        preseason_result = data_service.get_preseason_predictions(active_season)
        # Re-fetching game predictions afterwards must still hit the cache,
        # proving _get_predictions_bucket() didn't wipe the earlier entry.
        with patch("services.cache_service._fetch_game_predictions") as mock_fetch_2:
            cs.get_game_predictions(active_season)
            mock_fetch_2.assert_not_called()
    assert preseason_result == {}
    cs.clear_data_cache()


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
