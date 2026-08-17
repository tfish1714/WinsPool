"""Tests for services/betting_screener_service.py -- the Elo/spread angle
backtester behind the admin Betting tab. Pure logic, no Firestore/network."""
import math
import pandas as pd
import pytest

from services.betting_screener_service import (
    PREBUILT_ANGLES,
    _favorite_or_dog,
    _side_view,
    matches_filter,
    grade_bet,
    find_next_upcoming_week,
    screen_games,
)


class TestFavoriteOrDog:
    def test_positive_spread_is_favorite(self):
        assert _favorite_or_dog(3.5) == "favorite"

    def test_negative_spread_is_dog(self):
        assert _favorite_or_dog(-3.5) == "dog"

    def test_zero_spread_is_pickem_none(self):
        assert _favorite_or_dog(0) is None

    def test_none_spread_is_none(self):
        assert _favorite_or_dog(None) is None


class TestSideView:
    def test_flips_sign_for_away(self):
        views = _side_view(spread_line=6.0, elo_diff=42.0)
        assert views == [("home", 6.0, 42.0), ("away", -6.0, -42.0)]

    def test_handles_none_values(self):
        views = _side_view(spread_line=None, elo_diff=10.0)
        assert views == [("home", None, 10.0), ("away", None, -10.0)]


class TestMatchesFilter:
    def test_side_filter_excludes_other_side(self):
        assert not matches_filter(
            side="away", spread_for_side=3.0, elo_diff_for_side=10.0,
            f_side="home", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_side_any_matches_both(self):
        for side in ("home", "away"):
            assert matches_filter(
                side=side, spread_for_side=3.0, elo_diff_for_side=10.0,
                f_side="any", f_favorite_or_dog="any",
                f_spread_min=None, f_spread_max=None,
                f_elo_diff_min=None, f_elo_diff_max=None,
            )

    def test_favorite_or_dog_filter(self):
        # side favored by 3 -> "favorite"
        assert matches_filter(
            side="home", spread_for_side=3.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="favorite",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=3.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="dog",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_spread_range(self):
        # |spread| = 10, range [10, None] should match; [11, None] should not
        assert matches_filter(
            side="home", spread_for_side=10.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=10.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=10.0, elo_diff_for_side=None,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=11.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )

    def test_elo_diff_range(self):
        assert matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=50.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=25.0, f_elo_diff_max=None,
        )
        assert not matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=10.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=None, f_spread_max=None,
            f_elo_diff_min=25.0, f_elo_diff_max=None,
        )

    def test_null_spread_fails_a_spread_bound(self):
        assert not matches_filter(
            side="home", spread_for_side=None, elo_diff_for_side=10.0,
            f_side="any", f_favorite_or_dog="any",
            f_spread_min=3.0, f_spread_max=None,
            f_elo_diff_min=None, f_elo_diff_max=None,
        )


class TestGradeBet:
    def test_home_loss_matches_real_2025_week1_no_ari_game(self):
        # Verified against .local_db/nfl_games.pkl: NO 13 - ARI 20, spread_line=-6.0
        # (home underdog by 6). Home lost by 7, worse than getting 6 -> home does NOT cover.
        assert grade_bet("home", home_score=13, away_score=20, spread_line=-6.0) == "loss"
        assert grade_bet("away", home_score=13, away_score=20, spread_line=-6.0) == "win"

    def test_push(self):
        # home favored by 6 (spread_line=6), wins by exactly 6 -> push
        assert grade_bet("home", home_score=20, away_score=14, spread_line=6.0) == "push"

    def test_none_when_unplayed(self):
        assert grade_bet("home", home_score=None, away_score=None, spread_line=3.0) is None

    def test_none_when_spread_unknown(self):
        assert grade_bet("home", home_score=20, away_score=14, spread_line=None) is None

    def test_none_on_nan_score(self):
        assert grade_bet("home", home_score=float("nan"), away_score=14, spread_line=3.0) is None


