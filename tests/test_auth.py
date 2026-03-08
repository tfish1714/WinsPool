import pytest
from fastapi.testclient import TestClient
import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from main import app

client = TestClient(app)

def test_api_check_player_nonexistent():
    """Verify that a nonexistent player returns 404."""
    response = client.get("/api/check_player?email=ghost_stability@example.com")
    assert response.status_code == 404
    assert response.json()["exists"] is False

def test_api_profile_undefined_handling():
    """Verify that 'undefined' as a playerId returns 404 gracefully."""
    response = client.get("/api/profile?playerId=undefined")
    assert response.status_code == 404

def test_api_login_payload_consistency():
    """Verify login failure returns error JSON."""
    response = client.post("/api/login", json={"email": "wrong@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert "error" in response.json()

def test_api_admin_unauthorized():
    """Verify that admin endpoints reject requests without admin role."""
    response = client.get("/api/admin/players?playerId=non_admin_id")
    assert response.status_code == 401
