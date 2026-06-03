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


def _fake_def_advstats() -> pd.DataFrame:
    """Minimal advstats_week_def for DL/LB/CB players."""
    return pd.DataFrame([
        # DL (good pass rusher)
        {"pfr_player_id": "DEF00001", "pfr_player_name": "DE Star",
         "game_type": "REG", "def_sacks": 12.0, "def_pressures": 40.0,
         "def_times_hitqb": 15.0, "def_tackles_combined": 30.0,
         "def_targets": 0.0, "def_yards_allowed_per_tgt": 0.0,
         "def_passer_rating_allowed": 0.0},
        # CB (good coverage — low yards/target allowed)
        {"pfr_player_id": "DEF00002", "pfr_player_name": "CB Good",
         "game_type": "REG", "def_sacks": 0.0, "def_pressures": 0.0,
         "def_times_hitqb": 0.0, "def_tackles_combined": 20.0,
         "def_targets": 60.0, "def_yards_allowed_per_tgt": 5.0,
         "def_passer_rating_allowed": 70.0},
        # CB (bad coverage — high yards/target allowed)
        {"pfr_player_id": "DEF00003", "pfr_player_name": "CB Bad",
         "game_type": "REG", "def_sacks": 0.0, "def_pressures": 0.0,
         "def_times_hitqb": 0.0, "def_tackles_combined": 20.0,
         "def_targets": 60.0, "def_yards_allowed_per_tgt": 12.0,
         "def_passer_rating_allowed": 110.0},
    ])


def _fake_def_snap_counts() -> pd.DataFrame:
    return pd.DataFrame([
        {"pfr_player_id": "DEF00001", "player": "DE Star",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 700},
        {"pfr_player_id": "DEF00002", "player": "CB Good",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 600},
        {"pfr_player_id": "DEF00003", "player": "CB Bad",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 580},
    ])


def _fake_def_depth_chart() -> pd.DataFrame:
    return pd.DataFrame([
        # DL
        {"team": "AAA", "pos_abb": "LDE", "pos_rank": 1,
         "player_name": "DE Star", "gsis_id": "00-0030"},
        {"team": "AAA", "pos_abb": "RDE", "pos_rank": 1,
         "player_name": "DE Backup", "gsis_id": "00-0031"},
        # CB
        {"team": "AAA", "pos_abb": "LCB", "pos_rank": 1,
         "player_name": "CB Good", "gsis_id": "00-0040"},
        {"team": "AAA", "pos_abb": "RCB", "pos_rank": 1,
         "player_name": "CB Bad",  "gsis_id": "00-0041"},
        # SS/FS
        {"team": "AAA", "pos_abb": "SS", "pos_rank": 1,
         "player_name": "Safety One", "gsis_id": "00-0050"},
    ])


def _fake_def_roster() -> pd.DataFrame:
    return pd.DataFrame([
        {"gsis_id": "00-0030", "pfr_id": "DEF00001", "full_name": "DE Star",
         "position": "DE", "birth_date": "1997-01-01", "years_exp": 4},
        {"gsis_id": "00-0040", "pfr_id": "DEF00002", "full_name": "CB Good",
         "position": "CB", "birth_date": "1998-06-01", "years_exp": 3},
        {"gsis_id": "00-0041", "pfr_id": "DEF00003", "full_name": "CB Bad",
         "position": "CB", "birth_date": "1999-01-01", "years_exp": 2},
    ])


