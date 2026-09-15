"""Tests for scripts/predict_season.py's Firestore upload + cache-invalidation
signal routing (Task 6 of the cache mutability redesign)."""
from unittest.mock import patch, MagicMock

from scripts.predict_season import _upload_predictions


def _fake_db():
    db = MagicMock()
    db.batch.return_value = MagicMock()
    return db


def test_upload_predictions_signals_active_domain_for_active_season(monkeypatch):
    from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE

    with patch("scripts.predict_season._init_firebase", return_value=_fake_db()), \
         patch("services.data_service._get_active_bucket", return_value={"season": 2026}), \
         patch("services.db_service.signal_data_update") as mock_signal:
        _upload_predictions(2026, [{"team": "KC", "proj_wins": 11.0, "mean_wins": 10.8,
                                     "std_dev": 1.9, "floor": 7.0, "p25": 9.0,
                                     "p75": 12.0, "ceiling": 14.0}])

    mock_signal.assert_called_once_with(DOMAIN_PREDICTIONS_ACTIVE)


def test_upload_predictions_signals_historical_domain_for_past_season(monkeypatch):
    from services.cache_service import DOMAIN_PREDICTIONS_HISTORICAL

    with patch("scripts.predict_season._init_firebase", return_value=_fake_db()), \
         patch("services.data_service._get_active_bucket", return_value={"season": 2026}), \
         patch("services.db_service.signal_data_update") as mock_signal:
        _upload_predictions(2024, [{"team": "KC", "proj_wins": 9.0, "mean_wins": 8.8,
                                     "std_dev": 1.9, "floor": 5.0, "p25": 7.0,
                                     "p75": 10.0, "ceiling": 12.0}])

    mock_signal.assert_called_once_with(DOMAIN_PREDICTIONS_HISTORICAL)


def test_upload_predictions_forces_use_local_data_false(monkeypatch):
    """services.db_service.get_db() (used by signal_data_update()) returns
    None whenever USE_LOCAL_DATA is true, regardless of this script's own
    separate _init_firebase() connection -- _upload_predictions() must force
    it False before signaling, or the signal silently no-ops."""
    import os
    monkeypatch.setenv("USE_LOCAL_DATA", "true")

    with patch("scripts.predict_season._init_firebase", return_value=_fake_db()), \
         patch("services.data_service._get_active_bucket", return_value={"season": 2026}), \
         patch("services.db_service.signal_data_update"):
        _upload_predictions(2026, [{"team": "KC", "proj_wins": 11.0, "mean_wins": 10.8,
                                     "std_dev": 1.9, "floor": 7.0, "p25": 9.0,
                                     "p75": 12.0, "ceiling": 14.0}])

    assert os.environ["USE_LOCAL_DATA"] == "False"
