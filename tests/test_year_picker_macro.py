import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
ENV = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html"]))


def _render(*args):
    tpl = ENV.from_string('{% from "_macros.html" import year_picker %}{{ year_picker(*args) }}')
    return tpl.render(args=args)


def test_year_picker_preserves_id_class_aria_and_order():
    html = _render([2023, 2025, 2024], 2024, "/schedule/")
    assert 'id="year-pick"' in html
    assert 'class="season-select"' in html
    assert 'aria-label="Season"' in html
    assert "window.location='/schedule/' + this.value" in html
    assert re.findall(r'value="(\d+)"', html) == ["2025", "2024", "2023"]
    assert re.search(r'<option value="2024" selected>2024 Season</option>', html)
    assert html.count("selected") == 1


def test_year_picker_url_suffix():
    html = _render([2024], 2024, "/wins-pool/", "/weekbyweek")
    assert "window.location='/wins-pool/' + this.value + '/weekbyweek'" in html


def test_all_six_templates_use_macro_and_no_inline_select():
    for name in ["wins_pool", "schedule", "weekbyweek", "headtohead", "playoff_race", "draft_results"]:
        src = (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")
        assert "year_picker(" in src, name
        assert '<select id="year-pick"' not in src, name
