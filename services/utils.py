import math

import pandas as pd

from services.constants import PROB_CLIP_MAX, PROB_CLIP_MIN, SPREAD_TO_PROB_SCALE


# Sign convention throughout (nflverse): a positive spread_line / model_spread
# means the HOME team is favored (KC -7 at home is stored as +7).

def _as_float(value):
    """`value` as a float, or None when it is missing, NaN, or not numeric."""
    if value is None:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return None if math.isnan(number) else number


def prob_to_model_spread(home_prob: float) -> float:
    """The model's implied home spread for a home win probability: the
    logistic inverse, after clipping to [PROB_CLIP_MIN, PROB_CLIP_MAX], rounded
    to one decimal. Positive = home favored."""
    clipped = min(PROB_CLIP_MAX, max(PROB_CLIP_MIN, float(home_prob)))
    return round(SPREAD_TO_PROB_SCALE * math.log(clipped / (1.0 - clipped)), 1)


def edge_vs_vegas(model_spread, vegas_line):
    """How much more the model likes the home team than Vegas does, in points
    (`model_spread - vegas_line`, one decimal). Positive = home has the ATS
    edge, negative = away does. None when either input is missing or invalid.

    Single source of truth: derive_prediction_scalars() and the routes that
    backfill a missing edge all call this."""
    spread = _as_float(model_spread)
    line = _as_float(vegas_line)
    if spread is None or line is None:
        return None
    return round(spread - line, 1)


def pick_ats_team(home_team: str, away_team: str, winner: str, model_spread, vegas_line) -> str:
    """The team the model backs against the spread: home when its spread
    exceeds the Vegas line, else away (a tie goes to away). With no usable
    line or spread there is nothing to compare, so it falls back to the
    straight-up `winner`."""
    spread = model_spread if model_spread is None else float(model_spread)
    line = _as_float(vegas_line)
    if spread is None or line is None:
        return winner
    return home_team if spread > line else away_team


def derive_prediction_scalars(home_team: str, away_team: str, mean_prob: float,
                              model_spread: float, spread_line=None) -> dict:
    """Winner / SU confidence / ATS pick / edge-vs-vegas from one game's
    model probability and spread.

    Single source of truth for this formula -- shared by the MC-simulation
    path (services/nn_projection_engine.py's build_mc_prediction_entry,
    scripts/cache_builder.py, scripts/backfill_schedule_predictions.py), the
    ensemble lookup path (services/nn_prediction_service.py's
    build_ensemble_lookup), and the legacy schedule-enrichment functions, so
    the four fields can't drift apart between them. The ATS and edge rules
    themselves live in pick_ats_team() / edge_vs_vegas() for callers that
    only need one of them.

    `vegas_line` is returned alongside them as the parsed float (or None) so
    callers don't have to re-parse spread_line themselves.
    """
    winner = home_team if mean_prob >= 0.5 else away_team
    conf = round(min(99.0, max(50.0, (mean_prob if mean_prob >= 0.5 else 1.0 - mean_prob) * 100)), 1)
    vegas_line = _as_float(spread_line)
    ats = pick_ats_team(home_team, away_team, winner, model_spread, vegas_line)
    edge = edge_vs_vegas(model_spread, vegas_line)
    return {
        "pred_winner":   winner,
        "pred_su_conf":  conf,
        "pred_ats_pick": ats,
        "model_spread":  model_spread,
        "edge_vs_vegas": edge,
        "vegas_line":    vegas_line,
    }


def get_team_logo_url(team_code: str) -> str:
    """Returns high-res ESPN logo URL for a team code."""
    if not team_code:
        return ""
    # Normalization map
    mapping = {
        "LA": "LAR",
        "WAS": "WSH"
    }
    code = mapping.get(team_code.upper(), team_code.upper())
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{code}.png"

def normalize_team_abbr(abbr: str) -> str:
    """Maps various source abbreviations to the canonical ones used in the repo."""
    mapping = {
        "LAR": "LA",
        "WSH": "WAS",
        "JAC": "JAX"
    }
    return mapping.get(abbr.upper(), abbr.upper())


def abbreviate_player_name(name: str) -> str:
    """Return 'John S.' from 'John Smith', or original if single-word.

    Special cases: 'Undrafted' → 'Undrafted', 'Overall Record' → 'Overall'.
    """
    if not name or name == 'Undrafted':
        return name or 'Undrafted'
    if name == 'Overall Record':
        return 'Overall'
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return parts[0] if parts else name


def filter_season(df: "pd.DataFrame", year: int) -> "pd.DataFrame":
    """Return rows where df['season'] == year, or df unchanged if empty or no 'season' column."""
    if not isinstance(df, pd.DataFrame) or df.empty or 'season' not in df.columns:
        return df
    return df[df['season'] == year]
