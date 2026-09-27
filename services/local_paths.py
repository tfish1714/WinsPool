"""Single source of truth for where the local pickle/JSON cache lives.

Production and dev behavior is unchanged: with no redirect configured,
local_db_dir() returns exactly pathlib.Path(".local_db") (cwd relative).

The test suite (tests/conftest.py) copies the developer's real
<repo>/.local_db to a temp directory and sets WINSPOOL_LOCAL_DB_REDIRECT to it,
so tests never modify the real folder. The redirect applies ONLY when the
cwd-relative ".local_db" would resolve to the real repo folder; a test that
chdir()s into a tmp dir and builds its own ".local_db" there is untouched.
"""
import os
import pathlib

REDIRECT_ENV = "WINSPOOL_LOCAL_DB_REDIRECT"
REAL_LOCAL_DB = pathlib.Path(__file__).resolve().parent.parent / ".local_db"


def local_db_dir() -> pathlib.Path:
    redirect = os.environ.get(REDIRECT_ENV)
    if not redirect:
        return pathlib.Path(".local_db")
    try:
        if (pathlib.Path.cwd() / ".local_db").resolve() == REAL_LOCAL_DB.resolve():
            return pathlib.Path(redirect)
    except OSError:
        pass
    return pathlib.Path(".local_db")
