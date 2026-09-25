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
# immediately after import.
#
# Firestore/cache-touching note: most tests here mock `main()` or `build_year()`
# entirely, so nothing real is exercised. The exception is
# TestPreseasonPredictionsWiring's tests that call `build_year()` directly
# (not `main()`) -- those run the real schedule_enriched/preseason_predictions
# blocks inside `build_year()`; every Firestore call within them
# (`set_preseason_predictions`, `NNProjectionEngine`) is mocked.
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
        mock_run.return_value = MagicMock(returncode=0, stdout="  Sync complete\n  Downloaded: 3 files", stderr="")
        _sync_rawdata()
        assert "[warn]" not in capsys.readouterr().out

    @patch("scripts.cache_builder.subprocess.run")
    def test_failure_is_non_fatal(self, mock_run, capsys):
        """Must not raise -- a sync failure shouldn't abort the whole job;
        any resulting missing-file error surfaces naturally downstream."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="404 not found")
        _sync_rawdata()  # should not raise
        assert "[warn]" in capsys.readouterr().out

    @patch("scripts.cache_builder.subprocess.run")
    def test_success_still_prints_stdout_summary(self, mock_run, capsys):
        """A successful sync (exit 0) previously left NO trace in this job's
        own logs at all -- capture_output swallows the subprocess's stdout,
        and the old code only ever printed anything on a non-zero exit. That
        made it impossible to tell, from Cloud Logging alone, whether a given
        release/year (e.g. weekly_rosters for the current season) was
        actually downloaded, skipped as already up to date, or never
        attempted -- must always surface at least the summary tail."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="[weekly_rosters]\n  >> roster_weekly_2026.csv\n\n  Sync complete (12.3s)\n  Downloaded: 5 files\n  Up-to-date: 2 releases skipped\n  Failed:     0 files",
            stderr="",
        )
        _sync_rawdata()
        out = capsys.readouterr().out
        assert "Downloaded: 5 files" in out
        assert "Failed:     0 files" in out


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
    def test_writes_unlocked_for_current_season(
        self, mock_engine_cls, mock_set, mock_sync_live,
    ):
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

    # NOTE: this test used to assert that a past season gets written WITH
    # locked=True. That premise was wrong -- see the critical fix in
    # build_year(): a completed past season must not be written AT ALL
    # (set_preseason_predictions must never even be called for it), because
    # the existing historical preseason_predictions docs (written only by
    # predict_season.py, which never set a `locked` field) would otherwise
    # read back as unlocked and get silently regenerated/overwritten by
    # every unscoped daily run. See test_skips_write_for_past_season_even_
    # with_no_locked_field_in_existing_docs below for the real-world scenario
    # this protects against.
    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_writes_locked_for_past_season(
        self, mock_engine_cls, mock_set,
    ):
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

        # A completed past season (year < current_year, force=False) must
        # never be written -- the write block is gated off entirely.
        mock_set.assert_not_called()

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_skips_write_for_past_season_even_with_no_locked_field_in_existing_docs(
        self, mock_engine_cls, mock_set,
    ):
        """The real-world bug scenario: every historical preseason_predictions
        doc was written by predict_season.py, which never sets a `locked`
        field at all -- so set_preseason_predictions()'s per-team lock check
        (`data.get("locked")`) would read every one of those docs as
        unlocked. Without the year >= current_year (or force) gate in
        build_year(), an unscoped daily run would silently regenerate and
        permanently lock every completed historical season's projections
        with whatever model happens to be current that day. The fix is to
        never even call set_preseason_predictions() for a past season, which
        this test verifies directly -- it doesn't matter what shape the
        existing docs are in, because the call never happens."""
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

        mock_set.assert_not_called()

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_force_allows_writing_past_season(
        self, mock_engine_cls, mock_set,
    ):
        """--force is the intentional manual-override escape hatch: it must
        still allow writing (and relocking) an already-completed season."""
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
            all_games=games, force=True, pred_lookup={},
            model_version="nn_v15+xgb_v9+lr_v7",
        )

        mock_set.assert_called_once()
        assert mock_set.call_args.kwargs["force"] is True

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_skips_write_when_model_version_none(
        self, mock_engine_cls, mock_set,
    ):
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

        # available_years=[2024, 2025] and games only has season=2025 data (no
        # 2026 schedule rows), so _years_to_build() does not extend the range
        # -- build_year() must be called exactly once per year in [2024, 2025].
        assert mock_build_year.call_count == 2
        for call in mock_build_year.call_args_list:
            assert call.kwargs.get("model_version") == "nn_v15+xgb_v9+lr_v7"


