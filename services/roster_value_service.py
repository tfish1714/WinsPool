"""services/roster_value_service.py -- Week-aware WAR-like team roster value.

Computes four per-team features for every regular-season week, blending prior-season
player stats with current-season rolling performance as the season progresses:

    off_roster_value   -- QB + skill EPA, age-adjusted, depth-discounted (z-scored)
    def_roster_value   -- Edge/DL/LB/secondary composite, age-adjusted (z-scored)
    st_value           -- kicker FG+ over expected + punter gross-avg vs league mean (z-scored)
    qb_resilience      -- starter durability + backup quality blend (0–1)

Blend rule per player per week:
    alpha = min(1.0, current_season_games / MIN_GAMES_CURRENT)
    score = alpha * current_rolling_EPA + (1 - alpha) * prior_season_EPA

Week 1  → 100 % prior-season (no in-season data yet)
Week 5+ → 100 % current-season once a player has MIN_GAMES_CURRENT games
Mid-trade → traded player's prior stats immediately appear on new team's active roster

Output:
    {(season, week, team): {off_roster_value, def_roster_value, st_value, qb_resilience}}

Usage:
    from services.roster_value_service import compute_roster_value
    cache = compute_roster_value(target_season=2025, rawdata_dir=RAWDATA_DIR)
    vals = cache.get((2025, 9, "KC"), {})
"""

import glob
import logging
import pathlib
import warnings
from typing import Dict, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

MIN_GAMES_CURRENT = 4       # games needed before current-season EPA dominates
MIN_QB_ATTEMPTS   = 150     # season qualifying threshold (prior-season QB)
MIN_SKILL_GAMES   = 6
MIN_DEF_GAMES     = 5
MIN_K_ATTEMPTS    = 10
MIN_P_PUNTS       = 15

LEAGUE_AVG_PUNT_YDS = 44.5  # fallback gross avg if PBP unavailable

# League-average FG% by distance bucket (2018-2024 NFL)
_FG_AVG = {
    "0_19": 0.99, "20_29": 0.96, "30_39": 0.88,
    "40_49": 0.77, "50_59": 0.65, "60_": 0.35,
}

# Active statuses that represent a real roster spot
_ACTIVE_STATUSES = {"ACT", "INA", "PUP"}   # INA = inactive game-day, still on roster

# Depth discount: contribution drops off for each subsequent player (best → worst)
_DEPTH_DISC: Dict[str, list] = {
    "QB":   [1.00, 0.25],
    "WR":   [1.00, 0.65, 0.38, 0.15],
    "TE":   [1.00, 0.50],
    "RB":   [1.00, 0.55, 0.20],
    "edge": [1.00, 0.80, 0.60, 0.40],
    "dl":   [1.00, 0.80, 0.60],
    "lb":   [1.00, 0.80, 0.60, 0.40],
    "cb":   [1.00, 0.85, 0.70, 0.55],
    "s":    [1.00, 0.80],
}

_OFF_WEIGHTS = {"QB": 0.40, "WR": 0.25, "TE": 0.10, "RB": 0.15}
_DEF_WEIGHTS = {"edge": 0.35, "dl": 0.20, "lb": 0.20, "cb": 0.15, "s": 0.10}

# Raw position → internal group
_POS_GROUP: Dict[str, str] = {
    "QB": "QB", "WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB",
    "DE": "edge", "OLB": "edge",
    "DT": "dl", "NT": "dl", "DL": "dl",
    "LB": "lb", "MLB": "lb", "ILB": "lb",
    "CB": "cb",
    "S": "s", "FS": "s", "SS": "s", "SAF": "s",
}

_TEAM_MAP = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _norm_team(t: str) -> str:
    t = str(t).upper().strip()
    return _TEAM_MAP.get(t, t)


def _read_safe(path: str, **kw) -> pd.DataFrame:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return pd.read_csv(path, low_memory=False, **kw)
    except Exception as e:
        logger.debug("roster_value: cannot read %s — %s", path, e)
        return pd.DataFrame()


