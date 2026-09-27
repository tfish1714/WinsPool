"""tests/test_live_projection_fixwave.py -- unlock/delete helpers, the
unlock_preseason recovery script, and Elo tie scoring."""
import pathlib
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_simulate_season import mock_engine  # noqa: E402,F401

# scripts.* import does a module-level Firebase init; fake it for the import.
import firebase_admin as _firebase_admin  # noqa: E402
with patch.object(_firebase_admin, "_apps", {"__test__": object()}), \
     patch("firebase_admin.firestore.client"):
    import scripts.cache_builder  # noqa: E402,F401


def _doc(team, locked=None, **extra):
    d = MagicMock()
    payload = {"team": team, "projected_wins": 9.0, **extra}
    if locked is not None:
        payload["locked"] = locked
    d.to_dict.return_value = payload
    d.reference = MagicMock(name=f"ref-{team}")
    return d


def _tables(complete):
    rules = pd.DataFrame([{"season": 2026, "draftOrder": 1,
                           "pickOne": 1, "pickTwo": 2, "pickThree": 3}])
    n = 3 if complete else 1
    results = pd.DataFrame([{"season": 2026, "draftPick": i + 1, "team": "KC"}
                            for i in range(n)])
    return results, rules


class TestUnlockAndDeleteHelpers:
    def test_unlock_only_flips_locked_true_docs(self):
        from services import db_service
        docs = [_doc("KC", True), _doc("TEN", False), _doc("BUF", None), _doc("DAL", True)]
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = docs
        batch = MagicMock()
        db.batch.return_value = batch
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            n = db_service.unlock_preseason_predictions(2026)
        assert n == 2
        db.collection.assert_any_call("preseason_predictions")
        assert batch.update.call_count == 2
        for call in batch.update.call_args_list:
            assert call.args[1] == {"locked": False}
        batch.set.assert_not_called()

    def test_unlock_idempotent_noop(self):
        from services import db_service
        db = MagicMock()
        db.collection.return_value.where.return_value.stream.return_value = [_doc("KC", False)]
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update") as sig:
            assert db_service.unlock_preseason_predictions(2026) == 0
        sig.assert_not_called()
        db.batch.return_value.update.assert_not_called()

    def test_delete_season_projections_deletes_both_collections_only(self):
        from services import db_service
        streams = {
            "season_projections": [_doc("KC"), _doc("TEN")],
            "season_projection_history": [_doc("KC", week=1), _doc("KC", week=2), _doc("TEN", week=1)],
        }
        db = MagicMock()
        touched = []

        def collection(name):
            touched.append(name)
            col = MagicMock()
            col.where.return_value.stream.return_value = streams.get(name, [])
            return col

        db.collection.side_effect = collection
        batch = MagicMock()
        db.batch.return_value = batch
        with patch.object(db_service, "get_db", return_value=db), \
             patch.object(db_service, "signal_data_update"), \
             patch("services.data_service._get_active_bucket", return_value={"season": 2026}):
            n = db_service.delete_season_projections(2026)
        assert n == 5
        assert batch.delete.call_count == 5
        assert set(touched) == {"season_projections", "season_projection_history"}

    def test_no_db_returns_zero(self):
        from services import db_service
        with patch.object(db_service, "get_db", return_value=None):
            assert db_service.unlock_preseason_predictions(2026) == 0
            assert db_service.delete_season_projections(2026) == 0


