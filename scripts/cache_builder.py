"""
cache_builder.py — Background analytics pre-computation job.

This is the ONLY script that reads raw Firestore collections.
Run this after daily_nfl_sync.py completes, or manually to prime the local cache.

Usage:
    python scripts/cache_builder.py
    python scripts/cache_builder.py --year 2023  (rebuild a specific year)
"""
import sys
import os
import json
import argparse
import pathlib
import time

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

# cache_builder always reads from Firestore (never from local pkl)
os.environ['USE_LOCAL_DATA'] = 'False'

# Initialize Firebase before importing data_service (which reads from Firestore)
import firebase_admin
from firebase_admin import credentials, firestore as _fs
if not firebase_admin._apps:
    _creds_path = pathlib.Path(__file__).parent.parent / 'firebase_credentials.json'
    if not _creds_path.exists():
        print(f"ERROR: firebase_credentials.json not found at {_creds_path}. "
              f"Set FIREBASE_CREDENTIALS env var or place the file in the project root.")
        sys.exit(1)
    firebase_admin.initialize_app(credentials.Certificate(str(_creds_path)))

import numpy as np
import pandas as pd

from services.data_service import load_data, get_available_years, get_latest_week_for_year
from services.cache_service import write_cache, is_cache_final, write_game_predictions
import services.analysis_service as analysis
from services.prediction_service import PredictionService
from services.nn_projection_engine import NNProjectionEngine
from services.nn_prediction_service import NNPredictionService, build_ensemble_lookup
from services.xgb_prediction_service import XGBPredictionService
from services.lr_prediction_service import LRPredictionService
from services.nn_feature_engine import (
    build_master_feature_table, FEATURE_COLUMNS, _normalize_team,
)
from services.constants import UNDRAFTED_SENTINEL, NN_WEIGHT, XGB_WEIGHT, LR_WEIGHT
import services.live_score_service as live_scores

ANALYTICS = [
    'wins_pool_standings',
    'player_winlossmatrix',
    'schedule_enriched',
    'weekbyweek',
]


def _build_pred_lookup(ft: pd.DataFrame, nn_svc, xgb_svc, lr_svc) -> dict:
    """Thin wrapper around the shared build_ensemble_lookup."""
    return build_ensemble_lookup(ft, nn_svc, xgb_svc, lr_svc)


def _apply_predictions(schedule_df: pd.DataFrame, year: int, pred_lookup: dict,
                       fallback_engine=None) -> pd.DataFrame:
    """Inject ML predictions into every row of schedule_df.

    For games found in pred_lookup (feature-table predictions), uses those.
    For unplayed games not in pred_lookup, falls back to the team-profile engine,
    batched into one model call rather than one call per game -- looping
    game_win_probability() per row was the dominant cost of this script's
    Analytics Cache Build step (unbatched single-row Keras predict() calls, each
    carrying meaningful fixed overhead). Completed games with no feature data
    get None. If the batched fallback call itself fails, every pending row gets
    None rather than being retried individually -- a systemic model/scaler
    failure isn't going to resolve itself on the next row.
    """
    n = len(schedule_df)
    pred_winners: list = [None] * n
    pred_confs: list = [None] * n
    pred_ats: list = [None] * n
    pred_probs: list = [None] * n

    fallback_idx, fallback_pairs, fallback_spreads = [], [], []

    for i, (_, row) in enumerate(schedule_df.iterrows()):
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')

        pred = pred_lookup.get((year, int(wk), ht, at)) if (ht and at and wk is not None) else None

        if pred:
            pred_winners[i] = pred['pred_winner']
            pred_confs[i]   = pred['pred_su_conf']
            pred_ats[i]     = pred['pred_ats_pick']
            pred_probs[i]   = pred['pred_prob']
            continue

        # Not in feature table — unplayed future game, queue for the team-profile fallback
        result = row.get('result')
        is_unplayed = pd.isna(result) or result == UNDRAFTED_SENTINEL
        if is_unplayed and ht and at and fallback_engine:
            fallback_idx.append(i)
            fallback_pairs.append((ht, at))
            fallback_spreads.append(row.get('spread_line'))

    if fallback_pairs:
        try:
            batch = fallback_engine.game_win_probabilities_batch(fallback_pairs)
        except Exception:
            batch = None
        if batch is not None:
            for pos, i in enumerate(fallback_idx):
                ht, at = fallback_pairs[pos]
                spread = fallback_spreads[pos]
                hp = batch[pos]['home_win_prob']
                winner = ht if hp >= 0.5 else at
                conf   = round(min(99.0, max(50.0, (hp if hp >= 0.5 else 1 - hp) * 100)), 1)
                ats = winner
                if pd.notna(spread):
                    try:
                        sv = float(spread)
                        ats = at if sv < -3 else (ht if sv > 3 else (at if sv < 0 else ht))
                    except (ValueError, TypeError):
                        pass
                pred_winners[i] = winner
                pred_confs[i]   = conf
                pred_ats[i]     = ats
                pred_probs[i]   = round(hp, 4)

    out = schedule_df.copy()
    out['pred_winner']  = pred_winners
    out['pred_su_conf'] = pred_confs
    out['pred_ats_pick'] = pred_ats
    out['pred_prob']    = pred_probs
    return out


