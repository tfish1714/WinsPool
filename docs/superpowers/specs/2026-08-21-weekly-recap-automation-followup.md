# Post-Deploy Follow-Up: Weekly Recap Email Automation

**Date:** 2026-08-21
**Status:** Not designed — backlog. Needs its own brainstorming pass when picked up, not decided here.

**Split off from:** `docs/superpowers/specs/completed/2026-08-21-live-score-verification-followup.md`
(the live-score half of that original combined doc was resolved 2026-09-11;
this half is still open).

## Origin

Captured at the end of the scheduled-jobs deploy session (PRs #114, #116,
#117): the other 4 scheduled jobs got automated (nflverse sync, predictions,
live scores, kickoff scheduling), and it was noted that the weekly recap
email could follow the same pattern but was out of scope for that deploy.

## Goal

Automate the weekly recap (`services/recap_service.py`, Gemini-generated
summary + `services/email_service.py::send_weekly_recap_email()`) as a 5th
scheduled Cloud Run Job, following the pattern established in
`docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md`.

## What already exists to build on

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

## Open questions to resolve when scoped

- **Trigger cadence** — presumably weekly, after the week's games finish
  (Tuesday morning is the natural slot) — needs an explicit day/time decision.
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

- Not designing this in detail here — this is a parking spot, not a plan.
  Needs its own pass through `superpowers:brainstorming` when picked up:
  this is architectural scale (new subsystem, new job, new
  email-idempotency concern).
- Not blocking anything — follow-up to already-shipped, working
  infrastructure (PRs #114, #116, #117), not a gap in what's currently live.