class TestScheduleEnrichedGate:
    """Task 10 deviation: schedule_enriched's is_past_season gate replaces
    the deleted analytics_cache is_cache_final() finality check. Without
    this gate, a completed past season's game_predictions would be
    recomputed and rewritten to Firestore on every single daily
    cache_builder.py run, forever."""

    @patch("scripts.cache_builder.analysis.get_enriched_schedule")
    def test_past_season_skipped_without_force(self, mock_get_enriched):
        from scripts.cache_builder import build_year
        import pandas as pd

        games = pd.DataFrame([
            {"season": 2024, "week": 18, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2024, current_year=2026,
            all_games=games, force=False, pred_lookup={}, model_version=None,
        )

        mock_get_enriched.assert_not_called()

    @patch("scripts.cache_builder.analysis.get_enriched_schedule")
    def test_past_season_recomputed_with_force(self, mock_get_enriched):
        from scripts.cache_builder import build_year
        import pandas as pd

        mock_get_enriched.return_value = pd.DataFrame()
        games = pd.DataFrame([
            {"season": 2024, "week": 18, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2024, current_year=2026,
            all_games=games, force=True, pred_lookup={}, model_version=None,
        )

        mock_get_enriched.assert_called_once()

    @patch("scripts.cache_builder.analysis.get_enriched_schedule")
    def test_current_season_always_recomputed(self, mock_get_enriched):
        """A current/future season is never skipped by this gate, force or not."""
        from scripts.cache_builder import build_year
        import pandas as pd

        mock_get_enriched.return_value = pd.DataFrame()
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2026, current_year=2026,
            all_games=games, force=False, pred_lookup={}, model_version=None,
        )

        mock_get_enriched.assert_called_once()


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
        # Regression: model_spread/edge_vs_vegas used to be computed here and
        # then silently dropped, so a daily rebuild could refresh pred_winner
        # (from today's simulation) while leaving model_spread stale from
        # whatever a prior --resimulate/backfill run last wrote -- producing
        # a pred_winner that disagreed with the displayed model spread.
        assert out.iloc[0]["model_spread"] == -3.0
        assert out.iloc[0]["edge_vs_vegas"] == pytest.approx(-0.5)

    def test_completed_game_never_touches_fallback_engine(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": 3.0},
        ])
        pred_lookup = {(2026, 3, "WAS", "KC"): {
            "pred_winner": "WAS", "pred_su_conf": 70.0,
            "pred_ats_pick": "WAS", "pred_prob": 0.7,
            "model_spread": 4.5, "edge_vs_vegas": 1.0,
        }}
        fallback_engine = MagicMock()
        out = _apply_predictions(schedule, 2026, pred_lookup, fallback_engine=fallback_engine)
        fallback_engine.simulate_season.assert_not_called()
        assert out.iloc[0]["pred_winner"] == "WAS"
        assert out.iloc[0]["model_spread"] == 4.5
        assert out.iloc[0]["edge_vs_vegas"] == 1.0

    def test_pred_lookup_entry_missing_model_spread_does_not_raise(self):
        """Older feature-table lookups without model_spread/edge_vs_vegas
        should degrade to None rather than KeyError."""
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
        assert out.iloc[0]["model_spread"] is None
        assert out.iloc[0]["edge_vs_vegas"] is None

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


class TestPredictionsActiveSignal:
    """Task 6: main()'s daily full-build run must signal predictions_active_
    updated (merge=True, via signal_data_update()) instead of the old bare,
    non-merging metadata/cache_control write -- that bare write would wipe
    every other domain's field in the same document on every run."""

    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder.get_available_years", return_value=[2026])
    @patch("scripts.cache_builder._years_to_build", return_value=[2026])
    @patch("scripts.cache_builder.NNPredictionService", side_effect=RuntimeError("no model in test env"))
    @patch("scripts.cache_builder.build_year")
    @patch("services.db_service.signal_data_update")
    @patch("scripts.cache_builder._run_weekly_backfill_if_tuesday")
    def test_full_run_signals_predictions_active(
        self, mock_backfill, mock_signal, mock_build_year, mock_nn_svc,
        mock_years_to_build, mock_available_years, mock_load_data, monkeypatch,
    ):
        import sys
        from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        mock_load_data.return_value = (
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame([{"season": 2026}]),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        main()

        mock_signal.assert_called_once_with(DOMAIN_PREDICTIONS_ACTIVE)
        mock_backfill.assert_called_once()

    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder.get_available_years", return_value=[2026])
    @patch("scripts.cache_builder._years_to_build", return_value=[2026])
    @patch("scripts.cache_builder.NNPredictionService", side_effect=RuntimeError("no model in test env"))
    @patch("scripts.cache_builder.build_year")
    @patch("services.db_service.signal_data_update")
    @patch("scripts.cache_builder._run_weekly_backfill_if_tuesday")
    def test_full_run_never_spawns_backfill_subprocess_on_a_tuesday(
        self, mock_backfill, mock_signal, mock_build_year, mock_nn_svc,
        mock_years_to_build, mock_available_years, mock_load_data, monkeypatch,
    ):
        """Isolation proof: with the clock pinned to a Tuesday, a real
        subprocess.run would be reached if the weekly-backfill patch were
        removed. Deterministic -- never depends on the wall-clock weekday."""
        import sys
        from datetime import datetime, timezone
        import scripts.cache_builder as cb

        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)  # a real Tuesday
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        mock_load_data.return_value = (
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame([{"season": 2026}]),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run",
                          side_effect=AssertionError("real subprocess must not run")):
            mock_dt.now.return_value = tuesday
            main()  # must complete without reaching subprocess.run

        mock_backfill.assert_called_once()


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
             patch("services.db_service.signal_data_update"):
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
             patch("services.db_service.signal_data_update"):
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
             patch("services.db_service.signal_data_update"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()  # must not raise

        mock_publish.assert_called_once()


class TestWeeklyBackfillStep:
    def test_main_invokes_weekly_backfill_step(self):
        """main() calls the (mocked) weekly-backfill step. The Tuesday gate
        itself is covered by test_subprocess_only_runs_on_tuesday and
        test_subprocess_invoked_with_firestore_flag_on_tuesday."""
        import scripts.cache_builder as cb
        with patch.object(cb, "_run_weekly_backfill_if_tuesday") as mock_step, \
             patch.object(cb, "load_data", return_value=(pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame())), \
             patch.object(cb, "get_available_years", return_value=[]), \
             patch.object(cb, "_years_to_build", return_value=[]), \
             patch.object(cb, "NNPredictionService", side_effect=Exception("skip ML load")), \
             patch("sys.argv", ["cache_builder.py", "--skip-sync"]):
            cb.main()
        mock_step.assert_called_once()

    def test_subprocess_only_runs_on_tuesday(self):
        """_run_weekly_backfill_if_tuesday itself gates on weekday()."""
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        monday = datetime(2026, 9, 21, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run") as mock_run:
            mock_dt.now.return_value = monday
            cb._run_weekly_backfill_if_tuesday()
        mock_run.assert_not_called()

    def test_subprocess_invoked_with_firestore_flag_on_tuesday(self):
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run") as mock_run:
            mock_dt.now.return_value = tuesday
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cb._run_weekly_backfill_if_tuesday()
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        assert "backfill_schedule_predictions.py" in called_args[1]
        assert "--firestore" in called_args

    def test_scoped_to_the_current_season_only(self):
        """backfill_schedule_predictions.py's own default is every season back
        to 2006 -- all of them already locked. Re-grading them weekly is pure
        waste and makes the 600s timeout far likelier to bite."""
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run") as mock_run:
            mock_dt.now.return_value = tuesday
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cb._run_weekly_backfill_if_tuesday(2026)
        called_args = mock_run.call_args[0][0]
        assert called_args[-3:] == ["--seasons", "2026", "2026"]

    def test_timeout_is_non_fatal(self):
        """Regression: a TimeoutExpired used to propagate out of main() into
        _run_with_alerting(), failing the WHOLE daily job (and triggering
        Cloud Run retries of the entire build) over an optional weekly step."""
        import subprocess as sp
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run",
                          side_effect=sp.TimeoutExpired(cmd="backfill", timeout=600)):
            mock_dt.now.return_value = tuesday
            cb._run_weekly_backfill_if_tuesday(2026)  # must not raise

    def test_unexpected_subprocess_error_is_non_fatal(self):
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run", side_effect=OSError("no interpreter")):
            mock_dt.now.return_value = tuesday
            cb._run_weekly_backfill_if_tuesday(2026)  # must not raise

    def test_runs_after_the_cache_invalidation_signal(self):
        """A slow or failing weekly backfill must never delay (or, on a raise,
        skip) the predictions_active invalidation for the daily predictions
        this job just wrote."""
        import scripts.cache_builder as cb
        order = []
        with patch.object(cb, "_run_weekly_backfill_if_tuesday",
                          side_effect=lambda *a, **k: order.append("backfill")), \
             patch("services.db_service.signal_data_update",
                   side_effect=lambda *a, **k: order.append("signal")), \
             patch.object(cb, "load_data", return_value=(pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame())), \
             patch.object(cb, "get_available_years", return_value=[]), \
             patch.object(cb, "_years_to_build", return_value=[]), \
             patch.object(cb, "NNPredictionService", side_effect=Exception("skip ML load")), \
             patch("sys.argv", ["cache_builder.py", "--skip-sync"]):
            cb.main()
        assert order == ["signal", "backfill"]


class TestApplyPredictionsCarriesExplanation:
    def test_feature_table_branch_carries_explanation_through(self):
        """pred_lookup entries already include a full explanation dict
        (built by build_ensemble_lookup) -- it must survive into the output,
        not be dropped."""
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": 3.0},
        ])
        pred_lookup = {(2026, 3, "WAS", "KC"): {
            "pred_winner": "WAS", "pred_su_conf": 70.0,
            "pred_ats_pick": "WAS", "pred_prob": 0.7,
            "model_spread": 4.5, "edge_vs_vegas": 1.0,
            "explanation": {"elo_diff": 12.3, "vegas_line": 3.0},
        }}
        out = _apply_predictions(schedule, 2026, pred_lookup, fallback_engine=MagicMock())
        assert out.iloc[0]["explanation"] == {"elo_diff": 12.3, "vegas_line": 3.0}

    def _fallback_engine(self):
        engine = MagicMock()
        engine._team_profiles = pd.DataFrame([
            {"team": "WAS", "elo_pre": 1550.0, "roster_talent_delta": 0.5,
             "off_pass_epa_roll": 0.1, "off_rush_epa_roll": 0.05,
             "def_pass_epa_roll": 0.02, "def_rush_epa_roll": 0.01,
             "margin_roll": 3.0, "trench_score": 2.0},
            {"team": "KC", "elo_pre": 1500.0, "roster_talent_delta": 0.1,
             "off_pass_epa_roll": 0.0, "off_rush_epa_roll": 0.0,
             "def_pass_epa_roll": 0.0, "def_rush_epa_roll": 0.0,
             "margin_roll": 0.0, "trench_score": 1.0},
        ])
        engine._qb_availability = {(2026, 3, "WAS"): 1.0}
        engine.lookup_roster_value.side_effect = lambda team, week: {
            "WAS": {"off_roster_value": 2.0, "def_roster_value": 1.0},
            "KC":  {"off_roster_value": 1.0, "def_roster_value": 0.5},
        }.get(team, {})
        engine.simulate_season.return_value = {
            "game_probs": {
                "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                               "home_team": "WAS", "away_team": "KC", "week": 3},
            },
        }
        return engine

    def test_fallback_branch_builds_an_explanation_not_none(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        out = _apply_predictions(schedule, 2026, {},
                                 fallback_engine=self._fallback_engine())
        assert out.iloc[0]["explanation"] is not None
        assert out.iloc[0]["explanation"]["model_spread"] == -3.0

    def test_fallback_explanation_is_the_rich_shared_one_not_a_thin_4_key_dict(self):
        """Regression (final-review finding 1): the daily run built its own
        thin 4-key explanation, and merge_thin_game_predictions replaces the
        whole explanation dict per key -- so every non-Tuesday run wiped the
        15 richer keys backfill_schedule_predictions.py had written for the
        same future game, including the QB-availability flags this branch's
        whole feature set exists for. Both now build it from the one shared
        build_mc_prediction_entry()."""
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        out = _apply_predictions(schedule, 2026, {},
                                 fallback_engine=self._fallback_engine())
        explanation = out.iloc[0]["explanation"]

        # Exactly the key set scripts/backfill_schedule_predictions.py writes
        assert set(explanation) == {
            "vegas_line", "vegas_home_prob", "model_spread", "edge_vs_vegas",
            "elo_diff", "roster_delta", "pass_epa_matchup", "rush_epa_matchup",
            "early_down_matchup", "turnover_margin", "point_diff_advantage",
            "home_qb_out", "away_qb_out", "rest_advantage", "travel_disadvantage",
            "trench_dominance", "off_roster_value", "def_roster_value", "source",
        }
        assert explanation["home_qb_out"] == 1.0
        assert explanation["away_qb_out"] == 0.0
        assert explanation["elo_diff"] == pytest.approx(50.0)
        assert explanation["off_roster_value"] == pytest.approx(1.0)
        assert explanation["def_roster_value"] == pytest.approx(0.5)

    def test_unbuildable_explanation_is_none_so_the_stored_one_survives(self):
        """When the explanation can't be built at all, it must come back None
        -- build_year() then omits the key entirely and the shallow merge
        leaves the stored richer dict intact. Writing a degraded stand-in
        instead would destroy it."""
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        engine = self._fallback_engine()
        engine.lookup_roster_value.side_effect = RuntimeError("roster cache gone")

        out = _apply_predictions(schedule, 2026, {}, fallback_engine=engine)
        assert out.iloc[0]["pred_winner"] == "WAS"       # scalars still written
        assert out.iloc[0]["explanation"] is None


class TestBuildYearWritesExplanation:
    def test_explanation_included_in_daily_pmap_when_present(self):
        """Regression: the daily thin-write only ever sent pred_winner/
        pred_su_conf/pred_ats_pick/pred_prob/model_spread/edge_vs_vegas --
        explanation silently never refreshed after the first backfill run."""
        import scripts.cache_builder as cb
        schedule_df_with_explanation = pd.DataFrame([
            {"week": 3, "home_team": "WAS", "away_team": "KC",
             "pred_winner": "WAS", "pred_su_conf": 70.0, "pred_ats_pick": "WAS",
             "pred_prob": 0.7, "model_spread": 4.5, "edge_vs_vegas": 1.0,
             "explanation": {"elo_diff": 12.3}},
        ])
        captured = {}
        with patch.object(cb, "get_game_predictions", return_value={}), \
             patch.object(cb, "merge_thin_game_predictions", side_effect=lambda existing, fresh: fresh), \
             patch.object(cb, "write_game_predictions", side_effect=lambda year, merged: captured.update(merged)), \
             patch.object(cb.analysis, "get_enriched_schedule", return_value=schedule_df_with_explanation), \
             patch.object(cb, "_apply_predictions", return_value=schedule_df_with_explanation), \
             patch.object(cb, "NNProjectionEngine"), \
             patch.object(cb, "live_scores"):
            cb.build_year(
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), year=2026, current_year=2026,
                force=True,
            )
        assert captured["W03_WAS_KC"]["explanation"] == {"elo_diff": 12.3}

    def test_daily_write_without_an_explanation_keeps_the_stored_richer_one(self):
        """Regression (final-review finding 1): merge_thin_game_predictions
        merges per GAME KEY and shallowly, so any explanation in the daily
        pmap replaces the stored one wholesale. A run that has no explanation
        for a game must therefore omit the key entirely -- never write a
        thinner stand-in over the rich dict the weekly backfill stored."""
        import scripts.cache_builder as cb

        rich = {
            "vegas_line": -2.5, "model_spread": -3.0, "edge_vs_vegas": -0.5,
            "elo_diff": 50.0, "home_qb_out": 1.0, "away_qb_out": 0.0,
            "pass_epa_matchup": 0.12, "source": "mc_simulation (10000 trials)",
        }
        stored = {"W03_WAS_KC": {"pred_prob": 0.66, "explanation": rich, "locked": False}}

        # What _apply_predictions hands back when no explanation could be built
        schedule_df = pd.DataFrame([
            {"week": 3, "home_team": "WAS", "away_team": "KC",
             "pred_winner": "WAS", "pred_su_conf": 62.0, "pred_ats_pick": "WAS",
             "pred_prob": 0.62, "model_spread": -3.0, "edge_vs_vegas": -0.5,
             "explanation": None},
        ])
        captured = {}
        with patch.object(cb, "get_game_predictions", return_value=stored), \
             patch.object(cb, "write_game_predictions",
                          side_effect=lambda year, merged: captured.update(merged)), \
             patch.object(cb.analysis, "get_enriched_schedule", return_value=schedule_df), \
             patch.object(cb, "_apply_predictions", return_value=schedule_df), \
             patch.object(cb, "NNProjectionEngine"), \
             patch.object(cb, "live_scores"):
            cb.build_year(
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), year=2026, current_year=2026,
                force=True,
            )

        # real merge_thin_game_predictions, not a stub
        assert captured["W03_WAS_KC"]["explanation"] == rich
        assert captured["W03_WAS_KC"]["pred_prob"] == 0.62  # scalars still refreshed


