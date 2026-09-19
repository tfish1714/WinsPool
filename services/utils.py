import pandas as pd


def derive_prediction_scalars(home_team: str, away_team: str, mean_prob: float,
                              model_spread: float, spread_line=None) -> dict:
    """Winner / SU confidence / ATS pick / edge-vs-vegas from one game's
    model probability and spread.

    Single source of truth for this formula -- shared by the MC-simulation
    path (services/nn_projection_engine.py's build_mc_prediction_entry,
    scripts/cache_builder.py, scripts/backfill_schedule_predictions.py) and
    the ensemble lookup path (services/nn_prediction_service.py's
    build_ensemble_lookup) so the four fields can't drift apart between them.

    `vegas_line` is returned alongside them as the parsed float (or None) so
    callers don't have to re-parse spread_line themselves.
    """
    winner = home_team if mean_prob >= 0.5 else away_team
    conf = round(min(99.0, max(50.0, (mean_prob if mean_prob >= 0.5 else 1.0 - mean_prob) * 100)), 1)
    ats = winner
    edge = None
    vegas_line = None
    if spread_line is not None and pd.notna(spread_line):
        try:
            vegas_line = float(spread_line)
            ats = home_team if model_spread > vegas_line else away_team
            edge = round(model_spread - vegas_line, 1)
        except (ValueError, TypeError):
            vegas_line = None
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
