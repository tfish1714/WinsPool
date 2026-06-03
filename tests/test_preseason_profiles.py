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
