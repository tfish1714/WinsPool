import sys
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest

# scripts/cache_builder.py always talks to Firestore directly (never the local
# pkl cache — see its module docstring) and performs a module-level Firebase
# Admin SDK init that calls sys.exit(1) if no firebase_credentials.json is
# present. That file is gitignored and isn't guaranteed to exist in every dev/
# CI checkout (e.g. an isolated git worktree), so fake an already-initialized
# app just for the duration of the import to skip that guard. Restored
# immediately after import; nothing in these tests exercises real Firestore
# calls since main() is always mocked.
import firebase_admin as _firebase_admin
with patch.object(_firebase_admin, "_apps", {"__test__": object()}), \
     patch("firebase_admin.firestore.client"):
    from scripts.cache_builder import _run_with_alerting, _sync_rawdata, main


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main", side_effect=RuntimeError("model load failed"))
def test_main_failure_sends_alert_and_reraises(mock_main, mock_alert):
    with pytest.raises(RuntimeError):
        _run_with_alerting()

    mock_alert.assert_called_once()
    subject, message = mock_alert.call_args[0]
    assert "winspool-predict-daily" in subject
    assert "model load failed" in message


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main")
def test_main_success_does_not_alert(mock_main, mock_alert):
    _run_with_alerting()
    mock_alert.assert_not_called()


# winspool-predict-daily runs in its own, separate Cloud Run Job container
# from winspool-sync-daily -- no shared filesystem between them, so whatever
# rawdata/ winspool-sync-daily downloaded is gone by the time this job
# starts. _sync_rawdata() closes that gap.
class TestSyncRawdata:
    @patch("scripts.cache_builder.subprocess.run")
    def test_success_prints_no_warning(self, mock_run, capsys):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        _sync_rawdata()
        assert "[warn]" not in capsys.readouterr().out

    @patch("scripts.cache_builder.subprocess.run")
    def test_failure_is_non_fatal(self, mock_run, capsys):
        """Must not raise -- a sync failure shouldn't abort the whole job;
        any resulting missing-file error surfaces naturally downstream."""
        mock_run.return_value = MagicMock(returncode=1, stderr="404 not found")
        _sync_rawdata()  # should not raise
        assert "[warn]" in capsys.readouterr().out


