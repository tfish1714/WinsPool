"""tests/test_live_projection.py -- results-aware (live) season projection.

Covers: draft_is_complete, completed-result building + tie handling in the
engine, get_team_win_projections pass-through, the cache_builder in-season
flow (lock preseason, write season_projections + history), the db_service
writers, the data_service readers, and the local-mirror collection list.

Nothing here touches Firestore or a developer's .local_db: the DB client is a
MagicMock and readers use tmp_path.
"""
import pathlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Reuse the 2-team engine fixture + helpers from the simulate_season tests
# (tests/ has no __init__.py, so its files import by basename).
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_simulate_season import (  # noqa: E402,F401
    mock_engine, _make_schedule, _elo_only_predict,
)

# scripts/cache_builder.py does a module-level Firebase init that sys.exit(1)s
# without credentials; fake an initialized app just for the import (same
# pattern as tests/test_cache_builder.py).
import firebase_admin as _firebase_admin  # noqa: E402
with patch.object(_firebase_admin, "_apps", {"__test__": object()}),      patch("firebase_admin.firestore.client"):
    import scripts.cache_builder  # noqa: E402,F401

PROJ = {
    "KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
           "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
}


# --------------------------------------------------------------------------
# draft_is_complete
# --------------------------------------------------------------------------

def _rules(season=2026, n_rows=10):
    """n_rows rules rows, each defining pickOne/pickTwo/pickThree (3n picks)."""
    return pd.DataFrame([
        {"season": season, "draftOrder": i + 1,
         "pickOne": i + 1, "pickTwo": 20 - i, "pickThree": 21 + i}
        for i in range(n_rows)
    ])


def _results(n, season=2026):
    return pd.DataFrame([{"season": season, "draftPick": i + 1, "team": "KC"}
                         for i in range(n)])


class TestDraftIsComplete:
    def test_partial_picks_not_complete(self):
        from services.draft_state import draft_is_complete
        assert draft_is_complete(2026, _results(29), pd.DataFrame(), _rules()) is False

    def test_exactly_complete(self):
        from services.draft_state import draft_is_complete
        assert draft_is_complete(2026, _results(30), pd.DataFrame(), _rules()) is True

    def test_more_rules_than_picks_not_complete(self):
        from services.draft_state import draft_is_complete
        # 12 rows x 3 = 36 picks defined; 30 made is not enough.
        assert draft_is_complete(2026, _results(30), pd.DataFrame(), _rules(n_rows=12)) is False
        assert draft_is_complete(2026, _results(36), pd.DataFrame(), _rules(n_rows=12)) is True

    def test_no_rules_falls_back_to_draft_order_times_three(self):
        from services.draft_state import draft_is_complete
        order = pd.DataFrame([{"season": 2026, "draftOrder": i, "playerId": i}
                              for i in range(1, 9)])  # 8 players -> 24 picks
        assert draft_is_complete(2026, _results(24), order, pd.DataFrame()) is True
        assert draft_is_complete(2026, _results(23), order, pd.DataFrame()) is False

    def test_no_rules_no_order_falls_back_to_30(self):
        from services.draft_state import draft_is_complete
        assert draft_is_complete(2026, _results(30), pd.DataFrame(), pd.DataFrame()) is True
        assert draft_is_complete(2026, _results(29), pd.DataFrame(), pd.DataFrame()) is False

    def test_empty_results_never_complete(self):
        from services.draft_state import draft_is_complete
        assert draft_is_complete(2026, pd.DataFrame(), pd.DataFrame(), pd.DataFrame()) is False
        assert draft_is_complete(2026, None, None, None) is False

    def test_other_seasons_do_not_count(self):
        from services.draft_state import draft_is_complete
        both = pd.concat([_results(30, 2025), _results(5, 2026)], ignore_index=True)
        rules = pd.concat([_rules(2025), _rules(2026)], ignore_index=True)
        assert draft_is_complete(2026, both, pd.DataFrame(), rules) is False
        assert draft_is_complete(2025, both, pd.DataFrame(), rules) is True

    def test_rules_of_another_season_are_ignored(self):
        from services.draft_state import draft_is_complete
        # Only 2025 has rules (36 picks); 2026 has none -> fallback 30.
        assert draft_is_complete(2026, _results(30), pd.DataFrame(),
                                 _rules(2025, n_rows=12)) is True

    def test_null_pick_columns_are_not_counted(self):
        from services.draft_state import draft_is_complete
        rules = pd.DataFrame([{"season": 2026, "draftOrder": 1, "pickOne": 1,
                               "pickTwo": 2, "pickThree": None}])
        assert draft_is_complete(2026, _results(2), pd.DataFrame(), rules) is True

    def test_duplicate_pick_rows_do_not_inflate_the_count(self):
        from services.draft_state import draft_is_complete
        dup = pd.concat([_results(2), _results(2)], ignore_index=True)
        assert draft_is_complete(2026, dup, pd.DataFrame(), _rules()) is False

    def test_independent_of_draft_active_flag(self):
        """The helper takes no config: nothing about draft_active can matter."""
        import inspect
        from services import draft_state
        sig = inspect.signature(draft_state.draft_is_complete)
        assert list(sig.parameters) == [
            "season", "draft_results", "draft_order", "draft_order_rules"]
        with patch("services.db_service.get_config_settings",
                   side_effect=AssertionError("must not read config")):
            assert draft_state.draft_is_complete(
                2026, _results(30), pd.DataFrame(), _rules()) is True


