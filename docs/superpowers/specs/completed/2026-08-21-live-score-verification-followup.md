# Post-Deploy Follow-Up: Live-Score Polling Verification

**Date:** 2026-08-21
**Status:** Completed 2026-09-11 — see Resolution below.

## Origin

While verifying the live scheduled-jobs infrastructure (Task 9/10 of
`docs/superpowers/plans/completed/2026-08-19-scheduled-jobs.md`), a follow-up
ask came up that was out of scope for that deploy: testing the live-score
polling path more rigorously once a real regular-season game was available.

## What had been verified as of 2026-08-20/21

- `winspool-live-scores` executes cleanly on its Cloud Scheduler trigger, no errors.
- Manually confirmed against a real live preseason game (HOU@LV, LAC@SF,
  2026-08-20) that ESPN's scoreboard API returns the expected schema
  (`status.type.name`, `clock`, `displayClock`, `period`) and that
  `services/live_score_service.py::get_live_updates()` parses it correctly.
- The overlay's actual **write** path had never been exercised end-to-end —
  nflverse's `schedules` data source, which gates which games even exist in
  Firestore's `nfl_games` collection, never includes preseason games at all.

## What was still unverified

- That a real regular-season live game gets `is_live`/`clock`/`period`
  correctly written to a `nfl_games` Firestore doc during play.
- The "don't clobber a final score" guard in
  `scripts/sync_live_scores.py::overlay_espn_live_fields()`.
- Team abbreviation matching for ESPN's `LAR`/`WSH`/`JAC` vs nflverse's
  `LA`/`WAS`/`JAX` against a real live Rams/Commanders/Jaguars game.
- The frontend's "LIVE" badge actually rendering from real Firestore data.
- Whether `services/live_score_service.py::sync_live_scores_to_df()` (the
  older, separate ESPN-overlay path used by `scripts/cache_builder.py`'s
  nightly analytics build) has the abbreviation-normalization bug identified
  in `2026-08-20-scheduled-jobs-hardening-followups.md` §1.

## Resolution (2026-09-11)

Verified live against the actual season-opener game (SF@LA, 2026-09-10,
`game_id=2026_01_SF_LA`), triggered by a user report that the live badge
wasn't showing:

- `winspool-live-scores` was running every 5 minutes and correctly writing
  `is_live`/`clock`/`period`/`possession` to the Firestore `nfl_games` doc —
  confirmed by querying Firestore directly during the live game.
- Team abbreviation matching for the Rams (`LAR`→`LA`) works correctly in
  `sync_live_scores.py`'s overlay path — the abbreviation bug from
  `2026-08-20-scheduled-jobs-hardening-followups.md` §1 lives only in the
  older `sync_live_scores_to_df()` path used by `cache_builder.py`'s nightly
  build, which remains a separate, not-yet-fixed issue (see that spec).
- The frontend badge (`Q{period} {clock}`, possession icon) does render from
  real Firestore data via `templates/schedule.html`.
- **Found and fixed a real gap**: the ESPN overlay only ever wrote
  `is_live`/`clock`/`period`/`possession` — never the in-game score itself,
  so `schedule.html` always showed `—` for a live game's score even though
  the clock/quarter badge worked. Fixed by adding separate
  `live_home_score`/`live_away_score` fields (display-only, never touching
  `home_score`/`away_score`, which still come exclusively from nflverse and
  drive win totals) — see commit `f3365fb`.
- Also added client-side polling (`static/js/live_refresh.js`, `/api/live-scores`)
  so an already-open schedule page picks up score/clock updates without a
  manual reload — this had never existed at all before.
- The "don't clobber a final score" guard was exercised implicitly (the
  overlay correctly left `home_score`/`away_score`/`result` alone throughout
  and only ever wrote the new display-only fields) but not against an actual
  final-transition edge case in production; low-risk enough not to block
  closing this out.

**Still open, deliberately not addressed here:** the
`sync_live_scores_to_df()` abbreviation bug in the `cache_builder.py`
nightly-build path — tracked in
`2026-08-20-scheduled-jobs-hardening-followups.md`.
