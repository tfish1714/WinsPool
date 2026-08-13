import pytest
import pandas as pd
from services.analysis_service import get_player_analytics


def _draft():
    return pd.DataFrame([
        {"playerId": 1, "season": 2022, "draftPick": 1,  "team": "KC"},
        {"playerId": 1, "season": 2022, "draftPick": 11, "team": "SF"},
        {"playerId": 1, "season": 2022, "draftPick": 21, "team": "DAL"},
        {"playerId": 2, "season": 2022, "draftPick": 2,  "team": "BUF"},
        {"playerId": 2, "season": 2022, "draftPick": 12, "team": "PHI"},
        {"playerId": 2, "season": 2022, "draftPick": 22, "team": "MIA"},
    ])


def _standings():
    return pd.DataFrame([
        {"season": 2022, "team": "KC",  "wins": 14, "scored": 400, "allowed": 300},
        {"season": 2022, "team": "SF",  "wins": 13, "scored": 380, "allowed": 290},
        {"season": 2022, "team": "DAL", "wins": 12, "scored": 370, "allowed": 310},
        {"season": 2022, "team": "BUF", "wins": 13, "scored": 390, "allowed": 295},
        {"season": 2022, "team": "PHI", "wins": 14, "scored": 410, "allowed": 305},
        {"season": 2022, "team": "MIA", "wins": 9,  "scored": 330, "allowed": 340},
    ])


def _players():
    return pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        {"playerId": 2, "fullName": "Bob Jones",   "nickName": "Bob"},
    ])


def _preds():
    return {
        2022: {
            "KC":  {"projected_wins": 12.0},
            "SF":  {"projected_wins": 11.0},
            "DAL": {"projected_wins": 10.0},
            "BUF": {"projected_wins": 12.0},
            "PHI": {"projected_wins": 13.0},
            "MIA": {"projected_wins": 8.0},
        }
    }


def test_career_summary():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    assert result is not None
    career = result["career"]
    assert career["seasons"] == 1
    assert career["totalWins"] == 39   # 14+13+12
    assert career["avgWins"] == 39.0
    assert career["bestFinish"]["year"] == 2022
    assert career["worstFinish"]["year"] == 2022
    assert "championships" in career


def test_picks_with_deltas():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    season = result["seasons"][0]
    assert season["year"] == 2022
    assert season["totalWins"] == 39
    kc = next(p for p in season["picks"] if p["team"] == "KC")
    assert kc["actualWins"] == 14
    assert kc["projectedWins"] == 12.0
    assert kc["vsProjected"] == 2.0   # 14 - 12
    assert kc["vsSlot"] == 0.0
    assert kc["pickNum"] == 1


def test_slot_averages():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    sa = result["slotAverages"]
    assert sa["1"] == 14.0   # KC only pick at slot 1 across all seasons
    assert sa["11"] == 13.0  # SF
    assert sa["21"] == 12.0  # DAL


def test_returns_none_for_unknown_player():
    assert get_player_analytics(99, _draft(), _standings(), _players(), _preds()) is None


def test_returns_none_for_empty_draft_results():
    assert get_player_analytics(1, pd.DataFrame(), _standings(), _players(), _preds()) is None


