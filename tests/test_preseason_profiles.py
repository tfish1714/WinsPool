"""tests/test_preseason_profiles.py -- Tests for compute_preseason_player_profiles()."""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fake_player_stats() -> pd.DataFrame:
    """Minimal player_stats rows (REG season only)."""
    return pd.DataFrame([
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "AAA", "season_type": "REG", "season": 2025,
         "passing_epa": 80.0, "rushing_epa": 5.0, "receiving_epa": 0.0,
         "attempts": 400, "carries": 30, "targets": 0},
        {"player_id": "00-0002", "player_display_name": "WR Beta", "position": "WR",
         "recent_team": "AAA", "season_type": "REG", "season": 2025,
         "passing_epa": 0.0, "rushing_epa": 0.0, "receiving_epa": 60.0,
         "attempts": 0, "carries": 0, "targets": 100},
        {"player_id": "00-0003", "player_display_name": "RB Gamma", "position": "RB",
         "recent_team": "BBB", "season_type": "REG", "season": 2025,
         "passing_epa": 0.0, "rushing_epa": 30.0, "receiving_epa": 5.0,
         "attempts": 0, "carries": 150, "targets": 20},
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "AAA", "season_type": "POST", "season": 2025,
         "passing_epa": 20.0, "rushing_epa": 2.0, "receiving_epa": 0.0,
         "attempts": 80, "carries": 5, "targets": 0},
    ])


# ── _load_player_epa tests ─────────────────────────────────────────────────────

