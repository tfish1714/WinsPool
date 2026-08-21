# Admin Sync Health Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persistent health bar to the admin page that shows the real-time state of all calculated app data — NFL games, standings, Elo, predictions, analytics cache, and nflverse raw sync — derived from existing data sources and supplemented by script-written metadata.

**Architecture:** A new `GET /api/admin/sync_status` endpoint derives metrics on-demand from Firestore/cache (no heavy queries — uses the same `load_data()` path), supplemented by `metadata/sync_elo` and `metadata/sync_nflverse` documents written by their respective scripts. The admin page renders a slim status bar above the tab row via a new `admin_health.js` file.

**Tech Stack:** FastAPI, Firestore/pickle via existing `db_service.save_metadata` / `get_metadata`, Jinja2, vanilla JS

---

## Data Layer

### Six health areas and their sources

| Area | Source | Key fields |
|---|---|---|
| NFL Games | `nfl_games` collection via `load_data()` | `season`, `current_week`, `games_total`, `games_with_results`, `last_game_date` |
| Standings | `nfl_standings` collection via `load_data()` | `season`, `week`, `teams_count` |
| Predictions | `game_predictions` cache via `get_game_predictions()` | `season`, `locked`, `unlocked`, `locked_through_week`, `coverage_pct` |
| Analytics Cache | `metadata/cache_control` (already written by `daily_nfl_sync.py`) | `last_rebuilt_at`, `age_hours` |
| Elo | `metadata/sync_elo` written by `compute_elo.py` | `completed_at`, `season`, `week`, `games_processed`, `status`, `error` |
| nflverse | `metadata/sync_nflverse` written by `sync_nflverse_data.py` | `completed_at`, `season`, `datasets_synced`, `datasets_skipped`, `datasets_failed`, `status`, `error` |

### Script metadata writes

**`compute_elo.py`** — add at end of main():
```python
from services.db_service import save_metadata
save_metadata("sync_elo", {
    "completed_at": time.time(),
    "season": current_season,   # highest season processed
    "week": last_week,          # highest week processed
    "games_processed": total_games_processed,
    "status": "ok",
    "error": None,
})
```
Wrap in try/except; on exception write `status: "error", error: str(e)`.

**`sync_nflverse_data.py`** — add at end of main() after download loop:
```python
from services.db_service import save_metadata
save_metadata("sync_nflverse", {
    "completed_at": time.time(),
    "season": target_season,       # primary season argument (or current year)
    "datasets_synced": synced_count,
    "datasets_skipped": skipped_count,
    "datasets_failed": failed_count,
    "status": "ok" if failed_count == 0 else "error",
    "error": None,
})
```

### Status values
- `"ok"` — data is present and fresh
- `"warn"` — data exists but is stale (cache > 12h, nflverse > 3 days, Elo > 7 days)
- `"error"` — data missing or script-reported failure
- `"unknown"` — metadata document does not exist yet (script has never run)

---

## API Endpoint

### `GET /api/admin/sync_status`

Location: `routes/admin_routes.py` — requires `Depends(require_admin)`.

Returns a single JSON object with one key per health area. Each area has a `status` field (`ok` / `warn` / `error` / `unknown`). The endpoint never raises — individual failures set `status: "error"` for that area while others render normally.

```json
{
  "nfl_games": {
    "season": 2025,
    "current_week": 18,
    "games_total": 272,
    "games_with_results": 272,
    "last_game_date": "2026-01-05",
    "status": "ok"
  },
  "standings": {
    "season": 2025,
    "week": 18,
    "teams_count": 32,
    "status": "ok"
  },
  "predictions": {
    "season": 2025,
    "locked": 272,
    "unlocked": 0,
    "locked_through_week": 18,
    "coverage_pct": 100,
    "status": "ok"
  },
  "analytics_cache": {
    "last_rebuilt_at": 1748600000,
    "age_hours": 3.2,
    "status": "ok"
  },
  "elo": {
    "completed_at": 1748598000,
    "season": 2025,
    "week": 18,
    "games_processed": 12453,
    "status": "ok",
    "error": null
  },
  "nflverse": {
    "completed_at": 1748512000,
    "season": 2025,
    "datasets_synced": 8,
    "datasets_skipped": 2,
    "datasets_failed": 0,
    "status": "ok",
    "error": null
  }
}
```

