"""static/sw.js push + notificationclick handlers, evaluated in a node vm
with stubbed service-worker globals. Skips when node is not installed."""
import json
import pathlib
import shutil
import subprocess

import pytest

SW = pathlib.Path(__file__).resolve().parent.parent / "static" / "sw.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const cfg = JSON.parse(process.argv[1]);
const handlers = {}; const log = {shown: [], navigated: [], opened: [], focused: 0};
const mode = cfg.windows === true ? 'ok' : (cfg.windows || null);
const windows = !mode ? [] : mode === 'nonav' ? [{focus: () => Promise.resolve()}] : [{
  navigate: u => { log.navigated.push(u); return mode === 'navreject' ? Promise.reject(new TypeError('uncontrolled')) : Promise.resolve({}); },
  focus: () => { log.focused++; return mode === 'focusreject' ? Promise.reject(new Error('InvalidAccessError')) : Promise.resolve(); },
}];
const self = {
  addEventListener: (n, f) => { handlers[n] = f; },
  registration: {showNotification: (t, o) => { log.shown.push([t, o]); return Promise.resolve(); }},
  location: {origin: 'https://app.test'},
};
const clients = {
  matchAll: () => Promise.resolve(windows),
  openWindow: u => { log.opened.push(u); return Promise.resolve(); },
};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {self, clients, URL, console});
let pending = Promise.resolve();
const waitUntil = p => { pending = p; };
(async () => {
  if (cfg.kind === 'push') {
    handlers.push({data: {json: () => cfg.data}, waitUntil});
  } else {
    handlers.notificationclick({notification: {close() {}, data: cfg.data}, waitUntil});
  }
  await pending;
  console.log(JSON.stringify(log));
})();
"""


def _run(cfg):
    out = subprocess.run(
        ["node", "-e", _HARNESS, json.dumps(cfg), str(SW)],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=20,
    )
    return json.loads(out.stdout)


def test_push_passes_url_in_notification_data():
    log = _run({"kind": "push", "data": {"title": "T", "body": "B", "url": "/recap/2026/5"}})
    title, opts = log["shown"][0]
    assert title == "T" and opts["data"] == {"url": "/recap/2026/5"}


def test_click_with_existing_window_navigates():
    log = _run({"kind": "click", "windows": True, "data": {"url": "/recap/2026/5"}})
    assert log["navigated"] == ["/recap/2026/5"] and log["opened"] == [] and log["focused"] == 1


def test_click_without_window_opens_url():
    log = _run({"kind": "click", "windows": False, "data": {"url": "/recap/2026/5"}})
    assert log["opened"] == ["/recap/2026/5"]


def test_click_off_origin_opens_root():
    log = _run({"kind": "click", "windows": False, "data": {"url": "https://evil.test/x"}})
    assert log["opened"] == ["/"]


def test_click_without_url_opens_root():
    log = _run({"kind": "click", "windows": False, "data": None})
    assert log["opened"] == ["/"]


@pytest.mark.parametrize("mode", ["navreject", "focusreject", "nonav"])
def test_click_falls_back_to_open_window(mode):
    log = _run({"kind": "click", "windows": mode, "data": {"url": "/recap/2026/5"}})
    assert log["opened"] == ["/recap/2026/5"]


@pytest.mark.parametrize("bad", ["https://evil.test/x", "//evil.test/x", "javascript:alert(1)", "http://["])
def test_click_unsafe_urls_end_at_root(bad):
    log = _run({"kind": "click", "windows": False, "data": {"url": bad}})
    assert log["opened"] == ["/"]
