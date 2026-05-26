"""Unit tests for the redesigned nn_feature_engine feature set."""
import numpy as np
import pandas as pd

from services.nn_feature_engine import FEATURE_COLUMNS


EXPECTED_FEATURES = [
    # Elo (2)
    "elo_diff", "elo_confidence",
    # EPA matchup (3)
    "pass_epa_matchup", "rush_epa_matchup", "early_down_matchup",
    # Ball-control (2)
    "turnover_margin_rolling", "net_success_rate",
    # Score margin (1)
    "point_diff_advantage",
    # Game context (5)
    "market_implied_team_total", "passing_difficulty_index",
    "rest_advantage", "net_travel_disadvantage",
    "trench_dominance_metric",
    # Pressure (2)
    "qb_pressure_advantage", "def_pressure_diff",
    # QB health (2)
    "home_qb_injury_flag", "away_qb_injury_flag",
    # Roster Value (5)
    "roster_talent_delta",
    "off_roster_value_delta", "def_roster_value_delta",
    "st_value_delta", "qb_resilience_delta",
    # Contextual (5)
    "home_field_advantage",
    "div_game_flag", "surface_type", "playoff_flag", "week",
]

DROPPED_FEATURES = [
    "tm_elo_pre", "opp_elo_pre",
    "off_pass_epa", "def_pass_epa", "off_rush_epa", "def_rush_epa",
    "early_down_pass_epa",
    "tm_point_diff", "opp_point_diff",
    "qb_pressure_rate", "opp_qb_pressure_rate",
    "def_pressure_gen", "opp_def_pressure_gen",
    "qb_injury_flag",
    "home_flag",
    "is_dome_flag",
    "travel_rest_disadvantage",
]


def test_feature_columns_exact_count():
    assert len(FEATURE_COLUMNS) == 27, f"Expected 27, got {len(FEATURE_COLUMNS)}"


def test_feature_columns_exact_list():
    assert FEATURE_COLUMNS == EXPECTED_FEATURES, (
        f"Missing: {set(EXPECTED_FEATURES) - set(FEATURE_COLUMNS)}\n"
        f"Extra:   {set(FEATURE_COLUMNS) - set(EXPECTED_FEATURES)}"
    )


def test_no_dropped_features_remain():
    for f in DROPPED_FEATURES:
        assert f not in FEATURE_COLUMNS, f"Dropped feature still present: {f}"


from services.nn_feature_engine import _load_rolling_epa
from pathlib import Path


def _make_stats_df():
    """Two teams, three weeks of stats — used across multiple test functions."""
    rows = []
    for season in [2023, 2024]:
        for week in [1, 2, 3]:
            rows.append({"season": season, "week": week, "team": "KC",
                         "season_type": "REG",
                         "passing_epa": 20.0, "attempts": 30,
                         "rushing_epa": 5.0, "carries": 20,
                         "rushing_yards": 80.0, "passing_cpoe": 3.0})
            rows.append({"season": season, "week": week, "team": "BUF",
                         "season_type": "REG",
                         "passing_epa": 10.0, "attempts": 28,
                         "rushing_epa": 2.0, "carries": 18,
                         "rushing_yards": 60.0, "passing_cpoe": 1.0})
    return pd.DataFrame(rows)


def _make_schedule_df():
    rows = []
    for season in [2023, 2024]:
        for week in [1, 2, 3]:
            rows.append({"season": season, "week": week,
                         "home_team": "KC", "away_team": "BUF",
                         "game_type": "REG", "location": "Home"})
    return pd.DataFrame(rows)


