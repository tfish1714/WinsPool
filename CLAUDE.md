# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**WinsPool** is a FastAPI web app for managing NFL team draft pools. Players draft 3 NFL teams; rankings are determined by cumulative regular-season wins. Features AI-powered recaps (Google Gemini), live WebSocket draft rooms, and ML-based win predictions.

## Commands

### Development Server
```bash
uvicorn main:app --reload
```

### Production
```bash
python main.py  # Uses PORT env var (default 8000)
```

### Docker
```bash
docker build -t winspool .
docker run -p 8000:8080 -e USE_LOCAL_DATA=True -e ROOM_CODE=test winspool
```

### Scripts (run individually as needed)
```bash
# Data sync
python scripts/sync_nflverse_data.py                      # Update rawdata/ from nflverse (current season)
python scripts/sync_nflverse_data.py --seasons 2020 2025  # Full historical rebuild
python scripts/sync_nflverse_data.py --include-pbp        # Also fetch large play-by-play files
python scripts/daily_nfl_sync.py                          # Push game results → Firestore (web app)
python scripts/run_cron.py                                # Run full pipeline (sync + firestore + cache)

# Cache
python scripts/cache_builder.py        # Pre-build pickle cache

# ML
python scripts/train_nn_model.py       # Train ML model (auto-increments version in registry)
python scripts/train_nn_model.py --version v3   # Train and save as specific version
python scripts/weekly_model_eval.py --season 2025 --week 14    # Evaluate one week
python scripts/weekly_model_eval.py --season 2025 --week 1 17  # Evaluate full season range
python scripts/predict_2026.py         # Generate season predictions
```

### Tests
```bash
pytest tests/
pytest tests/ --cov=services --cov=routes
```

## Architecture

### Stack
- **Backend**: Python + FastAPI + Uvicorn
- **Database**: Google Cloud Firestore (prod) / local pickle files (dev)
- **Frontend**: Vanilla JS (ES6 modules) + Jinja2 templates + CSS (glassmorphism)
- **Real-time**: WebSockets (live draft room)
- **ML**: TensorFlow/Keras (`models/nn_v1.keras`), Pandas, NumPy
- **AI**: Google Gemini (weekly recaps)

### Key Environment Variables
```
USE_LOCAL_DATA=True         # True → .local_db/ pickles, False → Firestore
FIREBASE_CREDENTIALS=...    # Base64-encoded service account JSON
ROOM_CODE=...               # Draft room passcode
GEMINI_API_KEY=...          # For recap generation
SMTP_SERVER/PORT/USER/...   # Email delivery (optional)
PORT=8000
```

### Module Layout

```
main.py                  # App entry, router registration, Jinja2 globals
routes/
  api_routes.py          # 40+ JSON API endpoints (/api/*)
  standings_routes.py    # Standings & leaderboard pages
  draft_routes.py        # Live draft room
  history_routes.py      # Historical data views
services/
  data_service.py        # 3-tier cache: memory → pickle → Firestore
  db_service.py          # Firestore/pickle persistence + auth (bcrypt)
  analysis_service.py    # Standings calc, win matrices, schedules
  draft_service.py       # Draft state, pick validation, WebSocket sync
  prediction_service.py  # Win projections (calls NN model)
  nn_prediction_service.py / nn_feature_engine.py / nn_projection_engine.py
  recap_service.py       # Gemini-powered weekly summaries
  ai_service.py          # Gemini API wrapper
  cache_service.py       # In-memory cache with TTL
  email_service.py       # SMTP recap distribution
  live_score_service.py  # Live game score updates
templates/               # Jinja2 HTML (server-rendered)
static/
  style.css
  js/
    main.js              # Page init, event handling
    ui_renderer.js       # Dynamic DOM rendering
    api.js               # Fetch wrapper
    websocket_service.js # WebSocket client for live draft
    admin_main.js        # Admin dashboard
    auth_service.js      # Client-side auth
scripts/                 # CLI tools for data sync, ML training, cache building
models/                  # nn_v1.keras + nn_v1_scaler.pkl
rawdata/                 # NFL raw data (NOT committed)
.local_db/               # Local pickle cache (NOT committed)
```

### Data Flow & Caching

`data_service.load_data()` implements a **3-tier cache**:
1. **In-memory** (`cache_service.py`, TTL-based)
2. **Pickle files** (`.local_db/*.pkl`) — used when `USE_LOCAL_DATA=True`
3. **Firestore** — primary source of truth in production

