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
docker run -p 8000:8080 -e USE_LOCAL_DATA=True winspool
```

### Scripts (run individually as needed)
```bash
# Data sync
python scripts/sync_nflverse_data.py                      # Update rawdata/ from nflverse (current season)
python scripts/sync_nflverse_data.py --seasons 2020 2025  # Full historical rebuild
python scripts/sync_nflverse_data.py --include-pbp        # Also fetch large play-by-play files
python scripts/compute_elo.py                             # Recompute rawdata/elo_computed.csv + local elo_history cache from scratch (run after rawdata sync)
python scripts/compute_elo.py --firestore                  # Same, plus push elo_history/{season} to Firestore — required for the Elo Ratings Explorer in prod; NOT called by run_cron.py, still a manual step
python scripts/daily_nfl_sync.py                          # Read rawdata/schedules/games.csv → compute standings → push nfl_games + nfl_standings to Firestore
python scripts/run_cron.py                                # Run full pipeline (sync + firestore + cache) — does NOT include compute_elo.py

# Local dev cache (USE_LOCAL_DATA=True)
python scripts/refresh_local_pkls.py                      # Rebuild ALL local pkl/json from Firestore (run after any Firestore change)
python scripts/cache_builder.py                           # Pre-build analytics pkl cache

# ML predictions
python scripts/backfill_schedule_predictions.py                        # Backfill predictions local only
python scripts/backfill_schedule_predictions.py --firestore            # Backfill + push to Firestore
python scripts/backfill_schedule_predictions.py --seasons 2020 2026   # Specific season range
python scripts/backfill_schedule_predictions.py --force                # Overwrite locked predictions (after retraining)
python scripts/predict_season.py --season 2026                        # Generate season win projections → Firestore

# ML model training
python scripts/train_nn_model.py       # Train ML model (auto-increments version in registry)
python scripts/train_nn_model.py --version v3   # Train and save as specific version
python scripts/weekly_model_eval.py --season 2025 --week 14    # Evaluate ensemble accuracy for one week
python scripts/weekly_model_eval.py --season 2025 --week 1 18  # Evaluate full season range (NN+XGB+LR ensemble)

# Consensus benchmark
python scripts/seed_consensus.py --season 2026 --firestore    # Seed analyst consensus from data/consensus_2026.csv
python scripts/migrate_consensus.py --firestore               # One-shot: move 2017-2025 consensus out of preseason_predictions
python scripts/refresh_preseason.py --season 2026             # Full preseason refresh + freshness preflight + projection diff
python scripts/refresh_preseason.py --season 2026 --check-freshness   # Preflight only

# Diagnostics
python scripts/rank_position_groups.py --season 2026                  # Rank all 32 teams (CSV) on each preseason Elo-boost input dimension
python scripts/rank_position_groups.py --season 2026 --team ATL       # One team's dimension breakdown, sorted by weighted contribution
python scripts/rank_position_groups.py --season 2026 --dim dl_perf    # All 32 teams ranked on a single dimension
```

### Tests
```bash
pytest tests/
pytest tests/ --cov=services --cov=routes
```

### Frontend Testing

`pytest` covers routes/services only — there is no automated JS test suite. Any
UI-visible change (new page, new nav entry, CSS, layout) must be manually
verified in-browser, and **that verification must include a mobile viewport**,
not just desktop. This app has bitten itself on this before: `static/js/main.js`'s
`updateNav()` renders the desktop nav (top rail + "More" dropdown) entirely
client-side, but the mobile nav drawer (`templates/base.html`'s
`.nav-drawer__links`) is separate, hardcoded, server-rendered markup —
`responsive.js` only toggles the drawer open/closed, it doesn't populate its
links. **A nav link added to one does not appear in the other.** When adding or
changing a nav destination, update both `updateNav()`'s `moreLinks`/`primaryLinks`
arrays *and* the matching `<a>` in `base.html`'s drawer, and check both a desktop
width and a narrow (~390px) mobile width before calling it done.

## Architecture

### Stack
- **Backend**: Python + FastAPI + Uvicorn
- **Database**: Google Cloud Firestore (prod) / local pickle files (dev)
- **Frontend**: Vanilla JS (ES6 modules) + Jinja2 templates + CSS (glassmorphism)
- **Real-time**: WebSockets (live draft room)
- **ML**: TensorFlow/Keras (NN), XGBoost, scikit-learn (LR) — blended ensemble (45% NN + 20% XGB + 35% LR)
- **AI**: Google Gemini (weekly recaps)

### Dependencies
- `requirements.txt` — web app only; this is what the Dockerfile installs.
- `requirements-ml.txt` — TensorFlow, scikit-learn, XGBoost, scipy. Install where you train or run batch predictions: `pip install -r requirements.txt -r requirements-ml.txt`. **Deliberately excluded from the deployed image** — Cloud Run reads stored predictions from Firestore and never loads a model, which is why the prediction services guard their imports behind `TF_AVAILABLE` / `SKLEARN_AVAILABLE`. TensorFlow is pinned because the `.keras` artifact format has changed across minor versions and `models/nn_v*.keras` were trained under 2.21.0.

### Key Environment Variables
```
USE_LOCAL_DATA=True         # True → .local_db/ pickles, False → Firestore
FIREBASE_CREDENTIALS=...    # Base64-encoded service account JSON
GEMINI_API_KEY=...          # For recap generation
SMTP_SERVER/PORT/USER/...   # Email delivery (optional)
VAPID_PUBLIC_KEY=...        # Web Push VAPID public key (base64url)
VAPID_PRIVATE_KEY=...       # Web Push VAPID private key (base64url)
VAPID_CLAIMS_EMAIL=...      # Contact email included in VAPID JWT claims
PORT=8000
```

### Module Layout

```
main.py                  # App entry, router registration, Jinja2 globals
routes/
  api_routes.py          # 40+ JSON API endpoints (/api/*)
  auth_routes.py         # Login, logout, profile, password setup (/api/auth/*, /api/profile)
  standings_routes.py    # Standings & leaderboard pages
  draft_routes.py        # Live draft room
  history_routes.py      # Historical data views
  prediction_routes.py   # ML prediction endpoints (/api/predictions/*)
  mock_draft_routes.py   # Solo mock draft: page route + /api/mock-draft/* (setup, pick, results)
