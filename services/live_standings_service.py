"""services/live_standings_service.py -- Lightweight leaderboard payload for the
standings page's client-side poll (GET /api/live-standings).

Pure shaping only: win totals, point differentials, and tiebreakers come from
the already-computed pool standings (nflverse-driven). ESPN-derived live
fields (is_live, live scores, clock) are display-only and never feed back
into any total here.
"""
from datetime import datetime

import pandas as pd

TEAM_SLOTS = (1, 2, 3)


def _int(value, default: int = 0) -> int:
    return default if value is None or pd.isna(value) else int(value)


def _live_games_by_team(games) -> dict:
    """Map team abbreviation -> live-game display info, for games that are
    underway and not yet final per nflverse (result still NaN)."""
    out = {}
    if games is None or games.empty or "is_live" not in games.columns:
        return out
    for _, g in games.iterrows():
        if not bool(g.get("is_live")) or pd.notna(g.get("result")):
            continue
        home, away = g.get("home_team"), g.get("away_team")
        clock = g.get("clock")
        period = g.get("period")
        info = {
            "live_score": (
                f"{away} {_int(g.get('live_away_score'))} - {home} {_int(g.get('live_home_score'))}"
            ),
            "period": None if period is None or pd.isna(period) else int(period),
            "clock": None if clock is None or pd.isna(clock) else str(clock),
        }
        out[home] = info
        out[away] = info
    return out


def _team_entry(row, slot: int, live: dict):
    abbr = row.get(f"team{slot}")
    if not isinstance(abbr, str) or not abbr:
        return None
    entry = {
        "abbr": abbr,
        "wins": _int(row.get(f"wins{slot}")),
        "pt_diff": _int(row.get(f"ptDiff{slot}")),
        "is_live": abbr in live,
    }
    if abbr in live:
        entry.update(live[abbr])
    return entry


def build_live_standings_payload(sorted_df, games, year: int) -> dict:
    """Shape ranked pool standings + live game state for the client.

    `sorted_df` is analysis.calculate_wins_pool_standings()'s output (None or
    empty yields no standings). NaN point differentials/tiebreakers become 0,
    matching what the server-rendered template shows."""
    live = _live_games_by_team(games)
    rows = []
    if sorted_df is not None and not sorted_df.empty:
        for _, r in sorted_df.iterrows():
            teams = [t for t in (_team_entry(r, i, live) for i in TEAM_SLOTS) if t]
            rows.append({
                "rank": _int(r.get("Rank")),
                "player_id": _int(r.get("playerId")),
                "full_name": r.get("fullName"),
                "total_wins": _int(r.get("TotalWins")),
                "teams": teams,
                "tiebreakers": {
                    "tb1": _int(r.get("Tiebreaker1_WorstTeamWins")),
                    "tb2": _int(r.get("Tiebreaker2_2ndWorstTeamWins")),
                    "tb3": _int(r.get("Tiebreaker3_BestTeamWins")),
                    "tb4": _int(r.get("Tiebreaker4_WorstTeamPtDiff")),
                    "tb5": _int(r.get("Tiebreaker5_2ndWorstTeamPtDiff")),
                    "tb6": _int(r.get("Tiebreaker6_BestTeamPtDiff")),
                },
            })
    return {
        "year": year,
        "last_updated": datetime.now().astimezone().isoformat(),
        "standings": rows,
    }
