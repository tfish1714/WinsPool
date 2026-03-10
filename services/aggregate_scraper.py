import asyncio
import httpx
from bs4 import BeautifulSoup
import pandas as pd
from typing import Dict, List
import statistics
import time

from services.db_service import get_db
from services.utils import normalize_team_abbr

# Simple mapping for ESPN's internal FPI table JSON
ESPN_FPI_URL = "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/fpi"

async def fetch_espn_fpi(client: httpx.AsyncClient) -> Dict[str, float]:
    """
    Fetches ESPN's FPI projected wins.
    Returns: { 'BUF': 10.5, 'KC': 11.2, ... }
    """
    try:
        response = await client.get(ESPN_FPI_URL, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        predictions = {}
        # Parse the JSON structure (highly dependent on ESPN's exact schema)
        # Typically under teams[] -> fpi -> projectedWins
        teams = data.get('season', {}).get('teams', [])
        for team_data in teams:
            team = team_data.get('team', {})
            abbr = normalize_team_abbr(team.get('abbreviation', ''))
            
            fpi_data = team_data.get('fpi', {})
            proj_wins = fpi_data.get('projectedWins')
            
            if abbr and proj_wins is not None:
                predictions[abbr] = float(proj_wins)
                
        return predictions
    except Exception as e:
        print(f"[ESPN FPI] Scrape failed: {e}")
        return {}

async def fetch_pff_projections(client: httpx.AsyncClient) -> Dict[str, float]:
    """
    Mock implementation for scraping PFF or another HTML-based source.
    In a real scenario, this would use BeautifulSoup to parse a specific 
    PFF article or API endpoint.
    """
    # For demonstration of the concurrent pipeline, we return empty/mock data 
    # as HTML scraping requires an active, stable target URL.
    print("[PFF] Scraper stub executed.")
    return {}

async def fetch_vegas_odds(client: httpx.AsyncClient) -> Dict[str, float]:
    """
    Mock implementation for hitting a sports betting odds API 
    (e.g., DraftKings, FanDuel, or an odds aggregator) for Over/Under Win Totals.
    """
    print("[Vegas] Scraper stub executed.")
    return {}

async def aggregate_predictions_pipeline(season: int):
    """
    1. Concurrently fetches win total projections from multiple sources.
    2. Normalizes the data by team.
    3. Calculates the Consensus Mean and Standard Deviation.
    4. Writes the results directly into the Firestore `preseason_predictions` array.
    """
    print(f"Starting Prediction Aggregation Pipeline for {season} Season...")
    start_time = time.time()
    
    async with httpx.AsyncClient(headers={'User-Agent': 'WinsPool/1.0'}) as client:
        # Run scrappers concurrently
        results = await asyncio.gather(
            fetch_espn_fpi(client),
            fetch_pff_projections(client),
            fetch_vegas_odds(client),
            return_exceptions=True # Don't crash if one scraper fails
        )
        
    espn_res = results[0] if isinstance(results[0], dict) else {}
    pff_res = results[1] if isinstance(results[1], dict) else {}
    vegas_res = results[2] if isinstance(results[2], dict) else {}
    
    # Invert the dictionary structure: source -> team  ==>  team -> list(sources)
    team_data : Dict[str, List[float]] = {}
    
    def add_to_team_data(source_dict: Dict[str, float]):
        for team, wins in source_dict.items():
            if wins <= 0: continue
            if team not in team_data:
                team_data[team] = []
            team_data[team].append(wins)

    add_to_team_data(espn_res)
    add_to_team_data(pff_res)
    add_to_team_data(vegas_res)
    
    if not team_data:
        print("❌ Pipeline Failed: No data retrieved from any source.")
        return
        
    final_payload = []
    
    print("\n--- Consensus Results ---")
    for team, projections in team_data.items():
        count = len(projections)
        mean_wins = round(statistics.mean(projections), 1)
        
        # StDev requires at least 2 data points
        std_dev = 0.0
        if count > 1:
            std_dev = round(statistics.stdev(projections), 2)
            
        print(f"{team}: {mean_wins}W (±{std_dev}) based on {count} sources.")
        
        final_payload.append({
            "team": team,
            "projected_wins": mean_wins,
            "std_dev": std_dev,
            "sources_count": count
        })
        
    # Commit to Firestore
    db = get_db()
    if db:
        doc_ref = db.collection("preseason_predictions").document(str(season))
        doc_ref.set({"predictions": final_payload})
        print(f"\n✅ Successfully committed {len(final_payload)} team predictions to Firestore for {season}.")
    else:
        print("\n⚠️ Database not connected. (Local mode?) Data not written to Firestore.")
        
    print(f"Pipeline completed in {time.time() - start_time:.2f} seconds.")

if __name__ == "__main__":
    # Specify the upcoming season year. 
    # Can be passed via CLI args in a real cron context: sys.argv[1]
    import sys
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    asyncio.run(aggregate_predictions_pipeline(year))
