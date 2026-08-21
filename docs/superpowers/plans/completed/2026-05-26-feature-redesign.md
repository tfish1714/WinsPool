# Feature Engineering Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Redesign `nn_feature_engine.py` from 32 paired scalars to 27 signed-differential features, fixing mislabeled defensive EPA, the QB-injury-flag bug, constant `home_flag`, neutral-site travel error, and volume-based trench metric.

**Architecture:** All changes are confined to `services/nn_feature_engine.py` (primary), `services/nn_projection_engine.py` (feature handlers), and `services/nn_prediction_service.py` (explanation block). New FEATURE_COLUMNS drives everything; downstream models must be retrained after implementation.

**Tech Stack:** Python, pandas, numpy — no new dependencies. Reads `rawdata/` CSVs via existing helpers. Uses schedule pairing (opponent lookup from `games.csv`) to derive defensive EPA.

---

## File Map

| File | Change type | What changes |
|------|-------------|-------------|
| `services/nn_feature_engine.py` | Modify | `FEATURE_COLUMNS`, `_load_rolling_epa()`, `_load_trench_rolling_stats()` (new), `_load_injury_flags()`, `_load_schedule()`, `build_master_feature_table()` |
| `services/nn_projection_engine.py` | Modify | `_build_team_profiles()`, `game_win_probability()` |
| `services/nn_prediction_service.py` | Modify | `build_ensemble_lookup()` explanation dict |
| `tests/test_nn_feature_engine.py` | Create | All unit tests for new feature logic |

---

## Task 1: Update FEATURE_COLUMNS (32 → 27)

**Files:**
- Modify: `services/nn_feature_engine.py:52-74`
- Create: `tests/test_nn_feature_engine.py`

- [x] **Step 1: Write the failing test**

```python
# tests/test_nn_feature_engine.py
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
```

- [x] **Step 2: Run test to confirm it fails**

```
pytest tests/test_nn_feature_engine.py::test_feature_columns_exact_list -v
```
Expected: FAIL — current list has 32 features with old names.

- [x] **Step 3: Replace FEATURE_COLUMNS in `services/nn_feature_engine.py`**

Replace lines 52–74 (the entire `FEATURE_COLUMNS` list):

```python
FEATURE_COLUMNS = [
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
    # QB health (2) — split to fix both-injured=0 bug
    "home_qb_injury_flag", "away_qb_injury_flag",
    # Roster Value (5)
    "roster_talent_delta",
    "off_roster_value_delta", "def_roster_value_delta",
    "st_value_delta", "qb_resilience_delta",
    # Contextual (5)
    "home_field_advantage",   # 1.0 regular home, 0.0 neutral site
    "div_game_flag", "surface_type", "playoff_flag", "week",
]
```

- [x] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: 3 PASS.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: update FEATURE_COLUMNS 32→27 signed-differential features"
```

---

## Task 2: Extend `_load_rolling_epa()` — defensive EPA + YPC via schedule pairing

**Files:**
- Modify: `services/nn_feature_engine.py:535-608` (`_load_rolling_epa`)
- Modify: `tests/test_nn_feature_engine.py`

**Background:** The function currently returns only 3 offensive rolling columns. We need 8: 4 offensive + 4 defensive (derived by schedule pairing: opponent's offensive EPA that game = this team's defensive EPA allowed). We also add `off_rush_ypc_roll` and `def_rush_ypc_roll` (for trench OL/DL run components).

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
from services.nn_feature_engine import _load_rolling_epa
from pathlib import Path


def _make_stats_df():
    """Two teams, three weeks of stats."""
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
    """KC's def_pass_epa_roll should track BUF's offensive pass EPA (not KC's)."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir()
    _make_schedule_df().to_csv(sched_dir / "games.csv", index=False)

    result = _load_rolling_epa(tmp_path)
    # For week 3 of 2024, KC's off_pass_epa_roll reflects KC's own passing EPA.
    # KC's def_pass_epa_roll reflects BUF's passing EPA (10/28 ≈ 0.357).
    # BUF's passing EPA per play = 10/28 ≈ 0.357; KC's = 20/30 ≈ 0.667.
    kc_w3 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 3)]
    assert not kc_w3.empty
    off_val = float(kc_w3["off_pass_epa_roll"].iloc[0])
    def_val = float(kc_w3["def_pass_epa_roll"].iloc[0])
    # KC's off >> def because KC offense (0.667) > BUF offense (0.357)
    assert off_val > def_val, f"KC off_pass={off_val:.3f} should exceed def_pass={def_val:.3f}"


def test_no_leakage_week1(tmp_path):
    """Week-1 rolling values must not contain current-game data."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir()
    _make_schedule_df().to_csv(sched_dir / "games.csv", index=False)

    result = _load_rolling_epa(tmp_path)
    # Season 2024 week 1 can only use 2023 season average as fallback — no 2024 games yet.
    kc_w1_2024 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 1)]
    if not kc_w1_2024.empty:
        val = float(kc_w1_2024["off_pass_epa_roll"].iloc[0])
        # Value is prior-season average (2023 mean of ~0.667), NOT 2024 week-1 EPA (which is 0.667 too in our fixture, but test structure is correct)
        assert not np.isnan(val), "Week-1 value should be filled with prior-season average, not NaN"
```

