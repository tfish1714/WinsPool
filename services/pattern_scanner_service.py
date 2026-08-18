"""services/pattern_scanner_service.py — Walk-forward-validated automated
angle mining across FILTERABLE_FEATURES (Elo, Vegas spread, model spread,
edge vs Vegas, EPA matchups, roster deltas, rest/travel, trench dominance)
for the admin Betting tab's Pattern Scanner.

Builds one row per (season, week, side) historical bet candidate from the
same explanation data betting_screener_service.py reads, sweeps single-feature
and 2-feature threshold combinations, and ranks by how far a combo's cover
rate / straight-up win rate sits from the 50% breakeven line -- surfacing
both "back this" (high rate) and "fade this" (low rate) signals in one pass,
since the mirror-image argument in betting_screener_service's docstring means
a side's negative-direction filter is exactly the complementary stats of the
opposite side's positive-direction filter on the same games -- so a plain
`feature >= t` sweep already covers both directions for a single feature.

For pairs, the same trick only cancels out the "both flipped" case -- a
mixed-direction pair (e.g. "this side is the underdog by spread AND still has
the Elo edge") is a genuinely different game subset, so pairs are swept in
both same-direction and mixed-direction sign patterns.

Walk-forward discipline: the sweep only ever searches the TRAIN seasons.
Each surfaced combo is then evaluated -- once, no re-tuning -- against the
held-out TEST (most recent) seasons, so a combo's reported train edge can't
have been cherry-picked against the same data used to judge whether it holds
up. Everything here is still in-sample-derived exploratory data mining, not
a proven edge -- `held_up` is a filter for "worth investigating further,"
not a guarantee.
"""
from __future__ import annotations

import itertools
from typing import Optional

import numpy as np
import pandas as pd

from services.betting_screener_service import FILTERABLE_FEATURES, _feature_row, _flip_row, grade_bet
from services.nn_feature_engine import _normalize_team

DEFAULT_TEST_SEASONS = 5
DEFAULT_MIN_SAMPLE = 50
DEFAULT_MIN_TEST_SAMPLE = 15
DEFAULT_TOP_N = 20
SINGLE_THRESHOLD_QUANTILES = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
PAIR_THRESHOLD_QUANTILES = [0.6, 0.75, 0.9]


def build_bet_frame(predictions_by_season: dict, games_df) -> pd.DataFrame:
    """One row per (season, week, home_team, away_team, side) historical bet
    candidate -- only games with a known result, since this is backtest-only
    (unlike screen_games, it never looks at an upcoming target week)."""
    results_by_key = {}
    if games_df is not None and not games_df.empty:
        for row in games_df.itertuples(index=False):
            ht = _normalize_team(str(getattr(row, "home_team", "") or ""))
            at = _normalize_team(str(getattr(row, "away_team", "") or ""))
            wk = getattr(row, "week", None)
            season = getattr(row, "season", None)
            if ht and at and wk is not None and season is not None:
                results_by_key[(int(season), int(wk), ht, at)] = (
                    getattr(row, "home_score", None), getattr(row, "away_score", None),
                )

    records = []
    for season, preds in predictions_by_season.items():
        for game_key, pred in preds.items():
            parts = game_key.split("_")
            if len(parts) != 3:
                continue
            wk_str, ht, at = parts
            try:
                wk = int(wk_str.lstrip("W"))
            except ValueError:
                continue

            home_score, away_score = results_by_key.get((season, wk, ht, at), (None, None))
            if pd.isna(home_score) or pd.isna(away_score):
                continue

            ex = pred.get("explanation") or {}
            home_row = _feature_row(ex)
            for side, row in (("home", home_row), ("away", _flip_row(home_row))):
                ats = grade_bet(side, home_score, away_score, row.get("spread_line"))
                margin = (home_score - away_score) if side == "home" else (away_score - home_score)
                su = None if margin == 0 else ("win" if margin > 0 else "loss")
                records.append({
                    "season": season, "week": wk,
                    "home_team": ht, "away_team": at, "side": side,
                    "ats_result": ats, "su_result": su,
                    **row,
                })

    return pd.DataFrame.from_records(records)


