"""calculate_wins_pool_standings() when nfl_standings has no row for a drafted team.

nfl_standings is computed only from COMPLETED regular-season games, so a team
has no standings row until its first game is final. Two real situations follow:

1. A season whose draft is complete but which has played no games yet (the
   window between the draft finishing and Week 1 ending) has NO standings rows
   at all. The function merged an empty, column-less frame on ['team',
   'season'] and raised KeyError 'team', so /wins-pool/{year} returned HTTP 500
   and GET / (which redirects to the newest season's standings) broke with it.
2. Mid-Week-1, teams that have not played yet have no row while others do.
   The old inner join silently dropped those teams from a player's slots, so
   a player showed two teams (or one) instead of three.

A drafted team with no standings row is a team with zero games played: 0 wins,
0 point differential.
"""
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app
from services.analysis_service import calculate_wins_pool_standings

SEASON = 3000


def _players():
    return pd.DataFrame([
        {"playerId": 1, "fullName": "Ann Lee", "nickName": "Ann"},
        {"playerId": 2, "fullName": "Bo Kim", "nickName": "Bo"},
    ])


def _draft():
    """Player 1: BAL, KC, SF. Player 2: DEN, LA, NE (six distinct teams)."""
    rows = [
        (1, "BAL", 1), (1, "KC", 4), (1, "SF", 5),
        (2, "DEN", 2), (2, "LA", 3), (2, "NE", 6),
    ]
    return pd.DataFrame([
        {"team": t, "season": SEASON, "playerId": p, "draftPick": pick} for p, t, pick in rows
    ])


def _standings(rows):
    return pd.DataFrame([
        {"team": t, "season": SEASON, "wins": w, "losses": 0, "ties": 0, "scored": s, "allowed": a}
        for t, w, s, a in rows
    ])


@pytest.mark.parametrize("empty", [
    pd.DataFrame(),                                            # no rows, no columns
    pd.DataFrame(columns=["team", "season", "wins", "scored", "allowed"]),  # typed but empty
])
def test_season_with_no_standings_yet_ranks_everyone_at_zero(empty):
    out = calculate_wins_pool_standings(empty, _draft(), _players(), SEASON)

    assert len(out) == 2
    assert list(out["TotalWins"]) == [0, 0]
    ann = out[out["playerId"] == 1].iloc[0]
    assert [ann["team1"], ann["team2"], ann["team3"]] == ["BAL", "KC", "SF"]  # still in pick order
    assert [ann["wins1"], ann["wins2"], ann["wins3"]] == [0, 0, 0]
    assert [ann["ptDiff1"], ann["ptDiff2"], ann["ptDiff3"]] == [0, 0, 0]


def test_none_standings_is_treated_like_empty():
    out = calculate_wins_pool_standings(None, _draft(), _players(), SEASON)
    assert len(out) == 2 and list(out["TotalWins"]) == [0, 0]


def test_standings_for_other_seasons_only_counts_as_no_standings():
    other = pd.DataFrame([{"team": "KC", "season": 2024, "wins": 12, "scored": 400, "allowed": 300}])
    out = calculate_wins_pool_standings(other, _draft(), _players(), SEASON)
    assert len(out) == 2 and list(out["TotalWins"]) == [0, 0]


def test_team_with_no_row_yet_is_kept_at_zero_not_dropped():
    """Mid-Week-1: KC and DEN have played, everyone else has not."""
    standings = _standings([("KC", 1, 27, 20), ("DEN", 1, 24, 17)])

    out = calculate_wins_pool_standings(standings, _draft(), _players(), SEASON)

    ann = out[out["playerId"] == 1].iloc[0]
    bo = out[out["playerId"] == 2].iloc[0]
    assert [ann["team1"], ann["team2"], ann["team3"]] == ["BAL", "KC", "SF"]  # all three slots kept
    assert [ann["wins1"], ann["wins2"], ann["wins3"]] == [0, 1, 0]
    assert ann["ptDiff2"] == 7
    assert ann["TotalWins"] == 1
    assert [bo["team1"], bo["team2"], bo["team3"]] == ["DEN", "LA", "NE"]
    assert bo["TotalWins"] == 1


def test_full_standings_are_unchanged():
    """Regression guard: with a row for every drafted team the result is what
    it always was."""
    standings = _standings([
        ("BAL", 13, 450, 280), ("KC", 12, 400, 300), ("SF", 11, 420, 310),
        ("DEN", 10, 380, 350), ("LA", 9, 370, 360), ("NE", 4, 250, 400),
    ])

    out = calculate_wins_pool_standings(standings, _draft(), _players(), SEASON)

    ann = out[out["playerId"] == 1].iloc[0]
    bo = out[out["playerId"] == 2].iloc[0]
    assert ann["TotalWins"] == 36 and bo["TotalWins"] == 23
    assert list(out["playerId"]) == [1, 2]  # ranked by total wins
    assert [ann["ptDiff1"], ann["ptDiff2"], ann["ptDiff3"]] == [170, 100, 110]


def test_undrafted_team_standings_rows_do_not_leak_into_the_pool():
    standings = _standings([("BAL", 13, 450, 280), ("ZZZ", 17, 500, 100)])
    out = calculate_wins_pool_standings(standings, _draft(), _players(), SEASON)
    assert len(out) == 2
    assert "ZZZ" not in set(out[["team1", "team2", "team3"]].to_numpy().ravel())


client = TestClient(app)


def _load_data_for_completed_draft_without_games():
    empty = pd.DataFrame()
    return (
        empty,                      # nfl_standings: no rows for the season
        empty,                      # teams
        pd.DataFrame(),             # games: none played
        _players(),
        empty,
        _draft(),
        pd.DataFrame(),
    )


def test_standings_page_renders_for_a_completed_draft_with_no_games():
    """The e2e failure: /wins-pool/{season} returned HTTP 500 after pick #30."""
    with patch("routes.standings_routes.load_data",
               return_value=_load_data_for_completed_draft_without_games()), \
         patch("routes.standings_routes.get_active_season", return_value=SEASON), \
         patch("routes.standings_routes.get_available_years", return_value=[SEASON]), \
         patch("routes.standings_routes.get_latest_week_for_year", return_value=0), \
         patch("routes.standings_routes.analysis.get_draft_progress", return_value=(6, 6)), \
         patch("routes.standings_routes.analysis.get_enriched_schedule", return_value=pd.DataFrame()), \
         patch("routes.standings_routes.analysis.player_winlossmatrix", return_value=pd.DataFrame()), \
         patch("routes.standings_routes.db.get_weekly_recap", return_value=None):
        res = client.get(f"/wins-pool/{SEASON}")

    assert res.status_code == 200
    assert "Ann Lee" in res.text and 'id="winsChart"' in res.text


def test_live_standings_api_serves_a_completed_draft_with_no_games():
    with patch("routes.api_routes.load_data",
               return_value=_load_data_for_completed_draft_without_games()), \
         patch("routes.api_routes.analysis.get_draft_progress", return_value=(6, 6)):
        res = client.get(f"/api/live-standings?year={SEASON}")

    assert res.status_code == 200
    body = res.json()
    assert [row["full_name"] for row in body["standings"]] == ["Ann Lee", "Bo Kim"]
    assert all(row["total_wins"] == 0 for row in body["standings"])
