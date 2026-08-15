"""scripts/walk_forward_diagnose_preseason_path.py -- Force the preseason
player-profile path onto the 2025 walk-forward fold and score it against
real outcomes.

Every prior walk-forward result this session (and the ones already
committed to reports/walk_forward_validation.csv) used
used_preseason_profiles=False for all 160 team-seasons, because
NNProjectionEngine.initialize() only takes that branch when the target
season's snap_counts file is missing -- never true for a completed
historical season. That left the entire preseason-profile pipeline (DL/LB/
CB/QB/WR/TE/RB/OL multi-season blending, the composite-normalization Elo
boost, all fixed this session) with zero validation against real outcomes.

2025 is the only walk-forward fold year where this is even possible:
rawdata/depth_charts/depth_charts_{season}.csv changed schema starting
with the 2025 sync (dt/team/pos_abb/pos_rank/player_name); 2021-2024 use
an incompatible older schema (club_code/depth_position/full_name/week)
that compute_preseason_player_profiles() cannot read. No schema adapter
is written here -- this script only covers 2025.

Method: load the already-trained/cached 2025 fold models (no retraining),
run NNProjectionEngine.initialize(2025) normally to build team profiles,
then directly override engine._preseason_profiles with
compute_preseason_player_profiles(2025, RAWDATA_DIR) -- this is exactly
what initialize() would have done had snap_counts_2025.csv been absent,
without touching production code. Then run the real simulate_season()
Monte Carlo path and score against actual 2025 wins and consensus,
comparable to the original (non-preseason-path) 2025 walk-forward row
already in reports/walk_forward_validation.csv (MAE ~2.79-2.81).

Diagnostic only: reads cached models/walkforward/ artifacts, writes
reports/walk_forward_preseason_path_2025.csv.

Usage:
    python scripts/walk_forward_diagnose_preseason_path.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import pandas as pd

from services.nn_feature_engine import RAWDATA_DIR, compute_preseason_player_profiles
from services.nn_projection_engine import NNProjectionEngine
from scripts.predict_season import _load_schedule
from scripts.walk_forward_validate import (
    ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist,
    _actual_wins, _consensus_wins,
)

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_YEAR = 2025


def main():
    if not _fold_artifacts_exist(ARTIFACTS_DIR, FOLD_YEAR):
        print(f"ERROR: no cached fold artifacts for {FOLD_YEAR} -- run walk_forward_validate.py first.")
        sys.exit(1)

    logger.info("[%d] Loading cached fold models (no retraining)...", FOLD_YEAR)
    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, FOLD_YEAR)
    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)

    logger.info("[%d] Building team profiles (normal path)...", FOLD_YEAR)
    engine.initialize(FOLD_YEAR)
    logger.info("[%d] used_preseason_profiles under normal initialize(): %s",
                FOLD_YEAR, bool(engine._preseason_profiles))

    logger.info("[%d] Forcing preseason player-profile path (historical roster/depth_charts snapshot)...", FOLD_YEAR)
    forced_profiles = compute_preseason_player_profiles(FOLD_YEAR, RAWDATA_DIR)
    if not forced_profiles:
        print(f"ERROR: compute_preseason_player_profiles({FOLD_YEAR}, ...) returned empty -- "
              f"check rawdata/rosters/roster_{FOLD_YEAR}.csv and rawdata/depth_charts/depth_charts_{FOLD_YEAR}.csv.")
        sys.exit(1)
    engine._preseason_profiles = forced_profiles
    logger.info("[%d] Forced preseason profiles built for %d teams.", FOLD_YEAR, len(forced_profiles))

    logger.info("[%d] Running Monte Carlo season simulation (n_sims=10,000)...", FOLD_YEAR)
    schedule = _load_schedule(RAWDATA_DIR, FOLD_YEAR, FOLD_YEAR - 1)
    result = engine.simulate_season(schedule, n_sims=10_000)
    model_wins = {team: stats["mean_wins"] for team, stats in result["team_stats"].items()}

    actual = _actual_wins(FOLD_YEAR)
    consensus = _consensus_wins(FOLD_YEAR)

    rows = []
    for team, actual_w in actual.items():
        if team not in model_wins:
            continue
        row = {
            "season": FOLD_YEAR, "team": team, "actual_wins": actual_w,
            "model_wins": round(model_wins[team], 2),
            "model_abs_err": round(abs(model_wins[team] - actual_w), 2),
        }
        if team in consensus:
            row["consensus_wins"] = consensus[team]
            row["consensus_abs_err"] = round(abs(consensus[team] - actual_w), 2)
        else:
            row["consensus_wins"] = None
            row["consensus_abs_err"] = None
        rows.append(row)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(REPORTS_DIR / "walk_forward_preseason_path_2025.csv", index=False)

    print(f"\n{'=' * 60}\n  Forced Preseason-Path Validation -- {FOLD_YEAR}\n{'=' * 60}")
    print(df.sort_values("model_abs_err", ascending=False).to_string(index=False))

    model_mae = df["model_abs_err"].mean()
    cons_df = df.dropna(subset=["consensus_abs_err"])
    cons_mae = cons_df["consensus_abs_err"].mean() if not cons_df.empty else float("nan")
    print(f"\n  Forced preseason-path MAE ({FOLD_YEAR}, n={len(df)}): {model_mae:.2f}")
    print(f"  Consensus MAE ({FOLD_YEAR}, n={len(cons_df)}): {cons_mae:.2f}")
    print(f"  Original (in-season-path) 2025 walk-forward MAE, for comparison: 2.79 (pre-Elo-fix) / 2.81-2.82 (post-Elo-fix)")


if __name__ == "__main__":
    main()
