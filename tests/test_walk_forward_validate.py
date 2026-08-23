"""tests/test_walk_forward_validate.py -- Unit tests for scripts/walk_forward_validate.py."""

import sys

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

    def test_fold_artifacts_exist_false_when_partially_saved(self, tmp_path, tiny_feature_table, trained_lr_svc):
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
                             lambda *a, **k: ({"BUF": 11.0, "KC": 9.5, "NE": 6.0}, False))
        monkeypatch.setattr(wfv, "_actual_wins",
                             lambda *a, **k: {"BUF": 13.0, "KC": 11.0, "NE": 4.0})
        monkeypatch.setattr(wfv, "_consensus_wins",
                             lambda *a, **k: {"BUF": 12.0, "KC": 10.5})  # NE missing on purpose
        monkeypatch.setattr(wfv, "build_master_feature_table",
                             lambda **k: pd.DataFrame({"season": [2020], "week": [1]}))
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
        assert rows["BUF"]["used_preseason_profiles"] is False
        assert len(result["importance"]) == 1

    def test_skips_teams_with_no_model_projection(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "_get_or_train_fold_models", lambda *a, **k: (None, None, None))
        monkeypatch.setattr(wfv, "_project_fold_season", lambda *a, **k: ({"BUF": 11.0}, True))
        monkeypatch.setattr(wfv, "_actual_wins", lambda *a, **k: {"BUF": 13.0, "KC": 11.0})
        monkeypatch.setattr(wfv, "_consensus_wins", lambda *a, **k: {})
        monkeypatch.setattr(wfv, "build_master_feature_table",
                             lambda **k: pd.DataFrame({"season": [2020], "week": [1]}))
        monkeypatch.setattr(wfv, "_collect_feature_importance", lambda *a, **k: pd.DataFrame())

        result = wfv.run_fold(2021, tmp_path)
        teams = {r["team"] for r in result["rows"]}
        assert teams == {"BUF"}
        rows = {r["team"]: r for r in result["rows"]}
        assert rows["BUF"]["used_preseason_profiles"] is True


class TestPrintSummary:
    def test_prints_per_season_and_overall_mae(self, capsys):
        import pandas as pd
        from scripts.walk_forward_validate import _print_summary

        df = pd.DataFrame([
            {"season": 2021, "team": "BUF", "actual_wins": 13, "model_wins": 11,
             "model_abs_err": 2.0, "consensus_wins": 12, "consensus_abs_err": 1.0,
             "used_preseason_profiles": True},
            {"season": 2021, "team": "KC", "actual_wins": 11, "model_wins": 9.5,
             "model_abs_err": 1.5, "consensus_wins": None, "consensus_abs_err": None,
             "used_preseason_profiles": True},
            {"season": 2022, "team": "BUF", "actual_wins": 12, "model_wins": 10,
             "model_abs_err": 2.0, "consensus_wins": 11, "consensus_abs_err": 1.0,
             "used_preseason_profiles": True},
        ])

        _print_summary(df)
        out = capsys.readouterr().out

        assert "2021" in out
        assert "2022" in out
        assert "ALL" in out
        assert "NOTE" not in out  # all rows used the preseason path -- no caveat

    def test_handles_empty_report(self, capsys):
        import pandas as pd
        from scripts.walk_forward_validate import _print_summary

        _print_summary(pd.DataFrame())
        out = capsys.readouterr().out
        assert "No folds completed" in out

    def test_prints_caveat_when_some_folds_skipped_preseason_path(self, capsys):
        import pandas as pd
        from scripts.walk_forward_validate import _print_summary

        df = pd.DataFrame([
            {"season": 2021, "team": "BUF", "actual_wins": 13, "model_wins": 11,
             "model_abs_err": 2.0, "consensus_wins": 12, "consensus_abs_err": 1.0,
             "used_preseason_profiles": False},
            {"season": 2021, "team": "KC", "actual_wins": 11, "model_wins": 9.5,
             "model_abs_err": 1.5, "consensus_wins": None, "consensus_abs_err": None,
             "used_preseason_profiles": False},
            {"season": 2022, "team": "BUF", "actual_wins": 12, "model_wins": 10,
             "model_abs_err": 2.0, "consensus_wins": 11, "consensus_abs_err": 1.0,
             "used_preseason_profiles": True},
        ])

        _print_summary(df)
        out = capsys.readouterr().out

        assert "NOTE" in out
        assert "2 of 3" in out


