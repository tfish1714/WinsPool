"""The iOS install banner must sit above the mobile bottom tab bar, not over it."""
import pathlib
import re

CSS = (pathlib.Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(encoding="utf-8")


def test_tab_bar_height_token_defined_and_shared():
    assert re.search(r"--bottom-tab-bar-height:\s*72px", CSS)
    assert "padding-bottom: var(--bottom-tab-bar-height)" in CSS


def test_banner_offsets_from_tab_bar_at_tab_bar_breakpoint():
    m = re.search(
        r"@media \(max-width: 860px\)\s*\{\s*\.ios-install-banner\s*\{\s*bottom:\s*calc\(([^;]*)\);",
        CSS,
    )
    assert m, "banner needs a max-width:860px rule (same breakpoint as the tab bar)"
    assert "var(--bottom-tab-bar-height)" in m.group(1)
    assert "safe-area-inset-bottom" in m.group(1)
