"""services/mock_draft_service.py — Solo mock draft: pick sequencing and bot picks.

Fully stateless — every function here is a pure read against Firestore/pkl
(via get_collection_df / get_season_projection_legacy_shape) plus in-memory
computation. Nothing here writes to the database.
"""
import random
from typing import Dict, List, Tuple

from services.data_service import get_season_projection_legacy_shape
from services.db_service import get_collection_df

NFL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB",
    "TEN", "WAS",
]

WILDCARD_PROBABILITY = 0.08
MIN_WILDCARDS_PER_DRAFT = 2


def get_pick_sequence() -> List[Dict[str, int]]:
    """Derive the 30-pick sequence (pick number -> draft slot 1-10) from
    draft_order_rules, using whichever season currently has rows.

    Deliberately decoupled from the season used for team projections: the
    pickOne/pickTwo/pickThree pattern is copied forward season to season
    (see routes/admin_routes.py::create_new_season), so any available
    season's rules produce the same slot structure, and the mock draft
    keeps working even if the target season's rules get wiped.
    """
    rules_df = get_collection_df("draft_order_rules")
    if rules_df.empty:
        raise ValueError("No draft_order_rules configured for any season.")

    season = int(rules_df["season"].max())
    season_rules = rules_df[rules_df["season"] == season]

    entries = []
    for _, row in season_rules.iterrows():
        slot = int(row["draftOrder"])
        for pick_col in ("pickOne", "pickTwo", "pickThree"):
            entries.append({"pick": int(row[pick_col]), "slot": slot})
    entries.sort(key=lambda e: e["pick"])
    return entries


def get_projection_season() -> int:
    """The season whose team win projections the mock draft should use —
    the most recent season present in draft_order.
    """
    order_df = get_collection_df("draft_order")
    if order_df.empty:
        raise ValueError("No draft_order configured for any season.")
    return int(order_df["season"].max())


def _weighted_rank_pick(ranked_teams: List[str]) -> str:
    """Weighted-random pick from a projection-ranked list: the top team is
    most likely, decaying geometrically down the list, rather than always
    taking the single best team (so 9 bots don't draft identically).
    """
    weights = [0.6 ** i for i in range(len(ranked_teams))]
    total = sum(weights)
    roll = random.random() * total
    cumulative = 0.0
    for team, weight in zip(ranked_teams, weights):
        cumulative += weight
        if roll <= cumulative:
            return team
    return ranked_teams[-1]


def bot_pick(
    season: int,
    available_teams: List[str],
    wildcards_so_far: int,
    bot_picks_remaining: int,
) -> Tuple[str, bool]:
    """Choose a team for a bot-controlled mock draft slot.

    Returns (team, was_wildcard). Guarantees at least MIN_WILDCARDS_PER_DRAFT
    wildcard picks across a full draft's worth of calls via a pity mechanic:
    once the remaining bot picks can no longer make up the shortfall against
    the minimum, this pick is forced to be a wildcard.
    """
    if not available_teams:
        raise ValueError("available_teams must not be empty.")

    needed = max(0, MIN_WILDCARDS_PER_DRAFT - wildcards_so_far)
    forced = needed >= bot_picks_remaining
    was_wildcard = forced or random.random() < WILDCARD_PROBABILITY

    if was_wildcard:
        return random.choice(available_teams), True

    projections = get_season_projection_legacy_shape(season)
    if not projections:
        return random.choice(available_teams), False

    ranked = sorted(
        available_teams,
        key=lambda t: (projections.get(t) or {}).get("projected_wins", 0) or 0,
        reverse=True,
    )
    return _weighted_rank_pick(ranked), False
