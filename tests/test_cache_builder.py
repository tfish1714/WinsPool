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


def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_03_KC_WAS", "season": 2026, "week": 3,
         "home_team": "WAS", "away_team": "KC", "game_type": "REG"},
        {"game_id": "2026_03_SF_LAC", "season": 2026, "week": 3,
         "home_team": "LAC", "away_team": "SF", "game_type": "REG"},
    ])


class TestPublishGames:
    def test_publishes_only_requested_game(self):
        from scripts.cache_builder import _publish_games

        pred_lookup = {
            (2026, 3, "WAS", "KC"): {
                "pred_winner": "KC", "pred_su_conf": 62.0,
                "pred_ats_pick": "KC", "pred_prob": 0.62,
            },
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, pred_lookup)

        assert n == 1
        mock_write.assert_called_once()
        year, merged = mock_write.call_args[0]
        assert year == 2026
        assert "W03_WAS_KC" in merged
        assert "W03_LAC_SF" not in merged  # the other game was never touched

    def test_preserves_existing_richer_fields_for_untouched_games(self):
        from scripts.cache_builder import _publish_games

        existing = {"W03_LAC_SF": {"model_spread": -3.5, "edge_vs_vegas": 1.2}}
        pred_lookup = {
            (2026, 3, "WAS", "KC"): {
                "pred_winner": "KC", "pred_su_conf": 62.0,
                "pred_ats_pick": "KC", "pred_prob": 0.62,
            },
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value=existing), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, pred_lookup)

        _year, merged = mock_write.call_args[0]
        assert merged["W03_LAC_SF"] == existing["W03_LAC_SF"]

    def test_no_matching_game_id_returns_zero(self):
        from scripts.cache_builder import _publish_games
        with patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["nonexistent"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()

    def test_no_prediction_available_for_requested_game_returns_zero(self):
        from scripts.cache_builder import _publish_games
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()


class TestGamesModeWiring:
    def test_games_flag_skips_full_multi_year_build(self, monkeypatch):
        """--games must not call build_year() (the full standings/analytics
        rebuild) at all -- only the scoped publish path."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--games", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year") as mock_build_year, \
             patch("scripts.cache_builder.NNPredictionService"), \
             patch("scripts.cache_builder.XGBPredictionService"), \
             patch("scripts.cache_builder.LRPredictionService"), \
             patch("scripts.cache_builder.build_master_feature_table", return_value=pd.DataFrame()), \
             patch("scripts.cache_builder._build_pred_lookup", return_value={}), \
             patch("scripts.cache_builder._publish_games", return_value=0) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            main()

        mock_build_year.assert_not_called()
        mock_publish.assert_called_once()

    def test_games_flag_scopes_feature_table_to_one_season(self, monkeypatch):
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--games", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNPredictionService"), \
             patch("scripts.cache_builder.XGBPredictionService"), \
             patch("scripts.cache_builder.LRPredictionService"), \
             patch("scripts.cache_builder.build_master_feature_table", return_value=pd.DataFrame()) as mock_bmft, \
             patch("scripts.cache_builder._build_pred_lookup", return_value={}), \
             patch("scripts.cache_builder._publish_games", return_value=1), \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            main()

        _args, kwargs = mock_bmft.call_args
        assert kwargs["min_season"] == 2026
        assert kwargs["max_season"] == 2026