# --------------------------------------------------------------------------
# Engine: completed results, ties, pass-through
# --------------------------------------------------------------------------

class TestBuildCompletedResults:
    def _games(self):
        return pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "WAS",
             "away_team": "KC", "result": 3.0},
            {"season": 2026, "week": 4, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": None},
            {"season": 2026, "week": 4, "game_type": "REG", "home_team": "DAL",
             "away_team": "NYG", "result": -1000},
            {"season": 2026, "week": 5, "game_type": "POST", "home_team": "DAL",
             "away_team": "NYG", "result": -7.0},
            {"season": 2025, "week": 3, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": 9.0},
        ])

    def test_only_completed_reg_games_of_the_season(self):
        from services.nn_projection_engine import build_completed_results
        assert build_completed_results(self._games(), 2026) == {"W03_WAS_KC": 3.0}

    def test_normalizes_team_codes_like_simulate_season(self):
        from services.nn_projection_engine import build_completed_results
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "home_team": "OAK",
             "away_team": "WSH", "result": -4.0},
        ])
        from services.utils import normalize_team_abbr
        key = f"W01_{normalize_team_abbr('OAK')}_{normalize_team_abbr('WSH')}"
        assert build_completed_results(games, 2026) == {key: -4.0}

    def test_home_margin_and_tie_zero_are_kept(self):
        from services.nn_projection_engine import build_completed_results
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "game_type": "REG", "home_team": "KC",
             "away_team": "TEN", "result": 0.0},
        ])
        assert build_completed_results(games, 2026) == {"W01_KC_TEN": 0.0}

    def test_missing_columns_or_empty_return_empty(self):
        from services.nn_projection_engine import build_completed_results
        assert build_completed_results(pd.DataFrame(), 2026) == {}
        assert build_completed_results(
            pd.DataFrame([{"season": 2026, "week": 1}]), 2026) == {}
        assert build_completed_results(None, 2026) == {}

    def test_cache_builder_wrapper_delegates(self):
        """cache_builder._build_completed_results is kept (existing tests and
        walk_forward_validate import it) and returns the same thing."""
        from scripts.cache_builder import _build_completed_results
        from services.nn_projection_engine import build_completed_results
        g = self._games()
        assert _build_completed_results(g, 2026) == build_completed_results(g, 2026)


class TestCreditCompletedGame:
    def test_home_win(self):
        from services.nn_projection_engine import _credit_completed_game
        wm = np.zeros((3, 2), dtype=np.float32)
        _credit_completed_game(wm, 0, 1, 7.0)
        assert wm[:, 0].tolist() == [1, 1, 1] and wm[:, 1].tolist() == [0, 0, 0]

    def test_away_win(self):
        from services.nn_projection_engine import _credit_completed_game
        wm = np.zeros((3, 2), dtype=np.float32)
        _credit_completed_game(wm, 0, 1, -3.0)
        assert wm[:, 0].tolist() == [0, 0, 0] and wm[:, 1].tolist() == [1, 1, 1]

    def test_tie_credits_neither_team(self):
        from services.nn_projection_engine import _credit_completed_game
        wm = np.zeros((3, 2), dtype=np.float32)
        _credit_completed_game(wm, 0, 1, 0.0)
        assert not wm.any()


