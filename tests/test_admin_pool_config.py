"""Tests for GET/POST /api/admin/pool/config (per-season, dollar-amount pool config)."""
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app
from services.pool_service import DEFAULT_UNPAID_VISIBILITY
from services.session_service import create_token


def _order(n=10, season=2026):
    return pd.DataFrame([{"season": season, "draftOrder": i + 1, "playerId": i + 1, "paid": False}
                         for i in range(n)])


def _load(order):
    return (None, None, pd.DataFrame(), None, order, pd.DataFrame(), pd.DataFrame())


@pytest.fixture
def store():
    """In-memory config/settings doc wired into pool_service's read/write."""
    state = {}

    def _set(data):
        state.update(data)

    with patch("services.pool_service.get_config_settings", side_effect=lambda: dict(state)), \
         patch("services.pool_service.set_config_settings", side_effect=_set), \
         patch("routes.admin_routes.load_data", return_value=_load(_order())):
        yield state


def _h(admin_token):
    return {"Authorization": admin_token}


class TestAdminPoolConfig:
    def test_get_defaults(self, admin_token, store):
        r = TestClient(app).get("/api/admin/pool/config?season=2026", headers=_h(admin_token))
        assert r.status_code == 200
        b = r.json()
        assert b["season"] == 2026
        assert b["entry_fee"] == 200
        assert b["payouts"] == [{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}]
        assert b["member_count"] == 10
        assert b["total_pot"] == 2000 and b["payout_total"] == 2000 and b["pot_balance"] == 0
        assert b["is_default"] is True

    def test_post_saves_and_get_reflects(self, admin_token, store):
        c = TestClient(app)
        r = c.post("/api/admin/pool/config", headers=_h(admin_token),
                   json={"season": 2026, "entryFee": 100,
                         "payouts": [{"place": 1, "amount": 700}, {"place": "last", "amount": 100}]})
        assert r.status_code == 200
        b = r.json()
        assert b["entry_fee"] == 100 and b["is_default"] is False
        assert b["payout_total"] == 800 and b["pot_balance"] == 200
        g = c.get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["entry_fee"] == 100
        assert g["payouts"][-1] == {"place": "last", "amount": 100}

    def test_post_other_season_does_not_change_first(self, admin_token, store):
        c = TestClient(app)
        c.post("/api/admin/pool/config", headers=_h(admin_token),
               json={"season": 2027, "entryFee": 5, "payouts": [{"place": 1, "amount": 10}]})
        g = c.get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["entry_fee"] == 200 and g["is_default"] is True
        g27 = c.get("/api/admin/pool/config?season=2027", headers=_h(admin_token)).json()
        assert g27["entry_fee"] == 5

    @pytest.mark.parametrize("payload", [
        {"season": 2026, "entryFee": -1, "payouts": [{"place": 1, "amount": 10}]},
        {"season": 2026, "entryFee": 10, "payouts": [{"place": 1, "amount": -10}]},
        {"season": 2026, "entryFee": 10, "payouts": [{"place": 1, "amount": 5}, {"place": 1, "amount": 5}]},
        {"season": 2026, "entryFee": 10, "payouts": [{"place": "last", "amount": 5},
                                                     {"place": "last", "amount": 5}]},
        {"season": 2026, "entryFee": 10, "payouts": [{"place": 0, "amount": 5}]},
        {"season": 2026, "entryFee": 10, "payouts": [{"place": "middle", "amount": 5}]},
        {"season": 2026, "entryFee": 10,
         "payouts": [{"place": i + 1, "amount": 1} for i in range(11)]},
    ])
    def test_post_rejects_invalid(self, admin_token, store, payload):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token), json=payload)
        assert r.status_code in (400, 422)
        m.assert_not_called()

    @pytest.mark.parametrize("payload", [
        {"season": 2026, "entryFee": 10, "payouts": []},
        {"season": 2026, "entryFee": 10},
    ])
    def test_post_rejects_empty_or_missing_payouts(self, admin_token, store, payload):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token), json=payload)
        assert r.status_code in (400, 422)
        assert "payout" in r.text.lower()
        m.assert_not_called()

    def test_non_admin_forbidden(self, store):
        tok = create_token(player_id=2, role="player")
        h = {"Authorization": f"Bearer {tok}"}
        c = TestClient(app)
        assert c.get("/api/admin/pool/config?season=2026", headers=h).status_code == 403
        r = c.post("/api/admin/pool/config", headers=h,
                   json={"season": 2026, "entryFee": 10, "payouts": [{"place": 1, "amount": 10}]})
        assert r.status_code == 403


_VIS = {"enabled": True, "nudge_week": 8, "public_week": 10, "banner_week": 13}
_BASE = {"season": 2026, "entryFee": 200, "payouts": [{"place": 1, "amount": 1400}]}


class TestAdminPoolConfigUnpaid:
    def test_post_and_get_round_trip_with_season_isolation(self, admin_token, store):
        c = TestClient(app)
        r = c.post("/api/admin/pool/config", headers=_h(admin_token),
                   json={**_BASE, "unpaidVisibility": _VIS, "paymentNote": "venmo @x"})
        assert r.status_code == 200
        assert r.json()["unpaid_visibility"] == _VIS and r.json()["payment_note"] == "venmo @x"
        g = c.get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["unpaid_visibility"] == _VIS and g["payment_note"] == "venmo @x"
        g27 = c.get("/api/admin/pool/config?season=2027", headers=_h(admin_token)).json()
        assert g27["unpaid_visibility"] == DEFAULT_UNPAID_VISIBILITY and g27["payment_note"] == ""

    def test_get_defaults_to_disabled(self, admin_token, store):
        g = TestClient(app).get("/api/admin/pool/config?season=2026", headers=_h(admin_token)).json()
        assert g["unpaid_visibility"] == DEFAULT_UNPAID_VISIBILITY
        assert g["unpaid_visibility"]["enabled"] is False

    def test_post_without_new_fields_keeps_stored_values(self, admin_token, store):
        c = TestClient(app)
        c.post("/api/admin/pool/config", headers=_h(admin_token),
               json={**_BASE, "unpaidVisibility": _VIS, "paymentNote": "venmo @x"})
        r = c.post("/api/admin/pool/config", headers=_h(admin_token), json={**_BASE, "entryFee": 250})
        assert r.json()["entry_fee"] == 250
        assert r.json()["unpaid_visibility"] == _VIS and r.json()["payment_note"] == "venmo @x"

    @pytest.mark.parametrize("vis", [
        {**_VIS, "nudge_week": 0},
        {**_VIS, "banner_week": 23},
        {**_VIS, "nudge_week": 12, "public_week": 10},
        {**_VIS, "public_week": "x"},
    ])
    def test_invalid_weeks_rejected(self, admin_token, store, vis):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token),
                                     json={**_BASE, "unpaidVisibility": vis})
        assert r.status_code == 422
        m.assert_not_called()

    def test_payment_note_over_200_chars_rejected(self, admin_token, store):
        with patch("services.pool_service.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers=_h(admin_token),
                                     json={**_BASE, "paymentNote": "x" * 201})
        assert r.status_code == 422
        m.assert_not_called()

    def test_non_admin_cannot_set_visibility(self, store):
        tok = create_token(player_id=2, role="player")
        r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": f"Bearer {tok}"},
                                 json={**_BASE, "unpaidVisibility": _VIS})
        assert r.status_code == 403
