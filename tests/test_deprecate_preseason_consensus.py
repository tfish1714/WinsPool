"""The gated deletion of migrated consensus rows."""
import datetime
import json

import pandas as pd
import pytest

from scripts.deprecate_preseason_consensus import (
    backup_path,
    fetch_documents,
    find_consensus_doc_ids,
    write_backup,
)


def test_identifies_consensus_rows_only():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI", "sources": {"BR": 10, "O/U": 8.5}},
        {"season": 2026, "team": "LA", "sources": {"model": "nn_xgb_lr_ensemble"}},
    ])
    assert find_consensus_doc_ids(df) == ["2025_ARI"]


def test_row_without_sources_is_left_alone():
    df = pd.DataFrame([{"season": 2026, "team": "LA", "model_version": "nn_v14"}])
    assert find_consensus_doc_ids(df) == []


def test_returns_empty_for_empty_frame():
    assert find_consensus_doc_ids(pd.DataFrame()) == []


# --- Backup: the deletion must be recoverable, not merely gated -------------


class _FakeSnapshot:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class _FakeDoc:
    def __init__(self, doc_id, store, events):
        self.id = doc_id
        self._store = store
        self._events = events

    def get(self):
        self._events.append(("read", self.id))
        return _FakeSnapshot(self._store.get(self.id))


class _FakeCollection:
    def __init__(self, store, events):
        self._store = store
        self._events = events

    def document(self, doc_id):
        return _FakeDoc(doc_id, self._store, self._events)


class _FakeBatch:
    def __init__(self, events):
        self._events = events

    def delete(self, ref):
        self._events.append(("delete", ref.id))

    def commit(self):
        self._events.append(("commit", None))


class _FakeDB:
    def __init__(self, store):
        self._store = store
        self.events = []

    def collection(self, name):
        return _FakeCollection(self._store, self.events)

    def batch(self):
        return _FakeBatch(self.events)


def test_fetch_documents_captures_id_and_full_fields():
    db = _FakeDB({
        "2025_ARI": {"season": 2025, "team": "ARI", "sources": {"BR": 10}},
        "2024_BUF": {"season": 2024, "team": "BUF", "sources": {"ESPN": 11}},
    })
    records = fetch_documents(db, ["2025_ARI", "2024_BUF"])
    assert [r["id"] for r in records] == ["2025_ARI", "2024_BUF"]
    assert records[0]["data"] == {"season": 2025, "team": "ARI", "sources": {"BR": 10}}
    assert records[1]["data"]["sources"] == {"ESPN": 11}


def test_write_backup_round_trips_every_document(tmp_path):
    records = [
        {"id": "2025_ARI", "data": {"season": 2025, "team": "ARI", "sources": {"BR": 10}}},
        {"id": "2024_BUF", "data": {"season": 2024, "team": "BUF", "sources": {"ESPN": 11}}},
    ]
    path = tmp_path / "backup.json"
    written = write_backup(records, path)
    assert written == path.resolve()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["collection"] == "preseason_predictions"
    assert payload["count"] == 2
    assert payload["documents"] == records


def test_write_backup_serializes_non_json_values(tmp_path):
    records = [{"id": "2025_ARI",
                "data": {"as_of": datetime.datetime(2025, 8, 1, 12, 0, 0)}}]
    path = tmp_path / "backup.json"
    write_backup(records, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "2025-08-01" in payload["documents"][0]["data"]["as_of"]


def test_write_backup_raises_when_path_is_unwritable(tmp_path):
    # A directory standing where the backup file should go: open() must fail,
    # which is what makes main() abort before deleting anything.
    path = tmp_path / "backup.json"
    path.mkdir()
    with pytest.raises(OSError):
        write_backup([{"id": "x", "data": {}}], path)


def test_backup_path_is_timestamped_in_local_db():
    now = datetime.datetime(2026, 8, 12, 9, 5, 4)
    path = backup_path(now)
    assert path.name == "backup_preseason_consensus_20260812_090504.json"
    assert path.parent.name == ".local_db"


def test_backup_is_written_before_any_delete(monkeypatch, tmp_path):
    """The safety property: no document is deleted until the backup exists."""
    import scripts.deprecate_preseason_consensus as mod

    store = {"2025_ARI": {"season": 2025, "team": "ARI", "sources": {"BR": 10}}}
    db = _FakeDB(store)

    df = pd.DataFrame([{"season": 2025, "team": "ARI", "sources": {"BR": 10}}])
    monkeypatch.setattr(mod, "get_collection_df", lambda name: df)
    monkeypatch.setattr(mod, "get_consensus_projections", lambda season: {"ARI": {}})
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr(mod, "backup_path", lambda now=None: tmp_path / "backup.json")

    real_write = mod.write_backup

    def spy(records, path):
        db.events.append(("backup", len(records)))
        return real_write(records, path)

    monkeypatch.setattr(mod, "write_backup", spy)
    monkeypatch.setattr("sys.argv", ["deprecate", "--confirm"])

    mod.main()

    kinds = [e[0] for e in db.events]
    assert "backup" in kinds, "no backup was written"
    assert "delete" in kinds, "nothing was deleted"
    assert kinds.index("backup") < kinds.index("delete")
    assert json.loads((tmp_path / "backup.json").read_text(encoding="utf-8"))["count"] == 1


def test_deletion_aborts_when_backup_fails(monkeypatch, tmp_path):
    import scripts.deprecate_preseason_consensus as mod

    db = _FakeDB({"2025_ARI": {"season": 2025, "team": "ARI", "sources": {"BR": 10}}})
    df = pd.DataFrame([{"season": 2025, "team": "ARI", "sources": {"BR": 10}}])
    monkeypatch.setattr(mod, "get_collection_df", lambda name: df)
    monkeypatch.setattr(mod, "get_consensus_projections", lambda season: {"ARI": {}})
    monkeypatch.setattr(mod, "get_db", lambda: db)

    bad = tmp_path / "backup.json"
    bad.mkdir()  # unwritable
    monkeypatch.setattr(mod, "backup_path", lambda now=None: bad)
    monkeypatch.setattr("sys.argv", ["deprecate", "--confirm"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    assert "delete" not in [e[0] for e in db.events]


def test_dry_run_deletes_nothing(monkeypatch):
    import scripts.deprecate_preseason_consensus as mod

    db = _FakeDB({"2025_ARI": {"season": 2025, "team": "ARI", "sources": {"BR": 10}}})
    df = pd.DataFrame([{"season": 2025, "team": "ARI", "sources": {"BR": 10}}])
    monkeypatch.setattr(mod, "get_collection_df", lambda name: df)
    monkeypatch.setattr(mod, "get_consensus_projections", lambda season: {"ARI": {}})
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr("sys.argv", ["deprecate", "--dry-run"])

    mod.main()
    assert db.events == []


def test_refuses_to_delete_unmigrated_rows(monkeypatch):
    """The gate: consensus_projections must already hold every queued row."""
    import scripts.deprecate_preseason_consensus as mod

    db = _FakeDB({})
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI", "sources": {"BR": 10}},
        {"season": 2025, "team": "BUF", "sources": {"BR": 11}},
    ])
    monkeypatch.setattr(mod, "get_collection_df", lambda name: df)
    monkeypatch.setattr(mod, "get_consensus_projections", lambda season: {"ARI": {}})
    monkeypatch.setattr(mod, "get_db", lambda: db)
    monkeypatch.setattr("sys.argv", ["deprecate", "--confirm"])

    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 1
    assert db.events == []
