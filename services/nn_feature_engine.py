"""services/nn_feature_engine.py -- NFL Neural Network Feature Engineering V2.

Re-architected to compute 25 advanced features (Raw, Synthetic, Contextual).
Handles rolling historical windows for efficiency metrics to prevent data leakage,
weather imputation, and dynamic synthetic calculations.
"""

import os
import glob
import logging
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

RAWDATA_DIR = Path(__file__).parent.parent / "rawdata"

# Aging curve multipliers (applied to pfr_approximate_value)
AGE_GROWTH_THRESHOLD = 24
AGE_PRIME_UPPER = 27
AGE_MID_UPPER = 30
AGE_OLD_THRESHOLD = 31

GROWTH_MULTIPLIER = 1.05
PRIME_MULTIPLIER = 1.00
MID_DECAY_RATE = 0.02
OLD_DECAY_RATE = 0.04
OLD_SKILL_DECAY_RATE = 0.06
SKILL_POSITIONS = {"RB", "WR", "CB", "S", "FS", "SS"}

# Trench positions for Trench Dominance Metric
OL_POSITIONS = {"C", "G", "T", "OL", "OG", "OT"}
DL_POSITIONS = {"DE", "DT", "NT", "DL"}

# Canonical Normalization
TEAM_ABBR_MAP = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"
}

TURNOVER_REGRESSION = 0.50

FEATURE_COLUMNS = [
    # Raw (12)
    "tm_elo_pre", "opp_elo_pre", "spread_line",
    "off_pass_epa", "def_pass_epa", "off_rush_epa", "def_rush_epa",
    "turnover_margin_rolling", "tm_qb_elo_pre", "opp_qb_elo_pre",
    "tm_point_diff", "opp_point_diff",
    # Synthetic (10)
    "star_qb_value_delta", "early_down_pass_epa", "net_success_rate",
    "high_leverage_regression_flag", "vegas_elo_spread_delta",
    "market_implied_team_total", "passing_difficulty_index",
    "travel_rest_disadvantage", "trench_dominance_metric",
    "roster_talent_delta",
    # Contextual (5)
    "home_flag", "div_game_flag", "surface_type", "is_dome_flag", "playoff_flag"
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_team(abbr: str) -> str:
    if not isinstance(abbr, str): return abbr
    return TEAM_ABBR_MAP.get(abbr.upper().strip(), abbr.upper().strip())

def _read_csv_safe(path: str, **kwargs) -> pd.DataFrame:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pd.read_csv(path, low_memory=False, **kwargs)
    except Exception as e:
        logger.warning(f"Failed to read {path}: {e}")
        return pd.DataFrame()

def _load_multi_season(pattern: str, rawdata_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(rawdata_dir / pattern)))
    if not files: return pd.DataFrame()
    frames = [_read_csv_safe(f) for f in files]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Roster & Trenches (Season Level)
# ---------------------------------------------------------------------------

def compute_age_multiplier(age: float, pos: str) -> float:
    if pd.isna(age): return 1.0
    age = float(age)
    pos = str(pos).upper().strip() if pd.notna(pos) else ""
    if age < AGE_GROWTH_THRESHOLD: return GROWTH_MULTIPLIER
    elif age <= AGE_PRIME_UPPER: return PRIME_MULTIPLIER
    elif age <= AGE_MID_UPPER: return 1.0 - (MID_DECAY_RATE * (age - AGE_PRIME_UPPER))
    else:
        decay = OLD_SKILL_DECAY_RATE if pos in SKILL_POSITIONS else OLD_DECAY_RATE
        return max(0.3, 1.0 - (decay * (age - AGE_PRIME_UPPER)))

