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
python scripts/sync_live_scores.py                         # winspool-live-scores Cloud Run Job entrypoint: authoritative re-sync + best-effort ESPN live-score overlay (is_live/clock/period). Fast-exits (exit 0, no Firestore/ESPN/subprocess work) outside an NFL game window -- see is_live_score_window_active()
python scripts/sync_live_scores.py --force                 # Skip the game-window check (dev, testing, ad-hoc runs)
python scripts/schedule_kickoffs.py                        # winspool-schedule-kickoffs Cloud Run Job entrypoint: enqueues per-game Cloud Tasks for sync/predict/ESPN-aware resimulate shortly before kickoff, also enqueues live-score ticks for winspool-live-scores, then runs the weekly betting-edge alert as a step (see below)
python scripts/betting_edge_alert_weekly.py                # Composes scan_angles + screen_games into a weekly validated-angle-match + raw-edge-outlier summary, emails BETTING_ALERT_EMAIL if either tier is non-empty. Read-only, never touches the NN+XGB+LR ensemble. Not its own Cloud Run Job -- run as a subprocess step of winspool-schedule-kickoffs (schedule_kickoffs.py::_run_betting_alert()).

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
python scripts/weekly_model_eval.py --season 2025 --week 14 --firestore  # Also push to the nn_weekly_accuracy store (admin ML Accuracy tab)

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
pytest tests/                              # Unit suite (routes/services). A bare `pytest` also runs exactly this — pytest.ini pins testpaths = tests
pytest tests/ -n auto                      # Same suite, parallelized across CPU cores (needs requirements-dev.txt: pytest-xdist) -- the serial run takes several minutes, mostly TF/keras/sklearn import overhead per test file
pytest tests/ --cov=services --cov=routes
pytest tests_e2e/ -v                       # Playwright browser e2e suite (explicit path required — not collected by a bare `pytest`)
```

**Tests never touch your real `.local_db/`.** `tests/conftest.py` copies
`<repo>/.local_db` (or makes an empty directory if there is none) into a
per-session temp dir (one per xdist worker) and sets
`WINSPOOL_LOCAL_DB_REDIRECT` to it; every service/route write path resolves the
folder through `services/local_paths.py::local_db_dir()`, which honors the
redirect only while the cwd-relative `.local_db` is the real repo folder
(tests that `chdir(tmp_path)` and build their own `.local_db` are unaffected;
with no redirect set it is exactly `pathlib.Path(".local_db")`). The copy is
deleted at session end, and the session controller fingerprints the real folder
(path/size/mtime of every file, plus whether the directory exists) before and
after the run and **fails the run** if anything changed, listing the paths.
Set `WINSPOOL_ALLOW_LOCAL_DB_WRITES=1` to downgrade that to a warning for an
intentional dev flow. You no longer need to re-run `refresh_local_pkls.py`
after running the suite. New code that writes under `.local_db` must use
`local_db_dir()`, not the literal `pathlib.Path(".local_db")`.

`pytest tests_e2e/ -v` needs `requirements-dev.txt` installed plus a one-time
`playwright install chromium`, and the `E2E_TEST_PLAYER_IDS` /
`E2E_TEST_PLAYER_PASSWORD` env vars seeded by
`scripts/seed_e2e_test_players.py` (the suite skips itself without them).
**Also run it as a deploy pre-flight gate** — see `.claude/commands/deploy.md`
for the full pre-flight sequence (that file is untracked; this line is the
durable record of the gate).

### Frontend Testing

There is still no automated JS *unit* test suite, but `tests_e2e/` (Playwright)
now drives the real app in a real browser at both desktop and mobile viewports,
so UI-visible behavior is no longer covered by manual checking alone. Manual
in-browser verification of any UI-visible change (new page, new nav entry, CSS,
layout) is still good practice — and **that verification must include a mobile
viewport**, not just desktop — it just isn't the only line of defense anymore.

This app has bitten itself on this before: `static/js/main.js`'s `updateNav()`
renders the desktop nav (top rail + "More" dropdown) entirely client-side, but
the mobile nav drawer (`templates/base.html`'s `.nav-drawer__links`) is
separate, hardcoded, server-rendered markup — `responsive.js` only toggles the
drawer open/closed, it doesn't populate its links. **A nav link added to one
does not appear in the other.** When adding or changing a nav destination,
update both `updateNav()`'s `moreLinks`/`primaryLinks` arrays *and* the matching
`<a>` in `base.html`'s drawer, and check both a desktop width and a narrow
(~390px) mobile width before calling it done.
`tests_e2e/test_nav_parity.py` is the automated regression test for exactly
this gotcha — it logs in, then diffs the visible desktop nav hrefs against the
visible mobile drawer hrefs across several pages and fails on any asymmetry.

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
- `requirements-dev.txt` — Playwright plus the base `requirements.txt`. Needed only to run the `tests_e2e/` browser suite: `pip install -r requirements.txt -r requirements-dev.txt`, then `playwright install chromium` **once** (pip installs the Python package, not the browser binary — the suite cannot run without that second step). Not installed by any Docker image.
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
BETTING_ALERT_EMAIL=...     # Recipient for the weekly betting-edge-alert email (scripts/betting_edge_alert_weekly.py) -- deliberately separate from ALERT_EMAIL (job-failure alerts), even though they'll likely be the same address
BETTING_EDGE_THRESHOLD=...  # Optional; |edge_vs_vegas| points above which a game is flagged as a raw (unvalidated) edge outlier in the weekly alert email. Default 3.0.
MAX_RETRIES=...             # Must match the job's own --max-retries, or alerting fails open (see Scheduled Jobs)
GCP_PROJECT/GCP_REGION=...           # Used by schedule_kickoffs.py to target the right Cloud Run Jobs Admin API
GCP_TASKS_QUEUE=...                  # Cloud Tasks queue name (winspool-kickoff-triggers)
GCP_SCHEDULER_SERVICE_ACCOUNT=...    # Service account schedule_kickoffs.py's enqueued tasks authenticate as
VAPID_PUBLIC_KEY=...        # Web Push VAPID public key (base64url)
VAPID_PRIVATE_KEY=...       # Web Push VAPID private key (base64url) — in prod delivered via Secret Manager (`vapid-private-key`, bound with `--set-secrets` in `deploy/deploy.ps1`), not a plain env var; see DEPLOY.md
VAPID_CLAIMS_EMAIL=...      # Contact email included in VAPID JWT claims
DISABLE_OUTBOUND_EMAIL=...  # True → no-ops all Resend sends (safety gate; set by the tests_e2e/ harness so browser tests never send real mail)
E2E_TEST_PLAYER_IDS=...     # Comma-separated seeded e2e test player IDs (first is admin) — from scripts/seed_e2e_test_players.py; required to run tests_e2e/
E2E_TEST_PLAYER_PASSWORD=...# Shared password for those seeded e2e test players — required to run tests_e2e/
E2E_CLAIM_TEST_PLAYER_ID=...    # Dedicated no-password lifecycle account for tests_e2e/test_account_claim.py — from scripts/seed_e2e_test_players.py's "Lifecycle account IDs" print output; required to run that test, else it skips
E2E_MFA_TEST_PLAYER_ID=...      # Dedicated mfa_enabled=True lifecycle account for tests_e2e/test_mfa.py — from scripts/seed_e2e_test_players.py's "Lifecycle account IDs" print output; required to run that test, else it skips
E2E_LOCKOUT_TEST_PLAYER_ID=...  # Dedicated lifecycle account for tests_e2e/test_lockout.py — from scripts/seed_e2e_test_players.py's "Lifecycle account IDs" print output; required to run that test, else it skips
E2E_TEMPWORD_TEST_PLAYER_ID=... # Dedicated lifecycle account for tests_e2e/test_forced_password_change.py — from scripts/seed_e2e_test_players.py's "Lifecycle account IDs" print output; required to run that test, else it skips
GIT_SHA=...                 # Baked into images at build time (Docker ARG/ENV, see Deployment); defaults to 'unknown'
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
  history_routes.py      # Historical data views + the unified player page (/player/{id}); /history/player/{id} 301s to it, and /profile redirects to /player/{me} (cookie) or a tiny localStorage-redirect template
  prediction_routes.py   # ML prediction endpoints (/api/predictions/*)
  mock_draft_routes.py   # Solo mock draft: page route + /api/mock-draft/* (setup, pick, results)
services/
  data_service.py        # 3-tier cache: memory → pickle → Firestore
  db_service.py          # Firestore/pickle persistence + auth (bcrypt)
  analysis_service.py    # Standings calc, win matrices, schedules; get_season_progress() accepts injected games_df/standings_df/draft_results_df/teams_df/players_df (lazy load_data(year=season) fallback for any left None)
  draft_service.py       # Draft state, pick validation, WebSocket sync
  mock_draft_service.py  # Mock draft: pick sequencing, bot picks, end-of-draft ranking (stateless, no DB writes)
  prediction_service.py  # Win projections (calls NN model)
  nn_prediction_service.py / nn_feature_engine.py / nn_projection_engine.py
  recap_service.py       # Gemini-powered weekly summaries
  ai_service.py          # Gemini API wrapper
  cache_service.py       # In-memory cache with TTL
  email_service.py       # Resend-based email: weekly recaps, MFA codes, and send_alert_email() job-failure alerts
  live_score_service.py  # Live game score updates
  live_standings_service.py  # Payload shaping for GET /api/live-standings (standings page's 30s poll, static/js/standings_refresh.js)
  push_service.py        # Web Push notifications (VAPID); a failed players-cache invalidation after saving/pruning a subscription is logged as a warning and no longer fails the push operation
  chat_service.py        # Draft room chat message persistence
templates/               # Jinja2 HTML (server-rendered)
static/
  style.css              # Cache-busted automatically: base.html links it via static_url('style.css') (services/static_assets.py), which appends a ?v=<content hash>; no manual version bump needed
  js/
    main.js              # Page init, nav rendering (updateNav), event handling; uses stale-while-revalidate via localStorage; wires data-theme-toggle buttons
    theme_init.js        # Classic <head> script: applies saved/system theme before first paint, exposes window.WinsPoolTheme (see Theme below)
    unpaid_notice.js     # Standings page: unpaid pills / reminder strip / "Still owed" line from GET /api/pool/unpaid
    ui_renderer.js       # Dynamic DOM rendering
    api.js               # Fetch wrapper
    websocket_service.js # WebSocket client for live draft
    admin_main.js        # Admin dashboard
    auth_service.js      # Client-side auth; also exports STORAGE_KEYS ({TOKEN, PLAYER_ID, ROLE, DRAFT_ACTIVE, THEME}) and getAuthHeaders() -- use these, never inline localStorage key literals or hand-built Bearer headers
    responsive.js        # Mobile drawer controller (non-module IIFE, loaded after main.js)
    chat.js              # Draft room chat overlay
    mock_draft.js        # Standalone mock draft page logic — does NOT import main.js/websocket_service.js/auth_service.js
scripts/                 # CLI tools for data sync, ML training, cache building
tests/                   # pytest unit suite (routes/services) — what a bare `pytest` collects
tests_e2e/               # Playwright browser e2e suite (run by explicit path); see tests_e2e/conftest.py for fixtures
models/                  # nn_v{N}.keras + scaler, xgb_v{N}.json + scaler, lr_v{N}.pkl + scaler; *_registry.json per model type
rawdata/                 # NFL raw data (NOT committed)
docs/                    # Architecture and model documentation (prediction_model.md, etc.)
.local_db/               # Local pickle cache (NOT committed)
```

