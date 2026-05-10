"""tmp/run_eval.py -- Generate historical evaluation reports."""
import sys, os, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.metrics import r2_score, mean_absolute_error
from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS as FE_COLS
from services.nn_prediction_service import NNPredictionService, FEATURE_COLUMNS, LABEL_COLUMN

svc = NNPredictionService()
svc.load_model(None)
ft = build_master_feature_table(min_season=2006, max_season=2025)
X = ft[FEATURE_COLUMNS].values.astype(np.float32)
X = svc.scaler.transform(X)
ft = ft.copy()
ft["pred_home_wp"] = svc.model.predict(X, verbose=0).flatten()
ft["pred_winner"] = np.where(ft["pred_home_wp"] > 0.5, ft["home_team"], ft["away_team"])
ft["actual_winner"] = np.where(
    ft["home_win"] == 1.0, ft["home_team"],
    np.where(ft["home_win"] == 0.0, ft["away_team"], "TIE")
)
ft["correct"] = (ft["pred_winner"] == ft["actual_winner"]).astype(int)

# Season-level aggregation
records = []
for s in sorted(ft["season"].unique()):
    sd = ft[ft["season"] == s]
    teams = set(sd["home_team"].unique()) | set(sd["away_team"].unique())
    for t in teams:
        hw = sd[(sd["home_team"] == t) & (sd["home_win"] == 1.0)].shape[0]
        aw = sd[(sd["away_team"] == t) & (sd["home_win"] == 0.0)].shape[0]
        ht = sd[(sd["home_team"] == t) & (sd["home_win"] == 0.5)].shape[0]
        at = sd[(sd["away_team"] == t) & (sd["home_win"] == 0.5)].shape[0]
        actual = hw + aw + 0.5 * (ht + at)
        hp = sd[sd["home_team"] == t]["pred_home_wp"].sum()
        ap2 = (1 - sd[sd["away_team"] == t]["pred_home_wp"]).sum()
        pred = hp + ap2
        g = sd[sd["home_team"] == t].shape[0] + sd[sd["away_team"] == t].shape[0]
        if g >= 10:
            records.append({
                "season": s, "team": t, "actual_wins": actual,
                "pred_wins": round(pred, 2), "games_played": g,
                "error": round(pred - actual, 2),
            })

sdf = pd.DataFrame(records)
sdf.to_csv("reports/nn_season_predictions.csv", index=False)

# Game-level CSV
gt = ft[["season", "week", "home_team", "away_team", "pred_home_wp",
         "home_win", "pred_winner", "actual_winner", "correct"]].copy()
gt.to_csv("reports/nn_game_predictions.csv", index=False)

# Build text report
lines = []
lines.append("=" * 65)
lines.append("  NFL Neural Network -- Evaluation Report (Focal Loss V3)")
lines.append("=" * 65)
lines.append("  Features: {} | Architecture: 96-48-24-1 | Loss: Focal".format(len(FEATURE_COLUMNS)))
lines.append("  Seasons: {}-{} | Games: {}".format(int(ft.season.min()), int(ft.season.max()), len(ft)))
lines.append("")

# Overall season summary (full seasons only)
c = sdf[sdf["games_played"] >= 16]
r2 = r2_score(c.actual_wins, c.pred_wins) if len(c) > 1 else 0
mae = mean_absolute_error(c.actual_wins, c.pred_wins) if len(c) > 0 else 0

lines.append("=" * 65)
lines.append("  SEASON-LEVEL SUMMARY (full seasons only, >=16 games)")
lines.append("=" * 65)
lines.append("  Team-Seasons: {}".format(len(c)))
lines.append("  Season R2:    {:.4f}".format(r2))
lines.append("  Season MAE:   {:.2f} wins".format(mae))
lines.append("  Pred Range:   {:.1f} - {:.1f}".format(c.pred_wins.min(), c.pred_wins.max()))
lines.append("  Actual Range: {:.1f} - {:.1f}".format(c.actual_wins.min(), c.actual_wins.max()))
lines.append("=" * 65)
lines.append("")

# Per-season breakdown
hdr = "  {:<10}{:<10}{:<10}{:<8}".format("Season", "R2", "MAE", "Teams")
lines.append(hdr)
lines.append("  " + "-" * 38)
for s in sorted(c.season.unique()):
    ss = c[c.season == s]
    sr = r2_score(ss.actual_wins, ss.pred_wins) if len(ss) > 1 else 0
    sm = mean_absolute_error(ss.actual_wins, ss.pred_wins)
    lines.append("  {:<10}{:<10.4f}{:<10.2f}{:<8}".format(int(s), sr, sm, len(ss)))

# Largest errors
lines.append("")
lines.append("=" * 65)
lines.append("  LARGEST ERRORS (season-level)")
lines.append("=" * 65)
hdr2 = "  {:<8}{:<6}{:<10}{:<12}{:<8}".format("Season", "Team", "Actual", "Predicted", "Error")
lines.append(hdr2)
lines.append("  " + "-" * 44)
worst = c.reindex(c["error"].abs().sort_values(ascending=False).index).head(10)
for _, r in worst.iterrows():
    lines.append("  {:<8}{:<6}{:<10.1f}{:<12.1f}{:+.1f}".format(
        int(r.season), r.team, r.actual_wins, r.pred_wins, r.error
    ))

# Game-level accuracy
lines.append("")
lines.append("=" * 65)
lines.append("  GAME-LEVEL ACCURACY")
lines.append("=" * 65)
non_tie = ft[ft.home_win != 0.5]
total = len(non_tie)
correct = int(non_tie["correct"].sum())
lines.append("  Total games: {}".format(total))
lines.append("  Correct picks: {}".format(correct))
lines.append("  Accuracy: {:.1f}%".format(100 * correct / total))
lines.append("")
hdr3 = "  {:<10}{:<8}{:<10}{:<10}".format("Season", "Games", "Correct", "Accuracy")
lines.append(hdr3)
lines.append("  " + "-" * 38)
for s in sorted(ft.season.unique()):
    sd = ft[(ft.season == s) & (ft.home_win != 0.5)]
    if len(sd) == 0:
        continue
    cr = int(sd["correct"].sum())
    lines.append("  {:<10}{:<8}{:<10}{:.1f}%".format(int(s), len(sd), cr, 100 * cr / len(sd)))

lines.append("")
lines.append("=" * 65)
lines.append("  Reports: nn_season_predictions.csv, nn_game_predictions.csv")
lines.append("=" * 65)

report = "\n".join(lines)

with open("reports/eval_report.txt", "w", encoding="utf-8") as f:
    f.write(report)
with open("reports/evaluation_output.txt", "w", encoding="utf-8") as f:
    f.write(report)

print(report)
