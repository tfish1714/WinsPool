"""Tests for the team page: services/team_page_service.py, /team/{abbr}, /teams, and the Teams nav entry.

`data` handed to build_team_page is a dict with keys:
  standings, games, players, draft_results  (frames as returned by load_data())
  predictions   {game_key: pred_dict}  (cache_service.get_game_predictions; pred_prob = HOME win prob)
  projections   {team: {"projected_wins": float}}  (get_season_projection_legacy_shape)
"""
import json
import pathlib
import re

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from main import app
from services.session_service import create_token
from services import team_page_service as tps

ROOT = pathlib.Path(__file__).resolve().parent.parent
client = TestClient(app, follow_redirects=False)


def _data(games=None, predictions=None, projections=None):
    standings = pd.DataFrame([
        {"season": 2025, "team": "KC", "wins": 14, "losses": 3, "ties": 0},
        {"season": 2024, "team": "KC", "wins": 15, "losses": 2, "ties": 0},
        {"season": 2024, "team": "DEN", "wins": 10, "losses": 7, "ties": 0},
        {"season": 2025, "team": "DEN", "wins": 9, "losses": 8, "ties": 0},
    ])
    players = pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        {"playerId": 2, "fullName": "Bob Jones", "nickName": "Bob"},
    ])
    draft = pd.DataFrame([
        {"playerId": 1, "season": 2025, "draftPick": 4, "team": "KC"},
        {"playerId": 2, "season": 2025, "draftPick": 5, "team": "DEN"},
        {"playerId": 2, "season": 2024, "draftPick": 2, "team": "KC"},
        {"playerId": 1, "season": 2024, "draftPick": 3, "team": "DEN"},
    ])
    if games is None:
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
             "result": 7, "home_score": 24, "away_score": 17},
            {"season": 2026, "week": 2, "game_type": "REG", "home_team": "LV", "away_team": "KC",
             "result": -3, "home_score": 20, "away_score": 23},
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "KC", "away_team": "LV",
             "result": None, "home_score": None, "away_score": None},
            {"season": 2026, "week": 4, "game_type": "REG", "home_team": "LV", "away_team": "DEN",
             "result": None, "home_score": None, "away_score": None},  # KC bye
            {"season": 2026, "week": 5, "game_type": "REG", "home_team": "DEN", "away_team": "KC",
             "result": None, "home_score": None, "away_score": None},
            {"season": 2026, "week": 6, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
             "result": None, "home_score": None, "away_score": None},
        ])
    if predictions is None:
        predictions = {
            "W03_KC_LV": {"pred_prob": 0.7},
            "W05_DEN_KC": {"pred_prob": 0.7},   # away team KC: win prob 0.3
        }
    return {"standings": standings, "games": games, "players": players, "draft_results": draft,
            "predictions": predictions,
            "projections": projections if projections is not None else {"KC": {"projected_wins": 11.4}}}


def _build(team="KC", include=True, **kw):
    return tps.build_team_page(team, 2026, _data(**kw), include)