### Shared helpers

- `services/constants.py::make_game_key(week, home, away)` is the single builder for the per-game key `W{week:02d}_{HOME}_{AWAY}` (game_predictions, prediction caches, projection engine); it normalizes both teams via `normalize_team_abbr` and coerces the week from int/str/float. Never hand-format that string.
- `templates/_macros.html::year_picker(available_years, current_year, base_url, url_suffix='')` is the season dropdown for the six per-year pages.
- `routes/models.py` request models carry field descriptions and bounds (`EMAIL_PATTERN`, season 2000..2100). Password length/complexity stays in the routes on purpose (a 422 would bypass lockout counting); see `docs/api_endpoints.md`.

### Team page

`GET /team/{abbr}` (`routes/history_routes.py`, case-insensitive, 404 for an unknown team) renders `templates/team.html` via `static/js/team_page.js` from `services/team_page_service.py::build_team_page()`: pool-season history (record, drafter linking to `/player/{id}`, pick; a highlighted "Winning combo" note with the pool winner's name, wins and the other two teams of their roster appears only on rows where this team's drafter won that completed season, other rows show nothing extra; a lighter, muted "Runner-up combo" note appears only on rows where the drafter finished 2nd, never on a winner row or the in-progress season; both come from one cached `_pool_top_two` standings computation per season). The payload also carries `pool_summary` (`seasons` completed pool seasons drafted, `winning`, `runner_up`, `never_won`, `never_top2`; the flags need `MIN_SEASONS_FOR_MARKER` = 3 completed seasons) rendered as one summary line under the History heading with a muted "Never on a winning/winning or runner-up roster" pill plus this season's schedule with the stored ensemble projection overlaid (`pred_prob` is the HOME win probability; an away team uses `1 - pred_prob`; byes are rows). Projected wins, per-game win probability/projection and projected record are withheld from non-admins while `draft_active` is set; history, results and the dropdown always render. `GET /teams` (the More-menu "Teams" entry, in both `main.js` `moreLinks` and the `base.html` drawer) redirects to the viewer's first drafted team this season (from the `session_token` cookie), else the first team alphabetically.

