"""Charts follow the theme: Chart.js draws to a canvas, so colors must be read from CSS tokens
at render time and repainted on theme change (static/js/chart_theme.js)."""
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


def test_base_loads_chart_theme_before_chart_pages():
    html = _read("templates/base.html")
    assert "js/chart_theme.js" in html
    assert html.index("js/theme_init.js") < html.index("js/chart_theme.js")


@pytest.mark.parametrize("rel", ["static/js/standings.js", "static/js/admin_elo.js"])
def test_chart_pages_use_theme_colors_not_white_literals(rel):
    src = _read(rel)
    # Chart.js option colors (the legend's inline styles are covered by the token sweep test).
    assert not re.search(r"[cC]olor:\s*'rgba\(255,\s*255,\s*255", src), f"{rel} hard-codes white chart colors"
    assert "WinsPoolChartTheme" in src


def test_theme_init_announces_theme_changes():
    src = _read("static/js/theme_init.js")
    assert "wins-theme-change" in src


_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
function build(vars) {
  const listeners = {};
  const window = {
    addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener(t, fn) { listeners[t] = (listeners[t] || []).filter(f => f !== fn); },
    getComputedStyle() { return { getPropertyValue: (k) => (k in vars ? ' ' + vars[k] + ' ' : '') }; },
  };
  const document = { documentElement: {} };
  vm.runInNewContext(src, { window, document });
  return { window, listeners };
}
const LIGHT = {'--ink-2': '#414854', '--ink-3': '#5a616c', '--ink': '#14171c', '--line': 'rgba(15,23,42,0.10)',
               '--bg-elev-2': '#eceef2', '--line-strong': 'rgba(15,23,42,0.20)'};
const out = {};
let { window, listeners } = build(LIGHT);
const T = window.WinsPoolChartTheme;
out.colors = T.colors();

function fakeChart() {
  return { canvas: {}, updates: [], options: {
      plugins: { tooltip: {}, title: { display: true } },
      scales: { x: { ticks: {}, grid: {} }, y: { ticks: {}, grid: {}, title: { display: true } } } },
    update(mode) { this.updates.push(mode); } };
}
const c1 = fakeChart();
T.paint(c1);
out.painted = { tick: c1.options.scales.x.ticks.color, grid: c1.options.scales.y.grid.color,
                yTitle: c1.options.scales.y.title.color, tipBg: c1.options.plugins.tooltip.backgroundColor,
                tipBorder: c1.options.plugins.tooltip.borderColor, title: c1.options.plugins.title.color };

// tracking: repaint + update on theme change, using the NEW colors
const vars2 = Object.assign({}, LIGHT);
({ window, listeners } = build(vars2));
const T2 = window.WinsPoolChartTheme;
const c2 = fakeChart();
const baseline = (listeners['wins-theme-change'] || []).length;
T2.paint(c2); T2.track(c2);
vars2['--ink-2'] = '#e8eaef';
listeners['wins-theme-change'].forEach(fn => fn());
out.afterChange = { tick: c2.options.scales.x.ticks.color, updates: c2.updates.length };

// a destroyed chart is ignored and unsubscribed
c2.canvas = null;
listeners['wins-theme-change'].forEach(fn => fn());
out.afterDestroy = { updates: c2.updates.length, remaining: (listeners['wins-theme-change'] || []).length - baseline };

// every live Chart.js instance repaints on a theme change, without per-chart wiring
const vars3 = Object.assign({}, LIGHT);
({ window, listeners } = build(vars3));
const live = fakeChart(), dead = fakeChart(); dead.canvas = null;
window.Chart = { instances: { 1: live, 2: dead } };
vars3['--ink-2'] = '#e8eaef';
listeners['wins-theme-change'].forEach(fn => fn());
out.global = { tick: live.options.scales.x.ticks.color, updates: live.updates.length, deadUpdates: dead.updates.length };

// missing variables fall back to dark-theme defaults instead of undefined
({ window } = build({}));
out.fallback = window.WinsPoolChartTheme.colors();
console.log(JSON.stringify(out));
"""


@needs_node
def test_chart_theme_behavior(tmp_path):
    h = tmp_path / "h.js"
    h.write_text(_HARNESS, encoding="utf-8")
    res = subprocess.run(["node", str(h), str(ROOT / "static/js/chart_theme.js")],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)
    assert got["colors"]["text"] == "#414854"          # --ink-2, trimmed
    assert got["colors"]["muted"] == "#5a616c"
    assert got["colors"]["tipBg"] == "#eceef2"
    assert got["painted"]["tick"] == "#414854"
    assert got["painted"]["grid"] == "rgba(15,23,42,0.10)"
    assert got["painted"]["yTitle"] == "#5a616c"
    assert got["painted"]["tipBg"] == "#eceef2" and got["painted"]["tipBorder"] == "rgba(15,23,42,0.20)"
    assert got["painted"]["title"] == "#414854"
    assert got["afterChange"] == {"tick": "#e8eaef", "updates": 1}
    assert got["afterDestroy"] == {"updates": 1, "remaining": 0}
    assert got["global"] == {"tick": "#e8eaef", "updates": 1, "deadUpdates": 0}
    for key in ("text", "muted", "grid", "tipBg", "tipTitle", "tipBody", "tipBorder"):
        assert got["fallback"][key], f"{key} must have a fallback"
