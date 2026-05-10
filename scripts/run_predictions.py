"""
scripts/run_predictions.py — Manual trigger for model predictions.

Calculates Elo + Pythagorean win projections for a given season/week
and persists them to the Firestore analytics_cache.

Usage:
    python scripts/run_predictions.py --year 2024 --week 0
"""
import sys
import os
import argparse
import pathlib
import time
from datetime import datetime, timezone

# Ensure project root is on the path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Always verify against remote Firestore for persistence
os.environ['USE_LOCAL_DATA'] = 'False'

import firebase_admin
from firebase_admin import credentials
if not firebase_admin._apps:
    _creds_path = pathlib.Path(__file__).parent.parent / 'firebase_credentials.json'
    if not _creds_path.exists():
        print(f"ERROR: firebase_credentials.json not found at {_creds_path}. "
              f"Set FIREBASE_CREDENTIALS env var or place the file in the project root.")
        sys.exit(1)
    firebase_admin.initialize_app(credentials.Certificate(str(_creds_path)))

from services.data_service import load_data, get_latest_week_for_year
from services.nn_projection_engine import NNProjectionEngine
from services.cache_service import write_cache

def main():
    parser = argparse.ArgumentParser(description="Run and persist model predictions")
    parser.add_argument('--year', type=int, required=True, help="Season year (e.g. 2024)")
    parser.add_argument('--week', type=int, default=None, help="Week number (0 for preseason/draft). Defaults to latest found.")
    parser.add_argument('--force', action='store_true', help="Overwrite existing cache")
    args = parser.parse_args()

    print(f"[run_predictions] Loading data for {args.year}...")
    # We need ALL games for Elo history
    standings, teams, games, players, draft_order, results, rules = load_data()
    
    year = args.year
    week = args.week if args.week is not None else get_latest_week_for_year(games, year)
    
    print(f"[run_predictions] Running model for Year {year}, Week {week}...")
    
    try:
        start_time = time.time()
        engine = NNProjectionEngine()
        engine.initialize(year)
        
        yr_games = games[games['season'] == year].copy() if not games.empty else pd.DataFrame()
        
        # 1. Team level projections
        team_projections = engine.get_team_projected_wins(yr_games, n_sims=5000)
        
        # 2. Player level portfolio projections
        yr_drafts = results[results['season'] == year]
        player_projections = []
        if not yr_drafts.empty and 'playerId' in yr_drafts.columns and 'team' in yr_drafts.columns:
            unique_pids = yr_drafts['playerId'].dropna().unique()
            print(f"  [model] Simulating {len(unique_pids)} player portfolios...")
            for pid in unique_pids:
                pid = int(pid)
                p_teams = yr_drafts[yr_drafts['playerId'] == pid]['team'].dropna().tolist()
                if not p_teams:
                    continue
                # Use fewer simulations for speed in manual runs if desired
                proj = engine.project_portfolio_wins(p_teams, yr_games, n_sims=500)
                proj['playerId'] = pid
                proj['teams'] = p_teams
                player_projections.append(proj)

        snapshot = {
            'team_projections': team_projections,
            'player_projections': player_projections,
            'model_version': '1.1.0',
            'computed_at': datetime.now(timezone.utc).isoformat()
        }

        # 3. Persist to analytics_cache
        analytic = 'prediction_snapshot'
        write_cache(analytic, year, week, snapshot, is_final=False)
        
        duration = time.time() - start_time
        print(f"\n[run_predictions] Success! Saved {analytic}_{year}_{week}")
        print(f"[run_predictions] Total time: {duration:.2f}s")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[run_predictions] Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