class TestMainSyncWiring:
    @patch("scripts.cache_builder.load_data", side_effect=RuntimeError("stop after sync check"))
    @patch("scripts.cache_builder._sync_rawdata")
    def test_syncs_rawdata_by_default(self, mock_sync, mock_load, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cache_builder.py"])
        with pytest.raises(RuntimeError):
            main()
        mock_sync.assert_called_once()

    @patch("scripts.cache_builder.load_data", side_effect=RuntimeError("stop after sync check"))
    @patch("scripts.cache_builder._sync_rawdata")
    def test_skip_sync_flag_skips_it(self, mock_sync, mock_load, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        with pytest.raises(RuntimeError):
            main()
        mock_sync.assert_not_called()


class TestYearsToBuild:
    def test_adds_next_year_when_schedule_data_exists(self):
        from scripts.cache_builder import _years_to_build
        games = pd.DataFrame([{"season": 2025, "week": 1}, {"season": 2026, "week": 1}])
        result = _years_to_build([2024, 2025], games)
        assert result == [2024, 2025, 2026]

    def test_does_not_add_next_year_without_schedule_data(self):
        from scripts.cache_builder import _years_to_build
        games = pd.DataFrame([{"season": 2025, "week": 1}])
        result = _years_to_build([2024, 2025], games)
        assert result == [2024, 2025]

    def test_does_not_duplicate_if_next_year_already_drafted(self):
        from scripts.cache_builder import _years_to_build
        games = pd.DataFrame([{"season": 2025, "week": 1}, {"season": 2026, "week": 1}])
        result = _years_to_build([2024, 2025, 2026], games)
        assert result == [2024, 2025, 2026]

    def test_handles_empty_available_years(self):
        from scripts.cache_builder import _years_to_build
        games = pd.DataFrame([{"season": 2024, "week": 1}, {"season": 2025, "week": 1}])
        result = _years_to_build([], games)
        assert result == [2025]  # current_year fallback 2024, next_year 2025

    def test_handles_empty_games(self):
        from scripts.cache_builder import _years_to_build
        result = _years_to_build([2024, 2025], pd.DataFrame())
        assert result == [2024, 2025]


class TestYearsToBuildWiring:
    @patch("scripts.cache_builder.build_year")
    @patch("scripts.cache_builder.NNPredictionService", side_effect=RuntimeError("skip model load in test"))
    @patch("scripts.cache_builder.get_available_years", return_value=[2024, 2025])
    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder._sync_rawdata")
    def test_main_includes_next_year_with_schedule_data(
        self, mock_sync, mock_load, mock_avail, mock_nn, mock_build_year, monkeypatch,
    ):
        games = pd.DataFrame([
            {"season": 2025, "week": 1, "game_id": "a"},
            {"season": 2026, "week": 1, "game_id": "b"},
        ])
        mock_load.return_value = (
            pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        main()
        years_called = [call.args[6] for call in mock_build_year.call_args_list]
        assert years_called == [2024, 2025, 2026]

    @patch("scripts.cache_builder.build_year")
    @patch("scripts.cache_builder.NNPredictionService", side_effect=RuntimeError("skip model load in test"))
    @patch("scripts.cache_builder.get_available_years", return_value=[2024, 2025])
    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder._sync_rawdata")
    def test_explicit_year_arg_bypasses_next_year_logic(
        self, mock_sync, mock_load, mock_avail, mock_nn, mock_build_year, monkeypatch,
    ):
        games = pd.DataFrame([
            {"season": 2025, "week": 1, "game_id": "a"},
            {"season": 2026, "week": 1, "game_id": "b"},
        ])
        mock_load.return_value = (
            pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--year", "2024", "--skip-sync"])
        main()
        years_called = [call.args[6] for call in mock_build_year.call_args_list]
        assert years_called == [2024]


class TestPreseasonPredictionsWiring:
    @patch("scripts.cache_builder.live_scores.sync_live_scores_to_df")
    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_writes_unlocked_for_current_season(self, mock_engine_cls, mock_set, mock_sync_live):
        from scripts.cache_builder import build_year
        import pandas as pd

        # year == current_year triggers a real ESPN live-score sync inside
        # build_year() unless this is mocked -- keep this test hermetic (no
        # network I/O) by passing games through unchanged.
        mock_sync_live.side_effect = lambda g: g

        fake_engine = MagicMock()
        fake_engine.get_team_win_projections.return_value = {
            "KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                   "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
        }
        mock_engine_cls.return_value = fake_engine

        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2026, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version="nn_v15+xgb_v9+lr_v7",
        )

        mock_set.assert_called_once()
        call_kwargs = mock_set.call_args.kwargs
        assert call_kwargs["locked"] is False
        assert call_kwargs["model_version"] == "nn_v15+xgb_v9+lr_v7"
        assert call_kwargs["force"] is False

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_writes_locked_for_past_season(self, mock_engine_cls, mock_set):
        from scripts.cache_builder import build_year
        import pandas as pd

        fake_engine = MagicMock()
        fake_engine.get_team_win_projections.return_value = {
            "KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                   "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
        }
        mock_engine_cls.return_value = fake_engine

        games = pd.DataFrame([
            {"season": 2024, "week": 18, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2024, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version="nn_v15+xgb_v9+lr_v7",
        )

        mock_set.assert_called_once()
        assert mock_set.call_args.kwargs["locked"] is True

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_skips_write_when_model_version_none(self, mock_engine_cls, mock_set):
        """model_version=None signals model loading failed this run (mirrors
        pred_lookup={} for game_predictions) -- must not attempt the write."""
        from scripts.cache_builder import build_year
        import pandas as pd

        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2026, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version=None,
        )

        mock_set.assert_not_called()

    @patch("scripts.cache_builder.build_master_feature_table")
    @patch("scripts.cache_builder._build_pred_lookup")
    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder.get_available_years", return_value=[2024, 2025])
    @patch("scripts.cache_builder.NNPredictionService")
    @patch("scripts.cache_builder.XGBPredictionService")
    @patch("scripts.cache_builder.LRPredictionService")
    @patch("scripts.cache_builder.build_year")
    @patch("scripts.cache_builder._sync_rawdata")
    def test_main_threads_model_version_string(
        self, mock_sync, mock_build_year, mock_lr_cls, mock_xgb_cls, mock_nn_cls,
        mock_avail, mock_load, mock_pred_lookup, mock_feature_table, monkeypatch,
    ):
        from scripts.cache_builder import main
        import pandas as pd
        import sys

        mock_nn_cls.return_value = MagicMock(loaded_version="v15")
        mock_xgb_cls.return_value = MagicMock(loaded_version="v9")
        mock_lr_cls.return_value = MagicMock(loaded_version="v7")

        # Prevent main() from doing real feature-engineering I/O over on-disk
        # rawdata/ CSVs -- only the model_version string threading is under
        # test here, not the feature table build itself.
        mock_feature_table.return_value = pd.DataFrame()
        mock_pred_lookup.return_value = {}

        games = pd.DataFrame([{"season": 2025, "week": 1, "game_id": "a"}])
        mock_load.return_value = (
            pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        main()

        for call in mock_build_year.call_args_list:
            assert call.kwargs.get("model_version") == "nn_v15+xgb_v9+lr_v7"


class TestBuildCompletedResults:
    def test_extracts_completed_reg_games_only(self):
        from scripts.cache_builder import _build_completed_results
        games = pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "WAS",
             "away_team": "KC", "result": 3.0},
            {"season": 2026, "week": 4, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": None},
            {"season": 2026, "week": 3, "game_type": "POST", "home_team": "DAL",
             "away_team": "NYG", "result": -7.0},
        ])
        result = _build_completed_results(games, 2026)
        assert result == {"W03_WAS_KC": 3.0}

    def test_filters_to_requested_season(self):
        from scripts.cache_builder import _build_completed_results
        games = pd.DataFrame([
            {"season": 2025, "week": 3, "game_type": "REG", "home_team": "WAS",
             "away_team": "KC", "result": 3.0},
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": -7.0},
        ])
        result = _build_completed_results(games, 2026)
        assert result == {"W03_SF_LAC": -7.0}


class TestApplyPredictionsFallback:
    def test_unplayed_game_uses_simulate_season_not_batch_method(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        fallback_engine = MagicMock()
        fallback_engine.simulate_season.return_value = {
            "game_probs": {
                "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                               "home_team": "WAS", "away_team": "KC", "week": 3},
            },
            "team_stats": {},
        }
        out = _apply_predictions(schedule, 2026, {}, fallback_engine=fallback_engine)

        fallback_engine.simulate_season.assert_called_once()
        fallback_engine.game_win_probabilities_batch.assert_not_called()
        assert out.iloc[0]["pred_winner"] == "WAS"
        assert out.iloc[0]["pred_prob"] == 0.62

    def test_completed_game_never_touches_fallback_engine(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": 3.0},
        ])
        pred_lookup = {(2026, 3, "WAS", "KC"): {
            "pred_winner": "WAS", "pred_su_conf": 70.0,
            "pred_ats_pick": "WAS", "pred_prob": 0.7,
        }}
        fallback_engine = MagicMock()
        out = _apply_predictions(schedule, 2026, pred_lookup, fallback_engine=fallback_engine)
        fallback_engine.simulate_season.assert_not_called()
        assert out.iloc[0]["pred_winner"] == "WAS"

    def test_simulate_season_failure_leaves_predictions_none_not_raises(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None},
        ])
        fallback_engine = MagicMock()
        fallback_engine.simulate_season.side_effect = Exception("model unavailable")
        out = _apply_predictions(schedule, 2026, {}, fallback_engine=fallback_engine)
        assert out.iloc[0]["pred_winner"] is None


def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_03_KC_WAS", "season": 2026, "week": 3,
         "home_team": "WAS", "away_team": "KC", "game_type": "REG"},
        {"game_id": "2026_03_SF_LAC", "season": 2026, "week": 3,
         "home_team": "LAC", "away_team": "SF", "game_type": "REG"},
    ])