def _age_at(birth_date_str, season: int) -> float:
    try:
        bd = pd.Timestamp(birth_date_str)
        return (pd.Timestamp(f"{season}-09-01") - bd).days / 365.25
    except Exception:
        return float("nan")


def _age_mult(age: float, position: str) -> float:
    if np.isnan(age):
        return 1.0
    if age < 24:
        return 1.05
    if age <= 27:
        return 1.00
    ypp = age - 27          # years past prime
    if age <= 30:
        return max(0.3, 1.0 - ypp * 0.02)
    if position in {"RB", "WR", "CB", "S", "FS", "SS"}:
        return max(0.3, 1.0 - ypp * 0.06)
    return max(0.3, 1.0 - ypp * 0.04)


def _depth_discount(scores: list, group: str) -> float:
    disc = _DEPTH_DISC.get(group, [1.0])
    total = 0.0
    for i, score in enumerate(sorted(scores, reverse=True)):
        mult = disc[i] if i < len(disc) else disc[-1] * 0.5
        total += score * mult
    return total


def _zscore(series: pd.Series) -> pd.Series:
    std = series.std(ddof=0)
    return (series - series.mean()) / std if std > 1e-9 else pd.Series(0.0, index=series.index)


# --------------------------------------------------------------------------- #
# Data loaders
# --------------------------------------------------------------------------- #

def _load_prior_epa(rawdata_dir: pathlib.Path, prior_season: int) -> pd.DataFrame:
    """Season-total EPA composites per player from prior season.
    Returns: player_id, position_group, prior_score
    """
    df = _read_safe(str(rawdata_dir / "stats_player" / f"stats_player_week_{prior_season}.csv"))
    if df.empty:
        return pd.DataFrame(columns=["player_id", "position_group", "prior_score"])

    reg = df[df["season_type"] == "REG"].copy()
    num_cols = ["passing_epa", "rushing_epa", "receiving_epa", "attempts",
                "def_sacks", "def_qb_hits", "def_tackles_for_loss",
                "def_tackles_solo", "def_pass_defended", "def_interceptions"]
    for c in num_cols:
        reg[c] = pd.to_numeric(reg.get(c, 0), errors="coerce").fillna(0.0)

    records = []

    # QB
    qb = reg[reg["position"] == "QB"].groupby("player_id").agg(
        passing_epa=("passing_epa", "sum"), attempts=("attempts", "sum"), games=("week", "count")
    ).reset_index()
    for _, r in qb[qb["attempts"] >= MIN_QB_ATTEMPTS].iterrows():
        records.append({"player_id": r["player_id"], "position_group": "QB",
                        "prior_score": float(r["passing_epa"])})

    # WR / TE
    for pos in ("WR", "TE"):
        sub = reg[reg["position"] == pos].groupby("player_id").agg(
            receiving_epa=("receiving_epa", "sum"), games=("week", "count")
        ).reset_index()
        for _, r in sub[sub["games"] >= MIN_SKILL_GAMES].iterrows():
            records.append({"player_id": r["player_id"], "position_group": pos,
                            "prior_score": float(r["receiving_epa"])})

    # RB
    rb = reg[reg["position"].isin(["RB", "FB"])].groupby("player_id").agg(
        rushing_epa=("rushing_epa", "sum"), receiving_epa=("receiving_epa", "sum"),
        games=("week", "count")
    ).reset_index()
    for _, r in rb[rb["games"] >= MIN_SKILL_GAMES].iterrows():
        records.append({"player_id": r["player_id"], "position_group": "RB",
                        "prior_score": float(r["rushing_epa"]) + 0.5 * float(r["receiving_epa"])})

    # Edge (DE/OLB)
    edge = reg[reg["position"].isin(["DE", "OLB"])].groupby("player_id").agg(
        sacks=("def_sacks", "sum"), hits=("def_qb_hits", "sum"),
        tfl=("def_tackles_for_loss", "sum"), games=("week", "count")
    ).reset_index()
    for _, r in edge[edge["games"] >= MIN_DEF_GAMES].iterrows():
        records.append({"player_id": r["player_id"], "position_group": "edge",
                        "prior_score": float(r["sacks"] * 6 + r["hits"] * 2 + r["tfl"] * 2)})

    # Interior DL
    dl = reg[reg["position"].isin(["DT", "NT", "DL"])].groupby("player_id").agg(
        sacks=("def_sacks", "sum"), hits=("def_qb_hits", "sum"),
        tfl=("def_tackles_for_loss", "sum"), games=("week", "count")
    ).reset_index()
    for _, r in dl[dl["games"] >= MIN_DEF_GAMES].iterrows():
        records.append({"player_id": r["player_id"], "position_group": "dl",
                        "prior_score": float(r["sacks"] * 4 + r["hits"] * 2 + r["tfl"] * 2)})

    # LB
    lb = reg[reg["position"].isin(["LB", "MLB", "ILB"])].groupby("player_id").agg(
        tfl=("def_tackles_for_loss", "sum"), sacks=("def_sacks", "sum"),
        ints=("def_interceptions", "sum"), tackles=("def_tackles_solo", "sum"),
        games=("week", "count")
    ).reset_index()
    for _, r in lb[lb["games"] >= MIN_DEF_GAMES].iterrows():
        records.append({"player_id": r["player_id"], "position_group": "lb",
                        "prior_score": float(r["tfl"]*2 + r["sacks"]*4 + r["ints"]*5 + r["tackles"]*0.5)})

    # CB / Safety
    for pos_list, grp in [
        (["CB"], "cb"),
        (["S", "FS", "SS", "SAF"], "s"),
    ]:
        sub = reg[reg["position"].isin(pos_list)].groupby("player_id").agg(
            pds=("def_pass_defended", "sum"), ints=("def_interceptions", "sum"),
            tackles=("def_tackles_solo", "sum"), games=("week", "count")
        ).reset_index()
        for _, r in sub[sub["games"] >= MIN_DEF_GAMES].iterrows():
            records.append({"player_id": r["player_id"], "position_group": grp,
                            "prior_score": float(r["pds"]*2 + r["ints"]*5 + r["tackles"]*0.5)})

    return pd.DataFrame(records) if records else pd.DataFrame(columns=["player_id", "position_group", "prior_score"])


