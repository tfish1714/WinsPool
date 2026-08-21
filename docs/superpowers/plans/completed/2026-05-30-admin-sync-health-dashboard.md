# Admin Sync Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a persistent health bar to the admin page showing real-time state of NFL games, standings, Elo, predictions, analytics cache, and nflverse sync — derived from existing data sources.

**Architecture:** A new `GET /api/admin/sync_status` endpoint (admin-only) computes six health areas on-demand from `load_data()` + `get_game_predictions()` + `get_metadata()`. Two scripts (`compute_elo.py`, `sync_nflverse_data.py`) write completion metadata via the existing `save_metadata()`. A new `admin_health.js` renders a chip row above the tab bar and auto-refreshes every 5 minutes.

**Tech Stack:** FastAPI, pandas, existing `db_service.save_metadata/get_metadata`, vanilla JS (IIFE), CSS custom properties

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `routes/admin_routes.py` | Modify | Add `GET /api/admin/sync_status` endpoint |
| `scripts/compute_elo.py` | Modify | Write `metadata/sync_elo` on completion |
| `scripts/sync_nflverse_data.py` | Modify | Write `metadata/sync_nflverse` on completion |
| `templates/admin.html` | Modify | Insert `#sync-health-bar` div + script tag |
| `static/js/admin_health.js` | Create | Fetch, render chips, auto-refresh |
| `static/style.css` | Modify | Add `.sync-health-bar` and `.health-chip` styles |
| `tests/test_sync_health.py` | Create | Unit tests for endpoint logic |

---

## Task 1: `/api/admin/sync_status` endpoint

**Files:**
- Modify: `routes/admin_routes.py`
- Create: `tests/test_sync_health.py`

### Background

`load_data()` returns `(standings_master, teams, all_games, players, draft_order, draft_results, rules)`.
`get_active_season(games)` returns the last season with real game results (e.g. 2025, not 2026).
`UNDRAFTED_SENTINEL = -1000` marks placeholder result rows — filter these out when counting completed games.
`get_game_predictions(season)` returns a dict of `{key: {"locked": bool, ...}}` where keys are `W{wk:02d}_{home}_{away}`.
`get_metadata(doc_id)` returns a dict or `None` if the document doesn't exist yet.

Status values: `"ok"`, `"warn"` (stale thresholds: cache >12h, nflverse >3d, Elo >7d), `"error"`, `"unknown"` (metadata doc missing).

Note: `nfl_standings` has no `week` column — standings are season-level. The standings chip reuses `current_week` from the NFL Games computation (standings reflect all completed game results).

- [x] **Step 1: Write failing tests**

Create `tests/test_sync_health.py`:

```python
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


def _preds(season=2025, locked=272, unlocked=0):
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


class TestSyncStatus:
    def test_happy_path_returns_all_six_areas(self, admin_token):
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_meta_ok()):
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
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
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(n=272, week=18),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
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
            mock_load.return_value = (
                pd.DataFrame(), pd.DataFrame(), empty_games,
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        data = resp.json()
        assert data["nfl_games"]["status"] == "error"

    def test_cache_warn_when_older_than_12h(self, admin_token):
        def _stale_meta(doc_id):
            if doc_id == "cache_control":
                return {"last_update": time.time() - 50000}  # ~13.9 hours
            return None
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_stale_meta):
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.json()["analytics_cache"]["status"] == "warn"

    def test_elo_unknown_when_metadata_missing(self, admin_token):
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", return_value=None):
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
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
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.json()["elo"]["status"] == "warn"

    def test_predictions_locked_through_week_derived_from_keys(self, admin_token):
        preds = {"W14_KC_BUF": {"locked": True}, "W15_NE_NYJ": {"locked": False}}
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=preds), \
             patch("routes.admin_routes.get_metadata", side_effect=_meta_ok()):
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        p = resp.json()["predictions"]
        assert p["locked_through_week"] == 14
        assert p["locked"] == 1
        assert p["unlocked"] == 1
        assert p["coverage_pct"] == 50.0

    def test_individual_area_error_does_not_block_others(self, admin_token):
        """If one area raises, others still return data."""
        call_count = 0
        def _raising_meta(doc_id):
            nonlocal call_count
            call_count += 1
            if doc_id == "cache_control":
                raise RuntimeError("Firestore unavailable")
            return None
        with patch("routes.admin_routes.load_data") as mock_load, \
             patch("routes.admin_routes.get_active_season", return_value=2025), \
             patch("routes.admin_routes.get_game_predictions", return_value=_preds()), \
             patch("routes.admin_routes.get_metadata", side_effect=_raising_meta):
            mock_load.return_value = (
                _standings_df(), pd.DataFrame(), _games_df(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            resp = client.get("/api/admin/sync_status", headers={"Authorization": admin_token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["analytics_cache"]["status"] == "error"
        # Other areas unaffected
        assert data["nfl_games"]["status"] == "ok"

    def test_requires_admin_role(self, auth_token):
        resp = client.get("/api/admin/sync_status", headers={"Authorization": auth_token})
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        resp = client.get("/api/admin/sync_status")
        assert resp.status_code in (401, 403)
```

