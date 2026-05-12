"""services/nn_feature_engine.py -- NFL Neural Network Feature Engineering V2.

Re-architected to compute 27 advanced features (Raw, Synthetic, Contextual).
All rolling stats (EPA, turnovers, point differential) use expanding prior-game
means shifted by 1 to prevent data leakage. Elo is joined per-game when a week
column is available, falling back to season-level otherwise.
"""

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

# Canonical team abbreviation normalization
TEAM_ABBR_MAP = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"
}

TURNOVER_REGRESSION = 0.50

FEATURE_COLUMNS = [
    # Raw (10)
    "tm_elo_pre", "opp_elo_pre", "spread_line",
    "off_pass_epa", "def_pass_epa", "off_rush_epa", "def_rush_epa",
    "turnover_margin_rolling",
    "tm_point_diff", "opp_point_diff",
    # Synthetic (8)
    "early_down_pass_epa", "net_success_rate",
    "vegas_elo_spread_delta",
    "market_implied_team_total", "passing_difficulty_index",
    "travel_rest_disadvantage", "trench_dominance_metric",
    "roster_talent_delta",
    # Contextual (6)
    "home_flag", "div_game_flag", "surface_type", "is_dome_flag", "playoff_flag",
    "week",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_team(abbr: str) -> str:
    if not isinstance(abbr, str):
        return abbr
    return TEAM_ABBR_MAP.get(abbr.upper().strip(), abbr.upper().strip())


def _read_csv_safe(path: str, **kwargs) -> pd.DataFrame:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pd.read_csv(path, low_memory=False, **kwargs)
    except Exception as e:
        logger.warning("Failed to read %s: %s", path, e)
        return pd.DataFrame()


def _load_multi_season(pattern: str, rawdata_dir: Path) -> pd.DataFrame:
    files = sorted(glob.glob(str(rawdata_dir / pattern)))
    if not files:
        return pd.DataFrame()
    frames = [_read_csv_safe(f) for f in files]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ---------------------------------------------------------------------------
# Roster & Trenches (Season Level)
# ---------------------------------------------------------------------------

def compute_age_multiplier(age: float, pos: str) -> float:
    if pd.isna(age):
        return 1.0
    age = float(age)
    pos = str(pos).upper().strip() if pd.notna(pos) else ""
    if age < AGE_GROWTH_THRESHOLD:
        return GROWTH_MULTIPLIER
    elif age <= AGE_PRIME_UPPER:
        return PRIME_MULTIPLIER
    elif age <= AGE_MID_UPPER:
        return 1.0 - (MID_DECAY_RATE * (age - AGE_PRIME_UPPER))
    else:
        decay = OLD_SKILL_DECAY_RATE if pos in SKILL_POSITIONS else OLD_DECAY_RATE
        return max(0.3, 1.0 - (decay * (age - AGE_PRIME_UPPER)))


def compute_roster_features(rosters: pd.DataFrame):
    if rosters.empty:
        return {}
    rosters["age"] = pd.to_numeric(rosters.get("age"), errors="coerce")
    rosters["pfr_av"] = pd.to_numeric(rosters.get("pfr_approximate_value"), errors="coerce").fillna(0)
    rosters["gs"] = pd.to_numeric(rosters.get("games_started"), errors="coerce").clip(lower=1)

    cache = {}
    for (s, t), grp in rosters.groupby(["season", "alias"]):
        talent = ol_av = dl_av = 0.0
        for _, r in grp.iterrows():
            pos = str(r.get("position", "")).upper()
            mult = compute_age_multiplier(r["age"], pos)
            av = r["pfr_av"] * mult * r["gs"]
            talent += av
            if pos in OL_POSITIONS:
                ol_av += av
            if pos in DL_POSITIONS:
                dl_av += av
        cache[(s, t)] = {"talent": talent, "ol_av": ol_av, "dl_av": dl_av}
    return cache


# ---------------------------------------------------------------------------
# Feature Builders
# ---------------------------------------------------------------------------

def _load_schedule(rd: Path) -> pd.DataFrame:
    path = rd / "schedules" / "games.csv"
    if not path.exists():
        return pd.DataFrame()
    df = _read_csv_safe(str(path))
    if df.empty:
        return df

    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)

    # Filter to regular season only to prevent playoff inflation
    if "game_type" in df.columns:
        df = df[df["game_type"] == "REG"].copy()

    for c in ["spread_line", "total_line", "home_rest", "away_rest",
              "home_score", "away_score", "temp", "wind", "week"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Labels
    df["home_win"] = np.where(
        df["home_score"].notna() & df["away_score"].notna(),
        (df["home_score"] > df["away_score"]).astype(float),
        np.nan,
    )
    ties = df["home_score"].notna() & (df["home_score"] == df["away_score"])
    df.loc[ties, "home_win"] = 0.5

    # Dome weather imputation
    df["roof"] = df.get("roof", "").fillna("outdoors").astype(str).str.lower()
    dome_mask = df["roof"].isin(["dome", "closed"]) | df["roof"].str.contains("retractable")
    df.loc[dome_mask, "temp"] = df.loc[dome_mask, "temp"].fillna(72.0)
    df.loc[dome_mask, "wind"] = df.loc[dome_mask, "wind"].fillna(0.0)
    df["temp"] = df["temp"].fillna(55.0)
    df["wind"] = df["wind"].fillna(8.0)

    df["passing_difficulty_index"] = (df["wind"] * 1.5) + (40.0 - df["temp"]).clip(lower=0)
    df["rest_advantage"] = df.get("home_rest", 7).fillna(7) - df.get("away_rest", 7).fillna(7)

    df["div_game_flag"] = pd.to_numeric(df.get("div_game", 0), errors="coerce").fillna(0)
    df["playoff_flag"] = (df.get("game_type", "REG") != "REG").astype(float)
    df["home_flag"] = 1.0
    df["is_dome_flag"] = dome_mask.astype(float)
    surf = df.get("surface", "").astype(str).str.lower()
    df["surface_type"] = surf.str.contains("turf|artificial").astype(float)

    return df


def _load_rolling_epa(rd: Path) -> pd.DataFrame:
    """Load per-play EPA from nflverse weekly team stats.

    Returns per-(season, week, team) rolling prior-game averages using an
    expanding mean shifted by 1 so each row contains only pre-game information.
    Week-1 NaN values are filled with the prior season's average.
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

    # Per-play EPA
    for col, denom in [("passing_epa", "attempts"), ("rushing_epa", "carries")]:
        if col in df.columns and denom in df.columns:
            plays = pd.to_numeric(df[denom], errors="coerce").clip(lower=1)
            df[f"{col}_play"] = pd.to_numeric(df[col], errors="coerce").fillna(0) / plays
        elif col in df.columns:
            df[f"{col}_play"] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    if "passing_epa_play" not in df.columns:
        df["passing_epa_play"] = 0.0
    if "rushing_epa_play" not in df.columns:
        df["rushing_epa_play"] = 0.0

    cpoe = pd.to_numeric(df.get("passing_cpoe", 0), errors="coerce").fillna(0)
    df["early_down_epa"] = df["passing_epa_play"] * 0.8 + cpoe * 0.05

    df = df.sort_values(["season", "team", "week"])

    # Prior season per-team averages (for week-1 fallback)
    prior_avg = (
        df.groupby(["season", "team"])[["passing_epa_play", "rushing_epa_play", "early_down_epa"]]
        .mean()
        .rename(columns={"passing_epa_play": "pass_avg", "rushing_epa_play": "rush_avg",
                         "early_down_epa": "early_avg"})
        .reset_index()
    )
    prior_avg["join_season"] = prior_avg["season"] + 1

    # Rolling expanding prior-game mean (shift(1) excludes current game)
    df["off_pass_epa_roll"] = (
        df.groupby(["season", "team"])["passing_epa_play"]
        .transform(lambda s: s.expanding().mean().shift(1))
    )
    df["off_rush_epa_roll"] = (
        df.groupby(["season", "team"])["rushing_epa_play"]
        .transform(lambda s: s.expanding().mean().shift(1))
    )
    df["early_down_epa_roll"] = (
        df.groupby(["season", "team"])["early_down_epa"]
        .transform(lambda s: s.expanding().mean().shift(1))
    )

    # Fill week-1 NaN with prior season average
    df = df.merge(
        prior_avg[["join_season", "team", "pass_avg", "rush_avg", "early_avg"]],
        left_on=["season", "team"],
        right_on=["join_season", "team"],
        how="left",
    ).drop(columns=["join_season"], errors="ignore")

    df["off_pass_epa_roll"] = df["off_pass_epa_roll"].fillna(df["pass_avg"]).fillna(0.0)
    df["off_rush_epa_roll"] = df["off_rush_epa_roll"].fillna(df["rush_avg"]).fillna(0.0)
    df["early_down_epa_roll"] = df["early_down_epa_roll"].fillna(df["early_avg"]).fillna(0.0)

    return df[["season", "week", "team", "off_pass_epa_roll", "off_rush_epa_roll", "early_down_epa_roll"]]


def _load_box_stats_from_weekly(rd: Path) -> pd.DataFrame:
    """Compute rolling turnovers and first downs from nflverse weekly team stats.

    Returns per-(season, week, team) rolling prior-game averages using an
    expanding mean shifted by 1. Week-1 NaN values are filled with the prior
    season's average.
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

    # Turnovers: interceptions + rushing fumbles lost (+ receiving fumbles lost if present)
    to_parts = []
    for col in ["interceptions", "passing_interceptions"]:
        if col in df.columns:
            to_parts.append(pd.to_numeric(df[col], errors="coerce").fillna(0))
            break
    for col in ["rushing_fumbles_lost", "fumbles_lost"]:
        if col in df.columns:
            to_parts.append(pd.to_numeric(df[col], errors="coerce").fillna(0))
            break
    if "receiving_fumbles_lost" in df.columns:
        to_parts.append(pd.to_numeric(df["receiving_fumbles_lost"], errors="coerce").fillna(0))

    df["turnovers_game"] = sum(to_parts) if to_parts else 0.0

    # First downs
    fd_parts = []
    for col in ["passing_first_downs", "rushing_first_downs", "receiving_first_downs"]:
        if col in df.columns:
            fd_parts.append(pd.to_numeric(df[col], errors="coerce").fillna(0))

    df["first_downs_game"] = sum(fd_parts) if fd_parts else 0.0

    df = df.sort_values(["season", "team", "week"])

    # Prior season averages for week-1 fallback
    prior_avg = (
        df.groupby(["season", "team"])[["turnovers_game", "first_downs_game"]]
        .mean()
        .rename(columns={"turnovers_game": "to_avg", "first_downs_game": "fd_avg"})
        .reset_index()
    )
    prior_avg["join_season"] = prior_avg["season"] + 1

    # Rolling expanding prior-game mean
    df["turnovers_roll"] = (
        df.groupby(["season", "team"])["turnovers_game"]
        .transform(lambda s: s.expanding().mean().shift(1))
    )
    df["first_downs_roll"] = (
        df.groupby(["season", "team"])["first_downs_game"]
        .transform(lambda s: s.expanding().mean().shift(1))
    )

    # Fill week-1 NaN with prior season average
    df = df.merge(
        prior_avg[["join_season", "team", "to_avg", "fd_avg"]],
        left_on=["season", "team"],
        right_on=["join_season", "team"],
        how="left",
    ).drop(columns=["join_season"], errors="ignore")

    df["turnovers_roll"] = df["turnovers_roll"].fillna(df["to_avg"]).fillna(0.0)
    df["first_downs_roll"] = df["first_downs_roll"].fillna(df["fd_avg"]).fillna(0.0)

    return df[["season", "week", "team", "turnovers_roll", "first_downs_roll"]]


def _load_elo(rd: Path) -> pd.DataFrame:
    """Load pre-game Elo ratings from rawdata/elo_computed.csv.

    Computed by scripts/compute_elo.py using the Reddit methodology
    (K=12, HFA=15, MoV multiplier, optional quarter-by-quarter updates).
    Falls back to an empty DataFrame if the file doesn't exist.

    Returns columns: season, week, home_team, away_team, home_elo_pre, away_elo_pre
    """
    p = rd / "elo_computed.csv"
    if not p.exists():
        logger.warning("elo_computed.csv not found — Elo features will be 1500.0")
        return pd.DataFrame()

    df = _read_csv_safe(str(p))
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)
    df["home_elo_pre"] = pd.to_numeric(df["home_elo_pre"], errors="coerce")
    df["away_elo_pre"] = pd.to_numeric(df["away_elo_pre"], errors="coerce")

    return df[["season", "week", "home_team", "away_team", "home_elo_pre", "away_elo_pre"]].copy()


