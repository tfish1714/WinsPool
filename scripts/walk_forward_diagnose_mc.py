"""scripts/walk_forward_diagnose_mc.py -- Isolate MC/Elo-state-evolution error
from raw per-game classifier error.

Follow-on diagnostic to walk_forward_validate.py. For each cached fold
(2021-2025), scores season win totals two ways using the SAME trained
NN+XGB+LR models and the SAME static team profiles:

  1. "mc"     -- simulate_season(): Monte Carlo, Elo/EPA state evolves
                 week-to-week across the simulated season (the production
                 method).
  2. "simple" -- direct sum of per-game blended win probabilities computed
                 ONCE from static preseason team profiles, no state
                 evolution (docs/prediction_model.md's "Season Win
                 Projection" formula; NNProjectionEngine.game_win_probabilities_batch()
                 already implements exactly this).

If "simple" scores similarly to "mc", the MC/Elo-state machinery isn't
adding error -- the classifiers themselves are the ceiling. If "simple" is
meaningfully better, the state-evolution machinery is actively hurting.

Diagnostic only: reads cached models/walkforward/ artifacts (never
retrains), writes reports/walk_forward_mc_vs_simple.csv, touches no
registries or Firestore collections other than reads.

Usage:
    python scripts/walk_forward_diagnose_mc.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

import pandas as pd

from services.nn_feature_engine import RAWDATA_DIR
from services.nn_projection_engine import NNProjectionEngine
from scripts.predict_season import _load_schedule
from scripts.walk_forward_validate import (
    ARTIFACTS_DIR, _load_fold_artifacts, _fold_artifacts_exist,
    _actual_wins, _consensus_wins,
)

REPORTS_DIR = pathlib.Path(__file__).parent.parent / "reports"
FOLD_START, FOLD_END = 2021, 2025


def _simple_season_wins(engine: NNProjectionEngine, schedule: pd.DataFrame) -> dict:
    """Direct sum of per-game blended win probabilities, no MC/state evolution."""
    pairs = list(zip(schedule["home_team"], schedule["away_team"]))
    preds = engine.game_win_probabilities_batch(pairs)

    wins = {}
    for p in preds:
        wins[p["home_team"]] = wins.get(p["home_team"], 0.0) + p["home_win_prob"]
        wins[p["away_team"]] = wins.get(p["away_team"], 0.0) + p["away_win_prob"]
    return wins


def run_fold(fold_year: int) -> list:
    if not _fold_artifacts_exist(ARTIFACTS_DIR, fold_year):
        logger.warning("[%d] No cached fold artifacts -- run walk_forward_validate.py first.", fold_year)
        return []

    nn_svc, xgb_svc, lr_svc = _load_fold_artifacts(ARTIFACTS_DIR, fold_year)
    engine = NNProjectionEngine(nn_svc=nn_svc, xgb_svc=xgb_svc, lr_svc=lr_svc)
    engine.initialize(fold_year)

    schedule = _load_schedule(RAWDATA_DIR, fold_year, fold_year - 1)

    logger.info("[%d] Running Monte Carlo simulation (production method)...", fold_year)
    mc_results = engine.simulate_season(schedule, n_sims=10_000)
    mc_wins = {team: stats["mean_wins"] for team, stats in mc_results["team_stats"].items()}

    logger.info("[%d] Running direct sum-of-probabilities (simple method)...", fold_year)
    simple_wins = _simple_season_wins(engine, schedule)

    actual = _actual_wins(fold_year)
    consensus = _consensus_wins(fold_year)

    rows = []
    for team, actual_w in actual.items():
        if team not in mc_wins or team not in simple_wins:
            continue
        row = {
            "season": fold_year,
            "team": team,
            "actual_wins": actual_w,
            "mc_wins": round(mc_wins[team], 2),
            "mc_abs_err": round(abs(mc_wins[team] - actual_w), 2),
            "simple_wins": round(simple_wins[team], 2),
            "simple_abs_err": round(abs(simple_wins[team] - actual_w), 2),
        }
        if team in consensus:
            row["consensus_wins"] = consensus[team]
            row["consensus_abs_err"] = round(abs(consensus[team] - actual_w), 2)
        else:
            row["consensus_wins"] = None
            row["consensus_abs_err"] = None
        rows.append(row)
    return rows


def main():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for fold_year in range(FOLD_START, FOLD_END + 1):
        print(f"\n{'=' * 60}\n  Fold {fold_year}\n{'=' * 60}")
        rows = run_fold(fold_year)
        all_rows.extend(rows)
        if all_rows:
            pd.DataFrame(all_rows).to_csv(REPORTS_DIR / "walk_forward_mc_vs_simple.csv", index=False)

    if not all_rows:
        print("\nNo folds scored -- check that models/walkforward/ artifacts exist.")
        return

    df = pd.DataFrame(all_rows)
    print(f"\n{'=' * 60}\n  MC vs. Simple Summary\n{'=' * 60}")
    print(f"  {'Season':<8}{'MC MAE':<10}{'Simple MAE':<12}{'Consensus MAE':<15}{'n':<5}")
    for season, grp in df.groupby("season"):
        cons = grp.dropna(subset=["consensus_abs_err"])
        cons_mae = f"{cons['consensus_abs_err'].mean():.2f}" if not cons.empty else "n/a"
        print(f"  {season:<8}{grp['mc_abs_err'].mean():<10.2f}{grp['simple_abs_err'].mean():<12.2f}{cons_mae:<15}{len(grp):<5}")

    overall_cons = df.dropna(subset=["consensus_abs_err"])
    overall_cons_mae = f"{overall_cons['consensus_abs_err'].mean():.2f}" if not overall_cons.empty else "n/a"
    print(f"  {'ALL':<8}{df['mc_abs_err'].mean():<10.2f}{df['simple_abs_err'].mean():<12.2f}{overall_cons_mae:<15}{len(df):<5}")


if __name__ == "__main__":
    main()
