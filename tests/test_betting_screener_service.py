"""Tests for services/betting_screener_service.py -- the Elo/spread/model angle
backtester behind the admin Betting tab. Pure logic, no Firestore/network."""
import pandas as pd
import pytest

from services.betting_screener_service import (
    FILTERABLE_FEATURES,
    PREBUILT_ANGLES,
    _favorite_or_dog,
    _feature_row,
    _flip_row,
    matches_filters,
    side_matches,
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


class TestFeatureRow:
    def test_reads_known_features_from_explanation(self):
        row = _feature_row({"elo_diff": 42.0, "vegas_line": 6.0, "model_spread": 5.0})
        assert row["elo_diff"] == 42.0
        assert row["spread_line"] == 6.0
        assert row["model_spread"] == 5.0
        # Features missing from explanation come back as None, not KeyError
        assert row["edge_vs_vegas"] is None

    def test_includes_every_filterable_feature_key(self):
        row = _feature_row({})
        assert set(row.keys()) == set(FILTERABLE_FEATURES.keys())


class TestFlipRow:
    def test_negates_every_value(self):
        row = _flip_row({"elo_diff": 42.0, "spread_line": -6.0})
        assert row == {"elo_diff": -42.0, "spread_line": 6.0}

    def test_preserves_none(self):
        row = _flip_row({"elo_diff": None, "spread_line": -6.0})
        assert row == {"elo_diff": None, "spread_line": 6.0}


class TestMatchesFilters:
    def test_empty_filters_always_match(self):
        assert matches_filters({"elo_diff": 10.0}, [])

    def test_min_bound(self):
        row = {"elo_diff": 50.0}
        assert matches_filters(row, [{"feature": "elo_diff", "min": 25.0}])
        assert not matches_filters(row, [{"feature": "elo_diff", "min": 75.0}])

    def test_max_bound(self):
        row = {"elo_diff": 50.0}
        assert matches_filters(row, [{"feature": "elo_diff", "max": 75.0}])
        assert not matches_filters(row, [{"feature": "elo_diff", "max": 25.0}])

    def test_null_value_fails_a_bound(self):
        row = {"elo_diff": None}
        assert not matches_filters(row, [{"feature": "elo_diff", "min": 3.0}])

    def test_multiple_filters_are_and_combined(self):
        row = {"elo_diff": 50.0, "edge_vs_vegas": 2.0}
        assert matches_filters(row, [
            {"feature": "elo_diff", "min": 25.0},
            {"feature": "edge_vs_vegas", "min": 3.0},
        ]) is False
        assert matches_filters(row, [
            {"feature": "elo_diff", "min": 25.0},
            {"feature": "edge_vs_vegas", "min": 1.0},
        ]) is True

    def test_unknown_feature_fails_closed(self):
        assert not matches_filters({"elo_diff": 50.0}, [{"feature": "not_a_real_feature", "min": 0}])


class TestSideMatches:
    def test_side_filter_excludes_other_side(self):
        assert not side_matches(
            side="away", row={"spread_line": 3.0},
            f_side="home", f_favorite_or_dog="any", filters=[],
        )

    def test_side_any_matches_both(self):
        for side in ("home", "away"):
            assert side_matches(
                side=side, row={"spread_line": 3.0},
                f_side="any", f_favorite_or_dog="any", filters=[],
            )

    def test_favorite_or_dog_filter(self):
        assert side_matches(
            side="home", row={"spread_line": 3.0},
            f_side="any", f_favorite_or_dog="favorite", filters=[],
        )
        assert not side_matches(
            side="home", row={"spread_line": 3.0},
            f_side="any", f_favorite_or_dog="dog", filters=[],
        )

    def test_combines_side_fav_dog_and_generic_filters(self):
        row = {"spread_line": 12.0, "elo_diff": 60.0}
        assert side_matches(
            side="home", row=row,
            f_side="home", f_favorite_or_dog="favorite",
            filters=[{"feature": "elo_diff", "min": 50.0}],
        )
        assert not side_matches(
            side="home", row=row,
            f_side="home", f_favorite_or_dog="favorite",
            filters=[{"feature": "elo_diff", "min": 100.0}],
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
            assert isinstance(angle["filters"], list)

    def test_home_dog_and_away_favorite_are_opposite_sides_of_same_game(self):
        assert PREBUILT_ANGLES["home_dog"]["side"] == "home"
        assert PREBUILT_ANGLES["home_dog"]["favorite_or_dog"] == "dog"
        assert PREBUILT_ANGLES["away_favorite"]["side"] == "away"
        assert PREBUILT_ANGLES["away_favorite"]["favorite_or_dog"] == "favorite"

    def test_big_favorite_and_underdog_use_spread_bound_10(self):
        assert PREBUILT_ANGLES["big_favorite"]["filters"] == [{"feature": "spread_line", "min": 10.0}]
        assert PREBUILT_ANGLES["big_underdog"]["filters"] == [{"feature": "spread_line", "max": -10.0}]


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
                "W01_DAL_PHI": {"explanation": {
                    "elo_diff": 15.0, "vegas_line": -2.0,
                    "model_spread": -1.0, "edge_vs_vegas": 1.0,
                    "home_qb_out": 1.0, "away_qb_out": 0.0,
                }},
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

    def test_candidates_are_one_row_per_game(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        # No side/favorite_or_dog filter -> both perspectives match -> still one row
        assert len(result["candidates"]) == 1
        candidate = result["candidates"][0]
        assert set(candidate["matched_sides"]) == {"home", "away"}

    def test_side_any_does_not_double_count_a_single_favorite(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            side="any", favorite_or_dog="favorite",
        )
        # Exactly one favorite per historical game -> 2 games, 2 backtest entries, not 4
        assert result["backtest"]["n"] == 2

    def test_generic_elo_filter_narrows_candidates(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            filters=[{"feature": "elo_diff", "min": 50.0}],
        )
        # Only KC's home elo_diff=80 clears 50; DAL/PHI (target week) has elo_diff=15/-15 for each side
        assert result["candidates"] == []

    def test_multi_feature_filter(self):
        # DAL (home): elo_diff=15, edge_vs_vegas=1.0 -- both must pass
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
            side="home",
            filters=[
                {"feature": "elo_diff", "min": 10.0},
                {"feature": "edge_vs_vegas", "min": 0.5},
            ],
        )
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["matched_sides"] == ["home"]

    def test_ungraded_future_candidate_flagged_not_already_played(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        assert all(c["already_played"] is False for c in result["candidates"])

    def test_candidate_carries_home_perspective_feature_values(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        candidate = result["candidates"][0]
        # DAL is home; explanation's vegas_line=-2.0 is already home-perspective
        assert candidate["spread_line"] == -2.0
        assert candidate["model_spread"] == -1.0
        assert candidate["edge_vs_vegas"] == 1.0

    def test_candidate_carries_qb_flags_unflipped(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        candidate = result["candidates"][0]
        assert candidate["home_qb_out"] == 1.0
        assert candidate["away_qb_out"] == 0.0

    def test_response_includes_filterable_features(self):
        result = screen_games(
            self._predictions(), self._games_df(),
            target_season=2026, target_week=1,
        )
        assert result["filterable_features"]["elo_diff"] == "Elo Diff"
        assert "spread_line" in result["filterable_features"]
