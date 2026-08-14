"""tests/test_walk_forward_validate.py -- Unit tests for scripts/walk_forward_validate.py."""

import numpy as np
import pandas as pd
import pytest
from importlib.util import find_spec

TF_AVAILABLE = find_spec("tensorflow") is not None
XGB_AVAILABLE = find_spec("xgboost") is not None
SKLEARN_AVAILABLE = find_spec("sklearn") is not None


@pytest.fixture(scope="module")
def tiny_feature_table():
    """20-row, 2-season synthetic feature table -- fast real training for all 3 model types.

    Mirrors the fixture shape used in tests/test_lr_prediction.py and
    tests/test_xgb_prediction.py: interleaved wins/losses so every split
    segment contains both classes.
    """
    from services.nn_feature_engine import FEATURE_COLUMNS

    rng = np.random.default_rng(42)
    n = len(FEATURE_COLUMNS)
    df = pd.DataFrame(rng.standard_normal((20, n)), columns=FEATURE_COLUMNS)
    df["home_win"] = [1.0 if i % 2 == 0 else 0.0 for i in range(20)]
    df["season"] = [2020] * 10 + [2021] * 10
    df["week"] = list(range(1, 11)) * 2
    nfl_teams = ["NE", "BUF", "MIA", "NYJ", "BAL", "CLE", "PIT", "CIN",
                 "KC", "LAC", "DEN", "LV", "DAL", "PHI", "NYG", "WAS",
                 "GB", "MIN", "CHI", "DET"]
    df["home_team"] = nfl_teams
    df["away_team"] = nfl_teams[::-1]
    return df


@pytest.fixture
def trained_lr_svc(tiny_feature_table):
    from services.lr_prediction_service import LRPredictionService
    svc = LRPredictionService()
    svc.train(tiny_feature_table)
    return svc


@pytest.mark.skipif(not XGB_AVAILABLE or not SKLEARN_AVAILABLE, reason="xgboost/sklearn not installed")
class TestFoldArtifactRoundTrip:
    def test_save_then_load_preserves_predictions(self, tmp_path, tiny_feature_table, trained_lr_svc):
        """Saving and reloading a fold's LR model must produce identical predictions.

        LR is the cheapest of the three real model types to round-trip for real
        (no TF import cost), so it stands in for the save/load contract shared
        by all three artifact types.
        """
        from scripts.walk_forward_validate import _save_fold_artifacts, _load_fold_artifacts
        from services.xgb_prediction_service import XGBPredictionService
        from services.nn_feature_engine import FEATURE_COLUMNS

        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)

        row = tiny_feature_table.iloc[[0]]
        features = {c: float(row[c].iloc[0]) for c in FEATURE_COLUMNS}
        expected_lr_pred = trained_lr_svc.predict_game(features)
        expected_xgb_pred = xgb_svc.predict_game(features)

        # NN save/load requires a real Keras model; skip it here and cover it
        # in test_nn_round_trip_uses_correct_scaler_filename below instead.
        _save_fold_artifacts(tmp_path, 2021, nn_svc=None, xgb_svc=xgb_svc, lr_svc=trained_lr_svc,
                              skip_nn=True)

        assert (tmp_path / "xgb_2021.json").exists()
        assert (tmp_path / "xgb_2021_scaler.pkl").exists()
        assert (tmp_path / "lr_2021.pkl").exists()
        assert (tmp_path / "lr_2021_scaler.pkl").exists()

        _, loaded_xgb, loaded_lr = _load_fold_artifacts(tmp_path, 2021, load_nn=False)

        assert loaded_lr.predict_game(features) == pytest.approx(expected_lr_pred)
        assert loaded_xgb.predict_game(features) == pytest.approx(expected_xgb_pred)

    def test_fold_artifacts_exist_false_when_missing(self, tmp_path):
        from scripts.walk_forward_validate import _fold_artifacts_exist
        assert _fold_artifacts_exist(tmp_path, 2021) is False

    def test_fold_artifacts_exist_true_after_save(self, tmp_path, tiny_feature_table, trained_lr_svc):
        from scripts.walk_forward_validate import _save_fold_artifacts, _fold_artifacts_exist
        from services.xgb_prediction_service import XGBPredictionService

        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)

        assert _fold_artifacts_exist(tmp_path, 2022) is False
        _save_fold_artifacts(tmp_path, 2022, nn_svc=None, xgb_svc=xgb_svc, lr_svc=trained_lr_svc,
                              skip_nn=True)
        # With skip_nn=True the NN files are absent, so existence must stay False
        # until the NN artifact is present too -- fold artifacts are all-or-nothing.
        assert _fold_artifacts_exist(tmp_path, 2022) is False


@pytest.mark.skipif(not TF_AVAILABLE or not SKLEARN_AVAILABLE, reason="tensorflow/sklearn not installed")
class TestNNArtifactRoundTrip:
    def test_nn_round_trip_uses_correct_scaler_filename(self, tmp_path, tiny_feature_table):
        """Regression guard for the NNPredictionService.save_model() scaler-naming bug --
        two folds' NN scalers must never collide on disk."""
        from services.nn_prediction_service import NNPredictionService
        from scripts.walk_forward_validate import _save_fold_artifacts, _load_fold_artifacts
        from services.nn_feature_engine import FEATURE_COLUMNS

        svc_2021 = NNPredictionService()
        svc_2021.train(tiny_feature_table)
        svc_2022 = NNPredictionService()
        svc_2022.train(tiny_feature_table)

        _save_fold_artifacts(tmp_path, 2021, nn_svc=svc_2021, xgb_svc=None, lr_svc=None, skip_xgb=True, skip_lr=True)
        _save_fold_artifacts(tmp_path, 2022, nn_svc=svc_2022, xgb_svc=None, lr_svc=None, skip_xgb=True, skip_lr=True)

        assert (tmp_path / "nn_2021_scaler.pkl").exists()
        assert (tmp_path / "nn_2022_scaler.pkl").exists()

        loaded_2021, _, _ = _load_fold_artifacts(tmp_path, 2021, load_xgb=False, load_lr=False)

        row = tiny_feature_table.iloc[[0]]
        features = {c: float(row[c].iloc[0]) for c in FEATURE_COLUMNS}
        assert loaded_2021.predict_game(features) == pytest.approx(svc_2021.predict_game(features), abs=1e-4)