The team page header shows two clearly labeled projections: "Preseason projection: X" (`current.preseason_projection`, always the FROZEN number: `draft_snapshot_predictions` first, falling back to `preseason_predictions`, then the legacy-shape value) and "Current projection: Y (as of week N)" (`current.current_projection` = `{projected_wins, floor, ceiling, as_of_week}` from `data_service.get_season_projection_current`, results-aware and moving daily; `null` when no data, in which case only the preseason value shows). Both are `null` for non-admins while `draft_active` (same `include_projections` gate). `data_service.get_frozen_preseason_projection(season)` is the shared snapshot-first reader.

### Theme (light/dark)

Dark is the default; `:root[data-theme="light"]` in `static/style.css` overrides the base tokens (`--bg*`, `--line*`, `--ink*`, `--leader`, `--pos`/`--neg`, `--link`, `--warn`, `--glass-bg`) and every text token must stay >= 4.5:1 on `--bg`/`--bg-elev` (`tests/test_theme.py`). `static/js/theme_init.js` is a classic script in `base.html`'s `<head>` (a deferred module cannot prevent a flash) that applies `localStorage[STORAGE_KEYS.THEME]` (`nfl_wins_theme`, `light|dark`), else `prefers-color-scheme`, and exposes `window.WinsPoolTheme`; `main.js::wireThemeToggles()` binds every `[data-theme-toggle]` button and syncs across tabs. The toggle is an icon-only button (sun in dark, moon in light, `aria-label` "Switch to light/dark mode") in the desktop top bar (`.nav-rail`, before the avatar) and the mobile header (`.nav-mobile-header`); the icons swap in CSS via `.theme-icon-sun`/`.theme-icon-moon`. Panel tints and switch tracks use the `--surface-sunken*`, `--surface-hover` and `--toggle-off` tokens (dark default plus light override), not inline `rgba(0,0,0,...)`/`rgba(255,255,255,...)`. The theme survives sign-out: `AuthService.clearCredentials()` and `auth_guard.js` re-set it after `localStorage.clear()`. New CSS should use tokens; a hard-coded color needs a `:root[data-theme="light"]` override. Neutral tints and dividers use `--tint-faint`/`--tint`/`--tint-strong`/`--tint-heavy` and `--hairline`/`--hairline-strong` (dark default plus light override); `tests/test_theme_sweep.py` fails on any `rgba(255,255,255,...)` literal (except in shadows) or sub-0.5 black background in `style.css`, `templates/` and `static/js/`. **Canvas charts** (Chart.js) cannot use CSS variables: build options from `window.WinsPoolChartTheme.colors()` (`static/js/chart_theme.js`), which reads the active theme's tokens; it repaints every live `Chart.instances` entry when `theme_init.js` fires `wins-theme-change`. `paint()` must only assign nested option objects that are missing (Chart.js 4 returns proxies; re-assigning one onto itself overflows the stack). `:root` declares `color-scheme: dark` (light block: `light`) for native controls, and `<meta name="theme-color">` tracks `--bg-elev` per theme via `theme_init.js`. `templates/mock_draft.html` does not extend `base.html`, so it loads `theme_init.js` itself (it follows the saved theme and has its own `data-theme-toggle` button wired in `mock_draft.js`). Default-blue buttons and active tabs (`.btn-primary`, `.team-card.selected`, `.week-item.active`, `.expand-button`) use `--btn-primary-bg`, not `--primary-color`: the link blue is too light for white text in dark mode. A hard-coded dark chip (`#444`) must set its own `color:#fff`.

