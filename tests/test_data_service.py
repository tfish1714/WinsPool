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


# ── Issue #50 (rewritten): historical-year slice in local mode ─────────────

def test_load_data_historical_year_slice_served_from_base_pkl(monkeypatch, tmp_path):
    """Issue #50's year-suffixed pkl fallback is gone. get_collection_df() now
    reads the single base pkl and load_data() slices the requested historical
    season out of the in-memory historical bucket, so a historical year is still
    correctly retrievable in local mode — with no year-slice pkl written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    import services.cache_service as cs
    cs.clear_data_cache()

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
        # 2025 is the active season here, so 2024 came out of the historical
        # bucket — and no year-slice pkl is written any more.
        assert not (local_db / "nfl_games_2024.pkl").exists()
    finally:
        cs.clear_data_cache()


# ── Issue #91: in-memory cache TTL expiry ────────────────────────────────

def test_load_data_cache_ttl_expiry_triggers_refetch(monkeypatch, tmp_path):
    """After TTL expiry load_data() re-reads from disk rather than serving stale cache."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    import services.cache_service as cs
    cs.clear_data_cache()

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

        # Force TTL expiry by re-setting cached data with stale timestamp (timestamp=0)
        # This preserves the cached value while making it appear expired by TTL
        cached_bundle = cs.get_domain(cs.DOMAIN_ACTIVE)
        if cached_bundle is not None:
            cs.set_domain(cs.DOMAIN_ACTIVE, cached_bundle, timestamp=0)

        # Replace pkl with v2 data before second call
        games_v2 = pd.DataFrame({"season": [2024], "team": ["KC"], "_sentinel": [2]})
        games_v2.to_pickle(local_db / "nfl_games.pkl")

        result2 = load_data()
        # Must have re-fetched from disk — not served the old cached v1 data
        assert result2.games["_sentinel"].iloc[0] == 2
    finally:
        cs.clear_data_cache()


# ── Issue #92: remote Firestore cache invalidation signal ────────────────────

def test_remote_cache_invalidation_calls_clear_when_remote_is_newer(monkeypatch):
    """When Firestore reports a newer active_updated signal, the active domain is cleared."""
    monkeypatch.setenv("USE_LOCAL_DATA", "false")

    import services.cache_service as cs

    now = time.time()
    local_ts = now - 120          # local cache timestamp is 2 minutes old
    remote_ts = now + 1           # remote says there is a newer update

    cs.clear_data_cache()
    cs.set_domain(cs.DOMAIN_ACTIVE, MagicMock(), timestamp=local_ts)   # warm the in-memory cache with domain-keyed setter
    cs._LAST_REMOTE_CHECK = 0             # force the remote check to run

    mock_ctrl = MagicMock()
    mock_ctrl.exists = True
    mock_ctrl.to_dict.return_value = {"active_updated": remote_ts}

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value.get.return_value = mock_ctrl

    try:
        with patch("services.db_service.get_db", return_value=mock_db), \
             patch("services.cache_service.clear_domain") as mock_clear_domain, \
             patch("services.data_service.get_collection_df", return_value=pd.DataFrame()):
            load_data()

        mock_clear_domain.assert_called_once_with(cs.DOMAIN_ACTIVE)
    finally:
        cs.clear_data_cache()
        cs._LAST_REMOTE_CHECK = 0


# ── Cache mutability redesign: active/historical/static buckets ──────────────

def test_load_data_active_season_comes_from_active_bucket(monkeypatch):
    """A season equal to the resolved active season is served from the
    active bucket, not by scanning the historical bucket."""
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    bundle = load_data()  # cold start -- bootstraps both buckets
    active_games = bundle.games
    assert not active_games.empty
    # The active bucket must now be warm without a second cold-start fetch.
    assert cs.get_domain(cs.DOMAIN_ACTIVE) is not None
    assert cs.get_domain(cs.DOMAIN_HISTORICAL) is not None


def test_load_data_historical_year_is_served_from_historical_bucket(monkeypatch):
    import services.cache_service as cs
    from services.data_service import load_data, get_active_season
    cs.clear_data_cache()
    bundle = load_data()
    active_season = get_active_season(bundle.games, bundle.draft_results, bundle.draft_order_rules)
    historical_year = active_season - 1
    result = load_data(year=historical_year)
    assert (result.games["season"] == historical_year).all() or result.games.empty


def test_load_data_none_returns_full_history_concat_of_both_buckets():
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    full = load_data(year=None)
    active_season = full.games["season"].max()
    historical_only = load_data(year=int(active_season) - 1)
    # every historical row must also appear in the unfiltered bundle
    if not historical_only.games.empty:
        assert historical_only.games["season"].iloc[0] in full.games["season"].values


