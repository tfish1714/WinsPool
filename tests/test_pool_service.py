"""Tests for the pool fee / prize pot tracker (services/pool_service.py and routes)."""
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app
from services.pool_service import build_pool_status
from services.session_service import require_auth, create_token


def _order(n=10, paid=7, season=2026):
    rows = []
    for i in range(n):
        rows.append({"season": season, "draftOrder": i + 1, "playerId": i + 1, "paid": i < paid})
    return pd.DataFrame(rows)


def _load(order):
    return (None, None, pd.DataFrame(), None, order, pd.DataFrame(), pd.DataFrame())


class TestBuildPoolStatus:
    def test_counts_and_pot(self):
        s = build_pool_status(_order(), {"pool_entry_fee": 100}, 2026, 1)
        assert s["total_count"] == 10
        assert s["paid_count"] == 7
        assert s["total_pot"] == 1000
        assert s["collected"] == 700

    def test_payouts_split(self):
        settings = {"pool_entry_fee": 100, "pool_payouts": [
            {"place": 1, "pct": 60}, {"place": 2, "pct": 30}, {"place": 3, "pct": 10}]}
        s = build_pool_status(_order(), settings, 2026, 1)
        assert [p["amount"] for p in s["payouts"]] == [600, 300, 100]

    def test_my_paid_values(self):
        assert build_pool_status(_order(), {}, 2026, 1)["my_paid"] is True
        assert build_pool_status(_order(), {}, 2026, 10)["my_paid"] is False
        assert build_pool_status(_order(), {}, 2026, 999)["my_paid"] is None
        assert build_pool_status(_order(), {}, 2026, None)["my_paid"] is None

    def test_unset_fee_defaults(self):
        s = build_pool_status(_order(), {}, 2026, 1)
        assert s["entry_fee"] == 0
        assert s["total_pot"] == 0
        assert s["payouts"] == [{"place": 1, "pct": 100, "amount": 0}]

    def test_empty_order(self):
        s = build_pool_status(pd.DataFrame(), {"pool_entry_fee": 50}, 2026, 1)
        assert s["total_count"] == 0 and s["paid_count"] == 0
        assert s["my_paid"] is None

    def test_other_season_rows_ignored(self):
        df = pd.concat([_order(season=2025), _order(n=4, paid=1, season=2026)])
        s = build_pool_status(df, {"pool_entry_fee": 10}, 2026, 1)
        assert s["total_count"] == 4 and s["paid_count"] == 1

    def test_invalid_config_never_raises(self):
        s = build_pool_status(_order(), {"pool_entry_fee": -5, "pool_payouts": "junk"}, 2026, 1)
        assert s["entry_fee"] == 0
        assert s["payouts"][0]["pct"] == 100
        s = build_pool_status(_order(), {"pool_entry_fee": "abc",
                                         "pool_payouts": [{"place": 1, "pct": "x"}]}, 2026, 1)
        assert s["entry_fee"] == 0
        assert s["payouts"][0]["pct"] == 100

    def test_rounds_to_cents(self):
        s = build_pool_status(_order(n=3, paid=0), {"pool_entry_fee": 33.33,
                              "pool_payouts": [{"place": 1, "pct": 33.3333}]}, 2026, 1)
        assert s["payouts"][0]["amount"] == round(33.33 * 3 * 33.3333 / 100, 2)


@pytest.fixture
def auth_as_player_3():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestPoolStatusRoute:
    def test_no_leak_of_other_players(self, auth_as_player_3):
        client = TestClient(app)
        with patch("routes.api_routes.load_data", return_value=_load(_order())), \
             patch("routes.api_routes.get_config_settings", return_value={"pool_entry_fee": 100}):
            resp = client.get("/api/pool/status?season=2026")
        assert resp.status_code == 200
        body = resp.json()
        assert body["my_paid"] is True
        assert body["paid_count"] == 7
        assert "playerId" not in resp.text
        assert set(body) == {"season", "entry_fee", "total_count", "paid_count",
                             "total_pot", "collected", "payouts", "my_paid"}

    def test_requires_auth(self):
        assert TestClient(app).get("/api/pool/status").status_code in (401, 403)


class TestPoolConfigRoute:
    def test_rejects_negative_fee(self, admin_token):
        r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": admin_token},
                                 json={"entryFee": -1, "payouts": [{"place": 1, "pct": 100}]})
        assert r.status_code in (400, 422)

    def test_rejects_payouts_over_100_percent(self, admin_token):
        with patch("routes.admin_routes.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": admin_token},
                                     json={"entryFee": 10,
                                           "payouts": [{"place": 1, "pct": 70}, {"place": 2, "pct": 40}]})
        assert r.status_code in (400, 422)
        m.assert_not_called()

    def test_non_admin_forbidden(self):
        tok = create_token(player_id=2, role="player")
        r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": f"Bearer {tok}"},
                                 json={"entryFee": 10, "payouts": [{"place": 1, "pct": 100}]})
        assert r.status_code == 403

    def test_happy_path_stores_settings(self, admin_token):
        with patch("routes.admin_routes.set_config_settings") as m:
            r = TestClient(app).post("/api/admin/pool/config", headers={"Authorization": admin_token},
                                     json={"entryFee": 25.5,
                                           "payouts": [{"place": 1, "pct": 70}, {"place": 2, "pct": 30}]})
        assert r.status_code == 200
        m.assert_called_once()
        data = m.call_args[0][0]
        assert data["pool_entry_fee"] == 25.5
        assert data["pool_payouts"] == [{"place": 1, "pct": 70.0}, {"place": 2, "pct": 30.0}]