def _load_current_rolling_epa(rawdata_dir: pathlib.Path, target_season: int) -> pd.DataFrame:
    """Per-player per-week rolling EPA in the current season (expanding mean, shifted 1 week).

    Returns DataFrame: player_id, position_group, week, rolling_score, games_played
    rolling_score at week W = average of game scores from weeks 1..W-1 (no leakage)
    """
    df = _read_safe(str(rawdata_dir / "stats_player" / f"stats_player_week_{target_season}.csv"))
    if df.empty:
        return pd.DataFrame(columns=["player_id", "position_group", "week", "rolling_score", "games_played"])

    reg = df[df["season_type"] == "REG"].copy()
    num_cols = ["passing_epa", "rushing_epa", "receiving_epa", "attempts",
                "def_sacks", "def_qb_hits", "def_tackles_for_loss",
                "def_tackles_solo", "def_pass_defended", "def_interceptions"]
    for c in num_cols:
        reg[c] = pd.to_numeric(reg.get(c, 0), errors="coerce").fillna(0.0)

    # Map position → group
    reg["position_group"] = reg["position"].map(_POS_GROUP)
    reg = reg[reg["position_group"].notna()].copy()

    # Compute per-game composite score vectorised via np.select
    grp = reg["position_group"]
    s = reg
    conditions = [
        grp == "QB",
        grp == "WR",
        grp == "TE",
        grp == "RB",
        grp == "edge",
        grp == "dl",
        grp == "lb",
        grp.isin(["cb", "s"]),
    ]
    choices = [
        s["passing_epa"],
        s["receiving_epa"],
        s["receiving_epa"],
        s["rushing_epa"] + 0.5 * s["receiving_epa"],
        s["def_sacks"]*6 + s["def_qb_hits"]*2 + s["def_tackles_for_loss"]*2,
        s["def_sacks"]*4 + s["def_qb_hits"]*2 + s["def_tackles_for_loss"]*2,
        s["def_tackles_for_loss"]*2 + s["def_sacks"]*4 + s["def_interceptions"]*5 + s["def_tackles_solo"]*0.5,
        s["def_pass_defended"]*2 + s["def_interceptions"]*5 + s["def_tackles_solo"]*0.5,
    ]
    reg["game_score"] = np.select(conditions, choices, default=0.0)

    # Rolling expanding mean shifted by 1 within each player-season
    reg_sorted = reg.sort_values(["player_id", "week"])
    reg_sorted["rolling_score"] = (
        reg_sorted.groupby("player_id")["game_score"]
        .transform(lambda x: x.expanding().mean().shift(1))
    )
    reg_sorted["games_played"] = (
        reg_sorted.groupby("player_id")["game_score"]
        .transform(lambda x: (~x.isna()).cumsum().shift(1).fillna(0))
    )
    reg_sorted["rolling_score"] = reg_sorted["rolling_score"].fillna(0.0)

    keep = reg_sorted[["player_id", "position_group", "week", "rolling_score", "games_played"]].copy()
    keep["games_played"] = keep["games_played"].astype(int)
    return keep.reset_index(drop=True)


