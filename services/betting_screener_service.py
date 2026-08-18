"""services/betting_screener_service.py — Elo/spread/model angle backtesting for
the admin Betting tab.

Reads already-computed per-game data (the `explanation` dict written by
services.nn_prediction_service.build_ensemble_lookup, which carries elo_diff,
Vegas spread_line, model_spread, edge_vs_vegas, and a set of the underlying
model feature deltas) and actual game results (from services.data_service.
load_data) so an admin can build filter rules across any of those signals
("home dog", "model disagrees with Vegas by 3+", "elo favors home by 50+ and
rest_advantage is negative", etc.), see their historical against-the-spread
(ATS) record, and see which of an upcoming week's games currently match.
Read-only -- never writes anything, and does not touch the NN+XGB+LR ensemble.

Sign convention: every value in FILTERABLE_FEATURES is stored home-minus-away
-- positive = home team is better/favored, negative = away team is better/
favored. This matches nflverse's spread_line convention (verified empirically:
positive spread_line correlates with a bigger home-team margin) as well as
elo_diff and model_spread, so a single perspective flip (negate every value)
converts a home-perspective row into the away team's perspective.

home_qb_out/away_qb_out are the one exception: they're two independent flags,
not a home-minus-away delta, so they're never sign-flipped or filtered
generically -- they're just carried through on every candidate for display.
"""
from __future__ import annotations

import math
from typing import Optional

from services.nn_feature_engine import _normalize_team

# Elo data starts in 2006 (see services/cache_service.py's get_all_elo_history);
# earlier seasons have Vegas spreads but no elo_diff, so they'd only ever match
# spread-only filters and would understate Elo-filtered backtest sample sizes.
BACKTEST_MIN_SEASON = 2006

# feature key (as used in filters / candidate output) -> (display label, key in
# the per-game `explanation` dict it reads from). Every value here is a
# home-minus-away delta -- see module docstring for sign convention.
FILTERABLE_FEATURES: dict[str, tuple[str, str]] = {
    "spread_line":         ("Vegas Spread",        "vegas_line"),
    "elo_diff":            ("Elo Diff",             "elo_diff"),
    "model_spread":        ("Model Spread",         "model_spread"),
    "edge_vs_vegas":       ("Edge vs Vegas",         "edge_vs_vegas"),
    "pass_epa_matchup":    ("Pass EPA Matchup",      "pass_epa_matchup"),
    "rush_epa_matchup":    ("Rush EPA Matchup",      "rush_epa_matchup"),
    "early_down_matchup":  ("Early-Down Matchup",    "early_down_matchup"),
    "roster_delta":        ("Roster Talent Delta",   "roster_delta"),
    "turnover_margin":     ("Turnover Margin",       "turnover_margin"),
    "point_diff_advantage":("Point Diff Advantage",  "point_diff_advantage"),
    "rest_advantage":      ("Rest Advantage",        "rest_advantage"),
    "travel_disadvantage": ("Travel Disadvantage",   "travel_disadvantage"),
    "trench_dominance":    ("Trench Dominance",      "trench_dominance"),
    "off_roster_value":    ("Off Roster Value",      "off_roster_value"),
    "def_roster_value":    ("Def Roster Value",      "def_roster_value"),
}

PREBUILT_ANGLES: dict[str, dict] = {
    "home_dog":      {"side": "home", "favorite_or_dog": "dog", "filters": []},
    "away_favorite": {"side": "away", "favorite_or_dog": "favorite", "filters": []},
    "big_favorite":  {"side": "any", "favorite_or_dog": "favorite",
                       "filters": [{"feature": "spread_line", "min": 10.0}]},
    "big_underdog":  {"side": "any", "favorite_or_dog": "dog",
                       "filters": [{"feature": "spread_line", "max": -10.0}]},
}


def _favorite_or_dog(spread_for_side: Optional[float]) -> Optional[str]:
    """'favorite' if this side is favored (positive spread), 'dog' if not,
    None if the spread is unknown or a pick'em (0 -- no favorite exists)."""
    if spread_for_side is None:
        return None
    if spread_for_side > 0:
        return "favorite"
    if spread_for_side < 0:
        return "dog"
    return None


def _feature_row(explanation: dict) -> dict:
    """Home-perspective {feature: value} for every entry in FILTERABLE_FEATURES."""
    return {feat: explanation.get(exp_key) for feat, (_, exp_key) in FILTERABLE_FEATURES.items()}


def _flip_row(row: dict) -> dict:
    """Sign-flip every value in a feature row (home perspective -> away perspective)."""
    return {k: (-v if v is not None else None) for k, v in row.items()}


