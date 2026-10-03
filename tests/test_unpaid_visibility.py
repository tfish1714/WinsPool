"""Unpaid entry visibility: config, stage function, gated endpoint, source contracts."""
from unittest.mock import patch

import pandas as pd
import pytest
from starlette.testclient import TestClient

from main import app
from services import pool_service as ps
from services.pool_service import (
    DEFAULT_UNPAID_VISIBILITY, clean_unpaid_visibility, get_payment_note, get_unpaid_visibility,
)


def _vis(**kw):
    base = {"enabled": True, "nudge_week": 8, "public_week": 10, "banner_week": 13}
    base.update(kw)
    return base


class TestCleanUnpaidVisibility:
    def test_defaults_when_missing(self):
        assert clean_unpaid_visibility(None) == DEFAULT_UNPAID_VISIBILITY
        assert DEFAULT_UNPAID_VISIBILITY["enabled"] is False

    def test_valid_passes_through(self):
        assert clean_unpaid_visibility(_vis(nudge_week=3, public_week=3, banner_week=22)) == \
            _vis(nudge_week=3, public_week=3, banner_week=22)

    @pytest.mark.parametrize("bad", [
        _vis(nudge_week=0), _vis(banner_week=23), _vis(nudge_week=11),          # range and order
        _vis(public_week="x"), _vis(public_week=True), _vis(public_week=10.5),  # types
        "junk", 5, [],
    ])
    def test_invalid_falls_back_to_disabled_defaults(self, bad):
        assert clean_unpaid_visibility(bad) == DEFAULT_UNPAID_VISIBILITY

    def test_enabled_must_be_a_real_bool(self):
        assert clean_unpaid_visibility(_vis(enabled="yes"))["enabled"] is False

    def test_default_dict_is_not_shared(self):
        clean_unpaid_visibility(None)["enabled"] = True
        assert DEFAULT_UNPAID_VISIBILITY["enabled"] is False


class TestStoredConfig:
    def test_get_reads_per_season(self):
        settings = {"pool_config": {"2026": {"unpaid_visibility": _vis(public_week=11)}}}
        assert get_unpaid_visibility(settings, 2026)["public_week"] == 11
        assert get_unpaid_visibility(settings, 2025) == DEFAULT_UNPAID_VISIBILITY
        assert get_unpaid_visibility({}, 2026) == DEFAULT_UNPAID_VISIBILITY

    def test_payment_note_trimmed_and_capped(self):
        settings = {"pool_config": {"2026": {"payment_note": "  @venmo-handle  "}}}
        assert get_payment_note(settings, 2026) == "@venmo-handle"
        long = {"pool_config": {"2026": {"payment_note": "x" * 500}}}
        assert len(get_payment_note(long, 2026)) == 200
        assert get_payment_note({}, 2026) == ""
        assert get_payment_note({"pool_config": {"2026": {"payment_note": 5}}}, 2026) == ""

    def test_set_round_trip_season_isolation_and_preservation(self):
        store = {"pool_config": {"2025": {"entry_fee": 100.0, "payouts": [{"place": 1, "amount": 100.0}],
                                          "unpaid_visibility": _vis(nudge_week=2, public_week=3, banner_week=4)}}}
        with patch.object(ps, "get_config_settings", side_effect=lambda: store), \
             patch.object(ps, "set_config_settings", side_effect=lambda d: store.update(d)):
            ps.set_pool_config(2026, 200, [{"place": 1, "amount": 200}],
                               unpaid_visibility=_vis(), payment_note="venmo @x")
            assert store["pool_config"]["2026"]["unpaid_visibility"] == _vis()
            assert store["pool_config"]["2026"]["payment_note"] == "venmo @x"
            assert store["pool_config"]["2025"]["unpaid_visibility"]["nudge_week"] == 2  # other season untouched
            # Omitting the new fields preserves what is stored for that season.
            ps.set_pool_config(2026, 250, [{"place": 1, "amount": 250}])
            assert store["pool_config"]["2026"]["entry_fee"] == 250.0
            assert store["pool_config"]["2026"]["unpaid_visibility"] == _vis()
            assert store["pool_config"]["2026"]["payment_note"] == "venmo @x"

    def test_set_without_new_fields_keeps_legacy_shape(self):
        store = {}
        with patch.object(ps, "get_config_settings", side_effect=lambda: store), \
             patch.object(ps, "set_config_settings", side_effect=lambda d: store.update(d)):
            saved = ps.set_pool_config(2026, 200, [{"place": 1, "amount": 200}])
        assert set(saved) == {"entry_fee", "payouts"}


