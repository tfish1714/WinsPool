"""Tests for the pool fee / prize pot tracker (services/pool_service.py and routes)."""
import pandas as pd
import pytest
from unittest.mock import patch
from starlette.testclient import TestClient

from main import app
from services.pool_service import (
    build_pool_status, get_pool_config, set_pool_config, DEFAULT_ENTRY_FEE, DEFAULT_PAYOUTS,
)
from services.session_service import require_auth, create_token


def _order(n=10, paid=7, season=2026):
    rows = []
    for i in range(n):
        rows.append({"season": season, "draftOrder": i + 1, "playerId": i + 1, "paid": i < paid})
    return pd.DataFrame(rows)


def _load(order):
    return (None, None, pd.DataFrame(), None, order, pd.DataFrame(), pd.DataFrame())


def _cfg(season=2026, fee=100, payouts=None):
    p = payouts if payouts is not None else [{"place": 1, "amount": 600}, {"place": 2, "amount": 300},
                                             {"place": 3, "amount": 100}]
    return {"pool_config": {str(season): {"entry_fee": fee, "payouts": p}}}


class TestGetPoolConfig:
    def test_defaults_when_no_config(self):
        c = get_pool_config({}, 2026)
        assert c["entry_fee"] == 200
        assert c["payouts"] == [{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}]
        assert DEFAULT_ENTRY_FEE == 200

    def test_per_season_isolation(self):
        settings = _cfg(2026, fee=50)
        assert get_pool_config(settings, 2026)["entry_fee"] == 50
        assert get_pool_config(settings, 2027)["entry_fee"] == 200

    def test_invalid_data_falls_back(self):
        assert get_pool_config({"pool_config": "junk"}, 2026)["entry_fee"] == 200
        c = get_pool_config({"pool_config": {"2026": {"entry_fee": -5, "payouts": "junk"}}}, 2026)
        assert c["entry_fee"] == 200
        assert c["payouts"] == DEFAULT_PAYOUTS
        c = get_pool_config({"pool_config": {"2026": {"entry_fee": "abc",
                             "payouts": [{"place": 1, "amount": "x"}, {"place": "last", "amount": 25}]}}}, 2026)
        assert c["entry_fee"] == 200
        assert c["payouts"] == [{"place": "last", "amount": 25.0}]

    def test_missing_field_defaults_individually(self):
        c = get_pool_config({"pool_config": {"2026": {"entry_fee": 75}}}, 2026)
        assert c["entry_fee"] == 75
        assert c["payouts"] == DEFAULT_PAYOUTS


class TestSetPoolConfig:
    def test_merges_seasons(self):
        existing = _cfg(2025, fee=10)
        with patch("services.pool_service.get_config_settings", return_value=existing),              patch("services.pool_service.set_config_settings") as m:
            saved = set_pool_config(2026, 150, [{"place": 1, "amount": 1000}])
        assert saved == {"entry_fee": 150.0, "payouts": [{"place": 1, "amount": 1000.0}]}
        m.assert_called_once()
        merged = m.call_args[0][0]["pool_config"]
        assert set(merged) == {"2025", "2026"}
        assert merged["2025"]["entry_fee"] == 10
        assert merged["2026"]["entry_fee"] == 150.0


