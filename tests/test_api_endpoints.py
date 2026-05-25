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


# ── #12: API endpoints must require authentication ─────────────────────────

def test_api_progress_is_public():
    """GET /api/progress/{season}/{week} is public — no token required.

    The standings page is public and the chart shows the same data already
    visible in the standings table, so there is no reason to gate it behind
    auth. The endpoint must never return 401.
    """
    response = client.get("/api/progress/2024/10")
    assert response.status_code != 401


def test_api_progress_draft_summary_requires_auth():
    """GET /api/progress/draft_summary returns 401 without a token."""
    response = client.get("/api/progress/draft_summary")
    assert response.status_code == 401


def test_api_standings_requires_auth():
    """GET /api/standings returns 401 without a token."""
    response = client.get("/api/standings?year=2024")
    assert response.status_code == 401


def test_api_schedule_requires_auth():
    """GET /api/schedule returns 401 without a token."""
    response = client.get("/api/schedule?year=2024")
    assert response.status_code == 401


def test_api_predictions_accuracy_requires_auth():
    """GET /api/predictions/accuracy returns 401 without a token."""
    response = client.get("/api/predictions/accuracy")
    assert response.status_code == 401


def test_api_predictions_explain_requires_auth():
    """GET /api/predictions/explain returns 401 without a token."""
    response = client.get("/api/predictions/explain?season=2024&week=1&home=KC&away=BUF")
    assert response.status_code == 401


class TestPredictionFeaturesEndpoint:
    """Tests for GET /api/prediction_features/{season}/{week}/{away}/{home}."""

    def test_returns_feature_data_when_present(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        # game_key format: W{week:02d}_{home_team}_{away_team}
        # URL /{away_team}/{home_team} → away=KC, home=SF → key W08_SF_KC
        fake_doc = {
            "season": 2025,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2",
            "created_at": "2025-11-01T00:00:00Z",
            "games": {
                "W08_SF_KC": {
                    "game_key": "W08_SF_KC", "season": 2025, "week": 8,
                    "away_team": "KC", "home_team": "SF",
                    "nn_prob": 0.623, "xgb_prob": 0.589,
                    "lr_prob": 0.601, "blended_prob": 0.611,
                    "features": {"tm_elo_pre": 1550.0},
                    "scaled_features": {"tm_elo_pre": 0.81},
                    "feature_importance": [
                        {"feature": "tm_elo_pre", "score": 0.31, "direction": "home"}
                    ],
                }
            },
        }
        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: fake_doc,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["game_key"] == "W08_SF_KC"
        assert data["nn_prob"] == 0.623
        assert data["blended_prob"] == 0.611

    def test_returns_404_when_no_season_data(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: None,
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 404

    def test_returns_404_when_game_not_found(self, auth_token, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "routes.api_routes.get_prediction_features",
            lambda season, **kw: {"season": 2025, "ensemble_version": "v1", "games": {}},
        )

        client = TestClient(app)
        resp = client.get(
            "/api/prediction_features/2025/8/KC/SF",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code == 404

    def test_requires_auth(self):
        from fastapi.testclient import TestClient
        from main import app

        client = TestClient(app)
        resp = client.get("/api/prediction_features/2025/8/KC/SF")
        assert resp.status_code == 401
