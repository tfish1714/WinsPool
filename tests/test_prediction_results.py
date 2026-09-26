"""services/prediction_service.build_result_lookup / get_candidate_seasons --
the canonical replacement for the .iterrows() loops that used to be duplicated
in routes/api_routes.py and routes/admin_routes.py."""
import inspect
import time

import numpy as np
import pandas as pd
import pytest

from services.constants import UNDRAFTED_SENTINEL
from services.prediction_service import build_result_lookup, get_candidate_seasons


def _reference_lookup(all_games, season=None):
    """The original row-wise algorithm (admin_routes version), kept here as the
    oracle the vectorized helper must match."""
    from services.nn_feature_engine import _normalize_team
    out = {}
    if all_games is None or all_games.empty:
        return out
    mask = all_games['result'].notna() & (all_games['result'] != -1000)
    if season is not None:
        mask &= (all_games['season'] == season)
    for _, row in all_games[mask].iterrows():
        wk = row.get('week')
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        res = row.get('result', 0)
        if not wk or not ht or not at:
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        winner = ht if res > 0 else at if res < 0 else None
        sl = row.get('spread_line')
        out[key] = {
            "winner": winner,
            "home_score": int(row.get('home_score')) if row.get('home_score') is not None and not pd.isna(row.get('home_score')) else None,
            "away_score": int(row.get('away_score')) if row.get('away_score') is not None and not pd.isna(row.get('away_score')) else None,
            "spread_line": float(sl) if sl is not None and str(sl) not in ('', 'nan') else None,
        }
    return out


def _games():
    rows = [
        # season, week, home, away, result, hs, as, spread
        (3000, 1, "KC", "BAL", 3, 27, 24, -2.5),
        (3000, 1, "DEN", "LV", -7, 10, 17, 3.0),
        (3000, 2, "SF", "SEA", 0, 20, 20, -6.0),                 # tie
        (3000, 2, "DAL", "NYG", UNDRAFTED_SENTINEL, None, None, 1.0),   # unplayed sentinel
        (3000, 3, "MIA", "NE", np.nan, None, None, np.nan),      # NaN result
        (3000, 0, "GB", "CHI", 4, 14, 10, 1.0),                  # week 0 skipped
        (3000, 4, "", "CHI", 4, 14, 10, 1.0),                    # empty team skipped
        (3000, 5, "LA", "SF", 6, 24, 18, np.nan),                # alias team, NaN spread
        (3001, 1, "KC", "BAL", -3, 20, 23, -1.0),                # same key, other season
        (3000, 6, "KC", "BAL", 10, 30, 20, -1.0),
        (3000, 6, "KC", "BAL", -10, 20, 30, -1.0),               # duplicate key: last wins
    ]
    return pd.DataFrame(rows, columns=["season", "week", "home_team", "away_team", "result",
                                       "home_score", "away_score", "spread_line"])


def test_matches_reference_for_all_seasons():
    assert build_result_lookup(_games()) == _reference_lookup(_games())


def test_matches_reference_for_one_season():
    assert build_result_lookup(_games(), 3000) == _reference_lookup(_games(), 3000)
    assert build_result_lookup(_games(), 3001) == _reference_lookup(_games(), 3001)


def test_season_scoping_prevents_cross_season_key_collision():
    a = build_result_lookup(_games(), 3000)["W01_KC_BAL"]["winner"]
    b = build_result_lookup(_games(), 3001)["W01_KC_BAL"]["winner"]
    assert (a, b) == ("KC", "BAL")


def test_tie_has_none_winner_and_unplayed_rows_are_absent():
    lookup = build_result_lookup(_games(), 3000)
    assert lookup["W02_SF_SEA"]["winner"] is None
    assert "W02_DAL_NYG" not in lookup and "W03_MIA_NE" not in lookup


def test_duplicate_key_last_row_wins():
    assert build_result_lookup(_games(), 3000)["W06_KC_BAL"]["winner"] == "BAL"


def test_missing_spread_column_and_nan_scores_yield_none():
    df = _games().drop(columns=["spread_line"])
    lookup = build_result_lookup(df, 3000)
    assert lookup["W01_KC_BAL"]["spread_line"] is None
    df2 = _games()
    df2.loc[0, ["home_score", "away_score"]] = np.nan
    assert build_result_lookup(df2, 3000)["W01_KC_BAL"]["home_score"] is None


@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_empty_input_returns_empty_dict(empty):
    assert build_result_lookup(empty) == {}


def test_helper_source_has_no_iterrows():
    assert "iterrows" not in inspect.getsource(build_result_lookup)


def test_large_frame_is_fast():
    n = 20000
    df = pd.DataFrame({
        "season": np.repeat(np.arange(2000, 2000 + n // 300 + 1), 300)[:n],
        "week": np.tile(np.arange(1, 19), n // 18 + 1)[:n],
        "home_team": np.tile(["KC", "BAL", "SF", "DEN"], n // 4 + 1)[:n],
        "away_team": np.tile(["LV", "NE", "SEA", "MIA"], n // 4 + 1)[:n],
        "result": np.tile([3, -7, 0, 10], n // 4 + 1)[:n],
        "home_score": 20, "away_score": 17, "spread_line": -1.5,
    })
    start = time.perf_counter()
    build_result_lookup(df)
    assert time.perf_counter() - start < 3.0


def test_candidate_seasons_prefers_local_files(tmp_path, monkeypatch):
    (tmp_path / ".local_db").mkdir()
    for name in ("game_predictions_2024.json", "game_predictions_2023.json", "game_predictions_bad.json"):
        (tmp_path / ".local_db" / name).write_text("{}")
    monkeypatch.chdir(tmp_path)
    assert sorted(get_candidate_seasons()) == [2023, 2024]


def test_candidate_seasons_falls_back_to_firestore(tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch
    monkeypatch.chdir(tmp_path)
    docs = []
    for doc_id in ("2024", "2023", "meta"):
        d = MagicMock(); d.id = doc_id; docs.append(d)
    db = MagicMock(); db.collection.return_value.stream.return_value = docs
    with patch("services.db_service.get_db", return_value=db):
        assert sorted(get_candidate_seasons()) == [2023, 2024]


def test_candidate_seasons_swallows_firestore_failure(tmp_path, monkeypatch):
    from unittest.mock import patch
    monkeypatch.chdir(tmp_path)
    with patch("services.db_service.get_db", side_effect=RuntimeError("down")):
        assert get_candidate_seasons() == []


def test_float_week_values_still_produce_zero_padded_keys():
    df = _games()
    df["week"] = df["week"].astype(float)
    assert build_result_lookup(df, 3000) == build_result_lookup(_games(), 3000)
    assert "W05_LA_SF" in build_result_lookup(df, 3000)
