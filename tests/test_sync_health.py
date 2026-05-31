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


class TestComputeEloMetadata:
    def test_writes_sync_elo_metadata_on_success(self, tmp_path):
        """main() calls save_metadata with correct shape after computing Elo."""
        import importlib
        import scripts.compute_elo as elo_mod
        importlib.reload(elo_mod)

        fake_df = pd.DataFrame([
            {"season": 2025, "week": 18, "home_elo": 1500, "away_elo": 1500},
            {"season": 2024, "week": 18, "home_elo": 1490, "away_elo": 1510},
        ])
        out_csv = tmp_path / "elo_computed.csv"

        with patch("scripts.compute_elo.compute_elo", return_value=(fake_df, {})), \
             patch("scripts.compute_elo._print_season_summary"), \
             patch("scripts.compute_elo.save_metadata") as mock_save, \
             patch("sys.argv", ["compute_elo.py", "--output", str(out_csv)]):
            elo_mod.main()

        mock_save.assert_called_once()
        call_args = mock_save.call_args[0]
        assert call_args[0] == "sync_elo"
        meta = call_args[1]
        assert meta["season"] == 2025
        assert meta["week"] == 18
        assert meta["games_processed"] == 2
        assert meta["status"] == "ok"
        assert "completed_at" in meta


class TestSyncNflverseMetadata:
    def test_writes_sync_nflverse_metadata_on_success(self):
        """main() calls save_metadata with correct shape after syncing."""
        import importlib
        import scripts.sync_nflverse_data as nfl_mod
        importlib.reload(nfl_mod)

        with patch("scripts.sync_nflverse_data.sync", return_value=({}, 8, 2, 0)), \
             patch("scripts.sync_nflverse_data.save_metadata") as mock_save, \
             patch("sys.argv", ["sync_nflverse_data.py"]), \
             patch("sys.exit"):  # prevent sys.exit(0) from raising SystemExit
            nfl_mod.main()

        mock_save.assert_called_once()
        call_args = mock_save.call_args[0]
        assert call_args[0] == "sync_nflverse"
        meta = call_args[1]
        assert meta["datasets_synced"] == 8
        assert meta["datasets_skipped"] == 2
        assert meta["datasets_failed"] == 0
        assert meta["status"] == "ok"
        assert "completed_at" in meta
        assert "season" in meta

    def test_status_error_when_failures(self):
        """status is 'error' when n_fail > 0."""
        import importlib
        import scripts.sync_nflverse_data as nfl_mod
        importlib.reload(nfl_mod)

        with patch("scripts.sync_nflverse_data.sync", return_value=({}, 5, 0, 2)), \
             patch("scripts.sync_nflverse_data.save_metadata") as mock_save, \
             patch("sys.argv", ["sync_nflverse_data.py"]), \
             patch("sys.exit"):
            nfl_mod.main()

        meta = mock_save.call_args[0][1]
        assert meta["status"] == "error"
        assert meta["datasets_failed"] == 2
