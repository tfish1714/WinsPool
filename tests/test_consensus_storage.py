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

    # signal_data_update() itself short-circuits under USE_LOCAL_DATA=true
    # (set globally for the test session in conftest.py), so it can't be
    # observed via the FakeDB write path -- spy on the call directly instead.
    signaled = []
    monkeypatch.setattr(db_service, "signal_data_update", lambda: signaled.append(True))

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

    # Regression guard: the deployed app's in-memory cache has a 1-hour TTL,
    # so writes here must signal remote cache invalidation the same way
    # cache_builder.py and predict_season.py already do, or fresh consensus
    # data silently doesn't appear on the live site for up to an hour.
    assert signaled == [True]


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


def test_get_preseason_predictions_mean_wins_nan_falls_back_to_projected_wins(monkeypatch):
    """mean_wins can be a present-but-NaN column, not merely an absent key.

    This mirrors the real data shape: once any season's rows populate
    mean_wins (e.g. 2026 model rows), pandas gives every other season's rows
    that same column back as NaN rather than omitting it -- so a plain
    `row.get("mean_wins", default)` never falls back, because the key exists.
    Regression test for the IntCastingNaNError this caused in
    consensus_service.build_comparison's ranking step.
    """
    df = pd.DataFrame([
        {"season": 2025, "team": "BUF", "projected_wins": 11.5, "mean_wins": float("nan"),
         "std_dev": 1.0, "sources": {}},
        {"season": 2026, "team": "BUF", "projected_wins": 10.0, "mean_wins": 10.25,
         "std_dev": 1.0, "sources": {}},
    ])

    def fake_get_collection_df(collection, filters=None, **kwargs):
        assert collection == "preseason_predictions"
        season = next((f[2] for f in (filters or []) if f[0] == "season"), None)
        return df[df["season"] == season] if season is not None else df

    monkeypatch.setattr(data_service, "get_collection_df", fake_get_collection_df)

    nan_season = data_service.get_preseason_predictions(2025)
    assert nan_season["BUF"]["mean_wins"] == 11.5  # falls back to projected_wins
    assert isinstance(nan_season["BUF"]["mean_wins"], float)
    assert not pd.isna(nan_season["BUF"]["mean_wins"])

    populated_season = data_service.get_preseason_predictions(2026)
    assert populated_season["BUF"]["mean_wins"] == 10.25  # real value, not the fallback