services/
  data_service.py        # 3-tier cache: memory → pickle → Firestore
  db_service.py          # Firestore/pickle persistence + auth (bcrypt)
  analysis_service.py    # Standings calc, win matrices, schedules
  draft_service.py       # Draft state, pick validation, WebSocket sync
  mock_draft_service.py  # Mock draft: pick sequencing, bot picks, end-of-draft ranking (stateless, no DB writes)
  prediction_service.py  # Win projections (calls NN model)
  nn_prediction_service.py / nn_feature_engine.py / nn_projection_engine.py
  recap_service.py       # Gemini-powered weekly summaries
  ai_service.py          # Gemini API wrapper
  cache_service.py       # In-memory cache with TTL
  email_service.py       # SMTP recap distribution
  live_score_service.py  # Live game score updates
  push_service.py        # Web Push notifications (VAPID)
  chat_service.py        # Draft room chat message persistence
templates/               # Jinja2 HTML (server-rendered)
static/
  style.css              # Bump ?v=N on the <link> in base.html whenever changing CSS to bust browser cache
  js/
    main.js              # Page init, nav rendering (updateNav), event handling; uses stale-while-revalidate via localStorage
    ui_renderer.js       # Dynamic DOM rendering
    api.js               # Fetch wrapper
    websocket_service.js # WebSocket client for live draft
    admin_main.js        # Admin dashboard
    auth_service.js      # Client-side auth
    responsive.js        # Mobile drawer controller (non-module IIFE, loaded after main.js)
    chat.js              # Draft room chat overlay
    mock_draft.js        # Standalone mock draft page logic — does NOT import main.js/websocket_service.js/auth_service.js