class TestMainLoop:
    def test_one_bad_fold_does_not_abort_the_run(self, monkeypatch, tmp_path):
        """A fold that raises during run_fold is logged and skipped; the
        remaining folds still produce a report."""
        import scripts.walk_forward_validate as wfv
        import pandas as pd

        monkeypatch.setattr(wfv, "ARTIFACTS_DIR", tmp_path / "walkforward")
        monkeypatch.setattr(wfv, "REPORTS_DIR", tmp_path / "reports")
        # Preflight check runs before the fold loop -- give it something non-empty
        # so it doesn't short-circuit before fake_run_fold gets exercised.
        monkeypatch.setattr(wfv, "_actual_wins", lambda *a, **k: {"BUF": 12.0})
        monkeypatch.setattr(wfv, "_consensus_wins", lambda *a, **k: {"BUF": 11.5})

        def fake_run_fold(fold_year, artifacts_dir, force=False):
            if fold_year == 2022:
                raise RuntimeError("simulated feature table build failure")
            return {
                "rows": [{"season": fold_year, "team": "BUF", "actual_wins": 12,
                          "model_wins": 11, "model_abs_err": 1.0,
                          "consensus_wins": 11.5, "consensus_abs_err": 0.5,
                          "used_preseason_profiles": True}],
                "importance": pd.DataFrame([{"season": fold_year, "model": "xgb",
                                              "feature": "elo_diff",
                                              "importance_rank": 1, "importance_value": 0.5}]),
            }

        monkeypatch.setattr(wfv, "run_fold", fake_run_fold)
        monkeypatch.setattr(sys, "argv", ["walk_forward_validate.py", "--seasons", "2021", "2022"])

        wfv.main()

        report = pd.read_csv(wfv.REPORTS_DIR / "walk_forward_validation.csv")
        assert set(report["season"]) == {2021}  # 2022 skipped, no crash

        importance = pd.read_csv(wfv.REPORTS_DIR / "walk_forward_feature_importance.csv")
        assert set(importance["season"]) == {2021}

    def test_preflight_aborts_before_fold_loop_when_no_data_found(self, monkeypatch, tmp_path):
        """If _actual_wins/_consensus_wins both come back empty for the first fold
        year -- e.g. USE_LOCAL_DATA misconfigured with no Firestore reachable --
        main() must exit before doing any real fold work, not after hours of
        training on every fold."""
        import scripts.walk_forward_validate as wfv

        monkeypatch.setattr(wfv, "ARTIFACTS_DIR", tmp_path / "walkforward")
        monkeypatch.setattr(wfv, "REPORTS_DIR", tmp_path / "reports")
        monkeypatch.setattr(wfv, "_actual_wins", lambda *a, **k: {})
        monkeypatch.setattr(wfv, "_consensus_wins", lambda *a, **k: {})

        def fail_if_called(*a, **k):
            raise AssertionError("run_fold must not be called when preflight fails")

        monkeypatch.setattr(wfv, "run_fold", fail_if_called)
        monkeypatch.setattr(sys, "argv", ["walk_forward_validate.py", "--seasons", "2021", "2022"])

        with pytest.raises(SystemExit):
            wfv.main()


