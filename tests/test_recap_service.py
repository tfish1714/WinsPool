import pytest
import pandas as pd
from unittest.mock import patch
from services.constants import UNDRAFTED_SENTINEL
from services.recap_service import extract_weekly_data, _detect_comeback_win

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
def test_extract_weekly_data_includes_drafted_teams_and_their_records(mock_load_data, sample_games_df):
    """The AI recap prompt needs each player's full roster and those teams'
    real overall W-L records (not just the pool win/loss above) so it can
    comment on roster context, not just this week's result."""
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
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        # BUF/KC/DAL each played once in the fixture's week 1 games (sample_games_df):
        # KC beat BUF 27-24, PHI beat DAL 22-16 -- so BUF is 0-1 and KC is 1-0.
        # No draftPick column in this test's draft_results -> pick/round omitted.
        assert "TEAMS: BUF (0-1), KC (1-0)" in data_summary


@patch("services.recap_service.load_data")
def test_extract_weekly_data_round_ignores_extra_registered_accounts(mock_load_data, sample_games_df):
    """Regression test: `players` can contain far more rows than this
    season's actual drafters (e.g. seeded e2e test accounts) -- the round
    math must come from the draft itself (picks / 3), not len(players), or
    every round number silently shifts wrong."""
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "KC", "playerId": 1, "draftPick": 1},
        {"season": 2024, "team": "DAL", "playerId": 2, "draftPick": 3},
        {"season": 2024, "team": "BUF", "playerId": 1, "draftPick": 4},
        {"season": 2024, "team": "PHI", "playerId": 2, "draftPick": 6},
        {"season": 2024, "team": "SF", "playerId": 1, "draftPick": 7},
        {"season": 2024, "team": "MIA", "playerId": 2, "draftPick": 2},
    ])
    # 20 extra non-drafting accounts in `players` -- must not affect rounds.
    players = pd.DataFrame(
        [{"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
         {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"}]
        + [{"playerId": 100 + i, "nickName": f"E2E Test Player {i:02d}",
            "fullName": f"E2E Test Player {i:02d}", "email": f"e2e{i}@mock.com"} for i in range(20)]
    )
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
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        # If total_players wrongly came from len(players)=22, pick 7 would
        # round to Rd 1 instead of the correct Rd 4 (6 real picks / 3 = 2 players).
        assert "SF (Pick #7, Rd 4, 0-0)" in data_summary


@patch("services.recap_service.load_data")
def test_extract_weekly_data_includes_draft_pick_and_round(mock_load_data, sample_games_df):
    """With draftPick present, the roster line should also show the pick
    number and round -- so the AI can comment on reaches/steals, not just
    the current record. Pool size must be derived from the draft itself
    (picks / 3 teams-per-player), not len(players) -- a realistic 2-player
    draft has exactly 6 picks (3 each), matching this fixture."""
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "KC", "playerId": 1, "draftPick": 1},
        {"season": 2024, "team": "DAL", "playerId": 2, "draftPick": 3},
        {"season": 2024, "team": "BUF", "playerId": 1, "draftPick": 4},
        {"season": 2024, "team": "PHI", "playerId": 2, "draftPick": 6},
        {"season": 2024, "team": "SF", "playerId": 1, "draftPick": 7},
        {"season": 2024, "team": "MIA", "playerId": 2, "draftPick": 2},
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
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        # 6 picks / 3 teams-per-player = 2 players in this draft's pool ->
        # pick 1 = round 1, pick 4 = round 2, pick 7 = round 4 (ceil(7/2)).
        assert "TEAMS: KC (Pick #1, Rd 1, 1-0), BUF (Pick #4, Rd 2, 0-1), SF (Pick #7, Rd 4, 0-0)" in data_summary


@patch("services.recap_service.load_data")
def test_extract_weekly_data_includes_ranked_standings(mock_load_data, sample_games_df):
    """The recap is sent standalone (no separate standings page in context),
    so the ranked standings list must be explicit rather than left for
    Gemini to infer from each player's cumulative-wins line."""
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "KC", "playerId": 1},
        {"season": 2024, "team": "MIA", "playerId": 2},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
        {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"},
    ])
    mock_load_data.return_value = (pd.DataFrame(), pd.DataFrame(), games, players, pd.DataFrame(), draft_results, pd.DataFrame())

    with patch("services.recap_service.get_enriched_schedule") as mock_enriched:
        # TFish's KC beat Bob's MIA -- TFish should rank #1.
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "KC", "away_team": "MIA",
                "home_score": 27, "away_score": 10, "result": 17,
                "playerId": 2, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Bob",
            },
        ])

        data_summary, emails = extract_weekly_data(2024, 1)

        standings_section = data_summary.split("PLAYER:")[0]
        assert "SEASON STANDINGS (THROUGH WEEK 1):" in standings_section
        assert " 1. TFish - 1 wins" in standings_section
        assert " 2. Bob - 0 wins" in standings_section


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

        assert "WEEKLY RESULT: 2-0" in data_summary


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
        assert "WEEKLY RESULT: 0-1" in data_summary


