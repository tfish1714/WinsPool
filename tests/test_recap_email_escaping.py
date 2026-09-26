"""build_recap_html must not let model-generated text inject markup into the email."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

from generate_weekly_summary import build_recap_html


def test_script_tag_and_ampersand_are_escaped():
    out = build_recap_html(3, "Team <script>alert(1)</script> beat A&B")
    assert "<script>" not in out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "A&amp;B" in out


def test_newlines_preserved_and_pre_wrap_kept():
    out = build_recap_html(3, "line one\nline two")
    assert "line one\nline two" in out
    assert "white-space: pre-wrap" in out


def test_source_has_no_stale_wrapper_comment():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "scripts" / "generate_weekly_summary.py").read_text(encoding="utf-8")
    assert "# Create a simple HTML wrapper" not in src