class TestComputeUnpaidStage:
    def test_boundaries(self):
        v = _vis()
        got = [ps.compute_unpaid_stage(w, v) for w in (0, 1, 7, 8, 9, 10, 12, 13, 22)]
        assert got == ["off", "off", "off", "nudge", "nudge", "public", "public", "banner", "banner"]

    def test_disabled_is_always_off(self):
        assert ps.compute_unpaid_stage(22, _vis(enabled=False)) == "off"

    def test_invalid_settings_and_weeks_are_off(self):
        assert ps.compute_unpaid_stage(15, {"enabled": True, "nudge_week": 20, "public_week": 10, "banner_week": 13}) == "off"
        assert ps.compute_unpaid_stage(None, _vis()) == "off"
        assert ps.compute_unpaid_stage("x", _vis()) == "off"
        assert ps.compute_unpaid_stage(15, None) == "off"

    def test_equal_weeks_pick_the_latest_stage(self):
        v = _vis(nudge_week=5, public_week=5, banner_week=5)
        assert ps.compute_unpaid_stage(5, v) == "banner"


class TestCurrentPlayedWeek:
    def test_uses_results_not_schedule(self):
        games = pd.DataFrame({"season": [2026] * 4, "game_type": ["REG"] * 4, "week": [1, 2, 3, 18],
                              "result": [3.0, -7.0, 10.0, None]})
        assert ps.current_played_week(games, 2026) == 3

    def test_ignores_other_seasons_playoffs_and_sentinel(self):
        from services.constants import UNDRAFTED_SENTINEL
        games = pd.DataFrame({"season": [2025, 2026, 2026], "game_type": ["REG", "POST", "REG"],
                              "week": [18, 20, 2], "result": [1.0, 3.0, UNDRAFTED_SENTINEL]})
        assert ps.current_played_week(games, 2026) == 0
        assert ps.current_played_week(pd.DataFrame(), 2026) == 0
        assert ps.current_played_week(None, 2026) == 0


def _order(paid_flags):
    return pd.DataFrame({"season": [2026] * len(paid_flags), "playerId": list(range(1, len(paid_flags) + 1)),
                         "draftOrder": list(range(1, len(paid_flags) + 1)), "paid": paid_flags})


def _players(n):
    return pd.DataFrame({"playerId": list(range(1, n + 1)), "fullName": [f"Player {chr(64 + i)}" for i in range(1, n + 1)]})


def _settings(vis=None, note=None):
    entry = {"entry_fee": 200, "payouts": [{"place": 1, "amount": 200}]}
    if vis is not None:
        entry["unpaid_visibility"] = vis
    if note is not None:
        entry["payment_note"] = note
    return {"pool_config": {"2026": entry}}


class TestBuildUnpaidPayload:
    def _p(self, week, caller=2, admin=False, vis=None, note=None, paid=(True, False, False)):
        return ps.build_unpaid_payload(_order(list(paid)), _players(len(paid)),
                                       _settings(_vis() if vis is None else vis, note), 2026, week, caller, admin)

    def test_off_and_nudge_never_list_names_for_members(self):
        for week in (0, 5, 8, 9):
            p = self._p(week)
            assert p["unpaid"] == []
            assert "Player B" not in str(p)

    def test_nudge_reports_only_callers_own_status(self):
        p = self._p(8, caller=2)
        assert p["stage"] == "nudge" and p["me_unpaid"] is True and p["amount"] == 200.0
        assert self._p(8, caller=1)["me_unpaid"] is False

    def test_public_and_banner_list_unpaid_names(self):
        for week, stage in ((10, "public"), (13, "banner")):
            p = self._p(week)
            assert p["stage"] == stage
            assert p["unpaid"] == [{"playerId": 2, "name": "Player B"}, {"playerId": 3, "name": "Player C"}]

    def test_disabled_late_week_does_not_leak(self):
        p = self._p(15, vis=_vis(enabled=False))
        assert p["stage"] == "off" and p["unpaid"] == [] and p["me_unpaid"] is None

    def test_admin_sees_full_list_in_every_stage(self):
        for week in (0, 8, 10):
            p = self._p(week, admin=True)
            assert p["admin_view"] is True and len(p["unpaid"]) == 2
        assert self._p(0, admin=True, vis=_vis(enabled=False))["unpaid"] != []
        assert self._p(10)["admin_view"] is False

    def test_caller_not_a_member(self):
        assert self._p(9, caller=99)["me_unpaid"] is None
        assert self._p(9, caller=None)["me_unpaid"] is None

    def test_nan_and_missing_paid_count_as_unpaid(self):
        p = self._p(10, paid=(True, None, float("nan")))
        assert [u["playerId"] for u in p["unpaid"]] == [2, 3]
        order = _order([True, True]).drop(columns=["paid"])
        p2 = ps.build_unpaid_payload(order, _players(2), _settings(_vis()), 2026, 10, 1, False)
        assert len(p2["unpaid"]) == 2

    def test_no_rows_for_season_is_empty_not_error(self):
        p = ps.build_unpaid_payload(pd.DataFrame(), pd.DataFrame(), _settings(_vis()), 2026, 10, 1, False)
        assert p["unpaid"] == [] and p["me_unpaid"] is None
        p = ps.build_unpaid_payload(None, None, {}, 2026, 10, 1, False)
        assert p["stage"] == "off"

    def test_payment_note_only_for_unpaid_caller_or_admin(self):
        assert self._p(9, caller=2, note="venmo @x")["payment_note"] == "venmo @x"
        assert self._p(9, caller=1, note="venmo @x")["payment_note"] == ""
        assert self._p(0, caller=2, note="venmo @x")["payment_note"] == ""
        assert self._p(0, caller=1, admin=True, note="venmo @x")["payment_note"] == "venmo @x"

    def test_missing_player_name_falls_back(self):
        p = ps.build_unpaid_payload(_order([False]), pd.DataFrame(), _settings(_vis()), 2026, 10, 9, False)
        assert p["unpaid"] == [{"playerId": 1, "name": "Player 1"}]


