"""static/js/relative_time.js -- the admin panel's "Just now / 15m ago / 2h ago"
formatter. There is no JS unit-test runner in this repo, so this drives the
pure ES module through Node and skips when Node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "relative_time.js"
NOW_S = 1_800_000_000  # 2027-01-15 08:00:00 UTC

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(expr: str):
    script = (
        f"import {{ formatRelativeTime, isFresh }} from {json.dumps(MODULE.as_uri())};"
        f"const now = {NOW_S} * 1000;"
        f"console.log(JSON.stringify({expr}));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, check=True, timeout=20,
    )
    return json.loads(out.stdout)


@pytest.mark.parametrize("age_s, expected", [
    (0, "Just now"),
    (59, "Just now"),
    (60, "1m ago"),
    (15 * 60, "15m ago"),
    (59 * 60 + 59, "59m ago"),
    (60 * 60, "1h ago"),
    (2 * 3600 + 5, "2h ago"),
    (23 * 3600 + 59 * 60, "23h ago"),
    (24 * 3600, "1d ago"),
    (6 * 86400 + 3600, "6d ago"),
])
def test_relative_buckets(age_s, expected):
    assert _run(f"formatRelativeTime({NOW_S - age_s}, now)") == expected


def test_older_than_a_week_is_an_absolute_date():
    text = _run(f"formatRelativeTime({NOW_S - 10 * 86400}, now)")
    assert text == "Jan 5, 2027"


@pytest.mark.parametrize("ts", ["null", "undefined", "0", "'abc'", "NaN"])
def test_missing_or_invalid_returns_null(ts):
    assert _run(f"formatRelativeTime({ts}, now)") is None


def test_future_timestamp_from_clock_skew_reads_as_just_now():
    assert _run(f"formatRelativeTime({NOW_S + 120}, now)") == "Just now"


def test_is_fresh_under_fifteen_minutes_only():
    assert _run(f"isFresh({NOW_S - 14 * 60}, now)") is True
    assert _run(f"isFresh({NOW_S - 15 * 60}, now)") is False
    assert _run("isFresh(null, now)") is False