class TestPreseasonDefense:
    def test_returns_expected_teams(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        assert "AAA" in result

    def test_output_has_required_keys(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        for key in ("def_pass_epa", "def_rush_epa", "dl_perf"):
            assert key in result["AAA"], f"Missing key: {key}"

    def test_good_dl_produces_positive_dl_perf(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        # DE Star has 12 sacks + 40 pressures → high dl_perf
        assert result["AAA"]["dl_perf"] > 0

    def test_good_cb_improves_def_pass_epa(self):
        from services.nn_feature_engine import _preseason_defense
        # Build two teams: AAA with good CB, BBB with bad CB
        dc_good = pd.DataFrame([
            {"team": "AAA", "pos_abb": "LCB", "pos_rank": 1,
             "player_name": "CB Good", "gsis_id": "00-0040"},
        ])
        dc_bad = pd.DataFrame([
            {"team": "BBB", "pos_abb": "LCB", "pos_rank": 1,
             "player_name": "CB Bad", "gsis_id": "00-0041"},
        ])
        dc = pd.concat([dc_good, dc_bad], ignore_index=True)
        result = _preseason_defense(
            dc, _fake_def_advstats(), _fake_def_roster(),
            _fake_def_snap_counts(), season=2026
        )
        # Good CB → better (lower) def_pass_epa (less EPA allowed)
        assert result["AAA"]["def_pass_epa"] < result["BBB"]["def_pass_epa"]

    def test_missing_player_does_not_crash(self):
        from services.nn_feature_engine import _preseason_defense
        dc = pd.DataFrame([
            {"team": "CCC", "pos_abb": "LDE", "pos_rank": 1,
             "player_name": "Unknown DE", "gsis_id": "99-9999"},
        ])
        result = _preseason_defense(
            dc, _fake_def_advstats(), _fake_def_roster(),
            _fake_def_snap_counts(), season=2026
        )
        assert "CCC" in result


class TestComputePreseasonPlayerProfiles:
    def _write_files(self, tmp_path, season=2026):
        prior = season - 1
        (tmp_path / "stats_player").mkdir(exist_ok=True)
        _fake_player_stats().to_csv(
            tmp_path / "stats_player" / f"stats_player_regpost_{prior}.csv", index=False)

        (tmp_path / "depth_charts").mkdir(exist_ok=True)
        dc = pd.concat([_fake_depth_chart(), _fake_def_depth_chart()], ignore_index=True)
        dc.to_csv(tmp_path / "depth_charts" / f"depth_charts_{season}.csv", index=False)

        (tmp_path / "rosters").mkdir(exist_ok=True)
        pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True).to_csv(
            tmp_path / "rosters" / f"roster_{season}.csv", index=False)

        (tmp_path / "pfr_advstats").mkdir(exist_ok=True)
        _fake_def_advstats().to_csv(
            tmp_path / "pfr_advstats" / f"advstats_week_def_{prior}.csv", index=False)

        (tmp_path / "snap_counts").mkdir(exist_ok=True)
        pd.concat([_fake_snap_counts(), _fake_def_snap_counts()], ignore_index=True).to_csv(
            tmp_path / "snap_counts" / f"snap_counts_{prior}.csv", index=False)

    def test_returns_dict_with_teams(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path)
        assert isinstance(result, dict)
        assert "AAA" in result

    def test_all_required_keys_present(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path)
        for key in ("off_pass_epa", "off_rush_epa", "def_pass_epa",
                    "def_rush_epa", "ol_av", "dl_perf", "qb_tier"):
            assert key in result["AAA"], f"Missing: {key}"

    def test_epa_values_are_floats(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path)
        for key in ("off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa"):
            assert isinstance(result["AAA"][key], float), f"{key} is not float"

    def test_returns_empty_dict_if_files_missing(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        result = compute_preseason_player_profiles(2026, tmp_path)
        assert result == {}


class TestNNProjectionEngineInitialize:
    def test_preseason_profiles_set_when_snap_empty(self, tmp_path, monkeypatch):
        """When no 2026 snap data exists, initialize() should set _preseason_profiles."""
        import services.nn_projection_engine as eng_mod
        from unittest.mock import patch

        fake_profiles = {"KC": {"off_pass_epa": 0.1, "off_rush_epa": 0.05,
                                "def_pass_epa": -0.1, "def_rush_epa": -0.05,
                                "ol_av": 1200.0, "dl_perf": 80.0, "qb_tier": 0.18}}

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"), \
             patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch("services.nn_projection_engine.compute_preseason_player_profiles",
                   return_value=fake_profiles) as mock_fn, \
             patch.object(eng_mod.NNProjectionEngine, "_build_team_profiles",
                          return_value=pd.DataFrame()):
            engine = eng_mod.NNProjectionEngine()
            engine.initialize(2026)

        mock_fn.assert_called_once()
        assert hasattr(engine, "_preseason_profiles")
        assert engine._preseason_profiles == fake_profiles

    def test_preseason_roster_and_norm_not_set(self, tmp_path, monkeypatch):
        """_preseason_roster and _preseason_norm should not be set after initialize()."""
        import services.nn_projection_engine as eng_mod
        from unittest.mock import patch

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"), \
             patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch("services.nn_projection_engine.compute_preseason_player_profiles",
                   return_value={}), \
             patch.object(eng_mod.NNProjectionEngine, "_build_team_profiles",
                          return_value=pd.DataFrame()):
            engine = eng_mod.NNProjectionEngine()
            engine.initialize(2026)

        assert not hasattr(engine, "_preseason_roster") or engine._preseason_roster == {}
        assert not hasattr(engine, "_preseason_norm") or engine._preseason_norm is None
