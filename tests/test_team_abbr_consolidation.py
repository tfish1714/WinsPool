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
    """True if any dict literal in source has both "WSH" and "JAC" string keys."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if {"WSH", "JAC"} <= keys:
                return True
    return False


def test_abbr_dict_detector():
    assert _has_abbr_dict_literal('M = {"WSH": "WAS", "JAC": "JAX"}')
    assert _has_abbr_dict_literal("M = {'JAC': 'JAX', 'X': 1, 'WSH': 'WAS'}")
    assert not _has_abbr_dict_literal('M = {"JAC": "JAX"}')
    assert not _has_abbr_dict_literal('s = "JAC"; t = "WSH"')


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