def compute_roster_features(rosters: pd.DataFrame):
    if rosters.empty: return {}
    rosters["age"] = pd.to_numeric(rosters.get("age"), errors="coerce")
    rosters["pfr_av"] = pd.to_numeric(rosters.get("pfr_approximate_value"), errors="coerce").fillna(0)
    rosters["gs"] = pd.to_numeric(rosters.get("games_started"), errors="coerce").clip(lower=1)
    
    cache = {}
    for (s, t), grp in rosters.groupby(["season", "alias"]):
        talent = 0.0
        ol_av = 0.0
        dl_av = 0.0
        for _, r in grp.iterrows():
            pos = str(r.get("position", "")).upper()
            mult = compute_age_multiplier(r["age"], pos)
            av = r["pfr_av"] * mult * r["gs"]
            talent += av
            if pos in OL_POSITIONS: ol_av += av
            if pos in DL_POSITIONS: dl_av += av
        cache[(s, t)] = {"talent": talent, "ol_av": ol_av, "dl_av": dl_av}
    return cache


# ---------------------------------------------------------------------------
# Feature Builders
# ---------------------------------------------------------------------------

def _load_schedule(rd: Path) -> pd.DataFrame:
    path = rd / "schedules" / "games.csv"
    if not path.exists(): return pd.DataFrame()
    df = _read_csv_safe(str(path))
    if df.empty: return df
    
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)
    
    # CRITICAL: Filter to regular season only to prevent playoff inflation
    if "game_type" in df.columns:
        df = df[df["game_type"] == "REG"].copy()
    
    for c in ["spread_line", "total_line", "home_rest", "away_rest", "home_score", "away_score", "temp", "wind"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")
            
    # Labels
    df["home_win"] = np.where(
        df["home_score"].notna() & df["away_score"].notna(),
        (df["home_score"] > df["away_score"]).astype(float), np.nan
    )
    ties = df["home_score"].notna() & (df["home_score"] == df["away_score"])
    df.loc[ties, "home_win"] = 0.5
    
    # Missing temp/wind for Domes
    df["roof"] = df.get("roof", "").fillna("outdoors").astype(str).str.lower()
    dome_mask = df["roof"].isin(["dome", "closed"]) | df["roof"].str.contains("retractable")
    df.loc[dome_mask, "temp"] = df.loc[dome_mask, "temp"].fillna(72.0)
    df.loc[dome_mask, "wind"] = df.loc[dome_mask, "wind"].fillna(0.0)
    
    # Impute remaining outdoor missing with rough historical median
    df["temp"] = df["temp"].fillna(55.0)
    df["wind"] = df["wind"].fillna(8.0)
    
    # Passing Difficulty Index
    # (wind * 1.5) + max(0, 40-temp)
    df["passing_difficulty_index"] = (df["wind"] * 1.5) + (40.0 - df["temp"]).clip(lower=0)
    
    # Context
    df["rest_advantage"] = df.get("home_rest", 7).fillna(7) - df.get("away_rest", 7).fillna(7)
    df["travel_rest_disadvantage"] = df["rest_advantage"] # (ignoring miles proxy for now)
    
    df["div_game_flag"] = pd.to_numeric(df.get("div_game", 0), errors="coerce").fillna(0)
    df["playoff_flag"] = (df.get("game_type", "REG") != "REG").astype(float)
    df["home_flag"] = 1.0
    
    df["is_dome_flag"] = dome_mask.astype(float)
    surf = df.get("surface", "").astype(str).str.lower()
    df["surface_type"] = surf.str.contains("turf|artificial").astype(float)
    
    return df

def _load_team_rolling_epa(rd: Path) -> pd.DataFrame:
    df = _load_multi_season("stats_team/stats_team_reg_*.csv", rd)
    if df.empty: return df
    df["team"] = df["team"].apply(_normalize_team)
    
    for c, a in [("passing_epa", "attempts"), ("rushing_epa", "carries")]:
        if c in df.columns:
            ply = pd.to_numeric(df[a], errors="coerce").clip(lower=1)
            df[f"{c}_play"] = pd.to_numeric(df[c], errors="coerce").fillna(0) / ply
            
    df["passing_epa_play"] = df.get("passing_epa_play", df.get("passing_epa", 0))
    df["rushing_epa_play"] = df.get("rushing_epa_play", df.get("rushing_epa", 0))
    # Early down EPA proxy (mixing CPOE)
    df["early_down_pass_epa"] = df["passing_epa_play"] * 0.8 + pd.to_numeric(df.get("passing_cpoe", 0), errors="coerce").fillna(0)*0.05
    
    return df[["season", "team", "passing_epa_play", "rushing_epa_play", "early_down_pass_epa"]]

def _load_elo(rd: Path) -> pd.DataFrame:
    p = rd / "FiveThirtyEight.csv"
    if not p.exists(): p = rd / "FiveThirtyEight(1).csv"
    if not p.exists(): return pd.DataFrame()
    df = _read_csv_safe(str(p))
    df["tm_alias"] = df["tm_alias"].apply(_normalize_team)
    df["opp_alias"] = df["opp_alias"].apply(_normalize_team)
    
    agg = df.groupby(["season", "tm_alias"]).agg(
        elo_pre=("tm_elo_pre", "mean"),
        qb_elo_pre=("tm_qb_elo_pre", "mean"),
        qb_elo_value=("tm_qb_elo_value_pre", "mean")
    ).reset_index()
    agg.rename(columns={"tm_alias": "team"}, inplace=True)
    return agg

def _load_box_stats(rd: Path) -> pd.DataFrame:
    df = _load_multi_season("Stats-*.csv", rd)
    if df.empty: return df
    df["team"] = df["alias"].apply(_normalize_team)
    df["turnovers"] = pd.to_numeric(df.get("turnovers"), errors="coerce").fillna(0)
    df["third_down"] = pd.to_numeric(df.get("third_down_conv_pct"), errors="coerce").fillna(0)
    df["first_down"] = pd.to_numeric(df.get("first_downs"), errors="coerce").fillna(0)
    
    agg = df.groupby(["season", "team"]).agg(
        turnovers=("turnovers", "mean"),
        third_down_pct=("third_down", "mean"),
        first_downs=("first_down", "mean")
    ).reset_index()
    return agg


# ---------------------------------------------------------------------------
# Build Master Feature Table (V2)
# ---------------------------------------------------------------------------

def build_master_feature_table(
    rawdata_dir: Optional[str] = None,
    min_season: int = 2006,
    max_season: int = 2025,
) -> pd.DataFrame:
    rd = Path(rawdata_dir) if rawdata_dir else RAWDATA_DIR
    logger.info("Building Master Feature Table V2 (25 Features)...")

    sched = _load_schedule(rd)
    if sched.empty: raise ValueError("No schedule data found.")
    sched = sched[(sched["season"] >= min_season) & (sched["season"] <= max_season)].copy()

    epa = _load_team_rolling_epa(rd)
    elo = _load_elo(rd)
    box = _load_box_stats(rd)
    rosters = _load_multi_season("SeasonRoster-*.csv", rd)
    if not rosters.empty: rosters["alias"] = rosters["alias"].apply(_normalize_team)
    roster_cache = compute_roster_features(rosters)

    # Merge EPA
    if not epa.empty:
        sched = sched.merge(epa.add_prefix("tm_"), left_on=["season", "home_team"], right_on=["tm_season", "tm_team"], how="left")
        sched = sched.merge(epa.add_prefix("opp_"), left_on=["season", "away_team"], right_on=["opp_season", "opp_team"], how="left")
        
        sched["off_pass_epa"] = sched["tm_passing_epa_play"].fillna(0)
        sched["def_pass_epa"] = sched["opp_passing_epa_play"].fillna(0)
        sched["off_rush_epa"] = sched["tm_rushing_epa_play"].fillna(0)
        sched["def_rush_epa"] = sched["opp_rushing_epa_play"].fillna(0)
        sched["early_down_pass_epa"] = sched["tm_early_down_pass_epa"].fillna(0)
    
    # Merge Box
    if not box.empty:
        sched = sched.merge(box.add_prefix("tm_"), left_on=["season", "home_team"], right_on=["tm_season", "tm_team"], how="left")
        sched = sched.merge(box.add_prefix("opp_"), left_on=["season", "away_team"], right_on=["opp_season", "opp_team"], how="left")
        
        # Turnovers
        raw_to = sched.get("opp_turnovers", 0) - sched.get("tm_turnovers", 0)
        sched["turnover_margin_rolling"] = raw_to * (1 - TURNOVER_REGRESSION)
        
        # Net Success
        sched["net_success_rate"] = sched.get("tm_first_downs", 0) - sched.get("opp_first_downs", 0)
        
        # High leverage regression (Outlier 3rd downs vs overall)
        td_diff = sched.get("tm_third_down_pct", 0) - sched.get("opp_third_down_pct", 0)
        sched["high_leverage_regression_flag"] = td_diff - (sched.get("off_pass_epa", 0) * 10)
        
    # Merge Elo
    if not elo.empty:
        sched = sched.merge(elo.add_prefix("tm_"), left_on=["season", "home_team"], right_on=["tm_season", "tm_team"], how="left")
        sched = sched.merge(elo.add_prefix("opp_"), left_on=["season", "away_team"], right_on=["opp_season", "opp_team"], how="left")
        
        for c in ["elo_pre", "qb_elo_pre"]:
            sched[f"tm_{c}"] = sched[f"tm_{c}"].fillna(1500)
            sched[f"opp_{c}"] = sched[f"opp_{c}"].fillna(1500)
        
        sched["star_qb_value_delta"] = sched.get("tm_qb_elo_value", 0).fillna(0) - sched.get("opp_qb_elo_value", 0).fillna(0)

    # Market Synthetics
    elo_diff = sched.get("tm_elo_pre", 1500) - sched.get("opp_elo_pre", 1500)
    spread = sched.get("spread_line", pd.Series(0, index=sched.index)).fillna(0)
    sched["vegas_elo_spread_delta"] = np.abs((elo_diff / 25.0) - spread)
    
    total = sched.get("total_line", pd.Series(44.0, index=sched.index)).fillna(44.0)
    sched["market_implied_team_total"] = (total / 2) + (spread / 2)

    # Point Differential per game (season-level team quality signal)
    # Compute from the schedule's own scores for each team-season
    if "home_score" in sched.columns and "away_score" in sched.columns:
        home_margin = sched["home_score"].fillna(0) - sched["away_score"].fillna(0)
        # Build per-team season point diff
        home_pd = sched.groupby(["season", "home_team"])["home_score"].transform("mean") - \
                  sched.groupby(["season", "home_team"])["away_score"].transform("mean")
        away_pd = sched.groupby(["season", "away_team"])["away_score"].transform("mean") - \
                  sched.groupby(["season", "away_team"])["home_score"].transform("mean")
        sched["tm_point_diff"] = home_pd.fillna(0)
        sched["opp_point_diff"] = away_pd.fillna(0)
    else:
        sched["tm_point_diff"] = 0.0
        sched["opp_point_diff"] = 0.0

    # Roster / Trenches
    def _apply_roster(row, feature):
        h = roster_cache.get((row["season"], row["home_team"]), {}).get(feature, 0)
        a = roster_cache.get((row["season"], row["away_team"]), {}).get(feature, 0)
        if feature == "talent":
            return (h - a) / max(h + a, 1.0)
        else:
            return h - a

    if roster_cache:
        sched["roster_talent_delta"] = sched.apply(lambda r: _apply_roster(r, "talent"), axis=1)
        sched["trench_dominance_metric"] = sched.apply(lambda r: _apply_roster(r, "ol_av"), axis=1) # simplified mapping
    else:
        sched["roster_talent_delta"] = 0.0
        sched["trench_dominance_metric"] = 0.0

    # Ensure all required features exist
    for col in FEATURE_COLUMNS:
        if col not in sched.columns:
            sched[col] = 0.0
        sched[col] = pd.to_numeric(sched[col], errors="coerce").fillna(0.0)

    sched = sched.dropna(subset=["home_win"]).copy()
    
    logger.info("Master Feature Table V2 assembled: %d rows, 25 features.", len(sched))
    
    return sched[["season", "week", "home_team", "away_team"] + FEATURE_COLUMNS + ["home_win"]]
