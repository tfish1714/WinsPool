"""scripts/predict_2026.py -- Generate 2026 NFL season projections.

Uses a hybrid approach:
  1. The NN model provides per-matchup base probabilities
  2. Team power ratings (point differential per game) amplify differentiation
  3. A Monte Carlo simulation (10,000 trials) produces realistic distributions

The power-rating blending overcomes the sigmoid compression issue where
season-averaged features produce probabilities too close to 0.5.

Usage:
    python scripts/predict_2026.py
    python scripts/predict_2026.py --simulations 50000
"""

import sys
import os
import argparse
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

import numpy as np
import pandas as pd

from services.nn_feature_engine import (
    build_master_feature_table,
    _read_csv_safe,
    _normalize_team,
    RAWDATA_DIR,
)
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS as NN_FEATURE_COLUMNS,
)


ALL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
    "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
    "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
]

N_SIMULATIONS = 10_000
NN_WEIGHT = 0.40  # Weight given to the NN probability
POWER_WEIGHT = 0.60  # Weight given to the power-rating probability
HOME_ADVANTAGE_PTS = 2.5  # Standard NFL home-field advantage in points


# ---------------------------------------------------------------------------
# Feature Profile Construction
# ---------------------------------------------------------------------------

def _build_team_profiles(feature_table: pd.DataFrame) -> pd.DataFrame:
    """Build per-team average feature profiles from the latest season."""
    s2025 = feature_table[feature_table["season"] == 2025].copy()
    if s2025.empty:
        latest = feature_table["season"].max()
        s2025 = feature_table[feature_table["season"] == latest].copy()

    home_avg = s2025.groupby("home_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    home_avg.rename(columns={"home_team": "team"}, inplace=True)

    away_avg = s2025.groupby("away_team")[NN_FEATURE_COLUMNS].mean().reset_index()
    away_avg.rename(columns={"away_team": "team"}, inplace=True)

    combined = pd.concat([home_avg, away_avg], ignore_index=True)
    return combined.groupby("team")[NN_FEATURE_COLUMNS].mean().reset_index()


def _load_proxy_schedule(rawdata_dir: pathlib.Path) -> pd.DataFrame:
    """Load the 2025 regular season schedule as a proxy for 2026 matchups."""
    path = rawdata_dir / "schedules" / "games.csv"
    if not path.exists():
        return pd.DataFrame()

    df = _read_csv_safe(str(path))
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)

    reg = df[(df["season"] == 2025) & (df["game_type"] == "REG")].copy()
    if reg.empty:
        reg = df[(df["season"] == 2024) & (df["game_type"] == "REG")].copy()

    return reg[["season", "week", "home_team", "away_team"]].copy()


def _compute_team_power_ratings(rawdata_dir: pathlib.Path) -> dict:
    """Compute per-team power ratings from actual game results.

    Power rating = average point margin per game, blending home and away.
    This captures the true gap between strong and weak teams that the NN's
    season-averaged features fail to differentiate.

    Returns:
        Dict mapping team abbreviation to power rating (float).
    """
    path = rawdata_dir / "schedules" / "games.csv"
    df = _read_csv_safe(str(path))
    df["home_team"] = df["home_team"].apply(_normalize_team)
    df["away_team"] = df["away_team"].apply(_normalize_team)
    df["home_score"] = pd.to_numeric(df["home_score"], errors="coerce")
    df["away_score"] = pd.to_numeric(df["away_score"], errors="coerce")

    # Use latest available season
    for season in [2025, 2024]:
        reg = df[(df["season"] == season) & (df["game_type"] == "REG")].dropna(
            subset=["home_score", "away_score"]
        )
        if not reg.empty:
            break

    reg["margin"] = reg["home_score"] - reg["away_score"]

    home_power = reg.groupby("home_team")["margin"].mean()
    away_power = reg.groupby("away_team")["margin"].apply(lambda x: -x.mean())

    # Blend home and away perspectives
    all_teams_set = set(home_power.index) | set(away_power.index)
    power = {}
    for team in all_teams_set:
        hp = home_power.get(team, 0.0)
        ap = away_power.get(team, 0.0)
        power[team] = (hp + ap) / 2.0

    return power


