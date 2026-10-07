"""Pure helpers for the weekly personal standings push.

No Firestore access here: callers pass in frames. ``standings_as_of`` mirrors
``scripts/daily_nfl_sync.py::compute_standings`` (services must not import
scripts); tests/test_standings_push_service.py pins the equivalence.
"""
from __future__ import annotations

import pandas as pd

from services import analysis_service as analysis
from services.constants import UNDRAFTED_SENTINEL
from services.utils import abbreviate_player_name

_STANDINGS_COLUMNS = ["season", "team", "wins", "losses", "ties",
                      "scored", "allowed", "net", "pct"]


def ordinal(n: int) -> str:
    n = int(n)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _reg(games: pd.DataFrame, season: int) -> pd.DataFrame:
    reg = games[games["season"] == season]
    if "game_type" in reg.columns:
        reg = reg[reg["game_type"] == "REG"]
    return reg


def week_is_complete(games, season, week) -> bool:
    wk = _reg(games, season)
    wk = wk[wk["week"] == week]
    if wk.empty:
        return False
    res = wk["result"]
    return bool(res.notna().all() and (res != UNDRAFTED_SENTINEL).all())


def latest_complete_week(games, season):
    reg = _reg(games, season)
    for week in sorted(reg["week"].dropna().astype(int).unique(), reverse=True):
        if week_is_complete(games, season, week):
            return int(week)
    return None


def standings_as_of(games: pd.DataFrame, season: int, through_week: int) -> pd.DataFrame:
    """Per-team standings from completed REG games with week <= through_week.

    Same columns, formulas and ordering as daily_nfl_sync.compute_standings.
    """
    reg = games[
        (games["season"] == season)
        & (games["game_type"] == "REG")
        & (games["week"] <= through_week)
        & games["result"].notna()
        & games["home_score"].notna()
        & games["away_score"].notna()
    ]
    teams = pd.concat([reg["home_team"], reg["away_team"]]).drop_duplicates()
    records = []
    for team in teams:
        home = reg[reg["home_team"] == team]
        away = reg[reg["away_team"] == team]
        wins = int((home["result"] > 0).sum() + (away["result"] < 0).sum())
        losses = int((home["result"] < 0).sum() + (away["result"] > 0).sum())
        ties = int((home["result"] == 0).sum() + (away["result"] == 0).sum())
        scored = float(home["home_score"].sum() + away["away_score"].sum())
        allowed = float(home["away_score"].sum() + away["home_score"].sum())
        played = wins + losses + ties
        pct = round((wins + 0.5 * ties) / played, 6) if played else 0.0
        records.append({"season": int(season), "team": team, "wins": wins,
                        "losses": losses, "ties": ties, "scored": scored,
                        "allowed": allowed, "net": scored - allowed, "pct": pct})
    if not records:
        return pd.DataFrame(columns=_STANDINGS_COLUMNS)
    return (pd.DataFrame(records)[_STANDINGS_COLUMNS]
            .sort_values(["season", "team"]).reset_index(drop=True))


def pool_ranking(standings, draft_results, players, season, games=None) -> list[dict]:
    """Players in pool rank order (calculate_wins_pool_standings row order)."""
    df = analysis.calculate_wins_pool_standings(standings, draft_results, players, season, games)
    if df is None or df.empty:
        return []
    win_cols = [c for c in ("wins1", "wins2", "wins3") if c in df.columns]
    out = []
    for i, (_, row) in enumerate(df.iterrows(), start=1):
        out.append({"playerId": int(row["playerId"]),
                    "fullName": str(row["fullName"]),
                    "rank": i,
                    "wins": int(sum(row[c] for c in win_cols))})
    return out


def _leader_sentence(rank: int, leader_name: str) -> str:
    if rank == 1:
        return "You lead the pool."
    name = abbreviate_player_name(leader_name)
    return f"Leader: {name}" if name.endswith(".") else f"Leader: {name}."


def _wins(n: int) -> str:
    return f"{n} win" if n == 1 else f"{n} wins"


def build_messages(week: int, current: list[dict],
                   previous: list[dict] | None) -> dict[int, tuple[str, str]]:
    if not current:
        return {}
    leader = min(current, key=lambda r: r["rank"])
    prev_by_id = {r["playerId"]: r for r in (previous or [])}
    title = f"Week {week} standings"
    out = {}
    for r in current:
        rank, wins = r["rank"], r["wins"]
        tail = _leader_sentence(rank, leader["fullName"])
        prev = prev_by_id.get(r["playerId"])
        if prev is None:
            body = f"You are {ordinal(rank)} with {_wins(wins)}. {tail}"
        else:
            delta = prev["rank"] - rank
            verb = (f"moved up to {ordinal(rank)}" if delta > 0
                    else f"dropped to {ordinal(rank)}" if delta < 0
                    else f"held {ordinal(rank)}")
            body = f"You {verb} ({_wins(wins)}, +{wins - prev['wins']}). {tail}"
        out[int(r["playerId"])] = (title, body)
    return out
