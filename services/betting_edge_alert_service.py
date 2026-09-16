"""services/betting_edge_alert_service.py -- orchestrates the existing
betting screener (services.betting_screener_service.screen_games) and
pattern scanner (services.pattern_scanner_service.scan_angles) into a single
weekly summary for the winspool-betting-alert Cloud Run Job. Pure logic, no
Firestore/network -- callers pass in already-loaded predictions_by_season /
games_df, same as both services it composes. Never touches the NN+XGB+LR
ensemble.

Two tiers, both computed independently and never gating each other:
1. Validated-angle matches (find_validated_angle_matches): reuses
   scan_angles's walk-forward-validated leaderboard entries (held_up=True
   only -- see pattern_scanner_service's module docstring for what that
   means) and checks which currently match the upcoming week's games via
   screen_games, using each entry's own `conditions` as screen_games'
   `filters` -- the same {"feature", "min"|"max"} shape both already share.
2. Raw edge outliers (find_raw_edge_outliers): any upcoming game where
   |edge_vs_vegas| exceeds a threshold, read directly from the same
   explanation dict screen_games/scan_angles use -- not backtest-validated
   by itself, so kept in a clearly separate list for the email to label.
"""
from __future__ import annotations

from typing import Optional

from services.betting_screener_service import screen_games
from services.pattern_scanner_service import scan_angles

DEFAULT_EDGE_THRESHOLD = 3.0
DEFAULT_MAX_ANGLE_MATCHES = 10


def find_raw_edge_outliers(
    predictions_by_season: dict, *,
    target_season: int, target_week: int,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> list[dict]:
    """Upcoming games where |edge_vs_vegas| >= edge_threshold, sorted by
    magnitude descending. Reads edge_vs_vegas/vegas_line straight from each
    game's `explanation` dict (the source both betting_screener_service and
    pattern_scanner_service already read), and pred_ats_pick from the
    top-level pred_dict (which side the model favors ATS)."""
    preds = predictions_by_season.get(target_season, {})
    outliers = []
    for game_key, pred in preds.items():
        parts = game_key.split("_")
        if len(parts) != 3:
            continue
        wk_str, ht, at = parts
        try:
            wk = int(wk_str.lstrip("W"))
        except ValueError:
            continue
        if wk != target_week:
            continue

        ex = pred.get("explanation") or {}
        edge = ex.get("edge_vs_vegas")
        if edge is None or abs(edge) < edge_threshold:
            continue

        outliers.append({
            "season": target_season, "week": wk,
            "home_team": ht, "away_team": at,
            "edge_vs_vegas": edge,
            "model_spread": ex.get("model_spread"),
            "vegas_line": ex.get("vegas_line"),
            "ats_pick": pred.get("pred_ats_pick"),
        })

    outliers.sort(key=lambda o: abs(o["edge_vs_vegas"]), reverse=True)
    return outliers


def find_validated_angle_matches(
    predictions_by_season: dict, games_df, *,
    target_season: int, target_week: int,
    scan_kwargs: Optional[dict] = None,
    max_matches: int = DEFAULT_MAX_ANGLE_MATCHES,
) -> list[dict]:
    """Walk-forward-validated (held_up=True) leaderboard entries from
    scan_angles that currently match at least one of the upcoming week's
    games. Checks both the ATS and SU leaderboards. Each entry's own
    `conditions` list is reused verbatim as screen_games' `filters` -- both
    already share the {"feature", "min"|"max"} shape (see
    pattern_scanner_service._condition / betting_screener_service.matches_filters).
    """
    scan_kwargs = scan_kwargs or {}
    scan = scan_angles(predictions_by_season, games_df, **scan_kwargs)

    matches = []
    for metric, leaderboard in (("ats", scan["ats_leaderboard"]), ("su", scan["su_leaderboard"])):
        for entry in leaderboard:
            if not entry.get("held_up"):
                continue

            result = screen_games(
                predictions_by_season, games_df,
                target_season=target_season, target_week=target_week,
                filters=entry["conditions"],
            )
            upcoming = [c for c in result["candidates"] if not c["already_played"]]
            if not upcoming:
                continue

            matches.append({
                "metric": metric,
                "conditions": entry["conditions"],
                "train_rate": entry["train_rate"], "train_n": entry["train_n"],
                "test_rate": entry["test_rate"], "test_n": entry["test_n"],
                "games": upcoming,
            })
            if len(matches) >= max_matches:
                return matches
    return matches


def build_week_summary(
    predictions_by_season: dict, games_df, *,
    target_season: int, target_week: int,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
    scan_kwargs: Optional[dict] = None,
) -> dict:
    """Both tiers for one target week -- what scripts/betting_edge_alert_weekly.py
    hands to email_service.send_betting_edge_email(). Both lists can be empty
    (a normal quiet week); the caller decides whether that means skip sending."""
    return {
        "season": target_season,
        "week": target_week,
        "validated_angle_matches": find_validated_angle_matches(
            predictions_by_season, games_df,
            target_season=target_season, target_week=target_week,
            scan_kwargs=scan_kwargs,
        ),
        "raw_edge_outliers": find_raw_edge_outliers(
            predictions_by_season,
            target_season=target_season, target_week=target_week,
            edge_threshold=edge_threshold,
        ),
    }
