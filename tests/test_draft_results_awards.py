"""routes/draft_routes.py: draft results page award cards.

Win-based cards (Best/Worst Overall, Best by Round) are meaningless before
any games have been played -- every team is 0-0, so pick_value is just
draft-slot-minus-tiebreak noise. They must stay hidden until week 1 is
fully complete. Pick-time cards (quickest/slowest, and the new cumulative
fastest/slowest totals) are unaffected by that gate -- they're meaningful
right after the draft, and the cumulative cards intentionally sum whatever
picks are timed so far rather than requiring all 3, so they're useful to
watch live during the draft too.
"""
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from main import app

SEASON = 2026


def _players():
    return pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        {"playerId": 2, "fullName": "Bob Jones", "nickName": "Bob"},
    ])


def _draft_results(with_times=False):
    rows = [
        {"playerId": 1, "season": SEASON, "draftPick": 1, "team": "KC"},
        {"playerId": 2, "season": SEASON, "draftPick": 2, "team": "SF"},
        {"playerId": 1, "season": SEASON, "draftPick": 3, "team": "BUF"},
        {"playerId": 2, "season": SEASON, "draftPick": 4, "team": "DAL"},
    ]
    if with_times:
        # Alice: 30 + 90 = 120s total. Bob: 200s (only one pick timed so far).
        times = [30, 200, 90, None]
        for row, t in zip(rows, times):
            row["time_taken_seconds"] = t
    return pd.DataFrame(rows)


def _standings():
    return pd.DataFrame([
        {"season": SEASON, "team": "KC", "wins": 3, "scored": 90, "allowed": 60},
        {"season": SEASON, "team": "SF", "wins": 2, "scored": 80, "allowed": 70},
        {"season": SEASON, "team": "BUF", "wins": 1, "scored": 70, "allowed": 80},
        {"season": SEASON, "team": "DAL", "wins": 0, "scored": 60, "allowed": 90},
    ])


def _games(week1_complete: bool):
    result = 7.0 if week1_complete else None
    return pd.DataFrame([
        {"season": SEASON, "week": 1, "game_type": "REG",
         "home_team": "KC", "away_team": "SF", "result": result},
        {"season": SEASON, "week": 1, "game_type": "REG",
         "home_team": "BUF", "away_team": "DAL", "result": result},
    ])


def _mock_load_data(week1_complete, with_times=False):
    return (_standings(), pd.DataFrame(), _games(week1_complete), _players(),
            pd.DataFrame(), _draft_results(with_times), pd.DataFrame())


def _get_context(week1_complete, with_times=False):
    import routes.draft_routes as draft_routes

    captured = {}

    def fake_template_response(request, name, context, *a, **kw):
        captured.update(context)
        from fastapi.responses import HTMLResponse
        return HTMLResponse("ok")

    with patch.object(draft_routes, "load_data",
                       return_value=_mock_load_data(week1_complete, with_times)), \
         patch.object(draft_routes.templates, "TemplateResponse",
                      side_effect=fake_template_response):
        resp = TestClient(app).get(f"/draft/{SEASON}")

    assert resp.status_code == 200
    return captured


def test_win_based_cards_hidden_before_week1_complete():
    ctx = _get_context(week1_complete=False)
    assert ctx["best_overall"] is None
    assert ctx["worst_overall"] is None
    assert ctx["best_by_round"] == {}


def test_win_based_cards_shown_after_week1_complete():
    ctx = _get_context(week1_complete=True)
    assert ctx["best_overall"] is not None
    assert ctx["worst_overall"] is not None
    assert ctx["best_by_round"] != {}


def test_pick_time_cards_unaffected_by_week1_gate():
    """Quickest/slowest and cumulative totals must still show pre-week1."""
    ctx = _get_context(week1_complete=False, with_times=True)
    assert ctx["quickest"] is not None
    assert ctx["slowest"] is not None
    assert ctx["cumulative_fastest"] is not None
    assert ctx["cumulative_slowest"] is not None


def test_cumulative_totals_sum_across_a_players_timed_picks():
    """Alice: 30 + 90 = 120s (fastest total). Bob: 200s from one pick (slowest)."""
    ctx = _get_context(week1_complete=False, with_times=True)
    assert ctx["cumulative_fastest"]["player"] == "Alice Smith"
    assert ctx["cumulative_slowest"]["player"] == "Bob Jones"


def test_cumulative_cards_absent_when_no_pick_times_exist():
    """Historical seasons with no time_taken_seconds data at all -- no cards,
    not a crash or a zeroed placeholder."""
    ctx = _get_context(week1_complete=False, with_times=False)
    assert ctx["cumulative_fastest"] is None
    assert ctx["cumulative_slowest"] is None
