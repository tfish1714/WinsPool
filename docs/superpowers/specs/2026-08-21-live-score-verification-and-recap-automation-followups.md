# Post-Deploy Follow-Ups: Live-Score Verification + Weekly Recap Automation

**Date:** 2026-08-21
**Status:** Not designed — backlog, captured at the end of the scheduled-jobs deploy session (PRs #114, #116, #117) so these two asks aren't lost. Each needs its own brainstorming pass when picked up, not decided here.

## Origin

While verifying the live scheduled-jobs infrastructure (Task 9/10 of
`docs/superpowers/plans/2026-08-19-scheduled-jobs.md`), two follow-up asks
came up that are real but out of scope for that deploy: testing the
live-score polling path more rigorously once a real game is available, and
automating the weekly recap email the same way the other 4 jobs were
automated.

## 1. Live-Score Polling: Deeper Verification Needed

### What's been verified so far (2026-08-20/21)

- `winspool-live-scores` executes cleanly on its Cloud Scheduler trigger, no errors.
- Manually confirmed against a **real live preseason game** (HOU@LV, LAC@SF,
  2026-08-20) that ESPN's scoreboard API returns the expected schema
  (`status.type.name`, `clock`, `displayClock`, `period`) and that
  `services/live_score_service.py::get_live_updates()` parses it correctly.
- **But the overlay never actually wrote anything** — not a bug, a valid
  negative result: nflverse's `schedules` data source, which gates which
  games even exist in Firestore's `nfl_games` collection, never includes
  preseason games at all (`game_type` is always `REG`/`WC`/`DIV`/`CON`/`SB`,
  confirmed by downloading nflverse's raw `games.csv` directly). So the
  overlay's actual **write** path has never been exercised end-to-end —
  only the ESPN-parsing half.

### What's still unverified

- That a real **regular-season** live game (which nflverse does track) gets
  `is_live`/`clock`/`period` correctly written to a `nfl_games` Firestore
  doc during play.
- The "don't clobber a final score" guard in
  `scripts/sync_live_scores.py::overlay_espn_live_fields()` (skip the write
  if `result` is already non-null) — designed, never exercised against a
  real final-then-live transition.
- Team abbreviation matching for ESPN's `LAR`/`WSH`/`JAC` vs nflverse's
  `LA`/`WAS`/`JAX` — `sync_live_scores.py`'s overlay
  (`run_espn_overlay_safely()`) normalizes correctly per code review, but
  has never actually matched a live Rams/Commanders/Jaguars game.
- The frontend's "LIVE" badge actually rendering from real Firestore data —
  flagged as a known gap in the original plan; needs either a live game or
  a manually-seeded test doc.
- Whether `services/live_score_service.py::sync_live_scores_to_df()` — the
  **older, separate** ESPN-overlay path still used by
  `scripts/cache_builder.py`'s nightly analytics build — has the
  abbreviation-normalization bug already identified in
  `2026-08-20-scheduled-jobs-hardening-followups.md` (§1). That's a
  pre-existing bug this session didn't introduce or fix, but it means there
  are currently **two** ESPN-overlay code paths with different correctness
  characteristics, worth reconciling once both are actually exercised by a
  live game.

### Suggested approach when picked up

- Regular season starts ~Sept 10, 2026 — the cheapest real verification is
  to just watch the first live regular-season game and check the resulting
  `nfl_games` Firestore doc + the frontend badge directly, rather than
  building elaborate mocking for a one-time check.
- If earlier verification is wanted: manually seed a test `nfl_games` doc
  and run `run_espn_overlay_safely()` against whatever's live on ESPN right
  now (preseason games work fine for this, since the ESPN-parsing half is
  already proven — only the Firestore doc needs to exist first, which the
  preseason gap prevents happening naturally).
- Worth deciding then whether to fix or retire
  `live_score_service.py::sync_live_scores_to_df()`, per the open question
  already logged in the hardening-followups spec.

## 2. Weekly Recap Email Automation

### Goal

Automate the weekly recap (`services/recap_service.py`, Gemini-generated
summary + `services/email_service.py::send_weekly_recap_email()`) as a 5th
scheduled Cloud Run Job, following the pattern established in
`docs/superpowers/plans/2026-08-19-scheduled-jobs.md`.

### What already exists to build on

- `services/recap_service.py` — generates the recap content via Gemini.
- `services/email_service.py::send_weekly_recap_email(to_emails, subject, html_content)`
  — already Resend-based, sends to a list of recipients individually.
- The scheduled-jobs pattern to extend: `scripts/job_runner.py`'s shared
  step-runner, `send_alert_email()`'s two-layer alerting (script-level +
  Cloud Monitoring policy on `completed_execution_count`), the
  `MAX_RETRIES`/`[WinsPool Alert]` subject-prefix/Reply-To conventions
  (see CLAUDE.md's **Scheduled Jobs** section), the Cloud Scheduler trigger
  pattern, `deploy/deploy.ps1`'s automatic job-image rebuild on every
  deploy.

### Open questions to resolve when scoped

- **Trigger cadence** — presumably weekly, after the week's games finish
  (Tuesday morning is the natural slot, mirroring the old hypothetical
  Elo-recompute idea) — needs an explicit day/time decision.
- **Recipient list** — where does it come from? All players in the
  `players` Firestore collection? Does it need an opt-in/opt-out flag (some
  players may not want recap emails)? Check whether `players` already has
  an email-preferences field before assuming one needs to be added.
- **Ordering dependency** — should this run after `winspool-predict-daily`
  so the recap can reference fresh predictions, or is it independent
  (recapping only what already happened, not projecting forward)?
- **Idempotency** — what stops it from double-sending if the job is re-run
  or retried? The other 4 jobs are naturally idempotent (re-syncing data,
  or re-enqueuing Cloud Tasks with existing-task-id dedup), but sending an
  email is not naturally idempotent. Needs its own guard — e.g. a "recap
  already sent for week N" marker written to Firestore before/after the
  send.
- **Interaction with retry-gated alerting** — `send_alert_email()`'s
  retry-suppression logic assumes a failed attempt is safe to just retry.
  Is that still true for a job whose "success" is an irreversible side
  effect (an email actually sent)? A failure *after* the send but *before*
  job completion could cause a Cloud Run retry to send the recap twice.
  Worth deciding whether the actual send needs to be the last possible
  step, or needs its own idempotency check independent of the job-level
  retry count.
- **Docker image** — confirm `Dockerfile.sync` (the lean image, no ML
  deps) is sufficient. `recap_service.py`/Gemini calls shouldn't need
  TensorFlow, but worth double-checking the Gemini SDK's own dependency
  footprint doesn't secretly need something the lean image lacks before
  assuming this, rather than building a new job image from scratch.

## Non-goals

- Not designing either of these in detail here — this is a parking spot,
  not a plan. Both need their own pass through `superpowers:brainstorming`
  when picked up: the recap automation is architectural scale (new
  subsystem, new job, new email-idempotency concern); the live-score
  verification is closer to a spike (an existing, already-shipped feature
  that just needs a real live regular-season game to confirm against).
- Not blocking anything — both are follow-ups to already-shipped, working
  infrastructure (PRs #114, #116, #117), not gaps in what's currently live.