def matches_filters(row: dict, filters: list[dict]) -> bool:
    """True if every {feature, min, max} bound in `filters` is satisfied by `row`.
    All bounds are AND-combined. A bound naming an unrecognized feature fails
    closed (treated as unmet) -- callers should validate feature names against
    FILTERABLE_FEATURES up front and reject unknown ones with a 400 instead of
    silently relying on this."""
    for f in filters:
        feature = f.get("feature")
        if feature not in FILTERABLE_FEATURES:
            return False
        val = row.get(feature)
        f_min = f.get("min")
        f_max = f.get("max")
        if f_min is not None and (val is None or val < f_min):
            return False
        if f_max is not None and (val is None or val > f_max):
            return False
    return True


def side_matches(
    *, side: str, row: dict,
    f_side: str, f_favorite_or_dog: str, filters: list[dict],
) -> bool:
    """True if this side (with its own sign-flipped `row`) satisfies the filter."""
    if f_side != "any" and side != f_side:
        return False
    if f_favorite_or_dog != "any" and _favorite_or_dog(row.get("spread_line")) != f_favorite_or_dog:
        return False
    return matches_filters(row, filters)


def grade_bet(side: str, home_score, away_score, spread_line) -> Optional[str]:
    """Returns 'win' | 'loss' | 'push' for a bet on `side`, or None if the game
    hasn't been played yet or the spread is unknown/invalid."""
    if home_score is None or away_score is None or spread_line is None:
        return None
    try:
        home_score = float(home_score)
        away_score = float(away_score)
        spread_line = float(spread_line)
    except (TypeError, ValueError):
        return None
    if math.isnan(home_score) or math.isnan(away_score) or math.isnan(spread_line):
        return None

    result = home_score - away_score
    margin_for_side = result if side == "home" else -result
    line_for_side = spread_line if side == "home" else -spread_line

    if margin_for_side > line_for_side:
        return "win"
    if margin_for_side < line_for_side:
        return "loss"
    return "push"


def find_next_upcoming_week(games_df, season: int) -> Optional[int]:
    """Earliest week in `season` with at least one unplayed game (null result),
    or None if the season is missing or fully complete."""
    if games_df is None or games_df.empty or "season" not in games_df.columns:
        return None
    season_games = games_df[games_df["season"] == season]
    if season_games.empty:
        return None
    unplayed = season_games[season_games["result"].isna()]
    if unplayed.empty:
        return None
    return int(unplayed["week"].min())


def screen_games(
    predictions_by_season: dict,
    games_df,
    *,
    target_season: int,
    target_week: int,
    side: str = "any",
    favorite_or_dog: str = "any",
    filters: Optional[list[dict]] = None,
) -> dict:
    """Backtest a filter across every season in predictions_by_season, and list
    the target week's currently-matching candidates -- one row per game.

    predictions_by_season: {season: {game_key: pred_dict}}, as returned per-season
        by services.cache_service.get_game_predictions() -- pred_dict's
        `explanation` sub-dict carries elo_diff, vegas_line, model_spread, and
        the other FILTERABLE_FEATURES.
    games_df: full multi-season games dataframe (services.data_service.load_data()),
        used only to look up actual home_score/away_score for grading.
    filters: list of {"feature": <key in FILTERABLE_FEATURES>, "min": float|None,
        "max": float|None}. Values are checked against each side's own
        (sign-flipped-if-away) perspective.
    """
    filters = filters or []

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

    wins = losses = pushes = 0
    candidates_by_game: dict[tuple, dict] = {}

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

            ex = pred.get("explanation") or {}
            home_row = _feature_row(ex)
            side_rows = {"home": home_row, "away": _flip_row(home_row)}
            home_score, away_score = results_by_key.get((season, wk, ht, at), (None, None))

            matched_sides = []
            for cand_side, row in side_rows.items():
                if not side_matches(
                    side=cand_side, row=row,
                    f_side=side, f_favorite_or_dog=favorite_or_dog, filters=filters,
                ):
                    continue
                matched_sides.append(cand_side)

                outcome = grade_bet(cand_side, home_score, away_score, row.get("spread_line"))
                if outcome == "win":
                    wins += 1
                elif outcome == "loss":
                    losses += 1
                elif outcome == "push":
                    pushes += 1

            if matched_sides and season == target_season and wk == target_week:
                already_played = grade_bet("home", home_score, away_score, home_row.get("spread_line")) is not None
                candidates_by_game[(season, wk, ht, at)] = {
                    "season": season, "week": wk,
                    "home_team": ht, "away_team": at,
                    "matched_sides": matched_sides,
                    "already_played": already_played,
                    "home_qb_out": ex.get("home_qb_out"),
                    "away_qb_out": ex.get("away_qb_out"),
                    **home_row,
                }

    cover_pct = round(wins / (wins + losses), 4) if (wins + losses) > 0 else None

    return {
        "backtest": {
            "wins": wins, "losses": losses, "pushes": pushes,
            "n": wins + losses + pushes, "cover_pct": cover_pct,
        },
        "candidates": list(candidates_by_game.values()),
        "filterable_features": {k: v[0] for k, v in FILTERABLE_FEATURES.items()},
    }
