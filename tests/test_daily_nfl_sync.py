"""Tests for scripts/daily_nfl_sync.py's batch_upload() diff-before-write and
sync_nfl_data()'s active-season scoping (Task 8 of the cache mutability
redesign)."""
from unittest.mock import MagicMock
import pandas as pd

from scripts.daily_nfl_sync import batch_upload, compute_standings


def test_batch_upload_without_diff_writes_every_row():
    db = MagicMock()
    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 5}])
    written = batch_upload(db, "nfl_standings", df)
    assert written == 1
    db.batch.return_value.set.assert_called_once()


def test_batch_upload_with_diff_skips_unchanged_rows():
    db = MagicMock()
    existing_snap = MagicMock()
    existing_snap.exists = True
    existing_snap.id = "2026_KC"
    existing_snap.to_dict.return_value = {"season": 2026, "team": "KC", "wins": 5}
    db.get_all.return_value = [existing_snap]

    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 5}])  # identical to stored
    written = batch_upload(db, "nfl_standings", df, diff_before_write=True)

    assert written == 0
    db.batch.return_value.set.assert_not_called()


def test_batch_upload_with_diff_writes_only_changed_rows():
    db = MagicMock()
    existing_snap = MagicMock()
    existing_snap.exists = True
    existing_snap.id = "2026_KC"
    existing_snap.to_dict.return_value = {"season": 2026, "team": "KC", "wins": 5}
    db.get_all.return_value = [existing_snap]

    df = pd.DataFrame([{"season": 2026, "team": "KC", "wins": 6}])  # wins changed 5 -> 6
    written = batch_upload(db, "nfl_standings", df, diff_before_write=True)

    assert written == 1
    db.batch.return_value.set.assert_called_once()


def test_batch_upload_with_diff_always_writes_rows_with_no_derivable_id():
    """A row batch_upload can't compute a stable doc_id for (no id_col, no
    season+team, no game_id) can't be diffed against anything -- always write it."""
    db = MagicMock()
    db.get_all.return_value = []
    df = pd.DataFrame([{"some_other_field": "x"}])
    written = batch_upload(db, "misc", df, diff_before_write=True)
    assert written == 1


def test_compute_standings_returns_empty_shaped_frame_when_no_completed_games():
    """Regression: sync_live_scores.py's Task 9 scoping (active season only,
    no prior-season safety net) means this can now genuinely receive zero
    completed REG games -- e.g. every 5-minute run during the preseason
    window, before that season's Week 1 has finished. pd.DataFrame([]) has
    no columns at all, so a bare .sort_values(["season", "team"]) would
    raise KeyError instead of returning an empty frame every caller's
    existing `if df.empty` handling already expects."""
    games = pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": None, "home_score": None, "away_score": None},
        {"season": 2026, "game_type": "PRE", "home_team": "KC", "away_team": "SF",
         "result": 3.0, "home_score": 20.0, "away_score": 17.0},
    ])
    result = compute_standings(games)
    assert result.empty
    assert list(result.columns) == ["season", "team", "wins", "losses", "ties",
                                     "scored", "allowed", "net", "pct"]


