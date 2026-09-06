"""Tests for the draft room's onboarding banner/drawer markup (GET /draft).

There's no JS test suite in this repo (see CLAUDE.md) -- these are template
regression tests: they assert the onboarding elements main.js wires up
(banner, help button, drawer sections) actually render in the page, so a
future template edit can't silently drop one of the ids/classes JS depends
on without a test failing.
"""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_draft_page_loads():
    response = client.get("/draft")
    assert response.status_code == 200


def test_onboarding_banner_present_and_initially_hidden():
    """Banner markup renders with the 'hidden' class -- main.js removes it
    via localStorage check, not the server, so it must start hidden here."""
    html = client.get("/draft").text
    assert 'id="onboarding-banner"' in html
    assert 'class="onboarding-banner hidden"' in html
    assert 'id="onboarding-dismiss-btn"' in html
    assert 'id="onboarding-learn-btn"' in html


def test_rules_help_button_present():
    html = client.get("/draft").text
    assert 'id="rules-help-btn"' in html


def test_rules_drawer_present_with_all_three_sections():
    html = client.get("/draft").text
    assert 'id="rules-drawer"' in html
    assert 'id="rules-drawer-overlay"' in html
    assert 'id="rules-drawer-close"' in html
    assert "<h3>Draft flow</h3>" in html
    assert "<h3>Chat</h3>" in html
    assert "<h3>Chat icons</h3>" in html
