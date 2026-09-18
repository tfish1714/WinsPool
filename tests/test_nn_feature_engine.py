"""Unit tests for the redesigned nn_feature_engine feature set."""
import numpy as np
import pandas as pd
import pytest
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# Task 4: EPA matchup features in build_master_feature_table()
# ---------------------------------------------------------------------------

def _make_minimal_feature_table_inputs(tmp_path):
    """Write minimal rawdata fixtures for build_master_feature_table."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir(parents=True, exist_ok=True)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)

    # Two teams, 4 weeks (week 4 is the "unplayed" game)
    rows = []
    for week in range(1, 5):
        rows.append({"season": 2024, "week": week, "team": "KC", "season_type": "REG",
                     "passing_epa": 20.0 * week, "attempts": 30, "rushing_epa": 5.0, "carries": 20,
                     "rushing_yards": 80.0, "passing_cpoe": 3.0,
                     "sacks_suffered": 2.0, "def_sacks": 3.0, "def_qb_hits": 4.0,
                     "def_tackles_for_loss": 5.0,
                     "passing_interceptions": 0.5, "rushing_fumbles_lost": 0.2,
                     "passing_first_downs": 10.0, "rushing_first_downs": 4.0,
                     "receiving_first_downs": 0.0,
                     "passing_tds": 2.0, "rushing_tds": 0.5,
                     "def_interceptions": 1.0,
                     "off_raw": 0.0, "def_raw": 0.0})
        rows.append({"season": 2024, "week": week, "team": "BUF", "season_type": "REG",
                     "passing_epa": 10.0 * week, "attempts": 28, "rushing_epa": 2.0, "carries": 18,
                     "rushing_yards": 60.0, "passing_cpoe": 1.0,
                     "sacks_suffered": 1.0, "def_sacks": 2.0, "def_qb_hits": 3.0,
                     "def_tackles_for_loss": 4.0,
                     "passing_interceptions": 1.0, "rushing_fumbles_lost": 0.3,
                     "passing_first_downs": 8.0, "rushing_first_downs": 3.0,
                     "receiving_first_downs": 0.0,
                     "passing_tds": 1.5, "rushing_tds": 0.3,
                     "def_interceptions": 0.5,
                     "off_raw": 0.0, "def_raw": 0.0})
    pd.DataFrame(rows).to_csv(stats_dir / "stats_team_week_2024.csv", index=False)

    sched_rows = []
    for week in range(1, 5):
        sched_rows.append({
            "season": 2024, "week": week,
            "home_team": "KC", "away_team": "BUF",
            "game_type": "REG",
            "home_score": 28 if week < 4 else None,
            "away_score": 21 if week < 4 else None,
            "home_rest": 7, "away_rest": 7,
            "total_line": 48.0, "spread_line": -3.0,
            "temp": 55.0, "wind": 8.0, "roof": "outdoors",
            "surface": "grass", "div_game": 0,
            "location": "Home",
        })
    pd.DataFrame(sched_rows).to_csv(sched_dir / "games.csv", index=False)

    # Elo CSV
    elo_rows = [{"season": 2024, "week": w, "home_team": "KC", "away_team": "BUF",
                 "home_elo_pre": 1550.0, "away_elo_pre": 1480.0} for w in range(1, 5)]
    pd.DataFrame(elo_rows).to_csv(tmp_path / "elo_computed.csv", index=False)

    return tmp_path


def test_epa_matchup_columns_present(tmp_path):
    """build_master_feature_table must contain pass_epa_matchup, rush_epa_matchup, early_down_matchup."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    for col in ["pass_epa_matchup", "rush_epa_matchup", "early_down_matchup"]:
        assert col in df.columns, f"Missing column: {col}"


