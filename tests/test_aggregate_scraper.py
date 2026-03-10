import pytest
import httpx
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from services.aggregate_scraper import aggregate_predictions_pipeline, fetch_espn_fpi

def test_fetch_espn_fpi_happy_path():
    async def run_test():
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "season": {
                "teams": [
                    {"team": {"abbreviation": "BUF"}, "fpi": {"projectedWins": 11.2}},
                    {"team": {"abbreviation": "KC"}, "fpi": {"projectedWins": 10.5}}
                ]
            }
        }
        mock_client.get.return_value = mock_response
        
        result = await fetch_espn_fpi(mock_client)
        
        assert result is not None
        assert "BUF" in result
        assert result["BUF"] == 11.2
        mock_client.get.assert_called_once()
    asyncio.run(run_test())

def test_fetch_espn_fpi_failure():
    """
    Verify that if the HTTPX client experiences a network timeout or 404,
    the fetch safely catches the exception and returns an empty payload.
    """
    async def run_test():
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.RequestError("Mocked Connection Refused", request=MagicMock())
        
        result = await fetch_espn_fpi(mock_client)
        
        assert isinstance(result, dict)
        assert len(result) == 0
        mock_client.get.assert_called_once()
    asyncio.run(run_test())

def test_aggregate_pipeline_success():
    """
    Verify that the orchestrator concurrently executes all configured
    scraping tasks, aggregates the predictions natively.
    """
    async def run_test():
        with patch('services.aggregate_scraper.fetch_espn_fpi', new_callable=AsyncMock) as mock_fpi, \
             patch('services.aggregate_scraper.fetch_pff_projections', new_callable=AsyncMock) as mock_pff, \
             patch('services.aggregate_scraper.fetch_vegas_odds', new_callable=AsyncMock) as mock_br, \
             patch("services.aggregate_scraper.get_db") as mock_get_db:
             
            mock_fpi.return_value = {"BUF": 11.5}
            mock_pff.return_value = {"BUF": 10.5, "MIA": 9.0}
            mock_br.return_value = {"BUF": 12.0, "MIA": 8.5}
            
            mock_db = mock_get_db.return_value
            mock_doc = mock_db.collection.return_value.document.return_value
            
            await aggregate_predictions_pipeline(2024)
            
            mock_doc.set.assert_called_once()
    asyncio.run(run_test())
