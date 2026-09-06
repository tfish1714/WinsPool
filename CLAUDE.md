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
python scripts/compute_elo.py --firestore                  # Same, plus push elo_history/{season} to Firestore — required for the Elo Ratings Explorer in prod; runs daily in prod as a step inside run_cron.py (below) — this is for a manual/out-of-band recompute
python scripts/daily_nfl_sync.py                          # Read rawdata/schedules/games.csv → compute standings → push nfl_games + nfl_standings to Firestore
python scripts/run_cron.py                                # winspool-sync-daily Cloud Run Job entrypoint: nflverse sync → compute_elo.py --firestore → daily_nfl_sync.py. Does NOT run cache_builder.py (predictions) — see Scheduled Jobs below
python scripts/sync_live_scores.py                         # winspool-live-scores Cloud Run Job entrypoint: authoritative re-sync + best-effort ESPN live-score overlay (is_live/clock/period)
python scripts/schedule_kickoffs.py                        # winspool-schedule-kickoffs Cloud Run Job entrypoint: enqueues per-game Cloud Tasks for sync/predict/ESPN-aware resimulate shortly before kickoff

# Local dev cache (USE_LOCAL_DATA=True)
python scripts/refresh_local_pkls.py                      # Rebuild ALL local pkl/json from Firestore (run after any Firestore change)
python scripts/cache_builder.py                           # Pre-build analytics pkl cache
python scripts/cache_builder.py --resimulate <game_ids>   # Scoped re-simulate for specific comma-separated game_ids, ESPN-injury-aware; used by winspool-schedule-kickoffs' close-to-kickoff task, not the daily full build

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
python scripts/walk_forward_calibrate_preseason_weights.py                     # Validate PRESEASON_ELO_WEIGHTS against real historical outcomes (5 cached walk-forward folds, no retraining)
python scripts/walk_forward_calibrate_preseason_weights.py --weights '{...}'   # Score a candidate weight set against the same folds
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
- `requirements-ml.txt` — TensorFlow, Keras, scikit-learn, XGBoost, scipy. Install where you train or run batch predictions: `pip install -r requirements.txt -r requirements-ml.txt`. **Excluded from the main web-service image** (`Dockerfile`) — the deployed `winspool` Cloud Run *service* reads stored predictions from Firestore and never loads a model, which is why the prediction services guard their imports behind `TF_AVAILABLE` / `SKLEARN_AVAILABLE`. It **is** installed by `Dockerfile.predict` (the `winspool-predict-daily` scheduled job that regenerates predictions — see Scheduled Jobs below), which is why that image is pinned to `python:3.11-slim` rather than the web service's `python:3.10-slim`: `keras==3.13.2` (required to load `models/nn_v14.keras` — Keras added a Dense-layer config field in 3.13 that older Keras can't deserialize) has no Python 3.10 wheel at all. TensorFlow itself is pinned because the `.keras` artifact format has changed across minor versions and `models/nn_v*.keras` were trained under 2.21.0.

