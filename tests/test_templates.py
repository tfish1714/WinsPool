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


def test_recap_nav_entries_in_drawer_and_more_menu():
    base = (pathlib.Path(__file__).resolve().parent.parent / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'href="/recap"' in base
    js = (STATIC / "js" / "main.js").read_text(encoding="utf-8")
    assert "{ href: '/recap', label: 'Recaps' }" in js


def test_global_vapid_meta_emitted_once(monkeypatch):
    import main
    monkeypatch.setitem(main.standings_templates.env.globals, "push_vapid_key", "K")
    html = client.get("/").text
    assert html.count('name="vapid-public-key" content="K"') == 1


def test_player_page_has_push_card_markup():
    root = pathlib.Path(__file__).resolve().parent.parent
    src = (root / "templates" / "player_profile.html").read_text(encoding="utf-8")
    for needle in ('id="notifications"', 'id="push-status"', 'id="push-enable-btn"', 'id="push-ios-hint"',
                   'id="push-pref-recap"', 'id="push-pref-standings"', "js/push_card.js"):
        assert needle in src, needle
    assert src.index('id="notifications"') > src.index('id="own-page-only"')


def test_standings_template_loads_push_nudge_without_prompting():
    root = pathlib.Path(__file__).resolve().parent.parent
    assert "js/push_nudge.js" in (root / "templates" / "wins_pool.html").read_text(encoding="utf-8")
    assert "requestPermission" not in (STATIC / "js" / "push_nudge.js").read_text(encoding="utf-8")
