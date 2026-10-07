import pytest
import services.db_service as db
from fake_firestore import FakeFirestore


@pytest.fixture
def fs(monkeypatch):
    fake = FakeFirestore()
    monkeypatch.setattr(db, "get_db", lambda: fake)
    monkeypatch.setenv("USE_LOCAL_DATA", "False")
    db.clear_recap_cache()
    yield fake
    db.clear_recap_cache()


def test_save_then_get_roundtrip(fs):
    db.save_weekly_recap(2026, 5, "Week five text")
    got = db.get_weekly_recap(2026, 5)
    assert got["summary"] == "Week five text" and got["week"] == 5 and got["year"] == 2026
    assert db.list_recap_weeks(2026) == [5]


def test_saving_week_6_keeps_week_5_and_resave_replaces_only_that_week(fs):
    db.save_weekly_recap(2026, 5, "five")
    db.save_weekly_recap(2026, 6, "six")
    db.save_weekly_recap(2026, 6, "six v2")
    assert db.get_weekly_recap(2026, 5)["summary"] == "five"
    assert db.get_weekly_recap(2026, 6)["summary"] == "six v2"
    assert fs.store["season_recaps"]["2026"]["weeks"]["5"]["summary"] == "five"


def test_source_is_stored(fs):
    db.save_weekly_recap(2026, 7, "x", source="published")
    assert db.get_season_recaps(2026)[7]["source"] == "published"


def test_missing_week_returns_none_and_empty_list(fs):
    assert db.get_weekly_recap(2026, 3) is None
    assert db.list_recap_weeks(2026) == []


def test_reads_are_cached_until_save(fs, monkeypatch):
    db.save_weekly_recap(2026, 1, "a")
    db.get_season_recaps(2026)
    calls = []
    real = fs.collection
    monkeypatch.setattr(fs, "collection", lambda n: calls.append(n) or real(n))
    db.get_season_recaps(2026)
    assert calls == []                      # served from cache
    db.save_weekly_recap(2026, 2, "b")      # save clears the cache
    calls.clear()
    assert db.get_season_recaps(2026)[2]["summary"] == "b"
    assert calls                            # re-read


def test_legacy_per_week_docs_are_a_fallback_when_no_season_doc(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "old three", "timestamp": 1.0})
    fs.collection("weekly_recaps").document("2025_4").set(
        {"year": 2025, "week": 4, "summary": "old four", "timestamp": 2.0})
    assert db.list_recap_weeks(2025) == [3, 4]
    assert db.get_weekly_recap(2025, 4)["summary"] == "old four"


def test_season_doc_wins_over_legacy(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "legacy", "timestamp": 1.0})
    db.save_weekly_recap(2025, 9, "new nine")
    db.clear_recap_cache()
    assert db.list_recap_weeks(2025) == [9]


def test_local_mode_json_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "get_db", lambda: None)
    monkeypatch.setenv("USE_LOCAL_DATA", "True")
    monkeypatch.setattr(db, "local_db_dir", lambda: tmp_path)
    db.clear_recap_cache()
    db.save_weekly_recap(2026, 2, "local two")
    db.clear_recap_cache()
    assert (tmp_path / "season_recaps_2026.json").exists()
    assert db.get_weekly_recap(2026, 2)["summary"] == "local two"
    db.save_weekly_recap(2026, 3, "local three")
    assert db.list_recap_weeks(2026) == [2, 3]
    db.clear_recap_cache()