### Key Environment Variables
```
USE_LOCAL_DATA=True         # True → .local_db/ pickles, False → Firestore
FIREBASE_CREDENTIALS=...    # Base64-encoded service account JSON
GEMINI_API_KEY=...          # For recap generation
SMTP_SERVER/PORT/USER/...   # Legacy email delivery (optional; Resend is now the primary path)
RESEND_API_KEY=...          # Resend API key — primary email provider (alerts, recaps, MFA codes)
FROM_EMAIL=...              # Resend sender address (default onboarding@resend.dev — no domain verified yet)
APP_BASE_URL=...            # Base URL for links embedded in outbound emails (default http://localhost:8000; prod uses the Cloud Run service URL)
ALERT_EMAIL=...             # Recipient for send_alert_email() job-failure alerts — set on all 4 scheduled Cloud Run Jobs
MAX_RETRIES=...             # Must match the job's own --max-retries, or alerting fails open (see Scheduled Jobs)
GCP_PROJECT/GCP_REGION=...           # Used by schedule_kickoffs.py to target the right Cloud Run Jobs Admin API
GCP_TASKS_QUEUE=...                  # Cloud Tasks queue name (winspool-kickoff-triggers)
GCP_SCHEDULER_SERVICE_ACCOUNT=...    # Service account schedule_kickoffs.py's enqueued tasks authenticate as
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
  email_service.py       # Resend-based email: weekly recaps, MFA codes, and send_alert_email() job-failure alerts
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

### Scheduled Jobs

Four Cloud Run Jobs run in production, orchestrated by Cloud Scheduler
(recurring) and Cloud Tasks (one-off, dynamically scheduled). All 4 are
live, in-season only (Aug/Sept 1 – Feb 10), in `us-east1` of the
`fishbone-wins-pool` GCP project. See
`docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md` for
the full design and `docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md`
for how the GCP infrastructure itself was provisioned (Task 9 — one-time setup, not
repeated by normal deploys).

| Job | Entrypoint | Trigger | What it does |
|---|---|---|---|
| `winspool-sync-daily` | `scripts/run_cron.py` | Daily 9:00 UTC (Aug–Jan `winspool-sync-daily-trigger`; Feb 1–10 `-trigger-feb`) | nflverse raw data sync → `compute_elo.py --firestore` → `daily_nfl_sync.py` (standings + `nfl_games`) |
| `winspool-predict-daily` | `scripts/cache_builder.py` | Daily 9:15 UTC (same Aug–Jan / Feb 1–10 split) | Regenerates predictions/analytics cache; the only job that installs `requirements-ml.txt` (`Dockerfile.predict`). Also now owns `preseason_predictions` (previously written only by a human running `predict_season.py` manually) — it refreshes the current/next season's team win projections daily and locks them once that season is complete (`locked=True`), so a later unscoped run won't silently overwrite a finished season's projections with the current model. Only runs the write for `year >= current_year` (or `--force`); a completed past season is skipped entirely. **Footgun**: manually re-running `predict_season.py` on any season writes a payload with no `locked` field at all, which silently clears the lock — it's now effectively a manual-override tool, not a routine one. |
| `winspool-live-scores` | `scripts/sync_live_scores.py` | Every 5 min (Sept–Jan `winspool-live-scores-trigger`; Feb 1–10 `-trigger-feb`) | Authoritative re-sync (narrow, last-7-days `nfl_games` window) + best-effort ESPN live-score overlay (`is_live`/`clock`/`period`) — **only overlays games nflverse's own `schedules` data source carries, which never includes preseason (`game_type="PRE"`) games at all**, confirmed 2026-08-21 |
| `winspool-schedule-kickoffs` | `scripts/schedule_kickoffs.py` | Weekly, Tuesdays 10:00 UTC, Sept–Jan (`winspool-schedule-kickoffs-trigger`) | Reads the upcoming week's real kickoff times, enqueues 3 Cloud Tasks per kickoff cluster against the Cloud Run Jobs Admin API: sync (kickoff−75min), routine predict (kickoff−60min), and an ESPN-aware `cache_builder.py --resimulate` re-run (kickoff−20min, `RESIMULATE_LEAD_MINUTES` in `schedule_kickoffs.py` — must fire after the routine predict run, not before, or the routine run's stale prediction overwrites the fresher one; still unvalidated against a real measured runtime) |

**Two Docker images**, split by dependency weight:
- `Dockerfile.sync` (`python:3.10-slim`, `requirements.txt` only) — used by `winspool-sync-daily`, `winspool-live-scores`, `winspool-schedule-kickoffs`.
- `Dockerfile.predict` (`python:3.11-slim`, `requirements.txt` + `requirements-ml.txt`) — used only by `winspool-predict-daily`. Built via `cloudbuild-{sync,predict}.yaml` (`gcloud builds submit --tag` can't target a non-default Dockerfile name, hence explicit Cloud Build configs). **Must be built from a real checkout, not a bare `git worktree`** — `models/*.keras`/`*.pkl` are gitignored, and a worktree only checks out tracked files, so a build run from one silently ships whatever stale/missing model files happen to exist there with no error (this shipped `nn_v1.keras` instead of `nn_v14.keras` once). `deploy/deploy.ps1` rebuilds and redeploys both images on every deploy run.

**Alerting is two-layer** (`services/email_service.py::send_alert_email()` + a Cloud Monitoring alert policy):
- In-script: each job's own exception handler calls `send_alert_email()`, which suppresses itself on any non-final Cloud Run retry attempt (comparing the auto-injected `CLOUD_RUN_TASK_ATTEMPT` against a `MAX_RETRIES` env var that must be kept in sync with the job's actual `--max-retries`, or it fails open and sends once per attempt — see the Deployment gotcha below) and prefixes the subject `[WinsPool Alert]`. Reply-To is set to the same alert address (not the From address — Resend can't send *as* an arbitrary address without a verified domain, and Gmail's DMARC policy would bounce a spoofed `@gmail.com` From anyway; Reply-To has no such restriction).
- Infra-level: a Cloud Monitoring alert policy watches `run.googleapis.com/job/completed_execution_count` with `result="failed"`, catching failures the script never gets to handle (OOM, bad image, crash before the exception handler runs).
- `scripts/job_runner.py` is `run_cron.py`'s shared step-runner — runs a list of steps as subprocesses, logs each, and fires one summary alert if any *required* step failed. `cache_builder.py` doesn't use it (single-process, not multi-step); it has its own `_run_with_alerting()` wrapper around `main()` that calls `send_alert_email()` directly on any unhandled exception.

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

Cloud Scheduler triggers for the nflverse sync + Elo recompute are live in
production — see **Scheduled Jobs** above for the actual deployed schedule
(this used to be a manual/unprovisioned step; it isn't anymore).

## Deployment

See `DEPLOY.md` for full instructions. Three options:
1. **Google Cloud Run** (recommended) — Docker-based, scales to zero
2. **Fly.io** — `flyctl deploy`
3. **PythonAnywhere** — WSGI adapter required

Use the `/deploy` Claude slash command (`.claude/commands/deploy.md`) to run the full pre-flight + deploy flow: git commit → tests → push → confirm → `.\deploy\deploy.ps1`.

`deploy.ps1` also rebuilds and redeploys `winspool-sync`/`winspool-predict`
(the 4 Cloud Run Jobs' images) via `cloudbuild-sync.yaml`/`cloudbuild-predict.yaml`
on every run — it does not repeat Task 9's one-time GCP setup (API enablement,
service account/IAM, the Cloud Tasks queue, Cloud Scheduler triggers); see
`docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md` Task 9 for that.

**Gotcha: `gcloud builds submit` must run from a checkout that actually has
the model binaries on disk, not a bare git worktree.** `models/*.keras` and
`models/*.pkl` are gitignored (large binaries) and `.gcloudignore` re-includes
them for `Dockerfile.predict`'s build — but a `git worktree` only checks out
*tracked* files, so a build run from a worktree silently ships whatever stale
or missing model files happen to exist there (discovered when a worktree-built
`winspool-predict` image had only `nn_v1.keras`, the first-ever model, instead
of the current `nn_v14.keras` — Cloud Run Jobs don't error on a wrong model
version, they just predict worse). Run `deploy.ps1` from the main checkout.

**Gotcha: each scheduled job needs a `MAX_RETRIES` env var matching its own
`--max-retries`.** `send_alert_email()` (`services/email_service.py`) only
actually sends on the job's final retry attempt, comparing Cloud Run's
auto-injected `CLOUD_RUN_TASK_ATTEMPT` against this env var — Cloud Run does
NOT auto-inject the configured max-retries itself, so without `MAX_RETRIES`
set, every attempt sends its own alert (4 emails per failure at the default
`maxRetries=3`, i.e. 4 total attempts). All 4 jobs currently have
`MAX_RETRIES=3` to match their (default, never overridden) `--max-retries=3`.
If you ever change a job's `--max-retries`, update its `MAX_RETRIES` env var
to match, or alerting silently reverts to "fail open" (always sends).

See **Scheduled Jobs** (under Architecture, above) for the full set of
Cloud Scheduler/Cloud Tasks triggers running in production.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