class TestBuildTeamPage:
    def test_unknown_team_returns_none(self):
        assert _build("XXX") is None

    def test_header_and_dropdown(self):
        p = _build("kc")
        assert p["team"] == "KC" and p["name"] == "Kansas City Chiefs"
        assert p["logo"].endswith("KC.png")
        assert p["current_season"] == 2026
        assert len(p["teams"]) == 32
        assert [t["abbr"] for t in p["teams"]] == sorted(t["abbr"] for t in p["teams"])

    def test_history_only_drafted_seasons_newest_first(self):
        p = _build("KC")
        assert [h["season"] for h in p["history"]] == [2025, 2024]
        h25, h24 = p["history"]
        assert (h25["wins"], h25["losses"], h25["ties"]) == (14, 3, 0)
        assert h25["drafter"] == {"playerId": 1, "name": "Alice Smith"} and h25["pick"] == 4
        assert h24["drafter"]["playerId"] == 2 and h24["pick"] == 2

    def test_pool_winner_per_completed_season(self):
        p = _build("KC")
        # 2025: Alice has KC (14), Bob has DEN (9) -> Alice wins
        assert p["history"][0]["pool_winner"] == {"playerId": 1, "name": "Alice Smith", "wins": 14}
        # 2024: Bob has KC (15), Alice DEN (10) -> Bob wins
        assert p["history"][1]["pool_winner"] == {"playerId": 2, "name": "Bob Jones", "wins": 15}

    def test_never_drafted_team_has_empty_history(self):
        p = _build("SEA")
        assert p["history"] == []
        assert p["current"]["record"] == {"wins": 0, "losses": 0, "ties": 0}

    def test_team_missing_from_standings_does_not_raise(self):
        d = _data()
        d["standings"] = d["standings"][d["standings"]["team"] != "DEN"]
        p = tps.build_team_page("DEN", 2026, d, True)
        assert [h["wins"] for h in p["history"]] == [0, 0]

    def test_current_schedule_overlay(self):
        cur = _build("KC")["current"]
        assert cur["record"] == {"wins": 2, "losses": 0, "ties": 0}
        rows = {r["week"]: r for r in cur["schedule"]}
        assert rows[1] == {"week": 1, "opponent": "DEN", "home": True, "status": "played",
                           "result": "W", "score": "24-17", "win_prob": None, "projected": None}
        assert rows[2]["home"] is False and rows[2]["result"] == "W" and rows[2]["score"] == "23-20"
        assert rows[3]["status"] == "unplayed" and rows[3]["win_prob"] == 0.7 and rows[3]["projected"] == "W"
        # away team uses 1 - pred_prob
        assert rows[5]["win_prob"] == pytest.approx(0.3) and rows[5]["projected"] == "L"
        # no stored prediction
        assert rows[6]["win_prob"] is None and rows[6]["projected"] is None
        assert cur["projected_wins"] == 11.4

    def test_projected_record_none_when_any_unplayed_game_unpredicted(self):
        # Default data: week 6 is unplayed with no stored prediction, so a
        # partial sum would fall short of the season length; contract is None.
        cur = _build("KC")["current"]
        assert cur["projected_record"] is None

    def test_projected_record_sums(self):
        preds = {"W03_KC_LV": {"pred_prob": 0.7}, "W05_DEN_KC": {"pred_prob": 0.7},
                 "W06_KC_DEN": {"pred_prob": 0.6}}
        cur = _build("KC", predictions=preds)["current"]
        assert cur["projected_record"] == {"wins": 4, "losses": 1}
        played = cur["record"]["wins"] + cur["record"]["losses"] + cur["record"]["ties"]
        unplayed = sum(r["status"] == "unplayed" for r in cur["schedule"])
        assert cur["projected_record"]["wins"] + cur["projected_record"]["losses"] == played + unplayed

    def test_exact_toss_up_is_neutral(self):
        preds = {"W03_KC_LV": {"pred_prob": 0.5}, "W05_DEN_KC": {"pred_prob": 0.7},
                 "W06_KC_DEN": {"pred_prob": 0.6}}
        cur = _build("KC", predictions=preds)["current"]
        row = {r["week"]: r for r in cur["schedule"]}[3]
        assert row["win_prob"] == 0.5 and row["projected"] is None
        assert cur["projected_record"] is None

    def test_nan_player_name_falls_back(self):
        d = _data()
        d["players"] = pd.DataFrame([{"playerId": 2, "fullName": float("nan")},
                                     {"playerId": 1, "fullName": "  "}])
        p = tps.build_team_page("KC", 2026, d, True)
        names = [h["drafter"]["name"] for h in p["history"]]
        assert names == ["Player 1", "Player 2"]

    def test_pool_winner_computed_once_per_season(self):
        d = _data()
        # two rows for the same completed season (same team drafted twice)
        d["draft_results"] = pd.concat([d["draft_results"], pd.DataFrame(
            [{"playerId": 1, "season": 2025, "draftPick": 9, "team": "KC"}])], ignore_index=True)
        import services.analysis_service as analysis
        calls = []
        real = analysis.calculate_wins_pool_standings
        def spy(*a, **k):
            calls.append(a[3])
            return real(*a, **k)
        with patch.object(analysis, "calculate_wins_pool_standings", spy):
            tps.build_team_page("KC", 2026, d, True)
        assert len(calls) == len(set(calls))

    def test_legacy_team_code_in_draft_results_matches(self):
        d = _data()
        d["draft_results"] = pd.DataFrame([
            {"playerId": 1, "season": 2025, "draftPick": 4, "team": "LAR"}])
        p = tps.build_team_page("LA", 2026, d, True)
        assert [h["season"] for h in p["history"]] == [2025]

    def test_bye_week_row(self):
        rows = {r["week"]: r for r in _build("KC")["current"]["schedule"]}
        assert rows[4] == {"week": 4, "opponent": None, "home": None, "status": "bye",
                           "result": None, "score": None, "win_prob": None, "projected": None}

    def test_ties_and_null_results_not_counted(self):
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
             "result": 0, "home_score": 20, "away_score": 20},
            {"season": 2026, "week": 2, "game_type": "REG", "home_team": "KC", "away_team": "LV",
             "result": float("nan"), "home_score": None, "away_score": None},
        ])
        cur = _build("KC", games=games, predictions={})["current"]
        assert cur["record"] == {"wins": 0, "losses": 0, "ties": 1}
        assert cur["schedule"][0]["result"] == "T"
        assert cur["schedule"][1]["status"] == "unplayed"
        assert cur["projected_record"] is None  # unplayed games but no predictions

    def test_no_games_played_yet(self):
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "home_team": "KC", "away_team": "DEN",
             "result": None, "home_score": None, "away_score": None}])
        p = _build("KC", games=games, predictions={"W01_KC_DEN": {"pred_prob": 0.6}})
        assert p["current"]["record"] == {"wins": 0, "losses": 0, "ties": 0}
        assert p["current"]["projected_record"] == {"wins": 1, "losses": 0}
        json.dumps(p, allow_nan=False)

    def test_empty_games_frame(self):
        p = _build("KC", games=pd.DataFrame())
        assert p["current"]["schedule"] == []

    def test_include_projections_false_nulls_projection_fields(self):
        p = _build("KC", include=False)
        cur = p["current"]
        assert cur["projected_wins"] is None and cur["projected_record"] is None
        for r in cur["schedule"]:
            assert r["win_prob"] is None and r["projected"] is None
        # results and history still render
        assert cur["record"]["wins"] == 2 and len(p["history"]) == 2 and len(p["teams"]) == 32
        assert cur["schedule"][0]["result"] == "W"

    def test_payload_json_has_no_nan(self):
        json.dumps(_build("KC"), allow_nan=False)


