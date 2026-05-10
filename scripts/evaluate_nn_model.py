"""scripts/evaluate_nn_model.py -- Evaluate NN model predictions vs actuals.

Generates two CSV reports:
  1. Season-level: predicted vs actual win totals for every team-season
  2. Game-level: predicted home win probability vs actual outcome for every game

Usage:
    python scripts/evaluate_nn_model.py
    python scripts/evaluate_nn_model.py --min-season 2020 --max-season 2024
"""

import sys
import os
import argparse
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

import numpy as np
import pandas as pd

from services.nn_feature_engine import build_master_feature_table
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS,
    LABEL_COLUMN,
)

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "reports"


def _generate_game_level_report(
    svc: NNPredictionService, feature_table: pd.DataFrame
) -> pd.DataFrame:
    """Generate per-game predictions vs actuals.

    Returns:
        DataFrame with columns: season, week, home_team, away_team,
        pred_home_wp, actual_outcome, predicted_winner, actual_winner, correct
    """
    X = feature_table[FEATURE_COLUMNS].values.astype(np.float32)
    if svc.scaler is not None:
        X = svc.scaler.transform(X)

    preds = svc.model.predict(X, verbose=0).flatten()

    report = feature_table[["season", "week", "home_team", "away_team", "home_win"]].copy()
    report["pred_home_wp"] = np.round(preds, 4)
    report["pred_away_wp"] = np.round(1.0 - preds, 4)

    report["predicted_winner"] = np.where(
        report["pred_home_wp"] >= 0.5,
        report["home_team"],
        report["away_team"],
    )
    report["actual_winner"] = np.where(
        report["home_win"] == 1.0, report["home_team"],
        np.where(report["home_win"] == 0.0, report["away_team"], "TIE"),
    )
    report["correct"] = (report["predicted_winner"] == report["actual_winner"])

    report.rename(columns={"home_win": "actual_home_win"}, inplace=True)
    report = report.sort_values(["season", "week", "home_team"]).reset_index(drop=True)
    return report


