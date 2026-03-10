import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_root_redirect():
    """Verify that the root endpoint securely redirects to the active season standings."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert "/wins-pool/" in response.headers["location"]

def test_standings_route_success(mock_firestore):
    """Verify that the html standings matrix renders HTTP 200 dynamically."""
    # Since we are using mock_env_vars, USE_LOCAL_DATA=true, meaning FastAPI bypasses Firestore
    # and reads straight from the safe .pkl cache mock locally constructed by pandas.
    response = client.get("/wins-pool/2024")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_api_profile_no_auth():
    # Calling an endpoints that requires authorization but failing to provide it
    response = client.get("/api/profile?playerId=999")
    assert response.status_code == 404 or response.status_code == 401

def test_draft_history_route_renders():
    """Verify that the historical data page loads without 500 exceptions."""
    response = client.get("/draft/history")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]

def test_recap_preview_auth_rejection():
    # Only admins can hit preview prompt
    response = client.post("/api/admin/recap/preview_prompt", json={"playerId": 999, "year": 2024, "week": 1})
    assert response.status_code == 401

def test_recap_generate_auth_rejection():
    """Verify that generative AI triggers reject unauthorized profiles."""
    response = client.post("/api/admin/recap/generate", json={"playerId": 999, "prompt_data": "fake"})
    assert response.status_code == 401

def test_invalid_login_credentials():
    """Verify that an invalid email/password POST safely returns HTTP 401."""
    response = client.post("/api/login", json={"email": "nonexistent@test.com", "password": "wrong"})
    assert response.status_code == 401
    assert "Invalid email or password" in response.json().get("error", "")
