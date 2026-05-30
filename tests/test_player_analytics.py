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


def test_picks_with_deltas():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    season = result["seasons"][0]
    assert season["year"] == 2022
    assert season["totalWins"] == 39
    kc = next(p for p in season["picks"] if p["team"] == "KC")
    assert kc["actualWins"] == 14
    assert kc["projectedWins"] == 12.0
    assert kc["vsProjected"] == 2.0   # 14 - 12
    assert kc["pickNum"] == 1


def test_slot_averages():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    sa = result["slotAverages"]
    assert sa[1] == 14.0   # KC only pick at slot 1 across all seasons
    assert sa[11] == 13.0  # SF
    assert sa[21] == 12.0  # DAL


def test_returns_none_for_unknown_player():
    assert get_player_analytics(99, _draft(), _standings(), _players(), _preds()) is None


def test_returns_none_for_empty_draft_results():
    assert get_player_analytics(1, pd.DataFrame(), _standings(), _players(), _preds()) is None
