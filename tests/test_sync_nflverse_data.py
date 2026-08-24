"""Regression test for sync_nflverse_data.py's default season-range bug.

CURRENT_SEASON is deliberately gated to September (the month real games
start) -- correct for anything that needs completed game results, but
sync_nflverse_data.py's own default sync window used to be just
[CURRENT_SEASON - 1, CURRENT_SEASON], which meant winspool-predict-daily's
no-args sync call never attempted the upcoming season's rosters/depth_charts/
etc. from ~January through August -- silently starving
compute_preseason_player_profiles() (which returns {} outright when that
season's roster/depth_chart files are missing) for the entire preseason
draft window. This is exactly what caused the 2026 mock draft's win
projections to collapse to a near-flat distribution.
"""
import sys
from unittest.mock import patch

import pytest


def test_default_season_range_includes_upcoming_season():
    import scripts.sync_nflverse_data as sync_mod

    with patch.object(sys, "argv", ["sync_nflverse_data.py"]), \
         patch("scripts.sync_nflverse_data.sync", return_value=({}, 0, 0, 0)) as mock_sync, \
         patch("scripts.sync_nflverse_data.save_metadata"), \
         pytest.raises(SystemExit) as exc_info:
        sync_mod.main()
    assert exc_info.value.code == 0

    called_seasons = mock_sync.call_args.kwargs["seasons"]
    expected = [sync_mod.CURRENT_SEASON - 1, sync_mod.CURRENT_SEASON, sync_mod.CURRENT_SEASON + 1]
    assert called_seasons == expected, (
        f"Default sync must include CURRENT_SEASON+1 (the upcoming season's "
        f"rosters/depth_charts/etc. are published well before its games start) "
        f"-- got {called_seasons}, expected {expected}"
    )


def test_explicit_seasons_flag_still_overrides_default():
    """--seasons must still take priority over the default range."""
    import scripts.sync_nflverse_data as sync_mod

    with patch.object(sys, "argv", ["sync_nflverse_data.py", "--seasons", "2020", "2022"]), \
         patch("scripts.sync_nflverse_data.sync", return_value=({}, 0, 0, 0)) as mock_sync, \
         patch("scripts.sync_nflverse_data.save_metadata"), \
         pytest.raises(SystemExit):
        sync_mod.main()

    assert mock_sync.call_args.kwargs["seasons"] == [2020, 2021, 2022]


def test_explicit_season_flag_still_overrides_default():
    """--season (singular) must still sync exactly that one season."""
    import scripts.sync_nflverse_data as sync_mod

    with patch.object(sys, "argv", ["sync_nflverse_data.py", "--season", "2019"]), \
         patch("scripts.sync_nflverse_data.sync", return_value=({}, 0, 0, 0)) as mock_sync, \
         patch("scripts.sync_nflverse_data.save_metadata"), \
         pytest.raises(SystemExit):
        sync_mod.main()

    assert mock_sync.call_args.kwargs["seasons"] == [2019]
