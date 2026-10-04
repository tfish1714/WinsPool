# Kickoff-Aware Live-Score Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `winspool-live-scores` run only during real NFL regular-season game windows (every 10 minutes, starting 5 minutes before kickoff) by enqueueing Cloud Tasks from the weekly `schedule_kickoffs.py` job, and demote the all-day Scheduler trigger to a `5,35` (every 30 min, offset from the 10-minute ticks) backstop.

**Architecture:** Pure functions in `scripts/schedule_kickoffs.py` turn the week's REG kickoff times into merged UTC windows and 10-minute tick timestamps; a small enqueue helper sends one Cloud Task per tick through the existing `enqueue_task()` and queue. The in-job `is_within_live_window()` guard in `sync_live_scores.py` is untouched. Scheduler changes are manual `gcloud` infrastructure steps done after deploy.

**Tech Stack:** Python 3.10, pandas, `zoneinfo`, Google Cloud Tasks (`tasks_v2`), pytest, `gcloud`.

**Spec:** `docs/superpowers/specs/2026-09-30-live-scores-kickoff-tasks-design.md`

## Global Constraints

- Regular season only: windows come from `game_type == "REG"` games (reuse `compute_kickoff_clusters`). No playoff or Super Bowl handling.
- Window per game: `[kickoff - 5 min, kickoff + 4 h]`; overlapping or touching windows merge.
- Tick interval is the single constant `LIVE_TICK_MINUTES = 10`; ticks fall on minutes divisible by 10.
- Backstop trigger `winspool-live-scores-trigger` becomes `5,35 * * 9-12,1 *` (UTC); `winspool-live-scores-trigger-feb` is deleted.
- All window arithmetic is done in UTC (convert the ET kickoff with `.astimezone(timezone.utc)` first), never as wall-clock ET math.
- Task id format: `winspool-live-scores-<YYYYMMDDTHHMM>` (from existing `enqueue_task`; do not change `enqueue_task`).
- Ticks whose time is not strictly after "now" are skipped, so a mid-week manual run never dispatches a burst of past tasks.
- Do not modify `scripts/sync_live_scores.py` or `services/live_window_service.py`.
- Tests never touch the real `.local_db/` (handled by `tests/conftest.py`). Run with `pytest tests/test_schedule_kickoffs.py -v`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Work on a branch (e.g. `feat/live-scores-kickoff-tasks`), not on `main`.

## Review Focus

- Re-running the weekly job for the same week must not raise or double-enqueue (`AlreadyExists` path): covered in Task 3.
- Running the job mid-week (e.g. Sunday afternoon) must not enqueue ticks in the past: covered in Tasks 2 and 3.
- A late game crossing the November clock change (Sat night 2026-10-31, kickoff 22:00 ET, ends after 2 AM ET on 11-01): covered in Task 1 (UTC arithmetic).
- A week with no REG games for the requested (season, week) (bye or wrong week) returns zero windows/ticks and does not crash: covered in Tasks 1 and 3.
- Kickoffs exactly touching (window end == next window start) merge into one window: covered in Task 1.

---

### Task 1: Live windows from the week's kickoffs

**Files:**
- Modify: `scripts/schedule_kickoffs.py` (constants after line 91; new function after `compute_kickoff_clusters_with_games`, ~line 133)
- Test: `tests/test_schedule_kickoffs.py` (append)

