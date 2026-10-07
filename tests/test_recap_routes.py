from unittest.mock import patch
from starlette.testclient import TestClient
from main import app

client = TestClient(app)


def _patch(weeks_by_year, active=2026):
    return (
        patch("routes.recap_routes._active_season", return_value=active),
        patch("routes.recap_routes._available_years", return_value=[active, active - 1]),
        patch("routes.recap_routes.db.list_recap_weeks", side_effect=lambda y: weeks_by_year.get(y, [])),
        patch("routes.recap_routes.db.get_weekly_recap",
              side_effect=lambda y, w: {"year": y, "week": w, "summary": "Hello <b>x</b>\nnext", "timestamp": 1.0}
              if w in weeks_by_year.get(y, []) else None),
    )


def _run(weeks, path, **kw):
    ps = _patch(weeks)
    with ps[0], ps[1], ps[2], ps[3]:
        return client.get(path, follow_redirects=False, **kw)


def test_recap_redirects_to_latest_week():
    r = _run({2026: [3, 5]}, "/recap")
    assert r.status_code in (302, 307) and r.headers["location"] == "/recap/2026/5"


def test_recap_falls_back_to_prior_season():
    r = _run({2025: [18]}, "/recap")
    assert r.headers["location"] == "/recap/2025/18"


def test_recap_empty_state_when_none_exist():
    r = _run({}, "/recap")
    assert r.status_code == 200 and "No recaps" in r.text


def test_recap_page_renders_escaped_body_and_week_links():
    r = _run({2026: [3, 5]}, "/recap/2026/5")
    assert r.status_code == 200
    assert "Hello &lt;b&gt;x&lt;/b&gt;<br>next" in r.text
    assert 'href="/recap/2026/3"' in r.text


def test_unknown_week_is_friendly_not_500():
    r = _run({2026: [3]}, "/recap/2026/9")
    assert r.status_code == 200 and "No recap" in r.text


def test_year_route_redirects_to_latest_week_of_that_year():
    r = _run({2025: [4, 17]}, "/recap/2025")
    assert r.status_code in (302, 307) and r.headers["location"] == "/recap/2025/17"


def test_year_route_empty_year_shows_empty_state():
    r = _run({}, "/recap/2024")
    assert r.status_code == 200 and "No recaps" in r.text


def test_year_picker_links_to_year_route():
    r = _run({2026: [3]}, "/recap/2026/3")
    assert "window.location='/recap/' + this.value" in r.text
