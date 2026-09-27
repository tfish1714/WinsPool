"""Pure helpers describing the state of a season's pool draft.

Kept free of any DB/config access: "is the draft complete" is a persistent,
season-scoped fact derived from the draft tables, deliberately independent of
the transient `draft_active` flag (which an admin can toggle off mid-draft).
"""
import pandas as pd

PICK_COLUMNS = ("pickOne", "pickTwo", "pickThree")
DEFAULT_TOTAL_PICKS = 30
PICKS_PER_PLAYER = 3


def _season_rows(df, season):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    if "season" in df.columns:
        return df[df["season"] == season]
    return df


def total_picks_for_season(season, draft_order=None, draft_order_rules=None) -> int:
    """Total picks defined for `season`.

    1. draft_order_rules: count of non-null pick cells (pickOne/Two/Three) over
       the season's rule rows.
    2. else draft_order rows for the season x 3.
    3. else 30.
    """
    rules = _season_rows(draft_order_rules, season)
    if not rules.empty:
        cols = [c for c in PICK_COLUMNS if c in rules.columns]
        if cols:
            total = int(rules[cols].notna().sum().sum())
            if total > 0:
                return total
    order = _season_rows(draft_order, season)
    if not order.empty:
        return len(order) * PICKS_PER_PLAYER
    return DEFAULT_TOTAL_PICKS


def draft_is_complete(season, draft_results, draft_order, draft_order_rules) -> bool:
    """True when `season`'s draft_results hold at least as many picks as the
    season defines (see total_picks_for_season) and at least one pick exists.

    Picks are counted as distinct non-null `draftPick` values when that column
    exists (so a duplicated row cannot inflate the count), else as rows.
    """
    results = _season_rows(draft_results, season)
    if results.empty:
        return False
    if "draftPick" in results.columns:
        made = int(results["draftPick"].dropna().nunique())
    else:
        made = len(results)
    if made < 1:
        return False
    return made >= total_picks_for_season(season, draft_order, draft_order_rules)