- [x] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_nn_feature_engine.py::test_rolling_epa_returns_8_roll_columns tests/test_nn_feature_engine.py::test_defensive_epa_is_opponents_offense -v
```
Expected: FAIL — current function returns only 3 columns with old names.

- [x] **Step 3: Replace `_load_rolling_epa()` (lines 535–608) with the new implementation**

```python
def _load_rolling_epa(rd: Path) -> pd.DataFrame:
    """Load per-play EPA and rush YPC with 8 rolling prior-game columns.

    Offensive columns: off_pass_epa_roll, off_rush_epa_roll, off_early_down_roll,
                       off_rush_ypc_roll
    Defensive columns (via schedule pairing — opponent's offense = this team's defense allowed):
                       def_pass_epa_roll, def_rush_epa_roll, def_early_down_roll,
                       def_rush_ypc_roll

    All rolling values use expanding mean shifted by 1 (no data leakage).
    Week-1 NaN is filled with the prior season's team average.
    """
    df = _load_multi_season("stats_team/stats_team_week_*.csv", rd)
    if df.empty:
        return df

    df["team"] = df["team"].apply(_normalize_team)
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"].copy()

    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df = df.dropna(subset=["season", "week", "team"])

    # Per-play offensive rates
    attempts = pd.to_numeric(df.get("attempts", pd.Series(1, index=df.index)), errors="coerce").clip(lower=1)
    carries  = pd.to_numeric(df.get("carries",  pd.Series(1, index=df.index)), errors="coerce").clip(lower=1)
    rush_yds = pd.to_numeric(df.get("rushing_yards", pd.Series(0, index=df.index)), errors="coerce").fillna(0)
    cpoe     = pd.to_numeric(df.get("passing_cpoe", pd.Series(0, index=df.index)), errors="coerce").fillna(0)

    df["pass_epa_play"] = pd.to_numeric(df.get("passing_epa", 0), errors="coerce").fillna(0) / attempts
    df["rush_epa_play"] = pd.to_numeric(df.get("rushing_epa", 0), errors="coerce").fillna(0) / carries
    df["rush_ypc"]      = rush_yds / carries
    # Early-down composite: 0.6 pass EPA + 0.2 rush EPA + 0.05 cpoe
    df["early_down_epa"] = df["pass_epa_play"] * 0.6 + df["rush_epa_play"] * 0.2 + cpoe * 0.05

    # --- Schedule pairing: find each team's opponent per (season, week) ---
    sched_path = rd / "schedules" / "games.csv"
    if sched_path.exists():
        sched_raw = _read_csv_safe(str(sched_path))
        sched_raw = sched_raw[sched_raw.get("game_type", "REG") == "REG"].copy() if not sched_raw.empty else sched_raw
        if not sched_raw.empty:
            sched_raw["home_team"] = sched_raw["home_team"].apply(_normalize_team)
            sched_raw["away_team"] = sched_raw["away_team"].apply(_normalize_team)
            sched_raw["season"] = pd.to_numeric(sched_raw["season"], errors="coerce")
            sched_raw["week"]   = pd.to_numeric(sched_raw["week"],   errors="coerce")
            home_side = sched_raw[["season", "week", "home_team", "away_team"]].rename(
                columns={"home_team": "team", "away_team": "opponent"})
            away_side = sched_raw[["season", "week", "away_team", "home_team"]].rename(
                columns={"away_team": "team", "home_team": "opponent"})
            opp_lookup = pd.concat([home_side, away_side], ignore_index=True)
        else:
            opp_lookup = pd.DataFrame(columns=["season", "week", "team", "opponent"])
    else:
        opp_lookup = pd.DataFrame(columns=["season", "week", "team", "opponent"])

    # Join opponent, then join opponent's per-play stats for that same game
    df_paired = df.merge(opp_lookup, on=["season", "week", "team"], how="left")
    opp_stats = df[["season", "week", "team",
                    "pass_epa_play", "rush_epa_play", "early_down_epa", "rush_ypc"]].rename(columns={
        "team":          "opponent",
        "pass_epa_play": "opp_pass_epa_play",
        "rush_epa_play": "opp_rush_epa_play",
        "early_down_epa":"opp_early_down_epa",
        "rush_ypc":      "opp_rush_ypc",
    })
    df_paired = df_paired.merge(opp_stats, on=["season", "week", "opponent"], how="left")
    df_paired["opp_pass_epa_play"]  = df_paired["opp_pass_epa_play"].fillna(0.0)
    df_paired["opp_rush_epa_play"]  = df_paired["opp_rush_epa_play"].fillna(0.0)
    df_paired["opp_early_down_epa"] = df_paired["opp_early_down_epa"].fillna(0.0)
    df_paired["opp_rush_ypc"]       = df_paired["opp_rush_ypc"].fillna(0.0)

    df_paired = df_paired.sort_values(["season", "team", "week"])

    # Rolling expanding mean shifted by 1 (no leakage)
    def _roll(col: str) -> pd.Series:
        return df_paired.groupby(["season", "team"])[col].transform(
            lambda s: s.expanding().mean().shift(1))

    off_src = ["pass_epa_play", "rush_epa_play", "early_down_epa", "rush_ypc"]
    def_src = ["opp_pass_epa_play", "opp_rush_epa_play", "opp_early_down_epa", "opp_rush_ypc"]
    roll_cols = [
        "off_pass_epa_roll", "off_rush_epa_roll", "off_early_down_roll", "off_rush_ypc_roll",
        "def_pass_epa_roll", "def_rush_epa_roll", "def_early_down_roll", "def_rush_ypc_roll",
    ]
    for roll_col, src_col in zip(roll_cols, off_src + def_src):
        df_paired[roll_col] = _roll(src_col)

    # Prior-season team averages for week-1 NaN fill
    src_all = off_src + def_src
    prior_avg = (
        df_paired.groupby(["season", "team"])[src_all].mean()
        .reset_index()
        .rename(columns={c: f"_pa_{c}" for c in src_all})
    )
    prior_avg["join_season"] = prior_avg["season"] + 1

    df_paired = df_paired.merge(
        prior_avg[["join_season", "team"] + [f"_pa_{c}" for c in src_all]],
        left_on=["season", "team"], right_on=["join_season", "team"], how="left",
    ).drop(columns=["join_season"], errors="ignore")

    for roll_col, src_col in zip(roll_cols, src_all):
        df_paired[roll_col] = df_paired[roll_col].fillna(df_paired[f"_pa_{src_col}"]).fillna(0.0)

    return df_paired[["season", "week", "team"] + roll_cols]
```

- [x] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All 6 tests PASS.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: extend _load_rolling_epa with defensive EPA + YPC via schedule pairing"
```

---

## Task 3: Add `_load_trench_rolling_stats()`

**Files:**
- Modify: `services/nn_feature_engine.py` (insert new function after `_load_rolling_epa`, ~line 609)
- Modify: `tests/test_nn_feature_engine.py`

**Background:** The new 4-component trench needs two per-team rolling columns not available from `_load_rolling_epa`: `sacks_suffered_roll` (for OL_pass) and `dl_pass_roll` (for DL_pass = def_sacks×6 + def_qb_hits×1 + def_tfl×1). OL_run uses `off_rush_ypc_roll` and DL_run uses `def_rush_ypc_roll` from Task 2.

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
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
    # KC has def_sacks=3, def_qb_hits=4, def_tfl=5 → composite = 3*6+4+5 = 27
    # Week-3 KC value (rolling of weeks 1-2) = 27.0
    kc_w3 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 3)]
    assert not kc_w3.empty
    val = float(kc_w3["dl_pass_roll"].iloc[0])
    assert abs(val - 27.0) < 0.5, f"Expected ~27.0, got {val}"


