"""Light/dark theme: token contract, WCAG AA contrast, and node-run init/toggle behavior."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _light_tokens():
    css = _read("static/style.css")
    m = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\n\}', css, re.S)
    assert m, 'static/style.css must define :root[data-theme="light"]'
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


def _lum(hex_color):
    h = hex_color.strip().lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_storage_key_constant():
    assert "THEME: 'nfl_wins_theme'" in _read("static/js/auth_service.js")


def test_light_block_overrides_required_token_families():
    tokens = _light_tokens()
    for name in ("--bg", "--bg-elev", "--bg-elev-2", "--line", "--line-strong",
                 "--ink", "--ink-2", "--ink-3", "--glass-bg"):
        assert name in tokens, f"{name} missing from light theme"
    assert "color-scheme" in _read("static/style.css")


@pytest.mark.parametrize("fg", ["--ink", "--ink-2", "--ink-3", "--link", "--pos", "--neg", "--warn", "--leader"])
@pytest.mark.parametrize("bg", ["--bg", "--bg-elev"])
def test_light_text_tokens_meet_wcag_aa(fg, bg):
    t = _light_tokens()
    assert _ratio(t[fg], t[bg]) >= 4.5, f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}"


def test_base_loads_classic_theme_script_in_head():
    html = _read("templates/base.html")
    head = html[: html.index("</head>")]
    assert re.search(r'<script(?![^>]*type="module")[^>]*theme_init\.js', head), \
        "theme_init.js must be a classic script in <head>"


def test_main_js_uses_theme_storage_key_and_wires_toggles():
    js = _read("static/js/main.js")
    assert "STORAGE_KEYS.THEME" in js
    assert "data-theme-toggle" in js
    assert "'storage'" in js


def test_clear_credentials_keeps_theme():
    src = _read("static/js/auth_service.js")
    body = re.search(r"clearCredentials\(\) \{(.*?)\n    \},", src, re.S).group(1)
    assert "localStorage.clear()" in body
    assert "STORAGE_KEYS.THEME" in body


@needs_node
def test_guard_sign_out_keeps_theme_and_clears_the_rest():
    from test_auth_guard import _run, LOGGED_IN, DEAD
    r = _run({"storage": {**LOGGED_IN, "nfl_wins_theme": "light"}, "responses": {"*": DEAD},
              "requests": ["/api/profile"]})
    assert r["storage"] == {"nfl_wins_theme": "light"}
    r = _run({"storage": dict(LOGGED_IN), "responses": {"*": DEAD}, "requests": ["/api/profile"]})
    assert r["storage"] == {}


_HARNESS = """
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');  // argv[1] is this script
function run({stored, light, storageThrows, noMatchMedia}) {
  const attrs = {};
  const store = stored === undefined ? {} : {nfl_wins_theme: stored};
  const window = {
    localStorage: {
      getItem(k) { if (storageThrows) throw new Error('blocked'); return k in store ? store[k] : null; },
      setItem(k, v) { if (storageThrows) throw new Error('blocked'); store[k] = v; },
    },
  };
  if (!noMatchMedia) window.matchMedia = () => ({ matches: !!light });
  const document = { documentElement: {
    setAttribute(k, v) { attrs[k] = v; }, getAttribute(k) { return k in attrs ? attrs[k] : null; } } };
  vm.runInNewContext(src, { window, document });
  return { window, attrs, store };
}
const out = {};
out.unsetLight = run({light: true}).attrs['data-theme'];
out.unsetDark = run({light: false}).attrs['data-theme'];
out.storedBeatsSystem = run({stored: 'dark', light: true}).attrs['data-theme'];
out.invalidStored = run({stored: 'purple', light: true}).attrs['data-theme'];
out.noMatchMedia = run({noMatchMedia: true}).attrs['data-theme'];
out.storageBlocked = run({storageThrows: true, light: true}).attrs['data-theme'];
const r = run({stored: 'dark'});
const first = r.window.WinsPoolTheme.toggle();
out.toggleOnce = [first, r.attrs['data-theme'], r.store.nfl_wins_theme];
out.toggleTwice = [r.window.WinsPoolTheme.toggle(), r.store.nfl_wins_theme];
const b = run({storageThrows: true});
out.toggleWithBlockedStorage = b.window.WinsPoolTheme.toggle();
out.key = r.window.WinsPoolTheme.KEY;
console.log(JSON.stringify(out));
"""


@needs_node
def test_theme_init_behavior(tmp_path):
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(harness), str(ROOT / "static/js/theme_init.js")],
                         capture_output=True, text=True, check=True)
    got = json.loads(res.stdout)
    assert got["unsetLight"] == "light"
    assert got["unsetDark"] == "dark"
    assert got["storedBeatsSystem"] == "dark"
    assert got["invalidStored"] == "light"        # invalid value ignored, system preference used
    assert got["noMatchMedia"] == "dark"
    assert got["storageBlocked"] == "light"       # still renders when storage throws
    assert got["toggleOnce"] == ["light", "light", "light"]
    assert got["toggleTwice"] == ["dark", "dark"]
    assert got["toggleWithBlockedStorage"] in ("light", "dark")
    assert got["key"] == "nfl_wins_theme"


@pytest.mark.parametrize("rel,pattern", [
    ("templates/schedule.html", r"color:\s*#fff"),
    ("templates/schedule.html", r"rgba\(255,\s*255,\s*255,\s*0\.1\)"),
    ("templates/admin.html", r"color:\s*#fff"),
    ("static/js/admin_elo.js", r"style\.color\s*=\s*'rgba\(255,255,255"),
    ("static/js/admin_elo.js", r"(?m)^\s*color:\s*rgba\(255,\s*255,\s*255"),
    ("static/js/admin_elo.js", r"color:rgba\(255,255,255"),
])
def test_no_hardcoded_white_text_that_vanishes_on_light_cards(rel, pattern):
    assert not re.search(pattern, _read(rel)), f"{rel} still hard-codes white ({pattern}); use a token"


def test_progress_controls_select_uses_tokens():
    css = _read("static/style.css")
    block = re.search(r"\.progress-controls select\s*\{([^}]*)\}", css).group(1)
    assert "#fff" not in block and "rgba(0, 0, 0" not in block


def test_theme_toggles_are_wired_before_app_init():
    js = _read("static/js/main.js")
    assert js.count("wireThemeToggles();") == 1
    assert js.index("wireThemeToggles();") < js.index("window.App.init();"),         "an exception in App.init() must not leave the theme buttons unwired"


# ---------------------------------------------------------------------------
# Top-bar icon toggle and admin surfaces
# ---------------------------------------------------------------------------

def test_theme_toggle_is_an_icon_button_in_both_top_bars():
    html = _read("templates/base.html")
    rail = html[html.index('id="app-nav"'): html.index('id="app-nav-mobile"')]
    mobile = html[html.index('id="app-nav-mobile"'): html.index('id="nav-drawer-overlay"')]
    for bar in (rail, mobile):
        assert "data-theme-toggle" in bar
        assert "theme-icon-sun" in bar and "theme-icon-moon" in bar
        assert "aria-label" in bar
    # Moved out of the places the text buttons used to live.
    assert "data-theme-toggle" not in _read("templates/player_profile.html")
    assert "data-theme-toggle" not in html[html.index('id="nav-drawer"'):]


def test_theme_icons_swap_with_the_theme():
    css = _read("static/style.css")
    assert re.search(r"\.theme-icon-moon\s*\{[^}]*display:\s*none", css)
    assert re.search(r':root\[data-theme="light"\]\s+\.theme-icon-sun\s*\{[^}]*display:\s*none', css)
    assert re.search(r':root\[data-theme="light"\]\s+\.theme-icon-moon\s*\{[^}]*display:\s*(inline-block|block|inline)', css)


def test_main_js_labels_toggles_with_aria_label_not_text():
    js = _read("static/js/main.js")
    body = js[js.index("function wireThemeToggles()"):]
    body = body[: body.index("\n}\n") + 3]
    assert "aria-label" in body and "Switch to " in body
    assert "textContent" not in body  # icon-only buttons keep their SVG children


def test_admin_surface_tokens_exist_in_both_themes():
    css = _read("static/style.css")
    light = _light_tokens()
    for name in ("--surface-sunken", "--surface-sunken-strong", "--surface-sunken-soft",
                 "--surface-hover", "--toggle-off"):
        assert name in light, f"{name} missing from the light theme"
        assert len(re.findall(rf"{name}\s*:", css)) >= 2, f"{name} needs a dark default and a light override"


@pytest.mark.parametrize("rel", ["templates/admin.html", "static/js/admin_main.js"])
def test_admin_uses_tokens_not_dark_theme_tints(rel):
    src = _read(rel)
    assert "rgba(255,255,255" not in src.replace(" ", ""), f"{rel} still hard-codes a white tint"
    assert not re.search(r"background:\s*rgba\(0,\s*0,\s*0,\s*0\.(15|2|3)\)", src), \
        f"{rel} still hard-codes a dark panel background"