class TestPrebuiltAngles:
    def test_all_angles_have_valid_shape(self):
        for name, angle in PREBUILT_ANGLES.items():
            assert angle["side"] in ("home", "away", "any")
            assert angle["favorite_or_dog"] in ("favorite", "dog", "any")

    def test_home_dog_and_away_favorite_are_opposite_sides_of_same_game(self):
        assert PREBUILT_ANGLES["home_dog"]["side"] == "home"
        assert PREBUILT_ANGLES["home_dog"]["favorite_or_dog"] == "dog"
        assert PREBUILT_ANGLES["away_favorite"]["side"] == "away"
        assert PREBUILT_ANGLES["away_favorite"]["favorite_or_dog"] == "favorite"

    def test_big_favorite_and_underdog_use_spread_min_10(self):
        assert PREBUILT_ANGLES["big_favorite"]["spread_min"] == 10.0
        assert PREBUILT_ANGLES["big_underdog"]["spread_min"] == 10.0


class TestFindNextUpcomingWeek:
    def _games_df(self, rows):
        return pd.DataFrame(rows)

    def test_finds_earliest_unplayed_week(self):
        df = self._games_df([
            {"season": 2026, "week": 1, "result": 3.0},
            {"season": 2026, "week": 2, "result": None},
            {"season": 2026, "week": 3, "result": None},
        ])
        assert find_next_upcoming_week(df, 2026) == 2

    def test_none_when_season_fully_complete(self):
        df = self._games_df([{"season": 2026, "week": 1, "result": 3.0}])
        assert find_next_upcoming_week(df, 2026) is None

    def test_none_when_season_missing(self):
        df = self._games_df([{"season": 2025, "week": 1, "result": 3.0}])
        assert find_next_upcoming_week(df, 2026) is None

    def test_none_when_empty(self):
        assert find_next_upcoming_week(pd.DataFrame(), 2026) is None


class TestScreenGames:
    def _predictions(self):
        return {
            2025: {
                # home favored by 6, home wins by 10 -> home covers (win)
                "W01_KC_SF": {"explanation": {"elo_diff": 80.0, "vegas_line": 6.0}},
                # home underdog by 3 (away favorite), away wins by 1 -> away does not cover (loss for away side)
                "W01_BUF_MIA": {"explanation": {"elo_diff": -20.0, "vegas_line": -3.0}},
            },
            2026: {
                # future game, no result yet -- candidate only, ungraded
                "W01_DAL_PHI": {"explanation": {"elo_diff": 15.0, "vegas_line": -2.0}},
            },
        }

    def _games_df(self):
        return pd.DataFrame([
            {"season": 2025, "week": 1, "home_team": "KC", "away_team": "SF",
             "home_score": 30, "away_score": 20},
            {"season": 2025, "week": 1, "home_team": "BUF", "away_team": "MIA",
             "home_score": 20, "away_score": 21},
            {"season": 2026, "week": 1, "home_team": "DAL", "away_team": "PHI",
             "home_score": None, "away_score": None},
        ])

    def test_backtest_tallies_only_historical_games(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        # side="any" -> KC (home, favorite, covers=win) and BUF (home, dog) both graded;
        # away sides (SF favorite-loss, MIA dog-win) also graded since side defaults to "any"
        assert result["backtest"]["n"] == 4  # 2 historical games x 2 sides each
        assert result["backtest"]["wins"] + result["backtest"]["losses"] == 4

    def test_candidates_only_from_target_week(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        seasons_weeks = {(c["season"], c["week"]) for c in result["candidates"]}
        assert seasons_weeks == {(2026, 1)}

    def test_side_any_does_not_double_count_a_single_favorite(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            side="any", favorite_or_dog="favorite",
        )
        # Exactly one favorite per historical game -> 2 games, 2 backtest entries, not 4
        assert result["backtest"]["n"] == 2

    def test_elo_filter_narrows_candidates(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            elo_diff_min=50.0,
        )
        # Only KC's home elo_diff=80 clears 50; DAL/PHI (target week) has elo_diff=15/-15 for each side
        assert result["candidates"] == []

    def test_ungraded_future_candidate_flagged_not_already_played(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        assert all(c["already_played"] is False for c in result["candidates"])
