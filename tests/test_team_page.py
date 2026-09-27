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


def _data(games=None, predictions=None, projections=None, preseason_projections=None,
          current_projection=None):
    standings = pd.DataFrame([
        {"season": 2025, "team": "KC", "wins": 14, "losses": 3, "ties": 0},
        {"season": 2024, "team": "KC", "wins": 15, "losses": 2, "ties": 0},
        {"season": 2024, "team": "DEN", "wins": 10, "losses": 7, "ties": 0},
        {"season": 2025, "team": "DEN", "wins": 9, "losses": 8, "ties": 0},
        {"season": 2025, "team": "LV", "wins": 1, "losses": 16, "ties": 0},
        {"season": 2025, "team": "SF", "wins": 1, "losses": 16, "ties": 0},
        {"season": 2024, "team": "ARI", "wins": 1, "losses": 16, "ties": 0},
        {"season": 2024, "team": "NE", "wins": 1, "losses": 16, "ties": 0},
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
        {"playerId": 1, "season": 2025, "draftPick": 6, "team": "LV"},
        {"playerId": 1, "season": 2025, "draftPick": 7, "team": "SF"},
        {"playerId": 2, "season": 2024, "draftPick": 8, "team": "ARI"},
        {"playerId": 2, "season": 2024, "draftPick": 9, "team": "NE"},
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
            "projections": projections if projections is not None else {"KC": {"projected_wins": 11.4}},
            "preseason_projections": preseason_projections if preseason_projections is not None else {},
            "current_projection": current_projection if current_projection is not None else {}}


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

    def test_pool_winner_only_on_rows_where_drafter_is_winner(self):
        p = _build("KC")
        # 2025: Alice has KC (14)+LV+SF (16 total) vs Bob DEN (9) -> Alice wins, and drafted KC
        h25, h24 = p["history"]
        assert h25["pool_winner"] == {"playerId": 1, "name": "Alice Smith", "wins": 16}
        assert h25["winning_combo"] == ["LV", "SF"]
        # 2024: Bob has KC+ARI+NE (17) vs Alice DEN (10) -> Bob wins, drafted KC
        assert h24["pool_winner"] == {"playerId": 2, "name": "Bob Jones", "wins": 17}
        assert h24["winning_combo"] == ["ARI", "NE"]

    def test_no_callout_when_drafter_did_not_win(self):
        p = _build("DEN")
        # DEN: 2025 drafted by Bob (2nd), 2024 by Alice (2nd)
        for h in p["history"]:
            assert h["pool_winner"] is None
            assert not h.get("winning_combo")

    def test_callout_only_in_season_winner_drafted_team(self):
        d = _data()
        # 2024: Alice (drafter of DEN) now wins big; Bob drafted KC and loses
        d["standings"].loc[(d["standings"]["season"] == 2024) & (d["standings"]["team"] == "DEN"), "wins"] = 17
        d["standings"].loc[(d["standings"]["season"] == 2024) & (d["standings"]["team"] == "KC"), "wins"] = 0
        p = tps.build_team_page("KC", 2026, d, True)
        h25, h24 = p["history"]
        assert h25["pool_winner"]["playerId"] == 1 and h25["winning_combo"] == ["LV", "SF"]
        assert h24["drafter"]["playerId"] == 2
        assert h24["pool_winner"] is None and not h24["winning_combo"]

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
        patch("routes.history_routes.is_draft_active_fail_closed", return_value=draft_active),
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


def test_team_page_js_renders_winning_combo_conditionally():
    src = (ROOT / "static" / "js" / "team_page.js").read_text(encoding="utf-8")
    assert "winning_combo" in src and "pool_winner" in src
    assert re.search(r"if\s*\(\s*h\.pool_winner\s*\)", src)
    assert "Pool winner:" not in src


# ---- runner-up callout + never-winner marker --------------------------------

def _who(pid, name="X", wins=0):
    return {"playerId": pid, "name": name, "wins": wins}


def _multi_season_data(seasons, drafter_of_kc=1):
    """KC drafted by `drafter_of_kc` in every season in `seasons`."""
    rows = [{"playerId": drafter_of_kc, "season": s, "draftPick": 1, "team": "KC"} for s in seasons]
    d = _data()
    d["draft_results"] = pd.DataFrame(rows)
    return d


class TestRunnerUp:
    def test_runner_up_row_only_where_drafter_was_second(self):
        # fixture: 2025 Alice wins / Bob (DEN) 2nd; 2024 Bob wins / Alice (DEN) 2nd
        p = _build("DEN")
        h25, h24 = p["history"]
        assert h25["runner_up"] == {"playerId": 2, "name": "Bob Jones", "wins": 9}
        assert h25["pool_winner"] is None
        assert h24["runner_up"] == {"playerId": 1, "name": "Alice Smith", "wins": 10}
        assert h24["pool_winner"] is None

    def test_never_on_winner_rows_or_other_rows(self):
        p = _build("KC")  # KC drafters won both seasons
        for h in p["history"]:
            assert h["pool_winner"] is not None
            assert h["runner_up"] is None and h["runner_up_combo"] is None

    def test_combo_lists_other_two_teams(self):
        d = _data()
        # give Bob (2nd in 2025 with DEN 9) two more teams, one legacy code and a duplicate
        extra = pd.DataFrame([
            {"playerId": 2, "season": 2025, "draftPick": 10, "team": "MIA"},
            {"playerId": 2, "season": 2025, "draftPick": 11, "team": "OAK"},
            {"playerId": 2, "season": 2025, "draftPick": 12, "team": "MIA"},
        ])
        d["draft_results"] = pd.concat([d["draft_results"], extra], ignore_index=True)
        d["standings"] = pd.concat([d["standings"], pd.DataFrame([
            {"season": 2025, "team": "MIA", "wins": 0, "losses": 17, "ties": 0},
            {"season": 2025, "team": "LV", "wins": 1, "losses": 16, "ties": 0},
        ])], ignore_index=True)
        p = tps.build_team_page("DEN", 2026, d, True)
        h25 = p["history"][0]
        assert h25["runner_up"]["playerId"] == 2
        assert h25["runner_up_combo"] == ["MIA", "LV"]

    def test_runner_up_one_season_winner_another(self):
        d = _data()
        # Alice drafts KC in 2025 (wins) ; make Alice also KC drafter in 2024 as runner-up
        d["draft_results"] = pd.DataFrame([
            {"playerId": 1, "season": 2025, "draftPick": 4, "team": "KC"},
            {"playerId": 2, "season": 2025, "draftPick": 5, "team": "DEN"},
            {"playerId": 1, "season": 2024, "draftPick": 3, "team": "KC"},
            {"playerId": 2, "season": 2024, "draftPick": 2, "team": "ARI"},
            {"playerId": 2, "season": 2024, "draftPick": 8, "team": "NE"},
        ])
        d["standings"].loc[(d["standings"]["season"] == 2024) & (d["standings"]["team"] == "KC"), "wins"] = 0
        p = tps.build_team_page("KC", 2026, d, True)
        h25, h24 = p["history"]
        assert h25["pool_winner"]["playerId"] == 1 and h25["runner_up"] is None
        assert h24["runner_up"]["playerId"] == 1 and h24["pool_winner"] is None
        assert h24["runner_up_combo"] == []

    def test_in_progress_season_has_no_runner_up(self):
        d = _multi_season_data([2026])
        d["standings"] = pd.concat([d["standings"], pd.DataFrame(
            [{"season": 2026, "team": "KC", "wins": 5, "losses": 0, "ties": 0}])], ignore_index=True)
        p = tps.build_team_page("KC", 2026, d, True)
        assert p["history"][0]["runner_up"] is None and p["history"][0]["runner_up_combo"] is None

    def test_standings_still_computed_once_per_season_with_runner_up(self):
        import services.analysis_service as analysis
        calls = []
        real = analysis.calculate_wins_pool_standings
        def spy(*a, **k):
            calls.append(a[3])
            return real(*a, **k)
        with patch.object(analysis, "calculate_wins_pool_standings", spy):
            tps.build_team_page("DEN", 2026, _data(), True)
        assert sorted(calls) == [2024, 2025]


class TestPoolSummary:
    def _summary(self, seasons, tops, current=2030):
        """tops: {season: (winner_pid, runner_pid)}"""
        d = _multi_season_data(seasons)
        def fake(data, season):
            w, r = tops[season]
            return _who(w), _who(r)
        with patch.object(tps, "_pool_top_two", fake):
            return tps.build_team_page("KC", current, d, True)["pool_summary"]

    def test_counts_and_flags(self):
        s = self._summary([2020, 2021, 2022, 2023],
                          {2020: (1, 2), 2021: (2, 1), 2022: (2, 3), 2023: (3, 2)})
        assert s == {"seasons": 4, "winning": 1, "runner_up": 1, "never_won": False, "never_top2": False}

    def test_never_won_but_runner_up(self):
        s = self._summary([2020, 2021, 2022], {2020: (2, 1), 2021: (2, 3), 2022: (3, 2)})
        assert s["never_won"] is True and s["never_top2"] is False and s["runner_up"] == 1

    def test_never_top2_at_threshold(self):
        s = self._summary([2020, 2021, 2022], {y: (2, 3) for y in (2020, 2021, 2022)})
        assert s["seasons"] == tps.MIN_SEASONS_FOR_MARKER == 3
        assert s["never_won"] is True and s["never_top2"] is True

    def test_below_threshold_no_marker(self):
        s = self._summary([2020, 2021], {y: (2, 3) for y in (2020, 2021)})
        assert s["seasons"] == 2 and s["never_won"] is False and s["never_top2"] is False

    def test_in_progress_season_excluded(self):
        s = self._summary([2020, 2021, 2030], {2020: (2, 3), 2021: (2, 3), 2030: (2, 3)})
        assert s["seasons"] == 2 and s["never_top2"] is False

    def test_failed_standings_seasons_not_counted(self):
        d = _multi_season_data([2020, 2021, 2022, 2023])
        def fake(data, season):
            return (None, None) if season in (2020, 2021) else (_who(2), _who(3))
        with patch.object(tps, "_pool_top_two", fake):
            s = tps.build_team_page("KC", 2030, d, True)["pool_summary"]
        assert s["seasons"] == 2
        assert s["never_won"] is False and s["never_top2"] is False

    def test_no_history_summary_zero(self):
        s = _build("SEA")["pool_summary"]
        assert s == {"seasons": 0, "winning": 0, "runner_up": 0, "never_won": False, "never_top2": False}


def test_team_page_js_renders_runner_up_and_summary_conditionally():
    src = (ROOT / "static" / "js" / "team_page.js").read_text(encoding="utf-8")
    assert "runner_up_combo" in src and "pool_summary" in src
    assert re.search(r"if\s*\(\s*h\.runner_up\s*\)", src)
    assert "never_top2" in src and "never_won" in src
    assert re.search(r"if\s*\(\s*s\.never_top2\s*\)", src)
    assert "team-page__combo--runnerup" in src
    assert ".team-page__combo--runnerup" in (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_team_page_header_labels_preseason_projection():
    """The header value is the preseason projection, and must be labelled as such."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "team_page.js").read_text(encoding="utf-8")
    assert "Preseason projection: " in src
    assert "'Projected wins: '" not in src


# ---- Task 2: preseason (frozen) vs current (results-aware) projection ----

def _cur(**kw):
    base = {"projected_wins": 12.3, "mean_wins": 12.3, "std_dev": 1.1, "floor": 10.0,
            "p25": 11.0, "p75": 13.0, "ceiling": 14.0, "as_of_week": 3, "locked": False}
    base.update(kw)
    return {"KC": base}


class TestCurrentVsPreseasonProjection:
    def test_both_present(self):
        p = _build("KC", preseason_projections={"KC": {"projected_wins": 10.5}},
                   current_projection=_cur())
        cur = p["current"]
        assert cur["preseason_projection"] == 10.5
        assert cur["current_projection"] == {"projected_wins": 12.3, "floor": 10.0,
                                             "ceiling": 14.0, "as_of_week": 3}

    def test_current_projection_none_when_no_data(self):
        p = _build("KC", preseason_projections={"KC": {"projected_wins": 10.5}}, current_projection={})
        assert p["current"]["current_projection"] is None
        assert p["current"]["preseason_projection"] == 10.5

    def test_preseason_falls_back_to_legacy_projection_when_no_frozen_value(self):
        p = _build("KC", preseason_projections={})
        assert p["current"]["preseason_projection"] == 11.4  # legacy-shape projections fixture

    def test_gated_when_projections_excluded(self):
        p = _build("KC", include=False, preseason_projections={"KC": {"projected_wins": 10.5}},
                   current_projection=_cur())
        assert p["current"]["preseason_projection"] is None
        assert p["current"]["current_projection"] is None

    def test_payload_json_has_no_nan(self):
        p = _build("KC", current_projection=_cur(floor=float("nan")))
        json.dumps(p, allow_nan=False)


def _route_patches(frozen, preseason, current, draft_active=False):
    return _patches(draft_active) + (
        patch("routes.history_routes.get_draft_snapshot_predictions", return_value=frozen),
        patch("routes.history_routes.get_preseason_predictions", return_value=preseason),
        patch("routes.history_routes.get_season_projection_current", return_value=current),
    )


def _get_with(frozen, preseason, current, cookie=None, draft_active=False):
    ps = _route_patches(frozen, preseason, current, draft_active)
    for p in ps:
        p.start()
    try:
        return client.get("/team/KC", cookies={"session_token": cookie} if cookie else None)
    finally:
        for p in ps:
            p.stop()


class TestProjectionRoute:
    FROZEN = {"KC": {"projected_wins": 10.0}}
    DAILY = {"KC": {"projected_wins": 13.0}}

    def test_preseason_reads_frozen_snapshot_not_daily_doc(self):
        p = _embedded(_get_with(self.FROZEN, self.DAILY, _cur()).text)
        assert p["current"]["preseason_projection"] == 10.0
        assert p["current"]["current_projection"]["as_of_week"] == 3

    def test_falls_back_to_preseason_predictions_when_snapshot_missing(self):
        p = _embedded(_get_with({}, self.DAILY, {}).text)
        assert p["current"]["preseason_projection"] == 13.0
        assert p["current"]["current_projection"] is None

    def test_hidden_from_non_admin_during_draft(self):
        tok = create_token(1, "player")
        p = _embedded(_get_with(self.FROZEN, self.DAILY, _cur(), cookie=tok, draft_active=True).text)
        assert p["current"]["preseason_projection"] is None
        assert p["current"]["current_projection"] is None
        admin = create_token(9, "admin")
        p = _embedded(_get_with(self.FROZEN, self.DAILY, _cur(), cookie=admin, draft_active=True).text)
        assert p["current"]["current_projection"]["projected_wins"] == 12.3


def test_team_page_js_renders_both_projections_textcontent_only():
    src = (ROOT / "static" / "js" / "team_page.js").read_text(encoding="utf-8")
    assert "Preseason projection: " in src
    assert "Current projection: " in src
    assert "as of week " in src
    assert "current_projection" in src and "preseason_projection" in src
    assert "innerHTML" not in src


class TestProjectionReadFailure:
    def _get_raising(self, which):
        ps = list(_patches(False)) + [
            patch("routes.history_routes.get_draft_snapshot_predictions",
                  side_effect=RuntimeError("boom") if which == "snap" else None, return_value={"KC": {"projected_wins": 10.0}}),
            patch("routes.history_routes.get_preseason_predictions", return_value={}),
            patch("routes.history_routes.get_season_projection_current",
                  side_effect=RuntimeError("boom") if which == "cur" else None, return_value=_cur()),
        ]
        for p in ps:
            p.start()
        try:
            return client.get("/team/KC")
        finally:
            for p in ps:
                p.stop()

    def test_preseason_read_failure_still_renders(self):
        r = self._get_raising("snap")
        assert r.status_code == 200
        cur = _embedded(r.text)["current"]
        assert cur["preseason_projection"] is None
        assert cur["current_projection"]["as_of_week"] == 3

    def test_current_read_failure_still_renders(self):
        r = self._get_raising("cur")
        assert r.status_code == 200
        cur = _embedded(r.text)["current"]
        assert cur["current_projection"] is None
        assert cur["preseason_projection"] == 10.0
