#!/usr/bin/env python3
"""
run_cron.py — Master cron job for WinsPool analytics pipeline.

Runs:
  1. daily_nfl_sync.py  — fetches latest NFL game/standings data from GitHub → Firestore
  2. cache_builder.py   — reads raw Firestore data → computes analytics → writes to analytics_cache

Schedule recommendation:
  - Run nightly at ~2am ET to capture Monday Night Football results
  - Can also be triggered manually: python scripts/run_cron.py

Windows Task Scheduler setup:
  - Program: python
  - Arguments: G:\\path\\to\\WinsPool\\scripts\\run_cron.py
  - Start in: G:\\path\\to\\WinsPool
"""
import subprocess
import sys
import pathlib
import logging
from datetime import datetime, timezone

# ── Logging setup ────────────────────────────────────────────
LOG_DIR = pathlib.Path('logs')
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"cron_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)

SCRIPTS_DIR = pathlib.Path(__file__).parent

STEPS = [
    {
        'name': 'NFL Data Sync',
        'script': SCRIPTS_DIR / 'daily_nfl_sync.py',
        'required': True,   # If this fails, skip cache build (no new data)
    },
    {
        'name': 'Analytics Cache Build',
        'script': SCRIPTS_DIR / 'cache_builder.py',
        'required': False,  # Cache failure is not fatal — app falls back to direct compute
    },
]


def run_step(step: dict) -> bool:
    script = step['script']
    name = step['name']
    if not script.exists():
        log.warning(f"[{name}] Script not found: {script}  — skipping")
        return False

    log.info(f"[{name}] Starting...")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(SCRIPTS_DIR.parent),  # Run from project root
    )
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            log.info(f"  {line}")
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            log.warning(f"  [stderr] {line}")

    if result.returncode != 0:
        log.error(f"[{name}] FAILED with exit code {result.returncode}")
        return False

    log.info(f"[{name}] Complete ✓")
    return True


def main():
    log.info("=" * 60)
    log.info(f"WinsPool Cron Job — {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 60)

    for step in STEPS:
        success = run_step(step)
        if not success and step.get('required'):
            log.error(f"Required step '{step['name']}' failed. Aborting pipeline.")
            sys.exit(1)

    log.info("All cron steps complete.")


if __name__ == '__main__':
    main()