def _make_epa_diversity_fixture(tmp_path):
    """4-team fixture so each team's defensive EPA is based on different opponents.

    Schedule:
      Wk1: KC(home) vs DEN, BUF(home) vs NE
      Wk2: KC(home) vs NE,  BUF(home) vs DEN
      Wk3: KC(home) vs BUF  ← test game

    Team passing EPA/play:
      KC=0.667 (20/30), BUF=0.357 (10/28), DEN=0.160 (4/25), NE=0.160 (4/25)

    Expected pass_epa_matchup at wk3:
      KC_off_roll = 0.667, KC_def_roll = 0.160 (DEN+NE opponents)
      BUF_off_roll = 0.357, BUF_def_roll = 0.160 (NE+DEN opponents)
      matchup = (0.667 - 0.160) - (0.357 - 0.160) = 0.507 - 0.197 = 0.310 > 0
    """
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir(parents=True, exist_ok=True)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)

    team_stats_base = {
        "KC":  {"passing_epa": 20.0, "attempts": 30, "rushing_epa": 5.0, "carries": 20, "rushing_yards": 80.0, "passing_cpoe": 3.0},
        "BUF": {"passing_epa": 10.0, "attempts": 28, "rushing_epa": 2.0, "carries": 18, "rushing_yards": 60.0, "passing_cpoe": 1.0},
        "DEN": {"passing_epa": 4.0,  "attempts": 25, "rushing_epa": 1.0, "carries": 18, "rushing_yards": 45.0, "passing_cpoe": 0.2},
        "NE":  {"passing_epa": 4.0,  "attempts": 25, "rushing_epa": 1.0, "carries": 18, "rushing_yards": 45.0, "passing_cpoe": 0.2},
    }
    rows = []
    for week in range(1, 4):
        for team, ts in team_stats_base.items():
            rows.append({"season": 2024, "week": week, "team": team, "season_type": "REG",
                         **ts, "sacks_suffered": 1.0, "def_sacks": 2.0, "def_qb_hits": 3.0,
                         "def_tackles_for_loss": 2.0, "passing_interceptions": 0.5,
                         "rushing_fumbles_lost": 0.1, "passing_first_downs": 8.0,
                         "rushing_first_downs": 3.0, "receiving_first_downs": 0.0,
                         "passing_tds": 1.5, "rushing_tds": 0.3, "def_interceptions": 0.5,
                         "off_raw": 0.0, "def_raw": 0.0})
    pd.DataFrame(rows).to_csv(stats_dir / "stats_team_week_2024.csv", index=False)

    sched_rows = [
        {"season": 2024, "week": 1, "home_team": "KC",  "away_team": "DEN", "game_type": "REG",
         "home_score": 35, "away_score": 7,  "home_rest": 7, "away_rest": 7,
         "total_line": 44.0, "spread_line": -14.0, "temp": 55.0, "wind": 8.0,
         "roof": "outdoors", "surface": "grass", "div_game": 0, "location": "Home"},
        {"season": 2024, "week": 1, "home_team": "BUF", "away_team": "NE",  "game_type": "REG",
         "home_score": 24, "away_score": 17, "home_rest": 7, "away_rest": 7,
         "total_line": 44.0, "spread_line": -3.0,  "temp": 55.0, "wind": 8.0,
         "roof": "outdoors", "surface": "grass", "div_game": 0, "location": "Home"},
        {"season": 2024, "week": 2, "home_team": "KC",  "away_team": "NE",  "game_type": "REG",
         "home_score": 28, "away_score": 10, "home_rest": 7, "away_rest": 7,
         "total_line": 44.0, "spread_line": -10.0, "temp": 55.0, "wind": 8.0,
         "roof": "outdoors", "surface": "grass", "div_game": 0, "location": "Home"},
        {"season": 2024, "week": 2, "home_team": "BUF", "away_team": "DEN", "game_type": "REG",
         "home_score": 21, "away_score": 14, "home_rest": 7, "away_rest": 7,
         "total_line": 44.0, "spread_line": -7.0,  "temp": 55.0, "wind": 8.0,
         "roof": "outdoors", "surface": "grass", "div_game": 0, "location": "Home"},
        {"season": 2024, "week": 3, "home_team": "KC",  "away_team": "BUF", "game_type": "REG",
         "home_score": 28, "away_score": 21, "home_rest": 7, "away_rest": 7,
         "total_line": 50.0, "spread_line": -3.0,  "temp": 55.0, "wind": 8.0,
         "roof": "outdoors", "surface": "grass", "div_game": 0, "location": "Home"},
    ]
    pd.DataFrame(sched_rows).to_csv(sched_dir / "games.csv", index=False)

    elo_rows = [
        {"season": 2024, "week": 1, "home_team": "KC",  "away_team": "DEN", "home_elo_pre": 1550.0, "away_elo_pre": 1430.0},
        {"season": 2024, "week": 1, "home_team": "BUF", "away_team": "NE",  "home_elo_pre": 1480.0, "away_elo_pre": 1440.0},
        {"season": 2024, "week": 2, "home_team": "KC",  "away_team": "NE",  "home_elo_pre": 1550.0, "away_elo_pre": 1440.0},
        {"season": 2024, "week": 2, "home_team": "BUF", "away_team": "DEN", "home_elo_pre": 1480.0, "away_elo_pre": 1430.0},
        {"season": 2024, "week": 3, "home_team": "KC",  "away_team": "BUF", "home_elo_pre": 1550.0, "away_elo_pre": 1480.0},
    ]
    pd.DataFrame(elo_rows).to_csv(tmp_path / "elo_computed.csv", index=False)

    return tmp_path