### Standings player links

On `/wins-pool/{year}` the player NAME is the only link in each of the three server-rendered variants (leader card, desktop row, stacked mobile card): `<a class="player-link" href="/player/{playerId}">`, with a 44px-min-height tap target (`.player-link` in `style.css`). Team logos/chips stay non-links. `standings_refresh.js` patches text in place and never rebuilds name markup, so the links survive the 30s poll. On the player page, only the team abbreviation text in each pick cell links to `/team/{abbr}` (`.team-link`, a single-line 44px flex target that never overlaps the pick/wins lines); the abbreviation is normalized via the `normalize_team_abbr` Jinja global (legacy OAK/SD/STL/LAR/WSH/JAC resolve to a valid team page) and `|urlencode`d. `.player-link` gets a faint dotted underline under `@media (hover: none)` as a touch affordance. Contract tests: `tests/test_standings_player_links.py`.

### Newer API endpoints

All require auth (`require_auth`) unless noted.
- `GET /api/players/{player_id}/portfolio` — same payload as `/api/profile/portfolio` for any player id (both share `api_routes.build_player_outlook(player_id, is_admin)`); never includes paid data; non-admins get `draft_in_progress` while `draft_active`; unknown id -> `available: false, reason: "no_teams"`. Feeds the Season Outlook card on `/player/{id}`.
- `/player/{id}` is the canonical player page (career history, Season Outlook for everyone; the Account and Security form and pool block sit in a hidden-by-default `#own-page-only` block shown by script only when localStorage `nfl_wins_my_player_id` matches the page id). `/profile` and `/history/player/{id}` redirect to it.
- `GET /api/pool/status[?season=]` — pool fee / prize pot summary from `services/pool_service.py::build_pool_status`: `entry_fee`, `total_count`, `paid_count`, `total_pot`, `collected`, `payouts` (`place`/`label`/`amount`, in dollars), `payout_total`, `pot_balance` (`total_pot - payout_total`), and only the *caller's own* `my_paid` — no other player's paid flag is returned. Season defaults to the active season. UI: the standings page shows only a slim chip (`#pool-fee-banner`, `static/js/pool_fee.js`: `Pot $X | You: Paid`, linking to `/player/{me}`); the full pool card (pot, payouts, paid count, own entry) lives in the own-page-only block of `/player/{id}` (`templates/player_profile.html`). Config is per season in the `config/settings` doc under `pool_config` (`{"<season>": {entry_fee, payouts: [{place, amount}]}}`); `place` is a positive int or `"last"` (last-place money back). Missing fields fall back to defaults: entry fee 200, payouts 1st 1400 / 2nd 600. Amounts are dollars (not percentages) and are the source of truth.
- `GET /api/pool/unpaid[?season=]` — gated unpaid-entry view (`services/pool_service.py::build_unpaid_payload`): `{enabled, stage, week, amount, unpaid, me_unpaid, payment_note, admin_view}`. `stage` (`off|nudge|public|banner`, `compute_unpaid_stage`) comes from the season's current REG week as the standings WEEK pill shows it (`current_played_week`: the in-progress week via `data_service.get_latest_week_for_year`, else the last completed one; 0 before any result; do NOT use `get_latest_season_and_week`, it returns the schedule's last week), so "nudge from week N" starts during week N and the season's `unpaid_visibility` (`{enabled, nudge_week, public_week, banner_week}`, default off/8/10/13; invalid settings fall back to defaults with `enabled: false`). The `unpaid` name list is returned ONLY to admins or when enabled and the stage is `public`/`banner`; `off`/`nudge` always return `[]` to non-admins. `me_unpaid` is the caller's own flag once past `off`; `payment_note` (season config, max 200 chars) only reaches admins or an unpaid caller. `/api/pool/status` and its pinned key-set test are untouched. UI: `static/js/unpaid_notice.js` on the standings page; amount owed and note on the own-page pool card of `/player/{id}`.
- `GET /api/admin/pool/config?season=` (admin) — that season's `entry_fee`, `payouts`, `member_count`, `total_pot`, `payout_total`, `pot_balance`, and `is_default` (true when nothing has been saved for the season). `POST /api/admin/pool/config` (admin) takes `{season, entryFee, payouts[]}` of `{place, amount}` (1 to 10 payouts, no duplicate places, non-negative amounts; an empty payouts list is rejected) and merges only that season into `pool_config`. Both also carry `unpaid_visibility` and `payment_note`; `POST` takes optional `unpaidVisibility` (snake_case inner keys, weeks 1..22, `nudge <= public <= banner`, else 422) and `paymentNote`, and omitting them keeps the stored values (`pool_service.set_pool_config(..., unpaid_visibility=None, payment_note=None)`). Edited in the admin panel's Pool tab (`static/js/admin_pool.js`), which defaults to `active_season` from `GET /api/admin/seasons`. `pool_config` is stripped from the public settings endpoint.
- `GET /api/profile/portfolio` — the caller's 3-team season outlook. With `odds_basis == "per_game"` every outlook number (expected/final wins, range, per-team `projected_wins` plus `wins_to_date`/`preseason_projected_wins` (the latter from the frozen snapshot via `get_frozen_preseason_projection`, not the daily-rewritten `preseason_predictions` doc; the player page team line appends "(preseason X)"), playoff chances) comes from `analysis.simulate_pool_finish_odds_from_games` (wins to date plus stored per-game probabilities); only the fallback uses the preseason-based `analysis.compute_portfolio_projection`: `odds_basis` `"team_projection"` when no remaining game has a stored prediction, or, if the per-game simulation itself fails, the preseason-based numbers with `odds_basis` null. Response is `{season, available, reason, ...}`; `available: false` with `reason` of `draft_in_progress` (non-admins while `draft_active` is set — same projection gating as the draft room), `no_teams`, or `no_projections`. When available it also carries pool-finish odds from `analysis.simulate_pool_finish_odds_from_games` (per_game basis) or `analysis.simulate_pool_finish_odds` (fallback), a Monte Carlo over every player's drafted teams using current standings records: `top2_prob` (probability of finishing in the top 2), `win_prob`, `expected_rank`, `pool_size`, `odds_basis` (`"per_game"` = Monte Carlo over the actual remaining schedule using stored `game_predictions` `pred_prob` home win probabilities, unplayed games with no stored prediction at 0.5; `"team_projection"` = per-team normal fallback when no remaining game has a stored prediction), and `games_remaining` (int for per_game, else null); these are `null`/`0` when unavailable or if the simulation fails (the rest of the portfolio is still returned). Math is documented in `docs/prediction_model.md` ("Player Portfolio Projection").
- `GET /api/predictions/accuracy` takes an optional `?season=` filter and always returns `available_seasons`; the admin ML Accuracy tab has a season dropdown driven by it (see `docs/prediction_model.md`).

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
| `season_projections` | `.local_db/season_projections.pkl` + `_{year}.pkl` | Results-aware CURRENT projection per team (wins to date + simulated remainder, ratings updated by real results); doc `{season}_{team}`, overwritten daily by `winspool-predict-daily` once the season's draft is complete, with `as_of_week`, `locked` (true once the season is over). Read via `data_service.get_season_projection_current()`. Distinct from the frozen preseason numbers |
| `season_projection_history` | `.local_db/season_projection_history.pkl` + `_{year}.pkl` | Same fields plus `week`; doc `{season}_w{week:02d}_{team}`. Each run writes one doc per team for the latest week with a completed game as of that run (never week 0); reruns overwrite the same doc. No backfill: weeks that finished before the job began writing this collection, or that the daily job missed, are not recreated. Read via `data_service.get_season_projection_history()` -> `{week: {team: ...}}` |
| `draft_snapshot_predictions` | `.local_db/draft_snapshot_predictions.pkl` + `_{year}.pkl` | Frozen, pre-draft copy of `preseason_predictions` for the six "fairness" readers (real draft room, mock draft setup/bot AI/grading, draft recap, draft results/history); written via `services/db_service.py::sync_draft_snapshot_for_season()`, called by `scripts/write_draft_snapshot.py` (a `scripts/refresh_preseason.py` step) and by `POST /api/admin/draft_snapshot/sync` (manual trigger), and locked once `draft_results` shows the season's draft has started. **Unlike `preseason_predictions`, no scheduled Cloud Run Job writes this collection** — after deploying this collection for the first time, an admin must run `scripts/backfill_draft_snapshot.py --live` once for historical locked seasons, then `scripts/write_draft_snapshot.py --season <current>` (or the admin sync endpoint) for the live season, then `scripts/refresh_local_pkls.py`, or the six frozen readers will silently fall back to `consensus_projections`/empty. See `docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md`. |
| `consensus_projections` | `.local_db/consensus_projections.pkl` + `_{year}.pkl` | Analyst win projections; `preseason_predictions` is model output only |
| `game_predictions` | `.local_db/game_predictions_{year}.json` | JSON, not pkl; one doc per season |
| `analytics_cache` | `.local_db/analytics/{analytic}_{year}_{week}.json` | JSON |
| `elo_history` | `.local_db/elo_history_{season}.json` | JSON, one doc per season; written by `scripts/compute_elo.py --firestore` (not the normal service-write path — see gotcha below); powers the admin Elo Ratings Explorer |
| `nn_weekly_accuracy` | `.local_db/nn_weekly_accuracy_{season}.json` | JSON, one doc per season; written by `scripts/weekly_model_eval.py --firestore` (manual, run once a week's games finish; additionally, `winspool-predict-daily`'s `cache_builder.py` now runs it automatically on Tuesdays for the latest fully-completed regular-season week — see Scheduled Jobs). Durable per-week accuracy snapshot that survives later retrains, unlike `game_predictions` which `cache_builder.py` recomputes with whatever model is currently deployed every day. Powers the admin ML Accuracy tab's "Weekly Snapshots" panel. Writes upsert by week rather than overwrite the season doc, since each run only evaluates the weeks passed on its command line |
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