class TestProjectFoldInSeason:
    """_project_fold_in_season() walks a fold season week-by-week, calling
    simulate_season() with only strictly-prior-week results known at each
    step -- unlike _project_fold_season(), which always simulates blind
    (completed_results=None) and never exercises the in-season blending
    path cache_builder.py's daily job actually uses."""

    def _games_df(self):
        return pd.DataFrame([
            {"season": 2024, "week": 1, "game_type": "REG", "home_team": "KC",
             "away_team": "BUF", "result": 3.0},
            {"season": 2024, "week": 2, "game_type": "REG", "home_team": "KC",
             "away_team": "DEN", "result": -7.0},
            {"season": 2024, "week": 3, "game_type": "REG", "home_team": "KC",
             "away_team": "LAC", "result": 10.0},
        ])

    def test_completed_results_only_includes_strictly_prior_weeks(self, monkeypatch):
        """The whole point of this function: at week 3, week 1 and 2's real
        results must be known, but week 3's own result must NOT be -- that's
        what makes each week's score genuinely out-of-sample."""
        import scripts.walk_forward_validate as wfv

        captured_completed_results = []

        class FakeEngine:
            def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
                pass

            def initialize(self, fold_year):
                pass

            def simulate_season(self, schedule, n_sims=2000, completed_results=None):
                captured_completed_results.append(dict(completed_results or {}))
                return {"game_probs": {}}

        monkeypatch.setattr("services.nn_projection_engine.NNProjectionEngine", FakeEngine)
        monkeypatch.setattr("scripts.daily_nfl_sync.load_games", lambda: self._games_df())

        wfv._project_fold_in_season(2024, "nn", "xgb", "lr", n_sims=2000)

        # One simulate_season() call per week (3 weeks).
        assert len(captured_completed_results) == 3
        assert captured_completed_results[0] == {}  # week 1: nothing known yet
        assert list(captured_completed_results[1].keys()) == ["W01_KC_BUF"]  # week 2: only week 1 known
        assert set(captured_completed_results[2].keys()) == {"W01_KC_BUF", "W02_KC_DEN"}  # week 3: weeks 1-2 known, not week 3 itself

    def test_scores_predictions_against_actual_outcomes(self, monkeypatch):
        import scripts.walk_forward_validate as wfv

        class FakeEngine:
            def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
                pass

            def initialize(self, fold_year):
                pass

            def simulate_season(self, schedule, n_sims=2000, completed_results=None):
                # Predict KC (home) wins every game with high confidence.
                return {"game_probs": {
                    "W01_KC_BUF": {"mean_prob": 0.7, "model_spread": 3.0},
                    "W02_KC_DEN": {"mean_prob": 0.7, "model_spread": 3.0},
                    "W03_KC_LAC": {"mean_prob": 0.7, "model_spread": 3.0},
                }}

        monkeypatch.setattr("services.nn_projection_engine.NNProjectionEngine", FakeEngine)
        monkeypatch.setattr("scripts.daily_nfl_sync.load_games", lambda: self._games_df())

        rows = wfv._project_fold_in_season(2024, "nn", "xgb", "lr", n_sims=2000)

        by_week = {r["week"]: r for r in rows}
        # Week 1: result=3.0 (home win) -- predicted home win -- correct.
        assert by_week[1]["correct"] == 1 and by_week[1]["games"] == 1
        # Week 2: result=-7.0 (away win) -- predicted home win -- wrong.
        assert by_week[2]["correct"] == 0 and by_week[2]["games"] == 1
        # Week 3: result=10.0 (home win) -- predicted home win -- correct.
        assert by_week[3]["correct"] == 1 and by_week[3]["games"] == 1

    def test_skips_games_missing_from_game_probs(self, monkeypatch):
        """A game simulate_season() didn't return a prediction for (e.g. a
        team mismatch) must be skipped, not counted as wrong."""
        import scripts.walk_forward_validate as wfv

        class FakeEngine:
            def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
                pass

            def initialize(self, fold_year):
                pass

            def simulate_season(self, schedule, n_sims=2000, completed_results=None):
                return {"game_probs": {}}  # nothing predicted for any game

        monkeypatch.setattr("services.nn_projection_engine.NNProjectionEngine", FakeEngine)
        monkeypatch.setattr("scripts.daily_nfl_sync.load_games", lambda: self._games_df())

        rows = wfv._project_fold_in_season(2024, "nn", "xgb", "lr", n_sims=2000)
        assert rows == []  # every week had zero scoreable games -- no rows at all

    def test_skips_unplayed_games_without_a_result(self, monkeypatch):
        import scripts.walk_forward_validate as wfv

        games = pd.DataFrame([
            {"season": 2024, "week": 1, "game_type": "REG", "home_team": "KC",
             "away_team": "BUF", "result": 3.0},
            {"season": 2024, "week": 1, "game_type": "REG", "home_team": "SF",
             "away_team": "LAR", "result": None},  # not yet played
        ])

        class FakeEngine:
            def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
                pass

            def initialize(self, fold_year):
                pass

            def simulate_season(self, schedule, n_sims=2000, completed_results=None):
                return {"game_probs": {
                    "W01_KC_BUF": {"mean_prob": 0.7, "model_spread": 3.0},
                    "W01_SF_LAR": {"mean_prob": 0.6, "model_spread": 1.0},
                }}

        monkeypatch.setattr("services.nn_projection_engine.NNProjectionEngine", FakeEngine)
        monkeypatch.setattr("scripts.daily_nfl_sync.load_games", lambda: games)

        rows = wfv._project_fold_in_season(2024, "nn", "xgb", "lr", n_sims=2000)
        assert len(rows) == 1
        assert rows[0]["games"] == 1  # only the played game counted


class TestRunFoldInSeason:
    def test_wires_feature_table_and_fold_models_into_projection(self, monkeypatch, tmp_path):
        import scripts.walk_forward_validate as wfv

        monkeypatch.setattr(wfv, "build_master_feature_table",
                             lambda **k: pd.DataFrame({"season": [2020], "week": [1]}))
        monkeypatch.setattr(wfv, "_get_or_train_fold_models",
                             lambda *a, **k: ("fake_nn", "fake_xgb", "fake_lr"))

        captured = {}

        def fake_project(fold_year, nn_svc, xgb_svc, lr_svc, n_sims=2000):
            captured["args"] = (fold_year, nn_svc, xgb_svc, lr_svc, n_sims)
            return [{"fold_year": fold_year, "week": 1, "games": 10, "correct": 6, "accuracy_pct": 60.0}]

        monkeypatch.setattr(wfv, "_project_fold_in_season", fake_project)

        result = wfv.run_fold_in_season(2024, tmp_path, n_sims=500)

        assert captured["args"] == (2024, "fake_nn", "fake_xgb", "fake_lr", 500)
        assert result["rows"][0]["accuracy_pct"] == 60.0