class TestRunSubprocessStep:
    def _cb(self):
        import scripts.cache_builder as cb
        return cb

    def test_success_prints_a_labelled_tail_and_returns_true(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="\n".join(f"line{i}" for i in range(30)), stderr="")
            ok = cb._run_subprocess_step(["py", "/x/sync_nflverse_data.py"], "nflverse sync", 300)
        out = capsys.readouterr().out
        assert ok is True
        assert "[cache_builder] nflverse sync summary:" in out
        assert "  line29" in out and "line9\n" not in out  # only the last 20 lines
        assert "[warn]" not in out

    def test_passes_timeout_and_cwd_through(self):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cb._run_subprocess_step(["py", "s.py"], "x", 123)
        assert run.call_args.kwargs["timeout"] == 123
        assert run.call_args.kwargs["capture_output"] is True
        assert run.call_args.kwargs["cwd"] == str(cb.SCRIPTS_DIR.parent)

    def test_nonzero_exit_warns_with_script_name_and_returns_false(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="404 not found")
            ok = cb._run_subprocess_step(["py", "/x/sync_nflverse_data.py"], "nflverse sync", 300)
        out = capsys.readouterr().out
        assert ok is False
        assert "[warn] sync_nflverse_data.py exited non-zero (non-fatal): 404 not found" in out

    def test_none_stdout_and_stderr_are_tolerated(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=1, stdout=None, stderr=None)
            assert cb._run_subprocess_step(["py", "s.py"], "x", 5) is False

    def test_timeout_propagates_by_default(self):
        import subprocess as sp
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=300)):
            try:
                cb._run_subprocess_step(["py", "s.py"], "x", 300)
            except sp.TimeoutExpired:
                return
        raise AssertionError("TimeoutExpired must propagate when swallow_errors is False")

    def test_swallowed_timeout_warns_with_the_note_and_returns_false(self, capsys):
        import subprocess as sp
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=600)):
            ok = cb._run_subprocess_step(["py", "/x/backfill_schedule_predictions.py"], "weekly backfill", 600,
                                         swallow_errors=True, timeout_note=" -- daily build done")
        assert ok is False
        assert "[warn] backfill_schedule_predictions.py timed out after 600s (non-fatal) -- daily build done" in capsys.readouterr().out

    def test_swallowed_unexpected_error_warns_and_returns_false(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=OSError("no interpreter")):
            ok = cb._run_subprocess_step(["py", "/x/backfill_schedule_predictions.py"], "weekly backfill", 600,
                                         swallow_errors=True)
        assert ok is False
        assert "could not be run (non-fatal): no interpreter" in capsys.readouterr().out

    def test_unexpected_error_propagates_by_default(self):
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=OSError("boom")):
            try:
                cb._run_subprocess_step(["py", "s.py"], "x", 5)
            except OSError:
                return
        raise AssertionError("OSError must propagate when swallow_errors is False")


class TestSubprocessCallSitesUseTheSharedStep:
    def test_sync_rawdata_keeps_timeout_propagation(self):
        """A hung nflverse sync must still fail the job (Cloud Run retries it)."""
        import subprocess as sp
        import scripts.cache_builder as cb
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=300)):
            try:
                cb._sync_rawdata()
            except sp.TimeoutExpired:
                return
        raise AssertionError("_sync_rawdata must let TimeoutExpired propagate")

    def test_sync_rawdata_uses_the_300s_timeout_and_label(self, capsys):
        import scripts.cache_builder as cb
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="Sync complete", stderr="")
            cb._sync_rawdata()
        assert run.call_args.kwargs["timeout"] == 300
        assert "[cache_builder] nflverse sync summary:" in capsys.readouterr().out

    def test_weekly_backfill_labels_its_summary_and_uses_600s(self, capsys):
        from datetime import datetime, timezone
        import scripts.cache_builder as cb
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, patch.object(cb.subprocess, "run") as run:
            mock_dt.now.return_value = tuesday
            run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            cb._run_weekly_backfill_if_tuesday(2026)
        assert run.call_args.kwargs["timeout"] == 600
        assert "[cache_builder] weekly backfill summary:" in capsys.readouterr().out
