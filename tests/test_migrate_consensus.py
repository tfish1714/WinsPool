"""Migration of stored consensus out of preseason_predictions."""
import pandas as pd
import pytest

from scripts.migrate_consensus import build_migration_rows, map_source_key


def test_maps_every_known_stored_key():
    """These nine are the complete stored key set across 2017-2025."""
    assert map_source_key("O/U") == "vegas_ou"
    assert map_source_key("BR") == "br"
    assert map_source_key("CBS") == "cbs"
    assert map_source_key("ESPN") == "espn"
    assert map_source_key("FPI") == "fpi"
    assert map_source_key("NFL") == "nfl"
    assert map_source_key("PFF") == "pff"
    assert map_source_key("SI") == "si"
    assert map_source_key("Clay") == "clay"


def test_unknown_source_key_is_reported_not_dropped():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI", "sources": {"BR": 10, "MysteryPundit": 9}},
    ])
    rows, errors = build_migration_rows(df)
    assert rows == []
    assert any("MysteryPundit" in e for e in errors)


def test_migrates_known_2025_arizona_row():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI",
         "sources": {"BR": 10, "FPI": 8.3, "SI": 6, "O/U": 8.5, "Clay": 7.5}},
    ])
    rows, errors = build_migration_rows(df)
    assert errors == []
    assert len(rows) == 1
    assert rows[0]["team"] == "ARI"
    assert rows[0]["sources"] == {
        "br": 10.0, "fpi": 8.3, "si": 6.0, "vegas_ou": 8.5, "clay": 7.5,
    }


def test_skips_model_rows():
    df = pd.DataFrame([
        {"season": 2026, "team": "LA", "sources": {"model": "nn_xgb_lr_ensemble"}},
    ])
    rows, errors = build_migration_rows(df)
    assert rows == []
    assert errors == []


def test_normalizes_team_abbreviations():
    df = pd.DataFrame([
        {"season": 2025, "team": "LAR", "sources": {"BR": 9}},
    ])
    rows, _ = build_migration_rows(df)
    assert rows[0]["team"] == "LA"
