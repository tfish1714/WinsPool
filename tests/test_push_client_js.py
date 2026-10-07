"""static/js/push_client.js pure helpers, driven through Node (skipped without node)."""
import json
import pathlib
import shutil
import subprocess

import pytest

MODULE = pathlib.Path(__file__).resolve().parent.parent / "static" / "js" / "push_client.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"


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


def _state(**kw):
    args = {"userAgent": CHROME, "platform": "Win32", "maxTouchPoints": 0,
            "standalone": False, "hasServiceWorker": True, "hasPushManager": True}
    args.update(kw)
    return _run(f"m.pushSupportState({json.dumps(args)})")


def test_desktop_chrome_supported():
    assert _state() == "supported"


def test_iphone_in_browser_needs_install():
    assert _state(userAgent=IPHONE, platform="iPhone", hasPushManager=False) == "ios-needs-install"


def test_iphone_standalone_supported():
    assert _state(userAgent=IPHONE, platform="iPhone", standalone=True) == "supported"


def test_firefox_without_pushmanager_unsupported():
    assert _state(userAgent=FIREFOX, hasPushManager=False) == "unsupported"


def test_no_service_worker_unsupported():
    assert _state(hasServiceWorker=False) == "unsupported"


def _nudge(**kw):
    args = {"support": "supported", "subscribed": False, "permission": "default", "dismissed": False}
    args.update(kw)
    return _run(f"m.shouldShowPushNudge({json.dumps(args)})")


def test_nudge_truth_table():
    assert _nudge() is True
    assert _nudge(subscribed=True) is False
    assert _nudge(permission="denied") is False
    assert _nudge(dismissed=True) is False
    assert _nudge(support="unsupported") is False
    assert _nudge(support="ios-needs-install") is False
