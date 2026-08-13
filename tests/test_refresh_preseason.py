"""Freshness preflight and projection diff. No test may hit the network."""
import json

import pytest

from scripts.refresh_preseason import (
    STEPS, check_asset_freshness, diff_projections, diff_is_trustworthy,
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


def test_asset_present_but_missing_updated_at_does_not_raise(tmp_path):
    # A successful-but-malformed response: the asset is there, "updated_at" is not.
    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", tmp_path / "nope.csv",
        fetch=_fake_fetch([{"name": "depth_charts_2026.csv"}]),
    )
    assert res["status"] == "unknown"
    assert res["remote_updated_at"] is None


def test_asset_with_unparseable_timestamp_does_not_raise(tmp_path):
    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", tmp_path / "nope.csv",
        fetch=_fake_fetch([{"name": "depth_charts_2026.csv",
                            "updated_at": "not-a-timestamp"}]),
    )
    assert res["status"] == "unknown"
    assert res["remote_updated_at"] is None


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


def test_sync_and_elo_steps_target_the_requested_season():
    # Regression: both steps used to run with no season args, silently defaulting
    # to whatever the *current* season is (sync_nflverse_data.py) or a hardcoded
    # 2025 (compute_elo.py) instead of the season actually being refreshed.
    by_name = {s["name"]: s for s in STEPS}
    assert by_name["nflverse Raw Data Sync"]["args"] == ["--season", "{season}"]
    assert by_name["Elo Recompute"]["args"] == ["--max-season", "{season}"]


def test_diff_not_trustworthy_when_mirror_refresh_fails():
    assert diff_is_trustworthy({"Local Mirror Refresh": False}) is False


def test_diff_trustworthy_when_mirror_refresh_succeeds_or_did_not_run():
    assert diff_is_trustworthy({"Local Mirror Refresh": True}) is True
    # Step never ran (e.g. --skip-sync doesn't affect this one, but be permissive
    # about absence in general) -- absence must not be misread as failure.
    assert diff_is_trustworthy({}) is True