- [x] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sync_health.py -v
```

Expected: FAIL — `404 Not Found` (endpoint doesn't exist yet)

- [x] **Step 3: Add imports to `routes/admin_routes.py`**

Find the existing import block at the top of `routes/admin_routes.py`. Make these additions:

Add `import time` after `import sys` (stdlib block):
```python
import time
```

Change the `services.data_service` import line from:
```python
from services.data_service import load_data
```
to:
```python
from services.data_service import load_data, get_active_season
```

Change the `services.db_service` import block to add `get_metadata`:
```python
from services.db_service import (
    add_draft_order, add_draft_rule, add_player, delete_draft_results_for_season,
    delete_season_data, get_collection_df, get_metadata, get_password_hash,
    save_weekly_recap, set_member_paid, update_player_credentials, update_player_profile,
)
```

Change the `services.constants` import line from:
```python
from services.constants import PASSWORD_COMPLEXITY_RE
```
to:
```python
from services.constants import PASSWORD_COMPLEXITY_RE, UNDRAFTED_SENTINEL
```

- [x] **Step 4: Add the endpoint to `routes/admin_routes.py`**

Add this function at the end of the file, before the `_page_router` routes section (before `@_page_router.get("/admin/predictions")`):

```python
@router.get("/admin/sync_status")
async def get_sync_status(_: dict = Depends(require_admin)):
    """Admin: Health check on all calculated data and sync pipelines."""
    try:
        all_st, _, all_games, _, _, _, _ = load_data()
        active_season = get_active_season(all_games)
    except Exception:
        logger.exception("sync_status: failed to load data")
        return server_error()

    result: dict = {}

    # ── NFL Games ──────────────────────────────────────────────────────────
    try:
        season_games = all_games[all_games["season"] == active_season] if not all_games.empty else all_games.iloc[0:0]
        has_result = season_games[season_games["result"].notna() & (season_games["result"] != UNDRAFTED_SENTINEL)] if not season_games.empty else season_games.iloc[0:0]
        current_week = int(has_result["week"].max()) if not has_result.empty else 0
        last_game_date = str(has_result["gameday"].max()) if not has_result.empty else None
        result["nfl_games"] = {
            "season": active_season,
            "current_week": current_week,
            "games_total": len(season_games),
            "games_with_results": len(has_result),
            "last_game_date": last_game_date,
            "status": "ok" if len(has_result) > 0 else "error",
        }
    except Exception as e:
        logger.warning("sync_status nfl_games: %s", e)
        result["nfl_games"] = {"status": "error", "error": str(e)}

    # ── Standings ──────────────────────────────────────────────────────────
    try:
        season_st = all_st[all_st["season"] == active_season] if not all_st.empty else all_st.iloc[0:0]
        teams_count = int(season_st["team"].nunique()) if not season_st.empty else 0
        result["standings"] = {
            "season": active_season,
            "week": result.get("nfl_games", {}).get("current_week", 0),
            "teams_count": teams_count,
            "status": "ok" if teams_count > 0 else "error",
        }
    except Exception as e:
        logger.warning("sync_status standings: %s", e)
        result["standings"] = {"status": "error", "error": str(e)}

    # ── Predictions ────────────────────────────────────────────────────────
    try:
        preds = get_game_predictions(active_season)
        locked = sum(1 for v in preds.values() if v.get("locked"))
        unlocked = sum(1 for v in preds.values() if not v.get("locked"))
        total = locked + unlocked
        locked_through_week = 0
        for key, v in preds.items():
            if v.get("locked"):
                try:
                    locked_through_week = max(locked_through_week, int(key.split("_")[0][1:]))
                except (ValueError, IndexError):
                    pass
        coverage_pct = round(locked / total * 100, 1) if total > 0 else 0.0
        result["predictions"] = {
            "season": active_season,
            "locked": locked,
            "unlocked": unlocked,
            "locked_through_week": locked_through_week,
            "coverage_pct": coverage_pct,
            "status": "ok" if locked > 0 else ("warn" if total > 0 else "error"),
        }
    except Exception as e:
        logger.warning("sync_status predictions: %s", e)
        result["predictions"] = {"status": "error", "error": str(e)}

    # ── Analytics Cache ────────────────────────────────────────────────────
    try:
        cache_meta = get_metadata("cache_control")
        if cache_meta is None:
            result["analytics_cache"] = {"status": "unknown"}
        else:
            last_rebuilt_at = cache_meta.get("last_update", 0)
            age_hours = round((time.time() - float(last_rebuilt_at)) / 3600, 1)
            result["analytics_cache"] = {
                "last_rebuilt_at": last_rebuilt_at,
                "age_hours": age_hours,
                "status": "ok" if age_hours <= 12 else "warn",
            }
    except Exception as e:
        logger.warning("sync_status analytics_cache: %s", e)
        result["analytics_cache"] = {"status": "error", "error": str(e)}

    # ── Elo ────────────────────────────────────────────────────────────────
    try:
        elo_meta = get_metadata("sync_elo")
        if elo_meta is None:
            result["elo"] = {"status": "unknown"}
        else:
            age_days = (time.time() - float(elo_meta.get("completed_at", 0))) / 86400
            script_status = elo_meta.get("status", "unknown")
            elo_status = "error" if script_status == "error" else ("warn" if age_days > 7 else "ok")
            result["elo"] = {
                "completed_at": elo_meta.get("completed_at"),
                "season": elo_meta.get("season"),
                "week": elo_meta.get("week"),
                "games_processed": elo_meta.get("games_processed"),
                "status": elo_status,
                "error": elo_meta.get("error"),
            }
    except Exception as e:
        logger.warning("sync_status elo: %s", e)
        result["elo"] = {"status": "error", "error": str(e)}

    # ── nflverse ───────────────────────────────────────────────────────────
    try:
        nflverse_meta = get_metadata("sync_nflverse")
        if nflverse_meta is None:
            result["nflverse"] = {"status": "unknown"}
        else:
            age_days = (time.time() - float(nflverse_meta.get("completed_at", 0))) / 86400
            script_status = nflverse_meta.get("status", "unknown")
            nflverse_status = "error" if script_status == "error" else ("warn" if age_days > 3 else "ok")
            result["nflverse"] = {
                "completed_at": nflverse_meta.get("completed_at"),
                "season": nflverse_meta.get("season"),
                "datasets_synced": nflverse_meta.get("datasets_synced"),
                "datasets_skipped": nflverse_meta.get("datasets_skipped"),
                "datasets_failed": nflverse_meta.get("datasets_failed"),
                "status": nflverse_status,
                "error": nflverse_meta.get("error"),
            }
    except Exception as e:
        logger.warning("sync_status nflverse: %s", e)
        result["nflverse"] = {"status": "error", "error": str(e)}

    return JSONResponse(content=result)
