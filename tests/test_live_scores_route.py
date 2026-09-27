"""GET /api/live-scores and the /schedule/{year} template's server-rendered
quarter label.

Regression: `period` is NaN for every game the live-scores job hasn't
touched, which forces the whole nflverse `games` DataFrame column to
float64. An in-progress game's real value (e.g. 1) then comes through as
1.0 -- both in the JSON payload the schedule page polls every 30s
(live_refresh.js) and in the server-rendered `Q{{ row['period'] }}` label
shown on first page load -- and prints "Q1.0" instead of "Q1".
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
YEAR = 3001


def _games_with_float_period():
    """Mirrors what a real nfl_games DataFrame looks like mid-slate: most
    rows have never had `period` touched (NaN), one is live with an int
    ESPN period -- together this forces the column to float64."""
    return pd.DataFrame([
        {
            "game_id": "g1", "home_team": "KC", "away_team": "MIA",
            "is_live": True, "clock": "6:23", "period": 1,
            "possession": "home", "live_home_score": 0, "live_away_score": 7,
            "home_score": np.nan, "away_score": np.nan, "result": np.nan,
        },
        {
            "game_id": "g2", "home_team": "DAL", "away_team": "NYG",
            "is_live": np.nan, "clock": np.nan, "period": np.nan,
            "possession": np.nan, "live_home_score": np.nan, "live_away_score": np.nan,
            "home_score": np.nan, "away_score": np.nan, "result": np.nan,
        },
    ])


def test_live_scores_route_period_is_int_not_float():
    games = _games_with_float_period()
    assert games["period"].dtype == np.float64  # sanity: reproduces the real-world shape

    with patch("routes.api_routes.load_data",
               return_value=(pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
                             pd.DataFrame(), pd.DataFrame(), pd.DataFrame())):
        res = client.get(f"/api/live-scores?year={YEAR}")

    assert res.status_code == 200
    body = res.json()
    assert body["g1"]["period"] == 1
    assert isinstance(body["g1"]["period"], int)
    assert '"period":1.0' not in res.text
    assert body["g2"]["period"] is None


def _schedule_row():
    return pd.DataFrame([{
        "game_id": "g1", "week": 3, "gameday": pd.Timestamp("2026-09-27"),
        "home_team": "KC", "away_team": "MIA",
        "home_record": "2-0", "away_record": "1-1",
        "fullName_home": "Tom Fischer", "fullName_away": "Jane Doe",
        "home_score": None, "away_score": None, "result": None,
        "is_live": True, "clock": "6:23", "period": 1.0, "possession": "home",
        "live_home_score": 0, "live_away_score": 7,
        "spread_line": None, "total_line": None, "pred_winner": None,
        "season": YEAR,
    }])


def test_schedule_page_renders_whole_number_quarter(monkeypatch):
    import routes.standings_routes as sr
    games = pd.DataFrame([{"season": YEAR, "week": 3, "result": 1}])
    monkeypatch.setattr(
        sr, "load_data",
        lambda *a, **k: (pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
                          pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(sr, "get_available_years", lambda *a, **k: [YEAR])
    monkeypatch.setattr(sr, "get_active_season", lambda *a, **k: YEAR)
    monkeypatch.setattr(sr, "get_latest_week_for_year", lambda *a, **k: 3)
    monkeypatch.setattr(sr.analysis, "get_enriched_schedule", lambda *a, **k: _schedule_row())
    monkeypatch.setattr("services.cache_service.merge_game_predictions", lambda df, year: df)

    res = client.get(f"/schedule/{YEAR}")

    assert res.status_code == 200
    assert "Q1 6:23" in res.text
    assert "Q1.0" not in res.text
