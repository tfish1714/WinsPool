"""scripts/calibrate_elo_constants.py -- Fit Elo constants from historical NFL game data.

Optimises the parameters that are currently hand-set in:
  * services/prediction_service.py  (ELO_K, ELO_HOME_ADVANTAGE, ELO_TRAVEL_PER_1000MI)
  * services/constants.py           (ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE)

Method
------
ELO_K / ELO_HOME_ADVANTAGE / ELO_TRAVEL_PER_1000MI
    Grid search followed by L-BFGS-B optimisation to minimise Brier score on
    training seasons.  The Elo simulation is fully causal (each game's
    prediction uses only prior results), so running on all games and filtering
    by season is equivalent to separate training/test runs.

SPREAD_TO_PROB_SCALE
    Maximum-likelihood fit of the logistic scale parameter:
        P(home win) = sigmoid(spread_line / scale)
    Uses only games where Vegas spread_line is available.

ELO_TO_SPREAD
    OLS regression of optimised Elo diffs (including HFA + travel) against
    Vegas spread_line, forcing the intercept to zero (equal teams -> 0 spread).

Not calibrated: ELO_BYE_BONUS -- only ~32 bye games per season, not enough
    per-season signal to separate from noise.

Data: rawdata/schedules/games.csv (nflverse, always present).

Usage
-----
    python scripts/calibrate_elo_constants.py
    python scripts/calibrate_elo_constants.py --train-through 2022 --test-from 2023
    python scripts/calibrate_elo_constants.py --min-season 2010 --train-through 2021
"""

import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"
GAMES_CSV = RAWDATA_DIR / "schedules" / "games.csv"

# -- Current values (copied from source files for the diff display) ------------
CURRENT = {
    "ELO_K":                 20.0,
    "ELO_HOME_ADVANTAGE":    48.0,
    "ELO_TRAVEL_PER_1000MI":  4.0,
    "ELO_TO_SPREAD":         25.0,
    "SPREAD_TO_PROB_SCALE":   7.5,
}

ELO_MEAN      = 1505.0
ELO_REVERSION = 1.0 / 3.0  # fraction pulled toward mean each off-season

# Stadium coordinates (same as prediction_service.py)
STADIUM_COORDS = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4010),
    "BAL": (39.2780, -76.6227),  "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528),  "CHI": (41.8623, -87.6167),
    "CIN": (39.0955, -84.5161),  "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945),  "DEN": (39.7439, -105.0201),
    "DET": (42.3400, -83.0456),  "GB":  (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107),  "IND": (39.7601, -86.1639),
    "JAX": (30.3239, -81.6373),  "KC":  (39.0489, -94.4839),
    "LA":  (33.9535, -118.3390), "LAC": (33.9535, -118.3390),
    "LV":  (36.0909, -115.1833), "MIA": (25.9580, -80.2389),
    "MIN": (44.9736, -93.2575),  "NE":  (42.0909, -71.2643),
    "NO":  (29.9511, -90.0812),  "NYG": (40.8128, -74.0742),
    "NYJ": (40.8128, -74.0742),  "PHI": (39.9008, -75.1675),
    "PIT": (40.4468, -80.0158),  "SEA": (47.5952, -122.3316),
    "SF":  (37.4033, -121.9694), "TB":  (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713),  "WAS": (38.9076, -76.8645),
    # Historical franchises
    "OAK": (37.7516, -122.2005), "SD":  (32.7831, -117.1196),
    "STL": (38.6327, -90.1887),
}


# -- Geometry -----------------------------------------------------------------

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _travel_miles(away: str, home: str) -> float:
    ac, hc = STADIUM_COORDS.get(away), STADIUM_COORDS.get(home)
    if ac is None or hc is None:
        return 0.0
    return _haversine_miles(ac[0], ac[1], hc[0], hc[1])


# -- Elo maths (mirrors prediction_service.py exactly) ------------------------

def _mov_mult(pt_diff: float, winner_elo_diff: float) -> float:
    """FiveThirtyEight margin-of-victory multiplier."""
    return math.log(max(abs(pt_diff), 1) + 1) * 2.2 / (winner_elo_diff * 0.001 + 2.2)


