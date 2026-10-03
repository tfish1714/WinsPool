"""Draft/pool writers must clear the local cache AND signal other instances (issue #71)."""
import ast
import pathlib

import pandas as pd
import pytest

import services.db_service as db
from services.cache_service import DOMAIN_STATIC


@pytest.fixture
def spies(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "clear_data_cache", lambda domain: calls.append(("clear", domain)))
    monkeypatch.setattr(db, "signal_data_update", lambda domain="static": calls.append(("signal", domain)))
    monkeypatch.setattr(db, "get_db", lambda: None)
    monkeypatch.setattr(db, "_save_df_to_local", lambda *a, **k: None)
    return calls


def test_invalidate_static_clears_then_signals(spies):
    db._invalidate_static()
    assert spies == [("clear", DOMAIN_STATIC), ("signal", DOMAIN_STATIC)]


def test_delete_draft_results_for_season_signals(spies, monkeypatch):
    monkeypatch.setattr(db, "get_collection_df",
                        lambda name, filters=None: pd.DataFrame({"season": [2026, 2025], "draftPick": [1, 1]}))
    db.delete_draft_results_for_season(2026)
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_add_draft_order_signals(spies, monkeypatch):
    monkeypatch.setattr(db, "get_collection_df", lambda name, filters=None: pd.DataFrame())
    db.add_draft_order(2026, 1, 7)
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_set_member_paid_signals(spies, monkeypatch):
    order = pd.DataFrame({"season": [2026], "playerId": [7], "draftOrder": [1]})
    monkeypatch.setattr(db, "get_collection_df", lambda name, filters=None: order.copy())
    assert db.set_member_paid(2026, 7, True) is True
    assert ("signal", DOMAIN_STATIC) in spies and ("clear", DOMAIN_STATIC) in spies


def test_no_writer_clears_static_cache_without_signalling():
    """Guard: any db_service function that calls clear_data_cache must also signal."""
    tree = ast.parse(pathlib.Path(db.__file__).read_text(encoding="utf-8"))
    offenders = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        names = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        if "clear_data_cache" in names and not ({"signal_data_update", "_invalidate_static"} & names):
            offenders.append(fn.name)
    assert offenders == []
