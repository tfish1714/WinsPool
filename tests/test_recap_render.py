from markupsafe import Markup
from services.recap_render import render_recap_html


def test_blank_returns_empty_markup():
    assert render_recap_html(None) == Markup("")
    assert render_recap_html("  \n ") == Markup("")


def test_escapes_raw_html():
    out = str(render_recap_html("Hi <script>alert(1)</script> & bye"))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "&amp;" in out


def test_paragraphs_and_line_breaks():
    out = str(render_recap_html("Line one\nLine two\n\nSecond para"))
    assert out == "<p>Line one<br>Line two</p><p>Second para</p>"


def test_bold_italic_and_bullets():
    out = str(render_recap_html("**Big** win and *close* one\n\n- a\n- b"))
    assert "<strong>Big</strong>" in out and "<em>close</em>" in out
    assert "<ul><li>a</li><li>b</li></ul>" in out


def test_heading_line():
    assert str(render_recap_html("## Top story")) == "<h4>Top story</h4>"


def test_http_and_relative_links_allowed_javascript_not():
    ok = str(render_recap_html("[site](https://example.com/a?b=1&c=2) and [me](/player/3)"))
    assert 'href="https://example.com/a?b=1&amp;c=2"' in ok
    assert 'href="/player/3"' in ok and 'rel="noopener noreferrer"' in ok
    bad = str(render_recap_html("[x](javascript:alert(1))"))
    assert "<a " not in bad


def test_protocol_relative_link_is_not_an_anchor():
    out = str(render_recap_html("[x](//evil.com/a)"))
    assert "<a " not in out and "href" not in out
    assert "[x](//evil.com/a)" in out
    ok = str(render_recap_html("[me](/player/3) [s](https://example.com/q)"))
    assert 'href="/player/3"' in ok and 'href="https://example.com/q"' in ok


def test_quote_in_text_cannot_break_attribute():
    out = str(render_recap_html('[a](https://e.com/x" onclick="y)'))
    assert "onclick=" not in out or 'onclick=&quot;' in out
    assert '" onclick="' not in out


def test_asterisk_math_not_italic():
    assert "<em>" not in str(render_recap_html("5 * 3 * 2"))


def test_filter_registered_on_every_template_env():
    import main
    for t in (main.standings_templates, main.history_templates, main.draft_templates,
              main.admin_templates, main.mock_draft_templates):
        assert "recap_html" in t.env.filters


def test_standings_card_renders_sanitized_recap(monkeypatch):
    from test_standings_routes import _render_wins_pool
    resp = _render_wins_pool(monkeypatch, recap={"summary": "Hi <b>x</b>\nnext"})
    assert resp.status_code == 200
    assert "Hi &lt;b&gt;x&lt;/b&gt;<br>next" in resp.text


import time
import pytest

_ADVERSARIAL = [
    "[" * 50000,
    "[a](https://" * 5000,
    "*" * 50000,
    "**" * 25000,
    "* " * 25000,
    "(" * 50000,
    "**a " * 12500,
    "*a " * 16000,
    "[a" * 25000,
    "- " * 25000,
    "\n".join(["* x"] * 10000),
]


@pytest.mark.parametrize("payload", _ADVERSARIAL, ids=range(len(_ADVERSARIAL)))
def test_adversarial_input_is_fast(payload):
    start = time.perf_counter()
    render_recap_html(payload)
    assert time.perf_counter() - start < 3.0


def test_link_label_up_to_200_chars_still_renders():
    label = "a" * 200
    out = str(render_recap_html("[%s](https://example.com/x)" % label))
    assert '<a href="https://example.com/x"' in out and label in out


def test_inline_formatting_and_links_unchanged():
    out = str(render_recap_html("**a** and *b* and [c](/p/1)"))
    assert out == '<p><strong>a</strong> and <em>b</em> and <a href="/p/1" rel="noopener noreferrer">c</a></p>'