class TestSimulateSeasonResultsAware:
    def test_completed_tie_gives_no_win_to_either_team(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        result = mock_engine.simulate_season(
            _make_schedule(1), n_sims=50, completed_results={"W01_STRONG_WEAK": 0.0})
        assert result["team_stats"]["STRONG"]["mean_wins"] == 0.0
        assert result["team_stats"]["WEAK"]["mean_wins"] == 0.0

    def test_fully_completed_team_equals_actual_wins_with_zero_std(self, mock_engine):
        mock_engine._batch_predict = _elo_only_predict
        sched = _make_schedule(2)
        done = {"W01_STRONG_WEAK": -3.0, "W02_WEAK_STRONG": -10.0}  # WEAK wk1, STRONG wk2
        proj = mock_engine.get_team_win_projections(sched, n_sims=100, completed_results=done)
        for team in ("STRONG", "WEAK"):
            assert proj[team]["projected_wins"] == 1.0
            assert proj[team]["mean_wins"] == 1.0
            assert proj[team]["std_dev"] == 0.0

    def test_completed_week_changes_projection_vs_no_results(self, mock_engine):
        """Regression for the original bug: the daily job never passed results,
        so the projection was identical every day."""
        mock_engine._batch_predict = _elo_only_predict
        sched = _make_schedule(4)
        base = mock_engine.get_team_win_projections(sched, n_sims=400)
        # Upset: WEAK (home in wk2) beats STRONG by 20, wk1 STRONG wins.
        done = {"W01_STRONG_WEAK": 7.0, "W02_WEAK_STRONG": 20.0}
        live = mock_engine.get_team_win_projections(sched, n_sims=400, completed_results=done)
        assert live["WEAK"]["mean_wins"] != base["WEAK"]["mean_wins"]
        # Wins to date (1 each) are baked in: WEAK >= 1 win, STRONG >= 1 win.
        assert live["WEAK"]["floor"] >= 1.0 and live["STRONG"]["floor"] >= 1.0
        # Total wins across both teams still equals the games played.
        assert live["WEAK"]["mean_wins"] + live["STRONG"]["mean_wins"] == pytest.approx(4, abs=0.6)


class TestGetTeamWinProjectionsPassThrough:
    def test_passes_completed_results_to_simulate_season(self, mock_engine):
        captured = {}

        def fake_sim(schedule_df, n_sims=5000, completed_results=None):
            captured["cr"] = completed_results
            captured["n"] = n_sims
            return {"team_stats": {}}

        mock_engine.simulate_season = fake_sim
        sched = pd.DataFrame([{"home_team": "KC", "away_team": "TEN", "week": 1}])
        mock_engine.get_team_win_projections(sched, n_sims=77, completed_results={"k": 1.0})
        assert captured == {"cr": {"k": 1.0}, "n": 77}

    def test_default_is_backward_compatible(self, mock_engine):
        captured = {}

        def fake_sim(schedule_df, n_sims=5000, completed_results=None):
            captured["cr"] = completed_results
            return {"team_stats": {}}

        mock_engine.simulate_season = fake_sim
        sched = pd.DataFrame([{"home_team": "KC", "away_team": "TEN", "week": 1}])
        mock_engine.get_team_win_projections(sched)
        assert captured["cr"] is None


# --------------------------------------------------------------------------
# cache_builder.build_year in-season flow
# --------------------------------------------------------------------------

MV = "nn_v15+xgb_v9+lr_v7"


def _complete_draft(year=2026):
    rules = pd.DataFrame([{"season": year, "draftOrder": 1,
                           "pickOne": 1, "pickTwo": 2, "pickThree": 3}])
    results = _results(3, year)
    return results, rules


def _run_build_year(games, year, current_year, *, draft_results=None, rules=None,
                    force=False, engine=None):
    from scripts import cache_builder as cb
    engine = engine or MagicMock()
    if not isinstance(engine.get_team_win_projections.return_value, dict) or \
            not engine.get_team_win_projections.return_value:
        engine.get_team_win_projections.return_value = dict(PROJ)
    mocks = {}
    with patch.object(cb, "NNProjectionEngine", return_value=engine), \
         patch.object(cb.live_scores, "sync_live_scores_to_df", side_effect=lambda g: g), \
         patch.object(cb, "set_preseason_predictions", return_value=1) as m_pre, \
         patch.object(cb, "lock_preseason_predictions", return_value=1) as m_lock, \
         patch.object(cb, "set_season_projections", return_value=1) as m_cur, \
         patch.object(cb, "set_season_projection_history", return_value=1) as m_hist:
        cb.build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(),
            draft_results=draft_results if draft_results is not None else pd.DataFrame(),
            draft_order_rules=rules if rules is not None else pd.DataFrame(),
            year=year, current_year=current_year, all_games=games, force=force,
            pred_lookup={}, model_version=MV,
        )
    mocks.update(pre=m_pre, lock=m_lock, cur=m_cur, hist=m_hist, engine=engine)
    return mocks


