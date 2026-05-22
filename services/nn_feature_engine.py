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

# Aging curve multipliers (applied to snap-based talent score)
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
    # Raw (9)
    "tm_elo_pre", "opp_elo_pre",
    "off_pass_epa", "def_pass_epa", "off_rush_epa", "def_rush_epa",
    "turnover_margin_rolling",
    "tm_point_diff", "opp_point_diff",
    # Synthetic (8)
    "early_down_pass_epa", "net_success_rate",
    "elo_confidence",
    "market_implied_team_total", "passing_difficulty_index",
    "travel_rest_disadvantage", "trench_dominance_metric",
    "roster_talent_delta",
    # Pressure & Injury (5)  — pfr_advstats 2018+, injuries 2009+
    "qb_pressure_rate", "opp_qb_pressure_rate",
    "def_pressure_gen", "opp_def_pressure_gen",
    "qb_injury_flag",
    # Roster Value (4)  — EPA-based WAR proxy; 2000+; punter from PBP
    "off_roster_value_delta", "def_roster_value_delta",
    "st_value_delta", "qb_resilience_delta",
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
# Roster Performance Grade (Performance-based, replaces snap-count talent)
# ---------------------------------------------------------------------------

def compute_roster_performance(stats_team: pd.DataFrame) -> dict:
    """Performance-based team grade from stats_team_week data.

    Cumulative season-to-date (weeks 1..W-1) offense + defense composite,
    z-scored against the league average at each (season, week).

    Offense: passing_epa + rushing_epa + TDs*2 - INTs*3
    Defense: def_sacks*1.5 + def_interceptions*2.5 + def_tackles_for_loss*0.5
    Grade  : off_z * 0.6 + def_z * 0.4  (approx N(0,1) at any given week)

    Returns {(season, week, team): grade}
    """
    if stats_team.empty:
        return {}

    reg = stats_team.copy()
    if "season_type" in reg.columns:
        reg = reg[reg["season_type"] == "REG"].copy()

    num_cols = [
        "passing_epa", "rushing_epa", "passing_tds", "rushing_tds",
        "passing_interceptions", "def_sacks", "def_interceptions",
        "def_tackles_for_loss",
    ]
    for col in num_cols:
        reg[col] = pd.to_numeric(reg.get(col, 0), errors="coerce").fillna(0.0)

    reg["season"] = pd.to_numeric(reg["season"], errors="coerce")
    reg["week"]   = pd.to_numeric(reg["week"],   errors="coerce")
    reg["team"]   = reg["team"].apply(_normalize_team)
    reg = reg.dropna(subset=["season", "week", "team"]).copy()

    # Deduplicate (one row per season+week+team)
    reg = reg.drop_duplicates(subset=["season", "week", "team"])

    # Per-game composite scores
    reg["off_raw"] = (
        reg["passing_epa"] + reg["rushing_epa"]
        + (reg["passing_tds"] + reg["rushing_tds"]) * 2.0
        - reg["passing_interceptions"] * 3.0
    )
    reg["def_raw"] = (
        reg["def_sacks"] * 1.5
        + reg["def_interceptions"] * 2.5
        + reg["def_tackles_for_loss"] * 0.5
    )

    reg = reg.sort_values(["season", "team", "week"])

    # Cumulative average of all prior weeks (shift then expanding mean — no leakage)
    reg["cum_off"] = (
        reg.groupby(["season", "team"])["off_raw"]
        .transform(lambda x: x.shift(1).expanding().mean())
    )
    reg["cum_def"] = (
        reg.groupby(["season", "team"])["def_raw"]
        .transform(lambda x: x.shift(1).expanding().mean())
    )

    # Week 1 has no prior data — drop those rows (grade stays 0.0 for those games)
    reg = reg.dropna(subset=["cum_off", "cum_def"]).copy()

    # Z-score within (season, week) across teams → ≈ N(0,1)
    for col in ["cum_off", "cum_def"]:
        reg[f"{col}_z"] = reg.groupby(["season", "week"])[col].transform(
            lambda x: (x - x.mean()) / (x.std(ddof=1) + 1e-6)
        )

    reg["grade"] = reg["cum_off_z"] * 0.6 + reg["cum_def_z"] * 0.4

    return {
        (int(r.season), int(r.week), r.team): float(r.grade)
        for r in reg[["season", "week", "team", "grade"]].itertuples(index=False)
    }


# ---------------------------------------------------------------------------
# Trench Snap-Count Features (O-line / D-line — kept for trench_dominance_metric)
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


