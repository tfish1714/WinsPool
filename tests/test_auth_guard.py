"""Client auth guard: contract tests plus behavioral tests run under node.

static/js/auth_guard.js wraps window.fetch and signs the user out once when the
server rejects the session token. The behavioral tests execute the real file in
a node vm with a stubbed window/localStorage/fetch.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "static" / "js" / "auth_guard.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _src(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Markup / source contract
# --------------------------------------------------------------------------

def test_base_loads_guard_as_classic_script_before_main():
    html = _src("templates/base.html")
    guard = re.search(r'<script(?![^>]*type="module")[^>]*auth_guard\.js', html)
    assert guard, "auth_guard.js must be a classic (non-module) script"
    assert guard.start() < html.index("js/main.js")


def test_guard_source_contract():
    src = GUARD.read_text(encoding="utf-8")
    assert "window.fetch" in src
    assert "status !== 401" in src or "status === 401" in src
    assert ".clone()" in src
    assert "/api/" in src
    for ep in ("/api/login", "/api/mfa/verify", "/api/set_password",
               "/api/check_player", "/api/profile/update", "/api/logout"):
        assert ep in src
    for phrase in ("session expired", "invalid session token", "no longer valid",
                   "missing or invalid authorization header"):
        assert phrase in src.lower()
    assert "X-Session-State" in src
    assert "nfl_wins_token" in src and "nfl_wins_my_player_id" in src
    assert "localStorage.clear" in src


def test_api_js_error_includes_status_and_detail():
    src = _src("static/js/api.js")
    assert "response.status" in src
    assert "err.detail" in src
    assert "err.error" in src
    assert "Unknown API Error" not in src


def test_admin_error_helper_defined_and_uses_detail():
    src = _src("static/js/admin_main.js")
    assert "adminHttpError" in src
    body = src[src.index("adminHttpError"):]
    assert "detail" in body[:800] and "status" in body[:800]


@pytest.mark.parametrize("rel", [
    "static/js/admin_accuracy.js", "static/js/admin_pool.js", "static/js/admin_main.js",
])
def test_admin_scripts_do_not_throw_bare_http_status(rel):
    src = _src(rel)
    assert not re.search(r"new Error\((`HTTP \$\{[a-z.]+\}`|'HTTP ' \+ [a-z.]+)\)", src)


# --------------------------------------------------------------------------
# Server side: machine-readable dead-session header
# --------------------------------------------------------------------------

def test_dead_session_401s_carry_x_session_state(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-guard-header")
    from services.session_service import require_auth, require_admin
    for dep in (require_auth, require_admin):
        with pytest.raises(HTTPException) as e:
            dep(authorization=None, session_token=None)
        assert e.value.headers["X-Session-State"] == "missing"
        with pytest.raises(HTTPException) as e:
            dep(authorization="Bearer garbage", session_token=None)
        assert e.value.headers["X-Session-State"] == "invalid"


def test_expired_and_revoked_carry_header(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-guard-header")
    import time
    import jwt
    from services import session_service
    from services.session_service import require_auth
    expired = jwt.encode({"sub": "1", "role": "user", "exp": int(time.time()) - 10},
                         "test-secret-guard-header", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=f"Bearer {expired}", session_token=None)
    assert e.value.headers["X-Session-State"] == "expired"

    tok = session_service.create_token(player_id=1, role="user")
    monkeypatch.setattr(session_service, "_payload_is_current", lambda p: False)
    with pytest.raises(HTTPException) as e:
        require_auth(authorization=f"Bearer {tok}", session_token=None)
    assert e.value.headers["X-Session-State"] == "revoked"


def test_admin_403_has_no_session_state_header(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-guard-header")
    from services import session_service
    from services.session_service import require_admin
    tok = session_service.create_token(player_id=1, role="user")
    with pytest.raises(HTTPException) as e:
        require_admin(authorization=f"Bearer {tok}", session_token=None)
    assert e.value.status_code == 403
    assert not (e.value.headers or {}).get("X-Session-State")


# --------------------------------------------------------------------------
# Behavior: run the real guard in node
# --------------------------------------------------------------------------

_HARNESS = """
const vm = require('vm'), fs = require('fs');
const scenario = JSON.parse(process.argv[1]);
const store = Object.assign({}, scenario.storage || {});
const calls = [];
const redirects = [];
function mkResp(status, headers, body) {
  const h = headers || {};
  return {
    status, ok: status < 400,
    headers: { get: (k) => (h[k.toLowerCase()] !== undefined ? h[k.toLowerCase()] : null) },
    clone() { return mkResp(status, headers, body); },
    async json() { if (body === undefined) throw new Error('no body'); return JSON.parse(body); },
    async text() { return body || ''; },
  };
}
const window = {
  location: { origin: 'https://app.test', href: 'https://app.test/admin',
    assign(u) { redirects.push(u); }, reload() { redirects.push('reload'); } },
};
window.fetch = function (input, init) {
  const url = typeof input === 'string' ? input : (input && input.url);
  calls.push(url);
  if (String(url).endsWith('/api/logout')) return Promise.resolve(mkResp(200, {}, '{}'));
  const r = scenario.responses[url] || scenario.responses['*'];
  if (r.reject) return Promise.reject(new Error('net'));
  return Promise.resolve(mkResp(r.status, r.headers, r.body));
};
const origFetchRef = window.fetch;
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
  clear: () => { for (const k of Object.keys(store)) delete store[k]; },
};
window.localStorage = localStorage;
const ctx = vm.createContext({ window, localStorage, console, URL, Promise, setTimeout, clearTimeout });
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
(async () => {
  const out = [];
  for (const u of scenario.requests) {
    try {
      const r = await window.fetch(u);
      out.push(r.status);
    } catch (e) { out.push('err:' + e.message); }
  }
  await new Promise((r) => setTimeout(r, 100));
  console.log(JSON.stringify({ out, storage: store, redirects,
    logoutCalls: calls.filter((c) => String(c).endsWith('/api/logout')).length,
    wrapped: window.fetch !== origFetchRef }));
  process.exit(0);
})();
"""


def _run(scenario):
    proc = subprocess.run(
        ["node", "-e", _HARNESS, json.dumps(scenario), str(GUARD)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


LOGGED_IN = {"nfl_wins_token": "t", "nfl_wins_my_player_id": "5",
             "nfl_wins_playerName": "X", "nfl_wins_role": "admin"}
DEAD = {"status": 401, "headers": {"x-session-state": "expired"},
        "body": '{"detail":"Session expired. Please log in again."}'}


@needs_node
def test_dead_session_signs_out_once_for_concurrent_401s():
    r = _run({"storage": LOGGED_IN, "responses": {"*": DEAD},
              "requests": ["/api/admin/players", "/api/admin/seasons", "/api/profile"]})
    assert r["wrapped"] is True
    assert r["out"] == [401, 401, 401]
    assert r["storage"] == {}
    assert r["logoutCalls"] == 1
    assert len(r["redirects"]) == 1


@needs_node
@pytest.mark.parametrize("detail", [
    "Session expired. Please log in again.", "Invalid session token.",
    "Missing or invalid Authorization header.",
    "Session is no longer valid. Please log in again.",
])
def test_detail_text_matching_when_header_absent(detail):
    r = _run({"storage": LOGGED_IN,
              "responses": {"*": {"status": 401, "body": json.dumps({"detail": detail})}},
              "requests": ["/api/profile"]})
    assert r["storage"] == {}
    assert len(r["redirects"]) == 1


@needs_node
@pytest.mark.parametrize("path", ["/api/login", "/api/mfa/verify", "/api/set_password",
                                  "/api/check_player?email=a", "/api/profile/update",
                                  "/api/logout"])
def test_excluded_endpoints_never_sign_out(path):
    r = _run({"storage": LOGGED_IN, "responses": {"*": DEAD}, "requests": [path]})
    assert r["storage"] == LOGGED_IN
    assert r["redirects"] == []


@needs_node
def test_wrong_credentials_401_does_not_sign_out():
    r = _run({"storage": LOGGED_IN,
              "responses": {"*": {"status": 401, "body": '{"error":"Incorrect password."}'}},
              "requests": ["/api/some/other"]})
    assert r["storage"] == LOGGED_IN and r["redirects"] == []


@needs_node
def test_no_saved_login_never_redirects():
    r = _run({"storage": {}, "responses": {"*": DEAD}, "requests": ["/api/standings"]})
    assert r["redirects"] == [] and r["logoutCalls"] == 0


@needs_node
def test_non_api_cross_origin_and_non_401_untouched():
    r = _run({"storage": LOGGED_IN, "responses": {"*": DEAD},
              "requests": ["/static/x.js", "https://other.test/api/x", "//other.test/api/x"]})
    assert r["storage"] == LOGGED_IN and r["redirects"] == []
    r = _run({"storage": LOGGED_IN,
              "responses": {"*": {"status": 500, "body": "{}"}}, "requests": ["/api/x"]})
    assert r["out"] == [500] and r["storage"] == LOGGED_IN


@needs_node
def test_network_error_is_rethrown():
    r = _run({"storage": LOGGED_IN, "responses": {"*": {"reject": True}},
              "requests": ["/api/x"]})
    assert r["out"] == ["err:net"] and r["storage"] == LOGGED_IN and r["redirects"] == []


@needs_node
def test_unrelated_401_detail_mentioning_expired_is_not_a_dead_session():
    r = _run({"storage": LOGGED_IN,
              "responses": {"*": {"status": 401, "body": '{"detail":"MFA code expired or invalid."}'}},
              "requests": ["/api/some/other"]})
    assert r["storage"] == LOGGED_IN and r["redirects"] == []


@needs_node
def test_503_session_check_unavailable_never_signs_out():
    r = _run({"storage": LOGGED_IN,
              "responses": {"*": {"status": 503, "body": '{"detail":"Session check temporarily unavailable."}'}},
              "requests": ["/api/admin/players"]})
    assert r["out"] == [503] and r["storage"] == LOGGED_IN and r["redirects"] == []


def test_guard_source_only_acts_on_401():
    src = GUARD.read_text(encoding="utf-8")
    assert "status !== 401" in src and "503" not in src.split("function inspect")[1].split("status !== 401")[0]


def test_lookup_exception_gives_503_without_session_state(monkeypatch):
    from services import session_service
    from services.session_service import require_auth, require_admin

    def boom(pid):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(session_service, "_lookup_player", boom)
    for dep, role in ((require_auth, "user"), (require_admin, "admin")):
        tok = session_service.create_token(player_id=1, role=role)
        with pytest.raises(HTTPException) as e:
            dep(authorization=f"Bearer {tok}", session_token=None)
        assert e.value.status_code == 503
        assert e.value.detail == "Session check temporarily unavailable."
        assert not (e.value.headers or {}).get("X-Session-State")
    assert session_service.get_is_admin(
        authorization=f"Bearer {session_service.create_token(1, 'admin')}", session_token=None) is False
    assert session_service.decode_current_token(session_service.create_token(1, "user")) is None


def test_unloaded_players_data_is_unavailable_not_revoked(monkeypatch):
    import pandas as pd
    from services import session_service, db_service
    monkeypatch.setattr(session_service, "_lookup_player", session_service._load_player_from_db)
    monkeypatch.setattr(db_service, "_get_players_df", lambda: pd.DataFrame())
    with pytest.raises(HTTPException) as e:
        session_service.require_auth(
            authorization=f"Bearer {session_service.create_token(1, 'user')}", session_token=None)
    assert e.value.status_code == 503


def test_real_revocation_still_401_revoked(monkeypatch):
    from services import session_service
    tok = session_service.create_token(player_id=1, role="user", token_version=0)
    monkeypatch.setattr(session_service, "_lookup_player", lambda pid: {"token_version": 3})
    with pytest.raises(HTTPException) as e:
        session_service.require_auth(authorization=f"Bearer {tok}", session_token=None)
    assert e.value.status_code == 401
    assert e.value.headers["X-Session-State"] == "revoked"
    monkeypatch.setattr(session_service, "_lookup_player", lambda pid: None)
    with pytest.raises(HTTPException) as e:
        session_service.require_admin(
            authorization=f"Bearer {session_service.create_token(1, 'admin')}", session_token=None)
    assert e.value.status_code == 401


@pytest.mark.parametrize("rel,needle", [
    ("static/js/admin_accuracy.js", "accuracy data: ${_esc(err.message)}"),
    ("static/js/admin_accuracy.js", "weekly snapshots: ${_esc(err.message)}"),
    ("static/js/admin_betting.js", "_errHtml(resp, err,"),
    ("static/js/admin_pattern_scanner.js", "_errHtml(resp, err,"),
    ("static/js/admin_elo.js", "_errHtml(resp, err,"),
])
def test_admin_error_text_is_escaped(rel, needle):
    src = _src(rel)
    assert needle in src
    assert "${err.detail" not in src and "${err.message}" not in src
    if "_errHtml" in needle:
        assert "JSON.stringify(raw)" in src and "&lt;" in src