def _load(order, players, games):
    return (None, None, games, players, order, pd.DataFrame(), pd.DataFrame())


def _games(week=10):
    return pd.DataFrame({"season": [2026], "game_type": ["REG"], "week": [week], "result": [3.0]})


@pytest.fixture
def as_player_2():
    from services.session_service import require_auth
    app.dependency_overrides[require_auth] = lambda: {"sub": "2", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture
def as_admin():
    from services.session_service import require_auth
    app.dependency_overrides[require_auth] = lambda: {"sub": "1", "role": "admin"}
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestUnpaidRoute:
    def _get(self, week, vis=None):
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, False, False]), _players(3), _games(week))), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis() if vis is None else vis)):
            return TestClient(app).get("/api/pool/unpaid?season=2026")

    def test_requires_auth(self):
        assert TestClient(app).get("/api/pool/unpaid").status_code in (401, 403)

    def test_member_off_stage_gets_empty_list(self, as_player_2):
        body = self._get(3).json()
        assert body["stage"] == "off" and body["unpaid"] == [] and body["week"] == 3

    def test_member_nudge_gets_own_flag_only(self, as_player_2):
        r = self._get(8)
        assert r.json()["me_unpaid"] is True and r.json()["unpaid"] == []
        assert "Player C" not in r.text

    def test_member_public_gets_names(self, as_player_2):
        assert len(self._get(10).json()["unpaid"]) == 2

    def test_admin_view(self, as_admin):
        body = self._get(0).json()
        assert body["admin_view"] is True and len(body["unpaid"]) == 2

    def test_status_endpoint_untouched(self, as_player_2):
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, False]), _players(2), _games())), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis())):
            body = TestClient(app).get("/api/pool/status?season=2026").json()
        assert "unpaid" not in body and "stage" not in body

    def test_set_member_paid_is_reflected_on_next_call(self, as_player_2):
        first = self._get(10).json()
        assert {u["playerId"] for u in first["unpaid"]} == {2, 3}
        with patch("routes.api_routes.load_data", return_value=_load(_order([True, True, False]), _players(3), _games(10))), \
             patch("routes.api_routes.get_config_settings", return_value=_settings(_vis())):
            second = TestClient(app).get("/api/pool/unpaid?season=2026").json()
        assert {u["playerId"] for u in second["unpaid"]} == {3}


# ---------------------------------------------------------------------------
# Admin Pool tab controls (source contracts + node behavior)
# ---------------------------------------------------------------------------
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_admin_pool_markup_has_unpaid_controls():
    html = _read("templates/admin.html")
    for el_id in ("pool-unpaid-enabled", "pool-unpaid-nudge", "pool-unpaid-public",
                  "pool-unpaid-banner", "pool-unpaid-preview", "pool-payment-note"):
        assert f'id="{el_id}"' in html
    assert "Show unpaid entries to members" in html
    assert re.search(r'id="pool-unpaid-nudge"[^>]*min="1"[^>]*max="22"', html)


