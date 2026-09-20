import pandas as pd
import pytest
from unittest.mock import patch, MagicMock
from scripts.sync_live_scores import overlay_espn_live_fields, sync_authoritative


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
        assert written_data[0] == {
            "is_live": True, "clock": "5:23", "period": 2, "possession": None,
            "live_home_score": 10, "live_away_score": 7,
        }
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
        assert written_data[0] == {
            "is_live": True, "clock": "Halftime", "period": 2, "possession": None,
            "live_home_score": 10, "live_away_score": 7,
        }
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

    def test_live_score_is_written_to_display_only_fields(self):
        """ESPN's in-game score must land in live_home_score/live_away_score,
        never in home_score/away_score -- those drive win totals and must
        only ever come from nflverse's authoritative data."""
        db = MagicMock()
        live_data = {
            ("BUF", "KC"): {"home_score": 10, "away_score": 7, "status": "STATUS_IN_PROGRESS",
                             "clock": "5:23", "period": 2},
        }
        overlay_espn_live_fields(db, _games_df(), live_data)

        doc_ref = db.collection.return_value.document.return_value
        written_data, _ = doc_ref.set.call_args
        assert written_data[0]["live_home_score"] == 10
        assert written_data[0]["live_away_score"] == 7
        assert "home_score" not in written_data[0]
        assert "away_score" not in written_data[0]

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


class TestSyncAuthoritativeScoping:
    """Task 9: sync_authoritative() scopes to the active season only (not
    current + prior season -- compute_standings() has no cross-season
    dependency, matching daily_nfl_sync.py's Task 8 change) and diffs
    before writing, same as daily_nfl_sync.py."""

    def _patch_subprocess_ok(self):
        return patch("scripts.sync_live_scores.subprocess.run",
                     return_value=MagicMock(returncode=0, stderr=""))

    def test_scopes_standings_to_active_season_only(self, monkeypatch):
        from scripts import sync_live_scores
        fake_games = pd.DataFrame([
            {"season": 2025, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1",
             "gameday": "2025-09-01"},
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2",
             "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert set(captured["nfl_standings"]["season"].unique()) == {2026}

    def test_uses_diff_before_write(self, monkeypatch):
        from scripts import sync_live_scores
        calls = []

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            calls.append(diff_before_write)
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2",
             "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
        ]))

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert calls  # at least one batch_upload call happened
        assert all(calls)  # every call in this function passes diff_before_write=True

    def test_still_narrows_games_push_to_trailing_week(self, monkeypatch):
        """The nfl_games push window (last ~7 days by gameday) is unrelated
        to the season-scoping change above and must survive it unchanged."""
        from scripts import sync_live_scores
        old_game_day = (pd.Timestamp.now() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        recent_game_day = pd.Timestamp.now().strftime("%Y-%m-%d")
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "old",
             "gameday": old_game_day},
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "result": None, "home_score": None, "away_score": None, "game_id": "new",
             "gameday": recent_game_day},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert list(captured["nfl_games"]["game_id"]) == ["new"]

    def test_preseason_window_with_zero_completed_games_does_not_crash(self, monkeypatch):
        """Regression: scoping to the active season alone (no prior-season
        safety net) means this can hit compute_standings() with zero
        completed REG games -- e.g. every 5-minute run during the preseason
        window, before that season's Week 1 has finished."""
        from scripts import sync_live_scores
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "PRE", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "pre1",
             "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        monkeypatch.setattr(sync_live_scores, "batch_upload", lambda *a, **k: 0)

        with self._patch_subprocess_ok():
            result = sync_authoritative(MagicMock())  # must not raise -- that's the whole point

        assert list(result["game_id"]) == ["pre1"]

    def test_game_exactly_seven_days_old_is_included(self, monkeypatch):
        """The filter is >= today - 7 days, so a game exactly 7 days old is
        the oldest game still included, not the newest game excluded."""
        from scripts import sync_live_scores
        exactly_seven_days_ago = (pd.Timestamp.now().normalize() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "boundary",
             "gameday": exactly_seven_days_ago},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert list(captured["nfl_games"]["game_id"]) == ["boundary"]

    def test_game_eight_days_old_is_excluded(self, monkeypatch):
        """One day past the boundary must be excluded -- confirms the window
        actually has an edge, not just an off-by-a-lot margin."""
        from scripts import sync_live_scores
        eight_days_ago = (pd.Timestamp.now().normalize() - pd.Timedelta(days=8)).strftime("%Y-%m-%d")
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "too_old",
             "gameday": eight_days_ago},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            sync_authoritative(MagicMock())

        assert list(captured["nfl_games"]["game_id"]) == []

    def test_malformed_gameday_is_excluded_not_crashed_on(self, monkeypatch):
        """A game with an unparseable/missing gameday must be silently
        excluded from the trailing-week push, never raise or crash the sync."""
        from scripts import sync_live_scores
        fake_games = pd.DataFrame([
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
             "result": 3, "home_score": 20, "away_score": 17, "game_id": "malformed",
             "gameday": "not-a-real-date"},
            {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
             "result": None, "home_score": None, "away_score": None, "game_id": "good",
             "gameday": pd.Timestamp.now().strftime("%Y-%m-%d")},
        ])
        monkeypatch.setattr(sync_live_scores, "load_games", lambda: fake_games)
        captured = {}

        def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
            captured[name] = df
            return 0
        monkeypatch.setattr(sync_live_scores, "batch_upload", fake_batch_upload)

        with self._patch_subprocess_ok():
            result = sync_authoritative(MagicMock())  # must not raise

        assert list(captured["nfl_games"]["game_id"]) == ["good"]
        assert list(result["game_id"]) == ["good"]


class TestMainSignaling:
    """Task 9: main() signals DOMAIN_ACTIVE unconditionally (the ESPN
    overlay writes is_live/clock/period even when nflverse data didn't
    change, so gating on sync_authoritative()'s own write counts alone
    would under-signal during a live game)."""

    def test_signals_active_domain_after_overlay(self):
        from services.cache_service import DOMAIN_ACTIVE
        with patch("scripts.sync_live_scores.initialize_firebase", return_value=MagicMock()), \
             patch("scripts.sync_live_scores.sync_authoritative", return_value=_games_df()), \
             patch("scripts.sync_live_scores.run_espn_overlay_safely", return_value=0), \
             patch("services.db_service.signal_data_update") as mock_signal:
            from scripts.sync_live_scores import main
            main()

        mock_signal.assert_called_once_with(DOMAIN_ACTIVE)


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