scripts/                 # CLI tools for data sync, ML training, cache building
models/                  # nn_v{N}.keras + scaler, xgb_v{N}.json + scaler, lr_v{N}.pkl + scaler; *_registry.json per model type
rawdata/                 # NFL raw data (NOT committed)
docs/                    # Architecture and model documentation (prediction_model.md, etc.)
.local_db/               # Local pickle cache (NOT committed)
```

### Data Flow & Caching

`data_service.load_data()` implements a **3-tier cache**:
1. **In-memory** (`cache_service.py`, TTL-based)
2. **Pickle files** (`.local_db/*.pkl`) — used when `USE_LOCAL_DATA=True`
3. **Firestore** — primary source of truth in production

**Firestore is always the source of truth.** All writes go to Firestore first. Local pkl files are a read-only mirror built from Firestore via `scripts/refresh_local_pkls.py`.

Firestore collections and their local equivalents:

| Firestore collection | Local pkl / JSON | Notes |
|---|---|---|
| `nfl_games` | `.local_db/nfl_games.pkl` + `nfl_games_{year}.pkl` | Year slices written automatically |
| `nfl_standings` | `.local_db/nfl_standings.pkl` + `nfl_standings_{year}.pkl` | |
| `players` | `.local_db/players.pkl` | |
| `draft_results` | `.local_db/draft_results.pkl` | |
| `draft_order` | `.local_db/draft_order.pkl` | |
| `draft_order_rules` | `.local_db/draft_order_rules.pkl` | |
| `nfl_teams` | `.local_db/nfl_teams.pkl` | |
| `preseason_predictions` | `.local_db/preseason_predictions.pkl` + `_{year}.pkl` | Model output only |
| `consensus_projections` | `.local_db/consensus_projections.pkl` + `_{year}.pkl` | Analyst win projections; `preseason_predictions` is model output only |
| `game_predictions` | `.local_db/game_predictions_{year}.json` | JSON, not pkl; one doc per season |
| `analytics_cache` | `.local_db/analytics/{analytic}_{year}_{week}.json` | JSON |
| `elo_history` | `.local_db/elo_history_{season}.json` | JSON, one doc per season; written by `scripts/compute_elo.py --firestore` (not the normal service-write path — see gotcha below); powers the admin Elo Ratings Explorer |
| `config` | *(no local pkl — always reads Firestore)* | Single doc `config/settings`; stores `draft_active` flag and app-level settings |

`.local_db/backup_preseason_consensus_*.json` holds the pre-migration backup of
the 2017–2025 consensus rows deleted from `preseason_predictions` on
2026-08-12 (see `scripts/migrate_consensus.py`) — it is the only copy of that
data and should not be deleted.

**Rules for any new Firestore collection or data store:**
1. Write to Firestore first (or with `--firestore` flag in scripts).
2. Add the collection to `refresh_local_pkls.py` so local dev stays in sync.
3. The local file format must match exactly what `cache_service.py` / `data_service.py` reads — no format divergence between local and Firestore paths.
4. **Services and routes must never read from `rawdata/` CSVs.** They read exclusively from Firestore/pkl via `load_data()`. Scripts are allowed to read from `rawdata/` — sync scripts push game/standings data to Firestore; ML feature engineering scripts (`nn_feature_engine.py`, `predict_season.py`, `generate_weekly_predictions.py`, etc.) read rawdata directly for model training and batch prediction since that data is too large/ML-specific for Firestore.

**Local dev workflow after any data change:**
```bash
# If you changed data in Firestore (or ran a backfill with --firestore):
python scripts/refresh_local_pkls.py

# If you need to push rawdata/ changes to Firestore first:
python scripts/daily_nfl_sync.py && python scripts/refresh_local_pkls.py
```

**Gotcha: any script that writes to Firestore must force `USE_LOCAL_DATA=False`.**
`services/db_service.py::get_db()` returns `None` whenever `USE_LOCAL_DATA` is
true in the environment — regardless of any `use_local=False` argument passed
deeper in the call stack. A normal local dev `.env` has `USE_LOCAL_DATA=True`,
so a new script that pushes to Firestore must set
`os.environ["USE_LOCAL_DATA"] = "False"` near the top, before importing
anything from `services.db_service` (see `refresh_local_pkls.py`,
`cache_builder.py`, `run_predictions.py`, `smart_refresh.py`, and
`compute_elo.py --firestore` for the established pattern). Historically this
failed silently rather than raising — a bare `except Exception` around the
Firestore call would catch `None.collection(...)`'s `AttributeError` and only
`logger.error` it, so the script would print a false success message. That's
exactly what left prod's `elo_history` collection empty for days: the script
had been run without this override and reported success anyway.

### Auth

Handled in `db_service.py`: bcrypt (12 rounds) with legacy SHA-256 migration support. Role-based access (admin/player). No external auth library — session state stored in the DB.

Password login (`POST /api/login`) issues a signed JWT (`session_service.create_token`) returned in the response body and set as an `httpOnly` `session_token` cookie. HTTP routes validate it via `require_auth`/`require_admin` (`Authorization: Bearer` header or the cookie); `services/session_service.py::get_is_admin` is a third, non-raising variant for endpoints that must also work for anonymous callers (used by the mock draft). There is no separate room-code/passcode mechanism — that (`ROOM_CODE`, WebSocket `verify_code`/`request_signin`) was removed as dead code, since the frontend never used it (see Real-Time Draft below for how the live draft's WebSocket authenticates instead — it does **not** yet reuse this JWT; known follow-up).

### Real-Time Draft

- **Backend**: FastAPI WebSocket endpoint in `draft_routes.py`, state managed via `_CACHED_DRAFT_STATE` in `draft_service.py`
- **Frontend**: `js/websocket_service.js` connects and handles state updates
- Admin overrides (force/undo picks) are available via `/api/admin/*` endpoints
- **Auth handshake**: the client sends `{action: "reauthenticate", playerId}` on connect (`main.js::onWsOpen`); the server accepts `socket_player_id` from that client-supplied `playerId` after checking the player exists and has a `password_hash` set (`get_player_by_id`). **Known gap**: this never actually verifies the connecting browser's `session_token` JWT (issued by the real password check at `/api/login`) — it trusts the client-asserted ID once *any* password exists for that account. Closing this by decoding the `session_token` cookie already present on the WS handshake (browsers attach cookies to WS upgrades automatically) and deriving `socket_player_id` from the JWT's `sub` claim instead is a scoped, not-yet-implemented follow-up.
- **Projection gating**: `preseason_predictions` (team win projections) must never reach a non-admin socket. `ConnectionManager` (in `draft_routes.py`) tracks `admin_sockets` per connection (set via `set_admin()` once a socket's `reauthenticate` resolves) and `broadcast()` strips that field (`draft_service.strip_admin_only_fields`) for every non-admin recipient — enforced server-side on every `"state"` send path (initial connect, `switch_season`, and the post-`reauthenticate` broadcast), not left to client-side rendering.

### Mock Draft (Practice)

A standalone, **login-free** solo draft simulator at `/mock-draft` — a shareable
link for players to try the app before the real draft, with zero setup.

- **Page**: `templates/mock_draft.html` deliberately does **not** extend
  `base.html` (which would pull in `main.js`'s login wall) and loads only
  `static/js/mock_draft.js` — no `main.js`/`websocket_service.js`/`auth_service.js`.
  It mirrors the real draft room's layout (clock card, pick queue, teams grid
  with select-then-confirm, running portfolio, collapsible full board) minus
  chat and minus a real countdown (practice has no timer).
- **Backend**: `services/mock_draft_service.py` (pure, stateless — no DB
  writes) + `routes/mock_draft_routes.py` (`GET /setup`, `POST /pick`,
  `POST /results`).
- **Pick order** comes from `draft_order_rules` (the real draft's own
  pick-sequence pattern), using whichever season currently has rows —
  deliberately decoupled from whichever season supplies team projections, so
  the mock draft survives an admin resetting the real season's draft order.
- **Bots** pick using the same blended model/consensus projections as the
  real draft (`get_season_projection_legacy_shape`), weighted toward
  higher-projected teams via `_weighted_rank_pick`, with a guaranteed minimum
  of `MIN_WILDCARDS_PER_DRAFT` (2) uniform-random "wildcard" picks across a
  full draft's bot picks — enforced by a stateless pity mechanic in
  `bot_pick()` (the caller passes running `wildcardsSoFar`/`botPicksRemaining`
  counters each call; nothing is stored server-side).
- **Projections are admin-gated** exactly like the live draft: `GET /setup`
  only includes `projections` for a valid admin session (`get_is_admin`);
  `POST /results` rankings include `totalProjectedWins` only for admins, and
  carry a `graded: false` flag (never a fabricated rank) when a season has no
  projection data at all.

### ML Predictions

Three models are blended (45% NN + 20% XGB + 35% LR) for every game prediction:
- **NN** — `models/nn_v{N}.keras` + scaler; registry: `models/model_registry.json`
- **XGB** — `models/xgb_v{N}.json` + scaler; registry: `models/xgb_registry.json`
- **LR** — `models/lr_v{N}.pkl` + scaler; registry: `models/lr_registry.json`

Each registry tracks all versions and designates `latest` and `best`. When retraining, the train scripts auto-increment the version.

- Feature pipeline: `nn_feature_engine.py` (26 features) → `nn_prediction_service.py` / `xgb_prediction_service.py` / `lr_prediction_service.py` → blended in `prediction_service.py` and `backfill_schedule_predictions.py`
- Weekly ensemble accuracy tracked in `reports/nn_weekly_accuracy.csv` via `scripts/weekly_model_eval.py`
- Elo ratings computed by `scripts/compute_elo.py` → `rawdata/elo_computed.csv` (run after each rawdata sync)
- `NNProjectionEngine` (`nn_projection_engine.py`) produces season projections by running the **same** per-game ensemble forward through the schedule — there is no separate season-wins model, and no power-rating blend (`_batch_predict` is the plain 45/20/35 ensemble). `simulate_season()` seeds each team's state (Elo + 4 EPA dims + margin) from preseason player profiles plus a profile-composite Elo boost (`PRESEASON_ELO_BOOST_MAX`, ±200), tiles it across N Monte Carlo trials, then walks weeks in order: batch-predict every game across every trial, convert probability to an implied margin, sample `Normal(implied, MC_MARGIN_STD)`, increment wins on the margin sign, and **update Elo/EPA state in place** so later weeks see the simulated record. Win distributions across trials give `mean_wins`/`median`/`std_dev`/`p5`/`p25`/`p75`/`p95`. Preseason team state comes from `compute_preseason_player_profiles()` (`nn_feature_engine.py`) — a player-level blend of up to 3 prior seasons per position group (QB/WR/TE/RB, OL, DL, LB, CB/S), weighted by recency × reliability (share of a full season's volume), with a season excluded entirely (not just down-weighted) below a minimum-sample threshold so a single noisy small-sample season can't dominate a player's rate.
- `scripts/walk_forward_validate.py` scores the ensemble out-of-sample (train on seasons strictly before the fold, predict the fold) → `reports/walk_forward_validation.csv` — a diagnostic for "is the model actually better than consensus," not a production path. It can't exercise the preseason-profile branch above for most historical folds (see `docs/prediction_model.md`'s Season Win Projection section); `scripts/walk_forward_diagnose_preseason_path.py` forces that branch for the 2025 fold specifically as a one-off check.
- See `docs/prediction_model.md` for a full description of all 26 features, model architectures, and all three prediction paths (in-season, single-game preseason, season-simulation)

## Raw Data Sources

All rawdata comes from nflverse, synced by `scripts/sync_nflverse_data.py`. There is no longer any dependency on LeeSharpe/nfldata — `daily_nfl_sync.py` reads from local rawdata only.

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

Also required (not from nflverse, computed locally):
| Script | Output | Notes |
|---|---|---|
| `scripts/compute_elo.py` | `rawdata/elo_computed.csv` | Run after each rawdata sync; requires `rawdata/schedules/games.csv` |
| `scripts/scrape_quarter_scores.py` | `rawdata/quarter_scores.csv` | Optional; enables quarter-by-quarter Elo updates |

Not synced (redundant or unmaintained): `FiveThirtyEight.csv` (FTE, stops 2022), `Metadata-*.csv`, `Scoring-*.csv`, `ExpectedPoints-*.csv`, `Stats-*.csv` (box stats computed from nflverse weekly stats instead), `SeasonRoster-*.csv` (nflscraPy, superseded by snap_counts + rosters).

### Roster talent features
- `roster_talent_delta` — performance-based team grade from `stats_team_week_*.csv` (2020+): cumulative offense + defense composite z-scored within each week.
- `trench_dominance_metric` — composite of OL snap quality (snap counts × age multiplier, 2012+) and DL performance (sacks×6 + qb_hits×1 + tfl×1 from `stats_team_week_*.csv`, 2020+), z-scored per season so both components contribute equally. In the **preseason path**, this is overridden using the actual target-season roster file (`roster_{year}.csv`) joined to the prior season's individual player advstats (`advstats_week_def_*.csv`).

### Files safe to delete
- `rawdata/dont use/` — deprecated old PBP format (~157 MB)
- `rawdata/pbp_participation/` — 305 MB, never used
- `rawdata/players_components/` — unused mapping tables
- `rawdata/teams/` — duplicate of `rawdata/teams_colors_logos.csv`
- `rawdata/Seasons-2024(1)` and `rawdata/Seasons-2024(2)` — duplicate files
- `rawdata/SeasonRoster-*.csv` — superseded; feature engine now uses snap_counts

### Recommended Cloud Scheduler jobs
```
# Weekly nflverse raw data sync + Elo recompute — Tuesdays 9 AM UTC (after MNF)
# NOTE: not currently provisioned — compute_elo.py has been a fully manual
# step (run_cron.py below does not call it). The --firestore flag is required
# here or this job would do nothing prod-visible; see the USE_LOCAL_DATA
# gotcha above before wiring this up.
0 9 * * 2   →  python scripts/sync_nflverse_data.py && python scripts/compute_elo.py --firestore

# Nightly Firestore sync + cache rebuild — 2 AM ET daily
0 7 * * *   →  python scripts/run_cron.py
```

## Deployment

See `DEPLOY.md` for full instructions. Three options:
1. **Google Cloud Run** (recommended) — Docker-based, scales to zero
2. **Fly.io** — `flyctl deploy`
3. **PythonAnywhere** — WSGI adapter required

Use the `/deploy` Claude slash command (`.claude/commands/deploy.md`) to run the full pre-flight + deploy flow: git commit → tests → push → confirm → `.\deploy\deploy.ps1`.

Cloud Scheduler is used to run `scripts/daily_nfl_sync.py` on a schedule in production.
