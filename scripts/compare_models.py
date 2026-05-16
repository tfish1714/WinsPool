"""scripts/compare_models.py -- Compare NN vs XGBoost vs LR vs blended predictions.

Evaluates all model combinations across:
  - A full out-of-sample season (all 2025 games)
  - Year-by-year breakdown (2020-2025)
  - Two power rating variants: Elo (calibrated) vs Margin-of-Victory (heuristic)

Power rating sources
  MOV  : avg scoring margin -> sigmoid probability (legacy approach)
  Elo  : home_exp from elo_computed.csv (opponent-adjusted, directly calibrated)

Usage:
    python scripts/compare_models.py
    python scripts/compare_models.py --nn-version v7 --xgb-version v3 --lr-version v1
    python scripts/compare_models.py --start-season 2019
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import os
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import logging
logging.basicConfig(level=logging.WARNING)

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, roc_auc_score

from services.nn_feature_engine import build_master_feature_table, FEATURE_COLUMNS, RAWDATA_DIR, _read_csv_safe, _normalize_team
from services.nn_prediction_service import NNPredictionService
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService

LABEL_COLUMN  = "home_win"
HOME_ADV_PTS  = 2.5     # for MOV power rating
ML_W          = 0.40    # ML share in final blend
PWR_W         = 0.60    # power rating share


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _metrics(y_true, y_prob, label):
    y_bin  = (y_true > 0.5).astype(int)
    y_pred = (y_prob  > 0.5).astype(int)
    m = dict(model=label, n=len(y_true),
             accuracy=accuracy_score(y_bin, y_pred),
             logloss=log_loss(y_bin, np.clip(y_prob, 1e-7, 1-1e-7)),
             brier=brier_score_loss(y_bin, y_prob))
    m["auc"] = roc_auc_score(y_bin, y_prob) if len(np.unique(y_bin)) > 1 else float("nan")
    return m


def _print_table(rows, title=""):
    if title:
        print(f"\n  {title}")
        print("  " + "-" * 76)
    hdr = f"  {'Model':<38} {'N':>4}  {'Acc':>6}  {'LogLoss':>8}  {'Brier':>7}  {'AUC':>6}"
    print(hdr)
    print("  " + "-" * 76)
    best_acc = max(r["accuracy"] for r in rows)
    for r in rows:
        marker = " *" if abs(r["accuracy"] - best_acc) < 1e-9 else "  "
        auc = f"{r['auc']:.4f}" if not np.isnan(r["auc"]) else "  n/a"
        print(f"  {r['model']:<38} {r['n']:>4}  {r['accuracy']:>6.4f}  "
              f"{r['logloss']:>8.4f}  {r['brier']:>7.4f}  {auc}{marker}")


def _best_blend_2(y_true, p1, p2):
    """Grid search best 2-model blend weights (step 0.1). Returns (best_acc, w1, best_probs)."""
    best_acc, best_w, best_p = -1, 0.5, None
    for w in [i/10 for i in range(1, 10)]:
        p = w * p1 + (1-w) * p2
        acc = accuracy_score((y_true > 0.5).astype(int), (p > 0.5).astype(int))
        if acc > best_acc:
            best_acc, best_w, best_p = acc, w, p
    return best_acc, best_w, best_p


def _best_blend_3(y_true, p1, p2, p3):
    """Grid search best 3-model blend weights (step 0.1). Returns (best_acc, w1, w2, w3, best_probs)."""
    best_acc, bw1, bw2, bw3, best_p = -1, 1/3, 1/3, 1/3, None
    for i in range(1, 9):
        for j in range(1, 10-i):
            k = 10 - i - j
            if k < 1:
                continue
            w1, w2, w3 = i/10, j/10, k/10
            p = w1*p1 + w2*p2 + w3*p3
            acc = accuracy_score((y_true > 0.5).astype(int), (p > 0.5).astype(int))
            if acc > best_acc:
                best_acc, bw1, bw2, bw3, best_p = acc, w1, w2, w3, p
    return best_acc, bw1, bw2, bw3, best_p


def _load_elo_probs(rawdata_dir: pathlib.Path) -> pd.DataFrame:
    df = _read_csv_safe(str(rawdata_dir / "elo_computed.csv"))
    if df.empty:
        return pd.DataFrame()
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)
    df["season"]    = pd.to_numeric(df["season"], errors="coerce")
    df["week"]      = pd.to_numeric(df["week"],   errors="coerce")
    df["elo_prob"]  = pd.to_numeric(df["home_exp"], errors="coerce").clip(0.02, 0.98)
    return df[["season", "week", "home_team", "away_team", "elo_prob"]].dropna()


def _load_mov_probs(rawdata_dir: pathlib.Path) -> pd.DataFrame:
    path = rawdata_dir / "schedules" / "games.csv"
    df = _read_csv_safe(str(path))
    if df.empty:
        return pd.DataFrame()
    df["home_team"]  = df["home_team"].apply(_normalize_team)
    df["away_team"]  = df["away_team"].apply(_normalize_team)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")
    df["season"]     = pd.to_numeric(df["season"],     errors="coerce")
    df["week"]       = pd.to_numeric(df["week"],       errors="coerce")

    completed = df[(df["game_type"] == "REG") & df["home_score"].notna()].copy()
    completed["margin"] = completed["home_score"] - completed["away_score"]

    power_by_season: dict = {}
    for season in sorted(completed["season"].unique()):
        reg = completed[completed["season"] == season]
        hp = reg.groupby("home_team")["margin"].mean()
        ap = reg.groupby("away_team")["margin"].apply(lambda x: -x.mean())
        pwr = {}
        for team in set(hp.index) | set(ap.index):
            pwr[team] = (hp.get(team, 0.0) + ap.get(team, 0.0)) / 2.0
        power_by_season[season] = pwr

    rows = []
    for _, game in df.iterrows():
        prior = int(game["season"]) - 1
        pwr = power_by_season.get(prior, {})
        hp = pwr.get(game["home_team"], 0.0)
        ap = pwr.get(game["away_team"], 0.0)
        spread = (hp - ap) + HOME_ADV_PTS
        prob = float(np.clip(1.0 / (1.0 + np.exp(-spread / 6.5)), 0.02, 0.98))
        rows.append({"season": game["season"], "week": game["week"],
                     "home_team": game["home_team"], "away_team": game["away_team"],
                     "mov_prob": prob})
    return pd.DataFrame(rows).dropna()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--nn-version",   type=str, default="latest")
    parser.add_argument("--xgb-version",  type=str, default="latest")
    parser.add_argument("--lr-version",   type=str, default="latest")
    parser.add_argument("--start-season", type=int, default=2020,
                        help="First season in year-by-year breakdown (default 2020)")
    args = parser.parse_args()

    print("=" * 76)
    print("  NFL Model Comparison: NN vs XGB vs LR vs Blends | MOV vs Elo power")
    print("=" * 76)

    # ------------------------------------------------------------------ #
    # 1. Feature table + power rating lookup tables
    # ------------------------------------------------------------------ #
    print("\n[1/4] Building feature table (2006-2025)...")
    ft = build_master_feature_table(min_season=2006, max_season=2025)
    ft = ft[ft[LABEL_COLUMN] != 0.5].copy()
    print(f"  {len(ft)} games | {ft['season'].min()}-{ft['season'].max()}")

    elo_df = _load_elo_probs(RAWDATA_DIR)
    mov_df = _load_mov_probs(RAWDATA_DIR)

    ft = ft.merge(elo_df[["season","week","home_team","away_team","elo_prob"]],
                  on=["season","week","home_team","away_team"], how="left")
    ft = ft.merge(mov_df[["season","week","home_team","away_team","mov_prob"]],
                  on=["season","week","home_team","away_team"], how="left")
    ft["elo_prob"] = ft["elo_prob"].fillna(0.5)
    ft["mov_prob"] = ft["mov_prob"].fillna(0.5)

    # ------------------------------------------------------------------ #
    # 2. Load models
    # ------------------------------------------------------------------ #
    print(f"\n[2/4] Loading models (NN={args.nn_version}, XGB={args.xgb_version}, LR={args.lr_version})...")
    nn_svc = NNPredictionService()
    nn_svc.load_model(args.nn_version)

    xgb_svc = XGBPredictionService()
    xgb_svc.load_model(args.xgb_version)

    lr_svc = LRPredictionService()
    lr_svc.load_model(args.lr_version)
    print("  All models loaded.")

    # ------------------------------------------------------------------ #
    # 3. Generate predictions for all games
    # ------------------------------------------------------------------ #
    print("\n[3/4] Generating predictions for all seasons...")
    X_all = ft[FEATURE_COLUMNS].values.astype(np.float32)

    ft["nn_prob"]  = nn_svc.model.predict(
        nn_svc.scaler.transform(X_all), verbose=0
    ).flatten()
    ft["xgb_prob"] = xgb_svc.model.predict_proba(
        xgb_svc.scaler.transform(X_all)
    )[:, 1]
    ft["lr_prob"]  = lr_svc.model.predict_proba(
        lr_svc.scaler.transform(X_all)
    )[:, 1]

    # Best global blend weights (grid search on all data)
    y_all  = ft[LABEL_COLUMN].values.astype(float)
    nn_all = ft["nn_prob"].values
    xg_all = ft["xgb_prob"].values
    lr_all = ft["lr_prob"].values

    _, nn_w2, _        = _best_blend_2(y_all, nn_all, xg_all)
    xg_w2              = 1.0 - nn_w2
    _, nn_w3, xg_w3, lr_w3, _ = _best_blend_3(y_all, nn_all, xg_all, lr_all)

    print(f"  Best NN+XGB blend:     NN={nn_w2:.0%} / XGB={xg_w2:.0%}")
    print(f"  Best NN+XGB+LR blend:  NN={nn_w3:.0%} / XGB={xg_w3:.0%} / LR={lr_w3:.0%}")

    # ------------------------------------------------------------------ #
    # 4A. Full 2025 season (out-of-sample)
    # ------------------------------------------------------------------ #
    print("\n[4/4] Evaluation")

    oos = ft[ft["season"] == 2025].copy()
    y_oos = oos[LABEL_COLUMN].values.astype(float)

    nn_p  = oos["nn_prob"].values
    xgb_p = oos["xgb_prob"].values
    lr_p  = oos["lr_prob"].values
    elo_p = oos["elo_prob"].values
    mov_p = oos["mov_prob"].values

    ml2_p = nn_w2*nn_p + xg_w2*xgb_p
    ml3_p = nn_w3*nn_p + xg_w3*xgb_p + lr_w3*lr_p

    blend_elo2 = ML_W * ml2_p + PWR_W * elo_p
    blend_elo3 = ML_W * ml3_p + PWR_W * elo_p
    blend_mov2 = ML_W * ml2_p + PWR_W * mov_p
    blend_mov3 = ML_W * ml3_p + PWR_W * mov_p

    oos_rows = [
        _metrics(y_oos, nn_p,    "NN only"),
        _metrics(y_oos, xgb_p,   "XGBoost only"),
        _metrics(y_oos, lr_p,    "LR only"),
        _metrics(y_oos, ml2_p,   f"NN+XGB blend (NN={nn_w2:.0%}/XGB={xg_w2:.0%})"),
        _metrics(y_oos, ml3_p,   f"NN+XGB+LR blend"),
        _metrics(y_oos, elo_p,   "Elo power only"),
        _metrics(y_oos, mov_p,   "MOV power only"),
        _metrics(y_oos, blend_elo2, f"NN+XGB+Elo (ML={ML_W:.0%}/Elo={PWR_W:.0%})"),
        _metrics(y_oos, blend_elo3, f"NN+XGB+LR+Elo (ML={ML_W:.0%}/Elo={PWR_W:.0%})"),
        _metrics(y_oos, blend_mov2, f"NN+XGB+MOV (ML={ML_W:.0%}/MOV={PWR_W:.0%})"),
        _metrics(y_oos, blend_mov3, f"NN+XGB+LR+MOV (ML={ML_W:.0%}/MOV={PWR_W:.0%})"),
    ]
    _print_table(oos_rows, f"2025 Full Season (OUT-OF-SAMPLE) -- {len(oos)} games")

    # ------------------------------------------------------------------ #
    # 4B. Year-by-year breakdown
    # ------------------------------------------------------------------ #
    print(f"\n  Year-by-year breakdown ({args.start_season}-2025)")
    print(f"  {'Season':<8} {'N':>4}  {'NN':>6}  {'XGB':>6}  {'LR':>6}  "
          f"{'NN+XGB':>7}  {'3-model':>7}  {'Elo':>6}  "
          f"{'ML2+Elo':>8}  {'ML3+Elo':>8}  {'OOS':>5}")
    print("  " + "-" * 96)

    for season in range(args.start_season, 2026):
        sub = ft[ft["season"] == season]
        if sub.empty:
            continue
        y   = sub[LABEL_COLUMN].values.astype(float)
        nn_s  = sub["nn_prob"].values
        xg_s  = sub["xgb_prob"].values
        lr_s  = sub["lr_prob"].values
        elo_s = sub["elo_prob"].values
        ml2_s = nn_w2*nn_s + xg_w2*xg_s
        ml3_s = nn_w3*nn_s + xg_w3*xg_s + lr_w3*lr_s
        be2_s = ML_W * ml2_s + PWR_W * elo_s
        be3_s = ML_W * ml3_s + PWR_W * elo_s
        ybin  = (y > 0.5).astype(int)

        def acc(p): return accuracy_score(ybin, (p>0.5).astype(int))

        oos_label = "(OOS)" if season == 2025 else "     "
        print(f"  {season:<8} {len(sub):>4}  {acc(nn_s):>6.3f}  {acc(xg_s):>6.3f}  "
              f"{acc(lr_s):>6.3f}  {acc(ml2_s):>7.3f}  {acc(ml3_s):>7.3f}  "
              f"{acc(elo_s):>6.3f}  {acc(be2_s):>8.3f}  {acc(be3_s):>8.3f}  {oos_label}")

    # In-sample aggregate
    insample = ft[ft["season"].between(args.start_season, 2024)]
    y_in = insample[LABEL_COLUMN].values.astype(float)
    ybin_in = (y_in > 0.5).astype(int)
    nn_in  = insample["nn_prob"].values
    xg_in  = insample["xgb_prob"].values
    lr_in  = insample["lr_prob"].values
    elo_in = insample["elo_prob"].values
    ml2_in = nn_w2*nn_in + xg_w2*xg_in
    ml3_in = nn_w3*nn_in + xg_w3*xg_in + lr_w3*lr_in
    be2_in = ML_W * ml2_in + PWR_W * elo_in
    be3_in = ML_W * ml3_in + PWR_W * elo_in

    def acc_in(p): return accuracy_score(ybin_in, (p>0.5).astype(int))
    print("  " + "-" * 96)
    print(f"  {'AVG in-sample':<8} {len(insample):>4}  {acc_in(nn_in):>6.3f}  "
          f"{acc_in(xg_in):>6.3f}  {acc_in(lr_in):>6.3f}  {acc_in(ml2_in):>7.3f}  "
          f"{acc_in(ml3_in):>7.3f}  {acc_in(elo_in):>6.3f}  "
          f"{acc_in(be2_in):>8.3f}  {acc_in(be3_in):>8.3f}")

    # ------------------------------------------------------------------ #
    # 4C. Elo vs MOV grid (2025 out-of-sample)
    # ------------------------------------------------------------------ #
    print(f"\n  Elo vs MOV power rating grid (2025 out-of-sample)")
    print(f"  {'Variant':<42} {'Acc':>6}  {'LogLoss':>8}  {'Brier':>7}  {'AUC':>6}")
    print("  " + "-" * 70)

    for ml_label, ml_prob in [("NN+XGB", ml2_p), ("NN+XGB+LR", ml3_p)]:
        for pwr_label, pwr in [("Elo", elo_p), ("MOV", mov_p)]:
            for ml_share in [0.30, 0.40, 0.50]:
                p_share = 1.0 - ml_share
                blended = ml_share * ml_prob + p_share * pwr
                label = f"{ml_label} {ml_share:.0%} + {pwr_label} {p_share:.0%}"
                m = _metrics(y_oos, blended, label)
                auc = f"{m['auc']:.4f}" if not np.isnan(m["auc"]) else "  n/a"
                print(f"  {m['model']:<42} {m['accuracy']:>6.4f}  "
                      f"{m['logloss']:>8.4f}  {m['brier']:>7.4f}  {auc}")
        print()

    # ------------------------------------------------------------------ #
    # 4D. LR overfitting check (log-loss gap)
    # ------------------------------------------------------------------ #
    print(f"  Overfitting check (train log-loss gap = train_LL - test_LL, closer to 0 is better)")
    print(f"  {'Model':<12} {'Train LL':>9}  {'Test LL':>8}  {'Gap':>6}")
    print("  " + "-" * 38)
    insample_all = ft[ft["season"] < 2025]
    y_tr  = insample_all[LABEL_COLUMN].values.astype(float)
    nn_tr = insample_all["nn_prob"].values
    xg_tr = insample_all["xgb_prob"].values
    lr_tr = insample_all["lr_prob"].values
    y_bin_oos = (y_oos > 0.5).astype(int)
    y_bin_tr  = (y_tr  > 0.5).astype(int)
    for label, tr_p, te_p in [("NN", nn_tr, nn_p), ("XGBoost", xg_tr, xgb_p), ("LR", lr_tr, lr_p)]:
        tr_ll = log_loss(y_bin_tr, np.clip(tr_p, 1e-7, 1-1e-7))
        te_ll = log_loss(y_bin_oos, np.clip(te_p, 1e-7, 1-1e-7))
        print(f"  {label:<12} {tr_ll:>9.4f}  {te_ll:>8.4f}  {te_ll-tr_ll:>+6.4f}")

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    best_oos = max(oos_rows, key=lambda r: r["accuracy"])
    elo_only = next(r for r in oos_rows if r["model"] == "Elo power only")
    ml3_elo  = next(r for r in oos_rows if "LR+Elo" in r["model"] or "LR+Elo" in r.get("model",""))
    ml3_elo  = oos_rows[8]  # NN+XGB+LR+Elo

    print(f"\n{'='*76}")
    print(f"  Summary (2025 out-of-sample, {len(oos)} games)")
    print(f"{'='*76}")
    print(f"  Best single configuration:  {best_oos['model']}")
    print(f"  Accuracy: {best_oos['accuracy']:.4f} | "
          f"Brier: {best_oos['brier']:.4f} | AUC: {best_oos['auc']:.4f}")
    lr_vs_nn  = oos_rows[2]["accuracy"] - oos_rows[0]["accuracy"]
    ml3_vs_m2 = oos_rows[4]["accuracy"] - oos_rows[3]["accuracy"]
    elo_vs_mov = oos_rows[5]["accuracy"] - oos_rows[6]["accuracy"]
    print(f"  LR vs NN:             {lr_vs_nn:+.4f}")
    print(f"  3-model vs 2-model:   {ml3_vs_m2:+.4f}")
    print(f"  Elo vs MOV advantage: {elo_vs_mov:+.4f} ({'Elo' if elo_vs_mov >= 0 else 'MOV'} wins)")
    print(f"{'='*76}")
    print("Done.")


if __name__ == "__main__":
    main()