def _load_kicker_rolling(rawdata_dir: pathlib.Path,
                          target_season: int,
                          prior_season: int) -> pd.DataFrame:
    """Per-player per-week rolling kicker FG+ (makes above expected).
    Returns: player_id, week, rolling_k_value, games_played
    Prior season aggregated as the week-0 baseline.
    """
    buckets = ["0_19", "20_29", "30_39", "40_49", "50_59", "60_"]

    def _fg_plus(row) -> float:
        total = 0.0
        for b in buckets:
            made = float(row.get(f"fg_made_{b}", 0) or 0)
            miss = float(row.get(f"fg_missed_{b}", 0) or 0)
            att = made + miss
            total += made - att * _FG_AVG.get(b, 0.80)
        return total

    # Prior season: single number per kicker
    prior_df = _read_safe(str(rawdata_dir / "stats_player" / f"stats_player_week_{prior_season}.csv"))
    prior_baseline: Dict[str, float] = {}
    if not prior_df.empty:
        pk = prior_df[(prior_df["position"] == "K") & (prior_df["season_type"] == "REG")]
        agg_cols = {f"fg_made_{b}": (f"fg_made_{b}", "sum") for b in buckets if f"fg_made_{b}" in pk.columns}
        agg_cols.update({f"fg_missed_{b}": (f"fg_missed_{b}", "sum") for b in buckets if f"fg_missed_{b}" in pk.columns})
        if agg_cols:
            pk_agg = pk.groupby("player_id").agg(**agg_cols).reset_index()
            pk_agg["total_att"] = sum(
                pk_agg.get(f"fg_made_{b}", pd.Series(0)) + pk_agg.get(f"fg_missed_{b}", pd.Series(0))
                for b in buckets
            )
            for _, r in pk_agg[pk_agg["total_att"] >= MIN_K_ATTEMPTS].iterrows():
                prior_baseline[r["player_id"]] = _fg_plus(r)

    # Current season: weekly game-level FG+, then rolling
    cur_df = _read_safe(str(rawdata_dir / "stats_player" / f"stats_player_week_{target_season}.csv"))
    if cur_df.empty:
        rows = [{"player_id": pid, "week": 1, "rolling_k_value": val, "games_played": 0}
                for pid, val in prior_baseline.items()]
        return pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=["player_id", "week", "rolling_k_value", "games_played"])

    ck = cur_df[(cur_df["position"] == "K") & (cur_df["season_type"] == "REG")].copy()
    for b in buckets:
        for pfx in ("fg_made_", "fg_missed_"):
            col = f"{pfx}{b}"
            if col not in ck.columns:
                ck[col] = 0.0
            ck[col] = pd.to_numeric(ck[col], errors="coerce").fillna(0.0)

    ck["game_k_value"] = ck.apply(_fg_plus, axis=1)
    ck_sorted = ck.sort_values(["player_id", "week"])
    ck_sorted["rolling_k_value"] = (
        ck_sorted.groupby("player_id")["game_k_value"]
        .transform(lambda x: x.expanding().mean().shift(1).fillna(
            ck_sorted.loc[x.index, "player_id"].map(prior_baseline).fillna(0.0)
        ))
    )
    ck_sorted["games_played"] = (
        ck_sorted.groupby("player_id")["game_k_value"]
        .transform(lambda x: (~x.isna()).cumsum().shift(1).fillna(0).astype(int))
    )
    return ck_sorted[["player_id", "week", "rolling_k_value", "games_played"]].reset_index(drop=True)