def _games_with_results():
    return pd.DataFrame([
        {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
         "result": 7.0, "game_type": "REG"},
        {"season": 2026, "week": 2, "home_team": "TEN", "away_team": "KC",
         "result": 3.0, "game_type": "REG"},
        {"season": 2026, "week": 3, "home_team": "KC", "away_team": "TEN",
         "result": None, "game_type": "REG"},
    ])


class TestBuildYearLiveProjection:
    def test_draft_complete_locks_preseason_and_writes_current_and_history(self):
        results, rules = _complete_draft()
        m = _run_build_year(_games_with_results(), 2026, 2026,
                            draft_results=results, rules=rules)
        m["pre"].assert_not_called()
        m["lock"].assert_called_once_with(2026)
        m["cur"].assert_called_once()
        m["hist"].assert_called_once()
        cur = m["cur"].call_args
        assert cur.args[0] == 2026 and cur.args[1] == PROJ
        assert cur.kwargs["as_of_week"] == 2
        assert cur.kwargs["model_version"] == MV
        assert cur.kwargs["locked"] is False
        hist = m["hist"].call_args
        assert hist.args[0] == 2026 and hist.args[1] == 2 and hist.args[2] == PROJ
        assert hist.kwargs["model_version"] == MV

    def test_single_simulation_with_completed_results(self):
        results, rules = _complete_draft()
        m = _run_build_year(_games_with_results(), 2026, 2026,
                            draft_results=results, rules=rules)
        eng = m["engine"]
        assert eng.get_team_win_projections.call_count == 1
        cr = eng.get_team_win_projections.call_args.kwargs["completed_results"]
        assert cr == {"W01_KC_TEN": 7.0, "W02_TEN_KC": 3.0}

    def test_no_results_yet_writes_current_with_week_zero_and_no_history(self):
        results, rules = _complete_draft()
        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        m = _run_build_year(games, 2026, 2026, draft_results=results, rules=rules)
        m["pre"].assert_not_called()
        m["lock"].assert_called_once_with(2026)
        assert m["cur"].call_args.kwargs["as_of_week"] == 0
        m["hist"].assert_not_called()

    def test_draft_not_complete_keeps_preseason_refresh_and_skips_new_stores(self):
        m = _run_build_year(_games_with_results(), 2026, 2026,
                            draft_results=_results(2), rules=_rules_3())
        m["pre"].assert_called_once()
        assert m["pre"].call_args.kwargs["locked"] is False
        m["lock"].assert_not_called()
        m["cur"].assert_not_called()
        m["hist"].assert_not_called()

    def test_draft_not_complete_does_not_apply_results_to_preseason(self):
        """preseason numbers stay a pure pre-results simulation."""
        m = _run_build_year(_games_with_results(), 2026, 2026,
                            draft_results=_results(2), rules=_rules_3())
        kw = m["engine"].get_team_win_projections.call_args.kwargs
        assert not kw.get("completed_results")

    def test_completed_season_writes_locked_current_and_locks_preseason(self):
        results, rules = _complete_draft(2024)
        games = pd.DataFrame([
            {"season": 2024, "week": 17, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
            {"season": 2024, "week": 18, "home_team": "TEN", "away_team": "KC",
             "result": -3.0, "game_type": "REG"},
        ])
        m = _run_build_year(games, 2024, 2026, draft_results=results, rules=rules,
                            force=True)
        m["pre"].assert_not_called()
        m["lock"].assert_called_once_with(2024)
        assert m["cur"].call_args.kwargs["locked"] is True
        assert m["cur"].call_args.kwargs["as_of_week"] == 18
        assert m["hist"].call_args.args[1] == 18

    def test_past_season_without_force_is_untouched(self):
        results, rules = _complete_draft(2024)
        games = pd.DataFrame([
            {"season": 2024, "week": 18, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
        ])
        m = _run_build_year(games, 2024, 2026, draft_results=results, rules=rules)
        for k in ("pre", "lock", "cur", "hist"):
            m[k].assert_not_called()

    def test_failed_write_of_one_collection_does_not_stop_the_others(self, capsys):
        from scripts import cache_builder as cb
        results, rules = _complete_draft()
        engine = MagicMock()
        engine.get_team_win_projections.return_value = dict(PROJ)
        with patch.object(cb, "NNProjectionEngine", return_value=engine), \
             patch.object(cb.live_scores, "sync_live_scores_to_df", side_effect=lambda g: g), \
             patch.object(cb, "set_preseason_predictions"), \
             patch.object(cb, "lock_preseason_predictions", side_effect=RuntimeError("boom")), \
             patch.object(cb, "set_season_projections", return_value=1) as m_cur, \
             patch.object(cb, "set_season_projection_history", return_value=1) as m_hist:
            games = _games_with_results()
            cb.build_year(pd.DataFrame(), games, pd.DataFrame(), pd.DataFrame(),
                          results, rules, 2026, 2026, all_games=games,
                          force=False, pred_lookup={}, model_version=MV)
        m_cur.assert_called_once()
        m_hist.assert_called_once()
        assert "preseason_predictions" in capsys.readouterr().out

    def test_rerun_is_idempotent_same_calls(self):
        results, rules = _complete_draft()
        a = _run_build_year(_games_with_results(), 2026, 2026, draft_results=results, rules=rules)
        b = _run_build_year(_games_with_results(), 2026, 2026, draft_results=results, rules=rules)
        assert a["cur"].call_args == b["cur"].call_args
        assert a["hist"].call_args == b["hist"].call_args


def _rules_3():
    return pd.DataFrame([{"season": 2026, "draftOrder": 1,
                          "pickOne": 1, "pickTwo": 2, "pickThree": 3}])


# --------------------------------------------------------------------------
# db_service writers (mocked Firestore client)
# --------------------------------------------------------------------------

def _fake_db():
    db = MagicMock()
    refs = {}

    def document_for(name):
        col = MagicMock()

        def doc(doc_id):
            key = (name, doc_id)
            refs.setdefault(key, MagicMock(name=f"ref:{name}/{doc_id}"))
            return refs[key]

        col.document.side_effect = doc
        return col

    cols = {}

    def collection(name):
        cols.setdefault(name, document_for(name))
        return cols[name]

    db.collection.side_effect = collection
    batch = MagicMock()
    db.batch.return_value = batch
    return db, batch, refs, cols


class TestSeasonProjectionWriters:
    def test_set_season_projections_docs_fields_and_overwrite(self):
        from services import db_service
        db, batch, refs, _ = _fake_db()
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            n = db_service.set_season_projections(
                2026, {"KC": dict(PROJ["KC"]), "TEN": dict(PROJ["KC"])},
                model_version=MV, as_of_week=3, locked=False)
        assert n == 2
        ids = sorted(k[1] for k in refs if k[0] == "season_projections")
        assert ids == ["2026_KC", "2026_TEN"]
        # plain set (overwrite), never merge
        for call in batch.set.call_args_list:
            assert "merge" not in call.kwargs
            payload = call.args[1]
            assert payload["season"] == 2026 and payload["as_of_week"] == 3
            assert payload["locked"] is False and payload["model_version"] == MV
            assert payload["projected_wins"] == 11.0 and "generated_at" in payload
            assert payload["team"] in ("KC", "TEN")
        batch.commit.assert_called()

    def test_set_season_projections_locked_flag_written(self):
        from services import db_service
        db, batch, _, _ = _fake_db()
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            db_service.set_season_projections(2026, dict(PROJ), MV, 18, locked=True)
        assert batch.set.call_args.args[1]["locked"] is True

    def test_set_season_projection_history_keyed_by_week(self):
        from services import db_service
        db, batch, refs, _ = _fake_db()
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            n = db_service.set_season_projection_history(2026, 3, dict(PROJ), MV)
        assert n == 1
        assert [k for k in refs if k[0] == "season_projection_history"] == [
            ("season_projection_history", "2026_w03_KC")]
        payload = batch.set.call_args.args[1]
        assert payload["week"] == 3 and payload["season"] == 2026 and payload["team"] == "KC"
        assert payload["model_version"] == MV and "generated_at" in payload

    def test_no_db_writes_nothing(self):
        from services import db_service
        with patch.object(db_service, "get_db", return_value=None):
            assert db_service.set_season_projections(2026, dict(PROJ), MV, 1) == 0
            assert db_service.set_season_projection_history(2026, 1, dict(PROJ), MV) == 0
            assert db_service.lock_preseason_predictions(2026) == 0

    def test_writers_never_touch_frozen_or_preseason_collections(self):
        from services import db_service
        db, batch, refs, cols = _fake_db()
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            db_service.set_season_projections(2026, dict(PROJ), MV, 1)
            db_service.set_season_projection_history(2026, 1, dict(PROJ), MV)
        assert set(cols) == {"season_projections", "season_projection_history"}

    def test_lock_preseason_only_flips_locked_on_unlocked_docs(self):
        from services import db_service

        def mkdoc(team, locked):
            d = MagicMock()
            d.to_dict.return_value = {"team": team, "locked": locked, "projected_wins": 9.0}
            d.reference = MagicMock(name=f"ref-{team}")
            return d

        docs = [mkdoc("KC", False), mkdoc("TEN", True), mkdoc("BUF", False)]
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = docs
        batch = MagicMock()
        db.batch.return_value = batch
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            n = db_service.lock_preseason_predictions(2026)
        assert n == 2
        db.collection.assert_any_call("preseason_predictions")
        assert batch.update.call_count == 2
        for call in batch.update.call_args_list:
            assert call.args[1] == {"locked": True}
        batch.set.assert_not_called()

    def test_lock_preseason_noop_when_all_locked(self):
        from services import db_service
        d = MagicMock()
        d.to_dict.return_value = {"team": "KC", "locked": True}
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = [d]
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update") as sig:
            assert db_service.lock_preseason_predictions(2026) == 0
        sig.assert_not_called()
        db.batch.return_value.update.assert_not_called()


# --------------------------------------------------------------------------
# data_service readers
# --------------------------------------------------------------------------

@pytest.fixture
def fresh_pred_cache():
    """Isolate the predictions_active/historical domain buckets."""
    import services.cache_service as cs
    with patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
        cs.clear_domain(cs.DOMAIN_PREDICTIONS_ACTIVE)
        cs.clear_domain(cs.DOMAIN_PREDICTIONS_HISTORICAL)
        yield
        cs.clear_domain(cs.DOMAIN_PREDICTIONS_ACTIVE)
        cs.clear_domain(cs.DOMAIN_PREDICTIONS_HISTORICAL)


class TestReaders:
    def test_current_reads_local_pkl(self, tmp_path, monkeypatch, fresh_pred_cache):
        from services import data_service
        monkeypatch.setenv("USE_LOCAL_DATA", "True")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".local_db").mkdir()
        pd.DataFrame([
            {"season": 2026, "team": "KC", "projected_wins": 11.0, "mean_wins": 10.8,
             "std_dev": 1.9, "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0,
             "as_of_week": 3, "locked": False, "model_version": MV},
            {"season": 2025, "team": "KC", "projected_wins": 5.0, "mean_wins": 5.0,
             "std_dev": 1.0, "floor": 3.0, "p25": 4.0, "p75": 6.0, "ceiling": 7.0,
             "as_of_week": 18, "locked": True, "model_version": MV},
        ]).to_pickle(tmp_path / ".local_db" / "season_projections.pkl")
        out = data_service.get_season_projection_current(2026)
        assert set(out) == {"KC"}
        assert out["KC"]["projected_wins"] == 11.0
        assert out["KC"]["floor"] == 7.0 and out["KC"]["ceiling"] == 14.0
        assert out["KC"]["as_of_week"] == 3 and out["KC"]["locked"] is False

    def test_current_empty_returns_empty_dict(self, tmp_path, monkeypatch, fresh_pred_cache):
        from services import data_service
        monkeypatch.setenv("USE_LOCAL_DATA", "True")
        monkeypatch.chdir(tmp_path)
        assert data_service.get_season_projection_current(2026) == {}
        assert data_service.get_season_projection_history(2026) == {}

    def test_history_groups_by_week_then_team(self, tmp_path, monkeypatch, fresh_pred_cache):
        from services import data_service
        monkeypatch.setenv("USE_LOCAL_DATA", "True")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".local_db").mkdir()
        rows = []
        for wk, kc in ((1, 10.0), (2, 10.5)):
            for team in ("KC", "TEN"):
                rows.append({"season": 2026, "week": wk, "team": team,
                             "projected_wins": kc, "mean_wins": kc, "std_dev": 1.0,
                             "floor": 6.0, "p25": 8.0, "p75": 12.0, "ceiling": 13.0})
        rows.append({"season": 2025, "week": 1, "team": "KC", "projected_wins": 3.0,
                     "mean_wins": 3.0, "std_dev": 1.0, "floor": 1.0, "p25": 2.0,
                     "p75": 4.0, "ceiling": 5.0})
        pd.DataFrame(rows).to_pickle(tmp_path / ".local_db" / "season_projection_history.pkl")
        out = data_service.get_season_projection_history(2026)
        assert sorted(out) == [1, 2]
        assert set(out[1]) == {"KC", "TEN"}
        assert out[2]["KC"]["projected_wins"] == 10.5

    def test_remote_path_uses_the_collections(self, monkeypatch, fresh_pred_cache):
        from services import data_service
        monkeypatch.setenv("USE_LOCAL_DATA", "False")
        seen = []

        def fake_get(name, filters=None):
            seen.append((name, filters))
            return pd.DataFrame()

        with patch.object(data_service, "get_collection_df", side_effect=fake_get):
            assert data_service.get_season_projection_current(2026) == {}
            assert data_service.get_season_projection_history(2026) == {}
        assert ("season_projections", [("season", "==", 2026)]) in seen
        assert ("season_projection_history", [("season", "==", 2026)]) in seen

    def test_readers_are_cached_in_the_predictions_domain(self, fresh_pred_cache):
        from services import data_service
        df = pd.DataFrame([{"season": 2026, "team": "KC", "projected_wins": 9.0,
                            "mean_wins": 9.0, "std_dev": 1.0, "floor": 6.0,
                            "p25": 8.0, "p75": 10.0, "ceiling": 12.0,
                            "as_of_week": 1, "locked": False}])
        with patch.object(data_service, "get_collection_df", return_value=df) as g:
            data_service.get_season_projection_current(2026)
            data_service.get_season_projection_current(2026)
        assert g.call_count == 1

    def test_current_does_not_alter_preseason_readers(self, fresh_pred_cache):
        """Reading the new store must not populate or reuse preseason keys."""
        from services import data_service
        with patch.object(data_service, "get_collection_df", return_value=pd.DataFrame()) as g:
            data_service.get_season_projection_current(2026)
        assert [c.args[0] for c in g.call_args_list] == ["season_projections"]


