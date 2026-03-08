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

# cache_builder always reads from Firestore (never from local pkl)
os.environ['USE_LOCAL_DATA'] = 'False'

# Initialize Firebase before importing data_service (which reads from Firestore)
import firebase_admin
from firebase_admin import credentials, firestore as _fs
if not firebase_admin._apps:
    _creds_path = pathlib.Path(__file__).parent.parent / 'firebase_credentials.json'
    if not _creds_path.exists():
        _creds_path = pathlib.Path(r'G:\Other computers\My Laptop (1)\Gambling\WinsPool\firebase_credentials.json')
    firebase_admin.initialize_app(credentials.Certificate(str(_creds_path)))

from services.data_service import load_data, get_available_years, get_latest_week_for_year
from services.cache_service import write_cache, is_cache_final
import services.analysis_service as analysis

ANALYTICS = [
    'wins_pool_standings',
    'player_winlossmatrix',
    'schedule_enriched',
    'weekbyweek',
]


def week_is_complete(games, year: int, week: int) -> bool:
    """A week is complete when every game in it has a non-null result."""
    week_games = games[(games['season'] == year) & (games['week'] == week)]
    if week_games.empty:
        return False
    return week_games['result'].notna().all() and (week_games['result'] != -1000).all()


def build_year(standings, games, players, draft_order, draft_results,
               draft_order_rules, year: int, current_year: int, force: bool = False):
    print(f"\n[cache_builder] Building year {year}...")

    # Determine latest week for this year
    latest_week = get_latest_week_for_year(games, year)
    is_past_season = (year < current_year)

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
            # Store only the columns the web app needs to avoid huge payloads
            cols = ['week', 'gameday', 'home_team', 'away_team', 'home_score', 'away_score',
                    'result', 'fullName_home', 'fullName_away']
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


def main():
    parser = argparse.ArgumentParser(description="Pre-compute analytics into cache")
    parser.add_argument('--year', type=int, default=None, help="Only rebuild this year")
    parser.add_argument('--force', action='store_true', help="Ignore is_final and recompute everything")
    args = parser.parse_args()

    print("[cache_builder] Loading raw data from Firestore / local cache...")
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()

    available_years = get_available_years(draft_results)
    current_year = max(available_years) if available_years else 2024

    years_to_build = [args.year] if args.year else available_years
    print(f"[cache_builder] Years to process: {years_to_build}")

    for year in years_to_build:
        # Filter data to just this year to avoid large cross-season merges
        yr_standings = standings[standings['season'] == year].copy() if not standings.empty else standings
        yr_games = games[games['season'] == year].copy() if not games.empty else games
        build_year(yr_standings, yr_games, players, draft_order, draft_results,
                   draft_order_rules, year, current_year, force=args.force)

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