def compute_roster_features(snap_counts: pd.DataFrame, rosters: pd.DataFrame) -> dict:
    """Build per-(season, team) talent scores from nflverse snap counts.

    Quality proxy: total regular-season snaps (offense + defense) weighted by
    an age multiplier derived from birth_date in the nflverse roster file.
    Replaces the legacy pfr_approximate_value * games_started metric.
    snap_counts covers 2012+; seasons before that return zero features.
    """
    if snap_counts.empty:
        return {}

    sc = snap_counts[snap_counts.get("game_type", "REG") == "REG"].copy()
    sc["total_snaps"] = (
        pd.to_numeric(sc["offense_snaps"], errors="coerce").fillna(0)
        + pd.to_numeric(sc["defense_snaps"], errors="coerce").fillna(0)
    )
    sc["season"] = pd.to_numeric(sc["season"], errors="coerce")
    sc["team"] = sc["team"].apply(_normalize_team)

    # Aggregate to season-level per player
    player_season = (
        sc.groupby(["season", "team", "pfr_player_id", "position"], as_index=False)["total_snaps"]
        .sum()
    )

    # Join birth_date from nflverse rosters for age multiplier
    if not rosters.empty:
        roster_ages = (
            rosters[["pfr_id", "birth_date", "season"]]
            .dropna(subset=["pfr_id"])
            .drop_duplicates(["pfr_id", "season"])
        )
        player_season = player_season.merge(
            roster_ages.rename(columns={"pfr_id": "pfr_player_id"}),
            on=["pfr_player_id", "season"],
            how="left",
        )
    else:
        player_season["birth_date"] = None

    # Compute age as of Sept 1 of the season year
    def _age_on_sept1(row) -> float:
        bd = row.get("birth_date")
        if pd.isna(bd) or not bd:
            return float("nan")
        try:
            born = pd.to_datetime(bd)
            sept1 = pd.Timestamp(int(row["season"]), 9, 1)
            return (sept1 - born).days / 365.25
        except Exception:
            return float("nan")

    player_season["age"] = player_season.apply(_age_on_sept1, axis=1)

    cache: dict = {}
    for (s, t), grp in player_season.groupby(["season", "team"]):
        talent = ol_av = dl_av = 0.0
        for _, r in grp.iterrows():
            pos = str(r.get("position", "")).upper()
            mult = compute_age_multiplier(r["age"], pos)
            score = r["total_snaps"] * mult
            talent += score
            if pos in OL_POSITIONS:
                ol_av += score
            if pos in DL_POSITIONS:
                dl_av += score
        cache[(s, t)] = {"talent": talent, "ol_av": ol_av, "dl_av": dl_av}
    return cache


# ---------------------------------------------------------------------------
# Snap-Count QB Starter Flag
# ---------------------------------------------------------------------------

def compute_starter_qb_flags(snap_counts: pd.DataFrame) -> dict:
    """Detect when a team's expected season QB starter is no longer playing.

    Expected starter = QB with most offense_snaps across weeks 1-3 of that season.
    Flag fires (1.0) from week 4+ when that QB takes <20% of team QB snaps.
    Returns {(season, week, team): 1.0 if starter out else 0.0}.
    Covers 2012+ (snap_counts availability); earlier seasons not included.
    """
    if snap_counts.empty:
        return {}

    sc = snap_counts.copy()
    if "game_type" in sc.columns:
        sc = sc[sc["game_type"] == "REG"].copy()

    sc["season"] = pd.to_numeric(sc["season"], errors="coerce")
    sc["week"]   = pd.to_numeric(sc["week"],   errors="coerce")
    sc["team"]   = sc["team"].apply(_normalize_team)
    sc["offense_snaps"] = pd.to_numeric(sc.get("offense_snaps", 0), errors="coerce").fillna(0)
    sc = sc.dropna(subset=["season", "week", "team"])

    qbs = sc[sc["position"] == "QB"].copy()
    if qbs.empty:
        return {}

    id_col = next(
        (c for c in ["pfr_player_id", "gsis_id", "player_name"] if c in qbs.columns),
        None,
    )
    if id_col is None:
        return {}

    # Season expected starter: QB with most combined snaps in weeks 1–3
    early = qbs[qbs["week"].between(1, 3)]
    starter_map = (
        early.groupby(["season", "team", id_col], as_index=False)["offense_snaps"]
        .sum()
        .sort_values("offense_snaps", ascending=False)
        .groupby(["season", "team"], as_index=False)
        .first()[["season", "team", id_col]]
        .rename(columns={id_col: "starter_id"})
    )

    # From week 4 onward, compare starter snaps to total team QB snaps
    week4 = qbs[qbs["week"] >= 4].copy()
    week4 = week4.merge(starter_map, on=["season", "team"], how="left")

    total_snaps = (
        week4.groupby(["season", "week", "team"], as_index=False)["offense_snaps"]
        .sum()
        .rename(columns={"offense_snaps": "total_qb_snaps"})
    )
    starter_snaps = (
        week4[week4[id_col] == week4["starter_id"]]
        .groupby(["season", "week", "team"], as_index=False)["offense_snaps"]
        .sum()
        .rename(columns={"offense_snaps": "starter_snaps"})
    )

    result = total_snaps.merge(starter_snaps, on=["season", "week", "team"], how="left")
    result["starter_snaps"]   = result["starter_snaps"].fillna(0.0)
    result["total_qb_snaps"]  = result["total_qb_snaps"].clip(lower=1)
    result["starter_pct"]     = result["starter_snaps"] / result["total_qb_snaps"]
    result["flag"]            = (result["starter_pct"] < 0.20).astype(float)

    return {
        (int(r.season), int(r.week), r.team): float(r.flag)
        for r in result[["season", "week", "team", "flag"]].itertuples(index=False)
    }


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
# Pressure & Injury Loaders
# ---------------------------------------------------------------------------