@pytest.mark.skipif(not TF_AVAILABLE or not SKLEARN_AVAILABLE, reason="tensorflow/sklearn not installed")
class TestNNPermutationImportance:
    def test_returns_one_row_per_feature_ranked(self, tiny_feature_table):
        from services.nn_prediction_service import NNPredictionService
        from services.nn_feature_engine import FEATURE_COLUMNS
        from scripts.walk_forward_validate import _nn_permutation_importance

        svc = NNPredictionService()
        svc.train(tiny_feature_table)
        _, val_df, _ = NNPredictionService._split_data(tiny_feature_table)

        result = _nn_permutation_importance(svc, val_df)

        assert set(result["feature"]) == set(FEATURE_COLUMNS)
        assert len(result) == len(FEATURE_COLUMNS)
        # Ranks are a contiguous 1..N sequence with no ties collapsed away
        assert sorted(result["importance_rank"].tolist()) == list(range(1, len(FEATURE_COLUMNS) + 1))
        # Sorted descending by importance
        assert list(result["importance"]) == sorted(result["importance"], reverse=True)


@pytest.mark.skipif(not XGB_AVAILABLE or not TF_AVAILABLE or not SKLEARN_AVAILABLE,
                     reason="tensorflow/xgboost/sklearn not installed")
class TestCollectFeatureImportance:
    def test_combines_all_three_models_with_common_schema(self, tiny_feature_table, trained_lr_svc):
        from services.nn_prediction_service import NNPredictionService
        from services.xgb_prediction_service import XGBPredictionService
        from scripts.walk_forward_validate import _collect_feature_importance

        nn_svc = NNPredictionService()
        nn_svc.train(tiny_feature_table)
        xgb_svc = XGBPredictionService()
        xgb_svc.train(tiny_feature_table)
        _, val_df, _ = NNPredictionService._split_data(tiny_feature_table)

        result = _collect_feature_importance(2021, nn_svc, xgb_svc, trained_lr_svc, val_df)

        assert set(result.columns) == {"season", "model", "feature", "importance_rank", "importance_value"}
        assert set(result["model"]) == {"nn", "xgb", "lr"}
        assert (result["season"] == 2021).all()
        # Every model contributes a rank-1 row (its top feature)
        for model in ("nn", "xgb", "lr"):
            assert 1 in result[result["model"] == model]["importance_rank"].values


class TestRunFold:
    def test_assembles_rows_with_correct_errors(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "_get_or_train_fold_models",
                             lambda *a, **k: ("fake_nn", "fake_xgb", "fake_lr"))
        monkeypatch.setattr(wfv, "_project_fold_season",
                             lambda *a, **k: {"BUF": 11.0, "KC": 9.5, "NE": 6.0})
        monkeypatch.setattr(wfv, "_actual_wins",
                             lambda *a, **k: {"BUF": 13.0, "KC": 11.0, "NE": 4.0})
        monkeypatch.setattr(wfv, "_consensus_wins",
                             lambda *a, **k: {"BUF": 12.0, "KC": 10.5})  # NE missing on purpose
        monkeypatch.setattr(wfv, "build_master_feature_table", lambda **k: pd.DataFrame())
        monkeypatch.setattr(wfv, "_collect_feature_importance",
                             lambda *a, **k: pd.DataFrame([{"season": 2021, "model": "xgb",
                                                             "feature": "elo_diff",
                                                             "importance_rank": 1,
                                                             "importance_value": 0.5}]))

        result = wfv.run_fold(2021, tmp_path)
        rows = {r["team"]: r for r in result["rows"]}

        assert rows["BUF"]["model_abs_err"] == pytest.approx(2.0)
        assert rows["BUF"]["consensus_abs_err"] == pytest.approx(1.0)
        assert rows["KC"]["model_abs_err"] == pytest.approx(1.5)
        assert rows["NE"]["consensus_wins"] is None
        assert rows["NE"]["consensus_abs_err"] is None
        assert rows["NE"]["model_abs_err"] == pytest.approx(2.0)
        assert len(result["importance"]) == 1

    def test_skips_teams_with_no_model_projection(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "_get_or_train_fold_models", lambda *a, **k: (None, None, None))
        monkeypatch.setattr(wfv, "_project_fold_season", lambda *a, **k: {"BUF": 11.0})
        monkeypatch.setattr(wfv, "_actual_wins", lambda *a, **k: {"BUF": 13.0, "KC": 11.0})
        monkeypatch.setattr(wfv, "_consensus_wins", lambda *a, **k: {})
        monkeypatch.setattr(wfv, "build_master_feature_table", lambda **k: pd.DataFrame())
        monkeypatch.setattr(wfv, "_collect_feature_importance", lambda *a, **k: pd.DataFrame())

        result = wfv.run_fold(2021, tmp_path)
        teams = {r["team"] for r in result["rows"]}
        assert teams == {"BUF"}