**Cache invalidation in `db_service`:** a writer that changes static-domain data must call `_invalidate_static()` (clear this process's cache AND `signal_data_update(DOMAIN_STATIC)` so other Cloud Run instances refresh); never call `clear_data_cache(DOMAIN_STATIC)` alone. `tests/test_db_cache_signals.py` AST-scans `db_service.py` and fails if any function clears without signalling.

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
`cache_builder.py`, `smart_refresh.py`, and `compute_elo.py --firestore` for
the established pattern). Historically this
failed silently rather than raising — a bare `except Exception` around the
Firestore call would catch `None.collection(...)`'s `AttributeError` and only
`logger.error` it, so the script would print a false success message. That's
exactly what left prod's `elo_history` collection empty for days: the script
had been run without this override and reported success anyway.

**Current contract (`services/db_service.py`)**: `get_db()` is lazy — importing
`db_service` no longer initializes Firebase (or needs credentials). `get_db()`
returns `None` in local mode (`USE_LOCAL_DATA=True`) **and** when no
credentials are available (init failed / no default app). Firestore-writing
scripts should obtain their client via `get_db()` / `require_db()` *after*
forcing `USE_LOCAL_DATA=False`. `require_db(exit_on_missing=True,
missing_message=None, exc_type=None, getter=None)` does both: it sets
`USE_LOCAL_DATA=False`, calls the getter (default `get_db`), returns the client,
and otherwise either logs/prints the message and `sys.exit(1)`s or, with
`exit_on_missing=False`, raises `exc_type` (default `FileNotFoundError`). The
`getter` kwarg lets a script pass its own module-level `get_db` so test
monkeypatches keep working. Used by `daily_nfl_sync.py`,
`backfill_schedule_predictions.py`, `generate_weekly_predictions.py`,
`predict_season.py`, and `upload_configfiles.py` (their existing
`initialize_firebase`-style function names are kept as thin wrappers).