def _load_pressure_stats(rd: Path) -> pd.DataFrame:
    """Rolling QB pressure rate allowed and defensive pressure generated per (season, week, team).

    QB pressure rate: the weekly pressure rate of the team's primary QB (identified by most
    pressures faced = most dropbacks). Values in 0-1 range; clipped at 1.
    Def pressure gen: sum of all defenders' pressures per game.
    Both roll as expanding prior-game means shifted by 1 (no leakage). Week-1 NaN filled
    from prior season average. pfr_advstats covers 2018+; earlier seasons return zero.
    """
    pass_raw = _load_multi_season("pfr_advstats/advstats_week_pass_*.csv", rd)
    def_raw = _load_multi_season("pfr_advstats/advstats_week_def_*.csv", rd)

    def _roll_metric(df: pd.DataFrame, metric_col: str) -> pd.DataFrame:
        df = df.sort_values(["season", "team", "week"])
        prior = (
            df.groupby(["season", "team"])[metric_col].mean()
            .reset_index().rename(columns={metric_col: "_avg"})
        )
        prior["join_season"] = prior["season"] + 1
        df[f"{metric_col}_roll"] = (
            df.groupby(["season", "team"])[metric_col]
            .transform(lambda s: s.expanding().mean().shift(1))
        )
        df = df.merge(
            prior[["join_season", "team", "_avg"]],
            left_on=["season", "team"], right_on=["join_season", "team"], how="left",
        ).drop(columns=["join_season"], errors="ignore")
        df[f"{metric_col}_roll"] = df[f"{metric_col}_roll"].fillna(df["_avg"]).fillna(0.0)
        return df[["season", "week", "team", f"{metric_col}_roll"]]

    out = None

    if not pass_raw.empty:
        p = pass_raw[pass_raw.get("game_type", "REG") == "REG"].copy()
        p["season"] = pd.to_numeric(p["season"], errors="coerce")
        p["week"] = pd.to_numeric(p["week"], errors="coerce")
        p["team"] = p["team"].apply(_normalize_team)
        p["times_pressured"] = pd.to_numeric(p["times_pressured"], errors="coerce").fillna(0)
        p["qb_pressure_rate"] = (
            pd.to_numeric(p["times_pressured_pct"], errors="coerce").clip(0, 1).fillna(0)
        )
        p = p.dropna(subset=["season", "week", "team"])
        # Starting QB = player with most pressures faced that game
        starter = (
            p.sort_values("times_pressured", ascending=False)
            .groupby(["season", "week", "team"], as_index=False)
            .first()[["season", "week", "team", "qb_pressure_rate"]]
        )
        rolled = _roll_metric(starter, "qb_pressure_rate")
        out = rolled

    if not def_raw.empty:
        d = def_raw[def_raw.get("game_type", "REG") == "REG"].copy()
        d["season"] = pd.to_numeric(d["season"], errors="coerce")
        d["week"] = pd.to_numeric(d["week"], errors="coerce")
        d["team"] = d["team"].apply(_normalize_team)
        d["def_pressure_gen"] = pd.to_numeric(d["def_pressures"], errors="coerce").fillna(0)
        d = d.dropna(subset=["season", "week", "team"])
        team_press = (
            d.groupby(["season", "week", "team"], as_index=False)["def_pressure_gen"].sum()
        )
        rolled = _roll_metric(team_press, "def_pressure_gen")
        out = rolled if out is None else out.merge(rolled, on=["season", "week", "team"], how="outer")

    return out if out is not None else pd.DataFrame()


