"""static/js/nav_gating.js -- the Playoff Race nav link only appears from
Week 10. There is no JS unit-test runner in this repo, so this drives the
pure ES module through Node and skips when Node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

from services.constants import PLAYOFF_RACE_MIN_WEEK

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "nav_gating.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    script = (
        f"import * as m from {json.dumps(MODULE.as_uri())};"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_js_constant_matches_python_constant():
    assert _run("m.PLAYOFF_RACE_MIN_WEEK") == PLAYOFF_RACE_MIN_WEEK


@pytest.mark.parametrize("week,expected", [
    (1, False), (9, False), (10, True), (11, True), (18, True),
])
def test_visibility_threshold(week, expected):
    assert _run(f"m.isPlayoffRaceVisible({week})") is expected


@pytest.mark.parametrize("value", ["null", "undefined", "'abc'", "NaN", "-3", "''"])
def test_garbage_values_stay_hidden(value):
    assert _run(f"m.isPlayoffRaceVisible({value})") is False


def test_numeric_string_from_localstorage_is_accepted():
    assert _run("m.isPlayoffRaceVisible('12')") is True
