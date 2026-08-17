"""services/betting_screener_service.py — Elo/spread angle backtesting for the
admin Betting tab.

Reads already-computed per-game data (elo_diff + Vegas spread_line, from
services.cache_service.get_game_predictions) and actual game results (from
services.data_service.load_data) so an admin can build simple filter rules
("home dog", "elo favors home by 50+", etc.), see their historical
against-the-spread (ATS) record, and see which of an upcoming week's games
currently match. Read-only -- never writes anything, and does not touch the
NN+XGB+LR ensemble.

Sign convention (matches services/nn_prediction_service.py::build_ensemble_lookup):
spread_line and elo_diff are both stored signed home-minus-away. Positive
spread_line = home favored; positive elo_diff = home stronger by Elo.
"""
from __future__ import annotations

import math
from typing import Optional

from services.nn_feature_engine import _normalize_team

# Elo data starts in 2006 (see services/cache_service.py's get_all_elo_history);
# earlier seasons have Vegas spreads but no elo_diff, so they'd only ever match
# spread-only filters and would understate Elo-filtered backtest sample sizes.
BACKTEST_MIN_SEASON = 2006

PREBUILT_ANGLES: dict[str, dict] = {
    "home_dog":      {"side": "home", "favorite_or_dog": "dog"},
    "away_favorite": {"side": "away", "favorite_or_dog": "favorite"},
    "big_favorite":  {"side": "any", "favorite_or_dog": "favorite", "spread_min": 10.0},
    "big_underdog":  {"side": "any", "favorite_or_dog": "dog", "spread_min": 10.0},
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


def _side_view(spread_line: Optional[float], elo_diff: Optional[float]):
    """Return [("home", spread, elo), ("away", spread, elo)] with the away
    tuple's values sign-flipped to away's perspective."""
    flip = lambda v: -v if v is not None else None
    return [
        ("home", spread_line, elo_diff),
        ("away", flip(spread_line), flip(elo_diff)),
    ]


def matches_filter(
    *, side: str, spread_for_side: Optional[float], elo_diff_for_side: Optional[float],
    f_side: str, f_favorite_or_dog: str,
    f_spread_min: Optional[float], f_spread_max: Optional[float],
    f_elo_diff_min: Optional[float], f_elo_diff_max: Optional[float],
) -> bool:
    """True if one team-side candidate satisfies the given filter bounds.
    All non-null bounds are AND-combined."""
    if f_side != "any" and side != f_side:
        return False

    if f_favorite_or_dog != "any" and _favorite_or_dog(spread_for_side) != f_favorite_or_dog:
        return False

    magnitude = abs(spread_for_side) if spread_for_side is not None else None
    if f_spread_min is not None and (magnitude is None or magnitude < f_spread_min):
        return False
    if f_spread_max is not None and (magnitude is None or magnitude > f_spread_max):
        return False

    if f_elo_diff_min is not None and (elo_diff_for_side is None or elo_diff_for_side < f_elo_diff_min):
        return False
    if f_elo_diff_max is not None and (elo_diff_for_side is None or elo_diff_for_side > f_elo_diff_max):
        return False

    return True


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
    spread_min: Optional[float] = None,
    spread_max: Optional[float] = None,
    elo_diff_min: Optional[float] = None,
    elo_diff_max: Optional[float] = None,
) -> dict:
    """Backtest a filter across every season in predictions_by_season, and list
    the target week's currently-matching candidates.

    predictions_by_season: {season: {game_key: pred_dict}}, as returned per-season
        by services.cache_service.get_game_predictions() -- pred_dict's
        `explanation` sub-dict carries elo_diff and vegas_line.
    games_df: full multi-season games dataframe (services.data_service.load_data()),
        used only to look up actual home_score/away_score for grading.
    """
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
    candidates = []

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
            spread_line = ex.get("vegas_line")
            elo_diff = ex.get("elo_diff")

            for cand_side, spread_for_side, elo_for_side in _side_view(spread_line, elo_diff):
                if not matches_filter(
                    side=cand_side, spread_for_side=spread_for_side, elo_diff_for_side=elo_for_side,
                    f_side=side, f_favorite_or_dog=favorite_or_dog,
                    f_spread_min=spread_min, f_spread_max=spread_max,
                    f_elo_diff_min=elo_diff_min, f_elo_diff_max=elo_diff_max,
                ):
                    continue

                home_score, away_score = results_by_key.get((season, wk, ht, at), (None, None))
                outcome = grade_bet(cand_side, home_score, away_score, spread_line)
                if outcome == "win":
                    wins += 1
                elif outcome == "loss":
                    losses += 1
                elif outcome == "push":
                    pushes += 1

                if season == target_season and wk == target_week:
                    candidates.append({
                        "season": season, "week": wk,
                        "home_team": ht, "away_team": at,
                        "side": cand_side,
                        "spread_line": spread_line,
                        "elo_diff": elo_for_side,
                        "already_played": outcome is not None,
                    })

    cover_pct = round(wins / (wins + losses), 4) if (wins + losses) > 0 else None

    return {
        "backtest": {
            "wins": wins, "losses": losses, "pushes": pushes,
            "n": wins + losses + pushes, "cover_pct": cover_pct,
        },
        "candidates": candidates,
    }
