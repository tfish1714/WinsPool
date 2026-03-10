import pytest
from services.utils import get_team_logo_url, normalize_team_abbr

def test_normalize_team_abbr():
    """
    Verify that heterogeneous data abbreviations (e.g. from GitHub CSVs)
    are strictly mapped to the single source of truth application identifiers.
    """
    assert normalize_team_abbr("LAR") == "LA"
    assert normalize_team_abbr("WSH") == "WAS"
    assert normalize_team_abbr("JAC") == "JAX"
    # Fallback to pure uppercase if already standard
    assert normalize_team_abbr("buf") == "BUF"

def test_get_team_logo_url():
    """
    Verify the application leverages the ESPN HD vector API reliably, passing 
    through normalized string references to prevent dead images on the UI.
    """
    assert "LV.png" in get_team_logo_url("LV")
    # Native mapping fallback for ESPN anomalies
    assert "LAR.png" in get_team_logo_url("LA")
    
    # Empty string should yield empty safely
    assert get_team_logo_url("") == ""
    assert get_team_logo_url(None) == ""
