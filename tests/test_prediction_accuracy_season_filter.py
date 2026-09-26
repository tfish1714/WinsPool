"""Season filter + available_seasons on GET /api/predictions/accuracy."""
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

SEASONS = [2022, 2023, 2024]


@pytest.fixture
def calls(monkeypatch):
    import routes.api_routes as api_routes

    rows = []
    for s in SEASONS:
        rows.append({"season": s, "week": 1, "home_team": "KC", "away_team": "BAL",
                     "result": 3, "home_score": 27, "away_score": 24, "spread_line": -2.5})
    games = pd.DataFrame(rows)
    monkeypatch.setattr(api_routes, "load_data",
                        lambda *a, **k: (None, None, games, None, None, None, None))
    monkeypatch.setattr("services.prediction_service.get_candidate_seasons", lambda: list(SEASONS))
    recorder = []

    def fake_preds(season):
        recorder.append(season)
        if season not in SEASONS:
            return {}
        return {"W01_KC_BAL": {"locked": True, "pred_winner": "KC"}}

    monkeypatch.setattr("services.cache_service.get_game_predictions", fake_preds)
    return recorder


def _get(auth_token, qs=""):
    return client.get("/api/predictions/accuracy" + qs, headers={"Authorization": auth_token})


def test_no_season_requests_all(auth_token, calls):
    body = _get(auth_token).json()
    assert calls == SEASONS
    assert {r["season"] for r in body["seasons"]} == set(SEASONS)
    assert body["overall"] == {"total": 3, "correct": 3, "accuracy": 100.0}
    assert body["available_seasons"] == SEASONS


def test_season_filters_to_one(auth_token, calls):
    body = _get(auth_token, "?season=2023").json()
    assert calls == [2023]
    assert [r["season"] for r in body["seasons"]] == [2023]
    assert body["overall"] == {"total": 1, "correct": 1, "accuracy": 100.0}
    assert body["available_seasons"] == SEASONS


def test_unknown_season_is_empty(auth_token, calls):
    resp = _get(auth_token, "?season=1999")
    assert resp.status_code == 200
    body = resp.json()
    assert body["seasons"] == []
    assert body["overall"] == {"total": 0, "correct": 0, "accuracy": 0}
    assert body["available_seasons"] == SEASONS