def _load_punter_prior(rawdata_dir: pathlib.Path, prior_season: int) -> Dict[str, float]:
    """Gross avg punt yards above league mean, per punter, from prior season PBP.
    Returns {punter_player_id: punter_value}  (static; punters rarely change mid-season)
    """
    pbp_path = rawdata_dir / "pbp" / f"play_by_play_{prior_season}.csv"
    df = _read_safe(str(pbp_path),
                    usecols=["season_type", "punter_player_id", "punt_attempt",
                             "kick_distance", "punt_blocked"])
    if df.empty:
        return {}

    punts = df[
        (df["season_type"] == "REG") &
        (df["punt_attempt"] == 1) &
        (df["punt_blocked"].fillna(0) == 0)
    ].copy()
    punts["kick_distance"] = pd.to_numeric(punts["kick_distance"], errors="coerce")
    league_avg = punts["kick_distance"].mean()
    if np.isnan(league_avg):
        league_avg = LEAGUE_AVG_PUNT_YDS

    by_punter = (
        punts.groupby("punter_player_id")["kick_distance"]
        .agg(["mean", "count"]).reset_index()
    )
    by_punter = by_punter[by_punter["count"] >= MIN_P_PUNTS]
    return dict(zip(by_punter["punter_player_id"], by_punter["mean"] - league_avg))


def _load_prior_qb_durability(rawdata_dir: pathlib.Path, prior_season: int) -> Dict[str, float]:
    """Prior-season QB durability (fraction of games started, 0–1) keyed by gsis_id.
    Used as week-1 fallback before any current-season data exists.
    """
    df = _read_safe(str(rawdata_dir / "stats_player" / f"stats_player_week_{prior_season}.csv"),
                    usecols=["player_id", "position", "season_type", "week", "attempts"])
    if df.empty:
        return {}
    df["attempts"] = pd.to_numeric(df["attempts"], errors="coerce").fillna(0)
    qb = df[(df["position"] == "QB") & (df["season_type"] == "REG")]
    result = qb.groupby("player_id").agg(games=("week", "count"), attempts=("attempts", "sum")).reset_index()
    result = result[result["attempts"] >= 50]   # exclude pure backups
    return dict(zip(result["player_id"], (result["games"] / 17.0).clip(upper=1.0)))


