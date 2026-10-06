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


# --- Stubbed-environment tests (storage failures, dismissal, DOM rendering) ---

_PRELUDE = """
const store = {};
globalThis.__banners = [];
function mkEl(tag) {
  const el = { tag, attrs: {}, children: [], listeners: {}, className: '', textContent: '', removed: false,
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { this.listeners[t] = f; },
    append(...c) { this.children.push(...c); },
    remove() { this.removed = true; const i = globalThis.__banners.indexOf(this); if (i >= 0) globalThis.__banners.splice(i, 1); } };
  return el;
}
globalThis.document = {
  createElement: mkEl,
  querySelector(sel) { return globalThis.__banners.find(b => '.' + b.className === sel) || null; },
  body: { appendChild(el) { globalThis.__banners.push(el); } },
};
globalThis.window = { navigator: { userAgent: %s, platform: '', maxTouchPoints: 0, standalone: false } };
"""


def _run_env(body: str, storage: str = "ok"):
    """Run `body` (JS, may use `m`) with stubbed document/window/localStorage; print JSON of `out`."""
    storage_js = {
        "ok": "globalThis.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } };",
        "throws": "globalThis.localStorage = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };",
    }[storage]
    script = (
        (_PRELUDE % json.dumps(IPHONE)) + storage_js +
        f"const m = await import({json.dumps(MODULE.as_uri())});"
        f"let out; {body}; console.log(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def test_storage_throwing_does_not_raise_and_counts_as_not_dismissed():
    out = _run_env("out = { shown: m.maybeShowIosInstallHint() };", storage="throws")
    assert out["shown"] is True


def test_dismiss_with_throwing_storage_does_not_raise():
    out = _run_env(
        "m.maybeShowIosInstallHint(); const b = __banners[0]; b.children[1].listeners.click();"
        "out = { removed: b.removed };",
        storage="throws",
    )
    assert out["removed"] is True


def test_dismissal_persists_and_hides_hint():
    out = _run_env(
        "m.maybeShowIosInstallHint(); __banners[0].children[1].listeners.click();"
        "const stored = store[m.IOS_HINT_DISMISSED_KEY];"
        "out = { stored, show: m.shouldShowIosInstallHint({ userAgent: %s, dismissed: stored === '1' }) };"
        % json.dumps(IPHONE)
    )
    assert out == {"stored": "1", "show": False}


def test_already_dismissed_flag_suppresses_banner():
    out = _run_env(
        "store[m.IOS_HINT_DISMISSED_KEY] = '1';"
        "out = { shown: m.maybeShowIosInstallHint(), count: __banners.length };"
    )
    assert out == {"shown": False, "count": 0}


def test_banner_renders_text_dismiss_button_and_only_once():
    out = _run_env(
        "const a = m.renderIosInstallBanner(); const b = m.renderIosInstallBanner();"
        "out = { same: a === b, count: __banners.length, text: a.children[0].textContent,"
        " btnTag: a.children[1].tag, label: a.children[1].attrs['aria-label'], role: a.attrs.role };"
    )
    assert out["same"] is True
    assert out["count"] == 1
    assert out["text"] == (
        "To enable push notifications on iOS, tap the Share button and select 'Add to Home Screen'"
    )
    assert out["btnTag"] == "button"
    assert out["label"] == "Dismiss install hint"
    assert out["role"] == "status"
