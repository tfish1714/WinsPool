"""services/nn_projection_engine.py -- Neural Network Prediction Wrapper.

Encapsulates the high-discrimination, hybrid Monte Carlo logic from predict_2026.py
and exposes a clean API for cache_builder.py and the FastAPI backend.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from services.constants import UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT

from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
)
from services.nn_prediction_service import (
    NNPredictionService,
    FEATURE_COLUMNS as NN_FEATURE_COLUMNS,
)
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService

logger = logging.getLogger(__name__)


class NNProjectionEngine:
    """Wrapper that leverages the trained NN+XGB+LR ensemble and Monte Carlo engine for caching."""

    def __init__(self):
        self.svc = NNPredictionService()
        self.svc.load_model()
        self.xgb_svc = XGBPredictionService()
        self.xgb_svc.load_model()
        self.lr_svc = LRPredictionService()
        self.lr_svc.load_model()
        self._team_profiles = pd.DataFrame()

    def initialize(self, season: int):
        """Pre-compute the feature profiles required for predictions.

        Args:
            season: The target NFL season (e.g. 2026).
        """
        feature_table = build_master_feature_table(min_season=2020, max_season=season - 1)
        self._team_profiles = self._build_team_profiles(feature_table, season - 1)

    def _build_team_profiles(self, feature_table: pd.DataFrame, proxy_season: int) -> pd.DataFrame:
        """Build per-team average feature profiles."""
        s_proxy = feature_table[feature_table["season"] == proxy_season].copy()
        if s_proxy.empty:
            latest = feature_table["season"].max()
            s_proxy = feature_table[feature_table["season"] == latest].copy()

        home_avg = s_proxy.groupby("home_team")[NN_FEATURE_COLUMNS].mean().reset_index()
        home_avg.rename(columns={"home_team": "team"}, inplace=True)

        away_avg = s_proxy.groupby("away_team")[NN_FEATURE_COLUMNS].mean().reset_index()
        away_avg.rename(columns={"away_team": "team"}, inplace=True)

        combined = pd.concat([home_avg, away_avg], ignore_index=True)
        return combined.groupby("team")[NN_FEATURE_COLUMNS].mean().reset_index()

    def game_win_probability(self, home_team: str, away_team: str) -> dict:
        """Compute the blended NN+XGB+LR ensemble probability.

        Returns:
            Dict containing 'home_win_prob' and model-level probabilities.
        """
        profile_dict = {}
        for _, row in self._team_profiles.iterrows():
            profile_dict[row["team"]] = {c: row[c] for c in NN_FEATURE_COLUMNS}

        hp = profile_dict.get(home_team, {})
        ap = profile_dict.get(away_team, {})

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

        nn_prob  = self.svc.predict_game(features)
        xgb_prob = self.xgb_svc.predict_game(features)
        lr_prob  = self.lr_svc.predict_game(features)

        blended = float(np.clip(
            NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
            0.02, 0.98,
        ))

        return {
            "home_team": home_team,
            "away_team": away_team,
            "home_win_prob": round(blended, 4),
            "away_win_prob": round(1.0 - blended, 4),
            "nn_home_prob":  round(float(nn_prob),  4),
            "xgb_home_prob": round(float(xgb_prob), 4),
            "lr_home_prob":  round(float(lr_prob),  4),
        }

    def _run_monte_carlo(self, game_probs: list, all_teams: list, n_sims: int) -> dict:
        """Execute Monte Carlo season simulation over pre-computed game probabilities."""
        team_idx = {t: i for i, t in enumerate(all_teams)}
        n_teams = len(all_teams)
        win_matrix = np.zeros((n_sims, n_teams), dtype=np.float32)

        home_indices = np.array([team_idx[g[0]] for g in game_probs if g[0] in team_idx and g[1] in team_idx])
        away_indices = np.array([team_idx[g[1]] for g in game_probs if g[0] in team_idx and g[1] in team_idx])
        probs = np.array([g[2] for g in game_probs if g[0] in team_idx and g[1] in team_idx])

        rng = np.random.default_rng(seed=42)
        random_draws = rng.random((n_sims, len(probs)))
        home_wins = (random_draws < probs).astype(np.float32)

        for g_idx in range(len(probs)):
            win_matrix[:, home_indices[g_idx]] += home_wins[:, g_idx]
            win_matrix[:, away_indices[g_idx]] += (1.0 - home_wins[:, g_idx])
            
        return team_idx, win_matrix

    def get_team_projected_wins(self, schedule_df: pd.DataFrame, n_sims: int = 5000) -> Dict[str, float]:
        """Produce season win totals via Monte Carlo simulation for Draft logic.
        
        Args:
            schedule_df: The full season schedule to simulate.
            n_sims: Number of Monte Carlo trials.
            
        Returns:
            Dict mapping team abbreviation to projected wins (float).
        """
        # Filter schedule for regular season
        reg = schedule_df[schedule_df["game_type"] == "REG"] if "game_type" in schedule_df.columns else schedule_df
        
        if reg.empty:
            # Fallback: return equal projections when no schedule is available
            all_teams = ["ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB","HOU","IND","JAX","KC","LV","LAC","LA","MIA","MIN","NE","NO","NYG","NYJ","PHI","PIT","SF","SEA","TB","TEN","WAS"]
            return {t: 8.5 for t in all_teams}

        game_probs = []
        for _, game in reg.iterrows():
            ht = game["home_team"]
            at = game["away_team"]
            prob_dict = self.game_win_probability(ht, at)
            game_probs.append((ht, at, prob_dict["home_win_prob"]))

        all_teams = sorted(list(set([g[0] for g in game_probs] + [g[1] for g in game_probs])))
        team_idx, win_matrix = self._run_monte_carlo(game_probs, all_teams, n_sims)

        results = {}
        for team in all_teams:
            median_wins = float(np.median(win_matrix[:, team_idx[team]]))
            results[team] = round(median_wins, 1)

        return results

    def project_portfolio_wins(
        self, team_ids: List[str], schedule_df: pd.DataFrame, n_sims: int = 500
    ) -> dict:
        """Simulate cumulative wins for a player's portfolio of teams."""
        reg = schedule_df[schedule_df["game_type"] == "REG"] if "game_type" in schedule_df.columns else schedule_df
        
        game_probs = []
        for _, game in reg.iterrows():
            ht = game["home_team"]
            at = game["away_team"]
            
            # If game is already played, probability is 1.0 or 0.0 based on result.
            # (Assuming missing 'result' column means unplayed for Monte Carlo)
            res = game.get("result", np.nan)
            if pd.notna(res) and res != UNDRAFTED_SENTINEL:
                prob = 1.0 if res > 0 else 0.0
            else:
                prob_dict = self.game_win_probability(ht, at)
                prob = prob_dict["home_win_prob"]
                
            game_probs.append((ht, at, prob))

        all_teams = sorted(list(set([g[0] for g in game_probs] + [g[1] for g in game_probs])))
        team_idx, win_matrix = self._run_monte_carlo(game_probs, all_teams, n_sims)

        # Sum portfolio wins across trials
        portfolio_indices = [team_idx[t] for t in team_ids if t in team_idx]
        if not portfolio_indices:
            return {"mean_wins": 0.0, "std_wins": 0.0, "min_wins": 0.0, "max_wins": 0.0}

        portfolio_wins_per_trial = win_matrix[:, portfolio_indices].sum(axis=1)

        mean_wins = float(np.mean(portfolio_wins_per_trial))
        std_wins = float(np.std(portfolio_wins_per_trial))
        min_wins = float(np.min(portfolio_wins_per_trial))
        max_wins = float(np.max(portfolio_wins_per_trial))

        per_team = {}
        for t in team_ids:
            if t in team_idx:
                per_team[t] = round(float(np.mean(win_matrix[:, team_idx[t]])), 2)

        return {
            "mean_wins": round(mean_wins, 2),
            "std_wins": round(std_wins, 2),
            "min_wins": round(min_wins, 2),
            "max_wins": round(max_wins, 2),
            "projected_additional": per_team,
            "simulations": n_sims,
            "season_complete": False,
        }

