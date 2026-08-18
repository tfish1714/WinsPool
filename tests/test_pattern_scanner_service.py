"""Tests for services/pattern_scanner_service.py -- the walk-forward-validated
automated angle miner behind the admin Betting tab's Pattern Scanner."""
import pandas as pd
import pytest

from services.pattern_scanner_service import (
    build_bet_frame,
    _rate_stats,
    _thresholds_for,
    _edge,
    _evaluate_conditions,
    _held_up,
    scan_single_features,
    scan_pairs,
    scan_angles,
)


class TestBuildBetFrame:
    def _games_df(self):
        return pd.DataFrame([
            {"season": 2020, "week": 1, "home_team": "KC", "away_team": "SF",
             "home_score": 30, "away_score": 10},
            {"season": 2020, "week": 2, "home_team": "BUF", "away_team": "MIA",
             "home_score": None, "away_score": None},  # unplayed -- excluded
        ])

    def _predictions(self):
        return {
            2020: {
                "W01_KC_SF": {"explanation": {"elo_diff": 50.0, "vegas_line": 7.0}},
                "W02_BUF_MIA": {"explanation": {"elo_diff": 10.0, "vegas_line": 2.0}},
            },
        }

    def test_only_played_games_included(self):
        frame = build_bet_frame(self._predictions(), self._games_df())
        assert set(frame["week"]) == {1}
        assert len(frame) == 2  # one row per side for the one played game

    def test_home_and_away_rows_are_sign_flipped(self):
        frame = build_bet_frame(self._predictions(), self._games_df())
        home = frame[frame["side"] == "home"].iloc[0]
        away = frame[frame["side"] == "away"].iloc[0]
        assert home["elo_diff"] == 50.0
        assert away["elo_diff"] == -50.0
        assert home["spread_line"] == 7.0
        assert away["spread_line"] == -7.0

    def test_ats_and_su_results_graded_correctly(self):
        # KC (home) favored by 7, won by 20 -> covers (win); SF (away) -> loss
        frame = build_bet_frame(self._predictions(), self._games_df())
        home = frame[frame["side"] == "home"].iloc[0]
        away = frame[frame["side"] == "away"].iloc[0]
        assert home["ats_result"] == "win"
        assert away["ats_result"] == "loss"
        assert home["su_result"] == "win"
        assert away["su_result"] == "loss"


class TestRateStats:
    def test_basic_win_rate(self):
        col = pd.Series(["win", "win", "loss", "push"])
        mask = pd.Series([True, True, True, True])
        n, rate = _rate_stats(mask, col)
        assert n == 3
        assert rate == pytest.approx(round(2 / 3, 4))

    def test_zero_n_returns_none_rate(self):
        col = pd.Series(["push", "push"])
        mask = pd.Series([True, True])
        assert _rate_stats(mask, col) == (0, None)

    def test_mask_excludes_rows(self):
        col = pd.Series(["win", "loss"])
        mask = pd.Series([True, False])
        n, rate = _rate_stats(mask, col)
        assert n == 1
        assert rate == 1.0


class TestThresholdsFor:
    def test_returns_positive_quantiles(self):
        series = pd.Series([-10.0, -5.0, 0.0, 5.0, 10.0, 15.0, 20.0])
        thresholds = _thresholds_for(series, [0.5, 0.9])
        assert all(t > 0 for t in thresholds)

    def test_empty_series_returns_empty_list(self):
        assert _thresholds_for(pd.Series([], dtype=float), [0.5]) == []

    def test_all_zero_series_returns_empty_list(self):
        assert _thresholds_for(pd.Series([0.0, 0.0, None]), [0.5]) == []


class TestEdge:
    def test_distance_from_breakeven(self):
        assert _edge(0.75) == 0.25
        assert _edge(0.25) == 0.25
        assert _edge(0.5) == 0.0

    def test_none_rate_is_least_eligible(self):
        assert _edge(None) == -1.0


class TestEvaluateConditions:
    def test_and_combines_min_and_max(self):
        frame = pd.DataFrame({"elo_diff": [10.0, 60.0, 100.0], "spread_line": [1.0, -5.0, 3.0]})
        mask = _evaluate_conditions(frame, [
            {"feature": "elo_diff", "min": 50.0},
            {"feature": "spread_line", "max": 0.0},
        ])
        assert list(mask) == [False, True, False]


class TestHeldUp:
    def test_insufficient_test_sample_is_none(self):
        assert _held_up(train_rate=0.8, test_n=5, test_rate=0.8, min_test_sample=15) is None

    def test_same_direction_and_half_edge_is_true(self):
        # train edge = 0.3 (0.8), test edge = 0.2 (0.7) >= 0.15 -> holds up
        assert _held_up(train_rate=0.8, test_n=20, test_rate=0.7, min_test_sample=15) is True

    def test_opposite_direction_is_false(self):
        assert _held_up(train_rate=0.8, test_n=20, test_rate=0.3, min_test_sample=15) is False

    def test_same_direction_but_too_weak_is_false(self):
        # train edge 0.3, test edge only 0.05 (< half of 0.3)
        assert _held_up(train_rate=0.8, test_n=20, test_rate=0.55, min_test_sample=15) is False


