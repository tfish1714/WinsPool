"""Global unhandled-exception handler in main.py (#67)."""
import asyncio
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main

EXPECTED_BODY = {"error": "An internal server error occurred."}


class _FakeURL:
    path = "/api/boom"


class _FakeRequest:
    method = "GET"
    url = _FakeURL()


def test_handler_returns_500_with_exact_body():
    resp = asyncio.run(main.global_exception_handler(_FakeRequest(), RuntimeError("x")))
    assert resp.status_code == 500
    import json
    assert json.loads(resp.body) == EXPECTED_BODY


@pytest.fixture
def boom_routes():
    async def _boom():
        raise RuntimeError("kaboom")

    async def _http_exc():
        raise HTTPException(status_code=418, detail="teapot")

    before = len(main.app.router.routes)
    main.app.add_api_route("/__test_boom", _boom)
    main.app.add_api_route("/__test_http_exc", _http_exc)
    yield
    del main.app.router.routes[before:]


def test_unhandled_route_exception_returns_500_and_logs(boom_routes, caplog):
    client = TestClient(main.app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/__test_boom")
    assert resp.status_code == 500
    assert resp.json() == EXPECTED_BODY
    assert any("/__test_boom" in r.getMessage() for r in caplog.records)


def test_http_exception_not_swallowed(boom_routes):
    client = TestClient(main.app, raise_server_exceptions=False)
    resp = client.get("/__test_http_exc")
    assert resp.status_code == 418
    assert resp.json() == {"detail": "teapot"}
