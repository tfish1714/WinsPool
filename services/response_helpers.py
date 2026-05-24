# services/response_helpers.py
"""Centralised JSONResponse helpers — eliminates repeated JSONResponse(content={"error":...}) patterns."""
from fastapi.responses import JSONResponse


def error_response(message: str, status_code: int = 400) -> JSONResponse:
    """Return a JSON error response with a custom status code (default 400)."""
    return JSONResponse(status_code=status_code, content={"error": message})


def server_error(message: str = "An internal error occurred.") -> JSONResponse:
    """Return a 500 Internal Server Error JSON response."""
    return JSONResponse(status_code=500, content={"error": message})


def unauthorized(message: str = "Unauthorized.") -> JSONResponse:
    """Return a 401 Unauthorized JSON response."""
    return JSONResponse(status_code=401, content={"error": message})


def not_found(message: str = "Not found.") -> JSONResponse:
    """Return a 404 Not Found JSON response."""
    return JSONResponse(status_code=404, content={"error": message})
