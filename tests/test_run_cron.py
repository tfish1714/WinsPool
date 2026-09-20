"""scripts/run_cron.py -- CURRENT_SEASON's September 1st rollover boundary,
and its wiring into the "Elo Recompute + Firestore Push" step's args.

CURRENT_SEASON is computed once at module-import time from datetime.date.today().
To control that value in a test we patch datetime.date itself (not
scripts.run_cron.date, which `from datetime import date` would re-bind to the
real class on every reload) and force a reload so the constant recomputes."""
import importlib
from datetime import date
from unittest.mock import patch

import scripts.run_cron as run_cron_mod


def _reload_with_frozen_today(frozen_date):
    with patch("datetime.date") as mock_date_cls:
        mock_date_cls.today.return_value = frozen_date
        importlib.reload(run_cron_mod)
    return run_cron_mod


def test_current_season_is_prior_year_in_august():
    """Aug 31 is still last season -- the new season hasn't started yet."""
    try:
        mod = _reload_with_frozen_today(date(2026, 8, 31))
        assert mod.CURRENT_SEASON == 2025
    finally:
        importlib.reload(run_cron_mod)  # restore real date.today() for later tests


def test_current_season_rolls_over_on_september_first():
    """Sept 1 is the first day of the new season."""
    try:
        mod = _reload_with_frozen_today(date(2026, 9, 1))
        assert mod.CURRENT_SEASON == 2026
    finally:
        importlib.reload(run_cron_mod)


def test_current_season_stays_same_year_through_january():
    """A season that started in Sept 2026 is still "2026" the following January."""
    try:
        mod = _reload_with_frozen_today(date(2027, 1, 15))
        assert mod.CURRENT_SEASON == 2026
    finally:
        importlib.reload(run_cron_mod)


def test_elo_step_args_use_the_computed_current_season():
    """CURRENT_SEASON must actually be threaded through to the subprocess
    args job_runner.run_steps() passes to compute_elo.py -- not just exist
    as an unused module-level constant."""
    try:
        mod = _reload_with_frozen_today(date(2026, 9, 1))
        by_name = {s["name"]: s for s in mod.STEPS}
        assert by_name["Elo Recompute + Firestore Push"]["args"] == \
            ["--firestore", "--max-season", "2026"]
    finally:
        importlib.reload(run_cron_mod)