def test_multi_season_best_worst_finish():
    """bestFinish and worstFinish should differ across seasons with different ranks."""
    import pandas as pd
    from services.analysis_service import get_player_analytics

    # Two players, two seasons; each player drafts 3 teams per season.
    # 2021: player 1 drafts KC/SF/DAL (wins 12+11+10=33); player 2 drafts BUF/PHI/MIA (wins 9+8+7=24) → p1 rank 1
    # 2022: player 1 drafts BUF/PHI/MIA (wins 9+8+7=24); player 2 drafts KC/SF/DAL (wins 14+13+12=39) → p1 rank 2
    dr = pd.DataFrame([
        {"playerId": 1, "season": 2021, "team": "KC",  "draftPick": 1},
        {"playerId": 1, "season": 2021, "team": "SF",  "draftPick": 11},
        {"playerId": 1, "season": 2021, "team": "DAL", "draftPick": 21},
        {"playerId": 2, "season": 2021, "team": "BUF", "draftPick": 2},
        {"playerId": 2, "season": 2021, "team": "PHI", "draftPick": 12},
        {"playerId": 2, "season": 2021, "team": "MIA", "draftPick": 22},
        {"playerId": 1, "season": 2022, "team": "BUF", "draftPick": 1},
        {"playerId": 1, "season": 2022, "team": "PHI", "draftPick": 11},
        {"playerId": 1, "season": 2022, "team": "MIA", "draftPick": 21},
        {"playerId": 2, "season": 2022, "team": "KC",  "draftPick": 2},
        {"playerId": 2, "season": 2022, "team": "SF",  "draftPick": 12},
        {"playerId": 2, "season": 2022, "team": "DAL", "draftPick": 22},
    ])
    standings = pd.DataFrame([
        {"season": 2021, "team": "KC",  "wins": 12},
        {"season": 2021, "team": "SF",  "wins": 11},
        {"season": 2021, "team": "DAL", "wins": 10},
        {"season": 2021, "team": "BUF", "wins": 9},
        {"season": 2021, "team": "PHI", "wins": 8},
        {"season": 2021, "team": "MIA", "wins": 7},
        {"season": 2022, "team": "KC",  "wins": 14},
        {"season": 2022, "team": "SF",  "wins": 13},
        {"season": 2022, "team": "DAL", "wins": 12},
        {"season": 2022, "team": "BUF", "wins": 9},
        {"season": 2022, "team": "PHI", "wins": 8},
        {"season": 2022, "team": "MIA", "wins": 7},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Alice", "nickName": "Ali"},
        {"playerId": 2, "fullName": "Bob",   "nickName": "Bob"},
    ])
    preseason_preds = {
        2021: {"KC": {"projected_wins": 11.0}, "SF": {"projected_wins": 10.0}, "DAL": {"projected_wins": 9.0}},
        2022: {"BUF": {"projected_wins": 8.0}, "PHI": {"projected_wins": 7.0}, "MIA": {"projected_wins": 6.0}},
    }

    result = get_player_analytics(1, dr, standings, players, preseason_preds)
    assert result is not None
    assert result["career"]["seasons"] == 2
    best  = result["career"]["bestFinish"]
    worst = result["career"]["worstFinish"]
    assert best  is not None
    assert worst is not None
    assert best["rank"] != worst["rank"] or best["year"] != worst["year"]
    # Player 1 wins 2021 (rank 1), loses 2022 (rank 2) → 1 championship
    assert result["career"]["championships"] == 1


from fastapi.testclient import TestClient
from unittest.mock import patch
from main import app

client = TestClient(app)


def _mock_load_data():
    standings = pd.DataFrame([
        {"season": 2022, "team": "KC", "wins": 14, "scored": 400, "allowed": 300},
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"}])
    draft = pd.DataFrame([
        {"playerId": 1, "season": 2022, "draftPick": 1, "team": "KC"},
    ])
    games = pd.DataFrame([{"season": 2022}])
    return standings, pd.DataFrame(), games, players, pd.DataFrame(), draft, pd.DataFrame()


@patch("routes.history_routes.load_data", return_value=_mock_load_data())
@patch("routes.history_routes.get_season_projection_legacy_shape",
       return_value={"KC": {"projected_wins": 12.0}})
def test_player_profile_page_returns_200(mock_preds, mock_load):
    resp = client.get("/history/player/1")
    assert resp.status_code == 200


@patch("routes.history_routes.load_data", return_value=_mock_load_data())
@patch("routes.history_routes.get_season_projection_legacy_shape", return_value={})
def test_player_profile_page_returns_404_for_unknown_player(mock_preds, mock_load):
    resp = client.get("/history/player/999")
    assert resp.status_code == 404


@patch("routes.history_routes._get_player_analytics_data")
def test_api_player_analytics_returns_200(mock_data):
    mock_data.return_value = {
        "player": {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        "career": {
            "seasons": 1, "totalWins": 14, "avgWins": 14.0,
            "bestFinish": {"rank": 1, "year": 2022},
            "worstFinish": {"rank": 1, "year": 2022},
        },
        "seasons": [{"year": 2022, "rank": 1, "totalWins": 14, "picks": [
            {"pickNum": 1, "team": "KC", "actualWins": 14,
             "projectedWins": 12.0, "slotAvgWins": 14.0,
             "vsProjected": 2.0, "vsSlot": 0.0},
        ]}],
        "slotAverages": {"1": 14.0},
    }
    from services.session_service import create_token
    headers = {"Authorization": f"Bearer {create_token(player_id=1, role='user')}"}
    resp = client.get("/api/player/1/analytics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["player"]["playerId"] == 1
