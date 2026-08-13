"""Storage round-trip for consensus_projections."""
import pandas as pd
import pytest

import services.data_service as data_service


def test_get_consensus_projections_shapes_rows(monkeypatch):
    fake = pd.DataFrame([
        {"season": 2026, "team": "BUF", "sources": {"br": 12, "vegas_ou": 11.5},
         "n_sources": 2, "consensus_mean": 11.75, "consensus_median": 11.75,
         "consensus_min": 11.5, "consensus_max": 12.0, "consensus_std": 0.25},
    ])
    monkeypatch.setattr(data_service, "get_collection_df", lambda *a, **k: fake)

    res = data_service.get_consensus_projections(2026)
    assert set(res) == {"BUF"}
    assert res["BUF"]["consensus_mean"] == 11.75
    assert res["BUF"]["sources"]["vegas_ou"] == 11.5


def test_get_consensus_projections_empty_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(data_service, "get_collection_df", lambda *a, **k: pd.DataFrame())
    assert data_service.get_consensus_projections(2026) == {}


def test_set_consensus_projections_writes_derived_stats(monkeypatch):
    written = {}

    class FakeDoc:
        def __init__(self, doc_id):
            self.doc_id = doc_id

    class FakeBatch:
        def set(self, ref, payload):
            written[ref.doc_id] = payload

        def commit(self):
            pass

    class FakeCollection:
        def document(self, doc_id):
            return FakeDoc(doc_id)

    class FakeDB:
        def collection(self, name):
            assert name == "consensus_projections"
            return FakeCollection()

        def batch(self):
            return FakeBatch()

    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_db", lambda: FakeDB())

    count = db_service.set_consensus_projections(2026, [
        {"team": "BUF", "sources": {"br": 12, "vegas_ou": 11.5}, "as_of": "2026-08-12"},
    ])

    assert count == 1
    payload = written["2026_BUF"]
    assert payload["season"] == 2026
    assert payload["team"] == "BUF"
    assert payload["n_sources"] == 2
    assert payload["consensus_mean"] == pytest.approx(11.75)
    assert payload["as_of"] == "2026-08-12"


def test_set_consensus_projections_no_db_returns_zero(monkeypatch):
    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_db", lambda: None)

    count = db_service.set_consensus_projections(2026, [
        {"team": "BUF", "sources": {"br": 12, "vegas_ou": 11.5}, "as_of": "2026-08-12"},
    ])

    assert count == 0


def test_refresh_local_pkls_registers_consensus_collection():
    from scripts.refresh_local_pkls import COLLECTIONS
    assert ("consensus_projections", "season") in COLLECTIONS