def _load_injury_flags(rd: Path) -> pd.DataFrame:
    """Binary QB injury flag per (season, week, team).

    Returns 1.0 if any QB is listed as Out or Doubtful on the official injury report,
    0.0 otherwise. Data covers 2009+; earlier seasons get 0 (active assumed).
    """
    df = _load_multi_season("injuries/injuries_*.csv", rd)
    if df.empty:
        return pd.DataFrame()

    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["team"] = df["team"].apply(_normalize_team)
    df = df.dropna(subset=["season", "week", "team"])

    # Don't filter on season_type — many rows have NaN there. The join to the
    # schedule (which is already REG-only) handles postseason exclusion naturally.
    if "season_type" in df.columns:
        df = df[df["season_type"].isin(["REG"]) | df["season_type"].isna()].copy()

    qb_out = df[
        (df["position"] == "QB") &
        (df["report_status"].isin(["Out", "Doubtful"]))
    ]
    if qb_out.empty:
        return pd.DataFrame()

    flags = (
        qb_out.groupby(["season", "week", "team"]).size()
        .reset_index(name="_n")
        .assign(qb_injury_flag=1.0)
        [["season", "week", "team", "qb_injury_flag"]]
    )
    return flags


# ---------------------------------------------------------------------------
# Build Master Feature Table (V2)
# ---------------------------------------------------------------------------