# --------------------------------------------------------------------------
# refresh_local_pkls mirror
# --------------------------------------------------------------------------

class TestLocalMirror:
    def test_collections_listed_beside_preseason(self):
        import importlib
        rl = importlib.import_module("scripts.refresh_local_pkls")
        assert ("season_projections", "season") in rl.COLLECTIONS
        assert ("season_projection_history", "season") in rl.COLLECTIONS
        assert ("preseason_predictions", "season") in rl.COLLECTIONS
        assert ("draft_snapshot_predictions", "season") in rl.COLLECTIONS

    def test_dump_collection_writes_full_and_per_year_pkls(self, tmp_path, monkeypatch):
        import importlib
        rl = importlib.import_module("scripts.refresh_local_pkls")
        monkeypatch.setattr(rl, "LOCAL_DB", tmp_path)
        df = pd.DataFrame([
            {"season": 2025, "week": 1, "team": "KC", "projected_wins": 3.0},
            {"season": 2026, "week": 2, "team": "KC", "projected_wins": 9.0},
        ])
        monkeypatch.setattr(rl, "get_collection_df", lambda name, filters=None: df)
        rl.dump_collection("season_projection_history", "season")
        assert (tmp_path / "season_projection_history.pkl").exists()
        y26 = pd.read_pickle(tmp_path / "season_projection_history_2026.pkl")
        assert list(y26["week"]) == [2]
        assert (tmp_path / "season_projection_history_2025.pkl").exists()
