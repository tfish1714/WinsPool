"""services/nn_projection_engine.py -- Neural Network Prediction Wrapper.

Encapsulates the high-discrimination, hybrid Monte Carlo logic from predict_2026.py
and exposes a clean API for cache_builder.py and the FastAPI backend.
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from services.constants import (
    UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT,
    PROB_CLIP_MIN, PROB_CLIP_MAX, ELO_TO_SPREAD, SPREAD_TO_PROB_SCALE,
    MC_MARGIN_STD, MC_EPA_SCALE, MC_EPA_RUSH_WEIGHT,
    PRESEASON_ELO_BOOST_MAX, PRESEASON_ELO_WEIGHTS,
)

from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
    compute_preseason_roster_features,
    compute_preseason_player_profiles,
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
        self._preseason_profiles: dict = {}  # {team: {off_pass_epa, off_rush_epa, ...}}
        # Legacy attributes kept as empty defaults so _precompute_static_features fallback
        # doesn't AttributeError on older code paths
        self._preseason_roster: dict = {}
        self._preseason_norm: tuple | None = None

    def initialize(self, season: int):
        """Pre-compute the feature profiles required for predictions.

        Args:
            season: The target NFL season (e.g. 2026).
        """
        feature_table = build_master_feature_table(min_season=2020, max_season=season - 1)
        self._team_profiles = self._build_team_profiles(feature_table, season - 1)

        snap_path = RAWDATA_DIR / "snap_counts" / f"snap_counts_{season}.csv"
        snap_empty = not snap_path.exists() or pd.read_csv(snap_path, nrows=1).empty
        if snap_empty:
            try:
                self._preseason_profiles = compute_preseason_player_profiles(season, RAWDATA_DIR)
                if self._preseason_profiles:
                    logger.info(
                        "Preseason player profiles built for %d teams (season %d)",
                        len(self._preseason_profiles), season,
                    )
            except Exception as exc:
                logger.warning("Preseason player profile build failed: %s", exc)
                self._preseason_profiles = {}

    def _build_team_profiles(self, feature_table: pd.DataFrame, proxy_season: int) -> pd.DataFrame:
        """Build per-team average feature profiles from the proxy season.

        Each team gets one row with:
        - averages of all FEATURE_COLUMNS (for fallback)
        - aux columns: elo_pre, per-team off/def EPA rolls, margin_roll, pressure rolls, trench_score
        """
        s_proxy = feature_table[feature_table["season"] == proxy_season].copy()
        if s_proxy.empty:
            latest = feature_table["season"].max()
            s_proxy = feature_table[feature_table["season"] == latest].copy()

        # Home appearances: team = home_team, aux cols prefixed h_ or home_
        home_rename = {
            "home_team": "team",
            "home_elo_pre": "elo_pre",
            "home_trench_score": "trench_score",
            "home_margin_roll": "margin_roll",
            "home_qb_pressure_roll": "qb_pressure_roll",
            "home_def_pressures_roll": "def_pressures_roll",
            "h_off_pass": "off_pass_epa_roll", "h_off_rush": "off_rush_epa_roll",
            "h_off_early": "off_early_roll",
            "h_def_pass": "def_pass_epa_roll", "h_def_rush": "def_rush_epa_roll",
            "h_def_early": "def_early_roll",
        }
        away_rename = {
            "away_team": "team",
            "away_elo_pre": "elo_pre",
            "away_trench_score": "trench_score",
            "away_margin_roll": "margin_roll",
            "away_qb_pressure_roll": "qb_pressure_roll",
            "away_def_pressures_roll": "def_pressures_roll",
            "a_off_pass": "off_pass_epa_roll", "a_off_rush": "off_rush_epa_roll",
            "a_off_early": "off_early_roll",
            "a_def_pass": "def_pass_epa_roll", "a_def_rush": "def_rush_epa_roll",
            "a_def_early": "def_early_roll",
        }

        aux_target_cols = list(set(home_rename.values()) - {"team"})
        profile_cols = NN_FEATURE_COLUMNS + [c for c in aux_target_cols if c not in NN_FEATURE_COLUMNS]

        def _extract_side(rename_map, role_col):
            available = {k: v for k, v in rename_map.items() if k in s_proxy.columns}
            sub = s_proxy.rename(columns=available)
            if "team" not in sub.columns and role_col in sub.columns:
                sub = sub.rename(columns={role_col: "team"})
            available_profile = [c for c in profile_cols if c in sub.columns]
            if not available_profile:
                return pd.DataFrame()
            return sub.groupby("team")[available_profile].mean().reset_index()

        home_avg = _extract_side(home_rename, "home_team")
        away_avg = _extract_side(away_rename, "away_team")

        combined = pd.concat([home_avg, away_avg], ignore_index=True)
        avail = [c for c in profile_cols if c in combined.columns]
        return combined.groupby("team")[avail].mean().reset_index()

    def _build_initial_state(self) -> tuple:
        """Extract per-team absolute state from loaded team profiles.

        Returns:
            state_template: float32 array of shape (n_teams, 6).
                            Dims: [elo, off_pass_epa, off_rush_epa,
                                    def_pass_epa, def_rush_epa, margin_roll]
            team_list: sorted list of team abbreviations.
            team_idx: {team: index into state_template}.
        """
        profile_dict = {row["team"]: row.to_dict() for _, row in self._team_profiles.iterrows()}
        team_list = sorted(profile_dict.keys())
        team_idx = {t: i for i, t in enumerate(team_list)}

        state_template = np.zeros((len(team_list), 6), dtype=np.float32)
        for team, idx in team_idx.items():
            p = profile_dict[team]
            state_template[idx, 0] = float(p.get("elo_pre",           1500.0))
            state_template[idx, 1] = float(p.get("off_pass_epa_roll",    0.0))
            state_template[idx, 2] = float(p.get("off_rush_epa_roll",    0.0))
            state_template[idx, 3] = float(p.get("def_pass_epa_roll",    0.0))
            state_template[idx, 4] = float(p.get("def_rush_epa_roll",    0.0))
            state_template[idx, 5] = float(p.get("margin_roll",          0.0))

        # Override EPA dims 1-4 with bottom-up preseason player profiles when available
        if self._preseason_profiles:
            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                if pp:
                    state_template[idx, 1] = float(pp.get("off_pass_epa", state_template[idx, 1]))
                    state_template[idx, 2] = float(pp.get("off_rush_epa", state_template[idx, 2]))
                    state_template[idx, 3] = float(pp.get("def_pass_epa", state_template[idx, 3]))
                    state_template[idx, 4] = float(pp.get("def_rush_epa", state_template[idx, 4]))

            # Profile composite → Elo boost: widen preseason spread to match SB odds.
            # Algorithm: z-score each profile dim across all teams, flip sign for
            # defensive dims (lower EPA allowed = better), compute weighted composite,
            # clip to ±2σ and scale to ±PRESEASON_ELO_BOOST_MAX.
            _dims  = list(PRESEASON_ELO_WEIGHTS.keys())
            _def_d = {"def_pass_epa", "def_rush_epa"}
            _vals  = {
                d: [float(self._preseason_profiles.get(t, {}).get(d, 0.0)) for t in team_list]
                for d in _dims
            }
            _mu  = {d: float(np.mean(v)) for d, v in _vals.items()}
            _sig = {d: max(float(np.std(v)), 1e-6) for d, v in _vals.items()}

            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                composite = 0.0
                for d, w in PRESEASON_ELO_WEIGHTS.items():
                    z = (float(pp.get(d, _mu[d])) - _mu[d]) / _sig[d]
                    if d in _def_d:
                        z = -z
                    composite += w * z
                elo_adj = float(np.clip(composite, -2.0, 2.0) / 2.0 * PRESEASON_ELO_BOOST_MAX)
                state_template[idx, 0] += elo_adj

        return state_template, team_list, team_idx

    def _precompute_static_features(self, schedule_df: pd.DataFrame) -> dict:
        """Build the time-invariant portion of the feature vector for each game.

        The 5 dynamic features (elo_diff, elo_confidence, pass_epa_matchup,
        rush_epa_matchup, point_diff_advantage) are left at 0.0 and overwritten
        per-trial inside simulate_season().

        Returns:
            {game_key: float32 array of shape (n_features,)}
        """
        from services.nn_feature_engine import _normalize_team, FEATURE_COLUMNS as NN_FC
        from services.prediction_service import _get_travel_distance

        profile_dict = {row["team"]: row.to_dict() for _, row in self._team_profiles.iterrows()}
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        static_feats = {}

        for _, game in schedule_df.iterrows():
            ht = _normalize_team(str(game.get("home_team", "") or ""))
            at = _normalize_team(str(game.get("away_team", "") or ""))
            wk = game.get("week")
            if not ht or not at or wk is None:
                continue

            key = f"W{int(wk):02d}_{ht}_{at}"
            hp = profile_dict.get(ht, {})
            ap = profile_dict.get(at, {})

            feat = np.zeros(len(NN_FC), dtype=np.float32)

            # Game-context static values
            feat[col_idx["home_field_advantage"]]   = 1.0
            feat[col_idx["rest_advantage"]]         = 0.0
            feat[col_idx["home_qb_injury_flag"]]    = 0.0
            feat[col_idx["away_qb_injury_flag"]]    = 0.0
            feat[col_idx["playoff_flag"]]           = 0.0
            feat[col_idx["week"]]                   = float(wk)
            feat[col_idx["div_game_flag"]]          = float(game.get("div_game", 0) or 0)
            feat[col_idx["surface_type"]]           = float(game.get("surface_type", 0) or 0)

            # Travel (away team perspective)
            try:
                feat[col_idx["net_travel_disadvantage"]] = _get_travel_distance(at, ht) / 1000.0
            except Exception:
                pass

            # Team matchup features from profiles (static — prior-season baseline)
            feat[col_idx["market_implied_team_total"]]  = float(hp.get("market_implied_team_total", 22.0))
            feat[col_idx["passing_difficulty_index"]]   = float(hp.get("passing_difficulty_index", 0.0))
            feat[col_idx["early_down_matchup"]]         = (
                float(hp.get("off_early_roll", 0.0)) - float(ap.get("def_early_roll", 0.0))
                - float(ap.get("off_early_roll", 0.0)) + float(hp.get("def_early_roll", 0.0))
            )
            feat[col_idx["turnover_margin_rolling"]]    = (
                float(hp.get("turnover_margin_rolling", 0.0)) - float(ap.get("turnover_margin_rolling", 0.0))
            )
            feat[col_idx["net_success_rate"]]           = (
                float(hp.get("net_success_rate", 0.0)) - float(ap.get("net_success_rate", 0.0))
            )
            feat[col_idx["qb_pressure_advantage"]]      = (
                float(ap.get("qb_pressure_roll", 0.0)) - float(hp.get("qb_pressure_roll", 0.0))
            )
            feat[col_idx["def_pressure_diff"]]          = (
                float(hp.get("def_pressures_roll", 0.0)) - float(ap.get("def_pressures_roll", 0.0))
            )

            # Trench: preseason player profiles → legacy roster → profile average
            if self._preseason_profiles:
                h_pr = self._preseason_profiles.get(ht, {})
                a_pr = self._preseason_profiles.get(at, {})
                if h_pr and a_pr:
                    all_ol = [v.get("ol_av", 0.0) for v in self._preseason_profiles.values()]
                    all_dl = [v.get("dl_perf", 0.0) for v in self._preseason_profiles.values()]
                    ol_mu, ol_sig = float(np.mean(all_ol)), max(float(np.std(all_ol)), 1.0)
                    dl_mu, dl_sig = float(np.mean(all_dl)), max(float(np.std(all_dl)), 1.0)
                    h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    feat[col_idx["trench_dominance_metric"]] = float(h_z - a_z)
                else:
                    feat[col_idx["trench_dominance_metric"]] = (
                        float(hp.get("trench_score", 0.0)) - float(ap.get("trench_score", 0.0))
                    )
            elif self._preseason_roster and self._preseason_norm:
                ol_mu, ol_sig, dl_mu, dl_sig = self._preseason_norm
                h_pr = self._preseason_roster.get(ht, {})
                a_pr = self._preseason_roster.get(at, {})
                h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                       + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                       + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                feat[col_idx["trench_dominance_metric"]] = float(h_z - a_z)
            else:
                feat[col_idx["trench_dominance_metric"]] = (
                    float(hp.get("trench_score", 0.0)) - float(ap.get("trench_score", 0.0))
                )

            # Roster value deltas (home-centric signed features from prior season)
            feat[col_idx["roster_talent_delta"]]     = (
                float(hp.get("roster_talent_delta", 0.0)) - float(ap.get("roster_talent_delta", 0.0))
            )
            feat[col_idx["off_roster_value_delta"]]  = float(hp.get("off_roster_value_delta", 0.0))
            feat[col_idx["def_roster_value_delta"]]  = float(hp.get("def_roster_value_delta", 0.0))
            feat[col_idx["st_value_delta"]]          = float(hp.get("st_value_delta", 0.0))
            feat[col_idx["qb_resilience_delta"]]     = float(hp.get("qb_resilience_delta", 0.0))

            # RETRAIN SPEC: Override these 5 features with preseason profile z-scores
            # (def_pressure_diff, qb_pressure_advantage, off/def_roster_value_delta,
            # roster_talent_delta) once models are retrained on profile-derived z-scores
            # as feature values for historical seasons (2020–2025). Raw cross-team z-scores
            # from _preseason_profiles can reach ±3 for outlier teams (e.g. elite DL),
            # which pushes current models out of distribution and inverts predictions.
            # The Elo boost in _build_initial_state() already widens the spread correctly.

            static_feats[key] = feat

        return static_feats

    def game_win_probability(self, home_team: str, away_team: str) -> dict:
        """Compute the blended NN+XGB+LR ensemble probability.

        Returns:
            Dict containing 'home_win_prob' and model-level probabilities.
        """
        profile_dict = {row["team"]: row.to_dict()
                        for _, row in self._team_profiles.iterrows()}

        hp = profile_dict.get(home_team, {})
        ap = profile_dict.get(away_team, {})

        features = {}
        for col in NN_FEATURE_COLUMNS:
            if col == "home_field_advantage":
                features[col] = 1.0  # projection engine always predicts regular home games

            elif col == "elo_diff":
                h_elo = hp.get("elo_pre", 1500.0)
                a_elo = ap.get("elo_pre", 1500.0)
                features[col] = h_elo - a_elo

            elif col == "elo_confidence":
                features[col] = abs(features.get("elo_diff", 0.0)) / ELO_TO_SPREAD

            elif col == "pass_epa_matchup":
                features[col] = (
                    (hp.get("off_pass_epa_roll", 0.0) - ap.get("def_pass_epa_roll", 0.0))
                    - (ap.get("off_pass_epa_roll", 0.0) - hp.get("def_pass_epa_roll", 0.0))
                )

            elif col == "rush_epa_matchup":
                features[col] = (
                    (hp.get("off_rush_epa_roll", 0.0) - ap.get("def_rush_epa_roll", 0.0))
                    - (ap.get("off_rush_epa_roll", 0.0) - hp.get("def_rush_epa_roll", 0.0))
                )

            elif col == "early_down_matchup":
                features[col] = (
                    (hp.get("off_early_roll", 0.0) - ap.get("def_early_roll", 0.0))
                    - (ap.get("off_early_roll", 0.0) - hp.get("def_early_roll", 0.0))
                )

            elif col == "point_diff_advantage":
                features[col] = hp.get("margin_roll", 0.0) - ap.get("margin_roll", 0.0)

            elif col == "qb_pressure_advantage":
                # away_pressure - home_pressure (positive = home QB less pressured)
                features[col] = ap.get("qb_pressure_roll", 0.0) - hp.get("qb_pressure_roll", 0.0)

            elif col == "def_pressure_diff":
                features[col] = hp.get("def_pressures_roll", 0.0) - ap.get("def_pressures_roll", 0.0)

            elif col == "trench_dominance_metric":
                if self._preseason_roster and self._preseason_norm:
                    ol_mu, ol_sig, dl_mu, dl_sig = self._preseason_norm
                    h_pr = self._preseason_roster.get(home_team, {})
                    a_pr = self._preseason_roster.get(away_team, {})
                    h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                           + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                    features[col] = h_z - a_z
                else:
                    features[col] = hp.get("trench_score", 0.0) - ap.get("trench_score", 0.0)

            elif col == "net_travel_disadvantage":
                try:
                    from services.prediction_service import _get_travel_distance
                    features[col] = _get_travel_distance(away_team, home_team) / 1000.0
                except Exception:
                    features[col] = hp.get(col, 0.0)

            elif col in ("rest_advantage", "home_qb_injury_flag", "away_qb_injury_flag"):
                features[col] = 0.0  # unknown for future games; model trained on 0-mean baseline

            elif col == "market_implied_team_total":
                features[col] = hp.get(col, 22.0)

            else:
                # Signed-differential features: use team profile averages
                # (these average to ~0 across home+away appearances, which is correct)
                features[col] = hp.get(col, 0.0)

        nn_prob  = self.svc.predict_game(features)
        xgb_prob = self.xgb_svc.predict_game(features)
        lr_prob  = self.lr_svc.predict_game(features)

        blended = float(np.clip(
            NN_WEIGHT * nn_prob + XGB_WEIGHT * xgb_prob + LR_WEIGHT * lr_prob,
            PROB_CLIP_MIN, PROB_CLIP_MAX,
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

    def _batch_predict(self, X: np.ndarray) -> np.ndarray:
        """Run the NN+XGB+LR ensemble on a feature batch.

        Args:
            X: Raw (unscaled) feature matrix of shape (N, n_features).

        Returns:
            Blended win probabilities of shape (N,), clipped to [PROB_CLIP_MIN, PROB_CLIP_MAX].
        """
        X_f = X.astype(np.float32)
        nn_p  = self.svc.model.predict(self.svc.scaler.transform(X_f), verbose=0).flatten()
        xgb_p = self.xgb_svc.model.predict_proba(self.xgb_svc.scaler.transform(X_f))[:, 1]
        lr_p  = self.lr_svc.model.predict_proba(self.lr_svc.scaler.transform(X_f))[:, 1]
        blended = NN_WEIGHT * nn_p + XGB_WEIGHT * xgb_p + LR_WEIGHT * lr_p
        return np.clip(blended, PROB_CLIP_MIN, PROB_CLIP_MAX).astype(np.float64)

    def _vectorized_elo_update(
        self,
        state: np.ndarray,    # (n_sims, n_teams, 6) — mutated in-place
        h_idx: int,
        a_idx: int,
        margins: np.ndarray,  # (n_sims,) — positive = home wins
    ) -> None:
        """Update Elo ratings in-place for all trials after a simulated game."""
        home_wins = margins > 0
        abs_margin = np.abs(margins)

        h_elo = state[:, h_idx, 0]
        a_elo = state[:, a_idx, 0]

        # Elo diff from winner's perspective (home advantage = 48 pts)
        winner_elo_diff = np.where(
            home_wins,
            h_elo - a_elo + 48.0,   # home won: home advantage helps them
            a_elo - h_elo - 48.0,   # away won: home advantage hurt them
        )

        # Expected win probability for the actual winner
        expected = 1.0 / (10.0 ** (-winner_elo_diff / 400.0) + 1.0)

        # Margin-of-victory multiplier (FiveThirtyEight formula)
        log_comp = np.log(np.maximum(abs_margin, 1.0) + 1.0)
        autocorr = winner_elo_diff * 0.001 + 2.2
        mov_mult = log_comp * (2.2 / np.maximum(autocorr, 0.01))

        shift = 20.0 * (1.0 - expected) * mov_mult  # K = 20

        state[:, h_idx, 0] = np.where(home_wins, h_elo + shift, h_elo - shift)
        state[:, a_idx, 0] = np.where(home_wins, a_elo - shift, a_elo + shift)

    def _vectorized_epa_update(
        self,
        state: np.ndarray,    # (n_sims, n_teams, 6) — mutated in-place
        h_idx: int,
        a_idx: int,
        margins: np.ndarray,  # (n_sims,) — positive = home wins
    ) -> None:
        """Update EPA and margin_roll in-place for all trials after a simulated game."""
        home_wins = margins > 0
        abs_margin = np.abs(margins).astype(np.float32)

        delta      = abs_margin * MC_EPA_SCALE
        rush_delta = delta * MC_EPA_RUSH_WEIGHT
        sign_h = np.where(home_wins,  1.0, -1.0).astype(np.float32)
        sign_a = np.where(home_wins, -1.0,  1.0).astype(np.float32)

        # off_pass_epa (dim 1): winner off improves, loser off declines
        state[:, h_idx, 1] += sign_h * delta
        state[:, a_idx, 1] += sign_a * delta
        # def_pass_epa (dim 3): winner def improves = LOWER epa allowed, loser def = HIGHER
        state[:, h_idx, 3] -= sign_h * delta
        state[:, a_idx, 3] -= sign_a * delta

        # off_rush_epa (dim 2)
        state[:, h_idx, 2] += sign_h * rush_delta
        state[:, a_idx, 2] += sign_a * rush_delta
        # def_rush_epa (dim 4)
        state[:, h_idx, 4] -= sign_h * rush_delta
        state[:, a_idx, 4] -= sign_a * rush_delta

        # margin_roll (dim 5) — exponential moving average toward game result
        game_margin_h =  margins.astype(np.float32)
        game_margin_a = -margins.astype(np.float32)
        state[:, h_idx, 5] = 0.85 * state[:, h_idx, 5] + 0.15 * game_margin_h
        state[:, a_idx, 5] = 0.85 * state[:, a_idx, 5] + 0.15 * game_margin_a

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

    def simulate_season(
        self,
        schedule_df: pd.DataFrame,
        n_sims: int = 10_000,
        completed_results: dict = None,
    ) -> dict:
        """Dynamic week-by-week Monte Carlo season simulation.

        Args:
            schedule_df: Full season schedule with columns week, home_team, away_team.
                         Should include game_type column; if absent, all rows are treated
                         as regular season.
            n_sims: Number of independent simulation trials.
            completed_results: {game_key: margin} for already-played games.
                               game_key format: "W{wk:02d}_{home}_{away}"
                               margin = home_score - away_score (positive = home won).

        Returns:
            {
                "team_stats":  {team: {median_wins, mean_wins, std_dev, p5, p25, p75, p95}},
                "game_probs":  {game_key: {mean_prob, model_spread, home_team, away_team, week}},
            }
        """
        from services.nn_feature_engine import _normalize_team, FEATURE_COLUMNS as NN_FC

        if completed_results is None:
            completed_results = {}

        # Filter to regular season
        if "game_type" in schedule_df.columns:
            reg = schedule_df[schedule_df["game_type"] == "REG"].copy()
        else:
            reg = schedule_df.copy()

        if reg.empty:
            return {"team_stats": {}, "game_probs": {}}

        # Normalize team abbreviations
        reg["home_team"] = reg["home_team"].apply(lambda x: _normalize_team(str(x)))
        reg["away_team"] = reg["away_team"].apply(lambda x: _normalize_team(str(x)))

        # Build initial state and index
        state_template, team_list, team_idx = self._build_initial_state()
        n_teams = len(team_list)

        # Broadcast initial state across all simulations: (n_sims, n_teams, 6)
        state = np.tile(state_template[np.newaxis], (n_sims, 1, 1)).astype(np.float32)
        win_matrix = np.zeros((n_sims, n_teams), dtype=np.float32)
        game_probs_out = {}

        # Pre-compute static feature arrays for all games
        static_feats = self._precompute_static_features(reg)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        rng = np.random.default_rng(seed=42)

        # Process weeks in ascending order
        for week, week_df in reg.groupby("week", sort=True):
            future_games = []

            for _, game in week_df.iterrows():
                ht = game["home_team"]
                at = game["away_team"]
                if ht not in team_idx or at not in team_idx:
                    continue
                h_idx = team_idx[ht]
                a_idx = team_idx[at]
                key = f"W{int(week):02d}_{ht}_{at}"

                if key in completed_results:
                    # Apply real result deterministically across all trials
                    real_margin = float(completed_results[key])
                    margins = np.full(n_sims, real_margin, dtype=np.float32)
                    home_won = real_margin > 0
                    win_matrix[:, h_idx] += float(home_won)
                    win_matrix[:, a_idx] += float(not home_won)
                    self._vectorized_elo_update(state, h_idx, a_idx, margins)
                    self._vectorized_epa_update(state, h_idx, a_idx, margins)
                else:
                    future_games.append((ht, at, h_idx, a_idx, key))

            if not future_games:
                continue

            # Build batched feature matrix: (G * n_sims, n_features)
            G = len(future_games)
            X_week = np.zeros((G * n_sims, len(NN_FC)), dtype=np.float32)

            for g_i, (ht, at, h_idx, a_idx, key) in enumerate(future_games):
                s, e = g_i * n_sims, (g_i + 1) * n_sims
                base = static_feats.get(key, np.zeros(len(NN_FC), dtype=np.float32))
                X_week[s:e] = np.broadcast_to(base, (n_sims, len(NN_FC))).copy()

                # Overwrite dynamic features from current trial states
                h_elo = state[:, h_idx, 0]
                a_elo = state[:, a_idx, 0]
                elo_diff = h_elo - a_elo

                X_week[s:e, col_idx["elo_diff"]]            = elo_diff
                X_week[s:e, col_idx["elo_confidence"]]      = np.abs(elo_diff) / ELO_TO_SPREAD
                X_week[s:e, col_idx["pass_epa_matchup"]]    = (
                    (state[:, h_idx, 1] - state[:, a_idx, 3])
                    - (state[:, a_idx, 1] - state[:, h_idx, 3])
                )
                X_week[s:e, col_idx["rush_epa_matchup"]]    = (
                    (state[:, h_idx, 2] - state[:, a_idx, 4])
                    - (state[:, a_idx, 2] - state[:, h_idx, 4])
                )
                X_week[s:e, col_idx["point_diff_advantage"]] = (
                    state[:, h_idx, 5] - state[:, a_idx, 5]
                )

            # Batch predict: (G * n_sims,) → reshape to (G, n_sims)
            probs_flat = self._batch_predict(X_week)
            probs_matrix = probs_flat.reshape(G, n_sims)

            # Simulate outcomes and update state for each game
            for g_i, (ht, at, h_idx, a_idx, key) in enumerate(future_games):
                game_probs = probs_matrix[g_i].astype(np.float64)
                mean_prob = float(np.mean(game_probs))
                mean_prob_clipped = float(np.clip(mean_prob, PROB_CLIP_MIN, PROB_CLIP_MAX))

                # Sample margins: per-trial implied spread → Normal(implied, MC_MARGIN_STD)
                implied = SPREAD_TO_PROB_SCALE * np.log(
                    np.clip(game_probs, PROB_CLIP_MIN, PROB_CLIP_MAX)
                    / (1.0 - np.clip(game_probs, PROB_CLIP_MIN, PROB_CLIP_MAX))
                )
                margins = rng.normal(implied, MC_MARGIN_STD).astype(np.float32)

                # Update win counts
                win_matrix[:, h_idx] += (margins > 0).astype(np.float32)
                win_matrix[:, a_idx] += (margins < 0).astype(np.float32)

                # Update team state for future weeks
                self._vectorized_elo_update(state, h_idx, a_idx, margins)
                self._vectorized_epa_update(state, h_idx, a_idx, margins)

                # Record game prediction
                model_spread = float(
                    SPREAD_TO_PROB_SCALE * np.log(mean_prob_clipped / (1.0 - mean_prob_clipped))
                )
                game_probs_out[key] = {
                    "mean_prob":    round(mean_prob_clipped, 4),
                    "model_spread": round(model_spread, 1),
                    "home_team":    ht,
                    "away_team":    at,
                    "week":         int(week),
                }

        # Aggregate win distributions per team
        team_stats = {}
        for team, t_idx in team_idx.items():
            w = win_matrix[:, t_idx]
            team_stats[team] = {
                "median_wins": float(np.median(w)),
                "mean_wins":   float(np.mean(w)),
                "std_dev":     float(np.std(w)),
                "p5":          float(np.percentile(w, 5)),
                "p25":         float(np.percentile(w, 25)),
                "p75":         float(np.percentile(w, 75)),
                "p95":         float(np.percentile(w, 95)),
            }

        return {"team_stats": team_stats, "game_probs": game_probs_out}

    def get_team_projected_wins(self, schedule_df: pd.DataFrame, n_sims: int = 5000) -> Dict[str, float]:
        """Produce season win totals via Monte Carlo simulation for Draft logic.

        Delegates to simulate_season() so that preseason player profiles
        (_preseason_profiles) are applied to the initial state, ensuring
        trades and roster moves are reflected.

        Returns:
            Dict mapping team abbreviation to projected wins (float, median).
        """
        if schedule_df.empty:
            all_teams = ["ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN",
                         "DET","GB","HOU","IND","JAX","KC","LV","LAC","LA","MIA","MIN",
                         "NE","NO","NYG","NYJ","PHI","PIT","SF","SEA","TB","TEN","WAS"]
            return {t: 8.5 for t in all_teams}

        result = self.simulate_season(schedule_df, n_sims=n_sims)
        return {
            team: round(float(stats["median_wins"]), 1)
            for team, stats in result.get("team_stats", {}).items()
        }

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

        # ATS pick: positive model spread = home favored (matches nflverse convention).
        spread = row.get("spread_line")
        ats = winner
        if pd.notna(spread):
            try:
                sv = float(spread)
                hp_clip = min(PROB_CLIP_MAX, max(PROB_CLIP_MIN, home_prob))
                implied = SPREAD_TO_PROB_SCALE * np.log(hp_clip / (1.0 - hp_clip))
                ats = home if implied > sv else away
            except (ValueError, TypeError):
                pass

        pred_winners.append(winner)
        pred_confs.append(conf_pct)
        pred_ats.append(ats)

    schedule_df = schedule_df.copy()
    schedule_df["pred_winner"] = pred_winners
    schedule_df["pred_su_conf"] = pred_confs
    schedule_df["pred_ats_pick"] = pred_ats

    return schedule_df
