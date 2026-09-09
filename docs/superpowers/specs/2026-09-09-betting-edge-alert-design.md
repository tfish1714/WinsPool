# Weekly Betting Edge Alert Email (#N)

**Status:** Draft
**Date:** 2026-09-09

## Context

`services/betting_screener_service.py` (manual filter screen,
`/admin/betting/screen`) and `services/pattern_scanner_service.py`
(walk-forward-validated angle miner, `/admin/betting/scan`) already exist and
are read-only over already-computed prediction data — but both are pull-only:
an admin has to open the admin dashboard and run them by hand to see anything.
There is no push. This spec adds a weekly scheduled job that runs the same
logic automatically and emails a summary when something worth looking at
surfaces for the upcoming week's games.

This is a personal alert (project owner only, one recipient) — not a
player-facing feature, and doesn't change anything players see.

## What counts as a "great play"

Two tiers, both surfaced, ranked with validated angles first:

1. **Validated-angle matches** (primary): reuse
   `pattern_scanner_service.scan_angles`'s walk-forward-validated filter
   combos — combinations of `FILTERABLE_FEATURES` thresholds that have shown
   a real edge over the 50% breakeven line on held-out seasons, not just
   in-sample. Run the scan, then check which of the upcoming week's games
   (via `betting_screener_service.screen_games`) match any of the top-ranked
   angles from that scan.
2. **Raw edge outliers** (secondary, lower confidence): any upcoming game
   where `|edge_vs_vegas|` (model spread vs. Vegas line, already computed per
   game in the `explanation` dict) exceeds a threshold — proposed starting
   point 3.0 points, tunable via env var. Not backtest-validated by
   itself, so labeled clearly as a raw signal in the email, separate from the
   validated-angle section.

Both sections can be empty in a given week — that's a normal quiet week, not
a failure. See "No spam" below for what that means for whether an email
sends at all.

## Cadence

Weekly, once per game week — not daily. Vegas lines and that week's schedule
need to be settled first. Piggyback timing on the existing
`winspool-schedule-kickoffs` job's Tuesday 10:00 UTC run (which already reads
the upcoming week's real kickoff times) — run this new job shortly after,
same day, so lines have had a chance to open. In-season only (Aug/Sept –
Feb 10), matching every other scheduled job in this repo.

## Delivery

Email via the existing `email_service.py` / Resend path — not push. The
sibling post-launch-hardening spec
(`docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`, §3)
found the push path is currently unobserved and likely broken across
devices; email is the reliable channel already proven for MFA codes and
recaps.

New function `send_betting_edge_email(to_email, week_summary)` in
`email_service.py`, following the existing `_send()` single-recipient
pattern (same as `send_mfa_code_email`). Subject e.g.
`[WinsPool] Week {N} betting edges` so it's visually distinct from
`[WinsPool Alert]` job-failure emails.

**Recipient**: new `BETTING_ALERT_EMAIL` env var — deliberately separate
from `ALERT_EMAIL` (job-failure alerts) so betting picks and infra failures
can go to different addresses if that's ever wanted, even though they'll
likely be the same address today.

**No spam**: if both tiers are empty for the week, skip sending entirely
rather than emailing "nothing this week" every week. Log the empty result
instead.

## Implementation shape

New script `scripts/betting_edge_alert_weekly.py`, following the same
`os.environ["USE_LOCAL_DATA"] = "False"` pattern documented in CLAUDE.md for
any script that writes to Firestore or reads live production data —
though this script is read-only (screener/scanner are explicitly read-only,
per their own docstrings), so it only needs the override to make sure it's
reading Firestore rather than stale local pickles.

- Calls `pattern_scanner_service.scan_angles` (same params
  `/admin/betting/scan` uses by default) to get validated angles.
- Calls `betting_screener_service.screen_games` for the upcoming week,
  filtered by each top validated angle, to find actual matching games.
- Computes raw `edge_vs_vegas` outliers directly from the same
  already-loaded prediction data (`services.cache_service.get_game_predictions`)
  used by both existing services — no new data path.
- Builds the two-tier summary and calls `send_betting_edge_email` if either
  tier is non-empty.
- Wraps `main()` in the same `_run_with_alerting()` pattern `cache_builder.py`
  uses (single-process job, calls `send_alert_email()` on any unhandled
  exception) — a crash in this job should still fire the existing
  `[WinsPool Alert]` failure path, distinct from a normal "no edges this
  week" no-op.

**New Cloud Run Job**: `winspool-betting-alert`, using the existing
`Dockerfile.sync` image (`python:3.10-slim`, `requirements.txt` only) — the
screener/scanner never touch the NN+XGB+LR ensemble, so no ML dependencies
are needed, keeping this job as cheap as the sync/live-scores jobs rather
than needing the heavier `Dockerfile.predict` image.

**New Cloud Scheduler trigger**: `winspool-betting-alert-trigger`, Tuesdays,
shortly after `winspool-schedule-kickoffs-trigger`'s 10:00 UTC run — exact
offset (e.g. +30min) to be tuned once it's clear how quickly that week's
lines stabilize after the schedule is confirmed.

**`MAX_RETRIES`**: per the existing gotcha in CLAUDE.md, this job needs its
own `MAX_RETRIES` env var matching its `--max-retries` (default 3, matching
every other job) or alerting fails open on retry.

## Out of scope

- Any change to what players see — this is admin/owner-only.
- Tuning the actual angle-mining parameters (`min_sample`, `top_n`, etc.) or
  the edge threshold's exact value — ship with the existing defaults /
  proposed 3.0-point threshold, tune later against a few weeks of real
  output.
- A UI for reviewing alert history — the email itself is the interface for
  now; revisit only if that stops being enough.

## Sequencing note

Independent of the post-launch-hardening spec's four sections — no shared
code path. Reasonable to build any time; not blocked on the alerts/push work
in that spec since this uses email exclusively.
