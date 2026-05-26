"""Unit tests for the redesigned nn_feature_engine feature set."""
import pytest
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
