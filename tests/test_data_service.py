import pandas as pd
from services.data_service import (
    load_data, 
    process_games_data, 
    get_latest_season_and_week, 
    get_season_progress
)

def test_load_data():
    """Verify that all 7 required dataframes load successfully."""
    res = load_data()
    assert len(res) == 7
    for df in res:
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

def test_process_games_data():
    """Verify wins are correctly aggregated."""
    # Create simple mock games
    data = {
        'season': [2024, 2024],
        'week': [1, 1],
        'game_type': ['REG', 'REG'],
        'home_team': ['KC', 'PHI'],
        'away_team': ['BAL', 'DAL'],
        'result': [7, -3]  # Positive means home wins, negative means away wins
    }
    df = pd.DataFrame(data)
    processed = process_games_data(df)
    
    assert 'team' in processed.columns
    assert 'TotalWinsBySeason' in processed.columns
    # KC should win first game
    assert processed.iloc[0]['team'] == 'KC'
    # DAL should win second game
    assert processed.iloc[1]['team'] == 'DAL'

def test_get_season_progress():
    """Verify the progress payload returns the required dict structures."""
    # End-to-end read
    res = get_season_progress(2023, 10)
    assert "player_chart" in res
    assert "team_chart" in res
    assert "standings" in res
    
    assert isinstance(res["player_chart"]["labels"], list)
    assert isinstance(res["team_chart"]["datasets"], list)
    assert len(res["standings"]) > 0

def test_load_data_with_debug_flag(monkeypatch):
    """Verify load_data() still succeeds and returns all dataframes with debug flag enabled."""
    monkeypatch.setenv("DEBUG_PAGE_LOAD", "True")
    res = load_data()
    assert len(res) == 7
    for df in res:
        assert isinstance(df, pd.DataFrame)
        assert not df.empty
