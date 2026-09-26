"""tests_e2e/conftest.py — Playwright browser-driven test harness.

Launches a real `uvicorn main:app` subprocess against the developer's local
.local_db/ (USE_LOCAL_DATA=true) with DISABLE_OUTBOUND_EMAIL=true, so these
tests drive the actual app through a real browser rather than mocking
anything at the Python level.
"""
import collections
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
from playwright.sync_api import sync_playwright

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Cap on how many trailing lines of subprocess output we keep around for
# startup-failure diagnostics (see the drain thread in live_server below).
_OUTPUT_TAIL_LINES = 200


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["USE_LOCAL_DATA"] = "true"
    env["DISABLE_OUTBOUND_EMAIL"] = "true"
    # The suite logs in dozens of times a minute from one address; the
    # production auth rate limit (5/min per IP) would 429 unrelated tests.
    env["AUTH_RATE_LIMIT_PER_MINUTE"] = "100000"
    env["AUTH_LOOKUP_RATE_LIMIT_PER_MINUTE"] = "100000"
    env["PORT"] = str(port)
    env.setdefault("JWT_SECRET", "e2e-test-jwt-secret-not-for-production")

    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Continuously drain proc.stdout in a background thread for the whole
    # life of this session-scoped fixture. uvicorn defaults to access_log=True
    # (one line per HTTP request), and an undrained pipe's small OS buffer
    # (particularly on Windows) fills over a real test session; once the
    # child's write() to stdout blocks, its single-process asyncio event loop
    # stalls and every subsequent request hangs, not just logging. We keep
    # only the trailing lines so the startup-failure branch below can still
    # report something useful without an unbounded buffer.
    output_tail = collections.deque(maxlen=_OUTPUT_TAIL_LINES)

    def _drain_output():
        try:
            for raw_line in proc.stdout:
                output_tail.append(raw_line.decode(errors="replace"))
        except Exception:
            pass

    drain_thread = threading.Thread(target=_drain_output, daemon=True)
    drain_thread.start()

    deadline = time.time() + 20
    ready = False
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                ready = True
                break
        except OSError:
            time.sleep(0.3)

    if not ready:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        out = "".join(output_tail)
        raise RuntimeError(f"live_server failed to start within 20s.\n{out}")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    pg = context.new_page()
    yield pg
    context.close()


@pytest.fixture(scope="session")
def test_player_credentials():
    ids_raw = os.environ.get("E2E_TEST_PLAYER_IDS", "")
    password = os.environ.get("E2E_TEST_PLAYER_PASSWORD", "")
    if not ids_raw or not password:
        pytest.skip("E2E_TEST_PLAYER_IDS / E2E_TEST_PLAYER_PASSWORD not set — run scripts/seed_e2e_test_players.py first")

    ids = [int(x) for x in ids_raw.split(",") if x.strip()]
    return [
        {
            "id": pid,
            "email": f"e2e-test-{i+1:02d}@winspool.internal",
            "password": password,
            "role": "admin" if i == 0 else "user",
        }
        for i, pid in enumerate(ids)
    ]


@pytest.fixture(scope="session")
def lifecycle_test_accounts():
    """The 4 dedicated single-purpose accounts from Task 1 of the
    auth-lifecycle plan. Each dict also carries the shared base password
    (same one test_player_credentials uses) so tests that need to log in
    with a known-good password can."""
    password = os.environ.get("E2E_TEST_PLAYER_PASSWORD", "")
    ids = {
        "claim": os.environ.get("E2E_CLAIM_TEST_PLAYER_ID"),
        "mfa": os.environ.get("E2E_MFA_TEST_PLAYER_ID"),
        "lockout": os.environ.get("E2E_LOCKOUT_TEST_PLAYER_ID"),
        "tempword": os.environ.get("E2E_TEMPWORD_TEST_PLAYER_ID"),
    }
    if not all(ids.values()) or not password:
        pytest.skip("E2E_*_TEST_PLAYER_ID / E2E_TEST_PLAYER_PASSWORD not set — run Task 1's seed script extension first")

    return {
        "claim": {"id": int(ids["claim"]), "email": "e2e-test-11-claim@winspool.internal", "password": password},
        "mfa": {"id": int(ids["mfa"]), "email": "e2e-test-12-mfa@winspool.internal", "password": password},
        "lockout": {"id": int(ids["lockout"]), "email": "e2e-test-13-lockout@winspool.internal", "password": password},
        "tempword": {"id": int(ids["tempword"]), "email": "e2e-test-14-tempword@winspool.internal", "password": password},
    }
