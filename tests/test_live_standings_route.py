"""services/live_standings_service.py and GET /api/live-standings."""
from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from main import app
from services.live_standings_service import build_live_standings_payload

client = TestClient(app)


def _sorted_df():
    return pd.DataFrame([{
        "Rank": 1, "playerId": 4, "fullName": "Ann Lee", "TotalWins": 7,
        "team1": "KC", "wins1": 3, "ptDiff1": 18,
        "team2": "BUF", "wins2": 2, "ptDiff2": 10,
        "team3": "DET", "wins3": 2, "ptDiff3": np.nan,
        "Tiebreaker1_WorstTeamWins": 2, "Tiebreaker2_2ndWorstTeamWins": 2,
        "Tiebreaker3_BestTeamWins": 3, "Tiebreaker4_WorstTeamPtDiff": np.nan,
        "Tiebreaker5_2ndWorstTeamPtDiff": 10, "Tiebreaker6_BestTeamPtDiff": 18,
    }])


def _games(result=np.nan, is_live=True):
    return pd.DataFrame([{
        "season": 2026, "home_team": "LAC", "away_team": "KC", "is_live": is_live,
        "result": result, "live_home_score": 14, "live_away_score": 17,
        "period": 3, "clock": "4:12",
    }])


def test_payload_shape_and_live_flag():
    out = build_live_standings_payload(_sorted_df(), _games(), 2026)
    row = out["standings"][0]

    assert out["year"] == 2026 and "last_updated" in out
    assert row["rank"] == 1 and row["player_id"] == 4 and row["total_wins"] == 7
    assert row["full_name"] == "Ann Lee"
    kc = row["teams"][0]
    assert kc["abbr"] == "KC" and kc["wins"] == 3 and kc["pt_diff"] == 18
    assert kc["is_live"] is True
    assert kc["live_score"] == "KC 17 - LAC 14" and kc["period"] == 3 and kc["clock"] == "4:12"
    assert row["teams"][1]["is_live"] is False


def test_nan_point_diffs_and_tiebreakers_become_zero():
    row = build_live_standings_payload(_sorted_df(), _games(), 2026)["standings"][0]

    assert row["teams"][2]["pt_diff"] == 0
    assert row["tiebreakers"] == {"tb1": 2, "tb2": 2, "tb3": 3, "tb4": 0, "tb5": 10, "tb6": 18}


def test_final_game_is_not_live():
    out = build_live_standings_payload(_sorted_df(), _games(result=3), 2026)
    assert out["standings"][0]["teams"][0]["is_live"] is False


def test_is_live_false_game_is_not_live():
    out = build_live_standings_payload(_sorted_df(), _games(is_live=False), 2026)
    assert out["standings"][0]["teams"][0]["is_live"] is False


def test_empty_inputs_yield_no_standings():
    assert build_live_standings_payload(pd.DataFrame(), pd.DataFrame(), 2026)["standings"] == []
    assert build_live_standings_payload(None, None, 2026)["standings"] == []


def test_games_without_live_columns_are_tolerated():
    games = pd.DataFrame([{"home_team": "A", "away_team": "B"}])
    out = build_live_standings_payload(_sorted_df(), games, 2026)
    assert out["standings"][0]["teams"][0]["is_live"] is False


def test_player_with_fewer_than_three_teams_mid_draft():
    df = _sorted_df()
    df.loc[0, "team3"] = ""
    out = build_live_standings_payload(df, _games(), 2026)
    assert [t["abbr"] for t in out["standings"][0]["teams"]] == ["KC", "BUF"]


def _tables(games):
    empty = pd.DataFrame({"season": [2026]})
    return (empty, empty, games, empty, empty, empty, empty)


def _patch_route(sorted_df, games, picks=(10, 10)):
    return (
        patch("routes.api_routes.load_data", return_value=_tables(games)),
        patch("routes.api_routes.analysis.get_draft_progress", return_value=picks),
        patch("routes.api_routes.analysis.calculate_wins_pool_standings", return_value=sorted_df),
    )


def test_route_returns_payload_without_auth():
    p1, p2, p3 = _patch_route(_sorted_df(), _games())
    with p1, p2, p3:
        res = client.get("/api/live-standings?year=2026")

    assert res.status_code == 200
    body = res.json()
    assert body["standings"][0]["full_name"] == "Ann Lee"
    assert body["standings"][0]["teams"][0]["is_live"] is True


def test_route_json_contains_no_nan():
    p1, p2, p3 = _patch_route(_sorted_df(), _games())
    with p1, p2, p3:
        res = client.get("/api/live-standings?year=2026")

    assert "NaN" not in res.text


def test_route_draft_pending_returns_empty_standings():
    p1, p2, p3 = _patch_route(_sorted_df(), _games(), picks=(3, 10))
    with p1, p2, p3:
        res = client.get("/api/live-standings?year=2026")

    assert res.status_code == 200 and res.json()["standings"] == []


def test_route_requires_year():
    assert client.get("/api/live-standings").status_code == 422


def test_route_returns_500_on_failure():
    with patch("routes.api_routes.load_data", side_effect=RuntimeError("boom")):
        res = client.get("/api/live-standings?year=2026")

    assert res.status_code == 500


def _render_standings_page():
    empty = pd.DataFrame({"season": [2026]})
    two_players = pd.concat(
        [_sorted_df(), _sorted_df().assign(Rank=2, playerId=9, fullName="Bo Kim")],
        ignore_index=True,
    )
    two_players["refreshTime"] = "2026-09-20 12:00:00"
    patches = [
        patch("routes.standings_routes.load_data",
              return_value=(empty, empty, empty, empty, empty, empty, empty)),
        patch("routes.standings_routes.get_active_season", return_value=2026),
        patch("routes.standings_routes.get_available_years", return_value=[2026]),
        patch("routes.standings_routes.get_latest_week_for_year", return_value=3),
        patch("routes.standings_routes.analysis.get_draft_progress", return_value=(10, 10)),
        patch("routes.standings_routes.analysis.calculate_wins_pool_standings",
              return_value=two_players),
        patch("routes.standings_routes.analysis.get_enriched_schedule",
              return_value=pd.DataFrame()),
        patch("routes.standings_routes.analysis.player_winlossmatrix",
              return_value=pd.DataFrame()),
        patch("routes.standings_routes.db.get_weekly_recap", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        return client.get("/wins-pool/2026")
    finally:
        for p in patches:
            p.stop()


def test_standings_page_exposes_refresh_hooks():
    """standings_refresh.js finds everything by these data-* hooks; a template
    edit that drops one would break live refresh silently."""
    res = _render_standings_page()
    html = res.text

    assert res.status_code == 200
    assert "/static/js/standings_refresh.js" in html
    # leader: hero card + mobile card; #2: desktop row + mobile card
    assert html.count('data-player-id="4"') == 2
    assert html.count('data-player-id="9"') == 2
    assert 'data-team="KC"' in html
    for role in ("total", "rank", "team-wins", "team-pd", "live", "tb1", "tb4", "tb6"):
        assert f'data-role="{role}"' in html