class TestLoadPlayerEpa:
    def test_filters_to_reg_only(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        stats = _fake_player_stats()
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        # POST row for QB Alpha should be excluded
        assert len(result) == 3

    def test_aggregates_per_player_season_totals(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        # Two REG rows for same player (two weeks)
        stats = pd.DataFrame([
            {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
             "recent_team": "AAA", "season_type": "REG", "season": 2025,
             "passing_epa": 40.0, "rushing_epa": 2.0, "receiving_epa": 0.0,
             "attempts": 200, "carries": 10, "targets": 0},
            {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
             "recent_team": "AAA", "season_type": "REG", "season": 2025,
             "passing_epa": 40.0, "rushing_epa": 3.0, "receiving_epa": 0.0,
             "attempts": 200, "carries": 10, "targets": 0},
        ])
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        qb = result[result["player_id"] == "00-0001"].iloc[0]
        assert qb["passing_epa"] == pytest.approx(80.0)
        assert qb["attempts"] == 400

    def test_returns_empty_df_if_file_missing(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        result = _load_player_epa(2025, tmp_path)
        assert result.empty

    def test_computes_epa_per_play_rates(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        stats = _fake_player_stats()
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        qb = result[result["player_id"] == "00-0001"].iloc[0]
        # 80 EPA / 400 attempts = 0.2 EPA per dropback
        assert qb["pass_epa_rate"] == pytest.approx(0.2)
        wr = result[result["player_id"] == "00-0002"].iloc[0]
        # 60 EPA / 100 targets = 0.6 EPA per target
        assert wr["recv_epa_rate"] == pytest.approx(0.6)


# ── Fixtures shared across offense/defense tests ──────────────────────────────

def _fake_depth_chart() -> pd.DataFrame:
    """Minimal 2026 depth chart for teams AAA and BBB."""
    rows = [
        # AAA offense
        {"team": "AAA", "pos_abb": "QB", "pos_rank": 1, "player_name": "QB Alpha",  "gsis_id": "00-0001"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 1, "player_name": "WR Beta",   "gsis_id": "00-0002"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 2, "player_name": "WR Zeta",   "gsis_id": "00-0010"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 3, "player_name": "WR Kappa",  "gsis_id": "00-0011"},
        {"team": "AAA", "pos_abb": "TE", "pos_rank": 1, "player_name": "TE Delta",  "gsis_id": "00-0012"},
        {"team": "AAA", "pos_abb": "TE", "pos_rank": 2, "player_name": "TE Eta",    "gsis_id": "00-0013"},
        {"team": "AAA", "pos_abb": "RB", "pos_rank": 1, "player_name": "RB Gamma",  "gsis_id": "00-0003"},
        {"team": "AAA", "pos_abb": "RB", "pos_rank": 2, "player_name": "RB Theta",  "gsis_id": "00-0014"},
        # OL for AAA
        {"team": "AAA", "pos_abb": "LT", "pos_rank": 1, "player_name": "OL1", "gsis_id": "00-0020"},
        {"team": "AAA", "pos_abb": "LG", "pos_rank": 1, "player_name": "OL2", "gsis_id": "00-0021"},
        {"team": "AAA", "pos_abb": "C",  "pos_rank": 1, "player_name": "OL3", "gsis_id": "00-0022"},
        {"team": "AAA", "pos_abb": "RG", "pos_rank": 1, "player_name": "OL4", "gsis_id": "00-0023"},
        {"team": "AAA", "pos_abb": "RT", "pos_rank": 1, "player_name": "OL5", "gsis_id": "00-0024"},
    ]
    return pd.DataFrame(rows)


def _fake_player_epa() -> pd.DataFrame:
    """Aggregated player EPA rates (output of _load_player_epa)."""
    return pd.DataFrame([
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "OLD", "pass_epa_rate": 0.20, "recv_epa_rate": 0.0, "rush_epa_rate": 0.05,
         "passing_epa": 80.0, "receiving_epa": 0.0, "rushing_epa": 5.0,
         "attempts": 400, "targets": 0, "carries": 100},
        {"player_id": "00-0002", "player_display_name": "WR Beta", "position": "WR",
         "recent_team": "AAA", "pass_epa_rate": 0.0, "recv_epa_rate": 0.60, "rush_epa_rate": 0.0,
         "passing_epa": 0.0, "receiving_epa": 60.0, "rushing_epa": 0.0,
         "attempts": 0, "targets": 100, "carries": 0},
        {"player_id": "00-0003", "player_display_name": "RB Gamma", "position": "RB",
         "recent_team": "BBB", "pass_epa_rate": 0.0, "recv_epa_rate": 0.15, "rush_epa_rate": 0.10,
         "passing_epa": 0.0, "receiving_epa": 3.0, "rushing_epa": 15.0,
         "attempts": 0, "targets": 20, "carries": 150},
    ])


def _fake_roster() -> pd.DataFrame:
    """Minimal roster with age/pfr_id for OL players."""
    return pd.DataFrame([
        {"gsis_id": "00-0001", "pfr_id": "AlphQB00", "full_name": "QB Alpha", "position": "QB",
         "birth_date": "1995-01-01", "years_exp": 5},
        {"gsis_id": "00-0020", "pfr_id": "OL000001", "full_name": "OL1", "position": "T",
         "birth_date": "1997-06-15", "years_exp": 3},
        {"gsis_id": "00-0021", "pfr_id": "OL000002", "full_name": "OL2", "position": "G",
         "birth_date": "1996-03-20", "years_exp": 4},
        {"gsis_id": "00-0022", "pfr_id": "OL000003", "full_name": "OL3", "position": "C",
         "birth_date": "1994-09-10", "years_exp": 6},
        {"gsis_id": "00-0023", "pfr_id": "OL000004", "full_name": "OL4", "position": "G",
         "birth_date": "1998-02-28", "years_exp": 2},
        {"gsis_id": "00-0024", "pfr_id": "OL000005", "full_name": "OL5", "position": "T",
         "birth_date": "1993-11-05", "years_exp": 7},
    ])


def _fake_snap_counts() -> pd.DataFrame:
    """Minimal snap counts for OL players."""
    rows = []
    for pid, name in [("OL000001","OL1"),("OL000002","OL2"),("OL000003","OL3"),
                      ("OL000004","OL4"),("OL000005","OL5")]:
        rows.append({"pfr_player_id": pid, "player": name, "position": "OL",
                     "team": "AAA", "offense_snaps": 900, "defense_snaps": 0,
                     "game_type": "REG"})
    return pd.DataFrame(rows)


# ── _preseason_offense tests ───────────────────────────────────────────────────

class TestPreseasonOffense:
    def test_returns_expected_teams(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        assert "AAA" in result

    def test_off_pass_epa_influenced_by_qb(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # QB Alpha has pass_epa_rate = 0.20 (good QB) → AAA off_pass_epa should be positive
        assert result["AAA"]["off_pass_epa"] > 0.0

    def test_qb_tier_matches_qb_pass_epa_rate(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # qb_tier = starter's pass_epa_rate
        assert result["AAA"]["qb_tier"] == pytest.approx(0.20)

    def test_rookie_qb_gets_discount(self):
        from services.nn_feature_engine import _preseason_offense
        # Depth chart with unknown QB (no player_epa match → rookie discount)
        dc = pd.DataFrame([
            {"team": "BBB", "pos_abb": "QB", "pos_rank": 1,
             "player_name": "Rookie QB", "gsis_id": "99-9999"},
        ])
        result = _preseason_offense(
            dc, _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # Should use league_avg × 0.75 (not crash)
        assert "BBB" in result
        assert result["BBB"]["qb_tier"] > 0  # league avg × 0.75 is still > 0

    def test_output_has_required_keys(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        for key in ("off_pass_epa", "off_rush_epa", "qb_tier", "ol_av"):
            assert key in result["AAA"], f"Missing key: {key}"