def test_load_data_season_is_now_a_thin_wrapper():
    from services.data_service import load_data, load_data_season, get_active_season
    bundle = load_data()
    season = get_active_season(bundle.games, bundle.draft_results, bundle.draft_order_rules)
    direct = load_data(year=season)
    via_season = load_data_season(season)
    assert list(via_season.games.columns) == list(direct.games.columns)


def test_static_bucket_shared_across_year_arguments(monkeypatch):
    """players/draft_order/draft_results/draft_order_rules/teams never
    differ by year -- confirm both calls hit the same cached static bucket
    (no extra Firestore fetch for the second call)."""
    import services.cache_service as cs
    from services.data_service import load_data
    cs.clear_data_cache()
    first = load_data(year=None)
    static_after_first = cs.get_domain(cs.DOMAIN_STATIC)
    assert static_after_first is not None
    second = load_data(year=2020)
    assert cs.get_domain(cs.DOMAIN_STATIC) is static_after_first  # same object, not refetched
    assert list(first.players.columns) == list(second.players.columns)


def test_active_bucket_rebuilds_when_active_season_changes(monkeypatch):
    """Simulates a season rollover: the static bucket's draft data now
    resolves to a later active season than the cached active bucket."""
    import services.cache_service as cs
    from services.data_service import load_data, _get_active_bucket
    cs.clear_data_cache()
    first = load_data()
    old_active_season = first.games["season"].max() if not first.games.empty else None

    # Force a stale active bucket claiming an older season than what
    # get_active_season() would now resolve to.
    stale = cs.get_domain(cs.DOMAIN_ACTIVE)
    if stale and old_active_season is not None:
        cs.set_domain(cs.DOMAIN_ACTIVE, {**stale, "season": stale["season"] - 1})
        rebuilt = _get_active_bucket()
        assert rebuilt["season"] != stale["season"] - 1  # rebuilt against the real active season


def test_future_season_rows_are_served_from_historical_bucket(monkeypatch, tmp_path):
    """A season LATER than the resolved active season must not vanish.

    nfl_games/nfl_standings routinely carry a future season (next year's
    schedule is synced well before that season's draft completes, so
    get_active_season() still resolves to the current one). Splitting the
    buckets on `season < active` dropped those rows from both buckets, so
    neither load_data(year=<future>) nor load_data(year=None) could see them.
    From the cache's point of view a future season is just as frozen as a past
    one, so it belongs in the historical bucket too.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    import services.cache_service as cs
    from services.data_service import load_data, get_active_season
    cs.clear_data_cache()

    local_db = tmp_path / ".local_db"
    local_db.mkdir()

    # 2024 is played out; 2025's schedule exists but has no results yet.
    games_all = pd.DataFrame({
        "season": [2024, 2024, 2025],
        "week": [1, 2, 1],
        "home_team": ["BUF", "KC", "SF"],
        "away_team": ["NYJ", "DEN", "SEA"],
        "result": [3.0, 7.0, None],
    })
    games_all.to_pickle(local_db / "nfl_games.pkl")

    standings_all = pd.DataFrame({
        "season": [2024, 2025],
        "team": ["BUF", "SF"],
        "wins": [13, 0],
    })
    standings_all.to_pickle(local_db / "nfl_standings.pkl")

    # Draft picks only exist for 2024, so the active season resolves to 2024
    # while 2025 game rows are already present.
    pd.DataFrame({"season": [2024], "playerId": [1], "team": ["BUF"]}).to_pickle(
        local_db / "draft_results.pkl"
    )
    empty = pd.DataFrame()
    for name in ["nfl_teams", "players", "draft_order", "draft_order_rules"]:
        empty.to_pickle(local_db / f"{name}.pkl")

    try:
        full = load_data(year=None)
        assert get_active_season(full.games, full.draft_results, full.draft_order_rules) == 2024

        # 1. The future season must be retrievable on its own.
        future = load_data(year=2025)
        assert not future.games.empty
        assert set(future.games["season"].unique()) == {2025}
        assert not future.standings.empty
        assert set(future.standings["season"].unique()) == {2025}

        # 2. ...and must appear in the unfiltered full-history bundle.
        assert 2025 in set(full.games["season"].unique())
        assert 2025 in set(full.standings["season"].unique())
        assert len(full.games) == 3  # nothing dropped on the floor

        # 3. The active season is still served from the active bucket.
        assert cs.get_domain(cs.DOMAIN_ACTIVE)["season"] == 2024
        assert set(load_data(year=2024).games["season"].unique()) == {2024}
    finally:
        cs.clear_data_cache()


def test_check_remote_signals_clears_only_domains_with_newer_signal(mock_firestore, monkeypatch):
    import time
    from services.data_service import check_remote_signals
    import services.cache_service as cs

    cs.set_domain(cs.DOMAIN_ACTIVE, "old-active", timestamp=100.0)
    cs.set_domain(cs.DOMAIN_STATIC, "old-static", timestamp=100.0)
    monkeypatch.setattr(cs, "_LAST_REMOTE_CHECK", 0)

    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {"active_updated": 200.0, "static_updated": 50.0}
    mock_firestore.collection.return_value.document.return_value.get.return_value = mock_doc

    check_remote_signals(use_local=False)

    assert cs.get_domain(cs.DOMAIN_ACTIVE) is None       # 200 > 100 -> cleared
    assert cs.get_domain(cs.DOMAIN_STATIC) == "old-static"  # 50 < 100 -> untouched
    cs.clear_data_cache()  # cleanup


def test_get_preseason_predictions_active_season_is_cached(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()  # resolves the active season
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(active_season)
        data_service.get_preseason_predictions(active_season)  # second call
        preseason_calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "preseason_predictions"]
        assert len(preseason_calls) == 1  # only the first call actually fetched
    cs.clear_data_cache()


def test_get_preseason_predictions_historical_season_is_cached_separately(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]
    historical_season = active_season - 1

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(historical_season)
        data_service.get_preseason_predictions(historical_season)
        calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "preseason_predictions"]
        assert len(calls) == 1
    cs.clear_data_cache()


def test_get_consensus_projections_shares_the_same_predictions_bucket(monkeypatch):
    """get_preseason_predictions and get_consensus_projections for the same
    season must share one cached fetch pair, not trigger two independent
    bucket entries."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(active_season)
        data_service.get_consensus_projections(active_season)
        calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] in ("preseason_predictions", "consensus_projections")]
        assert len(calls) == 2  # one fetch per collection, shared across both callers
    cs.clear_data_cache()


