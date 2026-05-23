from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def _bearer(monkeypatch):
    """Helper: returns an Authorization header with a valid test JWT."""
    from services.session_service import create_token
    return {"Authorization": f"Bearer {create_token(player_id=1, role='user')}"}


def test_read_main():
    """Test that the standings page renders correctly (HTML route, no auth needed)."""
    response = client.get("/wins-pool/2024")
    assert response.status_code == 200


def test_api_progress(monkeypatch):
    """GET /api/progress returns valid JSON with a valid auth token."""
    headers = _bearer(monkeypatch)
    response = client.get("/api/progress/2023/5", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["season"] == 2023
    assert data["week"] == 5
    assert "player_chart" in data


def test_api_draft_summary(monkeypatch):
    """GET /api/progress/draft_summary returns best-picks data with a valid auth token."""
    headers = _bearer(monkeypatch)
    response = client.get("/api/progress/draft_summary", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert "best_overall" in data
    assert "best_by_round" in data
