# Scheduled Jobs Design — Raw Data, Model Predictions, Live Scores

**Date:** 2026-08-19
**Status:** Approved design, pending implementation plan

## Problem

Every data refresh in production today is manual. Verified directly (not just from docs):

- Cloud Scheduler API is **disabled** on the GCP project (`fishbone-wins-pool`) — confirmed via `gcloud scheduler jobs list`.
- `DEPLOY.md` documents a Cloud Scheduler job hitting `POST /api/trigger-sync` — that endpoint doesn't exist anywhere in the codebase.
- No GitHub Actions workflows.
- `scripts/run_cron.py`'s own docstring describes Windows Task Scheduler setup on a different machine (`G:\path\to\WinsPool`) — the historical mechanism, if any, was local, not cloud infra.

This spec covers wiring up real, scheduled automation for three things: refreshing raw data (rosters, depth charts, injuries), regenerating model predictions before games, and updating scores live during games — while keeping the cost near-zero and being honest about what can and can't be made real-time.

## Non-goals

- Changing what the model considers (e.g. using ESPN's per-game injury data to override a predicted starter) — verified feasible, tracked separately in `2026-08-19-espn-pregame-injury-signal-design.md` (see Open follow-ups below).
- Sub-minute live score/play-by-play tracking — out of scope; "live" here means "within ~5 minutes."
- Retraining the model on a schedule — out of scope; `train_nn_model.py` stays a manual, deliberate action.

## Architecture

Two new Cloud Run Job images, alongside the existing `winspool` web Service (untouched):

- **`winspool-sync`** (lean — `requirements.txt` only): `sync_nflverse_data.py`, `compute_elo.py --firestore`, `daily_nfl_sync.py`, `sync_live_scores.py` (new). None of these need TF/XGB/sklearn.
- **`winspool-predict`** (heavy — `requirements.txt` + `requirements-ml.txt`): `cache_builder.py`'s prediction-regeneration step only. This is the only piece that loads NN/XGB/LR models.

Splitting by actual dependency need (rather than one bundled image) mirrors the existing convention already established for the web service, and matters because image size affects container startup time on every execution, not just Python import time — a distinction the initial draft of this design got wrong before being corrected mid-review.

Cloud Scheduler cannot run arbitrary scripts directly — it targets HTTP endpoints or (for Cloud Run Jobs specifically) the Cloud Run Jobs Admin API's `:run` method via an OAuth service account. No new application HTTP endpoint is needed; this replaces the dead `/api/trigger-sync` idea in DEPLOY.md rather than trying to make it real.

## Jobs & Schedule

| Job | Trigger | Image | Purpose |
|---|---|---|---|
| `winspool-sync-daily` | Cloud Scheduler cron, ~9:00am UTC, **Aug 1 – ~Feb 10 only** | `winspool-sync` | Full raw data refresh (rosters, depth charts, injuries, snap counts, pfr_advstats, schedules, stats_team — `sync_nflverse_data.py` default priority 3) → `compute_elo.py --firestore` → `daily_nfl_sync.py` (standings) |
| `winspool-predict-daily` | Cloud Scheduler cron, ~9:15am UTC, **Aug 1 – ~Feb 10 only** | `winspool-predict` | Fixed baseline prediction regen. Always runs regardless of the dynamic path below, guaranteeing at least one fresh refresh+predict per day even if dynamic scheduling fails entirely. |
| `winspool-schedule-kickoffs` | Cloud Scheduler cron, weekly (Tue ~10am UTC), **Sept 1 – ~Feb 10 only** | `winspool-sync` | Reads that week's `gameday`/`gametime` from the schedule, computes each distinct kickoff-time cluster, and enqueues 2 Cloud Tasks per cluster: `winspool-sync-daily` at (kickoff − 75 min), `winspool-predict-daily` at (kickoff − 60 min). Cloud Tasks (not Cloud Scheduler) is the correct primitive for "run once at this specific future timestamp" vs. Cloud Scheduler's recurring-cron model. |
| `winspool-live-scores` | Cloud Scheduler cron, every 5 min, **Sept 1 – ~Feb 10 only** | `winspool-sync` | New `scripts/sync_live_scores.py` (below). |

None of these four run in the offseason (~Feb 10 – Jul 31) — no games, no roster/depth-chart churn worth tracking, no reason to pay for or even execute a run. Two different windows rather than one shared one:
- **`winspool-sync-daily`/`winspool-predict-daily`: Aug 1 – Feb 10.** Starts a month before Week 1 on purpose — preseason roster cuts, depth-chart formation, and draft-board projections (the exact ATL preseason-composite investigation from earlier this session) all happen in August, and the draft itself happens before Week 1 using this data.
- **`winspool-schedule-kickoffs`/`winspool-live-scores`: Sept 1 – Feb 10.** These are specifically about actual games with real kickoffs and pool-standings-relevant results — preseason exhibition games don't need last-minute re-syncs or live tracking the way regular-season/playoff games do.

The exact start/end dates should live as one place to edit (e.g. a constant in each script, or a shared config), not copy-pasted across four Cloud Scheduler job definitions — implementation detail for the plan, not decided here.

Rationale for the dynamic-vs-fixed split within the in-season window: the live-score job is cheap enough per-run that a broad always-on cadence is fine (verified: comfortably inside the Cloud Run free tier even at every-5-min/day). The predict job is not — hourly-all-day for the full Aug–Feb window would exceed the free tier (~230k vCPU-sec/month against a 180k allowance, by rough estimate). Dynamic per-kickoff-cluster scheduling gets precision (re-syncs + re-predicts shortly before each actual kickoff, catching Thu/Sun-early/Sun-late/Sun-night/Mon/occasional-Sat/international slates individually) without paying for runs nobody needs. The fixed daily baseline is the failure-mode backstop: if `winspool-schedule-kickoffs` itself fails silently, the week still gets one guaranteed daily refresh instead of nothing (and that failure fires the alert below).

## New script: `scripts/sync_live_scores.py`

Runs every 5 minutes in-season. Two parts:

1. **Authoritative (must not fail silently, drives actual wins):** `sync_nflverse_data.py --priority 1` (schedules/`games.csv` only — lightweight, single file) → re-run the same `compute_standings()` logic `daily_nfl_sync.py` uses (refactored into a shared, importable function so both call sites stay identical, never duplicated/divergent) → push `nfl_standings` + `nfl_games`. This is what actually moves player win totals, and intentionally does **not** depend on ESPN.
2. **Best-effort (cosmetic only, must not affect wins):** fetch ESPN's live scoreboard (`live_score_service.get_live_updates()`, already exists, verified working live during this design session), wrapped in try/except so any failure here is silent-safe and never blocks part 1. Writes only two new, purely-additive fields onto existing `nfl_games` documents: `is_live`, `clock`, `period` (the frontend's `ui_renderer.js` already has dead code rendering a "LIVE - Q3 8:42" badge from exactly these field names — it's just never received data, since today's `cache_builder.py` computes this in-memory and only feeds a nightly `analytics_cache` snapshot, never persisting back to `nfl_games`). Verified safe against both win-computation paths (`daily_nfl_sync.py::compute_standings()`, which reads local `games.csv` and never touches `nfl_games`; and `analysis_service.py`'s separate `get_season_progress()`/`TotalWinsBySeason` path, which does read `nfl_games` directly but only references specific named columns unaffected by new additive fields) — checked against `tests/test_firebase_schema.py`/`test_data_alignment.py`, neither enforces a closed schema on `nfl_games`.

**Fixes a real bug while here:** the current `sync_live_scores_to_df()` docstring claims it "only updates games that are NOT yet final," but the actual code overwrites unconditionally whenever ESPN has a matching game, regardless of prior final status. The new script implements a real version of that guard: once nflverse's own sync has marked a game final, ESPN's cosmetic fields stop being written for it.

## Error handling & alerting

- New `email_service.send_alert_email(subject, message)`, reusing the existing Resend integration (`email_service.py` — confirmed it's Resend-based today, not raw SMTP as CLAUDE.md's env var list still implies; that's a separate stale-doc note, not fixed here).
- Each job wraps its step sequence in the same required/non-required pattern `run_cron.py` already uses; any unhandled failure sends an alert email with the failing step + traceback before exiting non-zero.
- **Backstop:** a GCP Cloud Monitoring alert policy on Cloud Run Job execution failure → email notification channel, for catastrophic failures the script itself can't self-report (OOM kill, bad image, network egress blocked, container never starts).

## Cost estimate

All Cloud Run Jobs (no idle cost, billed only for actual execution time). None of the four run outside Aug 1 – Feb 10, so the ~5.5-month offseason costs $0, not just "cheap":

- `winspool-sync-daily` + `winspool-predict-daily`: ~18k + ~5k vCPU-sec/month during the active window — well inside the 180k free tier.
- `winspool-live-scores`: every 5 min in-season, lightweight (~30k vCPU-sec across the whole season) — trivial.
- Dynamic kickoff-cluster runs: ~4-6 clusters/week × 2 tasks × ~18 weeks — comparable order of magnitude to the daily baseline, still inside free tier.
- Cloud Scheduler: 4 recurring jobs, first 3/month free, $0.10/job/month after — ~$0.10-0.30/month.
- Artifact Registry: two images, but shared base layers dedupe storage; incremental cost over one image is marginal.

**Total estimate: $0-5/month.** This assumes the web service (`winspool`) stays untouched (no bundled ML deps, no forced `min-instances`) — that was the single biggest cost lever identified during design (a `min-instances=1` web service with ML deps bundled in was estimated at $50-100+/month, which is why it was rejected in favor of separate Jobs).

## Testing

- Unit tests for the refactored shared `compute_standings()` (already has test coverage via `daily_nfl_sync.py`'s existing tests — confirm they still pass post-refactor, add call-site tests for the new import path).
- New tests for `sync_live_scores.py`: no games in progress (no-op), a live game found (writes `is_live`/`clock`/`period`), a game that just went final (nflverse-sourced final score wins, ESPN cosmetic write stops), ESPN fetch failure (part 1 still completes, no exception propagates from part 2).
- New tests for `send_alert_email()`.
- Manual dry-run of each job locally (`USE_LOCAL_DATA=False` against prod or a scratch Firestore) before wiring the actual Cloud Scheduler/Cloud Tasks triggers.

## Open follow-ups (not in this spec)

- **ESPN pregame injury data → prediction pipeline.** Split into its own tracked spec: `docs/superpowers/specs/2026-08-19-espn-pregame-injury-signal-design.md`. Summary: ESPN's per-game `summary?event={id}` endpoint (verified live during this design session) has real timestamped pregame injury data, fresher than nflverse's daily-cadence injury files — but using it to actually change a prediction requires new work in `nn_feature_engine.py`, since the existing `compute_starter_qb_flags()` is retrospective (built from post-game data) and isn't reusable as a live pregame signal. Marked "not designed" there — needs its own brainstorming pass.
- CLAUDE.md's Key Environment Variables list still documents `SMTP_SERVER/PORT/USER` for email delivery; actual code uses Resend (`RESEND_API_KEY`, `FROM_EMAIL`). Stale, not fixed here.
- ESPN pregame injury data → prediction pipeline (see Appendix).
