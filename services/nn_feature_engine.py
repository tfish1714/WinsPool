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

from services.constants import ELO_TO_SPREAD

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

# DL performance weights (applied to per-player / per-team stats)
DL_SACK_WEIGHT    = 6.0
DL_PRESSURE_WEIGHT = 1.5
DL_HIT_WEIGHT     = 1.0
DL_TFL_WEIGHT     = 1.0

# Canonical team abbreviation normalization
TEAM_ABBR_MAP = {
    "LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"
}

TURNOVER_REGRESSION = 0.50

# Features fed to the NN, XGB, and LR models. Three conceptual groups:
#   Schedule-context (Elo, rest, home-field, travel): captures structural
#     advantages that exist before the game starts.
#   Recent-form (EPA matchup, turnover margin, point differential, pressure):
#     rolling expanding-mean stats shifted 1 game to prevent data leakage.
#   Season-context (roster talent, win rate, week, surface, playoff flag):
#     longer-horizon signals that stabilise over the course of a season.
# spread_line was intentionally removed to prevent Vegas-line leakage:
# including it caused the model to back-solve the spread rather than
# independently predict outcomes, which inflated accuracy artificially.
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


def compute_roster_features(
    snap_counts: pd.DataFrame,
    rosters: pd.DataFrame,
    team_stats: pd.DataFrame | None = None,
) -> dict:
    """Build per-(season, team) talent scores from nflverse snap counts + team defensive stats.

    OL quality: snap count × age multiplier (volume proxy for line depth and continuity).
    DL quality: sacks×6 + qb_hits×1.5 + tfl×1 from team-level weekly stats, summed per season.
    snap_counts covers 2012+; team_stats covers 2020+.
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

    player_season = (
        sc.groupby(["season", "team", "pfr_player_id", "position"], as_index=False)["total_snaps"]
        .sum()
    )

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

    # OL scores from snap counts
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
        cache[(s, t)] = {"talent": talent, "ol_av": ol_av, "dl_av": dl_av, "dl_perf": 0.0}

    # DL performance from team-level stats (sacks + qb_hits + tfl)
    if team_stats is not None and not team_stats.empty:
        ts = team_stats.copy()
        if "season_type" in ts.columns:
            ts = ts[ts["season_type"] == "REG"]
        elif "game_type" in ts.columns:
            ts = ts[ts["game_type"] == "REG"]
        ts["season"] = pd.to_numeric(ts["season"], errors="coerce")
        ts["team"]   = ts["team"].apply(_normalize_team)
        ts["_dl"] = (
            pd.to_numeric(ts.get("def_sacks",              0), errors="coerce").fillna(0) * DL_SACK_WEIGHT
            + pd.to_numeric(ts.get("def_qb_hits",          0), errors="coerce").fillna(0) * DL_HIT_WEIGHT
            + pd.to_numeric(ts.get("def_tackles_for_loss", 0), errors="coerce").fillna(0) * DL_TFL_WEIGHT
        )
        for (s, t), grp in ts.groupby(["season", "team"]):
            key = (int(s), t)
            if key in cache:
                cache[key]["dl_perf"] = float(grp["_dl"].sum())

    return cache


def compute_preseason_roster_features(target_season: int, rawdata_dir) -> dict:
    """Build per-team OL snap quality + DL performance from current roster + prior-season data.

    OL: snap count × age multiplier for each OL player on the target-season roster,
        using the prior season's snap data. Rookies/new signings get median × 0.5.
    DL: individual sacks×6 + pressures×1.5 + qb_hits×1 from prior-season advstats_def,
        age-adjusted and summed per team. Rookies get team-average prior-year contribution.

    Returns {team: {"ol_av": float, "dl_perf": float}}.
    """
    prior = target_season - 1
    roster_path  = Path(rawdata_dir) / "rosters"      / f"roster_{target_season}.csv"
    snap_path    = Path(rawdata_dir) / "snap_counts"  / f"snap_counts_{prior}.csv"
    adv_def_path = Path(rawdata_dir) / "pfr_advstats" / f"advstats_week_def_{prior}.csv"

    if not roster_path.exists() or not snap_path.exists():
        return {}

    roster = pd.read_csv(roster_path, low_memory=False)
    snaps  = pd.read_csv(snap_path,  low_memory=False)

    if "game_type" in snaps.columns:
        snaps = snaps[snaps["game_type"] == "REG"].copy()
    snaps["offense_snaps"] = pd.to_numeric(snaps["offense_snaps"], errors="coerce").fillna(0)
    snaps["defense_snaps"] = pd.to_numeric(snaps["defense_snaps"], errors="coerce").fillna(0)

    # Build per-player season totals, keyed by both pfr_player_id and name
    player_snaps = snaps.groupby("pfr_player_id")[["offense_snaps", "defense_snaps"]].sum().reset_index()
    pos_per_player = (
        snaps.groupby("pfr_player_id")["position"]
        .agg(lambda x: x.mode().iloc[0] if len(x) > 0 else "")
        .reset_index()
    )
    player_snaps = player_snaps.merge(pos_per_player, on="pfr_player_id", how="left")
    player_snaps["position"] = player_snaps["position"].fillna("").str.upper()

    # Name → season snap totals (for players where pfr_id matching fails)
    name_snaps = snaps.groupby("player")[["offense_snaps", "defense_snaps"]].sum().reset_index()
    name_to_snaps: dict = {
        row["player"]: (row["offense_snaps"], row["defense_snaps"])
        for _, row in name_snaps.iterrows()
    }

    ol_vets = player_snaps[player_snaps["position"].isin(OL_POSITIONS)]["offense_snaps"]
    ol_median = float(ol_vets.median()) if not ol_vets.empty else 300.0

    # DL performance: player-level advstats, keyed by pfr_player_id and name
    dl_by_pfr:  dict = {}  # {pfr_player_id: score}
    dl_by_name: dict = {}  # {player_name: score}
    dl_avg_per_player = 0.0
    if adv_def_path.exists():
        adv = pd.read_csv(adv_def_path, low_memory=False)
        if "game_type" in adv.columns:
            adv = adv[adv["game_type"] == "REG"].copy()
        adv["_dl"] = (
            pd.to_numeric(adv["def_sacks"],        errors="coerce").fillna(0) * DL_SACK_WEIGHT
            + pd.to_numeric(adv.get("def_pressures", pd.Series(0, index=adv.index)), errors="coerce").fillna(0) * DL_PRESSURE_WEIGHT
            + pd.to_numeric(adv["def_times_hitqb"], errors="coerce").fillna(0) * DL_HIT_WEIGHT
        )
        dl_by_pfr  = adv.groupby("pfr_player_id")["_dl"].sum().to_dict()
        dl_by_name = adv.groupby("pfr_player_name")["_dl"].sum().to_dict()
        all_scores = list(dl_by_pfr.values())
        if all_scores:
            dl_avg_per_player = float(np.mean(all_scores))

    # Age as of September 1 of target season
    sep1 = pd.Timestamp(f"{target_season}-09-01")
    roster = roster.copy()
    roster["birth_date"] = pd.to_datetime(roster["birth_date"], errors="coerce")
    roster["age"] = ((sep1 - roster["birth_date"]).dt.days / 365.25)
    roster["position"] = roster["position"].fillna("").str.upper()
    roster["team"] = roster["team"].apply(_normalize_team)

    # pfr_id → season snap lookup
    pfr_to_snaps: dict = {
        row["pfr_player_id"]: (row["offense_snaps"], row["defense_snaps"])
        for _, row in player_snaps.iterrows()
    }

    result = {}
    for team, grp in roster.groupby("team"):
        ol_av   = 0.0
        dl_perf = 0.0
        for _, p in grp.iterrows():
            pos  = str(p["position"]).upper().strip()
            age  = float(p["age"]) if pd.notna(p["age"]) else 26.0
            mult = compute_age_multiplier(age, pos)
            name = str(p.get("full_name", ""))
            pid  = str(p.get("pfr_id", "")) if pd.notna(p.get("pfr_id")) else ""

            if pos in OL_POSITIONS:
                # Try pfr_id first, fall back to name
                snaps_tup = pfr_to_snaps.get(pid) or name_to_snaps.get(name)
                off_snaps = snaps_tup[0] if snaps_tup else None
                snp = off_snaps if off_snaps is not None else (ol_median * 0.5)
                ol_av += snp * mult

            elif pos in DL_POSITIONS:
                # Try pfr_id first, fall back to name
                raw_dl = dl_by_pfr.get(pid) or dl_by_name.get(name)
                if raw_dl is None:
                    raw_dl = dl_avg_per_player * 0.5
                dl_perf += raw_dl * mult

        result[team] = {"ol_av": ol_av, "dl_perf": dl_perf}

    return result


# ---------------------------------------------------------------------------
# Player EPA Loading
# ---------------------------------------------------------------------------

def _load_player_epa(prior_season: int, rawdata_dir) -> pd.DataFrame:
    """Load and aggregate per-player season EPA totals from stats_player.

    Returns one row per player with cumulative REG-season EPA totals and
    per-play rate columns (pass_epa_rate, recv_epa_rate, rush_epa_rate).
    Returns empty DataFrame if file not found.
    """
    path = Path(rawdata_dir) / "stats_player" / f"stats_player_regpost_{prior_season}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path, low_memory=False)
    df = df[df["season_type"] == "REG"].copy()
    if df.empty:
        return pd.DataFrame()

    for col in ("passing_epa", "rushing_epa", "receiving_epa", "attempts", "carries", "targets"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    agg = df.groupby(["player_id", "player_display_name", "position", "recent_team"], as_index=False).agg(
        passing_epa=("passing_epa", "sum"),
        rushing_epa=("rushing_epa", "sum"),
        receiving_epa=("receiving_epa", "sum"),
        attempts=("attempts", "sum"),
        carries=("carries", "sum"),
        targets=("targets", "sum"),
    )

    agg["pass_epa_rate"] = agg["passing_epa"] / agg["attempts"].clip(lower=1)
    agg["recv_epa_rate"] = agg["receiving_epa"] / agg["targets"].clip(lower=1)
    agg["rush_epa_rate"] = agg["rushing_epa"] / agg["carries"].clip(lower=1)

    return agg


# ---------------------------------------------------------------------------
# Preseason Player Profiles
# ---------------------------------------------------------------------------

# Position group sets
_OFF_OL_POS = {"LT", "LG", "C", "RG", "RT"}

# Depth-chart snap allocation weights for pass game (QB=65%, WR/TE=35%)
_WR_TE_WEIGHTS = {"WR_1": 0.40, "WR_2": 0.25, "WR_3": 0.10,
                  "TE_1": 0.20, "TE_2": 0.05}

# Depth-chart snap allocation weights for rush game (RB=75%, OL normalised=25%)
_RB_WEIGHTS = {"RB_1": 0.50, "RB_2": 0.25}


def _preseason_offense(
    depth_charts: pd.DataFrame,
    player_epa: pd.DataFrame,
    roster: pd.DataFrame,
    snap_counts: pd.DataFrame,
    season: int,
) -> dict:
    """Build per-team offensive EPA estimates from depth chart + prior-season player stats.

    Returns {team: {off_pass_epa, off_rush_epa, qb_tier, ol_av}}.
    """
    epa_by_id:   dict = {}
    epa_by_name: dict = {}
    if not player_epa.empty:
        for _, row in player_epa.iterrows():
            epa_by_id[str(row["player_id"])] = row.to_dict()
            epa_by_name[str(row["player_display_name"]).lower()] = row.to_dict()

    def _lg_avg(col: str, min_vol: str, min_val: int) -> float:
        if player_epa.empty:
            return 0.0
        qualified = player_epa[player_epa[min_vol] >= min_val]
        if qualified.empty:
            return 0.0
        return float(qualified[col].mean())

    lg_pass_rate  = _lg_avg("pass_epa_rate", "attempts", 100)
    lg_recv_rate  = _lg_avg("recv_epa_rate", "targets",  20)
    lg_rush_rate  = _lg_avg("rush_epa_rate", "carries",  50)

    ROOKIE_DISC = 0.75

    def _lookup(gsis_id: str, name: str) -> dict | None:
        row = epa_by_id.get(str(gsis_id))
        if row is None:
            row = epa_by_name.get(str(name).lower())
        return row

    sep1 = pd.Timestamp(f"{season}-09-01")
    roster_cp = roster.copy()
    roster_cp["birth_date"] = pd.to_datetime(roster_cp["birth_date"], errors="coerce")
    roster_cp["age"] = ((sep1 - roster_cp["birth_date"]).dt.days / 365.25)
    pfr_to_snaps = {}
    if not snap_counts.empty:
        sc = snap_counts.copy()
        if "game_type" in sc.columns:
            sc = sc[sc["game_type"] == "REG"]
        sc["offense_snaps"] = pd.to_numeric(sc["offense_snaps"], errors="coerce").fillna(0)
        pfr_to_snaps = sc.groupby("pfr_player_id")["offense_snaps"].sum().to_dict()
        name_snaps = sc.groupby("player")["offense_snaps"].sum().to_dict()
    else:
        name_snaps = {}
    ol_vets = pd.to_numeric(
        pd.Series(list(pfr_to_snaps.values())), errors="coerce"
    ).dropna()
    ol_median = float(ol_vets.median()) if not ol_vets.empty else 300.0

    gsis_to_pfr = {}
    if not roster_cp.empty and "gsis_id" in roster_cp.columns and "pfr_id" in roster_cp.columns:
        gsis_to_pfr = {str(r["gsis_id"]): str(r["pfr_id"])
                       for _, r in roster_cp.iterrows()
                       if pd.notna(r.get("pfr_id"))}

    result = {}
    for team, grp in depth_charts.groupby("team"):
        off_pass_epa = 0.0
        off_rush_epa = 0.0
        qb_tier      = lg_pass_rate * ROOKIE_DISC
        ol_av        = 0.0

        # QB (65% of off_pass_epa)
        qb_rows = grp[(grp["pos_abb"] == "QB") & (grp["pos_rank"] == 1)]
        if not qb_rows.empty:
            r = qb_rows.iloc[0]
            data = _lookup(r["gsis_id"], r["player_name"])
            rate = data["pass_epa_rate"] if data and data.get("attempts", 0) >= 100 \
                else lg_pass_rate * ROOKIE_DISC
            qb_tier = rate
            off_pass_epa += 0.65 * rate

        # WR/TE (35% of off_pass_epa split by slot weights)
        for pos_abb, base_key in [("WR", "WR"), ("TE", "TE")]:
            for rank in [1, 2, 3]:
                weight_key = f"{base_key}_{rank}"
                if weight_key not in _WR_TE_WEIGHTS:
                    continue
                slot_rows = grp[(grp["pos_abb"] == pos_abb) & (grp["pos_rank"] == rank)]
                if slot_rows.empty:
                    rate = lg_recv_rate * ROOKIE_DISC
                else:
                    data = _lookup(slot_rows.iloc[0]["gsis_id"], slot_rows.iloc[0]["player_name"])
                    rate = data["recv_epa_rate"] if data and data.get("targets", 0) >= 10 \
                        else lg_recv_rate * ROOKIE_DISC
                off_pass_epa += _WR_TE_WEIGHTS[weight_key] * rate

        # RB
        for rank, weight in [(1, 0.50), (2, 0.25)]:
            rb_rows = grp[(grp["pos_abb"] == "RB") & (grp["pos_rank"] == rank)]
            if rb_rows.empty:
                rate = lg_rush_rate * ROOKIE_DISC
            else:
                data = _lookup(rb_rows.iloc[0]["gsis_id"], rb_rows.iloc[0]["player_name"])
                rate = data["rush_epa_rate"] if data and data.get("carries", 0) >= 30 \
                    else lg_rush_rate * ROOKIE_DISC
            off_rush_epa += weight * rate

        # OL snap × age quality (also stored as ol_av for trench metric)
        ol_grp = grp[grp["pos_abb"].isin(_OFF_OL_POS)]
        for _, p in ol_grp.iterrows():
            gid  = str(p["gsis_id"])
            name = str(p["player_name"])
            pfr  = gsis_to_pfr.get(gid, "")
            roster_match = roster_cp[roster_cp["gsis_id"] == gid]
            age = float(roster_match["age"].iloc[0]) if not roster_match.empty \
                and pd.notna(roster_match["age"].iloc[0]) else 26.0
            mult = compute_age_multiplier(age, "T")
            snaps = pfr_to_snaps.get(pfr) or name_snaps.get(name) or (ol_median * 0.5)
            ol_av += float(snaps) * mult

        off_rush_epa += 0.25 * (ol_av / max(ol_median * 5, 1))

        result[team] = {
            "off_pass_epa": off_pass_epa,
            "off_rush_epa": off_rush_epa,
            "qb_tier":      qb_tier,
            "ol_av":        ol_av,
        }

    return result


_DEF_DL_POS = {"LDE", "RDE", "LDT", "RDT", "NT"}
_DEF_LB_POS = {"WLB", "MLB", "SLB", "LILB", "RILB"}
_DEF_CB_POS = {"LCB", "RCB", "NB"}
_DEF_S_POS  = {"SS", "FS"}

# def_pass_epa weights: DL=45%, LB=20%, CB/S=35%
_DEF_PASS_WEIGHTS = {"dl": 0.45, "lb": 0.20, "cb_s": 0.35}
# def_rush_epa weights: DL=60%, LB=40%
_DEF_RUSH_WEIGHTS = {"dl": 0.60, "lb": 0.40}


def _preseason_defense(
    depth_charts: pd.DataFrame,
    def_advstats: pd.DataFrame,
    roster: pd.DataFrame,
    snap_counts: pd.DataFrame,
    season: int,
) -> dict:
    """Build per-team defensive EPA estimates from depth chart + prior-season advstats.

    Returns {team: {def_pass_epa, def_rush_epa, dl_perf}}.
    def_pass_epa is negative when a team's coverage is better than league average.
    """
    adv_by_pfr:  dict = {}
    adv_by_name: dict = {}
    if not def_advstats.empty:
        adv = def_advstats.copy()
        if "game_type" in adv.columns:
            adv = adv[adv["game_type"] == "REG"]
        num_cols = ["def_sacks", "def_pressures", "def_times_hitqb",
                    "def_tackles_combined", "def_targets",
                    "def_yards_allowed_per_tgt", "def_passer_rating_allowed"]
        for c in num_cols:
            if c in adv.columns:
                adv[c] = pd.to_numeric(adv[c], errors="coerce").fillna(0.0)
        agg = adv.groupby("pfr_player_id")[num_cols].sum().reset_index()
        if "def_targets" in agg.columns:
            agg["def_yards_per_tgt_avg"] = (
                agg["def_yards_allowed_per_tgt"] / agg["def_targets"].clip(lower=1)
            )
            agg["def_passer_rtg_avg"] = (
                agg["def_passer_rating_allowed"] / agg["def_targets"].clip(lower=1)
            )

        for _, row in agg.iterrows():
            adv_by_pfr[str(row["pfr_player_id"])] = row.to_dict()
        name_agg = adv.groupby("pfr_player_name")[num_cols].sum().reset_index()
        for _, row in name_agg.iterrows():
            adv_by_name[str(row["pfr_player_name"]).lower()] = row.to_dict()

    snap_by_pfr: dict = {}
    if not snap_counts.empty:
        sc = snap_counts.copy()
        if "game_type" in sc.columns:
            sc = sc[sc["game_type"] == "REG"]
        sc["defense_snaps"] = pd.to_numeric(sc["defense_snaps"], errors="coerce").fillna(0)
        snap_by_pfr = sc.groupby("pfr_player_id")["defense_snaps"].sum().to_dict()

    gsis_to_pfr = {}
    if not roster.empty and "gsis_id" in roster.columns and "pfr_id" in roster.columns:
        gsis_to_pfr = {str(r["gsis_id"]): str(r["pfr_id"])
                       for _, r in roster.iterrows()
                       if pd.notna(r.get("pfr_id"))}

    all_dl_scores = [
        (v.get("def_sacks", 0) * DL_SACK_WEIGHT
         + v.get("def_pressures", 0) * DL_PRESSURE_WEIGHT
         + v.get("def_times_hitqb", 0) * DL_HIT_WEIGHT)
        / max(snap_by_pfr.get(k, 1), 1)
        for k, v in adv_by_pfr.items()
        if snap_by_pfr.get(k, 0) > 100
    ]
    lg_dl_score_per_snap = float(np.mean(all_dl_scores)) if all_dl_scores else 0.01

    all_cb_ytgt = [
        v.get("def_yards_per_tgt_avg", v.get("def_yards_allowed_per_tgt", 9.0))
        for v in adv_by_pfr.values()
        if v.get("def_targets", 0) >= 20
    ]
    lg_cb_ytgt = float(np.mean(all_cb_ytgt)) if all_cb_ytgt else 9.0

    ROOKIE_DISC = 0.75

    def _get_adv(gsis_id: str, name: str) -> dict | None:
        pfr = gsis_to_pfr.get(str(gsis_id), "")
        row = adv_by_pfr.get(pfr)
        if row is None:
            row = adv_by_name.get(str(name).lower())
        return row

    def _dl_score(gsis_id: str, name: str) -> float:
        pfr  = gsis_to_pfr.get(str(gsis_id), "")
        snps = snap_by_pfr.get(pfr, 0)
        adv  = _get_adv(gsis_id, name)
        if adv is None or snps < 50:
            return lg_dl_score_per_snap * ROOKIE_DISC * 500
        raw = (adv.get("def_sacks", 0) * DL_SACK_WEIGHT
               + adv.get("def_pressures", 0) * DL_PRESSURE_WEIGHT
               + adv.get("def_times_hitqb", 0) * DL_HIT_WEIGHT)
        return float(raw)

    def _cb_coverage_score(gsis_id: str, name: str) -> float:
        """Inverted coverage quality: negative = better than average (less EPA allowed)."""
        adv = _get_adv(gsis_id, name)
        if adv is None or adv.get("def_targets", 0) < 10:
            return 0.0
        ytgt = adv.get("def_yards_per_tgt_avg", adv.get("def_yards_allowed_per_tgt", lg_cb_ytgt))
        return -(ytgt - lg_cb_ytgt) / max(lg_cb_ytgt, 1.0)

    result = {}
    for team, grp in depth_charts.groupby("team"):
        dl_pass_score = 0.0
        dl_rush_score = 0.0
        lb_pass_score = 0.0
        lb_rush_score = 0.0
        cb_s_score    = 0.0
        dl_perf_total = 0.0

        dl_grp = grp[grp["pos_abb"].isin(_DEF_DL_POS) & (grp["pos_rank"] <= 2)]
        for _, p in dl_grp.iterrows():
            score = _dl_score(p["gsis_id"], p["player_name"])
            pfr   = gsis_to_pfr.get(str(p["gsis_id"]), "")
            snps  = snap_by_pfr.get(pfr, 500)
            per_snap = score / max(snps, 1)
            dl_pass_score += per_snap
            dl_rush_score += per_snap
            dl_perf_total += score

        lb_grp = grp[grp["pos_abb"].isin(_DEF_LB_POS) & (grp["pos_rank"] <= 2)]
        for _, p in lb_grp.iterrows():
            adv = _get_adv(p["gsis_id"], p["player_name"])
            pfr = gsis_to_pfr.get(str(p["gsis_id"]), "")
            snps = snap_by_pfr.get(pfr, 500)
            if adv:
                rush_contrib = (adv.get("def_tackles_combined", 0)
                                + adv.get("def_sacks", 0) * 2) / max(snps, 1)
                pass_contrib = (adv.get("def_pressures", 0)
                                + adv.get("def_sacks", 0) * 3) / max(snps, 1)
            else:
                rush_contrib = pass_contrib = lg_dl_score_per_snap * ROOKIE_DISC
            lb_pass_score += pass_contrib
            lb_rush_score += rush_contrib

        cb_grp = grp[grp["pos_abb"].isin(_DEF_CB_POS | _DEF_S_POS) & (grp["pos_rank"] == 1)]
        for _, p in cb_grp.iterrows():
            cb_s_score += _cb_coverage_score(p["gsis_id"], p["player_name"])
        if len(cb_grp) > 0:
            cb_s_score /= len(cb_grp)

        # Negate: higher quality scores → more negative EPA allowed (better defense)
        def_pass_epa = -(
            _DEF_PASS_WEIGHTS["dl"]   * dl_pass_score
            + _DEF_PASS_WEIGHTS["lb"]   * lb_pass_score
            + _DEF_PASS_WEIGHTS["cb_s"] * cb_s_score
        )
        def_rush_epa = -(
            _DEF_RUSH_WEIGHTS["dl"] * dl_rush_score
            + _DEF_RUSH_WEIGHTS["lb"] * lb_rush_score
        )

        result[team] = {
            "def_pass_epa": def_pass_epa,
            "def_rush_epa": def_rush_epa,
            "dl_perf":      dl_perf_total,
        }

    return result


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
    # home_field_advantage: 1.0 for regular home games, 0.0 for neutral sites
    _location = df.get("location", pd.Series("Home", index=df.index)).fillna("Home").astype(str).str.strip()
    df["home_field_advantage"] = (_location.str.lower() != "neutral").astype(float)
    # is_dome_flag dropped — dome captured by passing_difficulty_index dome imputation (wind=0, temp=72)
    surf = df.get("surface", "").astype(str).str.lower()
    df["surface_type"] = surf.str.contains("turf|artificial").astype(float)

    return df


def _load_rolling_epa(rd: Path) -> pd.DataFrame:
    """Load per-play EPA and rush YPC with 8 rolling prior-game columns.

    Offensive columns: off_pass_epa_roll, off_rush_epa_roll, off_early_down_roll,
                       off_rush_ypc_roll
    Defensive columns (via schedule pairing — opponent's offense = this team's D allowed):
                       def_pass_epa_roll, def_rush_epa_roll, def_early_down_roll,
                       def_rush_ypc_roll

    All rolling values use expanding mean shifted by 1 (no data leakage).
    Week-1 NaN is filled with the prior season's team average.
    Early-down formula: pass_epa*0.6 + rush_epa*0.2 + cpoe*0.05
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
        if not sched_raw.empty:
            if "game_type" in sched_raw.columns:
                sched_raw = sched_raw[sched_raw["game_type"] == "REG"].copy()
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
    for c in ["opp_pass_epa_play", "opp_rush_epa_play", "opp_early_down_epa", "opp_rush_ypc"]:
        df_paired[c] = df_paired[c].fillna(0.0)

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


