# Scheduled Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace WinsPool's currently fully-manual data pipeline with real scheduled automation: daily raw-data refresh + predictions, dynamic pre-kickoff re-runs, live score updates, and failure alerting.

**Architecture:** Two new Cloud Run Job container images (`winspool-sync` — lean, no ML deps; `winspool-predict` — includes `requirements-ml.txt`) alongside the existing untouched `winspool` web Service. Four Cloud Scheduler-driven job entrypoints, one of which (`schedule_kickoffs.py`) dynamically enqueues Cloud Tasks for the other two at precise pre-kickoff times. All in-season only (two different windows — see spec).

**Tech Stack:** Python (existing stack), `google-cloud-tasks` (new dependency), Cloud Run Jobs, Cloud Scheduler, Cloud Tasks, Cloud Monitoring.

**Spec:** `docs/superpowers/specs/completed/2026-08-19-scheduled-jobs-design.md`

## Global Constraints

- All new Firestore writes must respect `USE_LOCAL_DATA` gotcha (CLAUDE.md): any script writing to Firestore must set `os.environ["USE_LOCAL_DATA"] = "False"` before importing `services.db_service`, matching the established pattern (`refresh_local_pkls.py`, `cache_builder.py`, `compute_elo.py --firestore`).
- `nfl_games` writes: full-row overwrites (via `batch_upload`, no merge) are fine for the authoritative games/standings refresh (matches existing `daily_nfl_sync.py` behavior). The ESPN cosmetic overlay (`is_live`/`clock`/`period`) MUST use a merge write (`doc_ref.set(data, merge=True)`), never `batch_upload`, or it will wipe out the rest of that game's document fields.
- No change to `nfl_standings`-driven pool-standings logic's data source (`compute_standings()` must keep reading from the games DataFrame it's passed — no dependency on `nfl_games` Firestore data for the authoritative win count).
- Every new script that can fail must call `send_alert_email()` on unhandled failure before exiting non-zero (per spec's alerting design) — this is what the Cloud Monitoring backstop policy complements, not replaces.

---

## Part 1: Application Code

### Task 1: `send_alert_email()` in `email_service.py`

**Files:**
- Modify: `services/email_service.py`
- Test: `tests/test_email_service.py`

**Interfaces:**
- Produces: `send_alert_email(subject: str, message: str) -> bool` — sends a plaintext-wrapped-in-`<pre>` alert email to the address in `ALERT_EMAIL` env var (falls back to `FROM_EMAIL`'s domain owner if unset — but for v1, require `ALERT_EMAIL` and return `False` with a log if missing, same pattern as the existing `RESEND_API_KEY` guard). Reuses the existing `_send()` helper.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_email_service.py`:

```python
from services.email_service import send_alert_email


@patch("services.email_service.resend.Emails.send")
@patch("services.email_service.os.getenv")
def test_send_alert_email_success(mock_getenv, mock_send):
    """Alert email sends to ALERT_EMAIL with subject/message in the body."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key", "ALERT_EMAIL": "alerts@x.com"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect
    mock_send.return_value = {"id": "alert123"}

    result = send_alert_email("winspool-sync-daily failed", "Step 'daily_nfl_sync.py' exited 1")

    assert result is True
    mock_send.assert_called_once()
    call_params = mock_send.call_args[0][0]
    assert call_params["to"] == ["alerts@x.com"]
    assert "winspool-sync-daily failed" == call_params["subject"]
    assert "daily_nfl_sync.py" in call_params["html"]


@patch("services.email_service.os.getenv")
def test_send_alert_email_aborts_without_alert_email(mock_getenv):
    """Returns False without sending when ALERT_EMAIL is not configured."""
    def getenv_side_effect(key, default=None):
        return {"RESEND_API_KEY": "re_test_key"}.get(key, default)
    mock_getenv.side_effect = getenv_side_effect

    result = send_alert_email("subject", "message")

    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_email_service.py -k alert -v`
Expected: FAIL with `ImportError: cannot import name 'send_alert_email'`

- [ ] **Step 3: Implement `send_alert_email()`**

Add to `services/email_service.py` (after `send_weekly_recap_email`):

```python
def send_alert_email(subject: str, message: str) -> bool:
    """Send a job-failure alert to the address in ALERT_EMAIL. Returns False (no-op) if unconfigured."""
    to_email = os.getenv("ALERT_EMAIL")
    if not to_email:
        logger.error("ALERT_EMAIL not set — alert email not sent. Subject: %s", subject)
        return False
    html = f"<p>{subject}</p><pre>{message}</pre>"
    return _send(to_email, subject, html)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_email_service.py -v`
Expected: all PASS (including the 4 pre-existing tests, unmodified)

- [ ] **Step 5: Commit**

```bash
git add services/email_service.py tests/test_email_service.py
git commit -m "feat: add send_alert_email for scheduled-job failure notifications"
```

---

### Task 2: Shared step-runner with alerting (`scripts/job_runner.py`)

Extracts `run_cron.py`'s existing `run_step()`/required-step semantics into a reusable module, adding the alert-email call on failure. `run_cron.py` becomes a consumer of it (Task 3), and the new `sync_live_scores.py`/`schedule_kickoffs.py` (Tasks 5-6) use it too — avoids three copies of the same "run subprocess, log output, decide whether to abort" logic.

**Files:**
- Create: `scripts/job_runner.py`
- Modify: `scripts/run_cron.py`
- Test: `tests/test_job_runner.py`

**Interfaces:**
- Produces:
  - `run_steps(steps: list[dict], job_name: str) -> bool` — runs each `{"name": str, "script": Path, "required": bool}` step via subprocess (same shape `run_cron.py` already uses), logs output, sends one alert email (via `email_service.send_alert_email`) summarizing all failed steps if any occurred, returns `True` iff no required step failed.
- Consumes: `services.email_service.send_alert_email` (Task 1)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_job_runner.py`:

```python
from unittest.mock import patch, MagicMock
import pathlib
from scripts.job_runner import run_steps


def _fake_result(returncode=0, stdout="ok", stderr=""):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_all_steps_succeed_no_alert(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=0)

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is True
    mock_alert.assert_not_called()


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_required_step_failure_sends_alert_and_returns_false(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=1, stderr="boom")

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is False
    mock_alert.assert_called_once()
    subject, message = mock_alert.call_args[0]
    assert "test-job" in subject
    assert "A" in message
    assert "boom" in message


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_non_required_step_failure_continues_without_alert(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=1, stderr="minor")

    ok = run_steps([{"name": "A", "script": script, "required": False}], job_name="test-job")

    assert ok is True
    mock_alert.assert_not_called()


@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run", side_effect=Exception("unexpected crash"))
def test_unhandled_exception_sends_alert_and_returns_false(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")

    ok = run_steps([{"name": "A", "script": script, "required": True}], job_name="test-job")

    assert ok is False
    mock_alert.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_job_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.job_runner'`

- [ ] **Step 3: Implement `scripts/job_runner.py`**

```python
"""scripts/job_runner.py -- Shared step-runner for scheduled Cloud Run Jobs.

Runs a list of steps as subprocesses (same shape run_cron.py originally
used), logs each step's output, and sends one alert email summarizing any
failures via services.email_service.send_alert_email -- the in-script half
of the two-layer alerting design (see docs/superpowers/specs/
2026-08-19-scheduled-jobs-design.md). The Cloud Monitoring alert policy on
job execution failure is the other half, catching crashes this code never
gets to run for (OOM, bad image, network down).
"""
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from services.email_service import send_alert_email


def _run_step(step: dict, log) -> tuple[bool, str]:
    """Returns (success, failure_detail). failure_detail is '' on success."""
    script = step["script"]
    name = step["name"]
    if not script.exists():
        detail = f"[{name}] Script not found: {script}"
        log(detail)
        return False, detail

    log(f"[{name}] Starting...")
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=str(script.parent.parent),
        )
    except Exception as e:
        detail = f"[{name}] Unhandled exception: {e}"
        log(detail)
        return False, detail

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log(f"  {line}")
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            log(f"  [stderr] {line}")

    if result.returncode != 0:
        detail = f"[{name}] FAILED with exit code {result.returncode}. stderr: {result.stderr.strip()[:500]}"
        log(detail)
        return False, detail

    log(f"[{name}] Complete")
    return True, ""


def run_steps(steps: list[dict], job_name: str, log=print) -> bool:
    """Run each step in order. Returns True iff no *required* step failed.

    A required-step failure (or any unhandled exception) sends one alert
    email summarizing all failures seen, via send_alert_email.
    """
    failures: list[str] = []
    any_required_failed = False

    for step in steps:
        try:
            ok, detail = _run_step(step, log)
        except Exception as e:
            ok, detail = False, f"[{step['name']}] job_runner crashed: {e}"

        if not ok:
            failures.append(detail)
            if step.get("required"):
                any_required_failed = True

    if failures and any_required_failed:
        send_alert_email(
            f"WinsPool job '{job_name}' failed",
            "\n\n".join(failures),
        )

    return not any_required_failed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_job_runner.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/job_runner.py tests/test_job_runner.py
git commit -m "feat: add shared step-runner with failure alerting for scheduled jobs"
```

---

### Task 3: Refactor `run_cron.py` into `winspool-sync-daily`'s entrypoint

Per the spec: this job runs `sync_nflverse_data.py` → `compute_elo.py --firestore` → `daily_nfl_sync.py`. `cache_builder.py` moves out (it needs the ML image — becomes `winspool-predict-daily`'s entrypoint in Task 4). Uses `job_runner.run_steps()` from Task 2 instead of its own duplicate step-runner.

**Files:**
- Modify: `scripts/run_cron.py`

**Interfaces:**
- Consumes: `scripts.job_runner.run_steps(steps, job_name)` (Task 2)

- [ ] **Step 1: Replace the STEPS list and step-runner with the shared one**

Rewrite `scripts/run_cron.py` to:

```python
#!/usr/bin/env python3
"""
run_cron.py — winspool-sync-daily Cloud Run Job entrypoint.

Runs:
  1. sync_nflverse_data.py — pulls rosters/depth_charts/injuries/snap_counts/
     pfr_advstats/schedules/stats_team from nflverse (default priority 3)
  2. compute_elo.py --firestore — recomputes Elo, pushes elo_history/{season}
  3. daily_nfl_sync.py — computes standings, pushes nfl_games + nfl_standings

Does NOT run cache_builder.py (prediction regen) -- that needs
requirements-ml.txt and runs as the separate winspool-predict-daily job
(scripts/cache_builder.py directly). See
docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md.

Schedule: Cloud Scheduler, ~9:00am UTC, Aug 1 - Feb 10 only.
"""
import sys
import pathlib
import logging
from datetime import datetime, timezone

LOG_DIR = pathlib.Path('logs')
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"cron_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

SCRIPTS_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR.parent))
from scripts.job_runner import run_steps

STEPS = [
    {
        'name': 'nflverse Raw Data Sync',
        'script': SCRIPTS_DIR / 'sync_nflverse_data.py',
        'required': False,  # Failure is non-fatal — rawdata/ may still be current
    },
    {
        'name': 'Elo Recompute + Firestore Push',
        'script': SCRIPTS_DIR / 'compute_elo.py',
        'required': False,  # Elo staleness doesn't block standings/predictions
    },
    {
        'name': 'NFL Data Sync (Firestore)',
        'script': SCRIPTS_DIR / 'daily_nfl_sync.py',
        'required': True,  # Standings must be current
    },
]


def main():
    log.info("=" * 60)
    log.info(f"winspool-sync-daily — {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 60)
    ok = run_steps(STEPS, job_name="winspool-sync-daily", log=log.info)
    if not ok:
        sys.exit(1)
    log.info("All steps complete.")


if __name__ == '__main__':
    main()
```

Note: `compute_elo.py` needs `--firestore` passed as an argument, but `job_runner.run_steps()`'s step shape only carries a script path, not args. Add `"args": ["--firestore"]` support to `job_runner._run_step()`'s subprocess call: change `[sys.executable, str(script)]` to `[sys.executable, str(script), *step.get("args", [])]`, and set `{'name': 'Elo Recompute + Firestore Push', 'script': SCRIPTS_DIR / 'compute_elo.py', 'args': ['--firestore'], 'required': False}` above.

- [ ] **Step 2: Update `job_runner.py` to support step args**

In `scripts/job_runner.py`'s `_run_step()`, change:
```python
        result = subprocess.run(
            [sys.executable, str(script)],
```
to:
```python
        result = subprocess.run(
            [sys.executable, str(script), *step.get("args", [])],
```

- [ ] **Step 3: Add a regression test for the args passthrough**

Add to `tests/test_job_runner.py`:

```python
@patch("scripts.job_runner.send_alert_email")
@patch("scripts.job_runner.subprocess.run")
def test_step_args_are_passed_to_subprocess(mock_run, mock_alert, tmp_path):
    script = tmp_path / "a.py"
    script.write_text("")
    mock_run.return_value = _fake_result(returncode=0)

    run_steps([{"name": "A", "script": script, "args": ["--firestore"], "required": True}], job_name="test-job")

    called_cmd = mock_run.call_args[0][0]
    assert "--firestore" in called_cmd
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_job_runner.py -v`
Expected: all 5 PASS

- [ ] **Step 5: Manual smoke test of `run_cron.py`**

Run: `python scripts/run_cron.py` (local dev env, `USE_LOCAL_DATA=True` is fine for this smoke test since it just needs to exercise the step sequence)
Expected: all 3 steps attempt to run in order (elo/daily_nfl_sync may fail locally without prod credentials — that's fine for a smoke test of the step *sequence*, not full behavior; full verification happens in Part 2's deployment testing)

- [ ] **Step 6: Commit**

```bash
git add scripts/run_cron.py scripts/job_runner.py tests/test_job_runner.py
git commit -m "refactor: run_cron.py becomes winspool-sync-daily entrypoint, adds compute_elo.py --firestore, drops cache_builder.py"
```

---

### Task 4: Alerting wrapper for `winspool-predict-daily` (`cache_builder.py`)

**Files:**
- Modify: `scripts/cache_builder.py`

**Interfaces:**
- Consumes: `services.email_service.send_alert_email` (Task 1)

`scripts/cache_builder.py` has `def main():` (argparse-driven: `--year`, `--force`) at line 328, and a bare `if __name__ == '__main__': main()` at lines 383-384. The `if __name__ == "__main__":` block itself can't be unit-tested directly (it only executes when the module is run as a script), so this task refactors it into a small, directly-testable wrapper function first.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_builder.py`:

```python
from unittest.mock import patch
import pytest
from scripts.cache_builder import _run_with_alerting


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main", side_effect=RuntimeError("model load failed"))
def test_main_failure_sends_alert_and_reraises(mock_main, mock_alert):
    with pytest.raises(RuntimeError):
        _run_with_alerting()

    mock_alert.assert_called_once()
    subject, message = mock_alert.call_args[0]
    assert "winspool-predict-daily" in subject
    assert "model load failed" in message


@patch("scripts.cache_builder.send_alert_email")
@patch("scripts.cache_builder.main")
def test_main_success_does_not_alert(mock_main, mock_alert):
    _run_with_alerting()
    mock_alert.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache_builder.py -v`
Expected: FAIL with `ImportError: cannot import name '_run_with_alerting'`

- [ ] **Step 3: Add the wrapper and import, replace the bare entrypoint**

At the top of `scripts/cache_builder.py` (alongside its existing imports), add:
```python
from services.email_service import send_alert_email
```

Replace lines 383-384 (`if __name__ == '__main__': main()`) with:

```python
def _run_with_alerting():
    try:
        main()
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-predict-daily' failed",
            f"cache_builder.py raised an unhandled exception:\n\n{traceback.format_exc()}",
        )
        raise


if __name__ == '__main__':
    _run_with_alerting()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder.py -v`
Expected: both PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "feat: alert on cache_builder.py (winspool-predict-daily) failure"
```

---

### Task 5: `scripts/sync_live_scores.py` (winspool-live-scores entrypoint)

The core new script. Two parts per the spec: (1) authoritative — re-sync schedules, re-run `compute_standings()`, push via `batch_upload` (full overwrite, same as `daily_nfl_sync.py`); (2) best-effort — ESPN overlay for `is_live`/`clock`/`period`, merge-write only, never touching authoritative score fields, and never writing those fields for a game nflverse has already marked final.

**Files:**
- Create: `scripts/sync_live_scores.py`
- Test: `tests/test_sync_live_scores.py`
- Modify: `scripts/daily_nfl_sync.py:88` (no code change — just confirming `compute_standings(games: pd.DataFrame) -> pd.DataFrame` stays a plain, importable, side-effect-free function; it already is)

**Interfaces:**
- Consumes:
  - `scripts.daily_nfl_sync.compute_standings(games: pd.DataFrame) -> pd.DataFrame`
  - `scripts.daily_nfl_sync.batch_upload(db, collection_name, dataframe, id_col=None) -> None`
  - `scripts.daily_nfl_sync.initialize_firebase() -> firestore.Client`
  - `services.live_score_service.get_live_updates() -> dict[tuple[str, str], dict]` (existing, unchanged)
  - `services.email_service.send_alert_email` (Task 1)
- Produces: nothing consumed elsewhere in this plan (this is a leaf script)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_live_scores.py`:

```python
import pandas as pd
from unittest.mock import patch, MagicMock
from scripts.sync_live_scores import overlay_espn_live_fields


def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_02_CIN_DET", "season": 2026, "week": 2, "game_type": "REG",
         "home_team": "DET", "away_team": "CIN", "home_score": 20.0, "away_score": 17.0,
         "result": 3.0},
        {"game_id": "2026_02_KC_BUF", "season": 2026, "week": 2, "game_type": "REG",
         "home_team": "BUF", "away_team": "KC", "home_score": None, "away_score": None,
         "result": None},
    ])


class TestOverlayEspnLiveFields:
    def test_no_live_games_writes_nothing(self):
        db = MagicMock()
        result = overlay_espn_live_fields(db, _games_df(), live_data={})
        db.collection.return_value.document.return_value.set.assert_not_called()
        assert result == 0

    def test_in_progress_game_writes_merge_update(self):
        db = MagicMock()
        live_data = {
            ("BUF", "KC"): {"home_score": 10, "away_score": 7, "status": "STATUS_IN_PROGRESS",
                             "clock": "5:23", "period": 2},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 1
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.set.assert_called_once()
        written_data, kwargs = doc_ref.set.call_args
        assert written_data[0] == {"is_live": True, "clock": "5:23", "period": 2}
        assert kwargs["merge"] is True

    def test_already_final_game_is_not_overwritten(self):
        """A game nflverse already marked final (has a non-null result) must not
        get its is_live/clock/period touched by ESPN data, even if ESPN still
        returns it (e.g. briefly after final whistle)."""
        db = MagicMock()
        live_data = {
            ("DET", "CIN"): {"home_score": 20, "away_score": 17, "status": "STATUS_FINAL",
                              "clock": "0:00", "period": 4},
        }
        result = overlay_espn_live_fields(db, _games_df(), live_data)

        assert result == 0
        db.collection.return_value.document.return_value.set.assert_not_called()

    def test_espn_failure_does_not_raise(self):
        """get_live_updates() raising must not propagate out of the overlay step."""
        db = MagicMock()
        with patch("scripts.sync_live_scores.get_live_updates", side_effect=Exception("ESPN down")):
            from scripts.sync_live_scores import run_espn_overlay_safely
            result = run_espn_overlay_safely(db, _games_df())
        assert result == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.sync_live_scores'`

- [ ] **Step 3: Implement `scripts/sync_live_scores.py`**

```python
"""scripts/sync_live_scores.py -- winspool-live-scores Cloud Run Job entrypoint.

Runs every 5 minutes, in-season (Sept 1 - Feb 10) only. Two parts:

1. Authoritative (must not fail silently -- this is what actually moves
   player win totals): re-pull rawdata/schedules/games.csv from nflverse
   (schedules only, priority 1 -- lightweight single file), re-run the same
   compute_standings() daily_nfl_sync.py uses, push nfl_standings + nfl_games
   via the same batch_upload() (full overwrite, same semantics as the daily
   sync). Intentionally does not depend on ESPN.

2. Best-effort (cosmetic only, must never affect wins or crash part 1):
   fetch ESPN's live scoreboard and merge-write only is_live/clock/period
   onto nfl_games documents that are NOT yet final per nflverse's own data --
   this is the "don't clobber a final score" guard the old
   sync_live_scores_to_df() docstring claimed but never actually
   implemented. Wrapped so any ESPN failure is silent-safe.

See docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md.
"""
import subprocess
import sys
import pathlib
import os
import traceback

os.environ["USE_LOCAL_DATA"] = "False"  # must be set before importing db_service (see CLAUDE.md gotcha)

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import compute_standings, batch_upload, initialize_firebase, load_games
from services.live_score_service import get_live_updates
from services.email_service import send_alert_email

SCRIPTS_DIR = pathlib.Path(__file__).parent


def sync_authoritative(db) -> pd.DataFrame:
    """Part 1: re-pull schedules, recompute standings, push nfl_games + nfl_standings.
    Returns the freshly-loaded games DataFrame for part 2 to reuse."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py"), "--priority", "1"],
        capture_output=True, text=True, timeout=120,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        raise RuntimeError(f"sync_nflverse_data.py --priority 1 failed: {result.stderr.strip()[:500]}")

    games = load_games()
    standings = compute_standings(games)
    batch_upload(db, "nfl_standings", standings)
    batch_upload(db, "nfl_games", games)
    return games


def overlay_espn_live_fields(db, games: pd.DataFrame, live_data: dict) -> int:
    """Merge-write is_live/clock/period onto not-yet-final nfl_games docs
    that ESPN reports on. Returns count of docs written."""
    if not live_data:
        return 0

    written = 0
    for _, row in games.iterrows():
        key = (row["home_team"], row["away_team"])
        update = live_data.get(key)
        if update is None:
            continue

        # Guard: nflverse's own data (result notna) already says this game is
        # final -- don't let ESPN's cosmetic fields touch it.
        if pd.notna(row.get("result")):
            continue

        db.collection("nfl_games").document(str(row["game_id"])).set(
            {
                "is_live": update["status"] == "STATUS_IN_PROGRESS",
                "clock": update.get("clock"),
                "period": update.get("period"),
            },
            merge=True,
        )
        written += 1

    return written


def run_espn_overlay_safely(db, games: pd.DataFrame) -> int:
    """Wraps the ESPN fetch + overlay so any failure here never propagates --
    this step is cosmetic-only, per the spec's must-not-affect-wins rule."""
    try:
        live_data = get_live_updates()
        return overlay_espn_live_fields(db, games, live_data)
    except Exception as e:
        print(f"[warn] ESPN live overlay failed (non-fatal): {e}")
        return 0


def main():
    db = initialize_firebase()
    try:
        games = sync_authoritative(db)
    except Exception:
        send_alert_email(
            "WinsPool job 'winspool-live-scores' failed",
            f"Authoritative sync failed:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)

    written = run_espn_overlay_safely(db, games)
    print(f"Live sync complete. ESPN overlay wrote {written} game(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_sync_live_scores.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/sync_live_scores.py tests/test_sync_live_scores.py
git commit -m "feat: add sync_live_scores.py (winspool-live-scores entrypoint)"
```

---

### Task 6: `scripts/schedule_kickoffs.py` (winspool-schedule-kickoffs entrypoint)

Weekly, in-season only. Reads the current week's `gameday`/`gametime`, computes distinct kickoff clusters, enqueues 2 Cloud Tasks per cluster (sync at kickoff−75min, predict at kickoff−60min) targeting the Cloud Run Jobs Admin API's `:run` endpoint for `winspool-sync-daily`/`winspool-predict-daily`.

**Files:**
- Create: `scripts/schedule_kickoffs.py`
- Test: `tests/test_schedule_kickoffs.py`
- Modify: `requirements.txt` (add `google-cloud-tasks`)

**Interfaces:**
- Produces: `compute_kickoff_clusters(games: pd.DataFrame, season: int, week: int) -> list[datetime]` — pure function, one entry per distinct kickoff time that week (deduplicated), for testability separate from the Cloud Tasks API calls.

- [ ] **Step 1: Add the new dependency**

Add to `requirements.txt`:
```
google-cloud-tasks>=2.16.0
```

- [ ] **Step 2: Write the failing tests for the pure scheduling logic**

Create `tests/test_schedule_kickoffs.py`:

```python
import pandas as pd
from datetime import datetime, timezone
from scripts.schedule_kickoffs import compute_kickoff_clusters


def _week_games():
    return pd.DataFrame([
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-17", "gametime": "20:15"},  # Thu night
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "13:00"},  # Sun early
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "13:00"},  # Sun early, same cluster
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "16:25"},  # Sun late
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-20", "gametime": "20:20"},  # Sun night
        {"season": 2026, "week": 2, "game_type": "REG", "gameday": "2026-09-21", "gametime": "20:15"},  # Mon night
    ])


class TestComputeKickoffClusters:
    def test_dedupes_same_day_same_time_games(self):
        clusters = compute_kickoff_clusters(_week_games(), season=2026, week=2)
        assert len(clusters) == 5  # Thu, Sun-early (deduped), Sun-late, Sun-night, Mon

    def test_filters_to_requested_season_and_week(self):
        games = pd.concat([
            _week_games(),
            pd.DataFrame([{"season": 2025, "week": 2, "game_type": "REG",
                            "gameday": "2025-09-18", "gametime": "20:15"}]),
        ], ignore_index=True)
        clusters = compute_kickoff_clusters(games, season=2026, week=2)
        assert all(c.year == 2026 for c in clusters)

    def test_ignores_non_reg_games(self):
        games = pd.concat([
            _week_games(),
            pd.DataFrame([{"season": 2026, "week": 2, "game_type": "POST",
                            "gameday": "2026-09-22", "gametime": "20:15"}]),
        ], ignore_index=True)
        clusters = compute_kickoff_clusters(games, season=2026, week=2)
        assert len(clusters) == 5  # POST game not counted

    def test_returns_timezone_aware_datetimes(self):
        clusters = compute_kickoff_clusters(_week_games(), season=2026, week=2)
        assert all(c.tzinfo is not None for c in clusters)


class TestCurrentSeasonWeek:
    def test_returns_earliest_upcoming_reg_game(self):
        from scripts.schedule_kickoffs import _current_season_week
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "result": 3.0},   # played
            {"season": 2026, "week": 2, "game_type": "REG", "result": None},  # upcoming
            {"season": 2026, "week": 3, "game_type": "REG", "result": None},  # further out
        ])
        assert _current_season_week(games) == (2026, 2)

    def test_ignores_non_reg_games(self):
        from scripts.schedule_kickoffs import _current_season_week
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "POST", "result": None},  # earlier but not REG
            {"season": 2026, "week": 2, "game_type": "REG", "result": None},
        ])
        assert _current_season_week(games) == (2026, 2)

    def test_raises_when_season_is_over(self):
        from scripts.schedule_kickoffs import _current_season_week
        import pytest
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "result": 3.0},
        ])
        with pytest.raises(ValueError):
            _current_season_week(games)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement `scripts/schedule_kickoffs.py`**

```python
"""scripts/schedule_kickoffs.py -- winspool-schedule-kickoffs Cloud Run Job entrypoint.

Runs weekly (Tue ~10am UTC), in-season only (Sept 1 - Feb 10). Reads the
upcoming week's actual gameday/gametime, computes distinct kickoff-time
clusters, and enqueues 2 Cloud Tasks per cluster:
  - winspool-sync-daily    at (kickoff - 75 min)
  - winspool-predict-daily at (kickoff - 60 min)

Cloud Tasks (not Cloud Scheduler) is used because it supports a specific
one-off future execution timestamp per task, whereas Cloud Scheduler is
built for recurring cron patterns. Each task's HTTP target hits the Cloud
Run Jobs Admin API's :run endpoint, authenticated via an OIDC token from a
service account with run.invoker on the target job.

See docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md.
"""
import os
import sys
import pathlib
from datetime import datetime, timedelta, timezone

os.environ["USE_LOCAL_DATA"] = "False"

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd

from scripts.daily_nfl_sync import load_games
from services.email_service import send_alert_email

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"

GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_REGION = os.environ.get("GCP_REGION", "us-east1")
GCP_TASKS_QUEUE = os.environ.get("GCP_TASKS_QUEUE", "winspool-kickoff-triggers")
GCP_SCHEDULER_SERVICE_ACCOUNT = os.environ.get("GCP_SCHEDULER_SERVICE_ACCOUNT")

SYNC_LEAD_MINUTES = 75
PREDICT_LEAD_MINUTES = 60

# NFL gametime is published in US/Eastern per nflverse convention.
_EASTERN = timezone(timedelta(hours=-4))  # EDT; DST-naive for the September-January season window


def compute_kickoff_clusters(games: pd.DataFrame, season: int, week: int) -> list[datetime]:
    """Distinct REG-season kickoff datetimes for (season, week), deduplicated."""
    wk = games[
        (games["season"] == season)
        & (games["week"] == week)
        & (games["game_type"] == "REG")
    ]
    seen: set[str] = set()
    clusters: list[datetime] = []
    for _, row in wk.iterrows():
        key = f"{row['gameday']} {row['gametime']}"
        if key in seen:
            continue
        seen.add(key)
        dt = datetime.strptime(key, "%Y-%m-%d %H:%M").replace(tzinfo=_EASTERN)
        clusters.append(dt)
    return sorted(clusters)


def _current_season_week(games: pd.DataFrame) -> tuple[int, int]:
    """The next upcoming REG-season week: the (season, week) of the earliest
    not-yet-played REG game (result is null). Deliberately NOT
    services.data_service.get_active_season()/get_latest_week_for_year() --
    those find the latest *completed* week (for the standings page,
    retrospective), which is the opposite of what pre-kickoff scheduling
    needs (the next upcoming week, prospective)."""
    upcoming = games[
        (games["game_type"] == "REG") & games["result"].isna()
    ].sort_values(["season", "week"])
    if upcoming.empty:
        raise ValueError("No upcoming REG games found — season may be over")
    row = upcoming.iloc[0]
    return int(row["season"]), int(row["week"])


def _run_url(job_name: str) -> str:
    return (
        f"https://{GCP_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/"
        f"namespaces/{GCP_PROJECT}/jobs/{job_name}:run"
    )


def enqueue_task(tasks_client, run_at: datetime, job_name: str) -> None:
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2

    parent = tasks_client.queue_path(GCP_PROJECT, GCP_REGION, GCP_TASKS_QUEUE)
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(run_at.astimezone(timezone.utc))

    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": _run_url(job_name),
            "oidc_token": {"service_account_email": GCP_SCHEDULER_SERVICE_ACCOUNT},
        },
        "schedule_time": ts,
    }
    tasks_client.create_task(request={"parent": parent, "task": task})


def main():
    from google.cloud import tasks_v2

    try:
        games = load_games()
        season, week = _current_season_week(games)
        clusters = compute_kickoff_clusters(games, season, week)

        client = tasks_v2.CloudTasksClient()
        for kickoff in clusters:
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")

        print(f"Enqueued {len(clusters)} kickoff cluster(s) x 2 tasks for {season} week {week}.")
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-schedule-kickoffs' failed",
            f"Dynamic kickoff scheduling failed -- this week falls back to the "
            f"fixed daily baseline only:\n\n{traceback.format_exc()}",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
```

Note: `services.data_service.get_active_season()`/`get_latest_week_for_year()` were deliberately **not** reused here — those find the latest *completed* week (for the standings/leaderboard pages, retrospective), which is the opposite of what pre-kickoff scheduling needs (the next *upcoming* week, prospective). `_current_season_week()` above is a new, small, purpose-built function instead.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: all 7 PASS (these test `compute_kickoff_clusters`/`_current_season_week`, which have no GCP dependency — `enqueue_task`/`main` are exercised in Part 2's deployment verification, not unit-tested here since they need live GCP credentials)

- [ ] **Step 6: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py requirements.txt
git commit -m "feat: add schedule_kickoffs.py (dynamic pre-kickoff Cloud Tasks scheduling)"
```

---

## Part 2: Deployment Infrastructure

These tasks configure real GCP resources. Each has a concrete verification step but isn't unit-testable the way Part 1 is — treat each checkbox as "run this, then confirm the stated result before moving on."

### Task 7: `Dockerfile.sync` (lean image)

**Files:**
- Create: `Dockerfile.sync`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# winspool-sync: lean image for scheduled jobs that don't need ML deps
# (run_cron.py / sync_live_scores.py / schedule_kickoffs.py / compute_elo.py)
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV USE_LOCAL_DATA False
```

(No `CMD` — Cloud Run Jobs specify the command per-job at deploy time, not baked into the image, so one image serves `winspool-sync-daily`, `winspool-live-scores`, and `winspool-schedule-kickoffs` with different `--command`/`--args` overrides.)

- [ ] **Step 2: Build and verify locally**

Run: `docker build -f Dockerfile.sync -t winspool-sync:local .`
Expected: builds successfully

Run: `docker run --rm winspool-sync:local python scripts/run_cron.py --help 2>&1 | head -5` (or just verify the script imports without error, e.g. `docker run --rm winspool-sync:local python -c "import scripts.run_cron"`)
Expected: no import errors

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.sync
git commit -m "feat: add Dockerfile.sync for lean scheduled-job image"
```

---

### Task 8: `Dockerfile.predict` (ML image)

**Files:**
- Create: `Dockerfile.predict`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# winspool-predict: ML-capable image for prediction regeneration only
# (cache_builder.py)
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-ml.txt

COPY . .

ENV USE_LOCAL_DATA False
```

- [ ] **Step 2: Build and verify locally**

Run: `docker build -f Dockerfile.predict -t winspool-predict:local .`
Expected: builds successfully (will take noticeably longer than `Dockerfile.sync` — TensorFlow install)

Run: `docker run --rm winspool-predict:local python -c "import scripts.cache_builder"`
Expected: no import errors (confirms TF/XGB/sklearn imports resolve)

- [ ] **Step 3: Commit**

```bash
git add Dockerfile.predict
git commit -m "feat: add Dockerfile.predict for ML-capable prediction-regen image"
```

---

### Task 9: GCP infrastructure setup

Everything needed to actually run these on a schedule: Artifact Registry images, service account + IAM, Cloud Run Jobs, Cloud Tasks queue, Cloud Scheduler jobs, Cloud Monitoring alert policy. Requires `gcloud` authenticated against the `fishbone-wins-pool` project (already confirmed working earlier this session).

**Files:**
- None (infrastructure-only; optionally document the final command sequence in `DEPLOY.md` as a follow-up, not required for this task)

- [ ] **Step 1: Enable required APIs**

```bash
gcloud services enable cloudscheduler.googleapis.com cloudtasks.googleapis.com \
  run.googleapis.com artifactregistry.googleapis.com monitoring.googleapis.com \
  --project=fishbone-wins-pool
```
Expected: each API enables without error (Cloud Scheduler was confirmed disabled earlier this session — this is the fix for that).

- [ ] **Step 2: Build and push both images to Artifact Registry**

```bash
gcloud builds submit --tag us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-sync:latest -f Dockerfile.sync .
gcloud builds submit --tag us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-predict:latest -f Dockerfile.predict .
```
(Adjust the Artifact Registry repo path/region if `winspool` isn't the existing repo name — check `gcloud artifacts repositories list --project=fishbone-wins-pool` first, reuse whatever the existing web service already deploys to.)
Expected: both builds succeed; confirm with `gcloud artifacts docker images list us-east1-docker.pkg.dev/fishbone-wins-pool/winspool`

- [ ] **Step 3: Create a dedicated service account for job invocation**

```bash
gcloud iam service-accounts create winspool-scheduler \
  --display-name="WinsPool Cloud Scheduler/Tasks job invoker" \
  --project=fishbone-wins-pool
```
Expected: service account `winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com` created.

- [ ] **Step 4: Create the four Cloud Run Jobs**

```bash
gcloud run jobs create winspool-sync-daily \
  --image=us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-sync:latest \
  --command=python --args=scripts/run_cron.py \
  --set-secrets=FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest \
  --set-env-vars=ALERT_EMAIL=fischerthomasg@gmail.com \
  --region=us-east1 --project=fishbone-wins-pool

gcloud run jobs create winspool-predict-daily \
  --image=us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-predict:latest \
  --command=python --args=scripts/cache_builder.py \
  --set-secrets=FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest \
  --set-env-vars=ALERT_EMAIL=fischerthomasg@gmail.com \
  --memory=2Gi --cpu=2 \
  --region=us-east1 --project=fishbone-wins-pool

gcloud run jobs create winspool-live-scores \
  --image=us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-sync:latest \
  --command=python --args=scripts/sync_live_scores.py \
  --set-secrets=FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest \
  --set-env-vars=ALERT_EMAIL=fischerthomasg@gmail.com \
  --region=us-east1 --project=fishbone-wins-pool

gcloud run jobs create winspool-schedule-kickoffs \
  --image=us-east1-docker.pkg.dev/fishbone-wins-pool/winspool/winspool-sync:latest \
  --command=python --args=scripts/schedule_kickoffs.py \
  --set-secrets=FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest,RESEND_API_KEY=RESEND_API_KEY:latest \
  --set-env-vars=ALERT_EMAIL=fischerthomasg@gmail.com,GCP_PROJECT=fishbone-wins-pool,GCP_REGION=us-east1,GCP_TASKS_QUEUE=winspool-kickoff-triggers,GCP_SCHEDULER_SERVICE_ACCOUNT=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --region=us-east1 --project=fishbone-wins-pool
```
(`--set-secrets` assumes `FIREBASE_CREDENTIALS`/`RESEND_API_KEY` are already in Secret Manager, matching however the existing `winspool` web service gets them today — check `gcloud run services describe winspool --region=us-east1` for the exact existing secret names/mounting approach and mirror it rather than assuming.)

Expected: `gcloud run jobs list --region=us-east1 --project=fishbone-wins-pool` shows all four.

- [ ] **Step 5: Grant the service account run.invoker on all four jobs**

```bash
for job in winspool-sync-daily winspool-predict-daily winspool-live-scores winspool-schedule-kickoffs; do
  gcloud run jobs add-iam-policy-binding $job \
    --member="serviceAccount:winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com" \
    --role="roles/run.invoker" \
    --region=us-east1 --project=fishbone-wins-pool
done
```
Expected: no errors; confirm with `gcloud run jobs get-iam-policy winspool-sync-daily --region=us-east1`

- [ ] **Step 6: Create the Cloud Tasks queue**

```bash
gcloud tasks queues create winspool-kickoff-triggers \
  --location=us-east1 --project=fishbone-wins-pool
```
Expected: `gcloud tasks queues list --location=us-east1` shows it.

- [ ] **Step 7: Create the four Cloud Scheduler jobs**

```bash
# winspool-sync-daily: 9:00am UTC, Aug 1 - Feb 10
gcloud scheduler jobs create http winspool-sync-daily-trigger \
  --schedule="0 9 1-10,15-28 8-9,11-12 *;0 9 * 1,2 *" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-sync-daily:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" \
  --location=us-east1 --project=fishbone-wins-pool
```

Note on the cron expression above: standard 5-field cron cannot natively express "Aug 1 through Feb 10" as one range (it wraps across year-end). During implementation, verify whether Cloud Scheduler's cron parser (it's unix-cron based) supports comma-separated month lists the way shown, or whether this needs to be two separate Scheduler jobs (one `0 9 * 8-12 *` for Aug-Dec, one `0 9 1-10 1-2 *` for Jan 1 - Feb 10) — the two-job split is the safer, more obviously-correct choice if there's any doubt; prefer it over a clever single expression. Apply the same pattern (adjusted for each job's actual cadence) to the remaining three:

```bash
# winspool-predict-daily: 9:15am UTC, Aug 1 - Feb 10 (same month-window caveat as above)
gcloud scheduler jobs create http winspool-predict-daily-trigger \
  --schedule="15 9 * 8-12 *" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-predict-daily:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" --location=us-east1 --project=fishbone-wins-pool
# + a second job for Jan 1 - Feb 10, mirroring winspool-sync-daily-trigger's split

# winspool-schedule-kickoffs: weekly Tue 10am UTC, Sept 1 - Feb 10
gcloud scheduler jobs create http winspool-schedule-kickoffs-trigger \
  --schedule="0 10 * 9-12 2" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-schedule-kickoffs:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" --location=us-east1 --project=fishbone-wins-pool
# + a second job for the Jan-Feb 10 tail

# winspool-live-scores: every 5 min, Sept 1 - Feb 10
gcloud scheduler jobs create http winspool-live-scores-trigger \
  --schedule="*/5 * * 9-12 *" \
  --uri="https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/fishbone-wins-pool/jobs/winspool-live-scores:run" \
  --http-method=POST \
  --oauth-service-account-email=winspool-scheduler@fishbone-wins-pool.iam.gserviceaccount.com \
  --time-zone="UTC" --location=us-east1 --project=fishbone-wins-pool
# + a second job for the Jan-Feb 10 tail
```

Expected: `gcloud scheduler jobs list --location=us-east1 --project=fishbone-wins-pool` shows all created jobs (4 or up to 8 if split for the year-wrap, per the note above), all state `ENABLED`.

- [ ] **Step 8: Create the Cloud Monitoring alert policy**

```bash
gcloud alpha monitoring channels create \
  --display-name="WinsPool job failure alerts" \
  --type=email \
  --channel-labels=email_address=fischerthomasg@gmail.com \
  --project=fishbone-wins-pool
```
Note the returned channel ID, then:
```bash
gcloud alpha monitoring policies create \
  --display-name="WinsPool Cloud Run Job execution failure" \
  --condition-display-name="Job execution failed" \
  --condition-filter='resource.type="cloud_run_job" AND metric.type="run.googleapis.com/job/completed_execution_count" AND metric.labels.result="failed"' \
  --notification-channels=<CHANNEL_ID from above> \
  --project=fishbone-wins-pool
```
(Exact metric filter syntax should be verified against current Cloud Monitoring docs during implementation — Cloud Run Job execution metrics have changed shape across GCP releases; this is the general shape, not a copy-paste guarantee.)

Expected: `gcloud alpha monitoring policies list --project=fishbone-wins-pool` shows the new policy, state `ENABLED`.

- [ ] **Step 9: Commit** (if any of the above got documented into a script/DEPLOY.md addition)

```bash
git add DEPLOY.md  # if updated
git commit -m "docs: document scheduled-jobs GCP setup commands"
```

---

### Task 10: End-to-end verification

- [ ] **Step 1: Manually trigger each Cloud Run Job once**

```bash
gcloud run jobs execute winspool-sync-daily --region=us-east1 --project=fishbone-wins-pool
gcloud run jobs execute winspool-predict-daily --region=us-east1 --project=fishbone-wins-pool
gcloud run jobs execute winspool-live-scores --region=us-east1 --project=fishbone-wins-pool
gcloud run jobs execute winspool-schedule-kickoffs --region=us-east1 --project=fishbone-wins-pool
```
Expected: each completes with `Execution ... has successfully completed` (or, for `winspool-schedule-kickoffs`, confirm via `gcloud tasks list --queue=winspool-kickoff-triggers --location=us-east1` that tasks got enqueued).

- [ ] **Step 2: Verify Firestore side effects**

Read-only checks (mirror the verification pattern used earlier this session for `compute_elo.py --firestore`):
- `elo_history` collection has a fresh `season` doc for the current season (from `winspool-sync-daily`'s `compute_elo.py --firestore` step)
- `nfl_standings`/`nfl_games` have current data
- `preseason_predictions` (or wherever `cache_builder.py` writes predictions) is fresh

- [ ] **Step 3: Verify the frontend live badge**

During an actual in-progress game (or by manually seeding a test `nfl_games` doc with `is_live: true` in a scratch environment), confirm `ui_renderer.js`'s "LIVE - Q{period} {clock}" badge renders on `/schedule` — this is the dead frontend code from the spec coming alive for the first time.

- [ ] **Step 4: Deliberately break one step and confirm the alert email arrives**

E.g., temporarily point `winspool-sync-daily`'s `daily_nfl_sync.py` step at a nonexistent script path, execute the job, confirm an email lands at `ALERT_EMAIL` within a few minutes. Revert afterward.

- [ ] **Step 5: Commit** (none — verification only, no code changes expected unless Step 4 surfaces a bug)
