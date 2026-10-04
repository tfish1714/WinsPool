"""No white or dark-tint color literals outside the theme token blocks (they vanish or turn
flat gray in one of the two themes). Use the tint/hairline/surface/ink tokens instead."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

WHITE = re.compile(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*[0-9.]+\s*\)")
BLACK_TINT = re.compile(r"background(?:-color)?\s*[:=]\s*['\"]?\s*rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0?\.[0-4][0-9]*\s*\)")

# Files that legitimately carry literals: the token definitions' fallbacks.
SKIP = {"static/js/chart_theme.js"}

TOKENS = ["--tint-faint", "--tint", "--tint-strong", "--tint-heavy", "--hairline", "--hairline-strong"]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _css_outside_root_blocks():
    out, depth_root = [], False
    for line in _read("static/style.css").splitlines():
        s = line.strip()
        if s.startswith(":root") and "{" in s:
            depth_root = True
        if depth_root:
            if s.startswith("}"):
                depth_root = False
            continue
        out.append(line)
    return "\n".join(out)


def _sources():
    yield "static/style.css", _css_outside_root_blocks()
    for p in sorted((ROOT / "templates").glob("*.html")):
        yield f"templates/{p.name}", p.read_text(encoding="utf-8")
    for p in sorted((ROOT / "static/js").glob("*.js")):
        rel = f"static/js/{p.name}"
        if rel not in SKIP:
            yield rel, p.read_text(encoding="utf-8")


_SRC = list(_sources())


def _non_shadow_hits(text):
    hits = []
    for line in text.splitlines():
        if "shadow" in line:
            continue
        if WHITE.search(line):
            hits.append(line.strip()[:110])
    return hits


@pytest.mark.parametrize("rel,text", _SRC, ids=[r for r, _ in _SRC])
def test_no_white_tint_literals(rel, text):
    assert _non_shadow_hits(text) == [], f"{rel}: white tint literal(s); use --tint*/--hairline*/--ink* tokens"


@pytest.mark.parametrize("rel,text", _SRC, ids=[r for r, _ in _SRC])
def test_no_dark_tint_backgrounds(rel, text):
    assert not BLACK_TINT.search(text), f"{rel}: dark tint background; use --surface-sunken* tokens"


def test_tint_tokens_have_dark_default_and_light_override():
    css = _read("static/style.css")
    for name in TOKENS:
        assert len(re.findall(rf"{re.escape(name)}\s*:", css)) >= 2, f"{name} needs a dark default and a light override"
    light = re.search(r':root\[data-theme="light"\]\s*\{(.*?)\n\}', css, re.S).group(1)
    for name in TOKENS:
        assert name in light, f"{name} missing from the light theme block"
    # light tints must be dark-on-light (navy base), never white
    for name in TOKENS:
        val = re.search(rf"{re.escape(name)}\s*:\s*([^;]+);", light).group(1)
        assert "255,255,255" not in val.replace(" ", ""), f"{name} is white in the light theme"
