import datetime
import pandas as pd
from services.constants import UNDRAFTED_SENTINEL


def get_future_schedule(year: int) -> pd.DataFrame:
    """Generate a mock schedule for a future season using prior-year matchup structure.

    Reads the prior year's schedule from Firestore/pkl via load_data() — never from CSV.
    Returns a DataFrame with the same structure as get_enriched_schedule() output,
    with UNDRAFTED_SENTINEL scores and no draft assignment columns.
    """
    from services.data_service import load_data

    _, _, prior_games, _, _, _, _ = load_data(year=year - 1)

    if prior_games.empty or "home_team" not in prior_games.columns:
        return pd.DataFrame()

    # Keep only regular-season weeks (1-18) with valid matchup pairs
    prior_reg = prior_games[
        prior_games["week"].between(1, 18)
    ][["week", "home_team", "away_team"]].dropna()

    if prior_reg.empty:
        return pd.DataFrame()

    # Use the same week/home/away matchup structure; just change the season year
    base_date = datetime.datetime(year, 9, 10)
    rows = []
    for _, row in prior_reg.iterrows():
        wk = int(row["week"])
        ht = str(row["home_team"])
        at = str(row["away_team"])
        gameday = base_date + datetime.timedelta(days=(wk - 1) * 7)
        rows.append({
            "season":      year,
            "week":        wk,
            "gameday":     gameday,
            "home_team":   ht,
            "away_team":   at,
            "home_score":  UNDRAFTED_SENTINEL,
            "away_score":  UNDRAFTED_SENTINEL,
            "result":      UNDRAFTED_SENTINEL,
            "spread_line": None,
            "total_line":  None,
            "home_moneyline":   None,
            "home_spread_odds": None,
            "pred_winner":   None,
            "pred_su_conf":  None,
            "pred_ats_pick": None,
            "pred_prob":     None,
            "fullName_home": "—",
            "fullName_away": "—",
        })

    return pd.DataFrame(rows)