Firestore collections: `standings`, `games`, `players`, `draft_order`, `draft_results`, `draft_rules`, `weekly_recaps`.

### Auth

Handled in `db_service.py`: bcrypt (12 rounds) with legacy SHA-256 migration support. Role-based access (admin/player). No external auth library — session state stored in the DB.

### Real-Time Draft

- **Backend**: FastAPI WebSocket endpoint in `draft_routes.py`, state managed via `_CACHED_DRAFT_STATE` in `draft_service.py`
- **Frontend**: `js/websocket_service.js` connects and handles state updates
- Admin overrides (force/undo picks) are available via `/api/admin/*` endpoints

### ML Predictions

- Model registry: `models/model_registry.json` — tracks all trained versions with metrics
- Active model resolved via `NNPredictionService.load_model("latest")` or `load_model("best")`
- When retraining, `train_nn_model.py` auto-increments version and writes to the registry
- Feature pipeline: `nn_feature_engine.py` → `nn_prediction_service.py` → `prediction_service.py`
- Weekly accuracy tracked in `reports/nn_weekly_accuracy.csv` via `scripts/weekly_model_eval.py`
- Raw data for training lives in `rawdata/` (not committed)
- `NNProjectionEngine` (`nn_projection_engine.py`) wraps the NN with a power-rating blend (40% NN, 60% power rating) for season projections

## Raw Data Sources

Two sources, both synced by `scripts/sync_nflverse_data.py`:

**[nflverse-data](https://github.com/nflverse/nflverse-data/releases)** — actively maintained, nightly updates during season:
| Release tag | Local path | Update frequency |
|---|---|---|
| `schedules` | `rawdata/schedules/games.csv` | Every 5 min during season |
| `stats_team` | `rawdata/stats_team/stats_team_{reg,week}_{year}.csv` | Nightly |
| `rosters` | `rawdata/rosters/roster_{year}.csv` | Daily 7 AM UTC |
| `pfr_advanced` | `rawdata/pfr_advstats/advstats_week_*_{year}.csv` | Daily |
| `ftn_charting` | `rawdata/ftn_charting/ftn_charting_{year}.csv` (2022+) | 4x daily |
| `depth_charts` | `rawdata/depth_charts/depth_charts_{year}.csv` | Daily |
| `snap_counts` | `rawdata/snap_counts/snap_counts_{year}.csv` | 4x daily |
| `injuries` | `rawdata/injuries/injuries_{year}.csv` | Daily |
| `weekly_rosters` | `rawdata/weekly_rosters/roster_weekly_{year}.csv` | Daily |
| `pbp` | `rawdata/pbp/play_by_play_{year}.csv` | Nightly (~100MB/year, opt-in) |

**[nflscraPy](https://github.com/blnkpagelabs/nflscraPy/releases)** — last updated Jan 2024, historical only:
| Release tag | Local path | Notes |
|---|---|---|
| `SeasonRoster` | `rawdata/SeasonRoster-{year}.csv` | Has PFR Approximate Value (not in nflverse) |

Not synced (redundant or unmaintained): `FiveThirtyEight.csv` (FTE, stops 2022), `Metadata-*.csv`, `Scoring-*.csv`, `ExpectedPoints-*.csv`, `Stats-*.csv` (box stats computed from nflverse weekly stats instead).

### Files safe to delete (~465 MB)
- `rawdata/dont use/` — deprecated old PBP format
- `rawdata/pbp_participation/` — 305 MB, never used
- `rawdata/players_components/` — unused mapping tables
- `rawdata/teams/` — duplicate of `rawdata/teams_colors_logos.csv`
- `rawdata/Seasons-2024(1)` and `rawdata/Seasons-2024(2)` — duplicate files

### Recommended Cloud Scheduler jobs
```
# Weekly nflverse raw data sync — Tuesdays 9 AM UTC (after MNF)
0 9 * * 2   →  python scripts/sync_nflverse_data.py

# Nightly Firestore sync + cache rebuild — 2 AM ET daily
0 7 * * *   →  python scripts/run_cron.py
```

## Deployment

See `DEPLOY.md` for full instructions. Three options:
1. **Google Cloud Run** (recommended) — Docker-based, scales to zero
2. **Fly.io** — `flyctl deploy`
3. **PythonAnywhere** — WSGI adapter required

Cloud Scheduler is used to run `scripts/daily_nfl_sync.py` on a schedule in production.
