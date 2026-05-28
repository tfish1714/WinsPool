"""tests/test_compute_elo_adjustments.py

Unit and integration tests for the bye-week / short-rest Elo adjustments
added to scripts/compute_elo.py.
"""
import sys
import pathlib
import pytest
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import scripts.compute_elo as elo_module
from scripts.compute_elo import _rest_adj, _update_game, _expected, BYE_BONUS, SHORT_REST_PENALTY


# ---------------------------------------------------------------------------
# _rest_adj
# ---------------------------------------------------------------------------

class TestRestAdj:

    def test_bye_week_returns_bonus(self):
        """week_gap >= 2 (skipped a schedule week) → BYE_BONUS."""
        assert _rest_adj(week_gap=2, rest=14) == BYE_BONUS
        assert _rest_adj(week_gap=3, rest=21) == BYE_BONUS

    def test_bye_with_low_rest_still_returns_bonus(self):
        """Monday game → bye → Thursday is week_gap=2 but rest≈10; bye wins."""
        assert _rest_adj(week_gap=2, rest=10) == BYE_BONUS

    def test_short_rest_returns_penalty(self):
        """rest <= 4, no bye → -SHORT_REST_PENALTY."""
        assert _rest_adj(week_gap=1, rest=4) == -SHORT_REST_PENALTY
        assert _rest_adj(week_gap=1, rest=3) == -SHORT_REST_PENALTY

    def test_normal_rest_returns_zero(self):
        """Standard 7-day rest with no bye → no adjustment."""
        assert _rest_adj(week_gap=1, rest=7) == 0.0

    def test_thursday_after_monday_no_bye(self):
        """Thursday game after Monday Night Football is rest=10, week_gap=1 → no adjustment."""
        assert _rest_adj(week_gap=1, rest=10) == 0.0


# ---------------------------------------------------------------------------
# _update_game with rest params
# ---------------------------------------------------------------------------

class TestUpdateGameWithRest:

    def _baseline_E(self):
        """Expected win prob for two even teams with no rest adjustment."""
        _, _, E = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=7, away_rest=7,
        )
        return E

    def test_home_bye_increases_expected(self):
        """Home team coming off bye → higher E than no adjustment."""
        E_base = self._baseline_E()
        _, _, E_bye = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=2, away_week_gap=1,
            home_rest=14, away_rest=7,
        )
        assert E_bye > E_base

    def test_away_bye_decreases_expected(self):
        """Away team coming off bye → lower E (away is stronger) for home team."""
        E_base = self._baseline_E()
        _, _, E_away_bye = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=2,
            home_rest=7, away_rest=14,
        )
        assert E_away_bye < E_base

    def test_both_short_rest_cancels(self):
        """Both teams on 4-day rest (Thanksgiving) → E unchanged."""
        E_base = self._baseline_E()
        _, _, E_both = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=4, away_rest=4,
        )
        assert abs(E_both - E_base) < 0.001

    def test_home_short_rest_decreases_expected(self):
        """Home team on 4-day rest → lower E."""
        E_base = self._baseline_E()
        _, _, E_short = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=4, away_rest=7,
        )
        assert E_short < E_base

    def test_bye_win_earns_less_elo(self):
        """Post-bye home win earns fewer Elo points (win was more expected)."""
        home_normal, _, _ = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=1, away_week_gap=1,
            home_rest=7, away_rest=7,
        )
        home_bye, _, _ = _update_game(
            1500.0, 1500.0, 28, 21, None,
            home_week_gap=2, away_week_gap=1,
            home_rest=14, away_rest=7,
        )
        assert home_bye < home_normal

    def test_defaults_match_baseline(self):
        """Calling _update_game without rest kwargs should behave as no adjustment."""
        _, _, E_default = _update_game(1500.0, 1500.0, 28, 21, None)
        E_base = self._baseline_E()
        assert abs(E_default - E_base) < 0.001


# ---------------------------------------------------------------------------
# Integration: compute_elo() week-gap detection
# ---------------------------------------------------------------------------

class TestComputeEloWeekGap:

    def _make_games_csv(self, file_path, rows):
        """Write a minimal games CSV at the given file path."""
        import pathlib
        p = pathlib.Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        required_cols = [
            "game_id", "season", "game_type", "week",
            "home_team", "away_team", "home_score", "away_score",
            "home_rest", "away_rest",
        ]
        df = pd.DataFrame(rows)
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
        df.to_csv(p, index=False)
        return p

    def test_bye_week_changes_home_exp(self, tmp_path, monkeypatch):
        """
        Two identical 3-game schedules: one where KC has a bye before week 3,
        one where KC plays every week. The bye-week game should have a higher
        home_exp for KC (home team) than the non-bye version.
        """
        base_rows = [
            # Week 1: KC (home) beats BUF — same in both schedules
            dict(game_id="w1", season=2024, game_type="REG", week=1,
                 home_team="KC", away_team="BUF",
                 home_score=28, away_score=21, home_rest=7, away_rest=7),
        ]

        # Schedule A: KC plays week 2 then week 3 (no bye, week_gap=1 at week 3).
        # KC loses week 2 so their Elo stays lower than the bye-bonus can cover;
        # this isolates the bye effect rather than confounding it with extra wins.
        rows_no_bye = base_rows + [
            dict(game_id="w2a", season=2024, game_type="REG", week=2,
                 home_team="BUF", away_team="KC",
                 home_score=24, away_score=17, home_rest=7, away_rest=7),
            dict(game_id="w3a", season=2024, game_type="REG", week=3,
                 home_team="KC", away_team="BUF",
                 home_score=21, away_score=17, home_rest=7, away_rest=7),
        ]

        # Schedule B: KC skips week 2 (bye), then plays week 3 (week_gap=2)
        rows_with_bye = base_rows + [
            dict(game_id="w2b", season=2024, game_type="REG", week=2,
                 home_team="BUF", away_team="DET",   # KC not in this game
                 home_score=17, away_score=14, home_rest=7, away_rest=7),
            dict(game_id="w3b", season=2024, game_type="REG", week=3,
                 home_team="KC", away_team="BUF",
                 home_score=21, away_score=17, home_rest=14, away_rest=7),
        ]

        csv_no_bye  = self._make_games_csv(tmp_path / "no_bye_games.csv",  rows_no_bye)
        csv_with_bye = self._make_games_csv(tmp_path / "with_bye_games.csv", rows_with_bye)
        # Point QUARTER_CSV at a non-existent path so quarter updates are skipped
        monkeypatch.setattr(elo_module, "QUARTER_CSV", tmp_path / "quarter_scores.csv")

        monkeypatch.setattr(elo_module, "GAMES_CSV", csv_no_bye)
        df_no_bye, _ = elo_module.compute_elo(min_season=2024, max_season=2024)
        E_no_bye = df_no_bye[df_no_bye["week"] == 3]["home_exp"].iloc[0]

        monkeypatch.setattr(elo_module, "GAMES_CSV", csv_with_bye)
        df_with_bye, _ = elo_module.compute_elo(min_season=2024, max_season=2024)
        E_with_bye = df_with_bye[df_with_bye["week"] == 3]["home_exp"].iloc[0]

        assert E_with_bye > E_no_bye, (
            f"Bye-week home_exp ({E_with_bye:.4f}) should exceed "
            f"no-bye home_exp ({E_no_bye:.4f})"
        )
