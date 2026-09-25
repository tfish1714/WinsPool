"""Shared config for the prod performance scripts (scripts/perf/)."""
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

# First .env found walking up from here (a git worktree has none of its own; the
# main checkout's sits a few levels up). Real env vars are never overridden.
for _d in Path(__file__).resolve().parents:
    if (_d / ".env").exists():
        load_dotenv(_d / ".env")
        break

DEFAULT_URL = "https://winspool-1045965963135.us-east1.run.app"
HISTORY = Path(__file__).resolve().parents[2] / "reports" / "perf_history.jsonl"


def base_url(arg):
    return (arg or os.environ.get("WINSPOOL_URL") or DEFAULT_URL).rstrip("/")


_LOGIN_DATA = {}


def login_data():
    """The /api/login JSON body from the last password login ({} otherwise). The
    browser script seeds the SPA's localStorage from it -- the client tracks
    login state there, not just in the cookie."""
    return _LOGIN_DATA


def login(session, url):
    """Return a session_token cookie value, or None for anonymous runs.

    Auth options (all optional; without them only the public login shell is
    measured, which is NOT what a logged-in user waits on):
      WINSPOOL_SESSION_TOKEN              copy the `session_token` cookie from DevTools
      WINSPOOL_EMAIL + WINSPOOL_PASSWORD  password login (fails if the account has MFA)
      default: seeded e2e test player (E2E_TEST_PLAYER_PASSWORD from .env); WINSPOOL_ANON=1 disables
    """
    token = os.environ.get("WINSPOOL_SESSION_TOKEN")
    if token:
        return token
    email, pw = os.environ.get("WINSPOOL_EMAIL"), os.environ.get("WINSPOOL_PASSWORD")
    if not (email and pw) and os.environ.get("E2E_TEST_PLAYER_PASSWORD") and not os.environ.get("WINSPOOL_ANON"):
        # Seeded prod e2e account #2 (a non-admin, like real users) -- see scripts/seed_e2e_test_players.py
        email, pw = "e2e-test-02@winspool.internal", os.environ["E2E_TEST_PLAYER_PASSWORD"]
        print(f"(logging in as {email}; set WINSPOOL_ANON=1 for anonymous)")
    if not (email and pw):
        return None
    r = session.post(f"{url}/api/login", json={"email": email, "password": pw}, timeout=60)
    if r.status_code != 200:
        raise SystemExit(f"Login failed ({r.status_code}): {r.text[:200]}")
    _LOGIN_DATA.update(r.json())
    tok = r.cookies.get("session_token") or _LOGIN_DATA.get("token")
    if not tok:
        raise SystemExit("Login succeeded but no session token found (MFA account?). "
                         "Use WINSPOOL_SESSION_TOKEN instead.")
    return tok


def append_history(kind, url, payload):
    HISTORY.parent.mkdir(exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "url": url, **payload}
    with HISTORY.open("a") as f:
        f.write(json.dumps(rec) + "\n")
