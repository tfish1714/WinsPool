import pandas as pd
from services.analysis_service import calculate_wins_pool_standings

def test_calculate_wins_pool_standings_orders_by_draft_pick():
    """
    Verify that teams in the reshaped standings are ordered by draft pick.
    """
    standings = pd.DataFrame([
        {"team": "KC", "season": 2024, "wins": 12, "scored": 400, "allowed": 300},
        {"team": "SF", "season": 2024, "wins": 11, "scored": 420, "allowed": 310},
        {"team": "BAL", "season": 2024, "wins": 13, "scored": 450, "allowed": 280},
    ])
    
    # Picks are made in this order: BAL (1), SF (10), KC (20)
    draft_results = pd.DataFrame([
        {"team": "KC", "season": 2024, "playerId": 1, "draftPick": 20},
        {"team": "SF", "season": 2024, "playerId": 1, "draftPick": 10},
        {"team": "BAL", "season": 2024, "playerId": 1, "draftPick": 1},
    ])
    
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Tom Fischer", "nickName": "Tom"}
    ])
    
    # Act
    result_df = calculate_wins_pool_standings(standings, draft_results, players, 2024)
    
    # Assert
    # team1 should be BAL (pick 1), team2 should be SF (pick 10), team3 should be KC (pick 20)
    row = result_df.iloc[0]
    assert row['team1'] == "BAL"
    assert row['team2'] == "SF"
    assert row['team3'] == "KC"
    
    # Also verify wins/ptDiff follow the same order
    assert row['wins1'] == 13
    assert row['wins2'] == 11
    assert row['wins3'] == 12