def _games_df_with_spread():
    return pd.DataFrame([
        {"game_id": "2026_03_KC_WAS", "season": 2026, "week": 3,
         "home_team": "WAS", "away_team": "KC", "game_type": "REG",
         "spread_line": -1.0},
    ])


class TestPublishGameProbs:
    def test_publishes_only_requested_game(self):
        from scripts.cache_builder import _publish_game_probs

        game_probs = {
            "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                           "home_team": "WAS", "away_team": "KC", "week": 3},
            "W03_LAC_SF": {"mean_prob": 0.55, "model_spread": -1.0,
                           "home_team": "LAC", "away_team": "SF", "week": 3},
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, game_probs)

        assert n == 1
        mock_write.assert_called_once()
        year, merged = mock_write.call_args[0]
        assert year == 2026
        assert "W03_WAS_KC" in merged
        assert "W03_LAC_SF" not in merged  # the other game was never touched

    def test_preserves_existing_richer_fields_for_untouched_games(self):
        from scripts.cache_builder import _publish_game_probs

        existing = {"W03_LAC_SF": {"model_spread": -3.5, "edge_vs_vegas": 1.2}}
        game_probs = {
            "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                           "home_team": "WAS", "away_team": "KC", "week": 3},
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value=existing), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, game_probs)

        _year, merged = mock_write.call_args[0]
        assert merged["W03_LAC_SF"] == existing["W03_LAC_SF"]

    def test_no_matching_game_id_returns_zero(self):
        from scripts.cache_builder import _publish_game_probs
        with patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["nonexistent"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()

    def test_no_game_probs_entry_for_requested_game_returns_zero(self):
        from scripts.cache_builder import _publish_game_probs
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()

    def test_published_dict_includes_edge_vs_vegas_from_model_spread(self):
        """model_spread and edge_vs_vegas must both be freshly derived
        together (via _derive_prediction_fields) so a resimulated game never
        ends up with a new model_spread sitting next to a stale
        edge_vs_vegas computed from an old spread."""
        from scripts.cache_builder import _publish_game_probs

        game_probs = {
            "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                           "home_team": "WAS", "away_team": "KC", "week": 3},
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            _publish_game_probs(["2026_03_KC_WAS"], _games_df_with_spread(), 2026, game_probs)

        _year, merged = mock_write.call_args[0]
        entry = merged["W03_WAS_KC"]
        assert entry["model_spread"] == -3.0
        assert entry["edge_vs_vegas"] == pytest.approx(-2.0)  # -3.0 - (-1.0)


class TestResimulateModeWiring:
    def test_resimulate_flag_skips_full_multi_year_build(self, monkeypatch):
        """--resimulate must not call build_year() (the full standings/analytics
        rebuild) at all -- only the scoped ESPN-check + re-simulate + publish path."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year") as mock_build_year, \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("services.espn_injury_service.get_espn_injury_overrides", return_value={}), \
             patch("scripts.cache_builder._publish_game_probs", return_value=0) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()

        mock_build_year.assert_not_called()
        mock_engine_cls.return_value.initialize.assert_called_once()
        mock_publish.assert_called_once()

    def test_resimulate_flag_fetches_espn_overrides_and_passes_to_initialize(self, monkeypatch):
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("services.espn_injury_service.get_espn_injury_overrides",
                   return_value={(3, "QB1"): 0.0}) as mock_espn, \
             patch("scripts.cache_builder._publish_game_probs", return_value=1), \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()

        mock_espn.assert_called_once()
        init_kwargs = mock_engine_cls.return_value.initialize.call_args.kwargs
        assert init_kwargs["espn_overrides"] == {(3, "QB1"): 0.0}

    def test_resimulate_flag_espn_failure_still_publishes(self, monkeypatch):
        """ESPN fetch failing must not abort the re-simulate -- it just proceeds
        with no overrides, matching the established graceful-degradation pattern."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("services.espn_injury_service.get_espn_injury_overrides",
                   side_effect=Exception("ESPN down")), \
             patch("scripts.cache_builder._publish_game_probs", return_value=1) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()  # must not raise

        mock_publish.assert_called_once()
