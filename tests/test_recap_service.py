import pytest
import pandas as pd
from unittest.mock import patch
from services.constants import UNDRAFTED_SENTINEL
from services.recap_service import extract_weekly_data

@patch("services.recap_service.load_data")
def test_extract_weekly_data(mock_load_data, sample_games_df):
    """
    Verify that the context extraction logic accurately parses games to find
    "Notable Wins" and "Bad Beats" returning the string payload.
    """
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "BUF", "playerId": 1},
        {"season": 2024, "team": "MIA", "playerId": 2}
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
        {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"}
    ])
    
    standings = pd.DataFrame()
    teams = pd.DataFrame([{"team": "BUF", "name": "Bills"}, {"team": "MIA", "name": "Dolphins"}])
    draft_order = pd.DataFrame()
    draft_rules = pd.DataFrame()
    
    mock_load_data.return_value = (standings, teams, games, players, draft_order, draft_results, draft_rules)
    
    with patch("services.recap_service.get_enriched_schedule") as mock_enriched:
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "BUF", "away_team": "MIA", 
                "home_score": 35, "away_score": 30, "result": 5,
                "playerId": 2, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Bob",
                "spread_line": -10.0
            }
        ])
        
        data_summary, emails = extract_weekly_data(2024, 1)
        
        assert "TFish" in data_summary
        assert "Bob" in data_summary
        assert "Scored 30 and still lost" in data_summary

@patch("services.recap_service.load_data")
def test_extract_weekly_data_credits_drafted_side_vs_undrafted_opponent(mock_load_data, sample_games_df):
    """
    A drafted team playing an undrafted team (this pool doesn't draft all 32
    teams) must still count for the drafted side's weekly record -- not be
    dropped entirely because the opponent has no owner. Regression test for
    a bug where e.g. a 3-team roster with one game against an undrafted team
    showed a 1-1 weekly record instead of the real 2-0.
    """
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "BUF", "playerId": 1},
        {"season": 2024, "team": "KC", "playerId": 1},
        {"season": 2024, "team": "MIA", "playerId": 2},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
        {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"},
    ])

    mock_load_data.return_value = (pd.DataFrame(), pd.DataFrame(), games, players, pd.DataFrame(), draft_results, pd.DataFrame())

    with patch("services.recap_service.get_enriched_schedule") as mock_enriched:
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "BUF", "away_team": "MIA",
                "home_score": 35, "away_score": 30, "result": 5,
                "playerId": 2, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Bob",
            },
            {
                "week": 1,
                "home_team": "KC", "away_team": "ARI",
                "home_score": 20, "away_score": 10, "result": 10,
                "playerId": UNDRAFTED_SENTINEL, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Undrafted",
            },
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        assert "PLAYER: TFish\nWEEKLY RESULT: 2-0" in data_summary


@patch("services.recap_service.load_data")
def test_extract_weekly_data_calls_out_loss_to_undrafted_team(mock_load_data, sample_games_df):
    """
    Losing outright to an undrafted team should always get a bad-beat callout,
    even when the margin is too large/small to trip the existing margin-based
    bad-beat checks (which only fire on a <=3pt loss or a 30+pt scoring loss).
    """
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "BUF", "playerId": 1},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
    ])

    mock_load_data.return_value = (pd.DataFrame(), pd.DataFrame(), games, players, pd.DataFrame(), draft_results, pd.DataFrame())

    with patch("services.recap_service.get_enriched_schedule") as mock_enriched:
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "BUF", "away_team": "ARI",
                "home_score": 10, "away_score": 20, "result": -10,
                "playerId": UNDRAFTED_SENTINEL, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Undrafted",
            },
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        assert "Lost outright to a team nobody even drafted" in data_summary
        assert "PLAYER: TFish\nWEEKLY RESULT: 0-1" in data_summary


def test_extract_weekly_data_handles_future_weeks():
    """
    Verify that generating a recap for a week with 0 completed games safely
    returns an empty structure rather than crashing pandas.
    """
    with patch("services.recap_service.load_data") as mock_load:
        # Pass empty frames
        mock_load.return_value = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), 
                                  pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        
        with patch("services.recap_service.get_enriched_schedule") as mock_enriched:
            mock_enriched.return_value = pd.DataFrame()
            
            data_summary, emails = extract_weekly_data(2024, 18)
            assert data_summary is None
            assert len(emails) == 0
