"""Payload builder for the team page (/team/{abbr}).

Pure function over already-loaded data; no I/O. See build_team_page().

`data` keys:
  standings, games, players, draft_results   frames as returned by load_data()
  predictions   {game_key: pred_dict} from cache_service.get_game_predictions();
                `pred_prob` is the HOME win probability
  projections   {team: {"projected_wins": float}} from
                data_service.get_season_projection_legacy_shape()

Bye weeks are emitted as rows (status "bye", no opponent) for every REG week in
which the league plays but the team does not.
"""
import math

import pandas as pd

from services.constants import UNDRAFTED_SENTINEL
from services.utils import get_team_logo_url, normalize_team_abbr

TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}


def team_list() -> list:
    """All 32 teams as [{"abbr", "name"}], sorted by abbreviation."""
    return [{"abbr": a, "name": TEAM_NAMES[a]} for a in sorted(TEAM_NAMES)]


def _num(value):
    """A finite float, or None for None/NaN/non-numeric."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _int(value) -> int:
    f = _num(value)
    return 0 if f is None else int(f)


def _empty(df) -> bool:
    return df is None or df.empty


def _standing(standings, season: int, team: str) -> dict:
    out = {"wins": 0, "losses": 0, "ties": 0}
    if _empty(standings) or "season" not in standings.columns or "team" not in standings.columns:
        return out
    row = standings[(standings["season"] == season) & (standings["team"] == team)]
    if row.empty:
        return out
    r = row.iloc[0]
    return {k: _int(r.get(k)) for k in out}


def _player_name(players, player_id) -> str:
    if _empty(players) or "playerId" not in players.columns:
        return f"Player {player_id}"
    row = players[players["playerId"] == player_id]
    if row.empty:
        return f"Player {player_id}"
    name = row.iloc[0].get("fullName")
    if name is None or pd.isna(name) or not str(name).strip():
        return f"Player {player_id}"
    return str(name)


def _pool_winner(data: dict, season: int):
    """Rank 1 of the season's pool standings (the app's own tiebreaker cascade)."""
    import services.analysis_service as analysis
    try:
        table = analysis.calculate_wins_pool_standings(
            data["standings"], data["draft_results"], data["players"], season)
    except Exception:
        return None
    if table is None or table.empty:
        return None
    top = table.iloc[0]
    return {"playerId": _int(top["playerId"]), "name": str(top["fullName"]),
            "wins": _int(top["TotalWins"])}


def _cached_winner(cache: dict, data: dict, season: int):
    if season not in cache:
        cache[season] = _pool_winner(data, season)
    return cache[season]


def _history(team: str, current_season: int, data: dict) -> list:
    dr = data.get("draft_results")
    if _empty(dr) or "season" not in dr.columns or "team" not in dr.columns:
        return []
    mine = dr[dr["team"].apply(normalize_team_abbr) == team]
    history = []
    winners = {}  # season -> pool winner, so each season is computed at most once
    for season in sorted({int(s) for s in mine["season"].dropna()}, reverse=True):
        pick_row = mine[mine["season"] == season].sort_values("draftPick").iloc[0]
        pid = _int(pick_row["playerId"])
        rec = _standing(data.get("standings"), season, team)
        history.append({
            "season": season, **rec,
            "drafter": {"playerId": pid, "name": _player_name(data.get("players"), pid)},
            "pick": _int(pick_row.get("draftPick")) or None,
            # The in-progress season has no winner yet, only a leader.
            "pool_winner": None if season == current_season else _cached_winner(winners, data, season),
        })
    return history


def _current(team: str, season: int, data: dict, include_projections: bool) -> dict:
    record = {"wins": 0, "losses": 0, "ties": 0}
    schedule = []
    games = data.get("games")
    preds = data.get("predictions") or {}
    if not _empty(games) and {"season", "week", "home_team", "away_team", "result"} <= set(games.columns):
        g = games[games["season"] == season]
        if "game_type" in g.columns:
            g = g[g["game_type"] == "REG"]
        g = g.copy()
        g["_home"] = g["home_team"].apply(normalize_team_abbr)
        g["_away"] = g["away_team"].apply(normalize_team_abbr)
        league_weeks = sorted({int(w) for w in pd.to_numeric(g["week"], errors="coerce").dropna()})
        mine = {}
        for row in g[(g["_home"] == team) | (g["_away"] == team)].to_dict("records"):
            wk = _num(row["week"])
            if wk is not None:
                mine[int(wk)] = row
        for wk in league_weeks:
            row = mine.get(wk)
            if row is None:
                if mine:
                    schedule.append({"week": wk, "opponent": None, "home": None, "status": "bye",
                                     "result": None, "score": None, "win_prob": None, "projected": None})
                continue
            home = row["_home"] == team
            res = _num(row.get("result"))
            played = res is not None and row.get("result") != UNDRAFTED_SENTINEL
            entry = {"week": wk, "opponent": row["_away"] if home else row["_home"], "home": home,
                     "status": "played" if played else "unplayed",
                     "result": None, "score": None, "win_prob": None, "projected": None}
            if played:
                margin = res if home else -res
                entry["result"] = "W" if margin > 0 else "L" if margin < 0 else "T"
                record[{"W": "wins", "L": "losses", "T": "ties"}[entry["result"]]] += 1
                hs, aws = _num(row.get("home_score")), _num(row.get("away_score"))
                if hs is not None and aws is not None:
                    mine_s, theirs = (hs, aws) if home else (aws, hs)
                    entry["score"] = f"{int(mine_s)}-{int(theirs)}"
            elif include_projections:
                prob = _num((preds.get(f"W{wk:02d}_{row['_home']}_{row['_away']}") or {}).get("pred_prob"))
                if prob is not None:
                    win_prob = prob if home else 1.0 - prob
                    entry["win_prob"] = round(win_prob, 4)
                    # An exact toss-up has no projected side (win_prob stays 0.5).
                    entry["projected"] = None if win_prob == 0.5 else "W" if win_prob > 0.5 else "L"
            schedule.append(entry)

    projected_wins = None
    projected_record = None
    if include_projections:
        projected_wins = _num(((data.get("projections") or {}).get(team) or {}).get("projected_wins"))
        unplayed = [r for r in schedule if r["status"] == "unplayed"]
        # Only when every unplayed game has a projected side; otherwise the
        # wins+losses total would fall short of the season length.
        if all(r["projected"] is not None for r in unplayed):
            projected_record = {
                "wins": record["wins"] + sum(r["projected"] == "W" for r in unplayed),
                "losses": record["losses"] + sum(r["projected"] == "L" for r in unplayed),
            }
    return {"record": record, "projected_wins": projected_wins,
            "schedule": schedule, "projected_record": projected_record}


def build_team_page(team: str, season_hint, data: dict, include_projections: bool):
    """Team page payload, or None for an unknown team.

    season_hint: the active season (falls back to the latest season in games).
    include_projections: False nulls projected_wins, win_prob, projected and
    projected_record; history, results and the dropdown still render.
    """
    abbr = normalize_team_abbr(team) if isinstance(team, str) else None
    if abbr not in TEAM_NAMES:
        return None
    season = season_hint
    if season is None:
        games = data.get("games")
        season = int(games["season"].max()) if not _empty(games) and "season" in games.columns else 0
    season = int(season)
    return {
        "team": abbr,
        "name": TEAM_NAMES[abbr],
        "logo": get_team_logo_url(abbr),
        "current_season": season,
        "teams": team_list(),
        "history": _history(abbr, season, data),
        "current": _current(abbr, season, data, include_projections),
    }
