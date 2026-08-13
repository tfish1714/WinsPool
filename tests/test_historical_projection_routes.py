"""Route-level guard: historical seasons must still show real projections.

After the consensus rows left preseason_predictions, every caller that read that
collection directly saw {} for 2017-2025. In services/ this was caught; in
routes/ it was not, and the pages silently degraded (0.0 wins on the player
profile, blank value columns on the draft recap).

These tests reproduce the exact production condition -- preseason_predictions
empty for the season, consensus_projections populated -- and assert the pages
still carry the projection. Every fixture value is non-zero and distinct so a
zeroed or blank result cannot pass by coincidence.
"""
from unittest.mock import patch

import pandas as pd
import pytest

import services.data_service as data_service

SEASON = 2023
CONSENSUS = {
    "KC": {"consensus_median": 9.5, "consensus_mean": 9.8,
           "consensus_std": 1.25, "sources": {"br": 10, "espn": 9}},
    "SF": {"consensus_median": 10.5, "consensus_mean": 10.2,
           "consensus_std": 1.5, "sources": {"br": 11, "espn": 10}},
    "BUF": {"consensus_median": 8.5, "consensus_mean": 8.1,
            "consensus_std": 1.75, "sources": {"br": 9, "espn": 8}},
    # Never drafted in the fixture -- exercises the undrafted-teams block.
    "DAL": {"consensus_median": 6.5, "consensus_mean": 6.9,
            "consensus_std": 2.0, "sources": {"br": 7, "espn": 6}},
}


@pytest.fixture
def deleted_preseason_rows(monkeypatch):
    """preseason_predictions empty for SEASON, consensus present -- prod today."""
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: CONSENSUS if int(s) == SEASON else {})


def _mock_load_data():
    standings = pd.DataFrame([
        {"season": SEASON, "team": "KC", "wins": 11, "scored": 400, "allowed": 300},
        {"season": SEASON, "team": "SF", "wins": 12, "scored": 420, "allowed": 290},
        {"season": SEASON, "team": "BUF", "wins": 9, "scored": 380, "allowed": 340},
        {"season": SEASON, "team": "DAL", "wins": 7, "scored": 350, "allowed": 360},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
    ])
    # Three picks: the pool standings calc expects wins1/wins2/wins3 per player.
    draft = pd.DataFrame([
        {"playerId": 1, "season": SEASON, "draftPick": 1, "team": "KC"},
        {"playerId": 1, "season": SEASON, "draftPick": 2, "team": "SF"},
        {"playerId": 1, "season": SEASON, "draftPick": 3, "team": "BUF"},
    ])
    games = pd.DataFrame([
        {"season": SEASON, "home_team": "KC", "away_team": "SF", "week": 1},
        {"season": SEASON, "home_team": "BUF", "away_team": "DAL", "week": 1},
    ])
    return standings, pd.DataFrame(), games, players, pd.DataFrame(), draft, pd.DataFrame()


# --- routes/history_routes.py: the player profile ---------------------------


def test_player_profile_shows_consensus_projection_for_historical_season(
    deleted_preseason_rows,
):
    """Was 0.0 with vsProjected inflated to the full win total."""
    import routes.history_routes as history_routes

    with patch.object(history_routes, "load_data", return_value=_mock_load_data()):
        analytics = history_routes._get_player_analytics_data(1)

    pick = analytics["seasons"][0]["picks"][0]
    assert pick["team"] == "KC"
    assert pick["actualWins"] == 11
    assert pick["projectedWins"] == 9.5, "historical projection lost -- reads 0.0"
    assert pick["vsProjected"] == 1.5


# --- routes/draft_routes.py: the historical draft recap ---------------------


def test_draft_recap_shows_projection_and_value_for_historical_season(
    deleted_preseason_rows,
):
    """Was projected_wins=None and draft_value=None for every historical row."""
    import routes.draft_routes as draft_routes
    from fastapi.testclient import TestClient
    from main import app

    captured = {}

    def fake_template_response(request, name, context, *a, **kw):
        captured.update(context)
        from fastapi.responses import HTMLResponse
        return HTMLResponse("ok")

    with patch.object(draft_routes, "load_data", return_value=_mock_load_data()), \
         patch.object(draft_routes.templates, "TemplateResponse",
                      side_effect=fake_template_response):
        resp = TestClient(app).get(f"/draft/{SEASON}")

    assert resp.status_code == 200
    row = captured["data"][0]
    assert row["team"] == "KC"
    assert row["projected_wins"] == 9.5, "historical projection lost -- reads None"
    assert row["draft_value"] == 1.5   # 11 actual - 9.5 projected

    undrafted = {t["team"]: t for t in captured["undrafted_teams"]}
    assert undrafted["DAL"]["projected_wins"] == 6.5


# --- routes/admin_routes.py: deliberately NOT repointed ---------------------


def test_admin_consensus_tab_still_reports_no_model_for_historical_season(
    deleted_preseason_rows,
):
    """The model side of the benchmark must stay empty for historical seasons.

    There is no model output for 2017-2025, so {} is correct here. Repointing
    this call site at the resolver would feed consensus back in as if it were
    model output and make the comparison compare consensus against itself.
    """
    from services.consensus_service import build_comparison

    model = data_service.get_preseason_predictions(SEASON)
    consensus = data_service.get_consensus_projections(SEASON)
    assert model == {}
    result = build_comparison(model, consensus, None)
    assert all(t.get("model_wins") is None for t in result["teams"])