def week_is_complete(games, year: int, week: int) -> bool:
    """A week is complete when every game in it has a non-null result."""
    week_games = games[(games['season'] == year) & (games['week'] == week)]
    if week_games.empty:
        return False
    return week_games['result'].notna().all() and (week_games['result'] != -1000).all()


def build_year(standings, games, players, draft_order, draft_results,
               draft_order_rules, year: int, current_year: int,
               all_games=None, force: bool = False, pred_lookup: dict = None):
    print(f"\n[cache_builder] Building year {year}...")

    # Use full multi-season games for Elo history (falls back to year-filtered)
    full_games = all_games if all_games is not None else games

    # Determine latest week for this year
    latest_week = get_latest_week_for_year(games, year)
    is_past_season = (year < current_year)

    # schedule_enriched (fallback engine) and prediction_snapshot both need an
    # NNProjectionEngine for this year; share one instance so initialize() --
    # which rebuilds a multi-season feature table plus preseason player
    # profiles -- runs at most once per year instead of twice.
    _engine_cache = {}

    def _get_engine() -> "NNProjectionEngine":
        if "engine" not in _engine_cache:
            eng = NNProjectionEngine()
            eng.initialize(year)
            _engine_cache["engine"] = eng
        return _engine_cache["engine"]

    # If it's the current year, sync live scores from ESPN
    if year == current_year:
        print(f"  [live] Syncing ESPN scores for {year}...")
        try:
            games = live_scores.sync_live_scores_to_df(games)
        except Exception as e:
            print(f"  [warn] Live sync failed: {e}")

    # --- Wins Pool Standings ---
    analytic = 'wins_pool_standings'
    final_flag = is_past_season or (latest_week >= 18)  # 18 weeks in NFL regular season
    if not force and is_cache_final(analytic, year, latest_week):
        print(f"  [skip] {analytic} year={year} week={latest_week} already final")
    else:
        try:
            df = analysis.calculate_wins_pool_standings(standings, draft_results, players, year)
            write_cache(analytic, year, latest_week, df.to_dict(orient='records'), is_final=final_flag)
            print(f"  [ok]   {analytic} year={year} week={latest_week} is_final={final_flag}")
        except Exception as e:
            print(f"  [err]  {analytic}: {e}")

    # --- Schedule enriched (for the live schedule view and h2h matrix) ---
    analytic = 'schedule_enriched'
    if not force and is_past_season and is_cache_final(analytic, year, latest_week):
        print(f"  [skip] {analytic} year={year} week={latest_week} already final")
    else:
        try:
            schedule_df = analysis.get_enriched_schedule(games, draft_results, players, year)

            # Apply ML ensemble predictions.
            # Feature-table lookup covers all completed games. For unplayed future
            # games (current season only), fall back to team-profile predictions.
            fallback_engine = _get_engine() if year >= current_year else None

            schedule_df = _apply_predictions(
                schedule_df, year,
                pred_lookup if pred_lookup is not None else {},
                fallback_engine=fallback_engine,
            )

            # Persist predictions to game_predictions store so /api/schedule can serve them.
            # This captures both feature-table predictions and fallback predictions for
            # future games, which pred_lookup alone would miss.
            pred_cols = ['week', 'home_team', 'away_team', 'pred_winner',
                         'pred_su_conf', 'pred_ats_pick', 'pred_prob']
            pc = [c for c in pred_cols if c in schedule_df.columns]
            if 'pred_winner' in pc:
                pmap = {}
                for _, r in schedule_df[pc].dropna(subset=['pred_winner']).iterrows():
                    ht = _normalize_team(str(r.get('home_team', '') or ''))
                    at = _normalize_team(str(r.get('away_team', '') or ''))
                    wk = r.get('week')
                    if ht and at and wk is not None:
                        pmap[f"W{int(wk):02d}_{ht}_{at}"] = {
                            'pred_prob':     r.get('pred_prob'),
                            'pred_winner':   r.get('pred_winner'),
                            'pred_su_conf':  r.get('pred_su_conf'),
                            'pred_ats_pick': r.get('pred_ats_pick'),
                        }
                if pmap:
                    write_game_predictions(year, pmap)
                    print(f"  [ok]   game_predictions year={year} ({len(pmap)} games)")

            # Store only the columns the web app needs to avoid huge payloads
            cols = ['week', 'gameday', 'home_team', 'away_team', 'home_score', 'away_score',
                    'result', 'fullName_home', 'fullName_away', 'spread_line', 'total_line',
                    'home_moneyline', 'home_spread_odds', 'pred_winner', 'pred_su_conf',
                    'pred_ats_pick', 'pred_prob']
            cols_present = [c for c in cols if c in schedule_df.columns]
            slim = schedule_df[cols_present].copy()
            write_cache(analytic, year, latest_week, slim.to_dict(orient='records'), is_final=final_flag)
            print(f"  [ok]   {analytic} year={year} week={latest_week} is_final={final_flag}")
        except Exception as e:
            print(f"  [err]  {analytic}: {e}")

    # --- Player win-loss matrix ---
    analytic = 'player_winlossmatrix'
    if not force and is_past_season and is_cache_final(analytic, year, latest_week):
        print(f"  [skip] {analytic} year={year} already final")
    else:
        try:
            schedule_df = analysis.get_enriched_schedule(games, draft_results, players, year)
            matrix = analysis.player_winlossmatrix(schedule_df)
            if not matrix.empty:
                # Store as nested dict for easy template rendering
                write_cache(analytic, year, latest_week,
                            {'index': list(matrix.index), 'columns': list(matrix.columns),
                             'data': matrix.values.tolist()}, is_final=final_flag)
                print(f"  [ok]   {analytic} year={year} week={latest_week} is_final={final_flag}")
        except Exception as e:
            print(f"  [err]  {analytic}: {e}")

    # --- Week-by-week wins ---
    analytic = 'weekbyweek'
    if not force and is_past_season and is_cache_final(analytic, year, latest_week):
        print(f"  [skip] {analytic} year={year} already final")
    else:
        try:
            schedule_df = analysis.get_enriched_schedule(games, draft_results, players, year)
            wbw = analysis.player_winsbyWeek(schedule_df)
            write_cache(analytic, year, latest_week,
                        {'index': list(wbw.index), 'columns': list(wbw.columns),
                         'data': wbw.values.tolist()}, is_final=final_flag)
            print(f"  [ok]   {analytic} year={year} week={latest_week} is_final={final_flag}")
        except Exception as e:
            print(f"  [err]  {analytic}: {e}")

    # --- Prediction Snapshot (portfolio projections for each player) ---
    analytic = 'prediction_snapshot'
    if not force and is_past_season and is_cache_final(analytic, year, latest_week):
        print(f"  [skip] {analytic} year={year} already final")
    else:
        try:
            engine = _get_engine()

            yr_games = full_games[full_games['season'] == year].copy() if not full_games.empty else pd.DataFrame()
            # Deduplicate by (week, home_team, away_team) — the same matchup can appear with
            # different game_ids if daily_nfl_sync uploaded the schedule multiple times
            if not yr_games.empty:
                yr_games = yr_games.drop_duplicates(subset=['week', 'home_team', 'away_team'])
            team_projections = engine.get_team_projected_wins(yr_games, n_sims=5000)

            # Build per-player portfolio projections
            yr_drafts = draft_results[draft_results['season'] == year]
            player_projections = []
            if not yr_drafts.empty and 'playerId' in yr_drafts.columns and 'team' in yr_drafts.columns:
                for pid in yr_drafts['playerId'].dropna().unique():
                    pid = int(pid)
                    teams = yr_drafts[yr_drafts['playerId'] == pid]['team'].dropna().tolist()
                    if not teams:
                        continue
                    proj = engine.project_portfolio_wins(teams, yr_games, n_sims=500)
                    proj['playerId'] = pid
                    proj['teams'] = teams
                    player_projections.append(proj)

            snapshot = {
                'team_projections': team_projections,
                'player_projections': player_projections,
            }
            write_cache(analytic, year, latest_week, snapshot, is_final=final_flag)
            print(f"  [ok]   {analytic} year={year} week={latest_week} is_final={final_flag}")
        except Exception as e:
            print(f"  [err]  {analytic}: {e}")