def test_sync_nfl_data_defaults_to_active_season_only(monkeypatch):
    from scripts import daily_nfl_sync
    fake_games = pd.DataFrame([
        {"season": 2024, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    captured = {}

    def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
        captured[name] = df
        return 0
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", fake_batch_upload)

    daily_nfl_sync.sync_nfl_data()

    assert set(captured["nfl_games"]["season"].unique()) == {2026}


def test_sync_nfl_data_explicit_seasons_forces_full_range(monkeypatch):
    from scripts import daily_nfl_sync
    fake_games = pd.DataFrame([
        {"season": 2024, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    captured = {}

    def fake_batch_upload(db, name, df, id_col=None, diff_before_write=False):
        captured[name] = df
        return 0
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", fake_batch_upload)

    daily_nfl_sync.sync_nfl_data(seasons=(2024, 2026))

    assert set(captured["nfl_games"]["season"].unique()) == {2024, 2026}


def test_sync_nfl_data_forces_use_local_data_false(monkeypatch):
    """See CLAUDE.md's 'any script that writes to Firestore must force
    USE_LOCAL_DATA=False' gotcha -- signal_data_update()'s get_db() call
    silently no-ops otherwise."""
    import os
    from scripts import daily_nfl_sync
    monkeypatch.setenv("USE_LOCAL_DATA", "true")
    fake_games = pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 0)

    daily_nfl_sync.sync_nfl_data()

    assert os.environ["USE_LOCAL_DATA"] == "False"


def test_sync_nfl_data_signals_active_domain_by_default(monkeypatch):
    from scripts import daily_nfl_sync
    from services.cache_service import DOMAIN_ACTIVE
    fake_games = pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 1)  # something written

    from unittest.mock import patch
    with patch("services.db_service.signal_data_update") as mock_signal:
        daily_nfl_sync.sync_nfl_data()

    mock_signal.assert_called_once_with(DOMAIN_ACTIVE)


def test_sync_nfl_data_no_writes_skips_signal(monkeypatch):
    from scripts import daily_nfl_sync
    fake_games = pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 0)  # nothing changed

    from unittest.mock import patch
    with patch("services.db_service.signal_data_update") as mock_signal:
        daily_nfl_sync.sync_nfl_data()

    mock_signal.assert_not_called()


def test_sync_nfl_data_explicit_historical_range_signals_historical_domain(monkeypatch):
    from scripts import daily_nfl_sync
    from services.cache_service import DOMAIN_HISTORICAL
    fake_games = pd.DataFrame([
        {"season": 2024, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 1)

    from unittest.mock import patch
    with patch("services.db_service.signal_data_update") as mock_signal:
        daily_nfl_sync.sync_nfl_data(seasons=(2020, 2024))  # entirely before the max season (2026)

    mock_signal.assert_called_once_with(DOMAIN_HISTORICAL)


def test_sync_nfl_data_explicit_active_only_range_signals_active_domain_only(monkeypatch):
    """Regression: a --seasons range that is exactly the active season alone
    must signal only DOMAIN_ACTIVE, not both (matches backfill_schedule_
    predictions.py's Task 6 boundary fix)."""
    from scripts import daily_nfl_sync
    from services.cache_service import DOMAIN_ACTIVE
    fake_games = pd.DataFrame([
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 1)

    from unittest.mock import patch
    with patch("services.db_service.signal_data_update") as mock_signal:
        daily_nfl_sync.sync_nfl_data(seasons=(2026, 2026))

    mock_signal.assert_called_once_with(DOMAIN_ACTIVE)


def test_sync_nfl_data_explicit_range_spanning_active_signals_both_domains(monkeypatch):
    """Regression for the boundary bug: a --seasons range that touches the
    active season but also extends into historical seasons must signal BOTH
    domains, not silently under-signal historical."""
    from scripts import daily_nfl_sync
    from services.cache_service import DOMAIN_ACTIVE, DOMAIN_HISTORICAL
    fake_games = pd.DataFrame([
        {"season": 2024, "game_type": "REG", "home_team": "KC", "away_team": "SF",
         "result": 3, "home_score": 20, "away_score": 17, "game_id": "g1"},
        {"season": 2026, "game_type": "REG", "home_team": "KC", "away_team": "BUF",
         "result": 3, "home_score": 24, "away_score": 21, "game_id": "g2"},
    ])
    monkeypatch.setattr(daily_nfl_sync, "load_games", lambda: fake_games)
    monkeypatch.setattr(daily_nfl_sync, "initialize_firebase", lambda: MagicMock())
    monkeypatch.setattr(daily_nfl_sync, "batch_upload", lambda *a, **k: 1)

    from unittest.mock import patch
    with patch("services.db_service.signal_data_update") as mock_signal:
        daily_nfl_sync.sync_nfl_data(seasons=(2013, 2026))  # spans historical + active (max season 2026)

    assert mock_signal.call_count == 2
    called_domains = {c.args[0] for c in mock_signal.call_args_list}
    assert called_domains == {DOMAIN_ACTIVE, DOMAIN_HISTORICAL}
