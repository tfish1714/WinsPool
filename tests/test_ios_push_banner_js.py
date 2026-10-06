"""static/js/ios_push_hint.js -- pure helper driven through Node (skipped without node)."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "ios_push_hint.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
IPAD = "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
MAC_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


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


def _hint(**kw):
    args = {"userAgent": IPHONE, "standalone": False, "dismissed": False}
    args.update(kw)
    return _run(f"m.shouldShowIosInstallHint({json.dumps(args)})")


def test_iphone_shows():
    assert _hint() is True


def test_iphone_standalone_hidden():
    assert _hint(standalone=True) is False


def test_desktop_chrome_hidden():
    assert _hint(userAgent=CHROME) is False


def test_iphone_dismissed_hidden():
    assert _hint(dismissed=True) is False


def test_ipad_ua_shows():
    assert _hint(userAgent=IPAD) is True


def test_ipados_as_mac_shows():
    assert _hint(userAgent=MAC_UA, platform="MacIntel", maxTouchPoints=5) is True


def test_real_mac_hidden():
    assert _hint(userAgent=MAC_UA, platform="MacIntel", maxTouchPoints=0) is False


def test_banner_text_constant():
    assert _run("m.IOS_HINT_TEXT") == (
        "To enable push notifications on iOS, tap the Share button and select 'Add to Home Screen'"
    )