### Derivation logic (endpoint-side)

"Active season" throughout means `get_active_season(all_games)` from `services.data_service` — the last season with actual game results (e.g. 2025, not 2026 which has all-zero standings).

**NFL Games** — filter `all_games` to active season, count rows and rows where `result` is not null / not -1000, find max week with result, find max `gameday`.

**Standings** — filter `standings_master` to active season, count distinct teams, find max `week`.

**Predictions** — call `get_game_predictions(active_season)`, count keys with `locked=True` and `locked=False`, find max week in locked keys (key format `W{ww}_{home}_{away}`), compute coverage %.

**Analytics Cache** — call `get_metadata("cache_control")`, read `last_update`, compute age in hours from `time.time()`. Stale threshold: 12h → `warn`.

**Elo** — call `get_metadata("sync_elo")`. If None → `unknown`. Check `completed_at` age: > 7 days → `warn`. Pass through script `status`/`error`.

**nflverse** — call `get_metadata("sync_nflverse")`. If None → `unknown`. Check `completed_at` age: > 3 days → `warn`. Pass through script `status`/`error`.

---

## Admin UI

### Status bar placement

In `templates/admin.html`, insert `<div id="sync-health-bar">` immediately before the tab button row (before the `<div>` containing the `.admin-tab-btn` buttons). The bar renders above the tabs and is always visible.

### Chip format

Six chips in a flex row, each with a colored dot and text:

```
● NFL Games   2025 · W18 · 272/272 · Jan 5
● Standings   2025 · W18 · 32 teams
● Elo         2025 · W18 · 12,453 games
● Predictions 2025 · locked thru W18 · 100%
● Cache       3.2h ago
● nflverse    2025 · 8 synced · 2d ago
```

Dot color:
- `ok` → `var(--accent-green)` (#6fbf73)
- `warn` → `var(--accent-yellow)` or `#f5a623`
- `error` → `var(--accent-red)` (#ff5050)
- `unknown` → `var(--text-secondary)` (grey)

On `error`, the chip tooltip (native `title` attribute) shows the error message.

### JavaScript (`static/js/admin_health.js`)

New standalone file — not added to `admin_main.js`. IIFE, `'use strict'`.

- On `DOMContentLoaded`: fetch `/api/admin/sync_status`, render chips into `#sync-health-bar`
- Auto-refresh: `setInterval` every 300,000ms (5 minutes)
- On fetch failure: show a single grey chip "Health check unavailable"
- Formats relative time (e.g. "3.2h ago", "2d ago") from Unix timestamps

### CSS

Add to `static/style.css` — a `.sync-health-bar` class: `display: flex; flex-wrap: wrap; gap: 0.5rem; padding: 0.5rem 0 1rem; border-bottom: 1px solid var(--glass-border); margin-bottom: 1rem;` and `.health-chip` for individual chips.

---

## Files Changed

| File | Change |
|---|---|
| `routes/admin_routes.py` | Add `GET /api/admin/sync_status` endpoint |
| `scripts/compute_elo.py` | Write `metadata/sync_elo` on completion |
| `scripts/sync_nflverse_data.py` | Write `metadata/sync_nflverse` on completion |
| `templates/admin.html` | Insert `#sync-health-bar` div + script tag for `admin_health.js` |
| `static/js/admin_health.js` | New file — fetch, render, auto-refresh |
| `static/style.css` | Add `.sync-health-bar` and `.health-chip` styles |
| `tests/test_sync_health.py` | New file — unit tests for endpoint logic |

---

## Testing

- Unit test `sync_status` endpoint with mocked `load_data()` and `get_metadata()` — assert each area returns correct `status` under normal, stale, and missing-data conditions
- Test `warn` threshold logic for cache (>12h), nflverse (>3d), Elo (>7d)
- Test `unknown` when metadata docs don't exist
- Test that individual area failures don't prevent other areas from returning data
