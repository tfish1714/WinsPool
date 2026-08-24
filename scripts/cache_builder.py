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
import subprocess
import time

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

# cache_builder always reads from Firestore (never from local pkl)
os.environ['USE_LOCAL_DATA'] = 'False'

# Initialize Firebase before importing data_service (which reads from Firestore).
# Delegates to daily_nfl_sync.py::initialize_firebase(), which checks the
# FIREBASE_CREDENTIALS env var (base64-encoded service account JSON -- how
# Cloud Run passes credentials) before falling back to a local
# firebase_credentials.json file. Previously this block only ever looked for
# the local file, which is gitignored and never present in the deployed
# image, so this job could never actually start on Cloud Run: it would
# sys.exit(1) at import time -- before _run_with_alerting() is even reached,
# so the failure produced no alert email either.
import firebase_admin
from firebase_admin import firestore as _fs
from scripts.daily_nfl_sync import initialize_firebase
initialize_firebase()

import numpy as np
import pandas as pd

from services.data_service import load_data, get_available_years, get_latest_week_for_year
from services.db_service import set_preseason_predictions
from services.cache_service import (
    write_cache, is_cache_final, write_game_predictions,
    get_game_predictions, merge_thin_game_predictions,
)
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
from services.email_service import send_alert_email

ANALYTICS = [
    'wins_pool_standings',
    'player_winlossmatrix',
    'schedule_enriched',
    'weekbyweek',
]


def _build_pred_lookup(ft: pd.DataFrame, nn_svc, xgb_svc, lr_svc) -> dict:
    """Thin wrapper around the shared build_ensemble_lookup."""
    return build_ensemble_lookup(ft, nn_svc, xgb_svc, lr_svc)


SIMULATE_SEASON_N_SIMS = 5000
RESIMULATE_N_SIMS = 2000
RAWDATA_DIR = pathlib.Path(__file__).parent.parent / "rawdata"


def _build_completed_results(games: pd.DataFrame, year: int) -> dict:
    """{game_key: margin} for every completed REG game in `year` -- the shape
    NNProjectionEngine.simulate_season() expects for its completed_results
    parameter. Reused by the --resimulate mode (Task 6)."""
    completed = {}
    yr_games = games[games["season"] == year] if "season" in games.columns else games
    for _, row in yr_games.iterrows():
        res = row.get("result")
        if pd.notna(res) and res != UNDRAFTED_SENTINEL and row.get("game_type") == "REG":
            ht = _normalize_team(str(row.get("home_team", "") or ""))
            at = _normalize_team(str(row.get("away_team", "") or ""))
            wk = row.get("week")
            if ht and at and wk is not None:
                completed[f"W{int(wk):02d}_{ht}_{at}"] = float(res)
    return completed


def _derive_prediction_fields(ht: str, at: str, mean_prob: float, model_spread: float, spread_line) -> dict:
    """Shared winner/confidence/ATS-pick/edge-vs-vegas derivation from a
    simulate_season() game_probs_out entry -- used by both
    _apply_predictions()'s fallback branch and _publish_game_probs() so
    the two call sites can't independently drift (previously only one of
    them wrote model_spread, and neither computed edge_vs_vegas from it,
    leaving a stale/inconsistent pair for anything reading edge_vs_vegas,
    e.g. services/betting_screener_service.py)."""
    winner = ht if mean_prob >= 0.5 else at
    conf = round(max(mean_prob, 1.0 - mean_prob) * 100, 1)
    ats = winner
    edge_vs_vegas = None
    if pd.notna(spread_line):
        try:
            sl_val = float(spread_line)
            ats = ht if model_spread > sl_val else at
            edge_vs_vegas = round(model_spread - sl_val, 1)
        except (ValueError, TypeError):
            pass
    return {
        "pred_winner": winner,
        "pred_su_conf": conf,
        "pred_ats_pick": ats,
        "model_spread": model_spread,
        "edge_vs_vegas": edge_vs_vegas,
    }