class TestScanSingleFeatures:
    def _frame(self):
        # elo_diff strongly separates ats outcome; noise feature doesn't.
        rows = []
        for i in range(60):
            rows.append({"elo_diff": 80.0, "noise": 1.0, "ats_result": "win", "su_result": "win"})
        for i in range(60):
            rows.append({"elo_diff": -80.0, "noise": 1.0, "ats_result": "loss", "su_result": "loss"})
        return pd.DataFrame(rows)

    def test_finds_high_edge_for_separating_feature(self):
        results = scan_single_features(self._frame(), features=["elo_diff"])
        assert results
        best = max(results, key=lambda r: _edge(r["cover_pct"]))
        assert best["cover_pct"] == 1.0
        assert best["n_ats"] == 60

    def test_skips_features_not_in_frame(self):
        results = scan_single_features(self._frame(), features=["not_a_column"])
        assert results == []


class TestScanPairs:
    def test_mixed_direction_combo_is_searched(self):
        # side has spread_line<0 (dog) but elo_diff>=t (elo edge) -- a mixed-sign
        # pattern that same-direction-only search would miss entirely.
        rows = []
        for i in range(30):
            rows.append({"spread_line": -5.0, "elo_diff": 60.0, "ats_result": "win", "su_result": "win"})
        for i in range(30):
            rows.append({"spread_line": 5.0, "elo_diff": -60.0, "ats_result": "loss", "su_result": "loss"})
        frame = pd.DataFrame(rows)
        results = scan_pairs(frame, features=["spread_line", "elo_diff"])
        assert results
        # A perfectly-separating combo exists (cover_pct 1.0 or its mirror 0.0 --
        # both carry maximal edge, so either is an equally valid "found it").
        perfect = [r for r in results if r["cover_pct"] in (0.0, 1.0)]
        assert perfect
        # At least one of the perfectly-separating combos must be mixed-direction
        # (one min bound, one max bound) -- same-direction-only search would miss it.
        assert any(
            any("max" in c for c in r["conditions"]) and any("min" in c for c in r["conditions"])
            for r in perfect
        )


class TestScanAngles:
    def _predictions_and_games(self, n_train_seasons=3, n_test_seasons=1):
        """Builds a synthetic dataset where elo_diff>=50 reliably covers ATS in
        both train and test seasons (a pattern that should hold up), scaled to
        clear the default min_sample/min_test_sample floors."""
        predictions = {}
        games_rows = []
        season_start = 2018
        seasons = list(range(season_start, season_start + n_train_seasons + n_test_seasons))
        for season in seasons:
            preds = {}
            for wk in range(1, 21):
                ht, at = f"H{wk}", f"A{wk}"
                # even weeks: big elo favorite home team that covers big
                if wk % 2 == 0:
                    elo_diff, spread_line, home_score, away_score = 80.0, 7.0, 30, 3
                else:
                    elo_diff, spread_line, home_score, away_score = -80.0, -7.0, 3, 30
                preds[f"W{wk:02d}_{ht}_{at}"] = {
                    "explanation": {"elo_diff": elo_diff, "vegas_line": spread_line}
                }
                games_rows.append({
                    "season": season, "week": wk, "home_team": ht, "away_team": at,
                    "home_score": home_score, "away_score": away_score,
                })
            predictions[season] = preds
        return predictions, pd.DataFrame(games_rows)

    def test_leaderboard_shape_and_held_up(self):
        predictions, games_df = self._predictions_and_games(n_train_seasons=3, n_test_seasons=1)
        result = scan_angles(
            predictions, games_df,
            test_seasons=1, include_pairs=False,
            min_sample=10, min_test_sample=5, top_n=5,
        )
        assert result["ats_leaderboard"], "expected at least one combo to clear the sample floor"
        top = result["ats_leaderboard"][0]
        assert top["train_rate"] == 1.0
        assert top["test_rate"] == 1.0
        assert top["held_up"] is True

    def test_baseline_present(self):
        predictions, games_df = self._predictions_and_games()
        result = scan_angles(predictions, games_df, test_seasons=1, include_pairs=False, min_sample=10)
        assert "favorite_ats_cover_pct" in result["baseline"]
        assert result["baseline"]["favorite_ats_cover_pct"]["rate"] == 1.0

    def test_zero_test_seasons_leaves_held_up_none(self):
        predictions, games_df = self._predictions_and_games(n_train_seasons=2, n_test_seasons=0)
        result = scan_angles(predictions, games_df, test_seasons=0, include_pairs=False, min_sample=10)
        assert result["test_seasons"] == []
        assert result["ats_leaderboard"]
        assert all(r["held_up"] is None for r in result["ats_leaderboard"])

    def test_empty_predictions_returns_empty_shape(self):
        result = scan_angles({}, pd.DataFrame(), test_seasons=1)
        assert result["ats_leaderboard"] == []
        assert result["su_leaderboard"] == []
        assert result["n_rows_scanned"] == 0
