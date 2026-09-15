# Comeback Win Detection in the Weekly Recap

**Status:** Draft — ready for `superpowers:writing-plans`
**Date:** 2026-09-15

## Origin

The weekly AI recap (`services/recap_service.py::extract_weekly_data`) already
calls out bad beats and notable wins (blowouts, escapes, close losses) from
final scores alone. The user asked for one more category: comeback wins —
"trailed and still won." Final scores can't express that; only a quarter-by-
quarter score breakdown can distinguish "won by 3" from "was down 17 in the
3rd and won by 3."

## Confirmed during design (read directly from source, not assumed)

- `nfl_games`/`rawdata/schedules/games.csv` (nflverse, the only source the
  daily sync pipeline touches) carry only final scores — no quarter
  breakdown at all.
- `rawdata/quarter_scores.csv` (`scripts/scrape_quarter_scores.py`) already
  has the needed per-quarter home/away scores, back to 2006, but comes from
  a **separate, currently-unscheduled** scraper against a third-party site
  (jt-sw.com) — it is not part of `run_cron.py`/`winspool-sync-daily` and
  has never been wired into Firestore. It is rate-limited (~0.5s/request,
  polite-scraping design) and already skip-caches per `(season, week,
  home_team)` via `_load_existing()`/`existing` tracking in `scrape_season()`
  — scoping a run to one week is a small, additive change (a `weeks` filter
  on the existing `range(1, max_week + 1)` loop), not a rewrite.
- `scripts/schedule_kickoffs.py` runs weekly (Tue ~10:00 UTC, in-season)
  but is entirely forward-looking: it reads the *upcoming* week's kickoff
  times and enqueues Cloud Tasks for games that haven't happened yet
  (`main()` → `_current_season_week(games)` → `compute_kickoff_clusters_with_games()`).
  Quarter scores only exist for games that have *already finished* — i.e.
  the week before the one this job is scheduling for. Reusing this job's
  trigger means adding a distinct, independent step for `week - 1`, not
  extending its existing forward-looking logic.
- The weekly recap (`extract_weekly_data`) is triggered manually — an admin
  action in `routes/admin_routes.py` (`preview_recap_prompt`) or a manual
  run of `scripts/generate_weekly_summary.py` — never on a schedule (see
  `[[project_weekly_recap_automation]]`-shaped follow-up, not yet scoped).
  It can run before *or* after Tuesday's new scrape step in any given week,
  so missing quarter-score data for the just-played week must be a silent,
  no-op skip, not an error.
- The cache mutability redesign that just landed
  (`docs/superpowers/specs/completed/2026-09-14-cache-mutability-redesign-design.md`)
  replaced the old year-keyed/`'all'` cache with named, signaled mutability
  domains (`active`/`historical`/`static`/`predictions_active`/
  `predictions_historical`/`admin_analytics`), and was deliberate about
  **only** giving a collection a domain + signal field when it's read
  repeatedly on a real (hot) path. It explicitly left `prediction_features`
  and `weekly_recaps` out for exactly this reason — admin-only/low-frequency,
  "not worth it now." `quarter_scores` has the identical shape: one reader
  (`extract_weekly_data`), invoked manually, at most a few times a week.
  Giving it a cache domain would mean adding a new signal field to
  `metadata/cache_control` and a new in-memory bucket for a reader that
  doesn't need either — the same complexity the redesign just finished
  trimming elsewhere. `elo_history`/`nn_weekly_accuracy`'s treatment (§4 of
  that design — full-collection read, cached under the shared
  `admin_analytics_updated` signal) doesn't fit either: those are read on
  every admin-explorer page load; `quarter_scores` is read only when an
  admin manually generates a recap, and only for one season's one week at a
  time.

## Explicitly out of scope (with reasoning)

- **Any in-memory cache domain for `quarter_scores`.** Per the point above:
  one low-frequency reader doesn't justify a new `metadata/cache_control`
  signal field or cache bucket. Read straight from local-json-or-Firestore
  on every call, mirroring how `prediction_features`/`weekly_recaps` were
  left uncached by the redesign.
- **Backfilling historical seasons' quarter scores into Firestore.** The
  recap only ever needs the just-finished week of the *active* season. A
  full 2006-current backfill remains a manual, on-demand
  `scrape_quarter_scores.py` run against the local CSV if ever needed for
  something else (e.g. the already-scaffolded quarter-by-quarter Elo
  update); not part of this feature.
- **Flagging comeback *losses*** (blew a 14+ point lead and lost) as a
  distinct bad-beat category. Only comeback *wins* were asked for. The
  underlying data would support the mirror case later if wanted, but YAGNI
  for now.
- **A new standalone Cloud Scheduler trigger/Cloud Run Job** for the
  scraper. Reusing `winspool-schedule-kickoffs`'s existing weekly trigger
  avoids provisioning anything new (see Design §1).

## Design

### 1. Scraper: scope to one week

Add a `weeks: list[int] | None = None` parameter to
`scrape_season()` (`scripts/scrape_quarter_scores.py`), defaulting to
today's full-season behavior; when given, restrict the existing
`range(1, max_week + 1)` loop to just those weeks. Add `--week INT` to
`main()`'s argparse (alongside the existing `--season`/`--seasons`),
mutually compatible with `--season` (not `--seasons`, which already means
"a range of years," not weeks).

### 2. Firestore push: mirror `compute_elo.py --firestore`