```

- [x] **Step 5: Run tests and verify they pass**

```bash
pytest tests/test_sync_health.py -v
```

Expected: All 9 tests PASS

- [x] **Step 6: Commit**

```bash
git add routes/admin_routes.py tests/test_sync_health.py
git commit -m "feat: add GET /api/admin/sync_status health endpoint"
```

---

## Task 2: `compute_elo.py` metadata write

**Files:**
- Modify: `scripts/compute_elo.py`
- Modify: `tests/test_sync_health.py` (add script test)

### Background

`compute_elo.main()` calls `compute_elo(min_season, max_season)` which returns `(df, final_elo)`.
`df` is a DataFrame with columns `season` and `week` — use `df["season"].max()` and `df["week"].max()` for the metadata.
`len(df)` is the total games processed.
The project root must be on `sys.path` to import `services.db_service` from a script. Use the same pattern as `scripts/cache_builder.py`:
`sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))`
`compute_elo.py` already imports `pathlib` and `sys`.

- [x] **Step 1: Write failing test — append to `tests/test_sync_health.py`**

```python
class TestComputeEloMetadata:
    def test_writes_sync_elo_metadata_on_success(self, tmp_path):
        """main() calls save_metadata with correct shape after computing Elo."""
        import pandas as pd
        from unittest.mock import patch, MagicMock
        import sys
        import pathlib
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

        fake_df = pd.DataFrame([
            {"season": 2025, "week": 18, "home_elo": 1500, "away_elo": 1500},
            {"season": 2024, "week": 18, "home_elo": 1490, "away_elo": 1510},
        ])
        out_csv = tmp_path / "elo_computed.csv"

        with patch("scripts.compute_elo.compute_elo", return_value=(fake_df, {})) as mock_compute, \
             patch("scripts.compute_elo._print_season_summary"), \
             patch("scripts.compute_elo.save_metadata") as mock_save, \
             patch("sys.argv", ["compute_elo.py", "--output", str(out_csv)]):
            import scripts.compute_elo as elo_mod
            elo_mod.main()

        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[0]
        assert call_kwargs[0] == "sync_elo"
        meta = call_kwargs[1]
        assert meta["season"] == 2025
        assert meta["week"] == 18
        assert meta["games_processed"] == 2
        assert meta["status"] == "ok"
        assert "completed_at" in meta
