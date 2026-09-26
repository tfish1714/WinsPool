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


def _render(week, monkeypatch, schedule=None):
    # Patch the names routes/standings_routes.py actually imports.
    import routes.standings_routes as sr
    monkeypatch.setattr(sr, "load_data", lambda *a, **k: _load(week))
    monkeypatch.setattr(sr, "get_latest_week_for_year", lambda games, year: week)
    monkeypatch.setattr(sr.analysis, "get_enriched_schedule", lambda *a, **k: _schedule() if schedule is None else schedule)
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


def _wins_pool_frame():
    """Two players tied on wins, every column templates/wins_pool.html reads."""
    def row(pid, name, rank, tb1):
        return {
            "playerId": pid, "fullName": name, "TotalWins": 9, "Rank": rank,
            "team1": "BAL", "team2": "KC", "team3": "SF",
            "wins1": 3, "wins2": 3, "wins3": 3,
            "ptDiff1": 10, "ptDiff2": -4, "ptDiff3": 7,
            "Tiebreaker1_WorstTeamWins": tb1, "Tiebreaker2_2ndWorstTeamWins": 3,
            "Tiebreaker3_BestTeamWins": 3, "Tiebreaker4_WorstTeamPtDiff": -4,
            "Tiebreaker5_2ndWorstTeamPtDiff": 7, "Tiebreaker6_BestTeamPtDiff": 10,
            "refreshTime": "now",
        }
    return pd.DataFrame([row(1, "Ann Lee", 1, 3), row(2, "Bo Kim", 2, 2)])


def _real_season_frames():
    """A small consistent season so the real analysis functions can run."""
    empty = pd.DataFrame()
    games = pd.DataFrame([
        {"season": YEAR, "week": 1, "game_type": "REG", "home_team": "KC", "away_team": "BAL",
         "result": 3, "home_score": 27, "away_score": 24, "gameday": "2000-09-10"},
        {"season": YEAR, "week": 2, "game_type": "REG", "home_team": "BAL", "away_team": "KC",
         "result": -1000, "home_score": None, "away_score": None, "gameday": "2000-09-17"},
    ])
    # The template reads three team slots per player.
    picks = [(1, "KC", 1), (2, "BAL", 2), (2, "DEN", 3), (1, "SF", 4), (1, "NE", 5), (2, "LA", 6)]
    draft = pd.DataFrame([
        {"season": YEAR, "team": t, "playerId": p, "draftPick": n} for p, t, n in picks
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Ann Lee", "nickName": "Ann"},
                            {"playerId": 2, "fullName": "Bo Kim", "nickName": "Bo"}])
    standings = pd.DataFrame([
        {"team": "KC", "season": YEAR, "wins": 1, "losses": 0, "ties": 0, "scored": 27, "allowed": 24},
        {"team": "BAL", "season": YEAR, "wins": 0, "losses": 1, "ties": 0, "scored": 24, "allowed": 27},
    ])
    return standings, empty, games, players, empty, draft, empty


def _render_wins_pool(monkeypatch, real_analysis=False):
    import routes.standings_routes as sr
    empty = pd.DataFrame()
    if real_analysis:
        # Leave the real analysis functions in place; only feed load_data.
        monkeypatch.setattr(sr, "load_data", lambda *a, **k: _real_season_frames())
        monkeypatch.setattr(sr, "get_available_years", lambda *a, **k: [YEAR])
        monkeypatch.setattr(sr, "get_active_season", lambda *a, **k: YEAR)
        monkeypatch.setattr(sr.db, "get_weekly_recap", lambda *a, **k: None)
        return client.get(f"/wins-pool/{YEAR}")
    games = pd.DataFrame([{"season": YEAR, "week": 5, "result": 1}])
    monkeypatch.setattr(sr, "load_data", lambda *a, **k: (empty, empty, games, empty, empty, empty, empty))
    monkeypatch.setattr(sr, "filter_season", lambda df, year: df)
    monkeypatch.setattr(sr.analysis, "get_draft_progress", lambda *a, **k: (0, 0))
    monkeypatch.setattr(sr.analysis, "calculate_wins_pool_standings", lambda *a, **k: _wins_pool_frame())
    # The stub games frame has no team columns; the route now precomputes records.
    monkeypatch.setattr(sr.analysis, "compute_team_records", lambda *a, **k: {})
    monkeypatch.setattr(sr.analysis, "get_enriched_schedule", lambda *a, **k: empty)
    monkeypatch.setattr(sr.analysis, "player_winlossmatrix", lambda *a, **k: empty)
    monkeypatch.setattr(sr, "get_latest_week_for_year", lambda games, year: 5)
    monkeypatch.setattr(sr, "get_available_years", lambda *a, **k: [YEAR])
    monkeypatch.setattr(sr, "get_active_season", lambda *a, **k: YEAR)
    monkeypatch.setattr(sr.db, "get_weekly_recap", lambda *a, **k: None)
    return client.get(f"/wins-pool/{YEAR}")