class TestUnlockPreseasonScript:
    def test_refuses_when_draft_complete(self, capsys):
        from scripts import unlock_preseason as up
        results, rules = _tables(True)
        with patch.object(up, "unlock_preseason_predictions") as u, \
             patch.object(up, "delete_season_projections") as d:
            code = up.run(2026, write=True, allow_complete=False,
                          draft_results=results, draft_order=pd.DataFrame(), rules=rules)
        assert code != 0
        u.assert_not_called()
        d.assert_not_called()
        assert "draft is complete" in capsys.readouterr().out.lower()

    def test_dry_run_default_changes_nothing(self, capsys):
        from scripts import unlock_preseason as up
        results, rules = _tables(False)
        with patch.object(up, "unlock_preseason_predictions") as u, \
             patch.object(up, "delete_season_projections") as d:
            code = up.run(2026, write=False, allow_complete=False,
                          draft_results=results, draft_order=pd.DataFrame(), rules=rules)
        assert code == 0
        u.assert_not_called()
        d.assert_not_called()
        assert "dry run" in capsys.readouterr().out.lower()

    def test_dry_run_still_refuses_complete_draft(self):
        from scripts import unlock_preseason as up
        results, rules = _tables(True)
        assert up.run(2026, write=False, allow_complete=False,
                      draft_results=results, draft_order=pd.DataFrame(), rules=rules) != 0

    def test_write_runs_both_helpers(self):
        from scripts import unlock_preseason as up
        results, rules = _tables(False)
        with patch.object(up, "unlock_preseason_predictions", return_value=32) as u, \
             patch.object(up, "delete_season_projections", return_value=40) as d:
            code = up.run(2026, write=True, allow_complete=False,
                          draft_results=results, draft_order=pd.DataFrame(), rules=rules)
        assert code == 0
        u.assert_called_once_with(2026)
        d.assert_called_once_with(2026)

    def test_override_flag_allows_complete_draft(self):
        from scripts import unlock_preseason as up
        results, rules = _tables(True)
        with patch.object(up, "unlock_preseason_predictions", return_value=1) as u, \
             patch.object(up, "delete_season_projections", return_value=1) as d:
            code = up.run(2026, write=True, allow_complete=True,
                          draft_results=results, draft_order=pd.DataFrame(), rules=rules)
        assert code == 0
        u.assert_called_once()
        d.assert_called_once()

    def test_parser_defaults_to_dry_run(self):
        from scripts import unlock_preseason as up
        a = up.build_parser().parse_args(["--season", "2026"])
        assert a.firestore is False and a.i_know_the_draft_is_complete is False


class TestEloTie:
    def test_tie_uses_half_point_actual(self, mock_engine):
        from services.nn_projection_engine import ELO_SIM_K, ELO_SIM_HFA, ELO_SIM_MOV_MIN
        state = np.zeros((4, 2, 6), dtype=np.float32)
        state[:, :, 0] = 1500.0
        mock_engine._vectorized_elo_update(state, 0, 1, np.zeros(4, dtype=np.float32))
        expected = 1.0 / (1.0 + 10.0 ** (-ELO_SIM_HFA / 400.0))
        delta = ELO_SIM_K * ELO_SIM_MOV_MIN * (0.5 - expected)
        assert state[0, 0, 0] == pytest.approx(1500.0 + delta, abs=1e-3)
        assert state[0, 1, 0] == pytest.approx(1500.0 - delta, abs=1e-3)

    def test_tie_not_scored_as_home_loss(self, mock_engine):
        s_tie = np.zeros((1, 2, 6), dtype=np.float32)
        s_tie[:, :, 0] = 1500.0
        s_loss = s_tie.copy()
        mock_engine._vectorized_elo_update(s_tie, 0, 1, np.array([0.0], dtype=np.float32))
        mock_engine._vectorized_elo_update(s_loss, 0, 1, np.array([-0.0001], dtype=np.float32))
        assert s_tie[0, 0, 0] > s_loss[0, 0, 0]

    def test_non_tie_unchanged(self, mock_engine):
        state = np.zeros((1, 2, 6), dtype=np.float32)
        state[:, :, 0] = 1500.0
        mock_engine._vectorized_elo_update(state, 0, 1, np.array([7.0], dtype=np.float32))
        assert state[0, 0, 0] > 1500.0 and state[0, 1, 0] < 1500.0
        s2 = np.zeros((1, 2, 6), dtype=np.float32)
        s2[:, :, 0] = 1500.0
        mock_engine._vectorized_elo_update(s2, 0, 1, np.array([-7.0], dtype=np.float32))
        assert s2[0, 0, 0] < 1500.0
