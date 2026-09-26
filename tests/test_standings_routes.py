"""Route-level tests for /playoff-race/{year}: the magic-number column only
renders once the season has reached PLAYOFF_RACE_MIN_WEEK."""
import pandas as pd
from fastapi.testclient import TestClient

from main import app
from services.constants import PLAYOFF_RACE_MIN_WEEK

YEAR = 3000
client = TestClient(app)


def _schedule():
    return pd.DataFrame([
        {"fullName_away": "Ann", "fullName_home": "Bo", "result": -3, "week": 1},
        {"fullName_away": "Ann", "fullName_home": "Bo", "result": -1000, "week": 2},
    ])


def _load(week):
    games = pd.DataFrame([{"season": YEAR, "week": week, "result": 1}])
    empty = pd.DataFrame()
    return empty, empty, games, empty, empty, empty, empty


def _render(week, monkeypatch):
    # Patch the names routes/standings_routes.py actually imports.
    import routes.standings_routes as sr
    monkeypatch.setattr(sr, "load_data", lambda *a, **k: _load(week))
    monkeypatch.setattr(sr, "get_latest_week_for_year", lambda games, year: week)
    monkeypatch.setattr(sr.analysis, "get_enriched_schedule", lambda *a, **k: _schedule())
    monkeypatch.setattr(sr, "get_available_years", lambda *a, **k: [YEAR])
    monkeypatch.setattr(sr, "get_active_season", lambda *a, **k: YEAR)
    return client.get(f"/playoff-race/{YEAR}")


def test_magic_column_hidden_before_min_week(monkeypatch):
    resp = _render(PLAYOFF_RACE_MIN_WEEK - 1, monkeypatch)
    assert resp.status_code == 200
    # Sanity: race cards did render, so absence is due to gating not an error.
    assert "race-card" in resp.text
    assert "data-magic-number" not in resp.text


def test_magic_column_shown_from_min_week(monkeypatch):
    resp = _render(PLAYOFF_RACE_MIN_WEEK, monkeypatch)
    assert resp.status_code == 200
    assert "race-card" in resp.text
    assert "data-magic-number" in resp.text
    assert "data-podium-magic-number" in resp.text
