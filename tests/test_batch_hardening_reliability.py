"""Task 1 (batch hardening): eval --skip-existing, fail-closed draft gate, require_db logging."""
import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

import services.db_service as db_service


# ---------- weekly_model_eval --skip-existing ----------

def _rows(weeks):
    return [{"season": 2025, "week": w, "games": 10, "correct": 6, "accuracy_pct": 60.0}
            for w in weeks]


def _run_eval(argv, existing, monkeypatch, tmp_path):
    import scripts.weekly_model_eval as wme
    monkeypatch.setattr(sys, "argv", ["weekly_model_eval.py", *argv])
    monkeypatch.setattr(wme, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(wme, "ACCURACY_CSV", tmp_path / "acc.csv")
    nn, xgb, lr = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(wme, "NNPredictionService", nn)
    monkeypatch.setattr(wme, "XGBPredictionService", xgb)
    monkeypatch.setattr(wme, "LRPredictionService", lr)
    build = MagicMock(return_value=[])
    monkeypatch.setattr(wme, "build_master_feature_table", build)
    monkeypatch.setattr(wme, "_evaluate_weeks", MagicMock(return_value=_rows([14])))
    with patch("services.cache_service.get_nn_weekly_accuracy_season", return_value=existing), \
         patch("services.cache_service.write_nn_weekly_accuracy_rows") as write:
        wme.main()
    return nn, build, write


class TestEvalSkipExisting:
    def test_existing_snapshot_skips_without_touching_store(self, monkeypatch, tmp_path, capsys):
        nn, build, write = _run_eval(
            ["--season", "2025", "--week", "14", "--skip-existing"],
            _rows([14]), monkeypatch, tmp_path)
        nn.assert_not_called()
        build.assert_not_called()
        write.assert_not_called()
        assert "skip" in capsys.readouterr().out.lower()

    def test_no_snapshot_proceeds(self, monkeypatch, tmp_path):
        nn, build, write = _run_eval(
            ["--season", "2025", "--week", "14", "--skip-existing"],
            None, monkeypatch, tmp_path)
        build.assert_called_once()
        write.assert_called()

    def test_snapshot_for_other_week_proceeds(self, monkeypatch, tmp_path):
        _, build, write = _run_eval(
            ["--season", "2025", "--week", "14", "--skip-existing"],
            _rows([13]), monkeypatch, tmp_path)
        build.assert_called_once()

    def test_no_flag_overwrites_even_when_snapshot_exists(self, monkeypatch, tmp_path):
        _, build, write = _run_eval(
            ["--season", "2025", "--week", "14"], _rows([14]), monkeypatch, tmp_path)
        build.assert_called_once()
        write.assert_called()

    def test_range_skips_only_when_all_weeks_present(self, monkeypatch, tmp_path):
        _, build, _ = _run_eval(
            ["--season", "2025", "--week", "13", "14", "--skip-existing"],
            _rows([13]), monkeypatch, tmp_path)
        build.assert_called_once()
        _, build2, _ = _run_eval(
            ["--season", "2025", "--week", "13", "14", "--skip-existing"],
            _rows([13, 14]), monkeypatch, tmp_path)
        build2.assert_not_called()


def test_cache_builder_tuesday_step_passes_skip_existing():
    import pandas as pd
    import scripts.cache_builder as cb
    from datetime import datetime, timezone
    games = pd.DataFrame([{"season": 2026, "week": 1, "result": 3.0, "game_type": "REG"},
                          {"season": 2026, "week": 1, "result": -3.0, "game_type": "REG"}])
    with patch("scripts.cache_builder.datetime") as dt, \
         patch.object(cb, "_run_subprocess_step") as step:
        dt.now.return_value = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        cb._run_weekly_eval_if_tuesday(games, 2026)
    assert "--skip-existing" in step.call_args[0][0]


# ---------- is_draft_active_fail_closed ----------

class TestDraftActiveFailClosed:
    def test_local_mode_mirrors_config(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr(db_service, "get_config_settings", lambda: {"draft_active": True})
        assert db_service.is_draft_active_fail_closed() is True
        monkeypatch.setattr(db_service, "get_config_settings", lambda: {"draft_active": False})
        assert db_service.is_draft_active_fail_closed() is False

    def test_local_mode_get_db_none_stays_open(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "true")
        monkeypatch.setattr(db_service, "get_db", lambda: None)
        monkeypatch.setattr(db_service, "get_config_settings", lambda: {"draft_active": False})
        assert db_service.is_draft_active_fail_closed() is False

    def test_remote_no_db_fails_closed(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        monkeypatch.setattr(db_service, "get_db", lambda: None)
        assert db_service.is_draft_active_fail_closed() is True

    def test_remote_read_raises_fails_closed(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        monkeypatch.setattr(db_service, "get_db", lambda: MagicMock())
        def boom():
            raise RuntimeError("firestore down")
        monkeypatch.setattr(db_service, "get_config_settings", boom)
        assert db_service.is_draft_active_fail_closed() is True

    def test_remote_ok_mirrors_config(self, monkeypatch):
        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        monkeypatch.setattr(db_service, "get_db", lambda: MagicMock())
        monkeypatch.setattr(db_service, "get_config_settings", lambda: {"draft_active": False})
        assert db_service.is_draft_active_fail_closed() is False


class TestRoutesFailClosedBehavior:
    """Remote mode with no Firestore client: non-admins lose projections, admins keep them."""

    def _outlook(self, monkeypatch, is_admin):
        import pandas as pd
        from routes import api_routes
        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        monkeypatch.setattr(db_service, "get_db", lambda: None)
        load = (None, None, pd.DataFrame(), None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        with patch("routes.api_routes.load_data", return_value=load), \
             patch("services.data_service.get_active_season", return_value=2026), \
             patch("routes.api_routes.get_season_projection_legacy_shape", return_value={}):
            return api_routes.build_player_outlook(1, is_admin)

    def test_outlook_non_admin_blocked(self, monkeypatch):
        assert self._outlook(monkeypatch, False)["reason"] == "draft_in_progress"

    def test_outlook_admin_bypasses_gate(self, monkeypatch):
        assert self._outlook(monkeypatch, True)["reason"] != "draft_in_progress"

    def test_team_page_hides_projections_for_non_admin_and_serves_admin(self, monkeypatch):
        import json
        import re
        import pandas as pd
        from fastapi.testclient import TestClient
        from main import app
        from services.session_service import create_token

        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        monkeypatch.setattr(db_service, "get_db", lambda: None)  # config unreadable
        standings = pd.DataFrame([{"season": 2025, "team": "KC", "wins": 14, "losses": 3, "ties": 0}])
        players = pd.DataFrame([{"playerId": 1, "fullName": "Alice", "nickName": "Alice"}])
        draft = pd.DataFrame([{"playerId": 1, "season": 2025, "draftPick": 4, "team": "KC"}])
        games = pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "KC", "away_team": "LV",
             "result": None, "home_score": None, "away_score": None}])
        load = (standings, pd.DataFrame(), games, players, pd.DataFrame(), draft, pd.DataFrame())
        preds = {"W03_KC_LV": {"pred_prob": 0.7}}
        client = TestClient(app, follow_redirects=False)

        def fetch(token):
            with patch("routes.history_routes.load_data", return_value=load), \
                 patch("routes.history_routes.get_active_season", return_value=2026), \
                 patch("routes.history_routes.get_game_predictions", return_value=preds), \
                 patch("routes.history_routes.get_season_projection_legacy_shape",
                       return_value={"KC": {"projected_wins": 11.4}}):
                r = client.get("/team/KC", cookies={"session_token": token})
            assert r.status_code == 200
            m = re.search(r'<script id="teamData" type="application/json">(.*?)</script>', r.text, re.S)
            return json.loads(m.group(1))["current"]

        non_admin = fetch(create_token(1, "player"))
        assert non_admin["projected_wins"] is None
        assert non_admin["projected_record"] is None
        assert all(g["win_prob"] is None and g["projected"] is None for g in non_admin["schedule"])

        admin = fetch(create_token(9, "admin"))
        assert admin["projected_wins"] == 11.4
        assert any(g["win_prob"] is not None for g in admin["schedule"])


# ---------- require_db logging ----------

def test_require_db_reports_message_once(monkeypatch, capsys, caplog):
    monkeypatch.setattr(db_service, "get_db", lambda: None)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SystemExit):
            db_service.require_db(exit_on_missing=True, missing_message="NO FIRESTORE HERE")
    out = capsys.readouterr().out
    logged = [r for r in caplog.records if "NO FIRESTORE HERE" in r.getMessage()]
    assert out.count("NO FIRESTORE HERE") == 1
    assert logged == []


def test_eval_skip_existing_with_firestore_reads_firestore(monkeypatch):
    import scripts.weekly_model_eval as wme
    monkeypatch.setattr(sys, "argv", ["weekly_model_eval.py", "--season", "2025",
                                      "--week", "14", "--firestore", "--skip-existing"])
    nn = MagicMock()
    monkeypatch.setattr(wme, "NNPredictionService", nn)
    with patch("services.cache_service._read_nn_weekly_accuracy_firestore",
               return_value=_rows([14])), \
         patch("services.cache_service.write_nn_weekly_accuracy_rows") as write:
        wme.main()
    nn.assert_not_called()
    write.assert_not_called()