def test_wins_pool_page_loads_tiebreaker_module_and_legend(monkeypatch):
    resp = _render_wins_pool(monkeypatch)
    assert resp.status_code == 200
    assert "tiebreaker_explain.js" in resp.text
    assert 'id="tb-tooltip"' in resp.text
    assert "worst team wins" in resp.text.lower()
    # Existing legend text is kept and the cascade sentence is added.
    assert "TB1 = worst team wins" in resp.text
    assert "ties are broken by TB1 through TB6 in order" in resp.text


def test_wins_pool_page_keeps_dom_hooks_the_highlighter_reads(monkeypatch):
    html = _render_wins_pool(monkeypatch).text
    # Stacked cards are the highlighter's data source: name + tb1..tb6 per player.
    assert html.count('class="standings-stacked-card"') == 2
    assert html.count('class="standings-stacked-card__name"') == 2
    for n in range(1, 7):
        assert html.count(f'data-role="tb{n}"') >= 2


def test_live_refresh_announces_patches_so_highlights_recompute():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "standings_refresh.js").read_text(encoding="utf-8")
    assert "standings:patched" in src


def test_completed_season_three_way_tie_renders_tied_not_eliminated(monkeypatch):
    # Cyclic results: Ann beats Bo, Bo beats Cy, Cy beats Ann -> 1 win each,
    # no games left. A tie on wins is settled by tiebreakers, so nobody is
    # eliminated and nobody has clinched; the cell must say so.
    schedule = pd.DataFrame([
        {"fullName_away": "Bo", "fullName_home": "Ann", "result": 3, "week": 1},
        {"fullName_away": "Cy", "fullName_home": "Bo", "result": 3, "week": 2},
        {"fullName_away": "Ann", "fullName_home": "Cy", "result": 3, "week": 3},
    ])
    resp = _render(PLAYOFF_RACE_MIN_WEEK, monkeypatch, schedule)
    assert resp.status_code == 200
    assert resp.text.count("Tied (tiebreakers)") >= 3
    assert "Magic #: <strong>Eliminated</strong>" not in resp.text
    assert "Podium #: <strong>Eliminated</strong>" not in resp.text


def test_wins_pool_by_year_computes_team_records_exactly_once(monkeypatch):
    import routes.standings_routes as sr
    calls = []
    real = sr.analysis.compute_team_records

    def spy(games, season):
        calls.append(season)
        return real(games, season)

    real_calc = sr.analysis.calculate_wins_pool_standings
    outputs = []

    def capture(*a, **k):
        out = real_calc(*a, **k)
        outputs.append(out)
        return out

    monkeypatch.setattr(sr.analysis, "compute_team_records", spy)
    monkeypatch.setattr(sr.analysis, "calculate_wins_pool_standings", capture)
    resp = _render_wins_pool(monkeypatch, real_analysis=True)
    assert resp.status_code == 200
    assert calls == [YEAR]
    # The shared records still reach the standings: KC is 1-0, BAL is 0-1.
    by_player = outputs[0].set_index("playerId")
    assert by_player.loc[1, "global_record1"] == "1-0"
    assert by_player.loc[2, "global_record1"] == "0-1"
