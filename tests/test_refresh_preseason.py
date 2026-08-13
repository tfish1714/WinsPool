"""Freshness preflight and projection diff. No test may hit the network."""
import json

import pytest

from scripts.refresh_preseason import (
    STEPS, check_asset_freshness, diff_projections,
)


def _fake_fetch(assets):
    def _fetch(tag):
        return {"assets": assets}
    return _fetch


def test_reports_remote_newer_than_local(tmp_path):
    local = tmp_path / "depth_charts_2026.csv"
    local.write_text("x")
    import os, time
    old = time.time() - 90 * 86400
    os.utime(local, (old, old))

    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", local,
        fetch=_fake_fetch([{"name": "depth_charts_2026.csv",
                            "updated_at": "2026-08-11T08:09:03Z"}]),
    )
    assert res["remote_updated_at"] == "2026-08-11T08:09:03Z"
    assert res["status"] == "stale"


def test_missing_remote_asset_is_reported_not_fatal(tmp_path):
    res = check_asset_freshness(
        "snap_counts", "snap_counts_2026.csv", tmp_path / "nope.csv",
        fetch=_fake_fetch([{"name": "snap_counts_2025.csv",
                            "updated_at": "2026-02-09T13:39:51Z"}]),
    )
    assert res["status"] == "absent"
    assert res["remote_updated_at"] is None


def test_unreachable_api_does_not_raise(tmp_path):
    def _boom(tag):
        raise OSError("network down")

    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", tmp_path / "nope.csv", fetch=_boom
    )
    assert res["status"] == "unknown"


def test_diff_projections_sorts_by_absolute_movement():
    before = {"LA": 12.6, "BUF": 10.3, "KC": 9.5}
    after = {"LA": 13.1, "BUF": 10.2, "KC": 11.9}
    rows = diff_projections(before, after)
    assert [r["team"] for r in rows] == ["KC", "LA", "BUF"]
    assert rows[0]["change"] == pytest.approx(2.4)


def test_diff_handles_new_and_dropped_teams():
    rows = diff_projections({"LA": 12.0}, {"LA": 12.0, "NEW": 8.0})
    teams = {r["team"]: r for r in rows}
    assert teams["NEW"]["before"] is None


def test_required_steps_are_marked():
    by_name = {s["name"]: s for s in STEPS}
    assert by_name["Elo Recompute"]["required"] is True
    assert by_name["Season Projection"]["required"] is True
    assert by_name["nflverse Raw Data Sync"]["required"] is False
