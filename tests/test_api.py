from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_read_main():
    """Test that the index.html serves correctly."""
    response = client.get("/")
    assert response.status_code == 200
    assert "NFL Wins Pool Draft" in response.text

def test_api_progress():
    """Test the progress API returns valid JSON."""
    response = client.get("/api/progress/2023/5")
    assert response.status_code == 200
    data = response.json()
    assert data["season"] == 2023
    assert data["week"] == 5
    assert "player_chart" in data

def test_api_draft_summary():
    """Test the draft summary API returns the best picks info."""
    response = client.get("/api/progress/draft_summary")
    assert response.status_code == 200
    data = response.json()
    assert "best_overall" in data
    assert "best_by_round" in data