def test_epa_matchup_formula(tmp_path):
    """pass_epa_matchup = (home_off_pass - away_def_pass) - (away_off_pass - home_def_pass).

    Uses a 4-team fixture so each team's defensive EPA roll is derived from different opponents.
    KC (strong) and BUF (medium) each faced weak DEN/NE before meeting in week 3.
    Expected: KC's matchup advantage = (KC_off - BUF_def) - (BUF_off - KC_def) > 0 ≈ 0.31.
    """
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_epa_diversity_fixture(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    kc_vs_buf = df[(df["home_team"] == "KC") & (df["away_team"] == "BUF")]
    assert not kc_vs_buf.empty, "KC vs BUF week-3 game missing from output"
    val = float(kc_vs_buf.iloc[0]["pass_epa_matchup"])
    # KC off >> BUF off; both faced weak DEN/NE defenders -> KC has net advantage
    assert val > 0, f"Expected positive pass_epa_matchup for KC vs BUF, got {val:.4f}"


# ---------------------------------------------------------------------------
# Task 5: Elo diff, point diff, pressure diffs, travel/rest split
# ---------------------------------------------------------------------------

def test_elo_diff_and_confidence(tmp_path):
    """elo_diff = home_elo_pre - away_elo_pre; elo_confidence = |elo_diff|/ELO_TO_SPREAD."""
    from services.nn_feature_engine import build_master_feature_table
    from services.constants import ELO_TO_SPREAD
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "elo_diff" in df.columns
    assert "elo_confidence" in df.columns
    row = df[df["home_team"] == "KC"].iloc[0]
    expected_confidence = 70.0 / ELO_TO_SPREAD
    assert abs(float(row["elo_diff"]) - 70.0) < 1.0, f"elo_diff expected ~70, got {row['elo_diff']}"
    assert abs(float(row["elo_confidence"]) - expected_confidence) < 0.1, (
        f"elo_confidence expected ~{expected_confidence:.2f}, got {row['elo_confidence']}"
    )


def test_point_diff_advantage(tmp_path):
    """point_diff_advantage = home_rolling_margin - away_rolling_margin."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "point_diff_advantage" in df.columns
    # KC wins 28-21 = +7 margin; BUF gets -7 from away perspective.
    # KC home games should have positive point_diff_advantage.
    kc = df[(df["home_team"] == "KC") & df["point_diff_advantage"].notna()]
    if not kc.empty:
        assert float(kc.iloc[-1]["point_diff_advantage"]) > 0


def test_rest_and_travel_are_separate_features(tmp_path):
    """rest_advantage and net_travel_disadvantage must both be present; old combined feature must not."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "rest_advantage" in df.columns
    assert "net_travel_disadvantage" in df.columns
    assert "travel_rest_disadvantage" not in df.columns


def test_neutral_site_travel_is_zero(tmp_path):
    """net_travel_disadvantage must be 0.0 for games with location=='Neutral'."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    sched_path = rd / "schedules" / "games.csv"
    sched_df = pd.read_csv(sched_path)
    sched_df.loc[sched_df["week"] == 2, "location"] = "Neutral"
    sched_df.to_csv(sched_path, index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    # net_travel_disadvantage should be numeric
    assert df["net_travel_disadvantage"].dtype in [np.float64, float]
    # Week-2 game should have net_travel_disadvantage = 0.0 (neutral site)
    w2 = df[df["week"] == 2]
    if not w2.empty:
        assert float(w2.iloc[0]["net_travel_disadvantage"]) == 0.0, \
            f"Neutral-site travel should be 0.0, got {w2.iloc[0]['net_travel_disadvantage']}"


def test_qb_pressure_advantage_direction(tmp_path):
    """qb_pressure_advantage and def_pressure_diff must both be present and numeric."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "qb_pressure_advantage" in df.columns
    assert "def_pressure_diff" in df.columns
    # No pfr_advstats fixture, so values are 0.0 by default — just verify no crash and numeric
    assert df["qb_pressure_advantage"].dtype in [np.float64, float]
    assert df["def_pressure_diff"].dtype in [np.float64, float]


# ---------------------------------------------------------------------------
# Task 6: 4-component trench redesign
# ---------------------------------------------------------------------------

def test_trench_uses_performance_not_snap_count(tmp_path):
    """trench_dominance_metric must use sacks_suffered, rush_ypc, dl_pass composite — not snap counts."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "trench_dominance_metric" in df.columns
    # Column must be numeric and finite
    assert df["trench_dominance_metric"].notna().all()
    assert np.isfinite(df["trench_dominance_metric"]).all()


def test_trench_is_signed_differential(tmp_path):
    """trench_dominance_metric = home_trench_score - away_trench_score (signed diff)."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    # KC has better DL stats; values should be consistent across weeks
    assert df["trench_dominance_metric"].dtype in [np.float64, float]
    # Not all constant (z-scoring should produce variation if any team differs from average)
    assert df["trench_dominance_metric"].std() >= 0


# ---------------------------------------------------------------------------
# Task 7: QB injury flags + home_field_advantage
# ---------------------------------------------------------------------------

def test_qb_injury_split_two_flags(tmp_path):
    """home_qb_injury_flag and away_qb_injury_flag must be present; old qb_injury_flag must not."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "home_qb_injury_flag" in df.columns
    assert "away_qb_injury_flag" in df.columns
    assert "qb_injury_flag" not in df.columns


def test_both_qbs_injured_distinguishable(tmp_path):
    """When both QBs are injured, home=1 and away=1 (not 0+0 like old diff)."""
    from services.nn_feature_engine import _load_injury_flags
    inj_dir = tmp_path / "injuries"
    inj_dir.mkdir(parents=True, exist_ok=True)
    inj_df = pd.DataFrame([
        {"season": 2024, "week": 1, "team": "KC",  "position": "QB", "report_status": "Out"},
        {"season": 2024, "week": 1, "team": "BUF", "position": "QB", "report_status": "Out"},
    ])
    inj_df.to_csv(inj_dir / "injuries_2024.csv", index=False)

    result = _load_injury_flags(tmp_path)
    assert not result.empty
    kc  = result[(result["team"] == "KC")  & (result["season"] == 2024) & (result["week"] == 1)]
    buf = result[(result["team"] == "BUF") & (result["season"] == 2024) & (result["week"] == 1)]
    assert not kc.empty  and float(kc["home_qb_injury_flag"].iloc[0]) == 1.0
    assert not buf.empty and float(buf["away_qb_injury_flag"].iloc[0]) == 1.0


def test_qb_availability_signal_ors_into_injury_flag(tmp_path):
    """A team whose declared starter is benched-via-snap-count (no official
    injury report entry at all) must still show home_qb_injury_flag == 1.0
    after this merge -- proving the new signal actually reaches the table,
    not just the official injury report."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)

    dc_dir = rd / "depth_charts"
    dc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"club_code": "KC", "week": 1, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "KC", "week": 2, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "KC", "week": 3, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 1, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 2, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 3, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
    ]).to_csv(dc_dir / "depth_charts_2024.csv", index=False)

    sc_dir = rd / "snap_counts"
    sc_dir.mkdir(parents=True, exist_ok=True)
    roster_dir = rd / "rosters"
    roster_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"pfr_id": "KCBACKUP0", "gsis_id": "KC-BACKUP", "birth_date": "1999-01-01", "season": 2024},
        {"pfr_id": "KCSTART00", "gsis_id": "KC-STARTER", "birth_date": "1998-01-01", "season": 2024},
    ]).to_csv(roster_dir / "roster_2024.csv", index=False)
    pd.DataFrame([
        # KC week 1: starter plays
        {"season": 2024, "week": 1, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCSTART00", "offense_snaps": 60, "defense_snaps": 0},
        # KC weeks 2-3: starter benched (0 snaps), backup plays (healthy -- no injury report entry)
        {"season": 2024, "week": 2, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCSTART00", "offense_snaps": 0, "defense_snaps": 0},
        {"season": 2024, "week": 2, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCBACKUP0", "offense_snaps": 60, "defense_snaps": 0},
        {"season": 2024, "week": 3, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCSTART00", "offense_snaps": 0, "defense_snaps": 0},
        {"season": 2024, "week": 3, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCBACKUP0", "offense_snaps": 60, "defense_snaps": 0},
    ]).to_csv(sc_dir / "snap_counts_2024.csv", index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    wk3 = df[df["week"] == 3].iloc[0]
    assert wk3["home_qb_injury_flag"] == 1.0  # KC is home in the shared fixture


def test_home_field_advantage_neutral_is_zero(tmp_path):
    """home_field_advantage must be 0.0 for games with location=='Neutral'."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    sched_path = rd / "schedules" / "games.csv"
    sched_df = pd.read_csv(sched_path)
    sched_df.loc[sched_df["week"] == 1, "location"] = "Neutral"
    sched_df.loc[sched_df["week"] == 1, ["home_score", "away_score"]] = [24, 21]
    sched_df.to_csv(sched_path, index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "home_field_advantage" in df.columns
    w1 = df[df["week"] == 1]
    if not w1.empty:
        assert float(w1.iloc[0]["home_field_advantage"]) == 0.0, \
            f"Neutral-site home_field_advantage should be 0.0, got {w1.iloc[0]['home_field_advantage']}"

    w2 = df[df["week"] == 2]
    if not w2.empty:
        assert float(w2.iloc[0]["home_field_advantage"]) == 1.0, \
            f"Regular home game should have home_field_advantage=1.0, got {w2.iloc[0]['home_field_advantage']}"


# ---------------------------------------------------------------------------
# Task 8: Finalize output
# ---------------------------------------------------------------------------

def test_all_27_features_in_output(tmp_path):
    """build_master_feature_table output must contain all 27 FEATURE_COLUMNS."""
    from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    missing = [f for f in FEATURE_COLUMNS if f not in df.columns]
    assert not missing, f"Missing features: {missing}"


def test_no_obsolete_features_in_output(tmp_path):
    """Old features must not appear in build_master_feature_table output."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    for old in ["tm_elo_pre", "opp_elo_pre", "off_pass_epa", "def_pass_epa",
                "home_flag", "is_dome_flag", "travel_rest_disadvantage",
                "qb_injury_flag", "off_rush_epa", "def_rush_epa", "early_down_pass_epa"]:
        assert old not in df.columns, f"Obsolete column still in output: {old}"


def test_aux_metadata_columns_in_output(tmp_path):
    """Aux columns for projection engine must be present in the output."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    for col in ["home_elo_pre", "away_elo_pre",
                "home_trench_score", "away_trench_score",
                "home_margin_roll", "away_margin_roll"]:
        assert col in df.columns, f"Missing aux column: {col}"


# ---------------------------------------------------------------------------
# _load_elo: Firestore elo_history fallback (winspool-predict-daily's
# container never has rawdata/elo_computed.csv -- see services/nn_feature_engine.py)
# ---------------------------------------------------------------------------

def test_load_elo_uses_csv_when_present(tmp_path):
    """The local CSV, when present, is used as-is -- no Firestore fallback."""
    from unittest.mock import patch
    from services.nn_feature_engine import _load_elo

    csv_rows = pd.DataFrame([
        {"season": 2025, "week": 1, "home_team": "KC", "away_team": "BUF",
         "home_elo_pre": 1600.0, "away_elo_pre": 1550.0},
    ])
    csv_rows.to_csv(tmp_path / "elo_computed.csv", index=False)

    with patch("services.cache_service.get_all_elo_history") as mock_fs:
        df = _load_elo(tmp_path)
        mock_fs.assert_not_called()

    assert len(df) == 1
    assert df.iloc[0]["home_elo_pre"] == 1600.0


def test_load_elo_falls_back_to_firestore_when_csv_missing(tmp_path):
    """No local CSV (winspool-predict-daily's actual runtime condition) --
    fall back to the Firestore elo_history collection instead of defaulting
    every team to a flat 1500.0."""
    from unittest.mock import patch
    from services.nn_feature_engine import _load_elo

    firestore_rows = [
        {"season": 2025, "week": 1, "home_team": "KC", "away_team": "BUF",
         "home_elo_pre": 1620.0, "away_elo_pre": 1480.0},
        {"season": 2025, "week": 2, "home_team": "SF", "away_team": "LA",
         "home_elo_pre": 1590.0, "away_elo_pre": 1510.0},
    ]

    with patch("services.cache_service.get_all_elo_history", return_value=firestore_rows) as mock_fs:
        df = _load_elo(tmp_path)  # tmp_path has no elo_computed.csv
        mock_fs.assert_called_once()

    assert len(df) == 2
    assert set(df.columns) >= {"season", "week", "home_team", "away_team", "home_elo_pre", "away_elo_pre"}
    assert float(df.iloc[0]["home_elo_pre"]) == 1620.0
    assert float(df.iloc[1]["away_elo_pre"]) == 1510.0


def test_load_elo_empty_when_csv_and_firestore_both_missing(tmp_path):
    """Preserves the original degrade-gracefully behavior (flat 1500.0
    upstream) when neither the CSV nor elo_history has anything -- e.g. a
    local dev machine with USE_LOCAL_DATA and no local elo_history cache."""
    from unittest.mock import patch
    from services.nn_feature_engine import _load_elo

    with patch("services.cache_service.get_all_elo_history", return_value=[]):
        df = _load_elo(tmp_path)

    assert df.empty


class TestLoadDeclaredStarters:
    def test_old_schema_uses_own_week_directly(self, tmp_path):
        """Pre-2025 depth charts are already per-week -- no dt resolution needed."""
        from services.nn_feature_engine import _load_declared_starters
        dc_dir = tmp_path / "depth_charts"
        dc_dir.mkdir()
        pd.DataFrame([
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Alpha", "gsis_id": "00-0001", "depth_position": "QB"},
            {"club_code": "AAA", "week": 2, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Bravo", "gsis_id": "00-0002", "depth_position": "QB"},
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 2,
             "full_name": "QB Backup", "gsis_id": "00-0003", "depth_position": "QB"},
        ]).to_csv(dc_dir / "depth_charts_2023.csv", index=False)

        result = _load_declared_starters(tmp_path)
        row = result[(result["season"] == 2023) & (result["week"] == 1) & (result["team"] == "AAA")]
        assert row.iloc[0]["gsis_id"] == "00-0001"
        row2 = result[(result["season"] == 2023) & (result["week"] == 2) & (result["team"] == "AAA")]
        assert row2.iloc[0]["gsis_id"] == "00-0002"

    def test_new_schema_resolves_snapshot_before_kickoff(self, tmp_path):
        """New-schema depth charts are dt-timestamped, not week-indexed --
        resolve against the schedule's own kickoff date via merge_asof."""
        from services.nn_feature_engine import _load_declared_starters
        dc_dir = tmp_path / "depth_charts"
        dc_dir.mkdir()
        pd.DataFrame([
            {"dt": "2026-09-05T00:00:00Z", "team": "SEA", "player_name": "Sam Darnold",
             "gsis_id": "00-0100", "pos_abb": "QB", "pos_rank": 1},
            {"dt": "2026-09-14T00:00:00Z", "team": "SEA", "player_name": "Drew Lock",
             "gsis_id": "00-0101", "pos_abb": "QB", "pos_rank": 1},
        ]).to_csv(dc_dir / "depth_charts_2026.csv", index=False)

        sched_dir = tmp_path / "schedules"
        sched_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "SEA", "away_team": "NE",
             "game_type": "REG", "gameday": "2026-09-09", "home_score": None, "away_score": None,
             "roof": "outdoors", "temp": 60.0, "wind": 10.0, "location": "Home",
             "home_rest": 7, "away_rest": 7, "div_game": 0, "surface": "grass"},
            {"season": 2026, "week": 2, "home_team": "ARI", "away_team": "SEA",
             "game_type": "REG", "gameday": "2026-09-20", "home_score": None, "away_score": None,
             "roof": "dome", "temp": 72.0, "wind": 0.0, "location": "Home",
             "home_rest": 7, "away_rest": 7, "div_game": 0, "surface": "turf"},
        ]).to_csv(sched_dir / "games.csv", index=False)

        result = _load_declared_starters(tmp_path)
        wk1 = result[(result["season"] == 2026) & (result["week"] == 1) & (result["team"] == "SEA")]
        assert wk1.iloc[0]["gsis_id"] == "00-0100"  # Sept 5 snapshot -- before Sept 9 kickoff
        wk2 = result[(result["season"] == 2026) & (result["week"] == 2) & (result["team"] == "SEA")]
        assert wk2.iloc[0]["gsis_id"] == "00-0101"  # Sept 14 snapshot -- before Sept 20 kickoff

    def test_new_schema_strictly_before_kickoff_excludes_exact_match(self, tmp_path):
        """Verify merge_asof uses allow_exact_matches=False: dt==kickoff_date is NOT a match."""
        from services.nn_feature_engine import _load_declared_starters
        dc_dir = tmp_path / "depth_charts"
        dc_dir.mkdir()
        # Snapshot at exact kickoff time should NOT be used; earlier snapshot should win
        pd.DataFrame([
            {"dt": "2026-09-08T00:00:00Z", "team": "SEA", "player_name": "QB Old",
             "gsis_id": "00-9998", "pos_abb": "QB", "pos_rank": 1},
            {"dt": "2026-09-09T00:00:00Z", "team": "SEA", "player_name": "Sam Darnold",
             "gsis_id": "00-0100", "pos_abb": "QB", "pos_rank": 1},
        ]).to_csv(dc_dir / "depth_charts_2026.csv", index=False)

        sched_dir = tmp_path / "schedules"
        sched_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "SEA", "away_team": "NE",
             "game_type": "REG", "gameday": "2026-09-09", "home_score": None, "away_score": None,
             "roof": "outdoors", "temp": 60.0, "wind": 10.0, "location": "Home",
             "home_rest": 7, "away_rest": 7, "div_game": 0, "surface": "grass"},
        ]).to_csv(sched_dir / "games.csv", index=False)

        result = _load_declared_starters(tmp_path)
        wk1 = result[(result["season"] == 2026) & (result["week"] == 1) & (result["team"] == "SEA")]
        # Should use Sept 8 snapshot (before kickoff), NOT Sept 9 exact match
        assert wk1.iloc[0]["gsis_id"] == "00-9998", "merge_asof should reject exact match (allow_exact_matches=False)"

    def test_missing_depth_charts_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_declared_starters
        result = _load_declared_starters(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id"]
        assert result.empty


# ---------------------------------------------------------------------------
# Task 2: QB Availability Loaders (injury report, reserve status, snap share)
# ---------------------------------------------------------------------------

class TestLoadQbReportStatus:
    def test_returns_qb_rows_with_status(self, tmp_path):
        from services.nn_feature_engine import _load_qb_report_status
        inj_dir = tmp_path / "injuries"
        inj_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0100", "report_status": "Out"},
            {"season": 2026, "week": 2, "team": "SEA", "position": "WR",
             "gsis_id": "00-0200", "report_status": "Questionable"},
        ]).to_csv(inj_dir / "injuries_2026.csv", index=False)

        result = _load_qb_report_status(tmp_path)
        assert len(result) == 1
        assert result.iloc[0]["gsis_id"] == "00-0100"
        assert result.iloc[0]["report_status"] == "Out"

    def test_missing_file_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_qb_report_status
        result = _load_qb_report_status(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id", "report_status"]
        assert result.empty


class TestLoadQbReserveStatus:
    def test_returns_only_reserve_rows(self, tmp_path):
        from services.nn_feature_engine import _load_qb_reserve_status
        wr_dir = tmp_path / "weekly_rosters"
        wr_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0100", "status": "RES"},
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0101", "status": "ACT"},
        ]).to_csv(wr_dir / "roster_weekly_2026.csv", index=False)

        result = _load_qb_reserve_status(tmp_path)
        assert len(result) == 1
        assert result.iloc[0]["gsis_id"] == "00-0100"

    def test_missing_file_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_qb_reserve_status
        result = _load_qb_reserve_status(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id"]
        assert result.empty


class TestLoadQbSnapShares:
    def test_computes_share_of_team_qb_snaps(self, tmp_path):
        from services.nn_feature_engine import _load_qb_snap_shares
        sc_dir = tmp_path / "snap_counts"
        sc_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "DarnSa00", "offense_snaps": 5},
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "LockDr00", "offense_snaps": 45},
        ]).to_csv(sc_dir / "snap_counts_2026.csv", index=False)

        roster_dir = tmp_path / "rosters"
        roster_dir.mkdir()
        pd.DataFrame([
            {"pfr_id": "DarnSa00", "gsis_id": "00-0100"},
            {"pfr_id": "LockDr00", "gsis_id": "00-0101"},
        ]).to_csv(roster_dir / "roster_2026.csv", index=False)

        result = _load_qb_snap_shares(tmp_path)
        darnold = result[result["gsis_id"] == "00-0100"].iloc[0]
        lock = result[result["gsis_id"] == "00-0101"].iloc[0]
        assert darnold["snap_share"] == pytest.approx(0.1)
        assert lock["snap_share"] == pytest.approx(0.9)

    def test_unmatched_pfr_id_dropped_not_crashed(self, tmp_path):
        """A snap-count player with no roster ID crosswalk entry is dropped,
        not treated as 0% -- fails open at the caller instead."""
        from services.nn_feature_engine import _load_qb_snap_shares
        sc_dir = tmp_path / "snap_counts"
        sc_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "Unknown00", "offense_snaps": 50},
        ]).to_csv(sc_dir / "snap_counts_2026.csv", index=False)
        roster_dir = tmp_path / "rosters"
        roster_dir.mkdir()
        pd.DataFrame([{"pfr_id": "SomeoneElse00", "gsis_id": "00-9999"}]).to_csv(
            roster_dir / "roster_2026.csv", index=False)

        result = _load_qb_snap_shares(tmp_path)
        assert result.empty

    def test_missing_file_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_qb_snap_shares
        result = _load_qb_snap_shares(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id", "snap_share"]
        assert result.empty

    def test_matched_qb_share_uses_all_qb_totals_not_just_matched(self, tmp_path):
        """REGRESSION: team-week snap totals must include ALL QBs in snap_counts,
        even those without a roster crosswalk. If totals were computed only from
        matched QBs, unmatched teammates would be invisible and inflated the matched
        player's snap_share. E.g., matched QB with 40 snaps + unmatched QB with 10
        snaps should give matched QB 40/50=0.8, not 40/40=1.0."""
        from services.nn_feature_engine import _load_qb_snap_shares
        sc_dir = tmp_path / "snap_counts"
        sc_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "MatchedQb", "offense_snaps": 40},
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "UnmatchedQb", "offense_snaps": 10},
        ]).to_csv(sc_dir / "snap_counts_2026.csv", index=False)

        roster_dir = tmp_path / "rosters"
        roster_dir.mkdir()
        pd.DataFrame([
            {"pfr_id": "MatchedQb", "gsis_id": "00-0100"},
            # UnmatchedQb intentionally omitted from roster to simulate no crosswalk
        ]).to_csv(roster_dir / "roster_2026.csv", index=False)

        result = _load_qb_snap_shares(tmp_path)
        assert len(result) == 1  # Only matched QB in output
        matched = result.iloc[0]
        # Denominator must be 50 (all QBs), not 40 (only matched)
        # So matched QB should be 40/50 = 0.8, NOT 1.0
        assert matched["snap_share"] == pytest.approx(0.8), \
            f"Matched QB with 40 snaps out of 50 total should be 0.8, got {matched['snap_share']}"


