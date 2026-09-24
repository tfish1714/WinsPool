"""static/js/grade_badge.js -- the "Why TEAM?" modal's ATS badge. A pushed bet
must render a distinct neutral PUSH badge, never a check mark: the server
sends "push" (a truthy string) and a naive truthiness check would show a win.

There is no JS unit-test runner in this repo, so this drives the pure ES
module through Node and skips when Node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "grade_badge.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _badge(value_js: str) -> str:
    script = (
        f"import {{ gradeBadge }} from {json.dumps(MODULE.as_uri())};"
        f"console.log(JSON.stringify(gradeBadge({value_js})));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_true_renders_a_check():
    html = _badge("true")
    assert "✓" in html and "accent-green" in html


def test_false_renders_a_cross():
    html = _badge("false")
    assert "✗" in html and "accent-red" in html


def test_push_renders_a_neutral_push_badge_not_a_check():
    html = _badge("'push'")
    assert "PUSH" in html
    assert "✓" not in html and "✗" not in html
    assert "accent-green" not in html and "accent-red" not in html


@pytest.mark.parametrize("value", ["null", "undefined", "'unexpected'", "0", "''"])
def test_null_undefined_and_unknown_values_render_nothing(value):
    assert _badge(value) == ""
