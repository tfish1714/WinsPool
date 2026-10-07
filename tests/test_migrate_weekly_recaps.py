from scripts import migrate_weekly_recaps as mig
from fake_firestore import FakeFirestore


def _legacy(fs):
    fs.collection("weekly_recaps").document("2025_3").set(
        {"year": 2025, "week": 3, "summary": "three", "timestamp": 1.0})
    fs.collection("weekly_recaps").document("2025_4").set(
        {"year": 2025, "week": 4, "summary": "four", "timestamp": 2.0})
    fs.collection("weekly_recaps").document("2024_1").set(
        {"year": 2024, "week": 1, "summary": "old", "timestamp": 0.5})


def test_dry_run_writes_nothing():
    fs = FakeFirestore(); _legacy(fs)
    report = mig.migrate(fs, write=False)
    assert report == {2024: [1], 2025: [3, 4]}
    assert "season_recaps" not in fs.store


def test_write_folds_weeks_and_is_idempotent_and_keeps_legacy():
    fs = FakeFirestore(); _legacy(fs)
    mig.migrate(fs, write=True)
    mig.migrate(fs, write=True)
    weeks = fs.store["season_recaps"]["2025"]["weeks"]
    assert set(weeks) == {"3", "4"} and weeks["4"]["summary"] == "four"
    assert len(fs.store["weekly_recaps"]) == 3


def test_does_not_overwrite_a_newer_published_week():
    fs = FakeFirestore(); _legacy(fs)
    fs.collection("season_recaps").document("2025").set(
        {"year": 2025, "weeks": {"3": {"summary": "published", "timestamp": 9.0}}})
    mig.migrate(fs, write=True)
    assert fs.store["season_recaps"]["2025"]["weeks"]["3"]["summary"] == "published"
    assert fs.store["season_recaps"]["2025"]["weeks"]["4"]["summary"] == "four"


def test_refresh_local_pkls_mirrors_season_recaps_to_json(monkeypatch, tmp_path):
    import json
    import scripts.refresh_local_pkls as rlp
    fs = FakeFirestore()
    fs.collection("season_recaps").document("2025").set(
        {"year": 2025, "weeks": {"3": {"summary": "three", "timestamp": 1.0}}})
    monkeypatch.setattr(rlp, "get_db", lambda: fs)
    monkeypatch.setattr(rlp, "LOCAL_DB", tmp_path)
    rlp.dump_season_recaps()
    out = json.loads((tmp_path / "season_recaps_2025.json").read_text())
    assert out["year"] == 2025 and out["weeks"]["3"]["summary"] == "three"


def test_refresh_main_runs_season_recaps_step(monkeypatch, tmp_path):
    import json
    import scripts.refresh_local_pkls as rlp
    fs = FakeFirestore()
    fs.collection("season_recaps").document("2025").set(
        {"year": 2025, "weeks": {"4": {"summary": "four", "timestamp": 2.0}}})
    monkeypatch.setattr(rlp, "get_db", lambda: fs)
    monkeypatch.setattr(rlp, "LOCAL_DB", tmp_path)
    monkeypatch.setattr(rlp, "COLLECTIONS", [])
    for name in ("dump_game_predictions", "dump_prediction_features", "dump_elo_history",
                 "dump_nn_weekly_accuracy", "dump_quarter_scores",
                 "dump_config_settings", "dump_analytics_cache"):
        monkeypatch.setattr(rlp, name, lambda *a, **k: None)
    monkeypatch.setattr("sys.argv", ["refresh_local_pkls.py"])
    rlp.main()
    out = json.loads((tmp_path / "season_recaps_2025.json").read_text())
    assert out["weeks"]["4"]["summary"] == "four"
