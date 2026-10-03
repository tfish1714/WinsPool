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
