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


def test_duplicate_standings_rows_for_one_normalized_team_do_not_multiply_wins():
    standings, draft, players = _frames("OAK", "OAK")
    both = pd.concat([standings, standings.assign(team="LV", wins=3)], ignore_index=True)
    out = calculate_wins_pool_standings(both, draft, players, 2018, team_records={})
    assert len(out) == 1
    assert out["TotalWins"].iloc[0] in (10, 3)  # one standings row is used, never the sum (13)


@pytest.mark.parametrize("draft_team,records_key", [("OAK", "LV"), ("LV", "OAK"), ("OAK", "OAK")])
def test_global_record_found_under_either_abbreviation(draft_team, records_key):
    standings, draft, players = _frames(draft_team, draft_team)
    out = calculate_wins_pool_standings(
        standings, draft, players, 2018, team_records={records_key: {"W": 10, "L": 6, "T": 0}})
    assert out["global_record1"].iloc[0] == "10-6"


def test_format_team_record_matches_normalized_key():
    from services.analysis_service import format_team_record
    recs = {"LV": {"W": 9, "L": 7, "T": 1}}
    assert format_team_record("OAK", recs) == "9-7-1"
    assert format_team_record("KC", recs) == "0-0"
