"""`.btn-secondary` is used by admin buttons and must have a themed rule."""
import pathlib
import re

CSS = (pathlib.Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(encoding="utf-8")


def _rule(selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    return m.group(1) if m else None


def test_btn_secondary_rule_uses_tokens():
    body = _rule(".btn-secondary")
    assert body, ".btn-secondary needs a CSS rule"
    assert "var(--surface-sunken" in body
    assert "var(--hairline-strong)" in body
    assert "var(--ink)" in body


def test_btn_secondary_states_exist():
    assert _rule(".btn-secondary:hover")
    assert _rule(".btn-secondary:disabled")
    assert _rule(".btn-secondary:focus-visible")
