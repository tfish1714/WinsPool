"""services/db_service.py::record_player_activity() -- lean last_active write.

Unlike update_player_profile(), it must not clear or signal the static cache
(that would invalidate every instance's players/draft bundle each time any
player crosses the activity throttle window)."""
from unittest.mock import MagicMock, patch

import pandas as pd

import services.cache_service as cs
import services.db_service as dbs


def _install_players(rows):
    bucket = {"players": pd.DataFrame(rows)}
    cs.set_domain(cs.DOMAIN_STATIC, bucket)
    return bucket


def teardown_function(_fn):
    cs.clear_domain(cs.DOMAIN_STATIC)


def test_writes_only_last_active_to_firestore():
    fake_db = MagicMock()
    with patch.object(dbs, "get_db", return_value=fake_db):
        dbs.record_player_activity(7, 1234.5)

    fake_db.collection.assert_called_once_with("players")
    fake_db.collection.return_value.document.assert_called_once_with("7")
    fake_db.collection.return_value.document.return_value.update.assert_called_once_with(
        {"last_active": 1234.5}
    )


def test_does_not_clear_or_signal_any_cache():
    with patch.object(dbs, "get_db", return_value=MagicMock()), \
         patch.object(dbs, "clear_data_cache") as clear, \
         patch.object(dbs, "signal_data_update") as signal:
        dbs.record_player_activity(7, 1.0)

    clear.assert_not_called()
    signal.assert_not_called()


def test_patches_cached_players_frame_in_place():
    bucket = _install_players([{"playerId": 7, "fullName": "A"}, {"playerId": 8, "fullName": "B"}])
    with patch.object(dbs, "get_db", return_value=MagicMock()):
        dbs.record_player_activity(7, 99.0)

    df = bucket["players"]
    assert df.loc[df["playerId"] == 7, "last_active"].iloc[0] == 99.0
    assert pd.isna(df.loc[df["playerId"] == 8, "last_active"].iloc[0])


def test_unknown_player_is_a_noop_for_the_cache():
    bucket = _install_players([{"playerId": 7, "fullName": "A"}])
    with patch.object(dbs, "get_db", return_value=MagicMock()):
        dbs.record_player_activity(999, 5.0)

    assert "last_active" not in bucket["players"].columns


def test_local_mode_skips_firestore_but_still_patches_memory():
    bucket = _install_players([{"playerId": 7, "fullName": "A"}])
    with patch.object(dbs, "get_db", return_value=None):
        dbs.record_player_activity(7, 42.0)

    assert bucket["players"].at[0, "last_active"] == 42.0


def test_no_cached_bucket_is_fine():
    cs.clear_domain(cs.DOMAIN_STATIC)
    with patch.object(dbs, "get_db", return_value=MagicMock()):
        dbs.record_player_activity(7, 1.0)  # must not raise