def enrich_schedule_with_nn_predictions(
    schedule_df: pd.DataFrame,
    season: int,
) -> pd.DataFrame:
    """Add game-level win predictions to an enriched schedule DataFrame using NN rules.

    Args:
        schedule_df: The enriched schedule DataFrame from analysis_service.
        season: The target season year.
    """
    engine = NNProjectionEngine()
    engine.initialize(season)

    pred_winners = []
    pred_confs = []
    pred_ats = []

    for _, row in schedule_df.iterrows():
        result = row.get("result")
        home = row.get("home_team", "")
        away = row.get("away_team", "")

        is_unplayed = (pd.isna(result) or result == UNDRAFTED_SENTINEL)
        if not is_unplayed or not home or not away:
            pred_winners.append(None)
            pred_confs.append(None)
            pred_ats.append(None)
            continue

        prediction = engine.game_win_probability(home, away)
        home_prob = prediction["home_win_prob"]

        if home_prob >= 0.5:
            winner = home
            confidence = home_prob
        else:
            winner = away
            confidence = 1.0 - home_prob

        # Clamp confidence to 50-99%
        conf_pct = round(min(99.0, max(50.0, confidence * 100)), 1)

        # ATS pick
        spread = row.get("spread_line")
        if pd.notna(spread):
            try:
                spread_val = float(spread)
                if abs(spread_val) <= 3:
                    ats = away if spread_val < 0 else home
                else:
                    ats = winner
            except (ValueError, TypeError):
                ats = winner
        else:
            ats = winner

        pred_winners.append(winner)
        pred_confs.append(conf_pct)
        pred_ats.append(ats)

    schedule_df = schedule_df.copy()
    schedule_df["pred_winner"] = pred_winners
    schedule_df["pred_su_conf"] = pred_confs
    schedule_df["pred_ats_pick"] = pred_ats

    return schedule_df
