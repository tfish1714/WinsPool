"""One canonical team-abbreviation map (GitHub #101)."""
import ast
import pathlib
import pytest

from services.constants import TEAM_ABBR_MAP
from services.utils import normalize_team_abbr

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("raw,expected", [
    ("LAR", "LA"), ("WSH", "WAS"), ("JAC", "JAX"),
    ("OAK", "LV"), ("SD", "LAC"), ("STL", "LA"),
    ("buf", "BUF"), (" kc ", "KC"), ("LA", "LA"), ("LV", "LV"), ("", ""),
])
def test_normalize_team_abbr_canonical(raw, expected):
    assert normalize_team_abbr(raw) == expected


def test_normalize_team_abbr_passes_non_strings_through():
    nan = float("nan")
    assert normalize_team_abbr(nan) is nan
    assert normalize_team_abbr(None) is None


def test_canonical_map_contents():
    assert TEAM_ABBR_MAP == {
        "LAR": "LA", "WSH": "WAS", "JAC": "JAX",
        "OAK": "LV", "SD": "LAC", "STL": "LA",
    }


def test_nn_feature_engine_reuses_canonical_map_and_function():
    import services.nn_feature_engine as nfe
    assert nfe.TEAM_ABBR_MAP is TEAM_ABBR_MAP
    for raw in ("OAK", "wsh", " kc ", "LAR"):
        assert nfe._normalize_team(raw) == normalize_team_abbr(raw)
    nan = float("nan")
    assert nfe._normalize_team(nan) is nan


def _has_abbr_dict_literal(source: str) -> bool:
    """True if any dict literal maps two or more legacy abbreviations (the keys of
    TEAM_ABBR_MAP) to string values, i.e. looks like a second abbreviation map.
    Tables keyed by the same abbreviations with non-string values (for example
    stadium coordinates) are not abbreviation maps and are ignored."""
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        hits = 0
        for k, v in zip(node.keys, node.values):
            if (isinstance(k, ast.Constant) and k.value in TEAM_ABBR_MAP
                    and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                hits += 1
        if hits >= 2:
            return True
    return False


def test_abbr_dict_detector():
    assert _has_abbr_dict_literal('M = {"WSH": "WAS", "JAC": "JAX"}')
    assert _has_abbr_dict_literal("M = {'JAC': 'JAX', 'X': 1, 'WSH': 'WAS'}")
    assert not _has_abbr_dict_literal('M = {"JAC": "JAX"}')
    assert not _has_abbr_dict_literal('s = "JAC"; t = "WSH"')


def test_abbr_dict_detector_catches_any_two_legacy_keys_but_not_coordinate_tables():
    assert _has_abbr_dict_literal('M = {"JAC": "JAX", "OAK": "LV"}')
    assert _has_abbr_dict_literal('M = {"SD": "LAC", "STL": "LA", "X": "Y"}')
    assert not _has_abbr_dict_literal('M = {"OAK": (37.7, -122.2), "SD": (32.7, -117.1)}')
    assert not _has_abbr_dict_literal('M = {"OAK": 1}')


def test_no_second_abbreviation_dict_outside_constants():
    offenders = []
    for folder in ("services", "routes", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "constants.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if _has_abbr_dict_literal(text):
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_call_sites_import_canonical_function_not_private_alias():
    offenders = []
    for folder in ("routes", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "_normalize_team" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