```

- [x] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_sync_health.py::TestComputeEloMetadata -v
```

Expected: FAIL — `ImportError` or `AssertionError` (save_metadata not called)

- [x] **Step 3: Add sys.path setup and import to `scripts/compute_elo.py`**

After the existing `import sys` line near the top of `compute_elo.py`, add:

```python
import time

# Allow importing services when run as a script
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from services.db_service import save_metadata
```

Note: `pathlib` is already imported in `compute_elo.py`.

- [x] **Step 4: Add metadata write to `main()` in `scripts/compute_elo.py`**

At the end of `main()`, after `_print_season_summary(final_elo)` and before the closing `print(f"{'='*65}")`:

```python
    try:
        save_metadata("sync_elo", {
            "completed_at": time.time(),
            "season": int(df["season"].max()),
            "week": int(df["week"].max()),
            "games_processed": len(df),
            "status": "ok",
            "error": None,
        })
    except Exception as _e:
        print(f"  Warning: could not write sync metadata: {_e}")
```

- [x] **Step 5: Run test and verify it passes**

```bash
pytest tests/test_sync_health.py::TestComputeEloMetadata -v
```

Expected: PASS

- [x] **Step 6: Commit**

```bash
git add scripts/compute_elo.py tests/test_sync_health.py
git commit -m "feat: write sync_elo metadata after compute_elo.py completes"
```

---

## Task 3: `sync_nflverse_data.py` metadata write

**Files:**
- Modify: `scripts/sync_nflverse_data.py`
- Modify: `tests/test_sync_health.py` (add script test)

### Background

`sync_nflverse_data.main()` already has `seasons` (list of ints), `n_dl`, `n_skip`, `n_fail` from the `sync()` call (lines 416–422).
Use `max(seasons)` as the target season. `time` is already imported in `sync_nflverse_data.py` (it's used on line 407).
The script doesn't currently import from `services` — add the same `sys.path.insert` pattern.

- [x] **Step 1: Write failing test — append to `tests/test_sync_health.py`**

```python
class TestSyncNflverseMetadata:
    def test_writes_sync_nflverse_metadata_on_success(self, tmp_path):
        """main() calls save_metadata with correct shape after syncing."""
        import sys
        import pathlib
        from unittest.mock import patch

        with patch("scripts.sync_nflverse_data.sync", return_value=({}, 8, 2, 0)), \
             patch("scripts.sync_nflverse_data.save_metadata") as mock_save, \
             patch("sys.argv", ["sync_nflverse_data.py"]), \
             patch("sys.exit"):  # prevent sys.exit(0) from raising SystemExit before assertions
            import scripts.sync_nflverse_data as nfl_mod
            # importlib.reload to pick up fresh state if already imported
            import importlib
            importlib.reload(nfl_mod)
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

    def test_status_error_when_failures(self, tmp_path):
        """status is 'error' when n_fail > 0."""
        from unittest.mock import patch
        import importlib
        import scripts.sync_nflverse_data as nfl_mod

        with patch("scripts.sync_nflverse_data.sync", return_value=({}, 5, 0, 2)), \
             patch("scripts.sync_nflverse_data.save_metadata") as mock_save, \
             patch("sys.argv", ["sync_nflverse_data.py"]), \
             patch("sys.exit"):  # prevent sys.exit(1) from stopping test
            importlib.reload(nfl_mod)
            nfl_mod.main()

        meta = mock_save.call_args[0][1]
        assert meta["status"] == "error"
        assert meta["datasets_failed"] == 2
```

- [x] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sync_health.py::TestSyncNflverseMetadata -v
```

Expected: FAIL — `save_metadata` not called

- [x] **Step 3: Add sys.path setup and import to `scripts/sync_nflverse_data.py`**

After the `import sys` line near the top of `sync_nflverse_data.py`, add:

```python
import pathlib as _pathlib
sys.path.insert(0, str(_pathlib.Path(__file__).parent.parent))
from services.db_service import save_metadata
```

Note: `pathlib` may already be imported in this file — check for a conflict. If `pathlib` is already imported, use `sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))` directly instead of aliasing.

- [x] **Step 4: Add metadata write to `main()` in `scripts/sync_nflverse_data.py`**

At the end of `main()`, after the final `print(f"{'='*65}")` block and before `sys.exit(...)`:

```python
    try:
        save_metadata("sync_nflverse", {
            "completed_at": time.time(),
            "season": max(seasons),
            "datasets_synced": n_dl,
            "datasets_skipped": n_skip,
            "datasets_failed": n_fail,
            "status": "ok" if n_fail == 0 else "error",
            "error": None,
        })
    except Exception as _e:
        print(f"  Warning: could not write sync metadata: {_e}")
