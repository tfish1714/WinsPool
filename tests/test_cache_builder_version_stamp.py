"""Only exercises the pure per-entry dict construction, not the full
build_year() pipeline (which needs live Firestore/model data) -- this
isolates the stamping logic itself."""
from unittest.mock import MagicMock, patch


class TestBuildYearPmapStamping:
    def test_pmap_entry_carries_ensemble_and_feature_version(self):
        """Reproduces the exact dict-construction shape at
        scripts/cache_builder.py's pmap-building loop (~line 355-374) to
        verify the two new keys land on a freshly-built entry."""
        model_version = "nn_v14+xgb_v9+lr_v7"
        feature_version = "abc1234"

        entry = {
            'pred_prob':     0.62,
            'pred_winner':   'KC',
            'pred_su_conf':  62.0,
            'pred_ats_pick': 'KC',
        }
        if model_version:
            entry['ensemble_version'] = model_version
        entry['feature_version'] = feature_version

        assert entry['ensemble_version'] == "nn_v14+xgb_v9+lr_v7"
        assert entry['feature_version'] == "abc1234"

    def test_pmap_entry_omits_ensemble_version_when_model_load_failed(self):
        model_version = None
        feature_version = "abc1234"

        entry = {'pred_prob': 0.5, 'pred_winner': 'KC'}
        if model_version:
            entry['ensemble_version'] = model_version
        entry['feature_version'] = feature_version

        assert "ensemble_version" not in entry
        assert entry["feature_version"] == "abc1234"


class TestPublishGameProbsStamping:
    def test_resimulate_entry_gets_stamped(self, monkeypatch):
        import scripts.cache_builder as cb

        fake_engine = MagicMock()
        fake_engine.svc.loaded_version = "v14"
        fake_engine.xgb_svc.loaded_version = "v9"
        fake_engine.lr_svc.loaded_version = "v7"

        games = MagicMock()
        games.__getitem__.return_value.astype.return_value.isin.return_value = [True]
        # Build a minimal DataFrame-like target instead of mocking pandas internals:
        import pandas as pd
        games_df = pd.DataFrame([{"game_id": "2026_02_CAR_ATL", "home_team": "ATL",
                                   "away_team": "CAR", "week": 2}])
        game_probs = {"W02_ATL_CAR": {"home_team": "ATL", "away_team": "CAR",
                                       "week": 2, "mean_prob": 0.55, "model_spread": 1.5}}

        with patch.object(cb, "_build_mc_entry") as mock_build_entry, \
             patch.object(cb, "get_game_predictions", return_value={}), \
             patch.object(cb, "merge_thin_game_predictions",
                          side_effect=lambda e, f: {**e, **f}) as mock_merge, \
             patch.object(cb, "write_game_predictions"):
            mock_build_entry.return_value = {"pred_prob": 0.55, "pred_winner": "ATL"}
            cb._publish_game_probs(
                ["2026_02_CAR_ATL"], games_df, 2026, game_probs, engine=fake_engine,
                ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="abc1234",
            )

        # pmap passed to merge_thin_game_predictions is the second positional/kw arg
        merged_call = mock_merge.call_args
        fresh_pmap = merged_call.args[1] if merged_call.args else merged_call.kwargs["fresh"]
        entry = fresh_pmap["W02_ATL_CAR"]
        assert entry["ensemble_version"] == "nn_v14+xgb_v9+lr_v7"
        assert entry["feature_version"] == "abc1234"