class TestDetectComebackWin:
    """Table-driven tests for the comeback predicate. See Design §5 of
    docs/superpowers/specs/2026-09-15-comeback-win-recap-design.md."""

    def test_trailing_entering_q4_is_a_comeback(self):
        qrow = {"home_q1": 0, "home_q2": 0, "home_q3": 3, "away_q1": 0, "away_q2": 0, "away_q3": 10}
        result = _detect_comeback_win(qrow, winner_is_home=True)
        assert result is not None
        assert "entering the 4th quarter" in result

    def test_early_14plus_deficit_then_comfortable_win_is_a_comeback(self):
        qrow = {"home_q1": 0, "home_q2": 20, "home_q3": 10,
                 "away_q1": 14, "away_q2": 0, "away_q3": 0}
        result = _detect_comeback_win(qrow, winner_is_home=True)
        assert result is not None
        assert "after the 1st quarter" in result
        assert "Down 14" in result

    def test_exactly_14_at_halftime_is_a_comeback(self):
        qrow = {"home_q1": 0, "home_q2": 0, "home_q3": 20,
                 "away_q1": 7, "away_q2": 7, "away_q3": 0}
        result = _detect_comeback_win(qrow, winner_is_home=True)
        assert result is not None
        assert "at halftime" in result
        assert "Down 14" in result

    def test_never_behind_by_14_and_not_behind_after_q3_is_not_a_comeback(self):
        qrow = {"home_q1": 5, "home_q2": 5, "home_q3": 5,
                 "away_q1": 10, "away_q2": 5, "away_q3": 0}
        assert _detect_comeback_win(qrow, winner_is_home=True) is None

    def test_away_winner_uses_away_perspective(self):
        qrow = {"home_q1": 0, "home_q2": 0, "home_q3": 10, "away_q1": 0, "away_q2": 0, "away_q3": 3}
        result = _detect_comeback_win(qrow, winner_is_home=False)
        assert result is not None
        assert "entering the 4th quarter" in result


@patch("services.recap_service.load_data")
def test_extract_weekly_data_includes_comeback_win_line(mock_load_data, sample_games_df):
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "BUF", "playerId": 1},
        {"season": 2024, "team": "MIA", "playerId": 2},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
        {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"},
    ])
    mock_load_data.return_value = (pd.DataFrame(), pd.DataFrame(), games, players, pd.DataFrame(), draft_results, pd.DataFrame())

    with patch("services.recap_service.get_enriched_schedule") as mock_enriched, \
         patch("services.recap_service.get_quarter_scores_season") as mock_qs:
        # Home (BUF) wins 24-20 -- margin of 4 avoids every other notable-win/
        # bad-beat bucket, isolating the comeback callout.
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "BUF", "away_team": "MIA",
                "home_score": 24, "away_score": 20, "result": 4,
                "playerId": 2, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Bob",
            },
        ])
        # BUF trailed 14-20 entering the 4th (cumulative through Q3), then
        # outscored MIA 10-0 in the 4th to win 24-20.
        mock_qs.return_value = [{
            "week": 1, "home_team": "BUF", "away_team": "MIA",
            "home_q1": 0, "home_q2": 10, "home_q3": 4, "home_q4": 10,
            "away_q1": 0, "away_q2": 10, "away_q3": 10, "away_q4": 0,
        }]

        data_summary, emails = extract_weekly_data(2024, 1)

        assert "still found a way to win" in data_summary
        assert "entering the 4th quarter" in data_summary
        # Only the winner (TFish/BUF) gets the callout, not the loser (Bob/MIA).
        tfish_block, bob_block = data_summary.split("PLAYER: Bob")
        assert "still found a way to win" in tfish_block
        assert "still found a way to win" not in bob_block


@patch("services.recap_service.load_data")
def test_extract_weekly_data_skips_comeback_check_when_no_quarter_data(mock_load_data, sample_games_df):
    """No matching quarter-score row (scrape hasn't run yet, or predates the
    pipeline) must be a silent no-op, not an error."""
    games = sample_games_df
    draft_results = pd.DataFrame([
        {"season": 2024, "team": "BUF", "playerId": 1},
        {"season": 2024, "team": "MIA", "playerId": 2},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "nickName": "TFish", "fullName": "TFish", "email": "tfish@mock.com"},
        {"playerId": 2, "nickName": "Bob", "fullName": "Bob", "email": "bob@mock.com"},
    ])
    mock_load_data.return_value = (pd.DataFrame(), pd.DataFrame(), games, players, pd.DataFrame(), draft_results, pd.DataFrame())

    with patch("services.recap_service.get_enriched_schedule") as mock_enriched, \
         patch("services.recap_service.get_quarter_scores_season") as mock_qs:
        mock_enriched.return_value = pd.DataFrame([
            {
                "week": 1,
                "home_team": "BUF", "away_team": "MIA",
                "home_score": 24, "away_score": 20, "result": 4,
                "playerId": 2, "playerId_home_draft": 1,
                "fullName_home": "TFish", "fullName_away": "Bob",
            },
        ])
        mock_qs.return_value = []

        data_summary, emails = extract_weekly_data(2024, 1)

        assert data_summary is not None
        assert "still found a way to win" not in data_summary


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