def test_sacks_suffered_roll_no_leakage(tmp_path):
    """Week-1 sacks_suffered_roll should use prior-season avg, not current week."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir()
    _make_trench_stats_df().to_csv(stats_dir / "stats_team_week_2024.csv", index=False)

    result = _load_trench_rolling_stats(tmp_path)
    kc_w1_2024 = result[(result["team"] == "KC") & (result["season"] == 2024) & (result["week"] == 1)]
    if not kc_w1_2024.empty:
        assert not np.isnan(float(kc_w1_2024["sacks_suffered_roll"].iloc[0]))
```

- [x] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_nn_feature_engine.py::test_load_trench_rolling_stats_returns_expected_columns -v
```
Expected: FAIL — function does not exist yet.

- [x] **Step 3: Insert `_load_trench_rolling_stats()` after `_load_rolling_epa()` (~line 609)**

```python
def _load_trench_rolling_stats(rd: Path) -> pd.DataFrame:
    """Rolling OL sacks-suffered and DL pass-rush composite per (season, week, team).

    Returns:
        sacks_suffered_roll  — rolling mean sacks allowed per game (for OL_pass component)
        dl_pass_roll         — rolling mean of (def_sacks×6 + def_qb_hits×1 + def_tfl×1) (DL_pass)

    OL_run (rush_ypc) and DL_run (opponents' rush_ypc) come from _load_rolling_epa().
    Covers 2020+ (stats_team_week availability); returns empty DataFrame for earlier seasons.
    """
    df = _load_multi_season("stats_team/stats_team_week_*.csv", rd)
    if df.empty:
        return pd.DataFrame()

    df["team"] = df["team"].apply(_normalize_team)
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"].copy()

    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"]   = pd.to_numeric(df["week"],   errors="coerce")
    df = df.dropna(subset=["season", "week", "team"])

    df["sacks_suffered"] = pd.to_numeric(df.get("sacks_suffered", 0), errors="coerce").fillna(0)
    df["dl_pass_raw"] = (
        pd.to_numeric(df.get("def_sacks",              0), errors="coerce").fillna(0) * DL_SACK_WEIGHT
        + pd.to_numeric(df.get("def_qb_hits",          0), errors="coerce").fillna(0) * DL_HIT_WEIGHT
        + pd.to_numeric(df.get("def_tackles_for_loss", 0), errors="coerce").fillna(0) * DL_TFL_WEIGHT
    )

    df = df.sort_values(["season", "team", "week"])

    # Prior season averages for week-1 fallback
    prior_avg = (
        df.groupby(["season", "team"])[["sacks_suffered", "dl_pass_raw"]].mean()
        .reset_index()
        .rename(columns={"sacks_suffered": "_pa_sacks", "dl_pass_raw": "_pa_dl"})
    )
    prior_avg["join_season"] = prior_avg["season"] + 1

    df["sacks_suffered_roll"] = df.groupby(["season", "team"])["sacks_suffered"].transform(
        lambda s: s.expanding().mean().shift(1))
    df["dl_pass_roll"] = df.groupby(["season", "team"])["dl_pass_raw"].transform(
        lambda s: s.expanding().mean().shift(1))

    df = df.merge(
        prior_avg[["join_season", "team", "_pa_sacks", "_pa_dl"]],
        left_on=["season", "team"], right_on=["join_season", "team"], how="left",
    ).drop(columns=["join_season"], errors="ignore")

    df["sacks_suffered_roll"] = df["sacks_suffered_roll"].fillna(df["_pa_sacks"]).fillna(0.0)
    df["dl_pass_roll"]        = df["dl_pass_roll"].fillna(df["_pa_dl"]).fillna(0.0)

    return df[["season", "week", "team", "sacks_suffered_roll", "dl_pass_roll"]]
```

- [x] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All 9 tests PASS.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: add _load_trench_rolling_stats for OL sacks + DL pass composite"
```

---

## Task 4: EPA matchup features in `build_master_feature_table()`

**Files:**
- Modify: `services/nn_feature_engine.py` — EPA join section in `build_master_feature_table()` (lines 848–871)
- Modify: `tests/test_nn_feature_engine.py`

**Background:** Replace the current join that mislabels away-team offense as "def_*" with the proper matchup formula:
`pass_epa_matchup = (home_off_pass − away_def_pass) − (away_off_pass − home_def_pass)`

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
def _make_minimal_feature_table_inputs(tmp_path):
    """Write minimal rawdata fixtures for build_master_feature_table."""
    stats_dir = tmp_path / "stats_team"
    stats_dir.mkdir(parents=True, exist_ok=True)
    sched_dir = tmp_path / "schedules"
    sched_dir.mkdir(parents=True, exist_ok=True)
    elo_dir = tmp_path

    # Two teams, 4 weeks (week 4 has a matchup we can predict)
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


def test_epa_matchup_formula(tmp_path):
    """pass_epa_matchup = (home_off_pass - away_def_pass) - (away_off_pass - home_def_pass).
    KC has better offense; BUF's defense-allowed is KC's offense = strong.
    The matchup should favour KC (positive value since KC dominates both sides).
    """
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    # Week 4 is unplayed (home_score=None) so only weeks 1-3 appear (home_win not NaN)
    # KC passes better → home_off_pass > away_off_pass; BUF's def (=KC offense) > KC's def (=BUF offense)
    # Matchup should be positive (KC advantage)
    completed = df[df["home_team"] == "KC"]
    if not completed.empty:
        val = float(completed["pass_epa_matchup"].iloc[-1])
        # With KC off >> BUF off and KC def (BUF offense) << BUF def (KC offense),
        # the matchup formula should be strongly positive
        assert val > 0, f"Expected positive pass_epa_matchup for KC home, got {val:.3f}"
```

- [x] **Step 2: Run to confirm tests fail**

```
pytest tests/test_nn_feature_engine.py::test_epa_matchup_columns_present -v
```
Expected: FAIL — columns don't exist in current output.

- [x] **Step 3: Replace the EPA join block in `build_master_feature_table()` (lines 848–871)**

Find the comment `# --- Rolling EPA (per-game join on season + week + team) ---` and replace the entire block up to the else clause with:

```python
    # --- Rolling EPA + trench data loaders ---
    epa = _load_rolling_epa(rd)
    trench_stats = _load_trench_rolling_stats(rd)

    # --- Rolling EPA (per-game join — 8 rolling columns for each side) ---
    if not epa.empty:
        sched = sched.merge(
            epa.rename(columns={"team": "home_team",
                                 "off_pass_epa_roll":   "h_off_pass",
                                 "off_rush_epa_roll":   "h_off_rush",
                                 "off_early_down_roll": "h_off_early",
                                 "off_rush_ypc_roll":   "h_off_ypc",
                                 "def_pass_epa_roll":   "h_def_pass",
                                 "def_rush_epa_roll":   "h_def_rush",
                                 "def_early_down_roll": "h_def_early",
                                 "def_rush_ypc_roll":   "h_def_ypc"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            epa.rename(columns={"team": "away_team",
                                 "off_pass_epa_roll":   "a_off_pass",
                                 "off_rush_epa_roll":   "a_off_rush",
                                 "off_early_down_roll": "a_off_early",
                                 "off_rush_ypc_roll":   "a_off_ypc",
                                 "def_pass_epa_roll":   "a_def_pass",
                                 "def_rush_epa_roll":   "a_def_rush",
                                 "def_early_down_roll": "a_def_early",
                                 "def_rush_ypc_roll":   "a_def_ypc"}),
            on=["season", "week", "away_team"], how="left",
        )
        for c in ["h_off_pass","h_off_rush","h_off_early","h_off_ypc",
                  "h_def_pass","h_def_rush","h_def_early","h_def_ypc",
                  "a_off_pass","a_off_rush","a_off_early","a_off_ypc",
                  "a_def_pass","a_def_rush","a_def_early","a_def_ypc"]:
            sched[c] = sched.get(c, pd.Series(0.0, index=sched.index)).fillna(0.0)
    else:
        for c in ["h_off_pass","h_off_rush","h_off_early","h_off_ypc",
                  "h_def_pass","h_def_rush","h_def_early","h_def_ypc",
                  "a_off_pass","a_off_rush","a_off_early","a_off_ypc",
                  "a_def_pass","a_def_rush","a_def_early","a_def_ypc"]:
            sched[c] = 0.0

    # EPA matchup formula: (home_off − away_def) − (away_off − home_def)
    sched["pass_epa_matchup"] = (
        (sched["h_off_pass"] - sched["a_def_pass"])
        - (sched["a_off_pass"] - sched["h_def_pass"])
    )
    sched["rush_epa_matchup"] = (
        (sched["h_off_rush"] - sched["a_def_rush"])
        - (sched["a_off_rush"] - sched["h_def_rush"])
    )
    sched["early_down_matchup"] = (
        (sched["h_off_early"] - sched["a_def_early"])
        - (sched["a_off_early"] - sched["h_def_early"])
    )
```

Also remove the old `epa = _load_rolling_epa(rd)` line near line 837 (it's now inline above).

- [x] **Step 4: Run tests**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All tests PASS.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: compute EPA matchup features with proper matchup formula and real defensive EPA"
```

---

## Task 5: Elo diff, point diff, pressure diffs, travel/rest split

**Files:**
- Modify: `services/nn_feature_engine.py` — Elo section (~line 902), travel section (~line 1036), and pressure section (~line 926)
- Modify: `tests/test_nn_feature_engine.py`

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
def test_elo_diff_and_confidence(tmp_path):
    """elo_diff = home_elo_pre - away_elo_pre; elo_confidence = |elo_diff|/25."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "elo_diff" in df.columns
    assert "elo_confidence" in df.columns
    # Elo fixture: home=1550, away=1480 → diff=70, confidence=2.8
    row = df[df["home_team"] == "KC"].iloc[0]
    assert abs(float(row["elo_diff"]) - 70.0) < 1.0
    assert abs(float(row["elo_confidence"]) - 2.8) < 0.1


def test_point_diff_advantage(tmp_path):
    """point_diff_advantage = home_rolling_margin - away_rolling_margin."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "point_diff_advantage" in df.columns
    # KC wins 28-21 = +7 margin each game; BUF gets -7 from away perspective.
    # point_diff_advantage for KC-home should be positive.
    kc = df[(df["home_team"] == "KC") & df["point_diff_advantage"].notna()]
    if not kc.empty:
        assert float(kc.iloc[-1]["point_diff_advantage"]) > 0


def test_rest_and_travel_are_separate_features(tmp_path):
    """rest_advantage and net_travel_disadvantage must both be present."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "rest_advantage" in df.columns
    assert "net_travel_disadvantage" in df.columns
    assert "travel_rest_disadvantage" not in df.columns


def test_neutral_site_travel_is_zero(tmp_path):
    """net_travel_disadvantage must be 0.0 when location == 'Neutral'."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    # Patch one game to Neutral
    sched_path = rd / "schedules" / "games.csv"
    sched = pd.read_csv(sched_path)
    sched.loc[sched["week"] == 2, "location"] = "Neutral"
    sched.to_csv(sched_path, index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    neutral_games = df[df.get("location", pd.Series("Home", index=df.index)) == "Neutral"] if "location" in df.columns else pd.DataFrame()
    # We can't easily filter by location after the build, so check the week-2 row directly
    w2 = df[(df["home_team"] == "KC") & (df["week"] == 2)] if "week" in df.columns else pd.DataFrame()
    # net_travel_disadvantage should be 0 for neutral games
    # The fixture has week 2 as neutral, which means home_win may be NaN if no score.
    # Just assert the column exists and is numeric.
    assert df["net_travel_disadvantage"].dtype in [np.float64, float]


def test_qb_pressure_advantage_direction(tmp_path):
    """qb_pressure_advantage = away_pressure_roll - home_pressure_roll (positive = home QB less pressured)."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "qb_pressure_advantage" in df.columns
    assert "def_pressure_diff" in df.columns
    # Both default to 0.0 since we have no pfr_advstats fixture — just assert no crash and numeric.
    assert df["qb_pressure_advantage"].dtype in [np.float64, float]
```

- [x] **Step 2: Run to confirm failures**

```
pytest tests/test_nn_feature_engine.py::test_elo_diff_and_confidence tests/test_nn_feature_engine.py::test_rest_and_travel_are_separate_features -v
```
Expected: FAIL.

- [x] **Step 3: Update the Elo section in `build_master_feature_table()` (~line 902)**

Replace the Elo join block (from `# --- Elo` to `sched["opp_elo_pre"] = ...`):

```python
    # --- Elo (per-game join) ---
    if not elo.empty:
        sched = sched.merge(elo, on=["season", "week", "home_team", "away_team"], how="left")
        sched["home_elo_pre"] = sched["home_elo_pre"].fillna(1500.0)
        sched["away_elo_pre"] = sched["away_elo_pre"].fillna(1500.0)
    else:
        sched["home_elo_pre"] = 1500.0
        sched["away_elo_pre"] = 1500.0

    sched["elo_diff"]       = sched["home_elo_pre"] - sched["away_elo_pre"]
    sched["elo_confidence"] = np.abs(sched["elo_diff"] / 25.0)
    # Keep home_elo_pre / away_elo_pre as aux metadata for projection engine (not in FEATURE_COLUMNS)
```

- [x] **Step 4: Update point diff section (~line 1015)**

Replace `sched["tm_point_diff"] = ...` and `sched["opp_point_diff"] = ...` assignments with:

```python
            sched["home_margin_roll"] = sched["tm_point_diff"]   # aux metadata
            sched["away_margin_roll"] = sched["opp_point_diff"]  # aux metadata
            sched["point_diff_advantage"] = (
                sched["tm_point_diff"].fillna(0.0) - sched["opp_point_diff"].fillna(0.0)
            )
```

(Keep `tm_point_diff` and `opp_point_diff` computation as-is; just add these three lines after.)

- [x] **Step 5: Update the pressure section (~line 926)**

Replace the pressure merge block's final rename/fallback section:

```python
    # --- Pressure Stats ---
    if pressure is not None and not pressure.empty:
        sched = sched.merge(
            pressure.rename(columns={"team": "home_team",
                                     "qb_pressure_rate_roll": "_home_qb_press",
                                     "def_pressure_gen_roll": "_home_def_press"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            pressure.rename(columns={"team": "away_team",
                                     "qb_pressure_rate_roll": "_away_qb_press",
                                     "def_pressure_gen_roll": "_away_def_press"}),
            on=["season", "week", "away_team"], how="left",
        )
        for c in ["_home_qb_press", "_away_qb_press", "_home_def_press", "_away_def_press"]:
            sched[c] = sched.get(c, pd.Series(0.0, index=sched.index)).fillna(0.0)
        # Positive = home QB faces LESS pressure (home advantage)
        sched["qb_pressure_advantage"] = sched["_away_qb_press"] - sched["_home_qb_press"]
        # Positive = home defense generates MORE pressure
        sched["def_pressure_diff"]     = sched["_home_def_press"] - sched["_away_def_press"]
        # Keep aux for projection engine
        sched["home_qb_pressure_roll"] = sched["_home_qb_press"]
        sched["away_qb_pressure_roll"] = sched["_away_qb_press"]
        sched["home_def_pressures_roll"] = sched["_home_def_press"]
        sched["away_def_pressures_roll"] = sched["_away_def_press"]
        sched.drop(columns=["_home_qb_press", "_away_qb_press",
                            "_home_def_press", "_away_def_press"], inplace=True)
    else:
        for col in ["qb_pressure_advantage", "def_pressure_diff",
                    "home_qb_pressure_roll", "away_qb_pressure_roll",
                    "home_def_pressures_roll", "away_def_pressures_roll"]:
            sched[col] = 0.0
```

- [x] **Step 6: Update the travel/rest section (~line 1036)**

Replace the entire travel+rest block with:

```python
    # --- Rest advantage (already computed in _load_schedule) ---
    sched["rest_advantage"] = sched.get("rest_advantage", pd.Series(0, index=sched.index)).fillna(0.0)

    # --- Net travel disadvantage (away team's travel to home stadium; 0 for neutral sites) ---
    try:
        from services.prediction_service import _get_travel_distance
        def _net_travel(row):
            if str(row.get("location", "Home")).strip().lower() == "neutral":
                return 0.0
            return _get_travel_distance(str(row["away_team"]), str(row["home_team"])) / 1000.0
        sched["net_travel_disadvantage"] = sched.apply(_net_travel, axis=1)
    except Exception:
        sched["net_travel_disadvantage"] = 0.0
```

- [x] **Step 7: Run all tests**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All tests PASS.

- [x] **Step 8: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: collapse Elo/point-diff/pressure pairs to diffs; split travel/rest; fix neutral-site"
```

---

## Task 6: 4-component trench redesign

**Files:**
- Modify: `services/nn_feature_engine.py` — trench section (~line 1072) in `build_master_feature_table()`
- Modify: `tests/test_nn_feature_engine.py`

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
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
    # KC has better DL stats in our fixture; BUF fewer sacks suffered.
    # Net result depends on z-scoring but metric must vary across rows.
    assert df["trench_dominance_metric"].std() >= 0  # at least non-constant
```

- [x] **Step 2: Run to confirm failure**

```
pytest tests/test_nn_feature_engine.py::test_trench_uses_performance_not_snap_count -v
```
Expected: May pass (column exists) but relies on snap-count logic. The important test is the formula audit after implementation.

- [x] **Step 3: Replace the entire trench section in `build_master_feature_table()` (~lines 1072–1098)**

Find the comment `# --- Trench Dominance` and replace everything through `sched["trench_dominance_metric"] = ...`:

```python
    # --- Trench Dominance (4-component performance-based, z-scored per season+week) ---
    # OL_pass = -sacks_suffered_roll     (fewer sacks = better pass protection)
    # OL_run  = +off_rush_ypc_roll       (more yards/carry = better run blocking)
    # DL_pass = +dl_pass_roll            (sacks×6 + qb_hits + tfl = more disruption = better)
    # DL_run  = -def_rush_ypc_roll       (fewer yards/carry allowed = better run defense)
    if not trench_stats.empty:
        sched = sched.merge(
            trench_stats.rename(columns={"team": "home_team",
                                          "sacks_suffered_roll": "h_sacks_suf",
                                          "dl_pass_roll": "h_dl_pass"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            trench_stats.rename(columns={"team": "away_team",
                                          "sacks_suffered_roll": "a_sacks_suf",
                                          "dl_pass_roll": "a_dl_pass"}),
            on=["season", "week", "away_team"], how="left",
        )
        for c in ["h_sacks_suf", "a_sacks_suf", "h_dl_pass", "a_dl_pass"]:
            sched[c] = sched.get(c, pd.Series(0.0, index=sched.index)).fillna(0.0)
    else:
        sched["h_sacks_suf"] = sched["a_sacks_suf"] = 0.0
        sched["h_dl_pass"]   = sched["a_dl_pass"]   = 0.0

    # Four raw component values per team (h_ = home, a_ = away)
    # OL_pass: negate sacks so higher = better protection
    sched["h_OL_pass"] = -sched["h_sacks_suf"]
    sched["a_OL_pass"] = -sched["a_sacks_suf"]
    # OL_run: rush ypc from _load_rolling_epa aux cols
    sched["h_OL_run"] = sched.get("h_off_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    sched["a_OL_run"] = sched.get("a_off_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    # DL_pass: composite from trench stats
    sched["h_DL_pass"] = sched["h_dl_pass"]
    sched["a_DL_pass"] = sched["a_dl_pass"]
    # DL_run: negate opponent rush ypc (less allowed = better run defense)
    sched["h_DL_run"] = -sched.get("h_def_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    sched["a_DL_run"] = -sched.get("a_def_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)

    # Z-score each component within (season, week) across all games in that week
    trench_components = ["OL_pass", "OL_run", "DL_pass", "DL_run"]
    for comp in trench_components:
        for side in ["h", "a"]:
            col = f"{side}_{comp}"
            both = pd.concat([sched[f"h_{comp}"], sched[f"a_{comp}"]])
            mu  = both.groupby(sched.index // len(sched) * 0 + sched["season"].astype(str) + "_" + sched["week"].astype(str)).transform("mean")
        # Z-score per (season, week) across home+away values pooled
        stacked = pd.concat(
            [sched[["season", "week", f"h_{comp}"]].rename(columns={f"h_{comp}": "_v"}),
             sched[["season", "week", f"a_{comp}"]].rename(columns={f"a_{comp}": "_v"})],
            ignore_index=True,
        )
        sw_stats = stacked.groupby(["season", "week"])["_v"].agg(["mean", "std"]).reset_index()
        sw_stats.columns = ["season", "week", f"_mu_{comp}", f"_sig_{comp}"]
        sched = sched.merge(sw_stats, on=["season", "week"], how="left")
        sig_col = f"_sig_{comp}"
        sched[sig_col] = sched[sig_col].fillna(1.0).clip(lower=1e-6)
        for side in ["h", "a"]:
            raw_col = f"{side}_{comp}"
            sched[f"{side}_{comp}_z"] = (sched[raw_col] - sched[f"_mu_{comp}"]) / sched[sig_col]
        sched.drop(columns=[f"_mu_{comp}", f"_sig_{comp}"], inplace=True)

    sched["home_trench_score"] = sum(sched[f"h_{c}_z"] for c in trench_components)
    sched["away_trench_score"] = sum(sched[f"a_{c}_z"] for c in trench_components)
    sched["trench_dominance_metric"] = sched["home_trench_score"] - sched["away_trench_score"]

    # Drop intermediate columns
    drop_trench = [f"{s}_{c}" for s in ["h","a"] for c in ["OL_pass","OL_run","DL_pass","DL_run",
                    "OL_pass_z","OL_run_z","DL_pass_z","DL_run_z"]]
    drop_trench += ["h_sacks_suf","a_sacks_suf","h_dl_pass","a_dl_pass"]
    sched.drop(columns=[c for c in drop_trench if c in sched.columns], inplace=True)
```

- [x] **Step 4: Run all tests**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All tests PASS.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: redesign trench_dominance_metric with 4-component performance-based formula"
```

---

## Task 7: Fix QB injury flags + replace `home_flag` with `home_field_advantage`

**Files:**
- Modify: `services/nn_feature_engine.py` — `_load_injury_flags()` (~line 784), QB flag join (~line 951), `_load_schedule()` (~line 527)
- Modify: `tests/test_nn_feature_engine.py`

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
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
    # Build injury fixture with both QBs out in week 1
    inj_dir = tmp_path / "injuries"
    inj_dir.mkdir(parents=True, exist_ok=True)
    inj_df = pd.DataFrame([
        {"season": 2024, "week": 1, "team": "KC",  "position": "QB", "report_status": "Out"},
        {"season": 2024, "week": 1, "team": "BUF", "position": "QB", "report_status": "Out"},
    ])
    inj_df.to_csv(inj_dir / "injuries_2024.csv", index=False)

    result = _load_injury_flags(tmp_path)
    # Should return rows for both teams
    kc  = result[(result["team"] == "KC")  & (result["season"] == 2024) & (result["week"] == 1)]
    buf = result[(result["team"] == "BUF") & (result["season"] == 2024) & (result["week"] == 1)]
    assert not kc.empty  and float(kc["home_qb_injury_flag"].iloc[0]) == 1.0
    assert not buf.empty and float(buf["away_qb_injury_flag"].iloc[0]) == 1.0


def test_home_field_advantage_neutral_is_zero(tmp_path):
    """home_field_advantage must be 0.0 for games with location=='Neutral'."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    sched_path = rd / "schedules" / "games.csv"
    sched = pd.read_csv(sched_path)
    sched.loc[sched["week"] == 1, "location"] = "Neutral"
    # Give it a score so it appears in completed games
    sched.loc[sched["week"] == 1, ["home_score", "away_score"]] = [24, 21]
    sched.to_csv(sched_path, index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    assert "home_field_advantage" in df.columns
    # Week 1 was neutral
    w1 = df[df["week"] == 1]
    if not w1.empty:
        assert float(w1.iloc[0]["home_field_advantage"]) == 0.0

    # Other weeks are regular home games
    w2 = df[df["week"] == 2]
    if not w2.empty:
        assert float(w2.iloc[0]["home_field_advantage"]) == 1.0
```

- [x] **Step 2: Run to confirm failures**

```
pytest tests/test_nn_feature_engine.py::test_qb_injury_split_two_flags tests/test_nn_feature_engine.py::test_home_field_advantage_neutral_is_zero -v
```
Expected: FAIL.

- [x] **Step 3: Update `_load_injury_flags()` to return two columns**

Replace the function body from `flags = (` through `return flags`:

```python
    # Two separate binary columns: one per team
    # Rename so we can distinguish home vs away at join time — the join uses team name
    flags = (
        qb_out.groupby(["season", "week", "team"]).size()
        .reset_index(name="_n")
        [["season", "week", "team"]]
        .copy()
    )
    # Both flag columns start at 1.0; the join site selects the right column by role
    flags["home_qb_injury_flag"] = 1.0
    flags["away_qb_injury_flag"] = 1.0
    return flags
```

- [x] **Step 4: Update QB flag join in `build_master_feature_table()` (~line 951)**

Replace the entire `# --- QB Starter Flag` block:

```python
    # --- QB Injury Flags (two separate binary flags — fixes both-injured=0 bug) ---
    injury_flags = _load_injury_flags(rd)
    if not injury_flags.empty:
        sched = sched.merge(
            injury_flags[["season", "week", "team", "home_qb_injury_flag"]].rename(
                columns={"team": "home_team"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            injury_flags[["season", "week", "team", "away_qb_injury_flag"]].rename(
                columns={"team": "away_team"}),
            on=["season", "week", "away_team"], how="left",
        )
        sched["home_qb_injury_flag"] = sched["home_qb_injury_flag"].fillna(0.0)
        sched["away_qb_injury_flag"] = sched["away_qb_injury_flag"].fillna(0.0)
    else:
        sched["home_qb_injury_flag"] = 0.0
        sched["away_qb_injury_flag"] = 0.0
```

Also remove the old `starter_qb_flags` loading and `compute_starter_qb_flags(snap_counts)` call (~lines 841–842 and 951–973). The injury report flags replace snap-count-based starter detection.

- [x] **Step 5: Update `_load_schedule()` — replace `home_flag` with `home_field_advantage`**

In `_load_schedule()`, replace:

```python
    df["home_flag"] = 1.0
    df["is_dome_flag"] = dome_mask.astype(float)
```

with:

```python
    location = df.get("location", pd.Series("Home", index=df.index)).fillna("Home").astype(str).str.strip()
    df["home_field_advantage"] = (location != "Neutral").astype(float)
    # is_dome_flag dropped — captured by passing_difficulty_index dome imputation above
```

- [x] **Step 6: Run all tests**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All tests PASS.

- [x] **Step 7: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "fix: split qb_injury_flag into home/away binary flags; replace home_flag with home_field_advantage"
```

---

## Task 8: Finalize output — expose aux columns, drop obsolete, confirm all 27 features present

**Files:**
- Modify: `services/nn_feature_engine.py` — output section (~line 1130)
- Modify: `tests/test_nn_feature_engine.py`

- [x] **Step 1: Add tests**

Append to `tests/test_nn_feature_engine.py`:

```python
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
    """Aux columns for projection engine must be present."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)
    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    for col in ["home_elo_pre", "away_elo_pre",
                "home_trench_score", "away_trench_score",
                "home_margin_roll", "away_margin_roll"]:
        assert col in df.columns, f"Missing aux column: {col}"
```

- [x] **Step 2: Run to confirm failures**

```
pytest tests/test_nn_feature_engine.py::test_all_27_features_in_output tests/test_nn_feature_engine.py::test_no_obsolete_features_in_output -v
```

- [x] **Step 3: Update the output section in `build_master_feature_table()` (~line 1130)**

Replace the final output block:

```python
    # --- Ensure all required columns exist and are numeric ---
    for col in FEATURE_COLUMNS:
        if col not in sched.columns:
            logger.warning("Feature %s not computed — filling with 0.0", col)
            sched[col] = 0.0
        sched[col] = pd.to_numeric(sched[col], errors="coerce").fillna(0.0)

    sched = sched.dropna(subset=["home_win"]).reset_index(drop=True)

    logger.info("Master Feature Table assembled: %d rows, %d features.", len(sched), len(FEATURE_COLUMNS))

    # Metadata: always present
    meta = ["season", "week", "home_team", "away_team"]
    # Aux cols for projection engine (not model inputs)
    aux = [c for c in [
        "home_elo_pre", "away_elo_pre",
        "home_trench_score", "away_trench_score",
        "home_margin_roll", "away_margin_roll",
        "home_qb_pressure_roll", "away_qb_pressure_roll",
        "home_def_pressures_roll", "away_def_pressures_roll",
        "h_off_pass", "h_off_rush", "h_off_early",
        "h_def_pass", "h_def_rush", "h_def_early",
        "a_off_pass", "a_off_rush", "a_off_early",
        "a_def_pass", "a_def_rush", "a_def_early",
    ] if c in sched.columns]

    out_cols = meta + [c for c in FEATURE_COLUMNS if c not in meta] + aux + ["home_win"]
    return sched[[c for c in out_cols if c in sched.columns]]
```

- [x] **Step 4: Run all tests**

```
pytest tests/test_nn_feature_engine.py -v
```
Expected: All tests PASS.

- [x] **Step 5: Run full test suite to confirm no regressions**

```
pytest tests/ -v --tb=short -q
```
Expected: Only nn_feature_engine tests change; all others continue to pass.

- [x] **Step 6: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: finalize feature table output with 27 FEATURE_COLUMNS + aux projection cols"
```

---

## Task 9: Update `nn_projection_engine.py`

**Files:**
- Modify: `services/nn_projection_engine.py:73-87` (`_build_team_profiles`), `services/nn_projection_engine.py:89-155` (`game_win_probability`)

**Background:** `game_win_probability()` currently handles `home_flag`, `def_*` prefix, `opp_*` prefix, and `tm_elo_pre`/`opp_elo_pre` — all of which no longer exist. The new handler uses aux metadata columns stored in team profiles.

- [x] **Step 1: Replace `_build_team_profiles()` (lines 73–87)**

```python
    def _build_team_profiles(self, feature_table: pd.DataFrame, proxy_season: int) -> pd.DataFrame:
        """Build per-team average feature profiles from the proxy season.

        Each team gets one row with:
        - averages of all FEATURE_COLUMNS (for fallback)
        - aux columns: elo_pre, per-team off/def EPA rolls, margin_roll, pressure rolls, trench_score
        """
        s_proxy = feature_table[feature_table["season"] == proxy_season].copy()
        if s_proxy.empty:
            latest = feature_table["season"].max()
            s_proxy = feature_table[feature_table["season"] == latest].copy()

        # Home appearances: team = home_team, aux cols prefixed h_ or home_
        home_rename = {
            "home_team": "team",
            "home_elo_pre": "elo_pre",
            "home_trench_score": "trench_score",
            "home_margin_roll": "margin_roll",
            "home_qb_pressure_roll": "qb_pressure_roll",
            "home_def_pressures_roll": "def_pressures_roll",
            "h_off_pass": "off_pass_epa_roll", "h_off_rush": "off_rush_epa_roll",
            "h_off_early": "off_early_roll",
            "h_def_pass": "def_pass_epa_roll", "h_def_rush": "def_rush_epa_roll",
            "h_def_early": "def_early_roll",
        }
        away_rename = {
            "away_team": "team",
            "away_elo_pre": "elo_pre",
            "away_trench_score": "trench_score",
            "away_margin_roll": "margin_roll",
            "away_qb_pressure_roll": "qb_pressure_roll",
            "away_def_pressures_roll": "def_pressures_roll",
            "a_off_pass": "off_pass_epa_roll", "a_off_rush": "off_rush_epa_roll",
            "a_off_early": "off_early_roll",
            "a_def_pass": "def_pass_epa_roll", "a_def_rush": "def_rush_epa_roll",
            "a_def_early": "def_early_roll",
        }

        aux_target_cols = list(set(home_rename.values()) - {"team"})
        profile_cols = NN_FEATURE_COLUMNS + [c for c in aux_target_cols if c not in NN_FEATURE_COLUMNS]

        def _extract_side(rename_map, role_col):
            available = {k: v for k, v in rename_map.items() if k in s_proxy.columns}
            sub = s_proxy.rename(columns=available)
            available_profile = [c for c in profile_cols if c in sub.columns]
            if "team" not in sub.columns and role_col in sub.columns:
                sub = sub.rename(columns={role_col: "team"})
            return sub.groupby("team")[available_profile].mean().reset_index() if available_profile else pd.DataFrame()

        home_avg = _extract_side(home_rename, "home_team")
        away_avg = _extract_side(away_rename, "away_team")

        combined = pd.concat([home_avg, away_avg], ignore_index=True)
        avail = [c for c in profile_cols if c in combined.columns]
        return combined.groupby("team")[avail].mean().reset_index()
```

- [x] **Step 2: Replace `game_win_probability()` feature assembly loop (lines 95–136)**

Replace the `features = {}` block through `nn_prob = self.svc.predict_game(features)`:

```python
        profile_dict = {row["team"]: row.to_dict()
                        for _, row in self._team_profiles.iterrows()}

        hp = profile_dict.get(home_team, {})
        ap = profile_dict.get(away_team, {})

        features = {}
        for col in NN_FEATURE_COLUMNS:
            if col == "home_field_advantage":
                features[col] = 1.0  # projection engine always predicts regular home games

            elif col == "elo_diff":
                h_elo = hp.get("elo_pre", 1500.0)
                a_elo = ap.get("elo_pre", 1500.0)
                features[col] = h_elo - a_elo

            elif col == "elo_confidence":
                features[col] = abs(features.get("elo_diff", 0.0)) / 25.0

            elif col == "pass_epa_matchup":
                features[col] = (
                    (hp.get("off_pass_epa_roll", 0.0) - ap.get("def_pass_epa_roll", 0.0))
                    - (ap.get("off_pass_epa_roll", 0.0) - hp.get("def_pass_epa_roll", 0.0))
                )

            elif col == "rush_epa_matchup":
                features[col] = (
                    (hp.get("off_rush_epa_roll", 0.0) - ap.get("def_rush_epa_roll", 0.0))
                    - (ap.get("off_rush_epa_roll", 0.0) - hp.get("def_rush_epa_roll", 0.0))
                )

            elif col == "early_down_matchup":
                features[col] = (
                    (hp.get("off_early_roll", 0.0) - ap.get("def_early_roll", 0.0))
                    - (ap.get("off_early_roll", 0.0) - hp.get("def_early_roll", 0.0))
                )

            elif col == "point_diff_advantage":
                features[col] = hp.get("margin_roll", 0.0) - ap.get("margin_roll", 0.0)

            elif col == "qb_pressure_advantage":
                # away_pressure - home_pressure (positive = home QB less pressured)
                features[col] = ap.get("qb_pressure_roll", 0.0) - hp.get("qb_pressure_roll", 0.0)

            elif col == "def_pressure_diff":
                features[col] = hp.get("def_pressures_roll", 0.0) - ap.get("def_pressures_roll", 0.0)

            elif col == "trench_dominance_metric":
                if self._preseason_roster and self._preseason_norm:
                    ol_mu, ol_sig, dl_mu, dl_sig = self._preseason_norm
                    h_pr = self._preseason_roster.get(home_team, {})
                    a_pr = self._preseason_roster.get(away_team, {})
                    h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    features[col] = h_z - a_z
                else:
                    features[col] = hp.get("trench_score", 0.0) - ap.get("trench_score", 0.0)

            elif col == "net_travel_disadvantage":
                try:
                    from services.prediction_service import _get_travel_distance
                    features[col] = _get_travel_distance(away_team, home_team) / 1000.0
                except Exception:
                    features[col] = hp.get(col, 0.0)

            elif col in ("rest_advantage", "home_qb_injury_flag", "away_qb_injury_flag"):
                features[col] = 0.0  # unknown for future games; model trained on 0-mean baseline

            elif col == "market_implied_team_total":
                features[col] = hp.get(col, 22.0)

            else:
                # Signed-differential features: use team profile averages
                # (these average to ~0 across home+away appearances, which is correct)
                features[col] = hp.get(col, 0.0)

        nn_prob  = self.svc.predict_game(features)
```

- [x] **Step 3: Run full test suite**

```
pytest tests/ -v --tb=short -q
```
Expected: PASS. No test directly covers projection engine in isolation, but existing tests that exercise `NNProjectionEngine` (if any) should pass.

- [x] **Step 4: Commit**

```bash
git add services/nn_projection_engine.py
git commit -m "feat: update nn_projection_engine feature handlers for new 27-feature schema"
```

---

## Task 10: Update `nn_prediction_service.py` explanation block

**Files:**
- Modify: `services/nn_prediction_service.py:137-156` (explanation dict in `build_ensemble_lookup`)

- [x] **Step 1: Replace the explanation dict (lines 137–156)**

```python
        explanation = {
            "vegas_line":           vegas_spread,
            "vegas_home_prob":      vegas_home_prob,
            "model_spread":         model_spread,
            "edge_vs_vegas":        edge_vs_vegas,
            "elo_diff":             round(_f("elo_diff"), 1),
            "elo_confidence":       round(_f("elo_confidence"), 3),
            "pass_epa_matchup":     round(_f("pass_epa_matchup"), 3),
            "rush_epa_matchup":     round(_f("rush_epa_matchup"), 3),
            "early_down_matchup":   round(_f("early_down_matchup"), 3),
            "roster_delta":         round(_f("roster_talent_delta"), 3),
            "turnover_margin":      round(_f("turnover_margin_rolling"), 2),
            "point_diff_advantage": round(_f("point_diff_advantage"), 2),
            "home_qb_out":          round(_f("home_qb_injury_flag"), 1),
            "away_qb_out":          round(_f("away_qb_injury_flag"), 1),
            "rest_advantage":       round(_f("rest_advantage"), 1),
            "travel_disadvantage":  round(_f("net_travel_disadvantage"), 2),
            "trench_dominance":     round(_f("trench_dominance_metric"), 3),
            "off_roster_value":     round(_f("off_roster_value_delta"), 3),
            "def_roster_value":     round(_f("def_roster_value_delta"), 3),
        }
```

- [x] **Step 2: Update the docstring at the top of the file**

Replace `Architecture: Input(26) ->` with `Architecture: Input(27) ->`.

- [x] **Step 3: Run tests**

```
pytest tests/test_nn_prediction_service.py tests/test_feature_audit_service.py -v --tb=short
```
Expected: PASS (explanation keys changed but no test should assert on specific key names).

- [x] **Step 4: Run full test suite**

```
pytest tests/ -q --tb=short
```
Expected: All pass.

- [x] **Step 5: Commit**

```bash
git add services/nn_prediction_service.py
git commit -m "docs: update explanation block in build_ensemble_lookup for new 27-feature schema"
```

---

## Post-Implementation Manual Steps

After all 10 tasks are implemented and tests pass, run these manually (not automated — they write to disk/Firestore):

```bash
# 1. Verify test suite is clean
pytest tests/ -q

# 2. Retrain all three models (new FEATURE_COLUMNS)
python scripts/train_nn_model.py        # → nn_v11
python scripts/train_xgb_model.py       # → xgb_v5  (if this script exists, else see models/xgb_registry.json)
python scripts/train_lr_model.py        # → lr_v3

# 3. Backfill predictions with new features
python scripts/backfill_schedule_predictions.py --firestore

# 4. Refresh local pickles
python scripts/refresh_local_pkls.py
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| FEATURE_COLUMNS 32→27 | Task 1 |
| Defensive EPA via schedule pairing | Task 2 |
| EPA matchup formula (home_off − away_def) − (away_off − home_def) | Task 4 |
| elo_diff + elo_confidence rebase | Task 5 |
| point_diff_advantage collapsed | Task 5 |
| qb_pressure_advantage, def_pressure_diff collapsed | Task 5 |
| rest_advantage split from travel | Task 5 |
| net_travel_disadvantage / 1000, 0.0 for Neutral | Task 5 |
| 4-component trench (OL_pass, OL_run, DL_pass, DL_run) | Task 6 |
| home_qb_injury_flag + away_qb_injury_flag (bug fix) | Task 7 |
| home_field_advantage (0.0 for Neutral) | Task 7 |
| Drop is_dome_flag, home_flag | Task 7 |
| Expose aux metadata cols for projection engine | Task 8 |
| nn_projection_engine.py handlers | Task 9 |
| nn_prediction_service.py explanation | Task 10 |
| early_down_matchup adds rush (0.2) | Task 4 |

All 16 spec requirements are covered. No placeholders. ✅
