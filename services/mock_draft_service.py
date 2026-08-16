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
