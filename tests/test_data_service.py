import time
import pandas as pd
from unittest.mock import patch, MagicMock
from services.data_service import load_data, get_latest_season_and_week
from services.analysis_service import process_games_data, get_season_progress

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


# ── Issue #50: year-slice pkl fallback ────────────────────────────────────

def test_load_data_year_slice_falls_back_to_base_pkl(monkeypatch, tmp_path):
    """When the year-specific pkl is absent, fetch_or_load filters the base pkl."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    import services.cache_service as cs
    cs._DATA_CACHE.clear()
    cs._CACHE_TIMESTAMPS.clear()

    local_db = tmp_path / ".local_db"
    local_db.mkdir()

    # Base pkl with two seasons — no year-specific pkl written
    games_all = pd.DataFrame({
        "season": [2024, 2024, 2025],
        "week": [1, 2, 1],
        "team": ["BUF", "KC", "SF"],
    })
    games_all.to_pickle(local_db / "nfl_games.pkl")

    empty = pd.DataFrame()
    for name in ["nfl_standings", "nfl_teams", "players", "draft_order", "draft_results", "draft_order_rules"]:
        empty.to_pickle(local_db / f"{name}.pkl")

    try:
        result = load_data(year=2024)
        games = result.games
        assert not games.empty
        assert set(games["season"].unique()) == {2024}
        assert len(games) == 2
        # Side-effect: year-slice pkl must be created for future fast reads
        assert (local_db / "nfl_games_2024.pkl").exists()
    finally:
        cs._DATA_CACHE.clear()
        cs._CACHE_TIMESTAMPS.clear()


# ── Issue #91: in-memory cache TTL expiry ────────────────────────────────

def test_load_data_cache_ttl_expiry_triggers_refetch(monkeypatch, tmp_path):
    """After TTL expiry load_data() re-reads from disk rather than serving stale cache."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    import services.cache_service as cs
    cs._DATA_CACHE.clear()
    cs._CACHE_TIMESTAMPS.clear()

    local_db = tmp_path / ".local_db"
    local_db.mkdir()

    # v1 pkls — sentinel value 1 in nfl_games
    games_v1 = pd.DataFrame({"season": [2024], "team": ["BUF"], "_sentinel": [1]})
    games_v1.to_pickle(local_db / "nfl_games.pkl")
    empty = pd.DataFrame()
    for name in ["nfl_standings", "nfl_teams", "players", "draft_order", "draft_results", "draft_order_rules"]:
        empty.to_pickle(local_db / f"{name}.pkl")

    try:
        result1 = load_data()
        assert result1.games["_sentinel"].iloc[0] == 1

        # Force TTL expiry by clearing timestamps in-place
        cs._CACHE_TIMESTAMPS.clear()

        # Replace pkl with v2 data before second call
        games_v2 = pd.DataFrame({"season": [2024], "team": ["KC"], "_sentinel": [2]})
        games_v2.to_pickle(local_db / "nfl_games.pkl")

        result2 = load_data()
        # Must have re-fetched from disk — not served the old cached v1 data
        assert result2.games["_sentinel"].iloc[0] == 2
    finally:
        cs._DATA_CACHE.clear()
        cs._CACHE_TIMESTAMPS.clear()


# ── Issue #92: remote Firestore cache invalidation signal ────────────────────

def test_remote_cache_invalidation_calls_clear_when_remote_is_newer(monkeypatch):
    """When Firestore reports a newer last_update, clear_data_cache() is called."""
    monkeypatch.setenv("USE_LOCAL_DATA", "false")

    import services.cache_service as cs

    now = time.time()
    local_ts = now - 120          # local cache timestamp is 2 minutes old
    remote_ts = now + 1           # remote says there is a newer update

    cs._DATA_CACHE.clear()
    cs._CACHE_TIMESTAMPS.clear()
    cs._DATA_CACHE['all'] = MagicMock()   # warm the in-memory cache
    cs._CACHE_TIMESTAMPS['all'] = local_ts
    cs._LAST_REMOTE_CHECK = 0             # force the remote check to run

    mock_ctrl = MagicMock()
    mock_ctrl.exists = True
    mock_ctrl.to_dict.return_value = {"last_update": remote_ts}

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.get.return_value = mock_ctrl

    try:
        with patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.data_service.clear_data_cache") as mock_clear, \
             patch("services.data_service.get_collection_df", return_value=pd.DataFrame()):
            load_data()

        mock_clear.assert_called_once()
    finally:
        cs._DATA_CACHE.clear()
        cs._CACHE_TIMESTAMPS.clear()
        cs._LAST_REMOTE_CHECK = 0
