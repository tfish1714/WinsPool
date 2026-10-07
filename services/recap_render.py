"""services/recap_render.py -- stored recap text -> safe HTML at render time.

Recaps are stored as written (plain text, optionally light markdown). Everything
is HTML-escaped FIRST, then a small allowlist of transforms is applied to the
escaped text, so raw HTML in a recap can never become markup.
"""
import html
import re

from markupsafe import Markup

# Label and URL lengths are bounded so an unclosed "[" cannot make matching
# quadratic on adversarial input.
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^\s)]{1,2000}|/(?!/)[^\s)]{0,2000})\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\*\w])")
_BULLET = re.compile(r"^\s*[-*]\s+")
_HEADING = re.compile(r"^#{1,3}\s+")
_BLOCK_SPLIT = re.compile(r"\n\s*\n")


def _inline(escaped: str) -> str:
    s = _LINK.sub(
        lambda m: '<a href="%s" rel="noopener noreferrer">%s</a>' % (m.group(2), m.group(1)),
        escaped,
    )
    s = _BOLD.sub(r"<strong>\1</strong>", s)
    return _ITALIC.sub(r"<em>\1</em>", s)


def _esc_inline(raw: str) -> str:
    return _inline(html.escape(raw))


def render_recap_html(text) -> Markup:
    if text is None or not str(text).strip():
        return Markup("")
    blocks = _BLOCK_SPLIT.split(str(text).replace("\r\n", "\n").strip())
    out = []
    for block in blocks:
        lines = [ln.rstrip() for ln in block.split("\n")]
        if all(_BULLET.match(ln) for ln in lines):
            items = "".join("<li>%s</li>" % _esc_inline(_BULLET.sub("", ln)) for ln in lines)
            out.append("<ul>%s</ul>" % items)
        elif len(lines) == 1 and _HEADING.match(lines[0]):
            out.append("<h4>%s</h4>" % _esc_inline(_HEADING.sub("", lines[0])))
        else:
            out.append("<p>%s</p>" % "<br>".join(_esc_inline(ln) for ln in lines))
    return Markup("".join(out))