def test_admin_pool_js_contract():
    js = _read("static/js/admin_pool.js")
    assert "unpaidVisibility" in js and "paymentNote" in js
    assert ".innerHTML" not in js  # the file header comment mentions the word; check property use
    assert "textContent" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_admin_pool_unpaid_preview_and_validation():
    js = _read("static/js/admin_pool.js")
    fns = []
    for name in ("_poolUnpaidPreview", "_poolUnpaidValidate"):
        m = re.search(rf"function {name}\(.*?\n\}}\n", js, re.S)
        assert m, f"{name} missing"
        fns.append(m.group(0))
    script = "\n".join(fns) + """
    const ok = {enabled: true, nudge_week: 8, public_week: 10, banner_week: 13};
    console.log(JSON.stringify([
      _poolUnpaidPreview(ok),
      _poolUnpaidPreview({...ok, enabled: false}),
      _poolUnpaidValidate(ok),
      _poolUnpaidValidate({...ok, nudge_week: 11}) !== null,
      _poolUnpaidValidate({...ok, banner_week: 23}) !== null,
      _poolUnpaidValidate({...ok, public_week: NaN}) !== null,
    ]));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got[0] == "Nudge from week 8, names shown from week 10, list from week 13"
    assert got[1] == "Unpaid visibility is off."
    assert got[2] is None and got[3] and got[4] and got[5]


# ---------------------------------------------------------------------------
# Standings and player page UI (source contracts)
# ---------------------------------------------------------------------------

def test_unpaid_script_contract():
    js = _read("static/js/unpaid_notice.js")
    assert "/api/pool/unpaid" in js
    assert ".innerHTML" not in js
    assert "createElement('span')" in js and "unpaid-pill" in js
    assert "createElement('a')" not in js          # pills are never links
    assert "querySelector('.unpaid-pill')" in js    # idempotent: skip when already present
    assert "MutationObserver" in js                 # re-applied after the 30s refresh
    assert "getAuthHeaders" in js
    assert "sessionStorage" in js                   # dismissible per session, in try/catch
    for container in (".wp-leader-name", ".wp-row-name", ".standings-stacked-card__name"):
        assert container in js


def test_wins_pool_loads_unpaid_script_as_module():
    html = _read("templates/wins_pool.html")
    assert re.search(r'<script type="module" src="\{\{ static_url\(\'js/unpaid_notice\.js\'\) \}\}"></script>', html)
    # nothing server-rendered by default
    assert "unpaid-pill" not in html and "Still owed" not in html


def test_unpaid_styles_exist_and_are_not_tap_targets():
    css = _read("static/style.css")
    assert ".unpaid-pill" in css and ".unpaid-notice" in css
    block = re.search(r"\.unpaid-pill\s*\{[^}]*\}", css).group(0)
    assert "pointer-events: none" in block and "margin-left" in block


def test_player_profile_shows_amount_owed_and_note_safely():
    html = _read("templates/player_profile.html")
    assert "/api/pool/unpaid" in html
    assert "payment_note" in html
    gate = html.index("own-page-only")
    start = html.index("/api/pool/unpaid")
    assert start > gate
    assert ".innerHTML" not in html[start: start + 1500]


class TestStageUsesTheWeekShownOnStandings:
    """The stage must track the same "current week" as the standings WEEK pill (the in-progress week)."""

    def _games(self):
        # Week 7 fully played; week 8 half played (one of two games has a result).
        return pd.DataFrame({
            "season": [2026] * 4, "game_type": ["REG"] * 4, "week": [7, 7, 8, 8],
            "result": [3.0, -4.0, 6.0, None],
        })

    def test_partially_played_week_counts_as_current(self):
        assert ps.current_played_week(self._games(), 2026) == 8

    def test_nudge_starts_during_nudge_week_not_after_it(self):
        week = ps.current_played_week(self._games(), 2026)
        assert ps.compute_unpaid_stage(week, _vis(nudge_week=8, public_week=10, banner_week=13)) == "nudge"

    def test_no_completed_game_is_week_zero_even_if_nudge_week_is_one(self):
        games = pd.DataFrame({"season": [2026], "game_type": ["REG"], "week": [1], "result": [None]})
        week = ps.current_played_week(games, 2026)
        assert week == 0
        assert ps.compute_unpaid_stage(week, _vis(nudge_week=1, public_week=1, banner_week=1)) == "off"
