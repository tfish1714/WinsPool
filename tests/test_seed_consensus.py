"""Validation of the hand-maintained consensus CSV."""
import pandas as pd
import pytest

from scripts.seed_consensus import ALL_TEAMS, validate_and_build


def _full_frame(**overrides):
    """32 valid teams, one source column, with optional per-team overrides."""
    rows = [{"team": t, "vegas_ou": 8.5} for t in ALL_TEAMS]
    for team, patch in overrides.items():
        for r in rows:
            if r["team"] == team:
                r.update(patch)
    return pd.DataFrame(rows)


def test_valid_frame_builds_all_teams():
    rows, errors = validate_and_build(_full_frame(), 2026)
    assert errors == []
    assert len(rows) == 32


def test_missing_team_is_rejected():
    df = _full_frame().iloc[1:]
    rows, errors = validate_and_build(df, 2026)
    assert rows == []
    assert any("missing" in e.lower() for e in errors)


def test_unknown_team_is_rejected():
    df = pd.concat([_full_frame(), pd.DataFrame([{"team": "XYZ", "vegas_ou": 8.5}])])
    _, errors = validate_and_build(df, 2026)
    assert any("XYZ" in e for e in errors)


def test_unknown_source_column_is_rejected():
    df = _full_frame()
    df["mystery_pundit"] = 9.0
    _, errors = validate_and_build(df, 2026)
    assert any("mystery_pundit" in e for e in errors)


def test_out_of_range_value_is_rejected():
    _, errors = validate_and_build(_full_frame(BUF={"vegas_ou": 21.0}), 2026)
    assert any("BUF" in e and "21" in e for e in errors)


def test_row_with_no_sources_is_rejected():
    _, errors = validate_and_build(_full_frame(BUF={"vegas_ou": None}), 2026)
    assert any("BUF" in e for e in errors)


def test_blank_cells_excluded_from_derived_stats():
    """A blank means 'not published', not zero."""
    df = _full_frame()
    df["br"] = 10.0
    df.loc[df["team"] == "BUF", "br"] = None
    rows, errors = validate_and_build(df, 2026)
    assert errors == []
    buf = next(r for r in rows if r["team"] == "BUF")
    assert "br" not in buf["sources"]
    assert buf["sources"] == {"vegas_ou": 8.5}


def test_team_abbreviations_are_normalized():
    df = _full_frame()
    df.loc[df["team"] == "LA", "team"] = "LAR"
    rows, errors = validate_and_build(df, 2026)
    assert errors == []
    assert any(r["team"] == "LA" for r in rows)
    assert not any(r["team"] == "LAR" for r in rows)