def _power_to_probability(home_power: float, away_power: float) -> float:
    """Convert a power rating gap into a win probability using the logistic function.

    The standard conversion: each point of spread corresponds to roughly
    3% win probability shift from 50%. We use the logistic function for
    a smooth, bounded conversion.

    Args:
        home_power: Home team's power rating.
        away_power: Away team's power rating.

    Returns:
        Home team win probability in (0, 1).
    """
    spread = (home_power - away_power) + HOME_ADVANTAGE_PTS
    # NFL spread-to-probability: sigma(spread / 6.5)
    # A 13-point spread = ~88% win probability (realistic for NFL)
    prob = 1.0 / (1.0 + np.exp(-spread / 6.5))
    return float(np.clip(prob, 0.02, 0.98))


# ---------------------------------------------------------------------------
# Monte Carlo Season Simulation
# ---------------------------------------------------------------------------

def _compute_game_probabilities(
    svc: NNPredictionService,
    team_profiles: pd.DataFrame,
    schedule: pd.DataFrame,
    power_ratings: dict,
) -> list:
    """Compute blended win probabilities for every scheduled game.

    Blends:
      - NN model probability (captures feature interactions)
      - Power-rating probability (captures team quality gap)

    Returns:
        List of tuples: (home_team, away_team, blended_probability)
    """
    profile_dict = {}
    for _, row in team_profiles.iterrows():
        profile_dict[row["team"]] = {c: row[c] for c in NN_FEATURE_COLUMNS}

    game_probs = []
    for _, game in schedule.iterrows():
        ht = game["home_team"]
        at = game["away_team"]

        if ht not in profile_dict or at not in profile_dict:
            continue

        hp = profile_dict[ht]
        ap = profile_dict[at]

        # NN probability
        features = {}
        for col in NN_FEATURE_COLUMNS:
            if col == "home_flag":
                features[col] = 1.0
            elif col in (
                "roster_talent_delta", "star_qb_value_delta",
                "trench_dominance_metric", "net_success_rate",
                "high_leverage_regression_flag",
            ):
                features[col] = hp.get(col, 0.0) - ap.get(col, 0.0)
            elif col.startswith("def_"):
                off_equiv = col.replace("def_", "off_")
                features[col] = ap.get(off_equiv, ap.get(col, 0.0))
            elif col.startswith("opp_"):
                raw = col.replace("opp_", "tm_")
                features[col] = ap.get(raw, ap.get(col, 0.0))
            elif col == "vegas_elo_spread_delta":
                elo_diff = hp.get("tm_elo_pre", 1500) - ap.get("tm_elo_pre", 1500)
                spread = hp.get("spread_line", 0)
                features[col] = abs((elo_diff / 25.0) - spread)
            elif col == "market_implied_team_total":
                features[col] = hp.get(col, 22.0)
            else:
                features[col] = hp.get(col, 0.0)

        nn_prob = svc.predict_game(features)

        # Power-rating probability
        h_power = power_ratings.get(ht, 0.0)
        a_power = power_ratings.get(at, 0.0)
        pwr_prob = _power_to_probability(h_power, a_power)

        # Weighted blend
        blended = NN_WEIGHT * nn_prob + POWER_WEIGHT * pwr_prob
        blended = float(np.clip(blended, 0.02, 0.98))

        game_probs.append((ht, at, blended))

    return game_probs


