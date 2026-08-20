import pandas as pd
from unittest.mock import patch, MagicMock
from scripts.sync_live_scores import overlay_espn_live_fields


def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_02_CIN_DET", "season": 2026, "week": 2, "game_type": "REG",
         "home_team": "DET", "away_team": "CIN", "home_score": 20.0, "away_score": 17.0,
         "result": 3.0},
        {"game_id": "2026_02_KC_BUF", "season": 2026, "week": 2, "game_type": "REG",
         "home_team": "BUF", "away_team": "KC", "home_score": None, "away_score": None,
         "result": None},
    ])


class TestOverlayEspnLiveFields:
    def test_no_live_games_writes_nothing(self):
        db = MagicMock()
        result = overlay_espn_live_fields(db, _games_df(), live_data={})
        db.collection.return_value.document.return_value.set.assert_not_called()
        assert result == 0

    def test_in_progress_game_writes_merge_update(self):
        db = MagicMock()
        live_data = {
            ("BUF", "KC"): {"home_score": 10, "away_score": 7, "status": "STATUS_IN_PROGRESS",
                             "clock": "5:23", "period": 2},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 1
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.set.assert_called_once()
        written_data, kwargs = doc_ref.set.call_args
        assert written_data[0] == {"is_live": True, "clock": "5:23", "period": 2}
        assert kwargs["merge"] is True

    def test_already_final_game_is_not_overwritten(self):
        """A game nflverse already marked final (has a non-null result) must not
        get its is_live/clock/period touched by ESPN data, even if ESPN still
        returns it (e.g. briefly after final whistle)."""
        db = MagicMock()
        live_data = {
            ("DET", "CIN"): {"home_score": 20, "away_score": 17, "status": "STATUS_FINAL",
                              "clock": "0:00", "period": 4},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 0
        db.collection.return_value.document.return_value.set.assert_not_called()

    def test_espn_failure_does_not_raise(self):
        """get_live_updates() raising must not propagate out of the overlay step."""
        db = MagicMock()
        with patch("scripts.sync_live_scores.get_live_updates", side_effect=Exception("ESPN down")):
            from scripts.sync_live_scores import run_espn_overlay_safely
            result = run_espn_overlay_safely(db, _games_df())
        assert result == 0
