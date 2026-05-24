# tests/test_response_helpers.py
"""Tests for the centralised JSONResponse error helpers."""
import pytest
from fastapi.responses import JSONResponse


def test_error_response_default_400():
    from services.response_helpers import error_response
    resp = error_response("bad input")
    assert resp.status_code == 400
    import json
    assert json.loads(resp.body) == {"error": "bad input"}


def test_error_response_custom_status():
    from services.response_helpers import error_response
    resp = error_response("teapot", status_code=418)
    assert resp.status_code == 418


def test_server_error_default_message():
    from services.response_helpers import server_error
    resp = server_error()
    assert resp.status_code == 500
    import json
    assert json.loads(resp.body) == {"error": "An internal error occurred."}


def test_unauthorized_returns_401():
    from services.response_helpers import unauthorized
    resp = unauthorized()
    assert resp.status_code == 401
    import json
    assert json.loads(resp.body) == {"error": "Unauthorized."}


def test_not_found_returns_404():
    from services.response_helpers import not_found
    resp = not_found("Player not found.")
    assert resp.status_code == 404
    import json
    assert json.loads(resp.body) == {"error": "Player not found."}