def main():
    parser = argparse.ArgumentParser(description="Pre-compute analytics into cache")
    parser.add_argument('--year', type=int, default=None, help="Only rebuild this year")
    parser.add_argument('--force', action='store_true',
                        help="Ignore is_final and recompute everything. "
                             "Use this after retraining models to refresh historical predictions.")
    args = parser.parse_args()

    print("[cache_builder] Loading raw data from Firestore / local cache...")
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()

    available_years = get_available_years(draft_results)
    current_year = max(available_years) if available_years else 2024

    years_to_build = [args.year] if args.year else available_years
    print(f"[cache_builder] Years to process: {years_to_build}")

    # Build ML ensemble prediction lookup once for all years
    print("[cache_builder] Loading ML models...")
    try:
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        min_ft = min(years_to_build)
        max_ft = max(years_to_build)
        print(f"[cache_builder] Building feature table ({min_ft}-{max_ft})...")
        ft = build_master_feature_table(min_season=min_ft, max_season=max_ft)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)
        print(f"[cache_builder] {len(pred_lookup)} game predictions pre-computed.")
    except Exception as e:
        print(f"[cache_builder] WARNING: ML models unavailable ({e}). Predictions will be skipped.")
        pred_lookup = {}

    for year in years_to_build:
        # Filter data to just this year to avoid large cross-season merges
        yr_standings = standings[standings['season'] == year].copy() if not standings.empty else standings
        yr_games = games[games['season'] == year].copy() if not games.empty else games
        build_year(yr_standings, yr_games, players, draft_order, draft_results,
                   draft_order_rules, year, current_year, all_games=games,
                   force=args.force, pred_lookup=pred_lookup)

    # Signal web server to invalidate in-memory cache
    print("\n[cache_builder] Signaling global cache invalidation...")
    try:
        db = _fs.client()
        db.collection("metadata").document("cache_control").set({
            "last_update": time.time()
        })
    except Exception as e:
        print(f"  [err] Failed to signal cache invalidation: {e}")

    print("\n[cache_builder] Done.")


if __name__ == '__main__':
    main()
