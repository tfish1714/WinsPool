"""services/espn_injury_service.py -- ESPN per-game injury signal for the
narrow window between the last scheduled predict run and kickoff.

nflverse's injuries_{season}.csv updates daily, not continuously -- a
starter ruled out 90 minutes before kickoff might not be reflected until
the *next* daily sync. ESPN's per-game summary endpoint carries a fresher,
same-day signal. This module fetches it and maps it onto the exact same
availability-weight scale roster_value_service.py's weekly grade already
uses (see docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md),
as an override that wins for the specific (week, player) it covers.

Unofficial, undocumented ESPN API -- no SLA, must degrade gracefully. Every
network call is isolated so one game's or one player's failure never blocks
the rest of a slate.
"""
import logging
import pathlib
from typing import Dict, List, Tuple

import pandas as pd
import requests

from services.live_score_service import fetch_espn_scores
from services.utils import normalize_team_abbr

logger = logging.getLogger(__name__)

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"

# Same scale as roster_value_service.py's _AVAILABILITY_WEIGHTS -- kept as a
# separate copy deliberately (not imported) so this module has no dependency
# on roster_value_service.py, and vice versa; both are wired together only
# by the caller (cache_builder.py's --games mode).
_AVAILABILITY_WEIGHTS: Dict[str, float] = {"Out": 0.0, "Doubtful": 0.15, "Questionable": 0.5}


def _status_to_weight(status: str) -> float:
    return _AVAILABILITY_WEIGHTS.get(status, 1.0)


def _extract_status(entry: dict) -> str:
    """ESPN's injuries[].status has been observed as a plain string during
    manual verification; handle a nested {status: {description}} shape
    defensively too, since this is an undocumented endpoint with no
    guaranteed schema."""
    status = entry.get("status")
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        return status.get("description") or status.get("name") or ""
    return ""


def _load_espn_id_crosswalk(rawdata_dir: pathlib.Path, season: int, week: int) -> Dict[str, str]:
    """{espn_id: gsis_id} for the given (season, week) -- weekly_rosters
    carries both IDs per player per week, giving an exact join instead of
    fuzzy name matching."""
    path = rawdata_dir / "weekly_rosters" / f"roster_weekly_{season}.csv"
    try:
        df = pd.read_csv(path, usecols=["season", "week", "gsis_id", "espn_id"], low_memory=False)
    except Exception as e:
        logger.warning("espn_injury_service: cannot read %s -- %s", path, e)
        return {}

    df = df[df["week"] == week].dropna(subset=["gsis_id", "espn_id"])
    return {str(row.espn_id): str(row.gsis_id) for row in df.itertuples()}


def _find_event_ids(target_games: List[Tuple[str, str]]) -> Dict[Tuple[str, str], str]:
    """Match (home, away) team pairs (already nflverse-normalized) to ESPN
    scoreboard event ids. A game absent from today's scoreboard (e.g. not
    yet close enough to kickoff) is silently absent from the result."""
    data = fetch_espn_scores()
    if not data:
        return {}

    wanted = set(target_games)
    matches: Dict[Tuple[str, str], str] = {}
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        home = away = None
        for c in comp.get("competitors", []):
            abbr = normalize_team_abbr(c.get("team", {}).get("abbreviation", ""))
            if c.get("homeAway") == "home":
                home = abbr
            else:
                away = abbr
        if home and away and (home, away) in wanted:
            matches[(home, away)] = event.get("id")
    return matches


def _fetch_game_injuries(espn_event_id: str) -> List[dict]:
    """Raw parse of one game's injuries[] -- flat list of {espn_id, status}.
    Any failure (network, HTTP error, malformed shape) returns [] rather
    than raising -- this is a per-game, best-effort signal."""
    try:
        resp = requests.get(SUMMARY_URL, params={"event": espn_event_id}, timeout=10)
        if not resp.ok:
            return []
        data = resp.json()
    except Exception as e:
        logger.warning("espn_injury_service: summary fetch failed for event %s -- %s", espn_event_id, e)
        return []

    rows: List[dict] = []
    for team_block in data.get("injuries", []):
        for entry in team_block.get("injuries", []):
            espn_id = entry.get("athlete", {}).get("id")
            if not espn_id:
                continue
            rows.append({"espn_id": str(espn_id), "status": _extract_status(entry)})
    return rows


def get_espn_injury_overrides(
    target_games: List[Tuple[str, str]],
    season: int,
    week: int,
    rawdata_dir: pathlib.Path,
) -> Dict[Tuple[int, str], float]:
    """Main entry point. target_games: [(home_team, away_team), ...],
    already nflverse-normalized. Returns {(week, gsis_id): availability_weight}
    ready to pass straight through to
    roster_value_service.compute_roster_value(..., espn_overrides=...).

    Degrades to {} at any stage (no scoreboard match, a game's summary fetch
    failing, a missing espn_id->gsis_id crosswalk row) -- this is a cosmetic
    freshness improvement on top of the already-graded nflverse weekly
    weight (Task 1), never the sole source of truth.
    """
    event_ids = _find_event_ids(target_games)
    if not event_ids:
        return {}

    crosswalk = _load_espn_id_crosswalk(rawdata_dir, season, week)
    if not crosswalk:
        return {}

    overrides: Dict[Tuple[int, str], float] = {}
    for _game, event_id in event_ids.items():
        for row in _fetch_game_injuries(event_id):
            gsis_id = crosswalk.get(row["espn_id"])
            if not gsis_id:
                continue
            overrides[(week, gsis_id)] = _status_to_weight(row["status"])
    return overrides
