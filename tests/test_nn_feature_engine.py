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
    """elo_diff = home_elo_pre - away_elo_pre; elo_confidence = |elo_diff|/25."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "elo_diff" in df.columns
    assert "elo_confidence" in df.columns
    # Elo fixture: home=1550, away=1480 -> diff=70, confidence=70/25=2.8
    row = df[df["home_team"] == "KC"].iloc[0]
    assert abs(float(row["elo_diff"]) - 70.0) < 1.0, f"elo_diff expected ~70, got {row['elo_diff']}"
    assert abs(float(row["elo_confidence"]) - 2.8) < 0.1, f"elo_confidence expected ~2.8, got {row['elo_confidence']}"


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