### Auth

Handled in `db_service.py`: bcrypt (12 rounds) with legacy SHA-256 migration support. Role-based access (admin/player). No external auth library — session state stored in the DB.

Password login (`POST /api/login`) issues a signed JWT (`session_service.create_token`) returned in the response body and set as an `httpOnly` `session_token` cookie. HTTP routes validate it via `require_auth`/`require_admin` (`Authorization: Bearer` header or the cookie); `services/session_service.py::get_is_admin` is a third, non-raising variant for endpoints that must also work for anonymous callers (used by the mock draft). `require_auth`/`require_admin` also stamp `players.last_active` (the admin panel's Activity column, distinct from `last_login`): `session_service.record_user_activity()` throttles to one write per player per 15 minutes per process and hands the write to a background thread; `db_service.record_player_activity()` writes only that field and swaps a patched copy into the cached players frame, deliberately *without* the static-cache clear/signal `update_player_profile()` does. **Session revocation (`token_version` / `tv`)**: `create_token(player_id, role, token_version=0)` embeds the player's stored `players.token_version` as the `tv` claim. Every place a token is trusted -- `require_auth`, `require_admin`, `get_is_admin`, and the cookie-only page routes (`history_routes._viewer`, the `/profile` redirect, via `session_service.decode_current_token`) -- looks the player up and applies `session_service.token_is_current(payload, player)`: the claim must equal the stored version, a missing `tv` or a missing/NaN stored version counts as 0 (so pre-existing tokens keep working until that player's password next changes), a deleted player fails closed (401), and a lookup error is 503 (not admin for `get_is_admin`). A token whose `tv` is ahead of the cached stored version is treated as a stale cache: the entry is dropped and the player document re-read once, and the token is rejected only if the fresh version still differs. Every write that changes or clears a password hash passes `bump_token_version=True` to `db_service.update_player_profile` (`update_player_credentials` always does; the profile-form password change and `POST /api/admin/reset_password` do too): in Firestore this is a server-side `Increment(1)` inside the same document update, locally +1 on the frame. A user's own password change (`POST /api/profile/update`) returns a fresh `token` in the body and resets the cookie, and the player page stores it in localStorage `nfl_wins_token` (which the JS API layer sends as a Bearer header). Password-changing writes also clear any pending `mfa_token`/`mfa_expiry` in the same update, and the static-cache fill is race-guarded (a fetch that overlaps a clear is not kept as current). The currency check does not use the players frame in production: `session_service._load_player_from_db` keeps a small per-player cache (`{player_id: (token_version, exists, fetched_at)}`, 30 second TTL, `_SESSION_CACHE_TTL_SECONDS`) filled by a single-document read (`players/{id}`) on a miss, so a cold static cache (every login) no longer triggers a full `players` collection read per authenticated request. `db_service.update_player_profile(..., bump_token_version=True)` calls `session_service.invalidate_session_cache()`, so a revocation takes effect immediately in the process that made the password change (a read already in flight cannot re-store its stale result); other instances honor the old token for at most the 30 second TTL. A deleted player is cached negatively for the same TTL. A missing Firestore client, a read error or a malformed document raises `SessionCheckUnavailable` (503, never cached). Local mode (`USE_LOCAL_DATA=True`) still reads the local players frame uncached. The role claim is still taken from the token, not re-read from the player row.

There is no separate room-code/passcode mechanism — that (`ROOM_CODE`, WebSocket `verify_code`/`request_signin`) was removed as dead code, since the frontend never used it (see Real-Time Draft below for how the live draft's WebSocket authenticates instead — it does **not** yet reuse this JWT; known follow-up).

**Client auth guard** (`static/js/auth_guard.js`, a classic script loaded from `base.html` before `main.js`): client-side "logged in" is otherwise only the saved `nfl_wins_my_player_id` in localStorage, while the JWT lives 7 days, so an expired/invalid/revoked token used to leave pages erroring until a manual sign-out. The guard wraps `window.fetch`; on a 401 from a same-origin `/api/` call (excluding `/api/login`, `/api/mfa/verify`, `/api/set_password`, `/api/check_player`, `/api/profile/update`, `/api/logout`) while a login is saved, it recognizes a dead session via the `X-Session-State` response header (`missing|invalid|expired|revoked`, set by `session_service._dead_session` on every session 401) or, for older responses, the detail text; then it runs `localStorage.clear()` (same as `AuthService.clearCredentials()`), best-effort `POST /api/logout` (the httpOnly cookie is only clearable server-side), and redirects to `/`, at most once per page load. `api.js` and the admin scripts now show `<status>: <server detail>` errors (`window.adminHttpError`). Contract + node behavioral tests: `tests/test_auth_guard.py`.

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
- Weekly ensemble accuracy tracked in `reports/nn_weekly_accuracy.csv` via `scripts/weekly_model_eval.py`, and optionally pushed to the `nn_weekly_accuracy` Firestore store (`--firestore`) for the admin ML Accuracy tab's "Weekly Snapshots" panel
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
| `winspool-predict-daily` | `scripts/cache_builder.py` | Daily 9:15 UTC (same Aug–Jan / Feb 1–10 split) | Regenerates predictions/analytics cache; on Tuesdays also runs `weekly_model_eval.py --season <year> --week <latest fully-completed REG week> --firestore` early in `main()` (runs first so a slow or failed prediction rebuild cannot skip it; it reloads the current models itself, so ordering doesn't change which model is graded) via `_run_weekly_eval_if_tuesday()` — non-fatal, 900s timeout, skipped if no week is complete yet; the only job that installs `requirements-ml.txt` (`Dockerfile.predict`). Also now owns `preseason_predictions` (previously written only by a human running `predict_season.py` manually) — until the season's DRAFT is complete (`services/draft_state.py::draft_is_complete`: `draft_results` picks >= picks defined by `draft_order_rules`, or `draft_order` x 3, else 30; independent of `draft_active`) it refreshes the current/next season's team win projections daily as a pure pre-results simulation; once the draft is complete it only LOCKS those docs (`lock_preseason_predictions`, `locked=True`, no value ever rewritten) and instead writes one results-aware simulation (completed REG results applied deterministically, ties credit no win, ratings updated, remainder simulated) to `season_projections` (current, overwritten daily; `locked=True` once week 18 is complete or the season is past) and `season_projection_history` (one doc per team per week with a completed game). The frozen `draft_snapshot_predictions` is never touched and remains the pre-draft analytics baseline. **Gotcha: a draft reset or pick undo after the draft was complete** (`/admin/reset_draft`, undo) leaves `preseason_predictions` locked (the job skips locked teams and logs 0 written) and leaves stale `season_projections`/`season_projection_history`; recover with `python scripts/unlock_preseason.py --season N` (dry run by default; `--firestore` to write; refuses while the draft is complete unless `--i-know-the-draft-is-complete`), which unlocks the preseason docs (values untouched) and deletes that season's two new collections. `season_projections.locked` is a label only; writes still overwrite daily. Only runs the write for `year >= current_year` (or `--force`); a completed past season is skipped entirely. **Footgun**: manually re-running `predict_season.py` on any season writes a payload with no `locked` field at all, which silently clears the lock — it's now effectively a manual-override tool, not a routine one. |
| `winspool-live-scores` | `scripts/sync_live_scores.py` | **Live as of 2026-10-02 (rollout done):** kickoff-aware Cloud Tasks enqueued weekly by `winspool-schedule-kickoffs` (`enqueue_live_ticks()`: one task every 10 min inside each game window, kickoff −5 min to +4 h, regular season only, ids `winspool-live-scores-<YYYYMMDDTHHMM>`, past ticks skipped) plus a slow `5,35 * * 9-12,1 *` Cloud Scheduler backstop (`winspool-live-scores-trigger`); the Feb trigger is deleted (regular season only). Still fast-exits outside a game window (20 min before first kickoff until 30 min after the last, or 4.5h after it if games aren't final; `--force` overrides) as a safety net. The container has no `rawdata/`, so the check does one small nflverse schedule GET and fails open on any error. See `docs/superpowers/specs/2026-09-30-live-scores-kickoff-tasks-design.md`. | Authoritative re-sync (narrow, last-7-days `nfl_games` window) + best-effort ESPN live-score overlay (`is_live`/`clock`/`period`) — **only overlays games nflverse's own `schedules` data source carries, which never includes preseason (`game_type="PRE"`) games at all**, confirmed 2026-08-21 |
| `winspool-schedule-kickoffs` | `scripts/schedule_kickoffs.py` | Weekly, Tuesdays 10:00 UTC, Sept–Jan (`winspool-schedule-kickoffs-trigger`) | Reads the schedule and selects every REG game kicking off in the next 8 days (`upcoming_clusters()`, `TASK_HORIZON_DAYS`; by kickoff time, not by week or result, so a late Monday result or a postponed game can't make it pick the wrong week; weekly runs overlap and deterministic task ids dedupe; a task whose run time already passed is skipped, since Cloud Tasks would dispatch it immediately), then enqueues up to 3 Cloud Tasks per kickoff cluster against the Cloud Run Jobs Admin API: sync (kickoff−75min), routine predict (kickoff−60min), and an ESPN-aware `cache_builder.py --resimulate` re-run (kickoff−30min, `RESIMULATE_LEAD_MINUTES` in `schedule_kickoffs.py` — must fire after the routine predict run, not before, or the routine run's stale prediction overwrites the fresher one; still unvalidated against a real measured runtime). Also enqueues the live-score ticks above (`enqueue_live_ticks()`, right after the per-cluster tasks, inside the main try block). Also runs the weekly betting-edge alert (`_run_betting_alert()`, piggybacked here rather than its own job — see `docs/superpowers/specs/2026-09-09-betting-edge-alert-design.md`) right after the kickoff tasks are enqueued: composes `pattern_scanner_service.scan_angles` + `betting_screener_service.screen_games` into a validated-angle-match + raw-`edge_vs_vegas`-outlier summary, emailing `BETTING_ALERT_EMAIL` only if either tier is non-empty. Wholly non-fatal — a screener bug can't fail the kickoff-task enqueue this job actually exists for. |

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
| `scripts/scrape_quarter_scores.py` | `rawdata/quarter_scores.csv` | Fetches from ESPN's scoreboard API (switched from jt-sw.com 2026-09-19 -- that site's week index occasionally omits a game entirely). Feeds the weekly recap's comeback-win detection (`--week`/`--firestore`, wired into `winspool-schedule-kickoffs`) and optionally enables quarter-by-quarter Elo updates. **Does not reliably support historical seasons** -- ESPN's endpoint silently returns the current season if an older `year` isn't honored; this script detects and refuses that mismatch (see its module docstring), but don't assume `--seasons 2006 2025` backfills correctly without checking. |

Not synced (redundant or unmaintained): `FiveThirtyEight.csv` (FTE, stops 2022), `Metadata-*.csv`, `Scoring-*.csv`, `ExpectedPoints-*.csv`, `Stats-*.csv` (box stats computed from nflverse weekly stats instead), `SeasonRoster-*.csv` (nflscraPy, superseded by snap_counts + rosters).

### Roster talent features
- `roster_talent_delta` — performance-based team grade from `stats_team_week_*.csv` (2020+): cumulative offense + defense composite z-scored within each week.
- `trench_dominance_metric` — composite of OL snap quality (snap counts × age multiplier, 2012+) and DL performance (sacks×6 + qb_hits×1 + tfl×1 from `stats_team_week_*.csv`, 2020+), z-scored per season so both components contribute equally. In the **preseason path**, this is overridden using `services/nn_feature_engine.py::compute_preseason_player_profiles()` — a player-level blend of up to 3 prior seasons' individual player snap-counts/advstats per position, weighted by recency × role reliability with age-scaled injury discounting — not a team-level roster-file lookup.

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

**`GIT_SHA` build arg**: `Dockerfile`, `Dockerfile.sync`, and `Dockerfile.predict`
each declare `ARG GIT_SHA=unknown` / `ENV GIT_SHA=$GIT_SHA` *after* the
`pip install` layer and *before* `COPY . .`, so a new commit SHA doesn't
invalidate the dependency cache (`tests/test_dockerfile_layer_order.py` pins
this). `cloudbuild-{sync,predict}.yaml` pass it via `--build-arg
GIT_SHA=$_GIT_SHA`, and `deploy.ps1` supplies `_GIT_SHA` (full `git rev-parse
HEAD`) for the two job images. The web service `Dockerfile` has the ARG/ENV
but `deploy.ps1` deploys it without a build arg, so it stays `unknown` there.

**VAPID private key** is bound from Secret Manager (`vapid-private-key`) by
`deploy.ps1`'s `--set-secrets`; DEPLOY.md has the one-time secret/IAM setup.

**Gotcha: each scheduled job needs a `MAX_RETRIES` env var matching its own
`--max-retries`.** `send_alert_email()` (`services/email_service.py`) only
actually sends on the job's final retry attempt, comparing Cloud Run's
auto-injected `CLOUD_RUN_TASK_ATTEMPT` against this env var — Cloud Run does
NOT auto-inject the configured max-retries itself, so without `MAX_RETRIES`
set, every attempt sends its own alert (4 emails per failure at the default
`maxRetries=3`, i.e. 4 total attempts). All 4 jobs currently have
`MAX_RETRIES=3` to match their (default, never overridden) `--max-retries=3`.
`betting_edge_alert_weekly.py`'s own `_run_with_alerting()` needs no separate
`MAX_RETRIES` of its own — it runs as a subprocess step inside
`winspool-schedule-kickoffs`'s execution, so it reads that job's already-set
`CLOUD_RUN_TASK_ATTEMPT`/`MAX_RETRIES` env vars. If you ever change a job's
`--max-retries`, update its `MAX_RETRIES` env var to match, or alerting
silently reverts to "fail open" (always sends).

See **Scheduled Jobs** (under Architecture, above) for the full set of
Cloud Scheduler/Cloud Tasks triggers running in production.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