```

- [x] **Step 5: Run tests and verify they pass**

```bash
pytest tests/test_sync_health.py::TestSyncNflverseMetadata -v
```

Expected: PASS

- [x] **Step 6: Run full suite to check for regressions**

```bash
pytest tests/ -x -q
```

Expected: All tests pass (5 pre-existing Firebase errors are OK)

- [x] **Step 7: Commit**

```bash
git add scripts/sync_nflverse_data.py tests/test_sync_health.py
git commit -m "feat: write sync_nflverse metadata after sync_nflverse_data.py completes"
```

---

## Task 4: CSS styles and admin.html health bar

**Files:**
- Modify: `static/style.css`
- Modify: `templates/admin.html`

No automated tests for UI. Verify visually after the server starts.

- [x] **Step 1: Add CSS to `static/style.css`**

Append at the end of `static/style.css`:

```css
/* ── Sync Health Bar ─────────────────────────────────────── */
.sync-health-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.75rem 0 1rem;
    border-bottom: 1px solid var(--glass-border);
    margin-bottom: 1rem;
}

.health-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--glass-border);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.8rem;
    color: var(--text-secondary);
    white-space: nowrap;
    cursor: default;
}

.health-chip strong {
    color: var(--text-primary);
    margin-right: 2px;
}

@media (max-width: 480px) {
    .health-chip { font-size: 0.72rem; padding: 3px 8px; }
}
```

- [x] **Step 2: Insert health bar div into `templates/admin.html`**

Find the line (around line 96):
```html
    <!-- Admin Sub-menu Tabs -->
    <div class="admin-tab-bar admin-tabs">
```

Insert the health bar div immediately before it:
```html
    <!-- Sync Health Bar -->
    <div id="sync-health-bar" class="sync-health-bar">
        <div class="health-chip"><span style="color:var(--text-secondary);">●</span>&nbsp;Loading health status…</div>
    </div>

    <!-- Admin Sub-menu Tabs -->
    <div class="admin-tab-bar admin-tabs">