def _load_weekly_roster(rawdata_dir: pathlib.Path, target_season: int) -> pd.DataFrame:
    """Weekly active roster snapshots for target_season.
    Returns: season, week, team, gsis_id, position, birth_date
    Only active-status players (ACT / INA game-day inactive / PUP).
    """
    path = rawdata_dir / "weekly_rosters" / f"roster_weekly_{target_season}.csv"
    needed = ["season", "week", "team", "position", "gsis_id", "status", "birth_date"]
    df = _read_safe(str(path), usecols=[c for c in needed])
    if df.empty:
        return pd.DataFrame()

    df["team"] = df["team"].apply(_norm_team)
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df = df[df["week"].between(1, 22)].copy()

    # Keep only active roster members (excludes IR / practice squad)
    if "status" in df.columns:
        df = df[df["status"].isin(_ACTIVE_STATUSES)]

    return df[["season", "week", "team", "gsis_id", "position", "birth_date"]].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def compute_roster_value(
    target_season: int,
    rawdata_dir: pathlib.Path,
) -> Dict[Tuple[int, int, str], dict]:
    """
    Returns {(target_season, week, team): {off_roster_value, def_roster_value,
                                            st_value, qb_resilience}}
    for all regular-season weeks (1–18).

    Blend logic per player (alpha = min(1, games_played / MIN_GAMES_CURRENT)):
        score = alpha * current_rolling_EPA + (1 - alpha) * prior_season_EPA
    """
    prior_season = target_season - 1
    logger.info("roster_value: building weekly profiles for %d (prior=%d)", target_season, prior_season)

    # ---- Load data ----
    prior_epa    = _load_prior_epa(rawdata_dir, prior_season)
    prior_qb_dur = _load_prior_qb_durability(rawdata_dir, prior_season)
    current_epa  = _load_current_rolling_epa(rawdata_dir, target_season)
    kicker_roll  = _load_kicker_rolling(rawdata_dir, target_season, prior_season)
    punter_map   = _load_punter_prior(rawdata_dir, prior_season)
    weekly_ros   = _load_weekly_roster(rawdata_dir, target_season)

    if weekly_ros.empty:
        logger.warning("roster_value: no weekly roster data for %d", target_season)
        return {}

    # Pre-index for fast lookup
    prior_idx   = prior_epa.set_index("player_id")[["position_group", "prior_score"]].to_dict("index") \
                  if not prior_epa.empty else {}
    cur_idx: Dict[Tuple[str, int], dict] = {}
    if not current_epa.empty:
        for _, r in current_epa.iterrows():
            cur_idx[(r["player_id"], int(r["week"]))] = {
                "rolling_score": float(r["rolling_score"]),
                "games_played":  int(r["games_played"]),
                "position_group": r["position_group"],
            }

    kicker_idx: Dict[Tuple[str, int], dict] = {}
    if not kicker_roll.empty:
        for _, r in kicker_roll.iterrows():
            kicker_idx[(r["player_id"], int(r["week"]))] = {
                "rolling_k_value": float(r["rolling_k_value"]),
                "games_played":    int(r["games_played"]),
            }

    weeks = sorted(weekly_ros["week"].unique())
    teams = sorted(weekly_ros["team"].unique())

    # Collect raw scores for z-scoring (per week)
    raw_off: Dict[Tuple[int, str], float] = {}
    raw_def: Dict[Tuple[int, str], float] = {}
    raw_st:  Dict[Tuple[int, str], float] = {}
    raw_qbr: Dict[Tuple[int, str], float] = {}

    for week in weeks:
        week_ros = weekly_ros[weekly_ros["week"] == week]

        for team in teams:
            team_ros = week_ros[week_ros["team"] == team]
            if team_ros.empty:
                raw_off[(week, team)] = 0.0
                raw_def[(week, team)] = 0.0
                raw_st[(week, team)]  = 0.0
                raw_qbr[(week, team)] = 0.5
                continue

            # Build blended player scores for this team this week
            off_by_grp: Dict[str, list] = {g: [] for g in _OFF_WEIGHTS}
            def_by_grp: Dict[str, list] = {g: [] for g in _DEF_WEIGHTS}
            qb_entries: list = []

            for _, player in team_ros.iterrows():
                pid  = player["gsis_id"]
                pos  = str(player.get("position", "")).upper().strip()
                grp  = _POS_GROUP.get(pos)
                if grp is None:
                    continue

                age  = _age_at(player.get("birth_date"), target_season)
                amlt = _age_mult(age, pos)

                # Blend current vs prior
                cur  = cur_idx.get((pid, week))
                prr  = prior_idx.get(pid)

                if cur and cur["position_group"] == grp:
                    gp    = cur["games_played"]
                    alpha = min(1.0, gp / MIN_GAMES_CURRENT)
                    p_sc  = prr["prior_score"] if prr and prr["position_group"] == grp else 0.0
                    score = alpha * cur["rolling_score"] + (1 - alpha) * p_sc
                elif prr and prr["position_group"] == grp:
                    score = prr["prior_score"]
                else:
                    score = 0.0

                adj = score * amlt

                if grp in off_by_grp:
                    off_by_grp[grp].append(adj)
                    if grp == "QB":
                        gp = cur["games_played"] if cur else 0
                        qb_entries.append({"pid": pid, "score": adj, "gp": gp})
                elif grp in def_by_grp:
                    def_by_grp[grp].append(adj)

            raw_off[(week, team)] = sum(
                _depth_discount(off_by_grp[g], g) * w for g, w in _OFF_WEIGHTS.items()
            )
            raw_def[(week, team)] = sum(
                _depth_discount(def_by_grp[g], g) * w for g, w in _DEF_WEIGHTS.items()
            )

            # Special teams: kicker (weekly rolling) + punter (prior season static)
            team_k_ids = team_ros[team_ros["position"] == "K"]["gsis_id"].tolist()
            k_val = 0.0
            for kid in team_k_ids:
                kr = kicker_idx.get((kid, week))
                if kr:
                    k_val = kr["rolling_k_value"]
                    break

            team_p_ids = team_ros[team_ros["position"] == "P"]["gsis_id"].tolist()
            p_val = next((punter_map.get(pid, 0.0) for pid in team_p_ids), 0.0)

            raw_st[(week, team)] = k_val + p_val

            # QB resilience — blend prior-season durability into current-season games count
            if not qb_entries:
                raw_qbr[(week, team)] = 0.5
            else:
                qb_entries_s = sorted(qb_entries, key=lambda x: x["gp"], reverse=True)
                starter = qb_entries_s[0]
                cur_gp = starter["gp"]
                # Blend: at week 1 (cur_gp=0) use prior durability; after MIN_GAMES_CURRENT use current
                alpha = min(1.0, cur_gp / MIN_GAMES_CURRENT)
                prior_dur = prior_qb_dur.get(starter["pid"], 0.75)   # unknown QB → assume 75% durable
                cur_dur   = min(1.0, cur_gp / 16.0)
                durability = alpha * cur_dur + (1 - alpha) * prior_dur
                backup_q = 0.0
                if len(qb_entries_s) > 1:
                    bk_gp = qb_entries_s[1]["gp"]
                    bk_prior = prior_qb_dur.get(qb_entries_s[1]["pid"], 0.0)
                    bk_alpha = min(1.0, bk_gp / MIN_GAMES_CURRENT)
                    bk_dur = bk_alpha * min(1.0, bk_gp / 8.0) + (1 - bk_alpha) * bk_prior
                    backup_q = min(1.0, bk_dur) * 0.5
                raw_qbr[(week, team)] = 0.7 * durability + 0.3 * backup_q

    # Z-score within each week across 32 teams
    result: Dict[Tuple[int, int, str], dict] = {}
    for week in weeks:
        off_s = pd.Series({t: raw_off[(week, t)] for t in teams})
        def_s = pd.Series({t: raw_def[(week, t)] for t in teams})
        st_s  = pd.Series({t: raw_st[(week, t)]  for t in teams})

        z_off = _zscore(off_s)
        z_def = _zscore(def_s)
        z_st  = _zscore(st_s)

        for team in teams:
            result[(target_season, week, team)] = {
                "off_roster_value": float(z_off[team]),
                "def_roster_value": float(z_def[team]),
                "st_value":         float(z_st[team]),
                "qb_resilience":    float(raw_qbr[(week, team)]),
            }

    logger.info("roster_value: %d (week, team) profiles for %d", len(result), target_season)
    return result