def _rate_stats(mask: pd.Series, result_col: pd.Series) -> tuple[int, Optional[float]]:
    """(n, win_rate) over rows where mask is True, counting only win/loss
    (excludes push/None). (0, None) if nothing graded under the mask."""
    sub = result_col[mask]
    wins = int((sub == "win").sum())
    losses = int((sub == "loss").sum())
    n = wins + losses
    if n == 0:
        return 0, None
    return n, round(wins / n, 4)


def _thresholds_for(series: pd.Series, quantiles: list[float]) -> list[float]:
    vals = series.dropna().abs()
    vals = vals[vals > 0]
    if vals.empty:
        return []
    qs = sorted({round(float(v), 2) for v in np.nanquantile(vals, quantiles)})
    return [q for q in qs if q > 0]


def _condition(feature: str, bound_type: str, value: float) -> dict:
    label, _ = FILTERABLE_FEATURES[feature]
    return {"feature": feature, "label": label, bound_type: round(value, 2)}


def scan_single_features(frame: pd.DataFrame, features: Optional[list[str]] = None) -> list[dict]:
    """feature >= t sweep. A single-feature filter's negative-direction case is
    exactly the complementary stats of this same sweep (see module docstring),
    so only the positive direction needs to be searched."""
    features = features or list(FILTERABLE_FEATURES.keys())
    results = []
    for feature in features:
        if feature not in frame.columns:
            continue
        for t in _thresholds_for(frame[feature], SINGLE_THRESHOLD_QUANTILES):
            mask = frame[feature] >= t
            n_ats, cover_pct = _rate_stats(mask, frame["ats_result"])
            n_su, su_win_pct = _rate_stats(mask, frame["su_result"])
            results.append({
                "conditions": [_condition(feature, "min", t)],
                "n_ats": n_ats, "cover_pct": cover_pct,
                "n_su": n_su, "su_win_pct": su_win_pct,
            })
    return results


def scan_pairs(frame: pd.DataFrame, features: Optional[list[str]] = None) -> list[dict]:
    """feature_a >= t_a AND feature_b (>= t_b OR <= -t_b) sweep -- both sign
    patterns, since a mixed-direction pair is a genuinely different game
    subset than same-direction (see module docstring)."""
    features = features or list(FILTERABLE_FEATURES.keys())
    results = []
    for fa, fb in itertools.combinations(features, 2):
        if fa not in frame.columns or fb not in frame.columns:
            continue
        thresholds_a = _thresholds_for(frame[fa], PAIR_THRESHOLD_QUANTILES)
        thresholds_b = _thresholds_for(frame[fb], PAIR_THRESHOLD_QUANTILES)
        for ta in thresholds_a:
            for tb in thresholds_b:
                for sign, bound_type, b_val in ((1, "min", tb), (-1, "max", -tb)):
                    mask = (frame[fa] >= ta) & (frame[fb] * sign >= tb)
                    n_ats, cover_pct = _rate_stats(mask, frame["ats_result"])
                    n_su, su_win_pct = _rate_stats(mask, frame["su_result"])
                    results.append({
                        "conditions": [_condition(fa, "min", ta), _condition(fb, bound_type, b_val)],
                        "n_ats": n_ats, "cover_pct": cover_pct,
                        "n_su": n_su, "su_win_pct": su_win_pct,
                    })
    return results


def _edge(rate: Optional[float]) -> float:
    return abs(rate - 0.5) if rate is not None else -1.0