```

- [x] **Step 3: Add script tag for `admin_health.js` to `templates/admin.html`**

Find the existing script tag at the bottom of `admin.html`:
```html
<script type="module" src="/static/js/admin_main.js"></script>
```

Add `admin_health.js` immediately after it:
```html
<script type="module" src="/static/js/admin_main.js"></script>
<script src="/static/js/admin_health.js"></script>
```

Note: `admin_health.js` is a plain IIFE (not an ES module), so no `type="module"`.

- [x] **Step 4: Commit**

```bash
git add static/style.css templates/admin.html
git commit -m "feat: add sync health bar to admin page"
```

---

## Task 5: `admin_health.js` — fetch, render, auto-refresh

**Files:**
- Create: `static/js/admin_health.js`

- [x] **Step 1: Create `static/js/admin_health.js`**

```javascript
(function () {
    'use strict';

    const STATUS_COLORS = {
        ok:      'var(--accent-green)',
        warn:    '#f5a623',
        error:   'var(--accent-red)',
        unknown: 'var(--text-secondary)',
    };

    function relTime(unixTs) {
        if (!unixTs) return '?';
        const diffSec = Math.floor(Date.now() / 1000 - unixTs);
        if (diffSec < 3600)  return `${Math.round(diffSec / 60)}m ago`;
        if (diffSec < 86400) return `${(diffSec / 3600).toFixed(1)}h ago`;
        return `${Math.floor(diffSec / 86400)}d ago`;
    }

    function dot(status) {
        const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
        return `<span style="color:${color}; margin-right:4px;">●</span>`;
    }

    function buildChips(data) {
        const chips = [];

        const g = data.nfl_games || {};
        chips.push({
            label: 'NFL Games',
            text: g.season
                ? `${g.season} · W${g.current_week} · ${g.games_with_results}/${g.games_total} · ${g.last_game_date || '?'}`
                : (g.error || 'unavailable'),
            status: g.status || 'unknown',
            error: g.error,
        });

        const s = data.standings || {};
        chips.push({
            label: 'Standings',
            text: s.season
                ? `${s.season} · W${s.week} · ${s.teams_count} teams`
                : (s.error || 'unavailable'),
            status: s.status || 'unknown',
            error: s.error,
        });

        const e = data.elo || {};
        chips.push({
            label: 'Elo',
            text: e.season
                ? `${e.season} · W${e.week} · ${(e.games_processed || 0).toLocaleString()} games`
                : (e.status === 'unknown' ? 'never run' : (e.error || 'unavailable')),
            status: e.status || 'unknown',
            error: e.error,
        });

        const p = data.predictions || {};
        chips.push({
            label: 'Predictions',
            text: p.season
                ? `${p.season} · locked thru W${p.locked_through_week} · ${p.coverage_pct}%`
                : (p.error || 'unavailable'),
            status: p.status || 'unknown',
            error: p.error,
        });

        const c = data.analytics_cache || {};
        chips.push({
            label: 'Cache',
            text: c.age_hours != null
                ? `${c.age_hours}h ago`
                : (c.status === 'unknown' ? 'never built' : (c.error || 'unavailable')),
            status: c.status || 'unknown',
            error: c.error,
        });

        const n = data.nflverse || {};
        chips.push({
            label: 'nflverse',
            text: n.season
                ? `${n.season} · ${n.datasets_synced} synced · ${relTime(n.completed_at)}`
                : (n.status === 'unknown' ? 'never run' : (n.error || 'unavailable')),
            status: n.status || 'unknown',
            error: n.error,
        });

        return chips;
    }

    function render(chips) {
        const bar = document.getElementById('sync-health-bar');
        if (!bar) return;
        bar.innerHTML = chips.map(function (c) {
            const title = c.error ? ` title="${c.error}"` : '';
            return `<div class="health-chip"${title}>${dot(c.status)}<strong>${c.label}</strong>&nbsp;${c.text}</div>`;
        }).join('');
    }

    function renderError() {
        const bar = document.getElementById('sync-health-bar');
        if (!bar) return;
        bar.innerHTML = `<div class="health-chip"><span style="color:var(--text-secondary);">●</span>&nbsp;Health check unavailable</div>`;
    }

    async function refresh() {
        try {
            const resp = await fetch('/api/admin/sync_status');
            if (!resp.ok) { renderError(); return; }
            render(buildChips(await resp.json()));
        } catch (_) {
            renderError();
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        refresh();
        setInterval(refresh, 300000);
    });
}());
```

- [x] **Step 2: Start dev server and verify visually**

```bash
uvicorn main:app --reload
```

Navigate to `http://localhost:8000/admin` (admin login required). Verify:
- Health bar renders above the tab row
- Six chips appear with correct labels
- Chips show colored dots (green/yellow/red/grey)
- Error chips show tooltip on hover with error message
- Page still functions normally (tabs switch, existing features work)

- [x] **Step 3: Commit**

```bash
git add static/js/admin_health.js
git commit -m "feat: admin_health.js — renders sync health chips with auto-refresh"
```

---

## Final Check

- [x] **Run full test suite**

```bash
pytest tests/ -q
```

Expected: All tests pass. (5 pre-existing Firebase schema errors unrelated to this work are acceptable.)

- [x] **Verify endpoint response manually**

```bash
# With server running and admin token:
curl -H "Authorization: Bearer <admin_token>" http://localhost:8000/api/admin/sync_status | python -m json.tool
```

Expected: JSON with all 6 keys, each containing a `status` field.
