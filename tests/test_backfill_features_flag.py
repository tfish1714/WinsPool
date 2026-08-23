"""Integration test: backfill --features flag calls write_prediction_features."""
import sys
import pathlib
import pandas as pd
import numpy as np
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Pre-import the module at collection time so patches target the correct object.
import scripts.backfill_schedule_predictions as bsp  # noqa: E402


def _make_mocks(n_features: int):
    """Return (nn, xgb_svc, lr) mock service objects."""
    nn = MagicMock()
    nn.loaded_version = "v10"
    nn.scaler.transform.side_effect = lambda x: x
    nn.model.predict.return_value = np.array([[0.6], [0.55]])

    xgb_svc = MagicMock()
    xgb_svc.loaded_version = "v4"
    xgb_svc.scaler.transform.side_effect = lambda x: x
    xgb_svc.model.predict_proba.return_value = np.array([[0.4, 0.6], [0.45, 0.55]])

    lr = MagicMock()
    lr.loaded_version = "v2"
    lr.scaler.transform.side_effect = lambda x: x
    lr.model.predict_proba.return_value = np.array([[0.4, 0.6], [0.45, 0.55]])
    lr.model.coef_ = np.zeros((1, n_features))

    return nn, xgb_svc, lr


def _make_fake_ft(feature_columns):
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {col: rng.normal(size=2) for col in feature_columns}
        | {
            "season": [2025, 2025],
            "week": [8, 9],
            "home_team": ["KC", "BUF"],
            "away_team": ["SF", "MIA"],
            "home_win": [1, 0],
        }
    )


def test_features_flag_calls_write_prediction_features(tmp_path, monkeypatch):
    """When --features is passed, write_prediction_features is called once per season."""
    from services.nn_feature_engine import FEATURE_COLUMNS

    # main()'s local-file branch writes directly to a hardcoded relative
    # ".local_db" path (bypassing the mocked write_game_predictions) -- chdir
    # into a scratch dir so this test can't clobber the real project's
    # .local_db/game_predictions_2025.json.
    monkeypatch.chdir(tmp_path)

    nn, xgb_svc, lr = _make_mocks(len(FEATURE_COLUMNS))
    fake_ft = _make_fake_ft(FEATURE_COLUMNS)

    # ft_lookup has one entry for 2025 so predictions_map is non-empty.
    fake_ft_lookup = {
        (2025, 8, "KC", "SF"): {"pred_prob": 0.6, "pred_winner": "KC", "pred_su_conf": 60.0}
    }

    fake_engine = MagicMock()
    fake_engine.simulate_season.return_value = {"game_probs": {}, "team_stats": {}}

    with patch.object(bsp, "NNPredictionService", return_value=nn), \
         patch.object(bsp, "XGBPredictionService", return_value=xgb_svc), \
         patch.object(bsp, "LRPredictionService", return_value=lr), \
         patch.object(bsp, "build_master_feature_table", return_value=fake_ft), \
         patch.object(bsp, "build_ensemble_lookup", return_value=fake_ft_lookup), \
         patch.object(bsp, "NNProjectionEngine", return_value=fake_engine), \
         patch("services.nn_feature_engine._load_schedule", return_value=pd.DataFrame()), \
         patch.object(bsp, "get_game_predictions", return_value={}), \
         patch.object(bsp, "write_game_predictions"), \
         patch.object(bsp, "write_prediction_features") as mock_wpf, \
         patch.object(bsp, "compute_feature_audit",
                      return_value=[{"game_key": "W08_KC_SF", "features": {}}]):

        sys.argv = [
            "backfill_schedule_predictions.py",
            "--seasons", "2025", "2025",
            "--features",
        ]
        try:
            bsp.main()
        except SystemExit:
            pass

    assert mock_wpf.called, "write_prediction_features should be called when --features is passed"


def test_no_features_flag_skips_write(tmp_path, monkeypatch):
    """Without --features, write_prediction_features is NOT called."""
    from services.nn_feature_engine import FEATURE_COLUMNS

    # See test_features_flag_calls_write_prediction_features for why this chdir
    # is required -- main()'s local-file write isn't covered by the mocks below.
    monkeypatch.chdir(tmp_path)

    nn, xgb_svc, lr = _make_mocks(len(FEATURE_COLUMNS))
    fake_ft = _make_fake_ft(FEATURE_COLUMNS)

    fake_engine = MagicMock()
    fake_engine.simulate_season.return_value = {"game_probs": {}, "team_stats": {}}

    with patch.object(bsp, "NNPredictionService", return_value=nn), \
         patch.object(bsp, "XGBPredictionService", return_value=xgb_svc), \
         patch.object(bsp, "LRPredictionService", return_value=lr), \
         patch.object(bsp, "build_master_feature_table", return_value=fake_ft), \
         patch.object(bsp, "build_ensemble_lookup", return_value={}), \
         patch.object(bsp, "NNProjectionEngine", return_value=fake_engine), \
         patch("services.nn_feature_engine._load_schedule", return_value=pd.DataFrame()), \
         patch.object(bsp, "get_game_predictions", return_value={}), \
         patch.object(bsp, "write_game_predictions"), \
         patch.object(bsp, "write_prediction_features") as mock_wpf:

        # No --features flag
        sys.argv = ["backfill_schedule_predictions.py", "--seasons", "2025", "2025"]
        try:
            bsp.main()
        except SystemExit:
            pass

    assert not mock_wpf.called, (
        "write_prediction_features should NOT be called without --features"
    )
