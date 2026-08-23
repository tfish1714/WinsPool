"""scripts/walk_forward_calibrate_preseason_weights.py -- Validate/recalibrate
PRESEASON_ELO_WEIGHTS (services/constants.py) against real historical outcomes.

Generalizes walk_forward_diagnose_preseason_path.py (which only covered 2025 --
the sole season compute_preseason_player_profiles() could read before the
depth_charts schema-compat fix) to loop over every walk-forward fold that has
cached trained models in models/walkforward/ (2021-2025 as of this writing).

For each fold: load the already-trained fold models (no retraining --
PRESEASON_ELO_WEIGHTS only affects simulate_season()'s state-seeding, not
anything the models learned), force the preseason player-profile path exactly
as initialize() would if that season's snap_counts were still empty, run the
real Monte Carlo simulate_season(), and score predicted wins against actual
wins. Also reports each of the 7 profile dimensions' real correlation with
actual win totals, pooled across folds (z-scored within each season), as a
sanity check on the current hand-picked weights.

--weights lets you pass a candidate PRESEASON_ELO_WEIGHTS dict (as a JSON
string) to compare against the baseline (services.constants.PRESEASON_ELO_WEIGHTS)
without editing constants.py.

Diagnostic only: writes reports/walk_forward_preseason_weights_calibration.csv.

Usage:
    python scripts/walk_forward_calibrate_preseason_weights.py
    python scripts/walk_forward_calibrate_preseason_weights.py --weights '{"qb_tier": 0.25, ...}'
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd

from services.nn_feature_engine import RAWDATA_DIR, compute_preseason_player_profiles
from services.nn_projection_engine import NNProjectionEngine
from services.constants import PRESEASON_ELO_WEIGHTS
from scripts.predict_season import _load_schedule
from scripts.walk_forward_validate import (
    ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist,
    _actual_wins, _consensus_wins,
)

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_YEARS = [2021, 2022, 2023, 2024, 2025]
_DEF_DIMS = {"def_pass_epa", "def_rush_epa"}


def _score_fold(fold_year: int) -> pd.DataFrame:
    """Force the preseason path for one fold, run simulate_season(), return
    a per-team DataFrame with actual/model wins and abs error."""
    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, fold_year)
    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)
    engine.initialize(fold_year)

    forced_profiles = compute_preseason_player_profiles(fold_year, RAWDATA_DIR)
    if not forced_profiles:
        raise RuntimeError(f"compute_preseason_player_profiles({fold_year}, ...) returned empty")
    engine._preseason_profiles = forced_profiles

    schedule = _load_schedule(RAWDATA_DIR, fold_year, fold_year - 1)
    result = engine.simulate_season(schedule, n_sims=10_000)
    model_wins = {team: stats["mean_wins"] for team, stats in result["team_stats"].items()}

    actual = _actual_wins(fold_year)
    consensus = _consensus_wins(fold_year)

    rows = []
    for team, actual_w in actual.items():
        if team not in model_wins:
            continue
        rows.append({
            "season": fold_year, "team": team, "actual_wins": actual_w,
            "model_wins": round(model_wins[team], 2),
            "model_abs_err": round(abs(model_wins[team] - actual_w), 2),
            "consensus_wins": consensus.get(team),
            "consensus_abs_err": (
                round(abs(consensus[team] - actual_w), 2) if team in consensus else None
            ),
        })
    return pd.DataFrame(rows)


def score_weights(weights: dict, fold_years=FOLD_YEARS) -> pd.DataFrame:
    """Run every fold with services.nn_projection_engine.PRESEASON_ELO_WEIGHTS
    monkeypatched to `weights`, restoring it afterward. Returns the combined
    per-team DataFrame across all folds."""
    import services.nn_projection_engine as eng_mod
    original = eng_mod.PRESEASON_ELO_WEIGHTS
    eng_mod.PRESEASON_ELO_WEIGHTS = weights
    try:
        frames = []
        for fold_year in fold_years:
            if not _fold_artifacts_exist(ARTIFACTS_DIR, fold_year):
                logger.warning("No cached fold artifacts for %d -- skipping.", fold_year)
                continue
            frames.append(_score_fold(fold_year))
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    finally:
        eng_mod.PRESEASON_ELO_WEIGHTS = original


def dimension_correlations(fold_years=FOLD_YEARS) -> pd.DataFrame:
    """For each of the 7 PRESEASON_ELO_WEIGHTS dimensions, pool
    (z-scored-within-season dimension value, z-scored-within-season actual
    wins) pairs across all folds and report the Pearson correlation --
    a real-data sanity check on the current hand-picked weight ranking."""
    dims = list(PRESEASON_ELO_WEIGHTS.keys())
    pooled = {d: [] for d in dims}
    pooled_wins = []

    for fold_year in fold_years:
        profiles = compute_preseason_player_profiles(fold_year, RAWDATA_DIR)
        actual = _actual_wins(fold_year)
        if not profiles or not actual:
            continue
        teams = [t for t in profiles if t in actual]
        if len(teams) < 4:
            continue

        wins = np.array([actual[t] for t in teams], dtype=float)
        wins_z = (wins - wins.mean()) / max(wins.std(), 1e-6)
        pooled_wins.extend(wins_z.tolist())

        for d in dims:
            vals = np.array([float(profiles[t].get(d, 0.0)) for t in teams], dtype=float)
            sig = max(float(vals.std()), 1e-6)
            z = (vals - vals.mean()) / sig
            if d in _DEF_DIMS:
                z = -z  # flip so higher z always means "better," matching PRESEASON_ELO_WEIGHTS convention
            pooled[d].extend(z.tolist())

    pooled_wins = np.array(pooled_wins)
    rows = []
    for d in dims:
        dv = np.array(pooled[d])
        corr = float(np.corrcoef(dv, pooled_wins)[0, 1]) if len(dv) > 1 else float("nan")
        rows.append({"dim": d, "current_weight": PRESEASON_ELO_WEIGHTS[d],
                      "corr_with_actual_wins": round(corr, 4), "n": len(dv)})
    return pd.DataFrame(rows).sort_values("corr_with_actual_wins", ascending=False)


def _summarize(label: str, df: pd.DataFrame) -> dict:
    mae = df["model_abs_err"].mean()
    cons_df = df.dropna(subset=["consensus_abs_err"])
    cons_mae = cons_df["consensus_abs_err"].mean() if not cons_df.empty else float("nan")
    per_fold = df.groupby("season")["model_abs_err"].mean().round(3).to_dict()
    return {"label": label, "mae": round(mae, 4), "consensus_mae": round(cons_mae, 4),
            "n": len(df), "per_fold_mae": per_fold}


def main():
    parser = argparse.ArgumentParser(
        description="Validate/recalibrate PRESEASON_ELO_WEIGHTS against real historical outcomes.")
    parser.add_argument("--weights", type=str, default=None,
                         help="Candidate PRESEASON_ELO_WEIGHTS as a JSON string, "
                              "compared against the baseline.")
    parser.add_argument("--skip-baseline", action="store_true",
                         help="Skip the current-weights baseline run (only score --weights).")
    args = parser.parse_args()

    print("=" * 64)
    print("  Preseason Elo Weight Calibration -- Real Historical Outcomes")
    print(f"  Folds: {FOLD_YEARS}")
    print("=" * 64)

    print("\n[1/3] Per-dimension correlation with actual win totals (pooled, z-scored per season)...")
    corr_df = dimension_correlations()
    print(corr_df.to_string(index=False))

    summaries = []
    if not args.skip_baseline:
        print(f"\n[2/3] Baseline (current PRESEASON_ELO_WEIGHTS): {PRESEASON_ELO_WEIGHTS}")
        baseline_df = score_weights(PRESEASON_ELO_WEIGHTS)
        summaries.append(_summarize("baseline", baseline_df))

    if args.weights:
        candidate = json.loads(args.weights)
        print(f"\n[3/3] Candidate weights: {candidate}")
        candidate_df = score_weights(candidate)
        summaries.append(_summarize("candidate", candidate_df))

    print(f"\n{'=' * 64}\n  Summary\n{'=' * 64}")
    for s in summaries:
        print(f"  {s['label']:>10}  MAE={s['mae']}  (consensus MAE={s['consensus_mae']}, n={s['n']})")
        print(f"             per-fold: {s['per_fold_mae']}")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    corr_df.to_csv(REPORTS_DIR / "walk_forward_preseason_weights_calibration.csv", index=False)


if __name__ == "__main__":
    main()