def _load_trench_rolling_stats(rd: Path) -> pd.DataFrame:
    """Rolling OL sacks-suffered and DL pass-rush composite per (season, week, team).

    Returns two rolling columns:
      sacks_suffered_roll  — rolling mean sacks allowed per game (OL_pass component)
      dl_pass_roll         — rolling mean of (def_sacks×6 + def_qb_hits×1 + def_tfl×1)

    OL_run (rush_ypc) and DL_run (opponents' rush_ypc) come from _load_rolling_epa().
    Uses expanding mean shifted by 1 (no leakage). Week-1 NaN filled with prior-season avg.
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

    # Two separate binary columns — fixes the both-injured=0 bug in the old signed diff.
    # The join site selects the correct column based on team's role (home vs away).
    flags = (
        qb_out.groupby(["season", "week", "team"]).size()
        .reset_index(name="_n")
        [["season", "week", "team"]]
        .copy()
    )
    flags["home_qb_injury_flag"] = 1.0
    flags["away_qb_injury_flag"] = 1.0
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

    elo = _load_elo(rd)
    box = _load_box_stats_from_weekly(rd)
    pressure = _load_pressure_stats(rd)
    snap_counts = _load_multi_season("snap_counts/snap_counts_*.csv", rd)
    starter_qb_flags = compute_starter_qb_flags(snap_counts)
    nflverse_rosters = _load_multi_season("rosters/roster_*.csv", rd)
    stats_team_weekly = _load_multi_season("stats_team/stats_team_week_*.csv", rd)
    roster_cache = compute_roster_features(snap_counts, nflverse_rosters, team_stats=stats_team_weekly)
    roster_perf_cache = compute_roster_performance(stats_team_weekly)

    # --- Rolling EPA + trench data loaders ---
    epa = _load_rolling_epa(rd)
    trench_stats = _load_trench_rolling_stats(rd)

    # --- Rolling EPA (per-game join — 8 rolling columns for each side) ---
    _epa_cols = ["h_off_pass", "h_off_rush", "h_off_early", "h_off_ypc",
                 "h_def_pass", "h_def_rush", "h_def_early", "h_def_ypc",
                 "a_off_pass", "a_off_rush", "a_off_early", "a_off_ypc",
                 "a_def_pass", "a_def_rush", "a_def_early", "a_def_ypc"]
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
        for c in _epa_cols:
            sched[c] = sched.get(c, pd.Series(0.0, index=sched.index)).fillna(0.0)
    else:
        for c in _epa_cols:
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
    sched["elo_diff"]       = sched["tm_elo_pre"] - sched["opp_elo_pre"]
    sched["elo_confidence"] = np.abs(sched["elo_diff"] / ELO_TO_SPREAD)
    # Keep raw elo as aux metadata for projection engine
    sched["home_elo_pre"] = sched["tm_elo_pre"]
    sched["away_elo_pre"] = sched["opp_elo_pre"]

    total = sched.get("total_line", pd.Series(44.0, index=sched.index)).fillna(44.0)
    sched["market_implied_team_total"] = total / 2

    # --- Pressure Stats (signed diffs; aux cols retained for projection engine) ---
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
        # Aux cols kept for projection engine
        sched["home_qb_pressure_roll"]   = sched["_home_qb_press"]
        sched["away_qb_pressure_roll"]   = sched["_away_qb_press"]
        sched["home_def_pressures_roll"] = sched["_home_def_press"]
        sched["away_def_pressures_roll"] = sched["_away_def_press"]
        sched.drop(columns=["_home_qb_press", "_away_qb_press",
                            "_home_def_press", "_away_def_press"], inplace=True)
    else:
        for col in ["qb_pressure_advantage", "def_pressure_diff",
                    "home_qb_pressure_roll", "away_qb_pressure_roll",
                    "home_def_pressures_roll", "away_def_pressures_roll"]:
            sched[col] = 0.0

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
    # Legacy aux cols for any downstream that still reads them
    sched["home_qb_out"] = sched["home_qb_injury_flag"]
    sched["away_qb_out"] = sched["away_qb_injury_flag"]

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

    # Signed differential: positive = home team outscores opponents by more
    sched["point_diff_advantage"] = sched["tm_point_diff"] - sched["opp_point_diff"]
    # Aux metadata for projection engine
    sched["home_margin_roll"] = sched["tm_point_diff"]
    sched["away_margin_roll"] = sched["opp_point_diff"]

    # --- Rest advantage (days) — already in sched from _load_schedule() ---
    sched["rest_advantage"] = sched.get(
        "rest_advantage", pd.Series(0, index=sched.index)
    ).fillna(0.0)

    # --- Net travel disadvantage (away team's travel; 0 for neutral sites) ---
    try:
        from services.prediction_service import _get_travel_distance
        def _net_travel(row):
            if str(row.get("location", "Home")).strip().lower() == "neutral":
                return 0.0
            return _get_travel_distance(str(row["away_team"]), str(row["home_team"])) / 1000.0
        sched["net_travel_disadvantage"] = sched.apply(_net_travel, axis=1)
    except Exception:
        sched["net_travel_disadvantage"] = 0.0

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

    # --- Trench Dominance (4-component performance-based, z-scored per season+week) ---
    # OL_pass = -sacks_suffered_roll     (fewer sacks = better pass protection)
    # OL_run  = +off_rush_ypc_roll       (more yards/carry = better run blocking)
    # DL_pass = +dl_pass_roll            (sacks×6 + qb_hits + tfl = more disruption)
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

    # Four raw component values (h_ = home, a_ = away); negate where lower = better
    sched["h_OL_pass"] = -sched["h_sacks_suf"]
    sched["a_OL_pass"] = -sched["a_sacks_suf"]
    sched["h_OL_run"]  = sched.get("h_off_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    sched["a_OL_run"]  = sched.get("a_off_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    sched["h_DL_pass"] = sched["h_dl_pass"]
    sched["a_DL_pass"] = sched["a_dl_pass"]
    sched["h_DL_run"]  = -sched.get("h_def_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)
    sched["a_DL_run"]  = -sched.get("a_def_ypc", pd.Series(4.0, index=sched.index)).fillna(4.0)

    # Z-score each component within (season, week) pooling home+away values
    _trench_components = ["OL_pass", "OL_run", "DL_pass", "DL_run"]
    for _comp in _trench_components:
        _stacked = pd.concat(
            [sched[["season", "week", f"h_{_comp}"]].rename(columns={f"h_{_comp}": "_v"}),
             sched[["season", "week", f"a_{_comp}"]].rename(columns={f"a_{_comp}": "_v"})],
            ignore_index=True,
        )
        _sw = _stacked.groupby(["season", "week"])["_v"].agg(["mean", "std"]).reset_index()
        _sw.columns = ["season", "week", f"_mu_{_comp}", f"_sig_{_comp}"]
        sched = sched.merge(_sw, on=["season", "week"], how="left")
        _sig_col = f"_sig_{_comp}"
        sched[_sig_col] = sched[_sig_col].fillna(1.0).clip(lower=1e-6)
        for _side in ["h", "a"]:
            _raw = f"{_side}_{_comp}"
            sched[f"{_side}_{_comp}_z"] = (sched[_raw] - sched[f"_mu_{_comp}"]) / sched[_sig_col]
        sched.drop(columns=[f"_mu_{_comp}", f"_sig_{_comp}"], inplace=True)

    sched["home_trench_score"] = sum(sched[f"h_{c}_z"] for c in _trench_components)
    sched["away_trench_score"] = sum(sched[f"a_{c}_z"] for c in _trench_components)
    sched["trench_dominance_metric"] = sched["home_trench_score"] - sched["away_trench_score"]

    # Drop interim trench columns (leave trench_score as aux metadata for projection engine)
    _drop_trench = (
        [f"{s}_{c}" for s in ["h", "a"] for c in ["OL_pass", "OL_run", "DL_pass", "DL_run",
                                                    "OL_pass_z", "OL_run_z", "DL_pass_z", "DL_run_z"]]
        + ["h_sacks_suf", "a_sacks_suf", "h_dl_pass", "a_dl_pass"]
    )
    sched.drop(columns=[c for c in _drop_trench if c in sched.columns], inplace=True)

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
            logger.warning("Feature %s not computed — filling with 0.0", col)
            sched[col] = 0.0
        sched[col] = pd.to_numeric(sched[col], errors="coerce").fillna(0.0)

    sched = sched.dropna(subset=["home_win"]).reset_index(drop=True)

    logger.info("Master Feature Table assembled: %d rows, %d features.", len(sched), len(FEATURE_COLUMNS))

    # Metadata: always present
    meta = ["season", "week", "home_team", "away_team"]
    # Aux cols for projection engine (not model inputs, exposed for downstream consumers)
    aux = [c for c in [
        "spread_line",          # Vegas line — not a model input, kept for explanation/edge display
        "home_elo_pre", "away_elo_pre",
        "home_trench_score", "away_trench_score",
        "home_margin_roll", "away_margin_roll",
        "home_qb_pressure_roll", "away_qb_pressure_roll",
        "home_def_pressures_roll", "away_def_pressures_roll",
        "h_off_pass", "h_off_rush", "h_off_early",
        "h_def_pass", "h_def_rush", "h_def_early",
        "a_off_pass", "a_off_rush", "a_off_early",
        "a_def_pass", "a_def_rush", "a_def_early",
        "home_qb_out", "away_qb_out",
    ] if c in sched.columns]

    out_cols = meta + [c for c in FEATURE_COLUMNS if c not in meta] + aux + ["home_win"]
    return sched[[c for c in out_cols if c in sched.columns]]