Add `--firestore` to `scrape_quarter_scores.py`. When set, after scraping
(regardless of how the run was scoped), for each **season actually
touched** re-read that season's full accumulated rows back out of
`rawdata/quarter_scores.csv` (all weeks scraped so far for that season, not
just the ones this run added — the Firestore doc is one-per-season, so a
partial write would regress it) and call a new
`cache_service.write_quarter_scores_season(season, rows, use_local=...)`,
following the exact `write_elo_history_season()` shape: local write to
`.local_db/quarter_scores_{season}.json` always, plus a Firestore
`quarter_scores/{season}.set({"season": season, "rows": rows})` when
`use_local=False`. Same `USE_LOCAL_DATA=False`-before-importing-`db_service`
gotcha applies (see CLAUDE.md's "Gotcha" note) — `schedule_kickoffs.py`
already sets this at import time, so the new step inherits it for free.

### 3. Read path: `cache_service.get_quarter_scores_season()`, no cache domain

New `cache_service.get_quarter_scores_season(season: int) -> list[dict]`:
reads `.local_db/quarter_scores_{season}.json` if present, else Firestore
`quarter_scores/{season}`, else returns `[]`. No in-memory memoization, no
`metadata/cache_control` signal field — called fresh every time (see
"Confirmed during design" above for why). `recap_service.py` calls this
directly, the same way it already calls `get_season_projection_blended()`
directly rather than routing through `load_data()`.

### 4. Scheduling: an independent step in `schedule_kickoffs.py`

In `main()`, after the existing forward-looking enqueue logic, compute
`prior_week = week - 1` (the week whose games just finished, from the same
`season, week = _current_season_week(games)` already computed there). If
`prior_week < 1`, skip entirely (nothing has completed yet this season).
Otherwise, run
`scrape_quarter_scores.py --season {season} --week {prior_week} --firestore`
as a subprocess, mirroring `_sync_schedule_data()`'s existing subprocess
pattern in the same file. **Non-fatal on failure**: log a warning and
continue, do not raise into the job's outer `except (Exception, SystemExit)`
→ `send_alert_email` path. Rationale: this scrapes a single third-party
site with no fallback; a flaky/blocked scrape is expected occasionally, it
only affects recap flavor text (not core standings/predictions), and it
must never cause the job to report failure for — or skip — the kickoff-task
enqueuing that's this job's actual purpose.

### 5. Comeback rule

A win counts as a comeback (added to the winner's `notable_wins`) if
**either** holds, computed from `quarter_scores`' per-quarter cumulative
home/away scores for that game:
- The winner was behind on the scoreboard after Q3 (cumulative Q1+Q2+Q3),
  i.e. trailing entering the 4th quarter, or
- The winner trailed by 14 or more points at any quarter checkpoint (after
  Q1, after Q2, or after Q3).

Both conditions are checked against the same three cumulative checkpoints;
there's no separate "how" messaging — either one produces the same
comeback callout, with the specific deficit/quarter mentioned in the
generated line (e.g. "Down 17 with one quarter left and still found a way
to win").

### 6. Recap integration

In `extract_weekly_data()` (`services/recap_service.py`): after loading
`weekly_games`, also call `get_quarter_scores_season(year)` once and index
its rows by `(week, home_team, away_team)` for O(1) lookup. For each row in
`weekly_games`, if a matching quarter-score row exists, evaluate the
comeback rule for whichever side won; if true, append a comeback line to
that winner's `player_stats[pid]['notable_wins']` (the same list already
used for blowouts/close wins) instead of a new top-level bucket — keeps the
existing "NOTABLE WINS THIS WEEK" section in the generated prompt as the
one place all positive callouts live. If no matching row exists (scrape
hasn't run yet for that week, or the week predates this pipeline), skip
silently — exactly like every other optional-data check already in this
function.

### 7. Local dev parity

Add `dump_quarter_scores()` to `scripts/refresh_local_pkls.py`, mirroring
`dump_elo_history()`/`dump_nn_weekly_accuracy()`: stream all
`quarter_scores` docs from Firestore → one
`.local_db/quarter_scores_{season}.json` per season.

## Testing approach

- `scrape_season()`: a `weeks=[N]` call only fetches/returns that week,
  existing full-season behavior unchanged when `weeks=None`.
- `write_quarter_scores_season()` / `get_quarter_scores_season()`: local
  round-trip (write then read back matches), and a Firestore-mode test
  mirroring the existing `write_elo_history_season` test's mocking
  approach.
- Comeback predicate: table-driven unit tests covering — trailing after Q3
  and won (true), trailing by 14+ after Q1 only then won comfortably
  (true), trailing by 14+ after Q2 but the exact 14 boundary (true at
  exactly 14), never trailing by more than 13 and never behind after Q3
  (false), and the losing side of a game that meets the criteria (not
  flagged — only the winner can have a "comeback win").
- `extract_weekly_data()`: a test with mocked `get_quarter_scores_season`
  returning a matching comeback row asserts the notable_wins line appears;
  a test with no matching row (empty list) asserts the function still
  returns normally with no comeback line and no error.
- `schedule_kickoffs.py`: a test asserting the new step is skipped when
  `prior_week < 1`, and a test asserting a non-zero exit from the scrape
  subprocess is logged but does not raise or prevent task enqueueing.

## Open questions to resolve during implementation

- Whether `write_quarter_scores_season()`'s "re-read the full season from
  CSV before pushing" step should live in `scrape_quarter_scores.py`'s
  `main()` directly, or as a small helper shared with any future caller —
  lean toward inline in `main()` until a second caller exists (YAGNI).