def build_master_feature_table(
    rawdata_dir: Optional[str] = None,
    min_season: int = 2006,
    max_season: int = 2025,
) -> pd.DataFrame:
    rd = Path(rawdata_dir) if rawdata_dir else RAWDATA_DIR
    logger.info("Building Master Feature Table V2 (26 Features)...")

    sched = _load_schedule(rd)
    if sched.empty:
        raise ValueError("No schedule data found.")
    sched = sched[(sched["season"] >= min_season) & (sched["season"] <= max_season)].copy()

    epa = _load_rolling_epa(rd)
    elo = _load_elo(rd)
    box = _load_box_stats_from_weekly(rd)
    pressure = _load_pressure_stats(rd)
    snap_counts = _load_multi_season("snap_counts/snap_counts_*.csv", rd)
    starter_qb_flags = compute_starter_qb_flags(snap_counts)
    nflverse_rosters = _load_multi_season("rosters/roster_*.csv", rd)
    roster_cache = compute_roster_features(snap_counts, nflverse_rosters)
    stats_team_weekly = _load_multi_season("stats_team/stats_team_week_*.csv", rd)
    roster_perf_cache = compute_roster_performance(stats_team_weekly)

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
    sched["elo_confidence"] = np.abs(elo_diff / 25.0)

    total = sched.get("total_line", pd.Series(44.0, index=sched.index)).fillna(44.0)
    sched["market_implied_team_total"] = total / 2

    # --- Pressure Stats (per-game join on season + week + team) ---
    if pressure is not None and not pressure.empty:
        pressure_cols = [c for c in pressure.columns if c not in ("season", "week", "team")]
        sched = sched.merge(
            pressure.rename(columns={
                "team": "home_team",
                **{c: c for c in pressure_cols if "qb_pressure" in c},
                **{"def_pressure_gen_roll": "def_pressure_gen"},
            }),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            pressure.rename(columns={
                "team": "away_team",
                "qb_pressure_rate_roll": "opp_qb_pressure_rate",
                "def_pressure_gen_roll": "opp_def_pressure_gen",
            }),
            on=["season", "week", "away_team"], how="left",
        )
        if "qb_pressure_rate_roll" in sched.columns:
            sched.rename(columns={"qb_pressure_rate_roll": "qb_pressure_rate"}, inplace=True)
    else:
        for col in ["qb_pressure_rate", "opp_qb_pressure_rate", "def_pressure_gen", "opp_def_pressure_gen"]:
            sched[col] = 0.0

    # --- QB Starter Flag (snap-count; signed delta: +1 = away starter out, -1 = home starter out) ---
    if starter_qb_flags:
        _qb_df = pd.DataFrame(
            [{"season": s, "week": w, "team": t, "flag": f}
             for (s, w, t), f in starter_qb_flags.items()]
        )
        sched = sched.merge(
            _qb_df.rename(columns={"team": "home_team", "flag": "_home_qb_out"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            _qb_df.rename(columns={"team": "away_team", "flag": "_away_qb_out"}),
            on=["season", "week", "away_team"], how="left",
        )
        sched["home_qb_out"] = sched["_home_qb_out"].fillna(0.0)
        sched["away_qb_out"] = sched["_away_qb_out"].fillna(0.0)
        # Positive = home team advantage (away starter is out)
        sched["qb_injury_flag"] = sched["away_qb_out"] - sched["home_qb_out"]
        sched = sched.drop(columns=["_home_qb_out", "_away_qb_out"])
    else:
        sched["qb_injury_flag"] = 0.0
        sched["home_qb_out"] = 0.0
        sched["away_qb_out"] = 0.0

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

    # --- Roster Performance Grade (performance-based; replaces snap-count talent) ---
    if roster_perf_cache:
        _perf_df = pd.DataFrame(
            [{"season": s, "week": w, "team": t, "grade": g}
             for (s, w, t), g in roster_perf_cache.items()],
        )
        sched = sched.merge(
            _perf_df.rename(columns={"team": "home_team", "grade": "_home_grade"}),
            on=["season", "week", "home_team"], how="left",
        )
        sched = sched.merge(
            _perf_df.rename(columns={"team": "away_team", "grade": "_away_grade"}),
            on=["season", "week", "away_team"], how="left",
        )
        sched["roster_talent_delta"] = (
            sched["_home_grade"].fillna(0.0) - sched["_away_grade"].fillna(0.0)
        )
        sched = sched.drop(columns=["_home_grade", "_away_grade"])
    else:
        sched["roster_talent_delta"] = 0.0

    # --- Trench Dominance (snap-count O-line score; kept separate from roster grade) ---
    def _trench(row):
        h = roster_cache.get((row["season"], row["home_team"]), {}).get("ol_av", 0)
        a = roster_cache.get((row["season"], row["away_team"]), {}).get("ol_av", 0)
        return h - a

    sched["trench_dominance_metric"] = sched.apply(_trench, axis=1) if roster_cache else 0.0

    # --- Roster Value (season-level WAR proxy; EPA + kicker FG+ + punter gross avg) ---
    try:
        from services.roster_value_service import compute_roster_value as _compute_rv
        rv_cache: dict = {}
        for _season in sorted(sched["season"].unique()):
            try:
                rv_cache.update(_compute_rv(int(_season), rd))
            except Exception as _e:
                logger.debug("roster_value skipped for %d: %s", _season, _e)

        def _rv_delta(row, feat):
            key = (int(row["season"]), int(row["week"]))
            h = rv_cache.get((*key, row["home_team"]), {}).get(feat, 0.0)
            a = rv_cache.get((*key, row["away_team"]), {}).get(feat, 0.0)
            return h - a

        for _feat, _col in [("off_roster_value", "off_roster_value_delta"),
                            ("def_roster_value", "def_roster_value_delta"),
                            ("st_value",         "st_value_delta"),
                            ("qb_resilience",    "qb_resilience_delta")]:
            sched[_col] = pd.to_numeric(
                sched.apply(lambda r, f=_feat: _rv_delta(r, f), axis=1),
                errors="coerce",
            ).fillna(0.0)
    except Exception as _e:
        logger.warning("roster_value_service unavailable: %s", _e)
        for _col in ["off_roster_value_delta", "def_roster_value_delta",
                     "st_value_delta", "qb_resilience_delta"]:
            sched[_col] = 0.0

    # --- Ensure all required columns exist and are numeric ---
    for col in FEATURE_COLUMNS:
        if col not in sched.columns:
            sched[col] = 0.0
        sched[col] = pd.to_numeric(sched[col], errors="coerce").fillna(0.0)

    sched = sched.dropna(subset=["home_win"]).reset_index(drop=True)

    logger.info("Master Feature Table V2 assembled: %d rows, %d features.", len(sched), len(FEATURE_COLUMNS))

    # Build column list without duplicates (week lives in both metadata and FEATURE_COLUMNS)
    meta = ["season", "week", "home_team", "away_team"]
    # home_qb_out / away_qb_out are extra explanation columns (not model inputs)
    extra = [c for c in ["home_qb_out", "away_qb_out"] if c in sched.columns]
    out_cols = meta + [c for c in FEATURE_COLUMNS if c not in meta] + extra + ["home_win"]
    return sched[out_cols]
