"""Tests for scripts/scrape_quarter_scores.py: ESPN scoreboard parsing, the
historical-season mismatch guard, `weeks` scoping (used by the weekly
incremental scrape wired into schedule_kickoffs.py), and --week/--firestore
CLI wiring. See docs/superpowers/specs/2026-09-15-comeback-win-recap-design.md.
"""
import pytest

from scripts.scrape_quarter_scores import main, scrape_season, _parse_espn_event


def _linescores(*values):
    return [{"value": v, "period": i + 1} for i, v in enumerate(values)]


def _fake_event(event_id, home, away, home_q, away_q, home_score, away_score, completed=True):
    """Build a minimal ESPN scoreboard event matching the real API shape."""
    return {
        "id": event_id,
        "competitions": [{
            "status": {"type": {"completed": completed}},
            "competitors": [
                {"homeAway": "home", "team": {"abbreviation": home},
                 "score": str(home_score), "linescores": _linescores(*home_q)},
                {"homeAway": "away", "team": {"abbreviation": away},
                 "score": str(away_score), "linescores": _linescores(*away_q)},
            ],
        }],
    }


def _fake_week_response(season, events):
    return {"leagues": [{"season": {"year": season}}], "events": events}


class TestParseEspnEvent:
    def test_completed_game_parses_all_fields(self):
        event = _fake_event("1", "KC", "DEN", [7, 7, 7, 10], [0, 3, 7, 0], 31, 10)
        result = _parse_espn_event(event)
        assert result == {
            "home_team": "KC", "away_team": "DEN",
            "home_q1": 7, "home_q2": 7, "home_q3": 7, "home_q4": 10, "home_ot": 0,
            "away_q1": 0, "away_q2": 3, "away_q3": 7, "away_q4": 0, "away_ot": 0,
            "home_score": 31, "away_score": 10,
        }

    def test_overtime_periods_sum_into_ot_field(self):
        event = _fake_event("2", "DET", "NO", [7, 0, 14, 3, 7], [0, 0, 14, 10, 6], 31, 30)
        result = _parse_espn_event(event)
        assert result["home_ot"] == 7
        assert result["away_ot"] == 6

    def test_not_yet_completed_game_returns_none(self):
        event = _fake_event("3", "KC", "DEN", [7, 0, 0, 0], [0, 0, 0, 0], 7, 0, completed=False)
        assert _parse_espn_event(event) is None

    def test_missing_linescores_returns_none(self):
        event = {
            "id": "4",
            "competitions": [{
                "status": {"type": {"completed": True}},
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": "KC"}, "score": "31", "linescores": []},
                    {"homeAway": "away", "team": {"abbreviation": "DEN"}, "score": "10", "linescores": []},
                ],
            }],
        }
        assert _parse_espn_event(event) is None

    def test_team_abbreviation_normalized_to_nflverse_convention(self):
        # ESPN returns WSH/LAR; the rest of the app (and this CSV's own
        # existing rows) use WAS/LA.
        event = _fake_event("5", "PHI", "WSH", [0, 14, 3, 7], [3, 6, 0, 13], 24, 22)
        result = _parse_espn_event(event)
        assert result["away_team"] == "WAS"


class TestScrapeSeasonEspn:
    def test_dry_run_default_covers_full_season(self, capsys, monkeypatch):
        # 2020 is a 17-week season (pre-2021 expansion). No fetch should happen.
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda *a, **k: pytest.fail("dry-run must not fetch"))
        scrape_season(2020, existing={}, expected={}, dry_run=True)
        out = capsys.readouterr().out
        assert out.count("[DRY RUN]") == 17

    def test_dry_run_weeks_param_restricts_to_given_weeks(self, capsys, monkeypatch):
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda *a, **k: pytest.fail("dry-run must not fetch"))
        scrape_season(2020, existing={}, expected={}, dry_run=True, weeks=[5])
        out = capsys.readouterr().out
        assert out.count("[DRY RUN]") == 1
        assert "week=5" in out

    def test_fetches_and_parses_completed_games(self, monkeypatch):
        events = [_fake_event("1", "KC", "DEN", [7, 7, 7, 10], [0, 3, 7, 0], 31, 10)]
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda season, week: _fake_week_response(season, events))
        rows = scrape_season(2026, existing={}, expected={}, weeks=[1])
        assert len(rows) == 1
        assert rows[0]["home_team"] == "KC"
        assert rows[0]["season"] == 2026 and rows[0]["week"] == 1

    def test_already_cached_game_is_skipped(self, monkeypatch):
        events = [_fake_event("1", "KC", "DEN", [7, 7, 7, 10], [0, 3, 7, 0], 31, 10)]
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda season, week: _fake_week_response(season, events))
        rows = scrape_season(2026, existing={(2026, 1): {"KC"}}, expected={}, weeks=[1])
        assert rows == []

    def test_historical_season_mismatch_stops_scraping(self, monkeypatch):
        """Regression test: ESPN's scoreboard endpoint silently returns the
        CURRENT season when an older `year` isn't honored -- must never
        ingest that as if it were the requested historical season."""
        events = [_fake_event("1", "KC", "DEN", [7, 7, 7, 10], [0, 3, 7, 0], 31, 10)]
        # Requested season 2015, but the response claims season 2026.
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda season, week: _fake_week_response(2026, events))
        rows = scrape_season(2015, existing={}, expected={}, weeks=[1, 2, 3])
        assert rows == []

    def test_fetch_failure_is_skipped_not_fatal(self, monkeypatch):
        monkeypatch.setattr("scripts.scrape_quarter_scores._fetch_espn_week",
                             lambda season, week: None)
        rows = scrape_season(2026, existing={}, expected={}, weeks=[1])
        assert rows == []


def test_main_rejects_week_without_season(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["scrape_quarter_scores.py", "--week", "3"])
    with pytest.raises(SystemExit):
        main()
    assert "--week requires --season" in capsys.readouterr().err


def test_main_rejects_week_with_seasons_range(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["scrape_quarter_scores.py", "--seasons", "2020", "2021", "--week", "3"])
    with pytest.raises(SystemExit):
        main()
    assert "not compatible with --seasons" in capsys.readouterr().err
