# Kickoff-aware live-score scheduling

Date: 2026-09-30. Status: draft, awaiting review.

## Problem

`winspool-live-scores` is triggered by two fixed Cloud Scheduler crons
(`*/2 * * 9-12,1 *` and `*/2 * 1-10 2 *`), i.e. ~720 container starts a day
all season. `sync_live_scores.py` fast-exits outside a game window
(`services/live_window_service.py::is_within_live_window`), but every
fast-exit still starts a container.

September 2026 measurements (Cloud Monitoring, `fishbone-wins-pool`):

- 11,218 live-scores runs, ~83% of Cloud Run Jobs CPU (114,900 of 137,800
  vCPU-s on the `billable_instance_time` metric).
- The `*/5` to `*/2` change (effective 2026-09-25) took runs from ~288 to
  ~720 a day and billable s/day from ~3,500 to ~5,700.
- Jobs CPU was $9.27 of a $21.51 September bill. Container start-up, which the
  metric excludes but billing includes, scales with run count.

## Goal

Run live-scores only while NFL regular-season games are being played, every
10 minutes inside those windows (down from 2 minutes since 2026-09-25, and 5
minutes before that). Playoffs and the Super Bowl are out
of scope: the pool counts regular-season wins only.

## Design

### 1. Window computation (`scripts/schedule_kickoffs.py`)

- Reuse the existing REG-only week selection (`_current_season_week`) and
  per-game kickoff times (`gameday` + `gametime`, US/Eastern, DST-aware).
- Each game contributes the interval `[kickoff - 5 min, kickoff + 4 h]`.
  Overlapping or touching intervals are merged into windows (a typical Sunday
  yields 2-3 windows; Thursday, Monday and international games yield their own).
- New pure function `compute_live_windows(games, season, week)` returns the
  merged `(start, end)` list. No I/O, so it is unit-testable.

### 2. Tick generation and enqueueing

- Ticks fall every 10 minutes within each window, aligned to minutes divisible
  by 10 (first tick is the first such minute at or after the window start).
  The interval is a single constant (`LIVE_TICK_MINUTES = 10`).
- Each tick is one Cloud Task targeting `winspool-live-scores:run` through the
  existing `enqueue_task()` and `winspool-kickoff-triggers` queue (queue limits
  verified: 500 dispatches/s, 1000 concurrent, retry max 5 attempts: ample
  for ~200 tasks a week).
- Task id: `winspool-live-scores-<YYYYMMDDTHHMM>`. Deterministic, so a rerun of
  the weekly job hits the existing `AlreadyExists` skip path, as the sync and
  predict tasks do.
- Called from `main()` after the existing per-cluster sync/predict/resimulate
  loop, inside `main()`'s existing try block and before the non-fatal
  quarter-score scrape and betting alert. A failure here sends the job's
  existing alert email and exits non-zero; tasks already enqueued stay
  queued, Cloud Run's retries re-run idempotently, and the two non-fatal
  steps are skipped for that run (accepted trade-off, no separate try block).

### 3. Existing in-job guard stays

`is_within_live_window()` still runs inside `sync_live_scores.py`. Its window
opens 20 min before the earliest kickoff and closes 30 min after the last
final (or 4.5 h after the last kickoff), so every tick this design schedules
(-5 min to +4 h) is inside it; a stray tick outside a real game day fast-exits.
No change to `sync_live_scores.py`.

### 4. Scheduler changes (infrastructure, not repo code)

- `winspool-live-scores-trigger` (Sept-Jan): `*/2` becomes `5,35` as a
  backstop (48 runs a day instead of 720). If the Tuesday job ever fails,
  scores still refresh every 30 minutes.
- `winspool-live-scores-trigger-feb`: deleted. Regular-season games end in
  early January; nothing runs in February.
- Order of rollout: deploy the new `schedule_kickoffs.py`, run it once manually
  so the current week's tasks exist, confirm tasks in the queue, then change
  the Scheduler crons. Reversing the order leaves a gap with no triggers.

### 5. Expected effect

Roughly 200 ticks a week plus ~340 backstop runs, versus 5,040 today:
about -89%. Within-window cadence goes from 2 to 10 minutes (score
freshness during games is 10 min). The actual saving shows on the
October invoice's `Jobs CPU` line.

## Edge cases

- DST: kickoff times are ET `ZoneInfo`; ticks are enqueued as UTC timestamps.
  Tests cover the first Sunday of November (clocks fall back).
- Overlapping windows (early + late Sunday games) merge: no duplicate ticks.
- A game rescheduled after Tuesday (flex scheduling, weather) is outside the
  enqueued windows; the `5,35` (every 30 min, offset from the 10-minute ticks) backstop covers it until the next weekly run.
- Weekly job fails: existing alert email fires; backstop keeps scores fresh.
- Season over: `_current_season_week()` already raises when no REG games
  remain; behaviour unchanged.

## Testing

Extend `tests/test_schedule_kickoffs.py` (no new framework):
- `compute_live_windows`: single game, overlapping games merged, disjoint
  games (Thu + Sun) stay separate, DST fall-back week.
- Tick generation: 10-minute-mark alignment, first tick at or after
  kickoff - 5 min, last tick at or before kickoff + 4 h.
- Enqueue path: deterministic task ids, `AlreadyExists` skipped,
  `winspool-live-scores` job target.

## Docs

Update `CLAUDE.md` Scheduled Jobs (live-scores row: trigger is now kickoff
Cloud Tasks plus a `5,35` (every 30 min, offset from the 10-minute ticks) backstop; schedule-kickoffs row: also enqueues
live-scores ticks) and `docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md`
only if it names the old cron.

## Out of scope

Playoffs/Super Bowl, changing the in-job window logic, trimming job start-up
time, Firestore read volume, Artifact Registry cleanup.

## Revision 2026-10-01: date-horizon selection (supersedes "week" selection above)

Selecting "the earliest week with no result" fails when last week's Monday
result is late or a game is postponed: the old week is chosen, its task times
are in the past (Cloud Tasks dispatches past-dated tasks immediately), and the
real upcoming week gets nothing. Replacement, shared by sync, predict,
resimulate and live-score ticks:

- `upcoming_clusters(games, now, horizon_days=8, lookback=4h)`: REG games of any
  season/week whose kickoff is in `(now - 4h, now + 8 days]`, grouped by kickoff
  into `(kickoff_et, [game_ids])`. The 4h lookback keeps in-progress games so a
  mid-game run still schedules the rest of their window. Rows with missing or
  malformed `gameday`/`gametime` are skipped, not fatal.
- `enqueue_kickoff_tasks(client, clusters, now)`: the existing sync (-75 min),
  predict (-60) and resimulate (-30) tasks per cluster, each skipped if its run
  time is not strictly after `now`. Task ids are unchanged, so tasks already
  queued by the old code dedupe via AlreadyExists.
- `compute_live_windows(kickoffs)` and `enqueue_live_ticks(client, clusters, now)`
  take the clusters instead of (games, season, week).
- `_current_season_week` remains only for the quarter-score scrape and betting
  alert, and now runs after the enqueueing. Season-over still raises there
  (alert), as before.
- Weekly runs overlap by design (8 days > 7); deterministic ids skip duplicates.