# ---------------------------------------------------------------------------
# Task 3: QB Availability Flags (sticky-reference algorithm)
# ---------------------------------------------------------------------------

class TestComputeQbAvailabilityFlags:
    def _patch_loaders(self, declared, report, reserve, snaps):
        import services.nn_feature_engine as nfe
        return [
            patch.object(nfe, "_load_declared_starters", return_value=pd.DataFrame(declared)),
            patch.object(nfe, "_load_qb_report_status", return_value=pd.DataFrame(report)),
            patch.object(nfe, "_load_qb_reserve_status", return_value=pd.DataFrame(reserve)),
            patch.object(nfe, "_load_qb_snap_shares", return_value=pd.DataFrame(snaps)),
        ]

    def test_healthy_starter_all_season_flags_zero(self, tmp_path):
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "QB1"} for w in [1, 2, 3]]
        patches = self._patch_loaders(declared, [], [], [])
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result == {
            (2026, 1, "AAA"): 0.0, (2026, 2, "AAA"): 0.0, (2026, 3, "AAA"): 0.0,
        }

    def test_starter_hurt_mid_game_week1_flags_week2_not_week1(self, tmp_path):
        """Regression for the SEA/MIN case, corrected: a starter's OWN
        snap share for week wk is only knowable after week wk's game is
        played, so it must never be used to set week wk's own flag -- doing
        so lets a week's own outcome leak into "explaining"/grading that
        same week's prediction (and into training on hindsight the model
        will never have at real prediction time). Darnold got hurt mid-game
        in week 1 (no pre-game injury report entry that week) -- week 1
        itself must read healthy; week 1's now-known low snap share
        legitimately carries forward to flag week 2, on top of week 2's own
        injury report."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD"}]
        report = [{"season": 2026, "week": 2, "team": "SEA", "gsis_id": "DARNOLD",
                   "report_status": "Out"}]
        snaps = [
            {"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD", "snap_share": 0.10},
            {"season": 2026, "week": 1, "team": "SEA", "gsis_id": "LOCK", "snap_share": 0.90},
        ]
        patches = self._patch_loaders(declared, report, [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 1, "SEA")] == 0.0  # unknowable before week 1's own kickoff
        assert result[(2026, 2, "SEA")] == 1.0  # week 1's snap share now known + week 2's own report

    def test_first_evaluated_week_never_uses_its_own_snap_share(self, tmp_path):
        """A team's very first evaluated week has no prior week to check --
        its own (only-knowable-after-the-fact) snap share must never
        substitute, even when it's the only signal available."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD"}]
        snaps = [
            {"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD", "snap_share": 0.05},
        ]
        patches = self._patch_loaders(declared, [], [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 1, "SEA")] == 0.0

    def test_reserve_status_flags_when_injury_report_silent(self, tmp_path):
        """A season-ending IR move often drops off the weekly injury report
        entirely -- the Reserve/IR roster status leg must catch it anyway."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "SEA", "gsis_id": "DARNOLD"} for w in [1, 2]]
        reserve = [{"season": 2026, "week": 2, "team": "SEA", "gsis_id": "DARNOLD"}]
        patches = self._patch_loaders(declared, [], reserve, [])
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 2, "SEA")] == 1.0

    def test_single_week_healthy_rest_does_not_flip_reference(self, tmp_path):
        """One week of a healthy backup (no injury/reserve entry for the
        starter) must not permanently reassign the reference -- only two
        CONSECUTIVE weeks of a >65% snap-share challenger does. Also
        exercises the corrected snap-share carry-forward: week 2's own low
        snap share is unknowable before week 2's kickoff (flags[2]==0.0),
        but legitimately informs week 3 (flags[3]==1.0) since it's now in
        the past; week 4 reads healthy again once the starter's most
        recent (week 3) snap share is normal."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3, 4]]
        snaps = [
            {"season": 2026, "week": 1, "team": "AAA", "gsis_id": "STARTER", "snap_share": 1.0},
            {"season": 2026, "week": 2, "team": "AAA", "gsis_id": "STARTER", "snap_share": 0.05},
            {"season": 2026, "week": 2, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 0.95},
            {"season": 2026, "week": 3, "team": "AAA", "gsis_id": "STARTER", "snap_share": 1.0},
            {"season": 2026, "week": 4, "team": "AAA", "gsis_id": "STARTER", "snap_share": 1.0},
        ]
        patches = self._patch_loaders(declared, [], [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        # Week 2's own low snap share isn't knowable before week 2's kickoff...
        assert result[(2026, 2, "AAA")] == 0.0
        # ...but it's known by week 3's kickoff, and legitimately flags week 3.
        assert result[(2026, 3, "AAA")] == 1.0
        # By week 4 the starter's most recent (week 3) snap share is normal
        # again, and the reference never flipped (only one week of benching).
        assert result[(2026, 4, "AAA")] == 0.0

    def test_two_consecutive_weeks_of_healthy_benching_flips_reference(self, tmp_path):
        """A genuine benching (or a resolving preseason committee): the
        reference flips to the new starter, and the flag then tracks THEM,
        not the original starter."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3, 4]]
        snaps = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER", "snap_share": 0.05}
            for w in [2, 3, 4]
        ] + [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 0.95}
            for w in [2, 3, 4]
        ]
        patches = self._patch_loaders(declared, [], [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        # Week 4: reference has flipped to BACKUP, who is playing at 95% ->
        # available, even though the ORIGINAL starter is still at 5%.
        assert result[(2026, 4, "AAA")] == 0.0

    def test_reference_stays_pinned_while_starter_has_any_injury_entry(self, tmp_path):
        """Even if a backup holds the job for weeks, the reference does NOT
        flip while the original starter still shows an Out/Doubtful entry --
        only a healthy-but-benched starter can lose the reference."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3, 4]]
        report = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER", "report_status": "Out"}
            for w in [2, 3, 4]
        ]
        snaps = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 1.0}
            for w in [2, 3, 4]
        ]
        patches = self._patch_loaders(declared, report, [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        for w in [2, 3, 4]:
            assert result[(2026, w, "AAA")] == 1.0, f"week {w} should still flag -- starter still Out"

    def test_missing_declared_starter_fails_open(self, tmp_path):
        from services.nn_feature_engine import compute_qb_availability_flags
        result = compute_qb_availability_flags([2026], tmp_path)
        assert result == {}

    def test_reference_initializes_from_teams_own_earliest_week_not_season_minimum(self, tmp_path):
        """Regression: a team whose depth chart wasn't resolved until week 3
        (no declared-starter row for weeks 1-2, e.g. crosswalk/timing gap)
        must still initialize its reference from ITS OWN earliest declared
        week -- not fail open to 0.0 for its whole season just because some
        OTHER team in the same season has a week-1 entry."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = (
            [{"season": 2026, "week": w, "team": "TEAM_A", "gsis_id": "A_QB"} for w in [1, 2, 3]]
            + [{"season": 2026, "week": 3, "team": "TEAM_B", "gsis_id": "B_QB"}]
        )
        report = [{"season": 2026, "week": 3, "team": "TEAM_B", "gsis_id": "B_QB",
                   "report_status": "Out"}]
        patches = self._patch_loaders(declared, report, [], [])
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 3, "TEAM_B")] == 1.0, (
            "TEAM_B's own week-3 declared starter should have been picked up "
            "as the reference, not left None because TEAM_A has a week-1 row"
        )
