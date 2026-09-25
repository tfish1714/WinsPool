"""Content-hash cache busting + Cache-Control for /static (services/static_assets.py)."""
import json
import re

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app
    return TestClient(app)


def test_static_url_hashes_file_contents(tmp_path, monkeypatch):
    from services import static_assets as sa
    (tmp_path / "a.js").write_text("one")
    monkeypatch.setattr(sa, "STATIC_PATH", tmp_path)
    first = sa.static_url("a.js")
    assert re.fullmatch(r"/static/a\.js\?v=[0-9a-f]{10}", first)
    (tmp_path / "a.js").write_text("two, longer")  # size changes -> cache invalidates
    assert sa.static_url("a.js") != first
    assert sa.static_url("missing.js") == "/static/missing.js"


def test_import_map_covers_modules_and_is_served_immutable(client):
    html = client.get("/mock-draft").text
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html).group(1))["imports"]
    assert "/static/js/api.js" in imports
    assert imports["/static/js/api.js"].startswith("/static/js/api.js?v=")
    r = client.get(imports["/static/js/api.js"])
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    # The importmap must precede the first module script, or browsers ignore it.
    assert html.index('type="importmap"') < html.index('type="module"')


def test_unversioned_static_revalidates_and_gzip_applies(client):
    r = client.get("/static/js/api.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-encoding"] == "gzip"
    assert client.get("/static/js/api.js", headers={"If-None-Match": r.headers["etag"]}).status_code == 304


def test_js_sources_have_no_versioned_relative_imports():
    """A `./x.js?v=N` specifier bypasses the import map and loads a second copy of the module."""
    import pathlib
    for f in pathlib.Path("static/js").glob("*.js"):
        assert not re.search(r"from\s+'\./[\w]+\.js\?", f.read_text(encoding="utf-8")), f.name