def _load_tuple(draft=None):
    d = _data()
    return (d["standings"], pd.DataFrame(), d["games"], d["players"], pd.DataFrame(),
            d["draft_results"] if draft is None else draft, pd.DataFrame())


def _patches(draft_active=False):
    return (
        patch("routes.history_routes.load_data", return_value=_load_tuple()),
        patch("routes.history_routes.get_active_season", return_value=2026),
        patch("routes.history_routes.get_game_predictions", return_value=_data()["predictions"]),
        patch("routes.history_routes.get_season_projection_legacy_shape",
              return_value=_data()["projections"]),
        patch("routes.history_routes.get_config_settings", return_value={"draft_active": draft_active}),
    )


def _get(path, cookie=None, draft_active=False):
    ps = _patches(draft_active)
    for p in ps:
        p.start()
    try:
        return client.get(path, cookies={"session_token": cookie} if cookie else None)
    finally:
        for p in ps:
            p.stop()


def _embedded(html):
    m = re.search(r'<script id="teamData" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


class TestTeamRoutes:
    def test_team_page_case_insensitive(self):
        assert _get("/team/kc").status_code == 200
        r = _get("/team/KC")
        assert r.status_code == 200 and "Kansas City Chiefs" in r.text

    def test_unknown_team_404(self):
        assert _get("/team/XXX").status_code == 404

    def test_select_has_32_options_and_no_nan(self):
        r = _get("/team/KC")
        sel = re.search(r'<select[^>]*id="team-select".*?</select>', r.text, re.S).group(0)
        assert sel.count("<option") == 32
        blob = r.text.split('id="teamData"')[1].split("</script>")[0]
        assert not re.search(r"\bnan\b", blob, re.I)

    def test_projections_hidden_for_non_admin_during_draft(self):
        tok = create_token(1, "player")
        p = _embedded(_get("/team/KC", cookie=tok, draft_active=True).text)
        assert p["current"]["projected_wins"] is None
        p = _embedded(_get("/team/KC", cookie=tok, draft_active=False).text)
        assert p["current"]["projected_wins"] == 11.4
        admin = create_token(9, "admin")
        p = _embedded(_get("/team/KC", cookie=admin, draft_active=True).text)
        assert p["current"]["projected_wins"] == 11.4

    def test_teams_redirects_to_first_drafted_team_with_cookie(self):
        d = _data()
        draft = pd.concat([d["draft_results"], pd.DataFrame([
            {"playerId": 2, "season": 2026, "draftPick": 9, "team": "SEA"},
            {"playerId": 2, "season": 2026, "draftPick": 3, "team": "DEN"}])], ignore_index=True)
        with patch("routes.history_routes.load_data", return_value=_load_tuple(draft)), \
             patch("routes.history_routes.get_active_season", return_value=2026):
            r = client.get("/teams", cookies={"session_token": create_token(2, "player")})
        assert r.status_code in (302, 307) and r.headers["location"] == "/team/DEN"

    def test_teams_redirects_to_first_alphabetical_without_cookie(self):
        r = _get("/teams")
        assert r.status_code in (302, 307) and r.headers["location"] == "/team/ARI"

    def test_teams_invalid_cookie_falls_back(self):
        r = _get("/teams", cookie="garbage")
        assert r.headers["location"] == "/team/ARI"


class TestTeamsNav:
    def test_teams_link_in_both_navs(self):
        js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        assert "href: '/teams'" in js and "label: 'Teams'" in js
        assert 'href="/teams"' in base