**Interfaces:**
- Consumes: `compute_kickoff_clusters(games, season, week) -> list[datetime]` (existing; sorted, ET-aware datetimes of REG kickoffs).
- Produces: constants `LIVE_TICK_MINUTES = 10`, `LIVE_LEAD_MINUTES = 5`, `LIVE_TAIL_HOURS = 4`, `LIVE_JOB_NAME = "winspool-live-scores"`; function `compute_live_windows(games: pd.DataFrame, season: int, week: int) -> list[tuple[datetime, datetime]]` returning merged, sorted, UTC-aware `(start, end)` pairs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedule_kickoffs.py`:

```python
class TestComputeLiveWindows:
    def _game(self, day, time, week=2, season=2026, game_type="REG"):
        return {"season": season, "week": week, "game_type": game_type,
                "gameday": day, "gametime": time}

    def test_single_game_window_is_lead_to_tail_in_utc(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-17", "20:15")])  # Thu 20:15 EDT = 00:15Z Fri
        windows = compute_live_windows(games, 2026, 2)
        assert windows == [(
            datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 18, 4, 15, tzinfo=timezone.utc),
        )]

    def test_week_merges_overlapping_sunday_games_but_keeps_thu_and_mon_separate(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([
            self._game("2026-09-17", "20:15"),   # Thu night
            self._game("2026-09-20", "13:00"),   # Sun early
            self._game("2026-09-20", "13:00"),   # same cluster
            self._game("2026-09-20", "16:25"),   # Sun late
            self._game("2026-09-20", "20:20"),   # Sun night
            self._game("2026-09-21", "20:15"),   # Mon night
        ])
        windows = compute_live_windows(games, 2026, 2)
        assert windows == [
            (datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc), datetime(2026, 9, 18, 4, 15, tzinfo=timezone.utc)),
            (datetime(2026, 9, 20, 16, 55, tzinfo=timezone.utc), datetime(2026, 9, 21, 4, 20, tzinfo=timezone.utc)),
            (datetime(2026, 9, 22, 0, 10, tzinfo=timezone.utc), datetime(2026, 9, 22, 4, 15, tzinfo=timezone.utc)),
        ]

    def test_windows_that_exactly_touch_are_merged(self):
        from scripts.schedule_kickoffs import compute_live_windows
        # Kickoff A ends at A+4h; kickoff B starts at B-5min. B = A+4h+5min makes them touch.
        games = pd.DataFrame([self._game("2026-09-20", "13:00"), self._game("2026-09-20", "17:05")])
        assert len(compute_live_windows(games, 2026, 2)) == 1

    def test_clock_change_night_uses_utc_arithmetic(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import compute_live_windows
        # Sat 2026-10-31 22:00 EDT = 02:00Z 11-01; +4h = 06:00Z. Wall-clock ET math
        # would cross the 2 AM fall-back and be off by an hour.
        games = pd.DataFrame([self._game("2026-10-31", "22:00", week=9)])
        windows = compute_live_windows(games, 2026, 9)
        assert windows == [(
            datetime(2026, 11, 1, 1, 55, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
        )]

    def test_week_with_no_reg_games_returns_empty(self):
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-20", "13:00")])
        assert compute_live_windows(games, 2026, 3) == []

    def test_ignores_non_reg_games(self):
        from scripts.schedule_kickoffs import compute_live_windows
        games = pd.DataFrame([self._game("2026-09-20", "13:00", game_type="POST")])
        assert compute_live_windows(games, 2026, 2) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py::TestComputeLiveWindows -v`
Expected: FAIL with `ImportError: cannot import name 'compute_live_windows'`

- [ ] **Step 3: Write the implementation**

In `scripts/schedule_kickoffs.py`, add constants directly after `RESIMULATE_LEAD_MINUTES = 30` (line 91):

```python
# Live-score ticks (winspool-live-scores): one Cloud Task every
# LIVE_TICK_MINUTES inside each game window. A game contributes
# [kickoff - LIVE_LEAD_MINUTES, kickoff + LIVE_TAIL_HOURS]; overlapping windows
# merge. See docs/superpowers/specs/2026-09-30-live-scores-kickoff-tasks-design.md.
LIVE_JOB_NAME = "winspool-live-scores"
LIVE_TICK_MINUTES = 10
LIVE_LEAD_MINUTES = 5
LIVE_TAIL_HOURS = 4
```

Add this function after `compute_kickoff_clusters_with_games` (after line 133, before `_current_season_week`):

```python
def compute_live_windows(games: pd.DataFrame, season: int, week: int) -> list[tuple[datetime, datetime]]:
    """Merged UTC (start, end) live-score windows for (season, week) REG games.

    Each distinct kickoff yields [kickoff - LIVE_LEAD_MINUTES, kickoff +
    LIVE_TAIL_HOURS]; windows that overlap or touch are merged. All arithmetic
    is done in UTC: adding a timedelta to a ZoneInfo-aware datetime is
    wall-clock math, which is wrong across the November fall-back."""
    windows: list[tuple[datetime, datetime]] = []
    for kickoff in compute_kickoff_clusters(games, season, week):
        k = kickoff.astimezone(timezone.utc)
        start = k - timedelta(minutes=LIVE_LEAD_MINUTES)
        end = k + timedelta(hours=LIVE_TAIL_HOURS)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schedule_kickoffs.py::TestComputeLiveWindows -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py
git commit -m "feat: compute merged live-score windows from weekly kickoffs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Tick generation

**Files:**
- Modify: `scripts/schedule_kickoffs.py` (new functions after `compute_live_windows`)
- Test: `tests/test_schedule_kickoffs.py` (append)

**Interfaces:**
- Consumes: `LIVE_TICK_MINUTES` and `compute_live_windows(...)` from Task 1 (windows are `list[tuple[datetime, datetime]]`, UTC-aware).
- Produces: `live_ticks(windows: list[tuple[datetime, datetime]], now: datetime) -> list[datetime]` returning UTC-aware tick times that are on `LIVE_TICK_MINUTES` marks, within `[window start, window end]` inclusive, strictly after `now`, sorted ascending.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedule_kickoffs.py`:

```python
class TestLiveTicks:
    def _week_windows(self):
        from scripts.schedule_kickoffs import compute_live_windows
        rows = [("2026-09-17", "20:15"), ("2026-09-20", "13:00"), ("2026-09-20", "16:25"),
                ("2026-09-20", "20:20"), ("2026-09-21", "20:15")]
        games = pd.DataFrame([
            {"season": 2026, "week": 2, "game_type": "REG", "gameday": d, "gametime": t}
            for d, t in rows
        ])
        return compute_live_windows(games, 2026, 2)

    def test_full_week_tick_count_and_bounds(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        ticks = live_ticks(self._week_windows(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        # Thu 00:10-04:10 = 25, Sun 17:00-04:20 = 69, Mon 00:10-04:10 = 25
        assert len(ticks) == 119
        assert ticks[0] == datetime(2026, 9, 18, 0, 10, tzinfo=timezone.utc)
        assert ticks[-1] == datetime(2026, 9, 22, 4, 10, tzinfo=timezone.utc)
        assert ticks == sorted(ticks)
        assert len(set(ticks)) == len(ticks)

    def test_ticks_fall_on_interval_marks(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks, LIVE_TICK_MINUTES
        ticks = live_ticks(self._week_windows(), now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert all(t.minute % LIVE_TICK_MINUTES == 0 and t.second == 0 for t in ticks)

    def test_first_tick_is_first_mark_at_or_after_window_start(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        # Window 16:55-21:00Z: first mark at/after 16:55 is 17:00.
        window = [(datetime(2026, 9, 20, 16, 55, tzinfo=timezone.utc),
                   datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc))]
        ticks = live_ticks(window, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert ticks[0] == datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
        assert ticks[-1] == datetime(2026, 9, 20, 21, 0, tzinfo=timezone.utc)  # end is inclusive

    def test_skips_ticks_not_strictly_after_now(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        now = datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc)  # mid Sunday window
        ticks = live_ticks(self._week_windows(), now=now)
        assert all(t > now for t in ticks)
        assert datetime(2026, 9, 20, 23, 0, tzinfo=timezone.utc) not in ticks
        assert len(ticks) == 57

    def test_no_windows_means_no_ticks(self):
        from datetime import datetime, timezone
        from scripts.schedule_kickoffs import live_ticks
        assert live_ticks([], now=datetime(2026, 1, 1, tzinfo=timezone.utc)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py::TestLiveTicks -v`
Expected: FAIL with `ImportError: cannot import name 'live_ticks'`

- [ ] **Step 3: Write the implementation**

Add after `compute_live_windows` in `scripts/schedule_kickoffs.py`:

```python
def _ceil_to_tick(dt: datetime, minutes: int) -> datetime:
    """Smallest datetime >= dt whose minute is divisible by `minutes` and whose
    seconds/microseconds are zero."""
    base = dt.replace(second=0, microsecond=0)
    if base < dt:
        base += timedelta(minutes=1)
    remainder = base.minute % minutes
    return base if remainder == 0 else base + timedelta(minutes=minutes - remainder)


def live_ticks(windows: list[tuple[datetime, datetime]], now: datetime) -> list[datetime]:
    """Tick times (every LIVE_TICK_MINUTES, on interval marks, window end
    inclusive) strictly after `now`. Past ticks are dropped: a Cloud Task with
    a past schedule_time dispatches immediately, so a mid-week manual run would
    otherwise fire a burst of stale live-score runs."""
    ticks: list[datetime] = []
    step = timedelta(minutes=LIVE_TICK_MINUTES)
    for start, end in windows:
        tick = _ceil_to_tick(start, LIVE_TICK_MINUTES)
        while tick <= end:
            if tick > now:
                ticks.append(tick)
            tick += step
    return ticks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schedule_kickoffs.py::TestLiveTicks -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py
git commit -m "feat: generate 10-minute live-score ticks from windows

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Enqueue live ticks from the weekly job

**Files:**
- Modify: `scripts/schedule_kickoffs.py` (new helper after `enqueue_task`; wire into `main()` lines ~359-367)
- Test: `tests/test_schedule_kickoffs.py` (append)

**Interfaces:**
- Consumes: `compute_live_windows`, `live_ticks`, `LIVE_JOB_NAME` (Tasks 1-2); existing `enqueue_task(tasks_client, run_at: datetime, job_name: str, job_args: list = None) -> None` (already catches `AlreadyExists`, builds id `f"{job_name}-{run_at.strftime('%Y%m%dT%H%M')}"`).
- Produces: `enqueue_live_ticks(tasks_client, games: pd.DataFrame, season: int, week: int, now: datetime = None) -> int` returning the number of ticks passed to `enqueue_task`; `main()` calls it after the per-cluster loop and before the quarter-score/betting steps.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_schedule_kickoffs.py`:

```python
class TestEnqueueLiveTicks:
    def _games(self):
        rows = [("2026-09-20", "13:00"), ("2026-09-20", "16:25")]
        return pd.DataFrame([
            {"season": 2026, "week": 2, "game_type": "REG", "gameday": d, "gametime": t}
            for d, t in rows
        ])

    def test_enqueues_one_task_per_tick_for_live_scores_job(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        calls = []
        monkeypatch.setattr(sk, "enqueue_task",
                            lambda client, run_at, job_name, job_args=None: calls.append((run_at, job_name, job_args)))
        client = MagicMock()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        count = sk.enqueue_live_ticks(client, self._games(), 2026, 2, now=now)

        assert count == len(calls) > 0
        assert all(job == "winspool-live-scores" and args is None for _, job, args in calls)
        assert [c[0] for c in calls] == sorted(c[0] for c in calls)

    def test_mid_week_run_enqueues_no_past_ticks(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        calls = []
        monkeypatch.setattr(sk, "enqueue_task",
                            lambda client, run_at, job_name, job_args=None: calls.append(run_at))
        now = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)  # Sunday, mid-window

        sk.enqueue_live_ticks(MagicMock(), self._games(), 2026, 2, now=now)

        assert calls and all(t > now for t in calls)

    def test_no_games_enqueues_nothing(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        monkeypatch.setattr(sk, "enqueue_task", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not enqueue")))
        count = sk.enqueue_live_ticks(MagicMock(), self._games(), 2026, 3,
                                      now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert count == 0

    def test_rerun_hits_already_exists_without_raising(self, monkeypatch, capsys):
        """Same week enqueued twice (Scheduler retry / manual rerun): the real
        enqueue_task swallows AlreadyExists, so the helper must complete."""
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        from google.api_core.exceptions import AlreadyExists
        import scripts.schedule_kickoffs as sk

        monkeypatch.setattr(sk, "GCP_PROJECT", "test-project")
        monkeypatch.setattr(sk, "GCP_REGION", "us-east1")
        monkeypatch.setattr(sk, "GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr(sk, "GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")
        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        client.create_task.side_effect = AlreadyExists("duplicate task")

        count = sk.enqueue_live_ticks(client, self._games(), 2026, 2,
                                      now=datetime(2026, 1, 1, tzinfo=timezone.utc))

        assert count > 0
        assert "already enqueued" in capsys.readouterr().out

    def test_task_ids_are_deterministic_per_tick(self, monkeypatch):
        from datetime import datetime, timezone
        from unittest.mock import MagicMock
        import scripts.schedule_kickoffs as sk

        monkeypatch.setattr(sk, "GCP_PROJECT", "test-project")
        monkeypatch.setattr(sk, "GCP_REGION", "us-east1")
        monkeypatch.setattr(sk, "GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr(sk, "GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")
        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        sk.enqueue_live_ticks(client, self._games(), 2026, 2, now=now)
        first_names = [c.kwargs["request"]["task"]["name"] for c in client.create_task.call_args_list]
        client.create_task.reset_mock()
        sk.enqueue_live_ticks(client, self._games(), 2026, 2, now=now)
        second_names = [c.kwargs["request"]["task"]["name"] for c in client.create_task.call_args_list]

        assert first_names == second_names
        assert first_names[0].endswith("/tasks/winspool-live-scores-20260920T1700")
        assert len(set(first_names)) == len(first_names)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py::TestEnqueueLiveTicks -v`
Expected: FAIL with `AttributeError: ... has no attribute 'enqueue_live_ticks'`

- [ ] **Step 3: Write the implementation**

Add after `enqueue_task` in `scripts/schedule_kickoffs.py` (before `_sync_schedule_data`):

```python
def enqueue_live_ticks(tasks_client, games: pd.DataFrame, season: int, week: int,
                       now: datetime = None) -> int:
    """Enqueue one winspool-live-scores Cloud Task per live-score tick for
    (season, week). Returns the number of ticks handed to enqueue_task()
    (already-enqueued duplicates are skipped inside enqueue_task and still
    counted). `now` defaults to the current UTC time."""
    now = now or datetime.now(timezone.utc)
    ticks = live_ticks(compute_live_windows(games, season, week), now)
    for tick in ticks:
        enqueue_task(tasks_client, tick, LIVE_JOB_NAME)
    return len(ticks)
```

Wire into `main()`: after the existing `print(f"Enqueued {len(clusters_with_games)} kickoff cluster(s) x 3 tasks ...")` line and before `_run_quarter_scores_scrape(season, week)`, add:

```python
        live_count = enqueue_live_ticks(client, games, season, week)
        print(f"Enqueued {live_count} live-score tick(s) for {season} week {week}.")
```

Also update the module docstring (top of file, after the resimulate bullet) with one more paragraph:

```
Also enqueues one winspool-live-scores task every LIVE_TICK_MINUTES inside each
merged game window (kickoff - 5 min to kickoff + 4 h) -- see
enqueue_live_ticks(). The standing Cloud Scheduler trigger for that job is only
a slow every-30-minute (5,35) backstop.
```

- [ ] **Step 4: Run the full file's tests to verify everything passes**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: PASS (all prior tests plus 16 new ones)

- [ ] **Step 5: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py
git commit -m "feat: enqueue live-score Cloud Tasks from the weekly kickoff job

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Docs

**Files:**
- Modify: `CLAUDE.md` (Scheduled Jobs section: `winspool-live-scores` and `winspool-schedule-kickoffs` rows; CLI commands list comment for `schedule_kickoffs.py`)
- Modify: `docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md` only if it contains the literal text `*/2` (check with `grep -n "\*/2" <file>`; skip if absent)

**Interfaces:**
- Consumes: final behavior from Tasks 1-3.
- Produces: docs that match the code; no code interfaces.

- [ ] **Step 1: Update the `winspool-live-scores` row's Trigger cell**

In `CLAUDE.md`, replace the Trigger text that describes the `*/2` crons (`Every 2 min (Sept–Jan winspool-live-scores-trigger = */2 * * 9-12,1 * ... older image every run does full work.`) with:

```
Kickoff-aware Cloud Tasks enqueued weekly by `winspool-schedule-kickoffs` (one task every 10 min inside each game window, kickoff −5 min to +4 h, regular season only) plus a slow `5,35 * * 9-12,1 *` Cloud Scheduler backstop (`winspool-live-scores-trigger`). The Feb trigger is deleted. Still fast-exits outside a game window (`is_live_score_window_active()`); that check stays as a safety net. The container has no `rawdata/`, so the check does one small nflverse schedule GET and fails open on any error.
```

- [ ] **Step 2: Update the `winspool-schedule-kickoffs` row's "What it does" cell and the command-list comment**

In the "What it does" cell, after the sentence about the 3 Cloud Tasks per kickoff cluster, add: `It also enqueues the live-score ticks described above (enqueue_live_ticks(), deterministic task ids winspool-live-scores-<YYYYMMDDTHHMM>, past ticks skipped).` In the commands list, extend the `python scripts/schedule_kickoffs.py` comment with `, plus live-score ticks for winspool-live-scores`.

- [ ] **Step 3: Check the old spec and commit**

Run: `grep -n "\*/2" docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md`
If it prints lines, update them to say the live-scores trigger is now task-driven plus a `5,35` (every 30 min, offset from the 10-minute ticks) backstop; if nothing prints, skip.

```bash
git add CLAUDE.md docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md
git commit -m "docs: document kickoff-aware live-score scheduling

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

(If the old spec was unchanged, `git add` it harmlessly stages nothing.)

---

### Task 5: Rollout (manual infrastructure; run in this order)

**Files:** none. Production changes: user runs the deploy and the `gcloud` commands (production writes need explicit user action).

**Interfaces:**
- Consumes: Tasks 1-4 merged to `main` and deployed.
- Produces: tasks in the `winspool-kickoff-triggers` queue; live-scores Scheduler triggers changed to the backstop. Reversing steps 3 and 4 leaves a gap with no triggers.

- [ ] **Step 1: Pre-flight and deploy**

Run the full unit suite, then follow the `/deploy` flow (`.claude/commands/deploy.md`: tests, push, confirm, `.\deploy\deploy.ps1`). `deploy.ps1` rebuilds `winspool-sync`, which is the image `winspool-schedule-kickoffs` runs.

Run: `pytest tests/ -n auto`
Expected: PASS

- [ ] **Step 2: Run the weekly job once by hand so this week's tasks exist**

```powershell
gcloud run jobs execute winspool-schedule-kickoffs --region=us-east1 --project=fishbone-wins-pool --wait
```
Expected: execution succeeds; log line `Enqueued N live-score tick(s) for <season> week <week>.` (about 119 for a normal week; fewer if run mid-week, since past ticks are skipped).

- [ ] **Step 3: Verify the queue before touching Scheduler**

```powershell
gcloud tasks list --queue=winspool-kickoff-triggers --location=us-east1 --project=fishbone-wins-pool --format="table(name.basename(),scheduleTime)" | Select-String "winspool-live-scores" | Select-Object -First 15
```
Expected: `winspool-live-scores-<YYYYMMDDTHHMM>` tasks with `scheduleTime` on 10-minute marks, starting at or after the first kickoff minus 5 min (rounded up to the mark), and none in the past.

- [ ] **Step 4: Change the Scheduler triggers (only after Step 3 looks right)**

```powershell
gcloud scheduler jobs update http winspool-live-scores-trigger --project=fishbone-wins-pool --location=us-east1 --schedule="5,35 * * 9-12,1 *"
gcloud scheduler jobs delete winspool-live-scores-trigger-feb --project=fishbone-wins-pool --location=us-east1
```
Expected: first command prints the updated job with `schedule: '5,35 * * 9-12,1 *'`; second prompts for confirmation, then deletes.

- [ ] **Step 5: Verify behavior during the next games**

During a game window, count live-scores executions per hour; expect about 6 (one per 10 minutes), plus about 2 backstop runs. Check Cloud Monitoring `run.googleapis.com/job/completed_execution_count` for `winspool-live-scores`, or:

```powershell
gcloud run jobs executions list --job=winspool-live-scores --region=us-east1 --project=fishbone-wins-pool --limit=20 --format="table(status.startTime,status.succeededCount,status.failedCount)"
```
Confirm the live-scores 'is_live' fields update on the schedule page during a game, and compare the October invoice's `Jobs CPU` line to September's $9.27.

**Rollback:** `gcloud scheduler jobs update http winspool-live-scores-trigger --project=fishbone-wins-pool --location=us-east1 --schedule="*/2 * * 9-12,1 *"` restores the old cadence; queued ticks are harmless alongside it.
