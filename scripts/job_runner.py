"""scripts/job_runner.py -- Shared step-runner for scheduled Cloud Run Jobs.

Runs a list of steps as subprocesses (same shape run_cron.py originally
used), logs each step's output, and sends one alert email summarizing any
failures via services.email_service.send_alert_email -- the in-script half
of the two-layer alerting design (see docs/superpowers/specs/completed/
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
            [sys.executable, str(script), *step.get("args", [])],
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