# ---------------------------------------------------------------------------
# Build Master Feature Table (V2)
# ---------------------------------------------------------------------------

def build_master_feature_table(
    rawdata_dir: Optional[str] = None,
    min_season: int = 2006,
    max_season: int = 2025,
) -> pd.DataFrame:
    rd = Path(rawdata_dir) if rawdata_dir else RAWDATA_DIR
    logger.info("Building Master Feature Table V2 (27 Features)...")

    sched = _load_schedule(rd)
    if sched.empty:
        raise ValueError("No schedule data found.")
    sched = sched[(sched["season"] >= min_season) & (sched["season"] <= max_season)].copy()

    epa = _load_rolling_epa(rd)
    elo = _load_elo(rd)
    box = _load_box_stats_from_weekly(rd)
    rosters = _load_multi_season("SeasonRoster-*.csv", rd)
    if not rosters.empty:
        rosters["alias"] = rosters["alias"].apply(_normalize_team)
    roster_cache = compute_roster_features(rosters)

    # --- Rolling EPA (per-game join on season + week + team) ---
    if not epa.empty:
        sched = sched.merge(
            epa.rename(columns={
                "team": "home_team",
                "off_pass_epa_roll": "off_pass_epa",
                "off_rush_epa_roll": "off_rush_epa",
                "early_down_epa_roll": "early_down_pass_epa",
            }),
            on=["season", "week", "home_team"],
            how="left",
        )
        sched = sched.merge(
            epa[["season", "week", "team", "off_pass_epa_roll", "off_rush_epa_roll"]].rename(columns={
                "team": "away_team",
                "off_pass_epa_roll": "def_pass_epa",
                "off_rush_epa_roll": "def_rush_epa",
            }),
            on=["season", "week", "away_team"],
            how="left",
        )
    else:
        for col in ["off_pass_epa", "def_pass_epa", "off_rush_epa", "def_rush_epa", "early_down_pass_epa"]:
            sched[col] = 0.0

    # --- Rolling Box Stats (per-game join on season + week + team) ---
    if not box.empty:
        sched = sched.merge(
            box.rename(columns={
                "team": "home_team",
                "turnovers_roll": "tm_turnovers_roll",
                "first_downs_roll": "tm_first_downs_roll",
            }),
            on=["season", "week", "home_team"],
            how="left",
        )
        sched = sched.merge(
            box.rename(columns={
                "team": "away_team",
                "turnovers_roll": "opp_turnovers_roll",
                "first_downs_roll": "opp_first_downs_roll",
            }),
            on=["season", "week", "away_team"],
            how="left",
        )
        raw_to = sched["opp_turnovers_roll"].fillna(0) - sched["tm_turnovers_roll"].fillna(0)
        sched["turnover_margin_rolling"] = raw_to * (1 - TURNOVER_REGRESSION)
        sched["net_success_rate"] = (
            sched["tm_first_downs_roll"].fillna(0) - sched["opp_first_downs_roll"].fillna(0)
        )
    else:
        sched["turnover_margin_rolling"] = 0.0
        sched["net_success_rate"] = 0.0

    # --- Elo (per-game join on season + week + home_team + away_team) ---
    if not elo.empty:
        sched = sched.merge(
            elo,
            on=["season", "week", "home_team", "away_team"],
            how="left",
        )
        # home perspective: tm_elo_pre = home_elo_pre
        # away perspective: opp_elo_pre = away_elo_pre
        sched["tm_elo_pre"] = sched["home_elo_pre"].fillna(1500.0)
        sched["opp_elo_pre"] = sched["away_elo_pre"].fillna(1500.0)
        sched.drop(columns=["home_elo_pre", "away_elo_pre"], inplace=True)
    else:
        sched["tm_elo_pre"] = 1500.0
        sched["opp_elo_pre"] = 1500.0


    # --- Market Synthetics ---
    elo_diff = sched["tm_elo_pre"] - sched["opp_elo_pre"]
    spread = sched.get("spread_line", pd.Series(0, index=sched.index)).fillna(0)
    sched["vegas_elo_spread_delta"] = np.abs((elo_diff / 25.0) - spread)

    total = sched.get("total_line", pd.Series(44.0, index=sched.index)).fillna(44.0)
    sched["market_implied_team_total"] = (total / 2) + (spread / 2)

    # --- Rolling Point Differential (no leakage) ---
    if "home_score" in sched.columns and "away_score" in sched.columns:
        completed = sched[
            sched["home_score"].notna() & sched["away_score"].notna()
        ][["season", "week", "home_team", "away_team", "home_score", "away_score"]].copy()

        if not completed.empty:
            home_m = completed[["season", "week", "home_team", "home_score", "away_score"]].copy()
            home_m["margin"] = home_m["home_score"] - home_m["away_score"]
            home_m = home_m.rename(columns={"home_team": "team"})[["season", "week", "team", "margin"]]

            away_m = completed[["season", "week", "away_team", "home_score", "away_score"]].copy()
            away_m["margin"] = away_m["away_score"] - away_m["home_score"]
            away_m = away_m.rename(columns={"away_team": "team"})[["season", "week", "team", "margin"]]

            margins = pd.concat([home_m, away_m], ignore_index=True).sort_values(
                ["season", "team", "week"]
            )

            # Prior season average for week-1 fallback
            prior_margin = (
                margins.groupby(["season", "team"])["margin"]
                .mean()
                .reset_index()
                .rename(columns={"margin": "margin_avg"})
            )
            prior_margin["join_season"] = prior_margin["season"] + 1

            margins["margin_roll"] = (
                margins.groupby(["season", "team"])["margin"]
                .transform(lambda s: s.expanding().mean().shift(1))
            )
            margins = margins.merge(
                prior_margin[["join_season", "team", "margin_avg"]],
                left_on=["season", "team"],
                right_on=["join_season", "team"],
                how="left",
            ).drop(columns=["join_season"], errors="ignore")
            margins["margin_roll"] = margins["margin_roll"].fillna(margins["margin_avg"]).fillna(0.0)

            sched = sched.merge(
                margins.rename(columns={"team": "home_team", "margin_roll": "tm_point_diff"})
                       [["season", "week", "home_team", "tm_point_diff"]],
                on=["season", "week", "home_team"],
                how="left",
            )
            sched = sched.merge(
                margins.rename(columns={"team": "away_team", "margin_roll": "opp_point_diff"})
                       [["season", "week", "away_team", "opp_point_diff"]],
                on=["season", "week", "away_team"],
                how="left",
            )
            sched["tm_point_diff"] = sched["tm_point_diff"].fillna(0.0)
            sched["opp_point_diff"] = sched["opp_point_diff"].fillna(0.0)
        else:
            sched["tm_point_diff"] = 0.0
            sched["opp_point_diff"] = 0.0
    else:
        sched["tm_point_diff"] = 0.0
        sched["opp_point_diff"] = 0.0

    # --- Travel + Rest Disadvantage ---
    # Lazy import to avoid circular dependency with prediction_service
    try:
        from services.prediction_service import _get_travel_distance
        sched["travel_miles"] = sched.apply(
            lambda r: _get_travel_distance(str(r["away_team"]), str(r["home_team"])), axis=1
        )
    except (ImportError, Exception):
        sched["travel_miles"] = 0.0

    # Combine rest advantage (home_rest - away_rest) with away team's travel distance.
    # Both components favor the home team when positive: more rest + more away travel.
    rest = sched.get("rest_advantage", pd.Series(0, index=sched.index)).fillna(0)
    sched["travel_rest_disadvantage"] = rest + sched["travel_miles"] / 1500.0

    # --- Roster / Trenches ---
    def _apply_roster(row, feature):
        h = roster_cache.get((row["season"], row["home_team"]), {}).get(feature, 0)
        a = roster_cache.get((row["season"], row["away_team"]), {}).get(feature, 0)
        if feature == "talent":
            return (h - a) / max(h + a, 1.0)
        return h - a

    if roster_cache:
        sched["roster_talent_delta"] = sched.apply(lambda r: _apply_roster(r, "talent"), axis=1)
        sched["trench_dominance_metric"] = sched.apply(lambda r: _apply_roster(r, "ol_av"), axis=1)
    else:
        sched["roster_talent_delta"] = 0.0
        sched["trench_dominance_metric"] = 0.0

    # --- Ensure all required columns exist and are numeric ---
    for col in FEATURE_COLUMNS:
        if col not in sched.columns:
            sched[col] = 0.0
        sched[col] = pd.to_numeric(sched[col], errors="coerce").fillna(0.0)

    sched = sched.dropna(subset=["home_win"]).reset_index(drop=True)

    logger.info("Master Feature Table V2 assembled: %d rows, 27 features.", len(sched))

    # Build column list without duplicates (week lives in both metadata and FEATURE_COLUMNS)
    meta = ["season", "week", "home_team", "away_team"]
    out_cols = meta + [c for c in FEATURE_COLUMNS if c not in meta] + ["home_win"]
    return sched[out_cols]