def test_predictions_domain_for_routes_by_active_season(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    assert data_service._predictions_domain_for(active_season) == cs.DOMAIN_PREDICTIONS_ACTIVE
    assert data_service._predictions_domain_for(active_season - 1) == cs.DOMAIN_PREDICTIONS_HISTORICAL
    cs.clear_data_cache()


def test_predict_season_write_is_picked_up_by_a_separate_load(monkeypatch):
    """Regression test for the cross-process staleness trap: a write from
    predict_season.py's process (a raw Firestore write + a scoped signal,
    never an in-process cache call) must be visible to a completely
    separate load_data()/prediction read within one remote-check cycle."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]
    data_service.get_preseason_predictions(active_season)  # warm the predictions cache
    assert cs.get_domain(cs.DOMAIN_PREDICTIONS_ACTIVE) is not None

    # Simulate predict_season.py's out-of-process signal.
    from unittest.mock import patch, MagicMock
    with patch("services.db_service.get_db") as mock_get_db:
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"predictions_active_updated": 9999999999.0}
        mock_get_db.return_value.collection.return_value.document.return_value.get.return_value = mock_doc
        cs._LAST_REMOTE_CHECK = 0  # force the next check to actually poll
        data_service.check_remote_signals(use_local=False)

    assert cs.get_domain(cs.DOMAIN_PREDICTIONS_ACTIVE) is None  # cleared by the remote signal
    cs.clear_data_cache()


def test_historical_bucket_does_not_refetch_purely_because_an_hour_passed(monkeypatch):
    """Regression: per the design spec, DOMAIN_HISTORICAL is 'fetched once,
    then effectively permanent ... not on any routine cadence' -- a warm
    bucket must survive well past the old 1-hour TTL with zero additional
    Firestore/pkl fetches."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()  # warm the historical bucket
    assert cs.get_domain(cs.DOMAIN_HISTORICAL) is not None

    # Simulate time well past the old TTL -- no signal fired, nothing changed.
    cs._DOMAIN_TIMESTAMPS[cs.DOMAIN_HISTORICAL] -= 10 * cs._CACHE_TTL_SECONDS

    with patch("services.data_service.get_collection_df") as mock_fetch:
        data_service._get_historical_bucket()
        mock_fetch.assert_not_called()
    cs.clear_data_cache()


def test_static_bucket_does_not_refetch_purely_because_an_hour_passed(monkeypatch):
    """Same regression as above, for DOMAIN_STATIC."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()  # warm the static bucket
    assert cs.get_domain(cs.DOMAIN_STATIC) is not None

    cs._DOMAIN_TIMESTAMPS[cs.DOMAIN_STATIC] -= 10 * cs._CACHE_TTL_SECONDS

    with patch("services.data_service.get_collection_df") as mock_fetch:
        data_service._get_static_bucket()
        mock_fetch.assert_not_called()
    cs.clear_data_cache()
