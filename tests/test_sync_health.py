"""tests/test_sync_health.py — Unit tests for GET /api/admin/sync_status."""
import time
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app

client = TestClient(app)


def _games_df(season=2025, n=272, week=18):
    rows = [{"season": season, "week": week, "result": 3.0, "gameday": "2026-01-05"} for _ in range(n)]
    return pd.DataFrame(rows)


def _standings_df(season=2025, n_teams=32):
    return pd.DataFrame([{"season": season, "team": f"T{i}", "wins": 8} for i in range(n_teams)])


def _preds(locked=272, unlocked=0):
    preds = {}
    for i in range(locked):
        preds[f"W18_KC_BUF_{i}"] = {"locked": True}
    for i in range(unlocked):
        preds[f"W19_KC_BUF_{i}"] = {"locked": False}
    return preds


def _meta_ok():
    now = time.time()
    def _side(doc_id):
        if doc_id == "cache_control":
            return {"last_update": now - 3600}
        if doc_id == "sync_elo":
            return {"completed_at": now - 3600, "season": 2025, "week": 18,
                    "games_processed": 12453, "status": "ok", "error": None}
        if doc_id == "sync_nflverse":
            return {"completed_at": now - 3600, "season": 2025, "datasets_synced": 8,
                    "datasets_skipped": 2, "datasets_failed": 0, "status": "ok", "error": None}
        return None
    return _side


def _load_data_return(games=None, standings=None):
    import pandas as pd
    g = games if games is not None else _games_df()
    s = standings if standings is not None else _standings_df()
    return (s, pd.DataFrame(), g, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())


class TestSyncStatus:
    def test_happy_path_returns_all_six_areas(self, admin_token):
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_meta_ok()):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        data = resp.json()
        for key in ("nfl_games", "standings", "predictions", "analytics_cache", "elo", "nflverse"):
            assert key in data, f"Missing key: {key}"
            assert "status" in data[key]

    def test_nfl_games_ok_when_results_present(self, admin_token):
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_meta_ok()):
            mock_load.return_value = _load_data_return(games=_games_df(n=272, week=18))
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        data = resp.json()
        g = data["nfl_games"]
        assert g["status"] == "ok"
        assert g["season"] == 2025
        assert g["current_week"] == 18
        assert g["games_total"] == 272
        assert g["games_with_results"] == 272

    def test_nfl_games_error_when_no_results(self, admin_token):
        empty_games = pd.DataFrame([{"season": 2025, "week": 1, "result": None, "gameday": None}])
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value={}), \
             patch("routes.admin_routes.get_metadata", return_value=None):
            mock_load.return_value = _load_data_return(games=empty_games)
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.json()["nfl_games"]["status"] == "error"

    def test_cache_warn_when_older_than_12h(self, admin_token):
        def _stale_meta(doc_id):
            if doc_id == "cache_control":
                return {"last_update": time.time() - 50000}
            return None
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_stale_meta):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.json()["analytics_cache"]["status"] == "warn"

    def test_elo_unknown_when_metadata_missing(self, admin_token):
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", return_value=None):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        data = resp.json()
        assert data["elo"]["status"] == "unknown"
        assert data["nflverse"]["status"] == "unknown"

    def test_elo_warn_when_older_than_7_days(self, admin_token):
        def _old_elo(doc_id):
            if doc_id == "sync_elo":
                return {"completed_at": time.time() - 8 * 86400, "season": 2025,
                        "week": 14, "games_processed": 10000, "status": "ok", "error": None}
            return None
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_old_elo):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.json()["elo"]["status"] == "warn"

    def test_predictions_locked_through_week_derived_from_keys(self, admin_token):
        preds = {"W14_KC_BUF": {"locked": True}, "W15_NE_NYJ": {"locked": False}}
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=preds), \
             patch("routes.admin_routes.get_metadata", side_effect=_meta_ok()):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        p = resp.json()["predictions"]
        assert p["locked_through_week"] == 14
        assert p["locked"] == 1
        assert p["unlocked"] == 1
        assert p["coverage_pct"] == 50.0

    def test_individual_area_error_does_not_block_others(self, admin_token):
        def _raising_meta(doc_id):
            if doc_id == "cache_control":
                raise RuntimeError("Firestore unavailable")
            return None
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_raising_meta):
            mock_load.return_value = _load_data_return()
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["analytics_cache"]["status"] == "error"
        assert data["nfl_games"]["status"] == "ok"

    def test_requires_admin_role(self, auth_token):
        resp = client.get("/api/admin/sync_status", headers={"Authorization": auth_token})
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        resp = client.get("/api/admin/sync_status")
        assert resp.status_code in (401, 403)