def test_rolling_epa_returns_8_roll_columns(tmp_path):
    """_load_rolling_epa returns exactly 8 rolling columns + season/week/team."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir()
    _make_schedule_df().to_csv(sched_dir / "games.csv", index=False)

    result = _load_rolling_epa(tmp_path)
    expected_cols = {
        "season", "week", "team",
        "off_pass_epa_roll", "off_rush_epa_roll", "off_early_down_roll", "off_rush_ypc_roll",
        "def_pass_epa_roll", "def_rush_epa_roll", "def_early_down_roll", "def_rush_ypc_roll",
    }
    assert set(result.columns) == expected_cols


def test_defensive_epa_is_opponents_offense(tmp_path):
    """KC's def_pass_epa_roll should track BUF's passing EPA, not KC's own passing EPA."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir()
    _make_schedule_df().to_csv(sched_dir / "games.csv", index=False)

    result = _load_rolling_epa(tmp_path)
    # KC passing EPA/play = 20/30 ≈ 0.667; BUF passing EPA/play = 10/28 ≈ 0.357
    # KC's off_pass_epa_roll should converge toward 0.667 (KC offense)
    # KC's def_pass_epa_roll should converge toward 0.357 (BUF offense = what KC's D faced)
    kc_w3 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 3)]
    assert not kc_w3.empty
    off_val = float(kc_w3["off_pass_epa_roll"].iloc[0])
    def_val = float(kc_w3["def_pass_epa_roll"].iloc[0])
    # KC off > KC def because KC offense (0.667) > BUF offense (0.357)
    assert off_val > def_val, f"KC off_pass={off_val:.3f} should exceed def_pass={def_val:.3f}"


def test_no_leakage_week1(tmp_path):
    """Week-1 rolling values must use prior-season average, not current-week data."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir()
    _make_schedule_df().to_csv(sched_dir / "games.csv", index=False)

    result = _load_rolling_epa(tmp_path)
    kc_w1_2024 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 1)]
    assert not kc_w1_2024.empty, "KC week-1 2024 row missing from rolling EPA output"
    val = float(kc_w1_2024["off_pass_epa_roll"].iloc[0])
    assert not np.isnan(val), "Week-1 value should be filled with prior-season average, not NaN"


from services.nn_feature_engine import _load_trench_rolling_stats


def _make_trench_stats_df():
    """Minimal stats_team_week data with trench columns."""
    rows = []
    for season in [2023, 2024]:
        for week in [1, 2, 3]:
            rows.append({"season": season, "week": week, "team": "KC",
                         "season_type": "REG",
                         "sacks_suffered": 2.0, "rushing_yards": 80.0, "carries": 20,
                         "def_sacks": 3.0, "def_qb_hits": 4.0, "def_tackles_for_loss": 5.0})
            rows.append({"season": season, "week": week, "team": "BUF",
                         "season_type": "REG",
                         "sacks_suffered": 1.0, "rushing_yards": 60.0, "carries": 18,
                         "def_sacks": 2.0, "def_qb_hits": 3.0, "def_tackles_for_loss": 4.0})
    return pd.DataFrame(rows)


def test_load_trench_rolling_stats_returns_expected_columns(tmp_path):
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_trench_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    result = _load_trench_rolling_stats(tmp_path)
    assert set(result.columns) == {"season", "week", "team", "sacks_suffered_roll", "dl_pass_roll"}


def test_dl_pass_roll_uses_weighted_composite(tmp_path):
    """dl_pass_roll = rolling mean of (def_sacks*6 + def_qb_hits*1 + def_tfl*1)."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_trench_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    result = _load_trench_rolling_stats(tmp_path)
    # KC: def_sacks=3, def_qb_hits=4, def_tfl=5 → composite = 3*6+4+5 = 27 each game
    # Week-3 rolling = mean of weeks 1+2 = 27.0
    kc_w3 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 3)]
    assert not kc_w3.empty
    val = float(kc_w3["dl_pass_roll"].iloc[0])
    assert abs(val - 27.0) < 0.5, f"Expected ~27.0, got {val}"


def test_sacks_suffered_roll_no_leakage(tmp_path):
    """Week-1 sacks_suffered_roll must be filled from prior-season avg, not NaN."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_trench_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    result = _load_trench_rolling_stats(tmp_path)
    kc_w1 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 1)]
    assert not kc_w1.empty, "KC week-1 2024 row missing"
    assert not np.isnan(float(kc_w1["sacks_suffered_roll"].iloc[0])), "Week-1 should not be NaN"
