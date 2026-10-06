"""base.html head tags + static/manifest.json (PWA / iOS add-to-home-screen)."""
import json
import pathlib

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"


def test_base_head_has_pwa_tags():
    html = client.get("/").text
    assert 'rel="manifest" href="/static/manifest.json"' in html
    assert 'rel="apple-touch-icon"' in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-status-bar-style" content="black-translucent"' in html


def test_manifest_contents_and_icons_exist():
    m = json.loads((STATIC / "manifest.json").read_text(encoding="utf-8"))
    assert m["name"] == "WinsPool"
    assert m["start_url"] == "/"
    assert m["display"] == "standalone"
    assert m["icons"]
    for icon in m["icons"]:
        assert icon["src"].startswith("/static/")
        assert (STATIC / icon["src"][len("/static/"):]).is_file()


def test_header_brands_link_to_standings():
    import re
    html = client.get("/").text
    anchors = re.findall(r'<a\b[^>]*class="nav-brand"[^>]*>', html)
    assert len(anchors) == 2
    for a in anchors:
        assert 'href="/wins-pool"' in a
    assert '<div class="nav-brand">' not in html