class TestBuildPoolStatus:
    def test_counts_and_pot(self):
        s = build_pool_status(_order(), _cfg(fee=100), 2026, 1)
        assert s["total_count"] == 10
        assert s["paid_count"] == 7
        assert s["total_pot"] == 1000
        assert s["collected"] == 700

    def test_payouts_dollar_amounts(self):
        s = build_pool_status(_order(), _cfg(fee=100), 2026, 1)
        assert [p["amount"] for p in s["payouts"]] == [600, 300, 100]
        assert [p["label"] for p in s["payouts"]] == ["1st", "2nd", "3rd"]
        assert s["payout_total"] == 1000
        assert s["pot_balance"] == 0

    def test_default_config_balances_at_ten_members(self):
        s = build_pool_status(_order(), {}, 2026, 1)
        assert s["entry_fee"] == 200
        assert s["total_pot"] == 2000
        assert s["payout_total"] == 2000
        assert s["pot_balance"] == 0
        assert [p["label"] for p in s["payouts"]] == ["1st", "2nd"]

    def test_last_place_label_and_sort(self):
        cfg = _cfg(fee=200, payouts=[{"place": "last", "amount": 200}, {"place": 2, "amount": 400},
                                     {"place": 1, "amount": 1400}])
        s = build_pool_status(_order(), cfg, 2026, 1)
        assert [p["label"] for p in s["payouts"]] == ["1st", "2nd", "Last place"]
        assert s["payouts"][-1]["place"] == "last"
        assert s["payout_total"] == 2000

    def test_fee_change_reports_negative_balance(self):
        cfg = _cfg(fee=150, payouts=[{"place": 1, "amount": 1400}, {"place": 2, "amount": 600}])
        s = build_pool_status(_order(), cfg, 2026, 1)
        assert s["total_pot"] == 1500
        assert s["pot_balance"] == -500

    def test_ordinal_labels(self):
        cfg = _cfg(payouts=[{"place": 4, "amount": 1}, {"place": 11, "amount": 1}, {"place": 22, "amount": 1}])
        s = build_pool_status(_order(), cfg, 2026, 1)
        assert [p["label"] for p in s["payouts"]] == ["4th", "11th", "22nd"]

    def test_my_paid_values(self):
        assert build_pool_status(_order(), {}, 2026, 1)["my_paid"] is True
        assert build_pool_status(_order(), {}, 2026, 10)["my_paid"] is False
        assert build_pool_status(_order(), {}, 2026, 999)["my_paid"] is None
        assert build_pool_status(_order(), {}, 2026, None)["my_paid"] is None

    def test_empty_order(self):
        s = build_pool_status(pd.DataFrame(), _cfg(fee=50), 2026, 1)
        assert s["total_count"] == 0 and s["paid_count"] == 0
        assert s["my_paid"] is None
        assert s["pot_balance"] == -1000

    def test_other_season_rows_ignored(self):
        df = pd.concat([_order(season=2025), _order(n=4, paid=1, season=2026)])
        s = build_pool_status(df, _cfg(fee=10), 2026, 1)
        assert s["total_count"] == 4 and s["paid_count"] == 1

    def test_invalid_config_never_raises(self):
        s = build_pool_status(_order(), {"pool_config": {"2026": {"entry_fee": -5, "payouts": "junk"}}}, 2026, 1)
        assert s["entry_fee"] == 200
        assert s["payouts"][0]["amount"] == 1400

    def test_rounds_to_cents(self):
        s = build_pool_status(_order(n=3, paid=0), _cfg(fee=33.33, payouts=[{"place": 1, "amount": 10.005}]),
                              2026, 1)
        assert s["total_pot"] == round(33.33 * 3, 2)
        assert s["payouts"][0]["amount"] == round(10.005, 2)


@pytest.fixture
def auth_as_player_3():
    app.dependency_overrides[require_auth] = lambda: {"sub": "3", "role": "player"}
    yield
    app.dependency_overrides.pop(require_auth, None)


class TestPoolStatusRoute:
    def test_no_leak_of_other_players(self, auth_as_player_3):
        client = TestClient(app)
        with patch("routes.api_routes.load_data", return_value=_load(_order())), \
             patch("routes.api_routes.get_config_settings", return_value=_cfg(fee=100)):
            resp = client.get("/api/pool/status?season=2026")
        assert resp.status_code == 200
        body = resp.json()
        assert body["my_paid"] is True
        assert body["paid_count"] == 7
        assert "playerId" not in resp.text
        assert set(body) == {"season", "entry_fee", "total_count", "paid_count",
                             "total_pot", "collected", "payouts", "payout_total",
                             "pot_balance", "my_paid"}

    def test_requires_auth(self):
        assert TestClient(app).get("/api/pool/status").status_code in (401, 403)
