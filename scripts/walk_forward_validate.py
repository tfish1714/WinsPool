"""scripts/walk_forward_validate.py -- Walk-forward validation harness.

Trains the NN+XGB+LR ensemble on strictly-prior seasons for each of five
expanding-window folds (2021-2025), generates an honest out-of-sample season
projection per fold via the real NNProjectionEngine Monte Carlo path, and
scores it against actual wins and analyst consensus.

Diagnostic only: writes to reports/, never to preseason_predictions, and
never touches model_registry.json / xgb_registry.json / lr_registry.json.

Usage:
    python scripts/walk_forward_validate.py
    python scripts/walk_forward_validate.py --seasons 2021 2025
    python scripts/walk_forward_validate.py --force   # retrain even if cached
"""

import argparse
import pathlib
import pickle
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import pandas as pd

from services.nn_feature_engine import build_master_feature_table, RAWDATA_DIR
from services.nn_prediction_service import NNPredictionService

MODEL_DIR = pathlib.Path(__file__).parent.parent / "models"
ARTIFACTS_DIR = MODEL_DIR / "walkforward"
REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"

FOLD_START_DEFAULT = 2021
FOLD_END_DEFAULT = 2025


def _save_fold_artifacts(artifacts_dir, fold_year, nn_svc, xgb_svc, lr_svc,
                          skip_nn=False, skip_xgb=False, skip_lr=False):
    """Save one fold's trained models directly to disk, bypassing every
    service's registry-integrated save path."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    if not skip_nn:
        import joblib
        nn_path = artifacts_dir / f"nn_{fold_year}.keras"
        nn_svc.model.save(str(nn_path))
        joblib.dump(nn_svc.scaler, str(artifacts_dir / f"nn_{fold_year}_scaler.pkl"))

    if not skip_xgb:
        xgb_path = artifacts_dir / f"xgb_{fold_year}.json"
        xgb_svc.model.save_model(str(xgb_path))
        with open(artifacts_dir / f"xgb_{fold_year}_scaler.pkl", "wb") as f:
            pickle.dump(xgb_svc.scaler, f)

    if not skip_lr:
        with open(artifacts_dir / f"lr_{fold_year}.pkl", "wb") as f:
            pickle.dump(lr_svc.model, f)
        with open(artifacts_dir / f"lr_{fold_year}_scaler.pkl", "wb") as f:
            pickle.dump(lr_svc.scaler, f)


def _fold_artifacts_exist(artifacts_dir, fold_year):
    """True only when all three model types' files are present for this fold --
    fold artifacts are all-or-nothing, never partially resumed."""
    required = [
        f"nn_{fold_year}.keras", f"nn_{fold_year}_scaler.pkl",
        f"xgb_{fold_year}.json", f"xgb_{fold_year}_scaler.pkl",
        f"lr_{fold_year}.pkl", f"lr_{fold_year}_scaler.pkl",
    ]
    return all((artifacts_dir / name).exists() for name in required)


def _load_fold_artifacts(artifacts_dir, fold_year, load_nn=True, load_xgb=True, load_lr=True):
    """Load one fold's models back from disk. Returns (nn_svc, xgb_svc, lr_svc);
    any skipped slot is None."""
    nn_svc = None
    if load_nn:
        nn_svc = NNPredictionService()
        nn_svc.load_model(path=str(artifacts_dir / f"nn_{fold_year}.keras"))

    xgb_svc = None
    if load_xgb:
        import xgboost as xgb
        from services.xgb_prediction_service import XGBPredictionService
        xgb_svc = XGBPredictionService()
        xgb_svc.model = xgb.XGBClassifier()
        xgb_svc.model.load_model(str(artifacts_dir / f"xgb_{fold_year}.json"))
        with open(artifacts_dir / f"xgb_{fold_year}_scaler.pkl", "rb") as f:
            xgb_svc.scaler = pickle.load(f)
        xgb_svc._is_trained = True

    lr_svc = None
    if load_lr:
        from services.lr_prediction_service import LRPredictionService
        lr_svc = LRPredictionService()
        with open(artifacts_dir / f"lr_{fold_year}.pkl", "rb") as f:
            lr_svc.model = pickle.load(f)
        with open(artifacts_dir / f"lr_{fold_year}_scaler.pkl", "rb") as f:
            lr_svc.scaler = pickle.load(f)
        lr_svc._is_trained = True

    return nn_svc, xgb_svc, lr_svc


def _nn_permutation_importance(nn_svc, val_df):
    """Permutation importance: shuffle one feature at a time, measure MAE
    degradation on the fold's own held-out validation split. NN has no
    built-in importance measure the way XGB/LR do, so this is its equivalent."""
    import numpy as np
    from sklearn.metrics import mean_absolute_error
    from services.nn_feature_engine import FEATURE_COLUMNS
    from services.nn_prediction_service import LABEL_COLUMN

    X_val = val_df[FEATURE_COLUMNS].values.astype("float32")
    y_val = val_df[LABEL_COLUMN].values.astype("float32")
    X_scaled = nn_svc.scaler.transform(X_val)

    baseline_pred = nn_svc.model.predict(X_scaled, verbose=0).flatten()
    baseline_mae = mean_absolute_error(y_val, baseline_pred)

    rng = np.random.default_rng(42)
    rows = []
    for i, feature in enumerate(FEATURE_COLUMNS):
        X_shuffled = X_scaled.copy()
        rng.shuffle(X_shuffled[:, i])
        shuffled_pred = nn_svc.model.predict(X_shuffled, verbose=0).flatten()
        shuffled_mae = mean_absolute_error(y_val, shuffled_pred)
        rows.append({"feature": feature, "importance": shuffled_mae - baseline_mae})

    result = pd.DataFrame(rows).sort_values("importance", ascending=False).reset_index(drop=True)
    result["importance_rank"] = result.index + 1
    return result


def _collect_feature_importance(fold_year, nn_svc, xgb_svc, lr_svc, val_df):
    """Combine all three models' feature importance into the report schema:
    season, model, feature, importance_rank, importance_value."""
    from services.nn_feature_engine import FEATURE_COLUMNS

    rows = []

    # top_n defaults to 15 and truncates -- pass the true feature count
    # explicitly so this stays the full, untruncated list even as the
    # feature set grows past 15.
    xgb_imp = xgb_svc.feature_importance(top_n=len(FEATURE_COLUMNS))
    for rank, (_, r) in enumerate(xgb_imp.iterrows(), start=1):
        rows.append({"season": fold_year, "model": "xgb", "feature": r["feature"],
                      "importance_rank": rank, "importance_value": float(r["importance"])})

    lr_imp = lr_svc.feature_importance(top_n=len(FEATURE_COLUMNS))
    for rank, (_, r) in enumerate(lr_imp.iterrows(), start=1):
        rows.append({"season": fold_year, "model": "lr", "feature": r["feature"],
                      "importance_rank": rank, "importance_value": float(r["abs_coef"])})

    nn_imp = _nn_permutation_importance(nn_svc, val_df)
    for _, r in nn_imp.iterrows():
        rows.append({"season": fold_year, "model": "nn", "feature": r["feature"],
                      "importance_rank": int(r["importance_rank"]),
                      "importance_value": float(r["importance"])})

    return pd.DataFrame(rows)


def _get_or_train_fold_models(fold_year, artifacts_dir, feature_table, force=False):
    if not force and _fold_artifacts_exist(artifacts_dir, fold_year):
        logger.info("[%d] Cached fold artifacts found, loading.", fold_year)
        return _load_fold_artifacts(artifacts_dir, fold_year)

    logger.info("[%d] Training fold models on seasons <= %d.", fold_year, fold_year - 1)
    from services.xgb_prediction_service import XGBPredictionService
    from services.lr_prediction_service import LRPredictionService

    nn_svc = NNPredictionService()
    nn_svc.train(feature_table)

    xgb_svc = XGBPredictionService()
    xgb_svc.train(feature_table)

    lr_svc = LRPredictionService()
    lr_svc.train(feature_table)

    _save_fold_artifacts(artifacts_dir, fold_year, nn_svc, xgb_svc, lr_svc)
    return nn_svc, xgb_svc, lr_svc


def _project_fold_season(fold_year, nn_svc, xgb_svc, lr_svc):
    """Run the fold's Monte Carlo season projection.

    Returns (model_wins, used_preseason_profiles). NNProjectionEngine.initialize()
    only populates self._preseason_profiles when snap-count data is missing for
    the target season -- true for a future season (production), false for every
    historical fold year here since that data already exists. Callers use the
    flag to flag folds whose simulation path diverges from production.
    """
    from services.nn_projection_engine import NNProjectionEngine
    from scripts.predict_season import _load_schedule

    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)
    engine.initialize(fold_year)
    used_preseason_profiles = bool(engine._preseason_profiles)
    schedule = _load_schedule(RAWDATA_DIR, fold_year, fold_year - 1)
    results = engine.simulate_season(schedule, n_sims=10_000)
    model_wins = {team: stats["mean_wins"] for team, stats in results["team_stats"].items()}
    return model_wins, used_preseason_profiles


def _actual_wins(fold_year):
    from services.db_service import get_collection_df
    df = get_collection_df("nfl_standings", filters=[("season", "==", fold_year)])
    if df.empty:
        return {}
    return dict(zip(df["team"], df["wins"]))


def _consensus_wins(fold_year):
    from services.data_service import get_consensus_projections
    consensus = get_consensus_projections(fold_year)
    return {
        team: v["consensus_mean"]
        for team, v in consensus.items()
        if v.get("consensus_mean") is not None
    }


def run_fold(fold_year, artifacts_dir, force=False):
    feature_table = build_master_feature_table(min_season=2006, max_season=fold_year - 1)
    nn_svc, xgb_svc, lr_svc = _get_or_train_fold_models(fold_year, artifacts_dir, feature_table, force)

    model_wins, used_preseason_profiles = _project_fold_season(fold_year, nn_svc, xgb_svc, lr_svc)
    actual = _actual_wins(fold_year)
    consensus = _consensus_wins(fold_year)

    _, val_df, _ = NNPredictionService._split_data(feature_table)
    importance_df = _collect_feature_importance(fold_year, nn_svc, xgb_svc, lr_svc, val_df)

    rows = []
    for team, actual_w in actual.items():
        if team not in model_wins:
            continue
        row = {
            "season": fold_year,
            "team": team,
            "actual_wins": actual_w,
            "model_wins": round(model_wins[team], 2),
            "model_abs_err": round(abs(model_wins[team] - actual_w), 2),
            "used_preseason_profiles": used_preseason_profiles,
        }
        if team in consensus:
            row["consensus_wins"] = consensus[team]
            row["consensus_abs_err"] = round(abs(consensus[team] - actual_w), 2)
        else:
            row["consensus_wins"] = None
            row["consensus_abs_err"] = None
        rows.append(row)

    return {"rows": rows, "importance": importance_df}


def _print_summary(report_df):
    print(f"\n{'=' * 60}")
    print("  Walk-Forward Validation Summary")
    print(f"{'=' * 60}")

    if report_df.empty:
        print("\nNo folds completed successfully.")
        return

    print(f"  {'Season':<8}{'Model MAE':<12}{'Consensus MAE':<15}{'n':<5}")
    for season, grp in report_df.groupby("season"):
        cons = grp.dropna(subset=["consensus_abs_err"])
        cons_mae = f"{cons['consensus_abs_err'].mean():.2f}" if not cons.empty else "n/a"
        print(f"  {season:<8}{grp['model_abs_err'].mean():<12.2f}{cons_mae:<15}{len(grp):<5}")

    overall_cons = report_df.dropna(subset=["consensus_abs_err"])
    overall_cons_mae = f"{overall_cons['consensus_abs_err'].mean():.2f}" if not overall_cons.empty else "n/a"
    print(f"  {'ALL':<8}{report_df['model_abs_err'].mean():<12.2f}{overall_cons_mae:<15}{len(report_df):<5}")
    print("\n  Benchmark bar (2017-2025 pooled analyst consensus): 2.18")

    non_preseason = (~report_df["used_preseason_profiles"]).sum()
    total = len(report_df)
    if non_preseason > 0:
        print(f"\n  NOTE: {non_preseason} of {total} team-seasons did not use the preseason-profile "
              "projection path (production behavior for a future season differs from what these "
              "historical folds could exercise).")


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation harness")
    parser.add_argument("--seasons", type=int, nargs=2, default=[FOLD_START_DEFAULT, FOLD_END_DEFAULT],
                         metavar=("START", "END"))
    parser.add_argument("--force", action="store_true",
                         help="Retrain fold models even if cached artifacts exist")
    args = parser.parse_args()

    # Preflight: a misconfigured environment (e.g. USE_LOCAL_DATA unset with no
    # reachable Firestore credentials) makes every fold "succeed" with zero rows,
    # and this would otherwise only surface after all folds finish training
    # (hours of real model training) with a misleading "no folds completed"
    # message. Fail fast against the first fold year instead.
    if not _actual_wins(args.seasons[0]) or not _consensus_wins(args.seasons[0]):
        print(f"ERROR: no nfl_standings or consensus_projections data found for season {args.seasons[0]}.")
        print("Check USE_LOCAL_DATA / Firestore credentials before running the full fold loop.")
        sys.exit(1)

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    all_rows = []
    all_importance = []
    report_df = pd.DataFrame()
    for fold_year in range(args.seasons[0], args.seasons[1] + 1):
        print(f"\n{'=' * 60}\n  Fold {fold_year}\n{'=' * 60}")
        try:
            result = run_fold(fold_year, ARTIFACTS_DIR, force=args.force)
        except Exception:
            logger.exception("Fold %d failed", fold_year)
            continue
        all_rows.extend(result["rows"])
        all_importance.append(result["importance"])

        # Write after every fold, not just at the end, so a crash or Ctrl-C
        # partway through a multi-hour run doesn't lose progress from folds
        # already scored.
        report_df = pd.DataFrame(all_rows)
        report_df.to_csv(REPORTS_DIR / "walk_forward_validation.csv", index=False)

        importance_df = pd.concat(all_importance, ignore_index=True) if all_importance else pd.DataFrame()
        importance_df.to_csv(REPORTS_DIR / "walk_forward_feature_importance.csv", index=False)

    _print_summary(report_df)


if __name__ == "__main__":
    main()
