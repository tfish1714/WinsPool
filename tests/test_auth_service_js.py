"""auth_service.js: STORAGE_KEYS and getAuthHeaders() contract + node behavior."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "static" / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_NODE = r"""
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync(process.argv[1], 'utf8')
  .replace(/export const/g, 'const').replace(/export function/g, 'function');
src += '\n;globalThis.__out = { STORAGE_KEYS, getAuthHeaders, AuthService };';
const mode = process.argv[2];
const store = mode === 'token' ? { nfl_wins_token: 'abc' } : {};
const ls = mode === 'throws'
  ? { getItem() { throw new Error('blocked'); } }
  : { getItem: (k) => (k in store ? store[k] : null) };
const ctx = { localStorage: ls, console, fetch: () => {} };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const o = ctx.__out;
console.log(JSON.stringify({ keys: o.STORAGE_KEYS, headers: o.getAuthHeaders(), viaService: o.AuthService.getAuthHeaders() }));
"""


def _run(mode):
    out = subprocess.run(["node", "-e", _NODE, str(JS / "auth_service.js"), mode],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@needs_node
def test_storage_keys_values():
    assert _run("none")["keys"] == {
        "TOKEN": "nfl_wins_token",
        "PLAYER_ID": "nfl_wins_my_player_id",
        "ROLE": "nfl_wins_role",
        "DRAFT_ACTIVE": "nfl_wins_draft_active",
    }


@needs_node
def test_auth_headers_attach_bearer_token():
    r = _run("token")
    assert r["headers"] == {"Authorization": "Bearer abc"}
    assert r["viaService"] == r["headers"]


@needs_node
def test_auth_headers_empty_without_token_and_when_storage_throws():
    assert _run("none")["headers"] == {}
    assert _run("throws")["headers"] == {}


@pytest.mark.parametrize("name", [
    "admin_accuracy.js", "admin_pool.js", "pool_fee.js", "schedule_explain.js", "api.js",
])
def test_no_inline_bearer_construction_outside_auth_service(name):
    src = (JS / name).read_text(encoding="utf-8")
    assert "'Authorization'" not in src
    assert "nfl_wins_token" not in src


def test_pool_fee_loaded_as_module():
    html = (ROOT / "templates" / "wins_pool.html").read_text(encoding="utf-8")
    assert re.search(r'<script type="module" src="[^"]*pool_fee\.js', html)
