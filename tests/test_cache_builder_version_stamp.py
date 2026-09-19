"""tests/test_cache_builder_version_stamp.py"""
from unittest.mock import MagicMock, patch

import pandas as pd


class TestBuildYearPmapStamping:
    def test_pmap_entry_carries_ensemble_and_feature_version(self):
        """Exercises the REAL cb.build_year() pmap-building branch (final
        review finding 3) -- not a hand-copied guard that can't fail if
        build_year() changes. Mocking pattern mirrors
        tests/test_cache_builder.py::TestBuildYearWritesExplanation, which
        already proves build_year() is directly callable with fully mocked
        collaborators."""
        import scripts.cache_builder as cb

        schedule_df = pd.DataFrame([
            {"week": 3, "home_team": "WAS", "away_team": "KC",
             "pred_winner": "WAS", "pred_su_conf": 70.0, "pred_ats_pick": "WAS",
             "pred_prob": 0.7, "model_spread": 4.5, "edge_vs_vegas": 1.0,
             "explanation": {"elo_diff": 12.3}},
        ])

        # get_team_win_projections() -> {} (falsy) so the separate
        # preseason_predictions branch (also gated on model_version) is a
        # no-op and never calls the real set_preseason_predictions/Firestore
        # write -- this test is only about the game_predictions pmap.
        mock_engine_instance = MagicMock()
        mock_engine_instance.get_team_win_projections.return_value = {}
        mock_engine_cls = MagicMock(return_value=mock_engine_instance)

        captured = {}
        with patch.object(cb, "get_game_predictions", return_value={}), \
             patch.object(cb, "merge_thin_game_predictions",
                          side_effect=lambda existing, fresh: fresh), \
             patch.object(cb, "write_game_predictions",
                          side_effect=lambda year, merged: captured.update(merged)), \
             patch.object(cb.analysis, "get_enriched_schedule", return_value=schedule_df), \
             patch.object(cb, "_apply_predictions", return_value=schedule_df), \
             patch.object(cb, "NNProjectionEngine", mock_engine_cls), \
             patch.object(cb, "live_scores"):
            cb.build_year(
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), year=2026, current_year=2026,
                force=True,
                model_version="nn_v15+xgb_v9+lr_v7", feature_version="abc1234",
            )

        entry = captured["W03_WAS_KC"]
        assert entry["ensemble_version"] == "nn_v15+xgb_v9+lr_v7"
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
