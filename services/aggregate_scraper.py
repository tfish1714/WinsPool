import asyncio
import logging
import httpx
from typing import Dict, List
import statistics
import time

from services.db_service import get_db
from services.utils import normalize_team_abbr

logger = logging.getLogger(__name__)

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
        logger.error("ESPN FPI scrape failed: %s", e)
        return {}

async def aggregate_predictions_pipeline(season: int):
    """Fetch ESPN FPI win projections and write them to Firestore preseason_predictions."""
    logger.info("Starting Prediction Aggregation Pipeline for %s season...", season)
    start_time = time.time()

    async with httpx.AsyncClient(headers={'User-Agent': 'WinsPool/1.0'}) as client:
        espn_res = await fetch_espn_fpi(client)

    team_data: Dict[str, List[float]] = {}

    def add_to_team_data(source_dict: Dict[str, float]):
        for team, wins in source_dict.items():
            if wins <= 0:
                continue
            if team not in team_data:
                team_data[team] = []
            team_data[team].append(wins)

    add_to_team_data(espn_res)
    
    if not team_data:
        logger.error("Pipeline failed: no data retrieved from any source.")
        return
        
    final_payload = []
    
    logger.info("Consensus results:")
    for team, projections in team_data.items():
        count = len(projections)
        mean_wins = round(statistics.mean(projections), 1)

        # StDev requires at least 2 data points
        std_dev = 0.0
        if count > 1:
            std_dev = round(statistics.stdev(projections), 2)

        logger.info("%s: %.1fW (±%.2f) based on %d sources.", team, mean_wins, std_dev, count)
        
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
        logger.info("Committed %d team predictions to Firestore for %s.", len(final_payload), season)
    else:
        logger.warning("Database not connected (local mode?). Data not written to Firestore.")
        
    logger.info("Pipeline completed in %.2f seconds.", time.time() - start_time)

if __name__ == "__main__":
    # Specify the upcoming season year. 
    # Can be passed via CLI args in a real cron context: sys.argv[1]
    import sys
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    asyncio.run(aggregate_predictions_pipeline(year))
