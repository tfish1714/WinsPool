import pytest
import pandas as pd
from services.prediction_service import calculate_confidence, predict_winner, enrich_schedule_with_predictions

def test_calculate_confidence_logic():
    """
    Verify that the spread math effectively calculates safe float
    percentages strictly guarded between 0.5 and 0.99.
    """
    assert calculate_confidence(pd.NA) == 0.5
    assert calculate_confidence(0.0) == 0.5
    assert calculate_confidence(14.0) == 0.99  # 0.5 + (14*0.035) -> 0.99
    assert calculate_confidence(-14.0) == 0.99

def test_predict_winner_straight_up():
    """
    Verify that predicting ATS and SU safely parses float spreads
    identifying the correct favorite vs underdog logic.
    """
    # Away favored by 3
    result = predict_winner("PHI", "KC", -3.0)
    assert result["predicted_winner"] == "KC"
    assert result["ats_pick"] == "PHI"  # underdog picked if spread is flat/3
    assert result["su_confidence"] > 50.0

def test_enrich_schedule_mutations():
    """
    Verify that the prediction service effectively loops over a native
    schedule payload and attaches heuristic probability tracking columns
    without corrupting existing fields.
    """
    df = pd.DataFrame([
        {"home_team": "PHI", "away_team": "KC", "spread_line": -3.0, "result": -1000},
        {"home_team": "BUF", "away_team": "MIA", "spread_line": 7.0, "result": 10.0}
    ])
    
    enriched = enrich_schedule_with_predictions(df)
    
    # Unplayed game gets predicted
    assert enriched.iloc[0]["pred_winner"] == "KC"
    assert "pred_su_conf" in enriched.columns
    
    # Already played game is ignored by prediction arrays
    assert pd.isna(enriched.iloc[1]["pred_winner"])