# -- Core simulation -----------------------------------------------------------

def simulate(
    games: pd.DataFrame,
    K: float,
    HFA: float,
    TRAVEL: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Replay the prediction_service Elo engine with given parameters.

    Returns
    -------
    home_probs : (N,) float64 -- predicted home-win probability before each game
    elo_diffs  : (N,) float64 -- pre-game adjusted Elo diff (home - away + adj)

    Both arrays are in the same row order as *games*.  Predictions are made
    from pre-game Elo ratings only, so the simulation is fully causal.
    """
    n = len(games)
    home_probs = np.empty(n, dtype=np.float64)
    elo_diffs  = np.empty(n, dtype=np.float64)

    elo: dict[str, float] = {}
    travel_cache: dict[tuple[str, str], float] = {}
    prev_season: int | None = None

    for i, row in enumerate(games.itertuples(index=False)):
        season = int(row.season)
        home   = str(row.home_team)
        away   = str(row.away_team)
        result = float(row.result)

        # Season reversion toward ELO_MEAN
        if prev_season is not None and season != prev_season:
            for t in elo:
                elo[t] = elo[t] * (1 - ELO_REVERSION) + ELO_MEAN * ELO_REVERSION
        prev_season = season

        elo.setdefault(home, ELO_MEAN)
        elo.setdefault(away, ELO_MEAN)

        key = (away, home)
        if key not in travel_cache:
            travel_cache[key] = _travel_miles(away, home)
        adj = HFA + (travel_cache[key] / 1000.0) * TRAVEL

        diff = (elo[home] - elo[away]) + adj
        prob = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

        home_probs[i] = prob
        elo_diffs[i]  = diff

        # Update Elo
        if result > 0:           # home won
            wed   = elo[home] + adj - elo[away]
            shift = K * (1.0 - prob) * _mov_mult(result, wed)
            elo[home] += shift
            elo[away] -= shift
        elif result < 0:         # away won
            wed   = elo[away] - elo[home] - adj
            shift = K * prob * _mov_mult(-result, wed)
            elo[away] += shift
            elo[home] -= shift
        # tie -> no shift

    return home_probs, elo_diffs


# -- Metrics -------------------------------------------------------------------

def brier(probs: np.ndarray, outcomes: np.ndarray, mask: np.ndarray) -> float:
    return float(np.mean((probs[mask] - outcomes[mask]) ** 2))


def log_loss(probs: np.ndarray, outcomes: np.ndarray, mask: np.ndarray) -> float:
    eps = 1e-7
    p = np.clip(probs[mask], eps, 1 - eps)
    y = outcomes[mask]
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


# -- Optimisers ----------------------------------------------------------------

def calibrate_elo_params(
    games: pd.DataFrame,
    outcomes: np.ndarray,
    train_mask: np.ndarray,
) -> tuple[float, float, float]:
    """Grid search then L-BFGS-B to minimise train Brier over (K, HFA, TRAVEL)."""

    def obj(x: list[float]) -> float:
        K, HFA, TRAVEL = float(x[0]), float(x[1]), float(x[2])
        if K <= 0 or HFA < 0 or TRAVEL < 0:
            return 1.0
        probs, _ = simulate(games, K, HFA, TRAVEL)
        return brier(probs, outcomes, train_mask)

    # Coarse grid -- find a good basin before the gradient descent
    print("    Grid search over K x HFA ... ", end="", flush=True)
    best_loss = float("inf")
    best_x0   = [CURRENT["ELO_K"], CURRENT["ELO_HOME_ADVANTAGE"],
                 CURRENT["ELO_TRAVEL_PER_1000MI"]]
    for k_try in (8, 12, 16, 20, 25, 30, 35, 40):
        for hfa_try in (15, 25, 35, 45, 55, 65, 80, 100):
            loss = obj([k_try, hfa_try, CURRENT["ELO_TRAVEL_PER_1000MI"]])
            if loss < best_loss:
                best_loss = loss
                best_x0 = [k_try, hfa_try, CURRENT["ELO_TRAVEL_PER_1000MI"]]
    print(f"best basin K={best_x0[0]}, HFA={best_x0[1]} (Brier={best_loss:.5f})")

    result = minimize(
        obj, best_x0, method="L-BFGS-B",
        bounds=[(1.0, 80.0), (0.0, 150.0), (0.0, 20.0)],
        options={"ftol": 1e-10, "gtol": 1e-7, "maxiter": 2000},
    )
    return float(result.x[0]), float(result.x[1]), float(result.x[2])


def calibrate_spread_scale(
    spread_line: np.ndarray,
    outcomes: np.ndarray,
) -> float:
    """Maximum-likelihood logistic scale for spread_line -> P(home win).

    Minimises negative log-likelihood of:
        P(home win) = 1 / (1 + exp(-spread_line / scale))

    The nflverse spread_line sign convention: positive = home favoured,
    so a home favourite has spread_line > 0 and P(home win) > 0.5.
    """
    eps = 1e-7

    def neg_ll(scale: float) -> float:
        if scale <= 0.1:
            return 1e9
        p = 1.0 / (1.0 + np.exp(-spread_line / scale))
        return float(-np.mean(
            outcomes * np.log(p + eps) + (1 - outcomes) * np.log(1 - p + eps)
        ))

    result = minimize_scalar(neg_ll, bounds=(1.0, 30.0), method="bounded")
    return float(result.x)


def calibrate_elo_to_spread(
    elo_diffs: np.ndarray,
    spread_line: np.ndarray,
) -> float:
    """OLS regression: spread_line = beta * elo_diff (zero intercept).

    Returns ELO_TO_SPREAD = 1/beta, which converts elo_diff to point spread.
    The intercept is suppressed because equal teams (elo_diff=0) should imply
    a pick-em (spread=0).
    """
    beta = float(np.dot(elo_diffs, spread_line) / np.dot(elo_diffs, elo_diffs))
    return 1.0 / beta if beta > 0 else CURRENT["ELO_TO_SPREAD"]


# -- Main ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate Elo constants from historical NFL game data"
    )
    parser.add_argument("--min-season",    type=int, default=2010,
                        help="Earliest season to include (default 2010)")
    parser.add_argument("--train-through", type=int, default=2022,
                        help="Last season used for fitting (default 2022)")
    parser.add_argument("--test-from",     type=int, default=2023,
                        help="First season used for held-out validation (default 2023)")
    args = parser.parse_args()

    W = 68
    print("=" * W)
    print("  NFL Elo Constant Calibration")
    print(f"  Train: {args.min_season}-{args.train_through}   "
          f"Test: {args.test_from}+")
    print("=" * W)

    # -- Load -----------------------------------------------------------------
    print("\n[1] Loading games ...")
    if not GAMES_CSV.exists():
        print(f"  ERROR: {GAMES_CSV} not found -- run sync_nflverse_data.py first.")
        sys.exit(1)

    raw = pd.read_csv(GAMES_CSV, low_memory=False)
    raw = raw[
        (raw["game_type"] == "REG")
        & (raw["season"] >= args.min_season)
        & raw["home_score"].notna()
        & raw["away_score"].notna()
    ].copy()
    raw["result"]   = raw["home_score"].astype(float) - raw["away_score"].astype(float)
    raw["home_win"] = (raw["result"] > 0).astype(np.float64)
    raw = raw.sort_values(["season", "week"]).reset_index(drop=True)

    outcomes    = raw["home_win"].values
    train_mask  = (raw["season"] <= args.train_through).values
    test_mask   = (raw["season"] >= args.test_from).values

    # Spread mask: valid spread_line, cap at +-25 to drop extreme/corrupt rows
    have_spread = "spread_line" in raw.columns
    if have_spread:
        spread_vals = pd.to_numeric(raw["spread_line"], errors="coerce")
        spread_mask = spread_vals.notna() & (spread_vals.abs() < 25)
        sl = spread_vals.values
    else:
        spread_mask = np.zeros(len(raw), dtype=bool)
        sl = np.zeros(len(raw))
        print("    WARNING: spread_line column not found -- skipping spread calibration")

    print(f"    {len(raw):,} completed games "
          f"({args.min_season}-{int(raw['season'].max())})")
    print(f"    {train_mask.sum():,} train / {test_mask.sum():,} test games")
    if have_spread:
        print(f"    {spread_mask.sum():,} games with usable Vegas spread_line")

    # -- Baseline -------------------------------------------------------------
    print("\n[2] Baseline with current constants ...")
    base_probs, base_diffs = simulate(
        raw,
        CURRENT["ELO_K"],
        CURRENT["ELO_HOME_ADVANTAGE"],
        CURRENT["ELO_TRAVEL_PER_1000MI"],
    )
    base_train = brier(base_probs, outcomes, train_mask)
    base_test  = brier(base_probs, outcomes, test_mask)
    base_ll    = log_loss(base_probs, outcomes, test_mask)
    print(f"    Brier  train={base_train:.5f}  test={base_test:.5f}  "
          f"log-loss(test)={base_ll:.5f}")

    # -- Optimise Elo engine params --------------------------------------------
    print("\n[3] Optimising K, HFA, TRAVEL_PER_1000MI ...")
    best_K, best_HFA, best_TRAVEL = calibrate_elo_params(raw, outcomes, train_mask)
    print(f"    K={best_K:.2f}  HFA={best_HFA:.2f}  TRAVEL={best_TRAVEL:.2f}")

    opt_probs, opt_diffs = simulate(raw, best_K, best_HFA, best_TRAVEL)
    opt_train = brier(opt_probs, outcomes, train_mask)
    opt_test  = brier(opt_probs, outcomes, test_mask)
    opt_ll    = log_loss(opt_probs, outcomes, test_mask)
    delta_brier = opt_test - base_test
    print(f"    Brier  train={opt_train:.5f}  test={opt_test:.5f}  "
          f"log-loss(test)={opt_ll:.5f}  (dtest {delta_brier:+.5f})")

    # -- Calibrate SPREAD_TO_PROB_SCALE ----------------------------------------
    best_scale = CURRENT["SPREAD_TO_PROB_SCALE"]
    if have_spread and (spread_mask & train_mask).sum() > 100:
        print("\n[4] Calibrating SPREAD_TO_PROB_SCALE from Vegas spreads ...")
        train_spread_mask = spread_mask.values & train_mask
        best_scale = calibrate_spread_scale(sl[train_spread_mask], outcomes[train_spread_mask])
        print(f"    SPREAD_TO_PROB_SCALE: {CURRENT['SPREAD_TO_PROB_SCALE']:.2f} "
              f"-> {best_scale:.2f}")

        test_spread_mask = spread_mask.values & test_mask
        if test_spread_mask.sum() > 0:
            for label, scale in [
                ("current",    CURRENT["SPREAD_TO_PROB_SCALE"]),
                ("calibrated", best_scale),
            ]:
                p  = 1.0 / (1.0 + np.exp(-sl[test_spread_mask] / scale))
                bs = float(np.mean((p - outcomes[test_spread_mask]) ** 2))
                print(f"    Vegas spread Brier ({label} scale={scale:.2f}): {bs:.5f}")
    else:
        print("\n[4] Skipped SPREAD_TO_PROB_SCALE (insufficient spread data in train window)")

    # -- Calibrate ELO_TO_SPREAD -----------------------------------------------
    best_e2s = CURRENT["ELO_TO_SPREAD"]
    if have_spread and (spread_mask & train_mask).sum() > 100:
        print("\n[5] Calibrating ELO_TO_SPREAD vs. Vegas spread_line ...")
        train_spread_mask = spread_mask.values & train_mask
        best_e2s = calibrate_elo_to_spread(
            opt_diffs[train_spread_mask],
            sl[train_spread_mask],
        )
        print(f"    ELO_TO_SPREAD: {CURRENT['ELO_TO_SPREAD']:.2f} -> {best_e2s:.2f}")

        test_spread_mask = spread_mask.values & test_mask
        if test_spread_mask.sum() > 0:
            for label, e2s in [
                ("current",    CURRENT["ELO_TO_SPREAD"]),
                ("calibrated", best_e2s),
            ]:
                pred = opt_diffs[test_spread_mask] / e2s
                mae  = float(np.mean(np.abs(sl[test_spread_mask] - pred)))
                r    = float(np.corrcoef(opt_diffs[test_spread_mask], sl[test_spread_mask])[0, 1])
                print(f"    Elo->Spread MAE ({label} ELO_TO_SPREAD={e2s:.2f}): "
                      f"{mae:.2f} pts  (r={r:.3f})")
    else:
        print("\n[5] Skipped ELO_TO_SPREAD (insufficient spread data in train window)")

    # -- Diagnostics: calibration curve ---------------------------------------
    print("\n[6] Calibration check -- predicted vs. actual win rate by probability bucket:")
    bucket_edges = np.linspace(0.35, 0.65, 7)
    print(f"    {'Pred range':<18} {'N':>6} {'Pred %':>8} {'Actual %':>9} {'diff':>7}")
    for lo, hi in zip(bucket_edges[:-1], bucket_edges[1:]):
        m = (opt_probs >= lo) & (opt_probs < hi) & test_mask
        if m.sum() < 5:
            continue
        pred_pct   = float(np.mean(opt_probs[m])) * 100
        actual_pct = float(np.mean(outcomes[m])) * 100
        print(f"    {lo:.2f}-{hi:.2f}            {m.sum():>6}  {pred_pct:>7.1f}%"
              f"  {actual_pct:>8.1f}%  {actual_pct - pred_pct:>+6.1f}%")

    # -- Summary ---------------------------------------------------------------
    print(f"\n{'=' * W}")
    print("  CALIBRATION RESULTS")
    print(f"{'=' * W}")
    print(f"  {'Parameter':<28} {'Current':>9} {'Calibrated':>11}  File")
    print(f"  {'-' * (W - 2)}")

    rows = [
        ("ELO_K",                 CURRENT["ELO_K"],                round(best_K, 1),     "prediction_service.py"),
        ("ELO_HOME_ADVANTAGE",    CURRENT["ELO_HOME_ADVANTAGE"],   round(best_HFA, 1),   "prediction_service.py"),
        ("ELO_TRAVEL_PER_1000MI", CURRENT["ELO_TRAVEL_PER_1000MI"],round(best_TRAVEL,1), "prediction_service.py"),
        ("ELO_TO_SPREAD",         CURRENT["ELO_TO_SPREAD"],         round(best_e2s, 1),   "constants.py"),
        ("SPREAD_TO_PROB_SCALE",  CURRENT["SPREAD_TO_PROB_SCALE"],  round(best_scale, 2), "constants.py"),
    ]
    for name, cur, cal, src in rows:
        flag = " " if abs(cal - cur) < 0.1 else ("^" if cal > cur else "v")
        print(f"  {flag} {name:<27} {cur:>9.2f} {cal:>11.2f}  {src}")

    delta_pct = delta_brier / base_test * 100
    print(f"\n  Win-prediction Brier (test): {base_test:.5f} -> {opt_test:.5f}"
          f"  ({delta_pct:+.2f}%)")

    print()
    print("  To apply, update services/prediction_service.py:")
    print(f"    ELO_K = {round(best_K, 1)}")
    print(f"    ELO_HOME_ADVANTAGE = {round(best_HFA, 1)}")
    print(f"    ELO_TRAVEL_PER_1000MI = {round(best_TRAVEL, 1)}")
    print()
    print("  And services/constants.py:")
    print(f"    ELO_TO_SPREAD = {round(best_e2s, 1)}")
    print(f"    SPREAD_TO_PROB_SCALE = {round(best_scale, 2)}")
    print("=" * W)


if __name__ == "__main__":
    main()
