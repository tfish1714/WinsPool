import pandas as pd
import pytest
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
        assert written_data[0] == {"is_live": True, "clock": "5:23", "period": 2, "possession": None}
        assert kwargs["merge"] is True

    def test_halftime_game_is_treated_as_live(self):
        """ESPN reports halftime as STATUS_HALFTIME, not STATUS_IN_PROGRESS --
        a game at halftime is still live and must get the same is_live=True
        badge, with a 'Halftime' clock label instead of a stale '0:00'."""
        db = MagicMock()
        live_data = {
            ("BUF", "KC"): {"home_score": 10, "away_score": 7, "status": "STATUS_HALFTIME",
                             "clock": "0:00", "period": 2},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 1
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.set.assert_called_once()
        written_data, kwargs = doc_ref.set.call_args
        assert written_data[0] == {"is_live": True, "clock": "Halftime", "period": 2, "possession": None}
        assert kwargs["merge"] is True

    def test_possession_field_is_written_through(self):
        """The possession indicator (which team has the ball, 'home'/'away'/
        None) must be merge-written alongside is_live/clock/period so the
        schedule page can show a ball icon next to the right team."""
        db = MagicMock()
        live_data = {
            ("BUF", "KC"): {"home_score": 10, "away_score": 7, "status": "STATUS_IN_PROGRESS",
                             "clock": "5:23", "period": 2, "possession": "away"},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 1
        doc_ref = db.collection.return_value.document.return_value
        written_data, kwargs = doc_ref.set.call_args
        assert written_data[0]["possession"] == "away"

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


class TestAlertingPaths:
    """I5: coverage on the two alerting-path constraints -- the authoritative
    path must alert + exit 1 on failure, and the best-effort ESPN overlay
    path must never alert (it degrades silently, per the two-part design)."""

    def test_authoritative_failure_sends_alert_and_exits_1(self):
        """sync_authoritative() raising inside main() must call
        send_alert_email exactly once and exit the process with code 1."""
        with patch("scripts.sync_live_scores.initialize_firebase", return_value=MagicMock()), \
             patch("scripts.sync_live_scores.sync_authoritative", side_effect=RuntimeError("boom")), \
             patch("scripts.sync_live_scores.send_alert_email") as mock_alert:
            from scripts.sync_live_scores import main
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        mock_alert.assert_called_once()
        args, _ = mock_alert.call_args
        assert "winspool-live-scores" in args[0]

    def test_systemexit_from_sync_authoritative_still_alerts_and_exits_1(self):
        """Regression: load_games() (called from sync_authoritative()) calls
        sys.exit(1) directly when rawdata/schedules/games.csv is genuinely
        missing, which raises SystemExit -- not a subclass of Exception. A
        bare `except Exception` around sync_authoritative() would let that
        specific failure mode propagate straight past the alert handler,
        contradicting the spec's must-not-fail-silently requirement for the
        authoritative part. Exercise the real path: subprocess succeeds (so
        the non-fatal warning branch is skipped) but load_games() itself
        raises SystemExit."""
        with patch("scripts.sync_live_scores.initialize_firebase", return_value=MagicMock()), \
             patch("scripts.sync_live_scores.subprocess.run",
                   return_value=MagicMock(returncode=0, stderr="")), \
             patch("scripts.sync_live_scores.load_games", side_effect=SystemExit(1)), \
             patch("scripts.sync_live_scores.send_alert_email") as mock_alert:
            from scripts.sync_live_scores import main
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        mock_alert.assert_called_once()
        args, _ = mock_alert.call_args
        assert "winspool-live-scores" in args[0]

    def test_espn_overlay_failure_does_not_alert(self):
        """get_live_updates() raising inside the overlay path must NOT call
        send_alert_email -- only the authoritative path alerts."""
        db = MagicMock()
        with patch("scripts.sync_live_scores.get_live_updates", side_effect=Exception("ESPN down")), \
             patch("scripts.sync_live_scores.send_alert_email") as mock_alert:
            from scripts.sync_live_scores import run_espn_overlay_safely
            result = run_espn_overlay_safely(db, _games_df())

        assert result == 0
        mock_alert.assert_not_called()