def _generate_season_level_report(game_report: pd.DataFrame) -> pd.DataFrame:
    """Aggregate game predictions into season win totals per team.

    Returns:
        DataFrame with columns: season, team, actual_wins, pred_wins,
        error, games_played
    """
    records = []
    for season in sorted(game_report["season"].unique()):
        s_data = game_report[game_report["season"] == season]
        all_teams = sorted(
            set(s_data["home_team"].unique()) | set(s_data["away_team"].unique())
        )

        for team in all_teams:
            home_g = s_data[s_data["home_team"] == team]
            away_g = s_data[s_data["away_team"] == team]

            # Actual wins
            actual = (
                (home_g["actual_home_win"] == 1.0).sum()
                + (away_g["actual_home_win"] == 0.0).sum()
                + 0.5 * (home_g["actual_home_win"] == 0.5).sum()
                + 0.5 * (away_g["actual_home_win"] == 0.5).sum()
            )

            # Predicted wins (sum of win probabilities)
            pred = (
                home_g["pred_home_wp"].sum()
                + away_g["pred_away_wp"].sum()
            )

            total_games = len(home_g) + len(away_g)

            records.append({
                "season": season,
                "team": team,
                "actual_wins": float(actual),
                "pred_wins": round(float(pred), 2),
                "error": round(float(pred) - float(actual), 2),
                "abs_error": round(abs(float(pred) - float(actual)), 2),
                "games_played": total_games,
            })

    df = pd.DataFrame(records)
    df = df.sort_values(["season", "team"]).reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate NN model: predicted vs actual"
    )
    parser.add_argument(
        "--min-season", type=int, default=2020,
        help="Earliest season (default: 2020)"
    )
    parser.add_argument(
        "--max-season", type=int, default=2025,
        help="Latest season (default: 2025)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to .keras model (default: models/nn_v1.keras)"
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  NFL Neural Network -- Evaluation Report")
    print("=" * 65)

    # Load model
    print("\n[1/4] Loading model...")
    svc = NNPredictionService()
    svc.load_model(args.model)

    # Build feature table
    print("[2/4] Building feature table...")
    ft = build_master_feature_table(
        min_season=args.min_season, max_season=args.max_season
    )
    print(f"  {len(ft)} games, seasons {ft['season'].min()}-{ft['season'].max()}")

    # Game-level report
    print("[3/4] Generating game-level report...")
    game_report = _generate_game_level_report(svc, ft)

    # Season-level report
    print("[4/4] Generating season-level report...")
    season_report = _generate_season_level_report(game_report)

    # Save to CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    game_path = OUTPUT_DIR / "nn_game_predictions.csv"
    season_path = OUTPUT_DIR / "nn_season_predictions.csv"
    game_report.to_csv(game_path, index=False)
    season_report.to_csv(season_path, index=False)
    print(f"\n  Saved: {game_path}")
    print(f"  Saved: {season_path}")

    # Print season summary to console
    from sklearn.metrics import r2_score, mean_absolute_error

    # Filter to complete seasons only (>= 16 games)
    complete = season_report[season_report["games_played"] >= 16]
    if not complete.empty:
        r2 = r2_score(complete["actual_wins"], complete["pred_wins"])
        mae = mean_absolute_error(complete["actual_wins"], complete["pred_wins"])

        print(f"\n{'='*65}")
        print(f"  SEASON-LEVEL SUMMARY (complete seasons only)")
        print(f"{'='*65}")
        print(f"  Seasons: {complete['season'].min()}-{complete['season'].max()}")
        print(f"  Team-Seasons: {len(complete)}")
        print(f"  Season R2: {r2:.4f}")
        print(f"  Season MAE: {mae:.2f} wins")
        print(f"{'='*65}")

        # Print per-season breakdown
        print(f"\n  {'Season':<10}{'R2':<10}{'MAE':<10}{'Teams':<8}")
        print(f"  {'-'*38}")
        for season in sorted(complete["season"].unique()):
            ss = complete[complete["season"] == season]
            sr2 = r2_score(ss["actual_wins"], ss["pred_wins"])
            smae = mean_absolute_error(ss["actual_wins"], ss["pred_wins"])
            print(f"  {season:<10}{sr2:<10.4f}{smae:<10.2f}{len(ss):<8}")

    # Print top/bottom predictions
    print(f"\n{'='*65}")
    print(f"  LARGEST ERRORS (season-level)")
    print(f"{'='*65}")
    worst = complete.nlargest(10, "abs_error")
    print(f"  {'Season':<8}{'Team':<6}{'Actual':<10}{'Predicted':<12}{'Error':<8}")
    print(f"  {'-'*44}")
    for _, row in worst.iterrows():
        print(f"  {int(row['season']):<8}{row['team']:<6}"
              f"{row['actual_wins']:<10.1f}{row['pred_wins']:<12.1f}"
              f"{row['error']:>+6.1f}")

    # Game-level accuracy
    correct = game_report["correct"].sum()
    total = len(game_report)
    print(f"\n{'='*65}")
    print(f"  GAME-LEVEL ACCURACY")
    print(f"{'='*65}")
    print(f"  Total games: {total}")
    print(f"  Correct picks: {correct}")
    print(f"  Accuracy: {correct/total*100:.1f}%")

    # Per-season accuracy
    print(f"\n  {'Season':<10}{'Games':<8}{'Correct':<10}{'Accuracy':<10}")
    print(f"  {'-'*38}")
    for season in sorted(game_report["season"].unique()):
        sg = game_report[game_report["season"] == season]
        sc = sg["correct"].sum()
        print(f"  {season:<10}{len(sg):<8}{sc:<10}{sc/len(sg)*100:<10.1f}%")

    print(f"\n{'='*65}")
    print(f"  Reports saved to: {OUTPUT_DIR}")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
