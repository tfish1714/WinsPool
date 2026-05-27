import os
import json
import pathlib
import pandas as pd

def get_project_root() -> pathlib.Path:
    return pathlib.Path(__file__).parent.parent

def load_config(filename: str):
    config_path = get_project_root() / 'config' / filename
    if not config_path.exists():
        return {}
    with open(config_path, 'r') as f:
        return json.load(f)

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
