import logging

import pandas as pd
import pytest

from scripts import send_weekly_standings_push as script
from services import push_events, push_service, standings_push_service as sp

SEASON = 2026


def _games(weeks_done):
    rows = []
    for w in range(1, 4):
        done = w <= weeks_done
        rows.append([SEASON, w, "REG", "A", "B", 10 if done else None,
                     3 if done else None, 7 if done else None])
    return pd.DataFrame(rows, columns=["season", "week", "game_type", "home_team",
                                       "away_team", "home_score", "away_score", "result"])


@pytest.fixture
def env(monkeypatch):
    state = {"complete": True, "weeks": 2, "exists": False, "configured": True,
             "sent": [], "recorded": [], "build_args": [], "send_raises": False}
    monkeypatch.setattr(script, "_require_db", lambda: None)
    monkeypatch.setattr(script, "_load", lambda season=None: (
        season or SEASON, _games(state["weeks"]), pd.DataFrame(), pd.DataFrame(),
        state["complete"]))
    monkeypatch.setattr(sp, "pool_ranking", lambda *a, **k: [
        {"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 3}])

    def fake_build(week, current, previous):
        state["build_args"].append((week, previous))
        return {1: (f"Week {week} standings", "body")}
    monkeypatch.setattr(sp, "build_messages", fake_build)
    monkeypatch.setattr(push_service, "is_configured", lambda: state["configured"])
    monkeypatch.setattr(push_events, "push_event_exists", lambda eid: state["exists"])

    def fake_send(build, *, pref=None, url=None):
        if state["send_raises"]:
            raise RuntimeError("boom")
        state["sent"].append((build(1), pref, url))
        return {"counts": {"total": 1, "sent": 1}, "messages": {1: {"title": "t"}}}
    monkeypatch.setattr(push_service, "send_to_subscribers", fake_send)
    monkeypatch.setattr(push_events, "record_push_event",
                        lambda *a, **k: state["recorded"].append(a) or True)
    return state


def test_dry_run_prints_and_neither_sends_nor_records(env, capsys):
    assert script.main(["--dry-run"]) == 0
    assert "1: Week 2 standings - body" in capsys.readouterr().out
    assert env["sent"] == [] and env["recorded"] == []


def test_sends_once_and_records_event(env):
    assert script.main([]) == 0
    assert env["sent"] == [(("Week 2 standings", "body"), "standings", "/wins-pool/2026")]
    eid, kind, counts, msgs, extra = env["recorded"][0]
    assert (eid, kind) == ("2026_w02_standings", "standings")
    assert msgs == {1: {"title": "t"}} and extra == {"season": 2026, "week": 2}


def test_already_sent_skips_and_force_resends(env):
    env["exists"] = True
    assert script.main([]) == 0
    assert env["sent"] == []
    assert script.main(["--force"]) == 0
    assert len(env["sent"]) == 1


def test_draft_incomplete_skips(env):
    env["complete"] = False
    assert script.main([]) == 0
    assert env["sent"] == []


def test_no_complete_week_skips(env):
    env["weeks"] = 0
    assert script.main([]) == 0
    assert env["sent"] == []


def test_not_configured_skips_with_warning(env, caplog):
    env["configured"] = False
    with caplog.at_level(logging.WARNING):
        assert script.main([]) == 0
    assert env["sent"] == [] and "not configured" in caplog.text


def test_week_one_has_no_previous(env):
    env["weeks"] = 1
    script.main([])
    assert env["build_args"] == [(1, None)]


def test_force_week_overrides_latest(env):
    script.main(["--force-week", "3"])
    assert env["build_args"][0][0] == 3
    assert env["recorded"][0][0] == "2026_w03_standings"


def test_send_failure_does_not_record_and_exits_zero(env, caplog):
    env["send_raises"] = True
    with caplog.at_level(logging.ERROR):
        assert script.main([]) == 0
    assert env["recorded"] == [] and "failed" in caplog.text


def _record_returning(env, monkeypatch, results):
    calls = []
    seq = iter(results)

    def fake_record(*a, **k):
        calls.append(a)
        return next(seq)
    monkeypatch.setattr(push_events, "record_push_event", fake_record)
    return calls


def test_record_failing_twice_logs_error_and_exits_zero(env, monkeypatch, caplog):
    calls = _record_returning(env, monkeypatch, [False, False])
    with caplog.at_level(logging.INFO):
        assert script.main([]) == 0
    assert len(calls) == 2
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors and "2026_w02_standings" in errors[0].getMessage()
    assert "resend" in errors[0].getMessage().lower()
    assert not any(r.levelno == logging.INFO and r.getMessage().startswith("Sent ")
                   for r in caplog.records)


def test_record_failing_once_then_succeeding_retries_without_error(env, monkeypatch, caplog):
    calls = _record_returning(env, monkeypatch, [False, True])
    with caplog.at_level(logging.INFO):
        assert script.main([]) == 0
    assert len(calls) == 2
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(r.levelno == logging.INFO and r.getMessage().startswith("Sent ")
               for r in caplog.records)


def test_record_success_records_once(env, monkeypatch):
    calls = _record_returning(env, monkeypatch, [True])
    assert script.main([]) == 0
    assert len(calls) == 1