def _publish_game_probs(game_ids: list, games: pd.DataFrame, year: int, game_probs: dict) -> int:
    """Publish only game_ids' entries from a simulate_season() game_probs_out
    dict into game_predictions, via the same merge-preserving path
    build_year() already uses -- every other stored game (including richer
    fields like model_spread/edge_vs_vegas the merge preserves) is untouched.
    """
    target = games[games["game_id"].astype(str).isin(game_ids)].copy()
    if target.empty:
        print(f"[cache_builder] --resimulate: no matching rows for {game_ids}")
        return 0

    pmap = {}
    for _, row in target.iterrows():
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')
        if not (ht and at and wk is not None):
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        gp = game_probs.get(key)
        if not gp:
            continue
        hp = gp['mean_prob']
        ms = gp['model_spread']
        spread = row.get('spread_line')
        derived = _derive_prediction_fields(ht, at, hp, ms, spread)
        pmap[key] = {
            'pred_prob':      round(hp, 4),
            'pred_winner':    derived['pred_winner'],
            'pred_su_conf':   derived['pred_su_conf'],
            'pred_ats_pick':  derived['pred_ats_pick'],
            'model_spread':   derived['model_spread'],
            'edge_vs_vegas':  derived['edge_vs_vegas'],
        }

    if not pmap:
        print("[cache_builder] --resimulate: no predictions produced for requested games")
        return 0

    existing = get_game_predictions(year)
    merged = merge_thin_game_predictions(existing, pmap)
    write_game_predictions(year, merged)
    return len(pmap)


def _apply_predictions(schedule_df: pd.DataFrame, year: int, pred_lookup: dict,
                       fallback_engine=None) -> pd.DataFrame:
    """Inject ML predictions into every row of schedule_df.

    For games found in pred_lookup (feature-table predictions), uses those.
    For unplayed games not in pred_lookup, falls back to fallback_engine's
    NNProjectionEngine.simulate_season() -- run ONCE across every unplayed row
    (not per-row), seeded with completed_results from this schedule's own
    real scores so the state feeding each future week's prediction has
    already absorbed every real result up to that point. Completed games
    with no feature data get None. If simulate_season() itself fails, every
    pending row gets None rather than being retried individually -- a
    systemic model/scaler failure isn't going to resolve itself on the next
    row.
    """
    n = len(schedule_df)
    pred_winners: list = [None] * n
    pred_confs: list = [None] * n
    pred_ats: list = [None] * n
    pred_probs: list = [None] * n

    unplayed_idx: list = []

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

        # Not in feature table — unplayed future game, queue for simulate_season()
        result = row.get('result')
        is_unplayed = pd.isna(result) or result == UNDRAFTED_SENTINEL
        if is_unplayed and ht and at and fallback_engine:
            unplayed_idx.append(i)

    if unplayed_idx and fallback_engine:
        try:
            completed_results = _build_completed_results(schedule_df, year)
            sim = fallback_engine.simulate_season(
                schedule_df, n_sims=SIMULATE_SEASON_N_SIMS, completed_results=completed_results,
            )
            game_probs = sim.get("game_probs", {})
        except Exception:
            game_probs = {}

        for i in unplayed_idx:
            row = schedule_df.iloc[i]
            ht = _normalize_team(str(row.get('home_team', '') or ''))
            at = _normalize_team(str(row.get('away_team', '') or ''))
            wk = row.get('week')
            if not (ht and at and wk is not None):
                continue
            key = f"W{int(wk):02d}_{ht}_{at}"
            gp = game_probs.get(key)
            if not gp:
                continue
            hp = gp['mean_prob']
            ms = gp['model_spread']
            spread = row.get('spread_line')
            derived = _derive_prediction_fields(ht, at, hp, ms, spread)
            pred_winners[i] = derived['pred_winner']
            pred_confs[i]   = derived['pred_su_conf']
            pred_ats[i]     = derived['pred_ats_pick']
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
               all_games=None, force: bool = False, pred_lookup: dict = None,
               model_version: str = None):
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
                    existing = get_game_predictions(year)
                    merged = merge_thin_game_predictions(existing, pmap)
                    write_game_predictions(year, merged)
                    print(f"  [ok]   game_predictions year={year} "
                          f"({len(pmap)} refreshed, {len(merged)} total)")

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

    # --- Preseason predictions (full-season win totals for the draft) ---
    # model_version is None when this run's model loading failed (see main()'s
    # except branch) -- mirrors pred_lookup={} silently skipping game_predictions
    # for the same reason, rather than writing under an unknown model version.
    if model_version:
        try:
            engine = _get_engine()
            yr_games = full_games[full_games['season'] == year].copy() if not full_games.empty else pd.DataFrame()
            if not yr_games.empty:
                yr_games = yr_games.drop_duplicates(subset=['week', 'home_team', 'away_team'])
            full_projections = engine.get_team_win_projections(yr_games, n_sims=5000)
            if full_projections:
                n = set_preseason_predictions(
                    year, full_projections, model_version=model_version,
                    locked=final_flag, force=force,
                )
                print(f"  [ok]   preseason_predictions year={year} ({n} teams written)")
        except Exception as e:
            print(f"  [err]  preseason_predictions: {e}")