def _evaluate_conditions(frame: pd.DataFrame, conditions: list[dict]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for cond in conditions:
        feature = cond["feature"]
        if "min" in cond:
            mask &= frame[feature] >= cond["min"]
        if "max" in cond:
            mask &= frame[feature] <= cond["max"]
    return mask


def _held_up(train_rate: Optional[float], test_n: int, test_rate: Optional[float],
             min_test_sample: int) -> Optional[bool]:
    """None if there isn't enough test-set data to judge. Otherwise True if the
    test-set edge points the same direction as train and is at least half the
    train edge in size."""
    if test_n < min_test_sample or test_rate is None or train_rate is None:
        return None
    train_edge = _edge(train_rate)
    test_edge = _edge(test_rate)
    same_direction = (test_rate - 0.5) * (train_rate - 0.5) > 0
    return bool(same_direction and test_edge >= train_edge / 2)


def _build_leaderboard(
    test_frame: pd.DataFrame, raw_results: list[dict], metric: str,
    min_sample: int, min_test_sample: int, top_n: int,
) -> list[dict]:
    rate_key = "cover_pct" if metric == "ats" else "su_win_pct"
    n_key = "n_ats" if metric == "ats" else "n_su"
    result_col = "ats_result" if metric == "ats" else "su_result"

    eligible = [r for r in raw_results if r[n_key] >= min_sample and r[rate_key] is not None]
    eligible.sort(key=lambda r: _edge(r[rate_key]), reverse=True)

    leaderboard = []
    seen = set()
    for r in eligible:
        key = tuple((c["feature"], c.get("min"), c.get("max")) for c in r["conditions"])
        if key in seen:
            continue
        seen.add(key)

        if not test_frame.empty:
            test_mask = _evaluate_conditions(test_frame, r["conditions"])
            test_n, test_rate = _rate_stats(test_mask, test_frame[result_col])
        else:
            test_n, test_rate = 0, None

        leaderboard.append({
            "conditions": r["conditions"],
            "train_n": r[n_key], "train_rate": r[rate_key],
            "train_edge": round(_edge(r[rate_key]), 4),
            "test_n": test_n, "test_rate": test_rate,
            "test_edge": round(_edge(test_rate), 4) if test_rate is not None else None,
            "held_up": _held_up(r[rate_key], test_n, test_rate, min_test_sample),
        })
        if len(leaderboard) >= top_n:
            break
    return leaderboard


def scan_angles(
    predictions_by_season: dict, games_df, *,
    test_seasons: int = DEFAULT_TEST_SEASONS,
    include_pairs: bool = True,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    min_test_sample: int = DEFAULT_MIN_TEST_SAMPLE,
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Sweep single- and (optionally) 2-feature threshold combos on the train
    seasons, rank by distance from the 50% breakeven line, and evaluate each
    surfaced combo once against the held-out most-recent `test_seasons` seasons.
    """
    frame = build_bet_frame(predictions_by_season, games_df)
    if frame.empty:
        return {
            "ats_leaderboard": [], "su_leaderboard": [], "baseline": {},
            "train_seasons": [], "test_seasons": [], "n_rows_scanned": 0,
        }

    seasons_sorted = sorted(int(s) for s in frame["season"].unique())
    test_seasons_list = seasons_sorted[-test_seasons:] if test_seasons > 0 else []
    train_seasons_list = [s for s in seasons_sorted if s not in test_seasons_list]

    train_frame = frame[frame["season"].isin(train_seasons_list)]
    test_frame = frame[frame["season"].isin(test_seasons_list)] if test_seasons_list else frame.iloc[0:0]

    raw_results = scan_single_features(train_frame)
    if include_pairs:
        raw_results += scan_pairs(train_frame)

    ats_leaderboard = _build_leaderboard(test_frame, raw_results, "ats", min_sample, min_test_sample, top_n)
    su_leaderboard = _build_leaderboard(test_frame, raw_results, "su", min_sample, min_test_sample, top_n)

    def _baseline(mask: pd.Series, result_col: str) -> dict:
        n, rate = _rate_stats(mask, frame[result_col])
        return {"n": n, "rate": rate}

    baseline = {
        "favorite_ats_cover_pct": _baseline(frame["spread_line"] > 0, "ats_result"),
        "favorite_su_win_pct":    _baseline(frame["spread_line"] > 0, "su_result"),
        "home_su_win_pct":        _baseline(frame["side"] == "home", "su_result"),
    }

    return {
        "ats_leaderboard": ats_leaderboard,
        "su_leaderboard": su_leaderboard,
        "baseline": baseline,
        "train_seasons": train_seasons_list,
        "test_seasons": test_seasons_list,
        "n_rows_scanned": int(len(frame)),
    }
