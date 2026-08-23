"""Unit tests for scripts/backfill_schedule_predictions.py::_build_predictions_map().

Covers two fixes:
1. The MC simulation (NNProjectionEngine.initialize()/simulate_season()) must be
   skipped entirely when every scheduled game for the year already has a locked
   feature-table prediction -- running it anyway is wasted work and, for years
   <= 2020, always logs noisy warnings from an invalid feature-table range.
2. The explanation payload's off_roster_value/def_roster_value must come from
   NNProjectionEngine.lookup_roster_value(), not a hardcoded 0.0.
"""
import sys
import pathlib
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

import scripts.backfill_schedule_predictions as bsp  # noqa: E402


def _schedule_row(home, away, week, season=2025):
    return {
        "season": season, "week": week, "home_team": home, "away_team": away,
        "game_type": "REG", "spread_line": None,
    }


class TestSkipsSimulationWhenFullyLocked:
    def test_skips_engine_when_every_game_already_locked(self):
        schedule_df = pd.DataFrame([_schedule_row("CAR", "CHI", 1)])
        ft_lookup = {
            (2025, 1, "CAR", "CHI"): {
                "pred_prob": 0.55, "pred_winner": "CAR", "pred_su_conf": 55.0,
            },
        }
        with patch.object(bsp, "NNProjectionEngine") as MockEngine, \
             patch.object(bsp, "get_game_predictions", return_value={}):
            result = bsp._build_predictions_map(
                2025, ft_lookup, schedule_df, pd.DataFrame(), force=True,
            )

        assert not MockEngine.called, (
            "NNProjectionEngine should never be constructed when every "
            "scheduled game is already covered by a locked feature-table prediction"
        )
        assert result["W01_CAR_CHI"]["locked"] is True

    def test_runs_engine_when_any_game_unlocked(self):
        schedule_df = pd.DataFrame([
            _schedule_row("CAR", "CHI", 1),
            _schedule_row("KC", "BUF", 2),
        ])
        ft_lookup = {
            (2025, 1, "CAR", "CHI"): {
                "pred_prob": 0.55, "pred_winner": "CAR", "pred_su_conf": 55.0,
            },
        }
        fake_engine = MagicMock()
        fake_engine._team_profiles = pd.DataFrame(columns=["team"])
        fake_engine.lookup_roster_value.return_value = {}
        fake_engine.simulate_season.return_value = {
            "game_probs": {
                "W02_KC_BUF": {
                    "home_team": "KC", "away_team": "BUF", "week": 2,
                    "mean_prob": 0.6, "model_spread": -2.5,
                },
            },
        }
        with patch.object(bsp, "NNProjectionEngine", return_value=fake_engine) as MockEngine, \
             patch.object(bsp, "get_game_predictions", return_value={}):
            result = bsp._build_predictions_map(
                2025, ft_lookup, schedule_df, pd.DataFrame(), force=True,
            )

        assert MockEngine.called
        fake_engine.initialize.assert_called_once_with(2025)
        # Locked entry preserved untouched, unlocked entry filled from the sim.
        assert result["W01_CAR_CHI"]["locked"] is True
        assert result["W02_KC_BUF"]["locked"] is False
        assert result["W02_KC_BUF"]["pred_winner"] == "KC"


class TestExplanationRosterValue:
    def test_off_and_def_roster_value_come_from_lookup_not_hardcoded_zero(self):
        schedule_df = pd.DataFrame([_schedule_row("KC", "BUF", 2)])
        ft_lookup = {}  # nothing locked -> simulation always runs

        fake_engine = MagicMock()
        fake_engine._team_profiles = pd.DataFrame(columns=["team"])

        def _fake_lookup(team, week):
            return {
                "KC":  {"off_roster_value": 2.0, "def_roster_value": 1.5},
                "BUF": {"off_roster_value": -1.0, "def_roster_value": -0.5},
            }.get(team, {})

        fake_engine.lookup_roster_value.side_effect = _fake_lookup
        fake_engine.simulate_season.return_value = {
            "game_probs": {
                "W02_KC_BUF": {
                    "home_team": "KC", "away_team": "BUF", "week": 2,
                    "mean_prob": 0.6, "model_spread": -2.5,
                },
            },
        }
        with patch.object(bsp, "NNProjectionEngine", return_value=fake_engine), \
             patch.object(bsp, "get_game_predictions", return_value={}):
            result = bsp._build_predictions_map(
                2025, ft_lookup, schedule_df, pd.DataFrame(), force=True,
            )

        explanation = result["W02_KC_BUF"]["explanation"]
        assert explanation["off_roster_value"] == pytest.approx(3.0)   # 2.0 - (-1.0)
        assert explanation["def_roster_value"] == pytest.approx(2.0)   # 1.5 - (-0.5)

    def test_defaults_to_zero_when_lookup_returns_empty(self):
        """Graceful degradation: a team with no roster-value entry at all
        (lookup_roster_value returns {}) must not crash -- falls back to 0.0,
        same as every other _pf(d, col, default) usage in this function."""
        schedule_df = pd.DataFrame([_schedule_row("KC", "BUF", 2)])
        ft_lookup = {}

        fake_engine = MagicMock()
        fake_engine._team_profiles = pd.DataFrame(columns=["team"])
        fake_engine.lookup_roster_value.return_value = {}
        fake_engine.simulate_season.return_value = {
            "game_probs": {
                "W02_KC_BUF": {
                    "home_team": "KC", "away_team": "BUF", "week": 2,
                    "mean_prob": 0.6, "model_spread": -2.5,
                },
            },
        }
        with patch.object(bsp, "NNProjectionEngine", return_value=fake_engine), \
             patch.object(bsp, "get_game_predictions", return_value={}):
            result = bsp._build_predictions_map(
                2025, ft_lookup, schedule_df, pd.DataFrame(), force=True,
            )

        explanation = result["W02_KC_BUF"]["explanation"]
        assert explanation["off_roster_value"] == pytest.approx(0.0)
        assert explanation["def_roster_value"] == pytest.approx(0.0)
