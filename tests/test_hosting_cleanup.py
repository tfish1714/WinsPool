"""Dead Firebase Hosting config is retired and the recap email links to the real app URL."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_firebase_json_has_no_rewrites_to_missing_service():
    config = json.loads((ROOT / "firebase.json").read_text(encoding="utf-8"))
    hosting = config.get("hosting")
    if hosting is None:
        return
    hosts = hosting if isinstance(hosting, list) else [hosting]
    for h in hosts:
        for rewrite in h.get("rewrites", []):
            run = rewrite.get("run")
            if run:
                assert run["serviceId"] == "winspool"


def test_firebase_json_has_no_hosting_block():
    config = json.loads((ROOT / "firebase.json").read_text(encoding="utf-8"))
    assert "hosting" not in config


def test_recap_html_links_to_app_base_url(monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", "https://winspool-abc.a.run.app/")
    from scripts.generate_weekly_summary import build_recap_html
    html = build_recap_html(5, "Summary body")
    assert 'href="https://winspool-abc.a.run.app"' in html
    assert "web.app" not in html
    assert "Summary body" in html
    assert "Week 5" in html


def test_recap_html_defaults_to_local_base_url(monkeypatch):
    monkeypatch.delenv("APP_BASE_URL", raising=False)
    from scripts.generate_weekly_summary import build_recap_html
    assert 'href="http://localhost:8000"' in build_recap_html(1, "x")
