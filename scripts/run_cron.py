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
        'args': ['--firestore'],
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
