"""scripts/backfill_draft_snapshot.py -- one-time copy of every already-locked
past season's preseason_predictions into draft_snapshot_predictions.

Pure copy, not a recomputation -- nothing rewrites a preseason_predictions
doc once locked=True (see scripts/cache_builder.py's set_preseason_predictions
call), so every locked season's numbers are already frozen at the source."""
import pandas as pd

from scripts.backfill_draft_snapshot import find_locked_seasons, run


def test_find_locked_seasons_only_returns_fully_locked_seasons():
    preds_df = pd.DataFrame([
        {"season": 2024, "team": "KC", "locked": True},
        {"season": 2024, "team": "SF", "locked": True},
        {"season": 2025, "team": "KC", "locked": True},
        {"season": 2025, "team": "SF", "locked": False},  # not fully locked
        {"season": 2026, "team": "KC", "locked": False},
    ])

    seasons = find_locked_seasons(preds_df)

    assert seasons == [2024]  # 2025 has an unlocked team, 2026 has none locked


def test_find_locked_seasons_treats_missing_locked_field_as_unlocked():
    """predict_season.py's manual writes never set `locked` at all (see
    CLAUDE.md's footgun note) -- those rows must not be mistaken for locked."""
    preds_df = pd.DataFrame([
        {"season": 2019, "team": "KC"},  # no `locked` column value at all
    ])
    assert find_locked_seasons(preds_df) == []


def test_run_dry_run_does_not_write(monkeypatch):
    import scripts.backfill_draft_snapshot as bds
    monkeypatch.setattr(bds, "get_collection_df", lambda name, filters=None: pd.DataFrame([
        {"season": 2024, "team": "KC", "projected_wins": 10.0, "mean_wins": 9.8,
         "std_dev": 1.5, "floor": 6.0, "p25": 8.0, "p75": 11.0, "ceiling": 13.0,
         "model_version": "nn_v9", "locked": True},
    ]))
    calls = []
    monkeypatch.setattr(bds, "set_draft_snapshot_predictions",
                        lambda *a, **k: calls.append((a, k)) or 0)

    result = bds.run(dry_run=True)

    assert result == {2024: 1}  # reports what WOULD be written
    assert calls == []


def test_run_live_writes_each_locked_season(monkeypatch):
    import scripts.backfill_draft_snapshot as bds
    monkeypatch.setattr(bds, "get_collection_df", lambda name, filters=None: pd.DataFrame([
        {"season": 2024, "team": "KC", "projected_wins": 10.0, "mean_wins": 9.8,
         "std_dev": 1.5, "floor": 6.0, "p25": 8.0, "p75": 11.0, "ceiling": 13.0,
         "model_version": "nn_v9", "locked": True},
        {"season": 2025, "team": "SF", "projected_wins": 12.0, "mean_wins": 11.5,
         "std_dev": 1.2, "floor": 9.0, "p25": 11.0, "p75": 13.0, "ceiling": 14.0,
         "model_version": "nn_v10", "locked": True},
    ]))
    captured = []

    def fake_set(season, projections, model_version, locked, force=False):
        captured.append((season, locked))
        return len(projections)

    monkeypatch.setattr(bds, "set_draft_snapshot_predictions", fake_set)

    result = bds.run(dry_run=False)

    assert result == {2024: 1, 2025: 1}
    assert (2024, True) in captured
    assert (2025, True) in captured
