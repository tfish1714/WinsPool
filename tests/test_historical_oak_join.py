"""Historical Oakland/Las Vegas rows join whichever abbreviation each side uses (5.7)."""
import pandas as pd
import pytest

from services.analysis_service import calculate_wins_pool_standings


def _frames(draft_team, standings_team):
    draft = pd.DataFrame({"season": [2018], "playerId": [1], "draftPick": [1], "team": [draft_team]})
    standings = pd.DataFrame({"season": [2018], "team": [standings_team], "wins": [10], "losses": [6],
                              "ties": [0], "scored": [400], "allowed": [300], "net": [100], "pct": [0.625]})
    players = pd.DataFrame({"playerId": [1], "fullName": ["A"]})
    return standings, draft, players


@pytest.mark.parametrize("draft_team,standings_team", [("OAK", "LV"), ("LV", "OAK"), ("OAK", "OAK")])
def test_oak_lv_rows_join(draft_team, standings_team):
    standings, draft, players = _frames(draft_team, standings_team)
    out = calculate_wins_pool_standings(standings, draft, players, 2018, team_records={})
    assert out["wins1"].iloc[0] == 10
    assert out["team1"].iloc[0] == draft_team  # displayed abbreviation is untouched