def _run_monte_carlo(
    game_probs: list,
    all_teams: list,
    n_simulations: int = N_SIMULATIONS,
) -> dict:
    """Run Monte Carlo simulation of the full season.

    For each trial, every game outcome is resolved as a Bernoulli trial
    using the blended win probability as the success rate.

    Returns:
        Dict mapping team -> stats dict (median, mean, percentiles).
    """
    team_idx = {t: i for i, t in enumerate(all_teams)}
    n_teams = len(all_teams)

    win_matrix = np.zeros((n_simulations, n_teams), dtype=np.float32)

    home_indices = np.array([team_idx[g[0]] for g in game_probs if g[0] in team_idx and g[1] in team_idx])
    away_indices = np.array([team_idx[g[1]] for g in game_probs if g[0] in team_idx and g[1] in team_idx])
    probs = np.array([g[2] for g in game_probs if g[0] in team_idx and g[1] in team_idx])

    rng = np.random.default_rng(seed=42)
    random_draws = rng.random((n_simulations, len(probs)))
    home_wins = (random_draws < probs).astype(np.float32)

    for g_idx in range(len(probs)):
        win_matrix[:, home_indices[g_idx]] += home_wins[:, g_idx]
        win_matrix[:, away_indices[g_idx]] += (1.0 - home_wins[:, g_idx])

    results = {}
    for team in all_teams:
        if team not in team_idx:
            continue
        idx = team_idx[team]
        team_wins = win_matrix[:, idx]
        results[team] = {
            "median_wins": float(np.median(team_wins)),
            "mean_wins": float(np.mean(team_wins)),
            "p5": float(np.percentile(team_wins, 5)),
            "p25": float(np.percentile(team_wins, 25)),
            "p75": float(np.percentile(team_wins, 75)),
            "p95": float(np.percentile(team_wins, 95)),
        }

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate 2026 NFL season win projections (Monte Carlo)"
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="Path to trained .keras model (default: models/nn_v1.keras)"
    )
    parser.add_argument(
        "--simulations", type=int, default=N_SIMULATIONS,
        help=f"Number of Monte Carlo trials (default: {N_SIMULATIONS})"
    )
    args = parser.parse_args()

    print("=" * 72)
    print("  NFL Neural Network -- 2026 Season Projections (Monte Carlo)")
    print("=" * 72)

    # Load model
    print("\n[1/6] Loading trained model...")
    svc = NNPredictionService()
    svc.load_model(args.model)

    # Build feature table baseline
    print("[2/6] Building 2026 feature baseline from 2025 data...")
    feature_table = build_master_feature_table(min_season=2020, max_season=2025)
    team_profiles = _build_team_profiles(feature_table)
    print(f"  {len(team_profiles)} teams with feature profiles.")

    # Compute power ratings
    print("[3/6] Computing team power ratings (point differential)...")
    power_ratings = _compute_team_power_ratings(RAWDATA_DIR)
    top = sorted(power_ratings.items(), key=lambda x: -x[1])
    print(f"  Top 3: {top[0][0]} ({top[0][1]:+.1f}), {top[1][0]} ({top[1][1]:+.1f}), {top[2][0]} ({top[2][1]:+.1f})")
    print(f"  Bot 3: {top[-1][0]} ({top[-1][1]:+.1f}), {top[-2][0]} ({top[-2][1]:+.1f}), {top[-3][0]} ({top[-3][1]:+.1f})")

    # Load proxy schedule
    print("[4/6] Loading 2025 schedule as 2026 proxy...")
    schedule = _load_proxy_schedule(RAWDATA_DIR)
    print(f"  {len(schedule)} regular season games loaded.")

    # Pre-compute blended game probabilities
    print(f"[5/6] Computing blended probabilities (NN:{NN_WEIGHT:.0%} + Power:{POWER_WEIGHT:.0%})...")
    game_probs = _compute_game_probabilities(svc, team_profiles, schedule, power_ratings)
    probs_only = [p for _, _, p in game_probs]
    print(f"  {len(game_probs)} matchups | prob range: [{min(probs_only):.3f}, {max(probs_only):.3f}]")

    # Run Monte Carlo
    n_sims = args.simulations
    print(f"[6/6] Running Monte Carlo simulation ({n_sims:,} trials)...")
    teams_in_data = sorted(set(
        [g[0] for g in game_probs] + [g[1] for g in game_probs]
    ))
    mc_results = _run_monte_carlo(game_probs, teams_in_data, n_sims)

    # Build output table
    projections = []
    for team, stats in mc_results.items():
        projections.append({
            "team": team,
            "proj_wins": round(stats["median_wins"], 1),
            "mean_wins": round(stats["mean_wins"], 1),
            "floor": round(stats["p5"], 1),
            "p25": round(stats["p25"], 1),
            "p75": round(stats["p75"], 1),
            "ceiling": round(stats["p95"], 1),
            "power": round(power_ratings.get(team, 0.0), 1),
        })

    projections.sort(key=lambda x: x["proj_wins"], reverse=True)

    # Print table
    print(f"\n{'Rk':<4}{'Team':<6}{'Proj':<6}{'Mean':<6}"
          f"{'Floor':<7}{'25th':<6}{'75th':<6}{'Ceil':<6}{'Pwr':<6}")
    print("-" * 53)
    for i, p in enumerate(projections, 1):
        print(f"{i:<4}{p['team']:<6}{p['proj_wins']:<6.1f}{p['mean_wins']:<6.1f}"
              f"{p['floor']:<7.1f}{p['p25']:<6.1f}{p['p75']:<6.1f}"
              f"{p['ceiling']:<6.1f}{p['power']:+5.1f}")

    print(f"\n{'='*53}")
    total = sum(p["proj_wins"] for p in projections)
    print(f"  {len(projections)} teams | {n_sims:,} simulations")
    print(f"  Total median wins: {total:.0f}")
    print(f"  Spread: {min(p['proj_wins'] for p in projections):.1f} - "
          f"{max(p['proj_wins'] for p in projections):.1f}")
    print(f"{'='*53}")


if __name__ == "__main__":
    main()
