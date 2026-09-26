"""Markup contracts for player-name links on the standings page and team links
in the player page's pick cells (mobile-safe tap targets, one link per player
per rendering, logos never linked)."""
import re
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from main import app
from test_wins_pool_missing_standings import (
    SEASON, _draft, _players, _standings,
)

client = TestClient(app)
ROOT = Path(__file__).resolve().parent.parent


def _render_standings():
    data = (
        _standings([
            ("BAL", 13, 450, 280), ("KC", 12, 400, 300), ("SF", 11, 420, 310),
            ("DEN", 10, 380, 350), ("LA", 9, 370, 360), ("NE", 4, 250, 400),
        ]),
        pd.DataFrame(), pd.DataFrame(), _players(), pd.DataFrame(), _draft(), pd.DataFrame(),
    )
    with patch("routes.standings_routes.load_data", return_value=data), \
         patch("routes.standings_routes.get_active_season", return_value=SEASON), \
         patch("routes.standings_routes.get_available_years", return_value=[SEASON]), \
         patch("routes.standings_routes.get_latest_week_for_year", return_value=0), \
         patch("routes.standings_routes.analysis.get_draft_progress", return_value=(6, 6)), \
         patch("routes.standings_routes.analysis.get_enriched_schedule", return_value=pd.DataFrame()), \
         patch("routes.standings_routes.analysis.player_winlossmatrix", return_value=pd.DataFrame()), \
         patch("routes.standings_routes.db.get_weekly_recap", return_value=None):
        return client.get(f"/wins-pool/{SEASON}").text


def _main(html):
    start = html.index('<main class="dashboard-main">')
    return html[start:html.index("</main>", start)]


def test_player_name_links_in_every_variant():
    m = _main(_render_standings())
    # Leader card (player 1), desktop row (player 2), stacked cards (both).
    leader = m[m.index('class="wp-leader '):m.index("wp-leader-sub")]
    assert 'href="/player/1"' in leader and 'class="player-link"' in leader
    row = m[m.index('class="wp-row-name"'):m.index("wp-row-meta")]
    assert 'href="/player/2"' in row
    stacked = m[m.index('class="standings-stacked"'):]
    assert stacked.count('href="/player/1"') == 1
    assert stacked.count('href="/player/2"') == 1
    # Exactly one link per player per rendering: leader card 1, one desktop row 1,
    # two stacked cards 2 = 4 total for 2 players.
    assert len(re.findall(r'href="/player/\d+"', m)) == 4


def test_standings_has_no_other_links_and_logos_are_not_wrapped():
    m = _main(_render_standings())
    assert len(re.findall(r"<a\b", m)) == 4
    assert not re.search(r"<a\b[^>]*>\s*<img", m)


def test_player_link_css_has_44px_tap_target():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.player-link\s*\{([^}]*)\}", css)
    assert m, "missing .player-link rule"
    body = m.group(1)
    assert "min-height: 44px" in body and "display: inline-flex" in body


def test_live_refresh_patches_in_place_and_never_rebuilds_name_markup():
    js = (ROOT / "static" / "js" / "standings_refresh.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "replaceWith" not in js
    assert "player-link" not in js  # link nodes are untouched by patching


def test_player_page_pick_cells_link_only_the_team_abbreviation():
    from test_player_page import _get
    html = _get("/player/1").text
    assert '<a class="team-link" href="/team/KC">KC</a>' in html
    assert not re.search(r'<td[^>]*>\s*<a\b', html)


def test_player_page_team_link_normalizes_legacy_abbreviation():
    import test_player_page as tpp
    standings, a, games, players, b, draft, c = tpp._load()
    draft = draft.assign(team="OAK")
    data = (standings, a, games, players, b, draft, c)
    with patch("routes.history_routes.load_data", return_value=data),          patch("services.analysis_service.load_data", return_value=data),          patch("services.analysis_service.get_season_projection_legacy_shape",
               return_value={}):
        html = client.get("/player/1", follow_redirects=False).text
    assert 'href="/team/LV"' in html
    assert 'href="/team/OAK"' not in html


def test_legacy_abbreviations_all_resolve_to_valid_team_pages():
    from services.constants import TEAM_ABBR_MAP
    from services.team_page_service import TEAM_NAMES
    from services.utils import normalize_team_abbr
    for legacy in list(TEAM_ABBR_MAP) + ["OAK", "SD", "STL", "LAR", "WSH", "JAC"]:
        assert normalize_team_abbr(legacy) in TEAM_NAMES


def test_team_link_css_is_single_line_tap_target_without_negative_margin():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"\.team-link\s*\{([^}]*)\}", css)
    assert m, "missing .team-link rule"
    body = m.group(1)
    assert "min-height: 44px" in body and "display: inline-flex" in body
    assert "margin: -" not in body and "margin:-" not in body


def test_player_link_has_touch_only_affordance():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    m = re.search(r"@media \(hover: none\)\s*\{[^@]*?\.player-link[^{]*\{([^}]*)\}", css)
    assert m, "missing (hover: none) .player-link affordance"
    assert "border-bottom" in m.group(1)