SCRIPTS_DIR = pathlib.Path(__file__).parent


def _sync_rawdata() -> None:
    """Re-pull rawdata/ from nflverse before build_master_feature_table() reads
    it. winspool-predict-daily runs in its own, separate Cloud Run Job
    container from winspool-sync-daily -- Cloud Run Jobs are stateless,
    one-shot executions with no shared filesystem between them, so whatever
    winspool-sync-daily downloaded into ITS container (running ~15 min
    earlier per the dynamic kickoff schedule) is gone by the time this job
    starts. Default priority (3, full sync: rosters/depth_charts/injuries/
    snap_counts/pfr_advstats/schedules/stats_team) since build_master_feature_table()
    needs all of it, unlike the lightweight priority-1-only syncs in
    sync_live_scores.py/schedule_kickoffs.py. Non-fatal on failure, same
    established pattern as those two -- a stale-but-present rawdata/ from a
    prior local run isn't available in a fresh container either way, so any
    failure here is worth surfacing (via whatever downstream code hits
    missing files) rather than treated specially."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py")],
        capture_output=True, text=True, timeout=300,
        cwd=str(SCRIPTS_DIR.parent),
    )
    if result.returncode != 0:
        print(f"[warn] sync_nflverse_data.py exited non-zero (non-fatal): "
              f"{result.stderr.strip()[:500]}")


def _years_to_build(available_years: list, games: pd.DataFrame) -> list:
    """Years this job should process for the full-sweep (no --year) case.

    get_available_years() -- used elsewhere in the app for season pickers,
    draft pages, etc. -- only returns seasons with a real draft_results row,
    so a season with no draft yet (e.g. next year, during its preseason
    window) is invisible to it. That left game_predictions for that season
    unmaintained by this daily job entirely: it only ever got refreshed by a
    manual backfill_schedule_predictions.py run, never automatically.

    Extends available_years with the season right after the most recent
    drafted one, but only when real schedule data for it already exists
    (nflverse publishes next season's schedule well before the season
    starts) -- otherwise there's nothing to predict yet and no reason to add
    it. build_year()'s other four analytics (wins_pool_standings,
    player_winlossmatrix, weekbyweek, prediction_snapshot's player_projections)
    already degrade gracefully to empty output for a season with no
    draft_results (each wrapped in its own try/except) -- only
    schedule_enriched's game_predictions write is what this is actually for.
    """
    current_year = max(available_years) if available_years else 2024
    next_year = current_year + 1
    if (not games.empty and "season" in games.columns
            and next_year not in available_years
            and next_year in games["season"].values):
        return available_years + [next_year]
    return available_years


def main():
    parser = argparse.ArgumentParser(description="Pre-compute analytics into cache")
    parser.add_argument('--year', type=int, default=None, help="Only rebuild this year")
    parser.add_argument('--force', action='store_true',
                        help="Ignore is_final and recompute everything. "
                             "Use this after retraining models to refresh historical predictions.")
    parser.add_argument('--skip-sync', action='store_true',
                        help="Skip the rawdata/ sync step (for local runs where rawdata/ "
                             "is already fresh -- the deployed winspool-predict-daily job "
                             "never passes this, since its container starts empty).")
    parser.add_argument('--resimulate', type=str, default=None,
                        help="Comma-separated game_ids to re-run simulate_season() for "
                             "with a fresh ESPN injury check, publishing only those "
                             "games' predictions. For the close-to-kickoff last-mile refresh.")
    args = parser.parse_args()

    if not args.skip_sync:
        print("[cache_builder] Syncing rawdata/ from nflverse...")
        _sync_rawdata()

    print("[cache_builder] Loading raw data from Firestore / local cache...")
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()

    if args.resimulate:
        game_ids = [g.strip() for g in args.resimulate.split(",") if g.strip()]
        target_rows = games[games["game_id"].astype(str).isin(game_ids)]
        if target_rows.empty:
            print(f"[cache_builder] --resimulate: no matching rows for {game_ids}; nothing to do")
            return

        year = int(target_rows["season"].iloc[0])
        week = int(target_rows["week"].iloc[0])

        espn_overrides = {}
        try:
            from services.espn_injury_service import get_espn_injury_overrides
            target_pairs = list(zip(
                target_rows["home_team"].map(_normalize_team),
                target_rows["away_team"].map(_normalize_team),
            ))
            espn_overrides = get_espn_injury_overrides(target_pairs, year, week, RAWDATA_DIR)
        except Exception as e:
            print(f"[cache_builder] ESPN override fetch failed (non-fatal): {e}")

        engine = NNProjectionEngine()
        engine.initialize(year, espn_overrides=espn_overrides)

        yr_games = games[games["season"] == year].copy()
        completed_results = _build_completed_results(yr_games, year)
        sim = engine.simulate_season(yr_games, n_sims=RESIMULATE_N_SIMS, completed_results=completed_results)

        n = _publish_game_probs(game_ids, games, year, sim.get("game_probs", {}))
        print(f"[cache_builder] --resimulate: published {n} prediction(s).")

        try:
            db = _fs.client()
            db.collection("metadata").document("cache_control").set({"last_update": time.time()})
        except Exception as e:
            print(f"  [err] Failed to signal cache invalidation: {e}")
        return

    available_years = get_available_years(draft_results)
    current_year = max(available_years) if available_years else 2024

    years_to_build = [args.year] if args.year else _years_to_build(available_years, games)
    print(f"[cache_builder] Years to process: {years_to_build}")

    # Build ML ensemble prediction lookup once for all years
    print("[cache_builder] Loading ML models...")
    try:
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        model_version = f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"

        min_ft = min(years_to_build)
        max_ft = max(years_to_build)
        print(f"[cache_builder] Building feature table ({min_ft}-{max_ft})...")
        ft = build_master_feature_table(min_season=min_ft, max_season=max_ft)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)
        print(f"[cache_builder] {len(pred_lookup)} game predictions pre-computed.")
    except Exception as e:
        print(f"[cache_builder] WARNING: ML models unavailable ({e}). Predictions will be skipped.")
        pred_lookup = {}
        model_version = None

    for year in years_to_build:
        # Filter data to just this year to avoid large cross-season merges
        yr_standings = standings[standings['season'] == year].copy() if not standings.empty else standings
        yr_games = games[games['season'] == year].copy() if not games.empty else games
        build_year(yr_standings, yr_games, players, draft_order, draft_results,
                   draft_order_rules, year, current_year, all_games=games,
                   force=args.force, pred_lookup=pred_lookup,
                   model_version=model_version)

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


def _run_with_alerting():
    try:
        main()
    except Exception:
        import traceback
        send_alert_email(
            "WinsPool job 'winspool-predict-daily' failed",
            f"cache_builder.py raised an unhandled exception:\n\n{traceback.format_exc()}",
        )
        raise


if __name__ == '__main__':
    _run_with_alerting()
