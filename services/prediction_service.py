import pandas as pd
import numpy as np

def calculate_confidence(spread: float, home_ml: float = None) -> float:
    """
    Confidence metric (0.0 to 1.0) based on spread width and implied ML.
    If spread is -7 or more, confidence is high (0.8+).
    """
    if pd.isna(spread):
        return 0.5
        
    # Heuristic: Spread of 0 is 0.5 confidence (50/50)
    # Spread of 14 is 0.95 confidence
    abs_spread = abs(float(spread))
    conf = 0.5 + (abs_spread * 0.035) 
    return min(max(conf, 0.5), 0.99)

def predict_winner(home_team: str, away_team: str, spread_line: float, home_moneyline: float = None) -> dict:
    """
    Return predicted winner and confidence scores (SU and ATS).
    """
    if pd.isna(spread_line):
        return {"winner": "TBD", "su_confidence": 0.5, "ats_pick": "Pass", "ats_confidence": 0.5}

    # spread_line is usually from home team's perspective in many datasets
    # but nfldata 'spread_line' is (away_score - home_score) or vice versa?
    # In Leesharpe data: spread_line is (away - home) point spread.
    # If spread_line is -3, away is favored by 3.
    # If spread_line is 7, home is favored by 7.
    
    favored = home_team if spread_line > 0 else away_team
    underdog = away_team if spread_line > 0 else home_team
    
    su_conf = calculate_confidence(spread_line)
    
    # Against the Spread (ATS) is trickier. 
    # For now, we'll provide a 'leverage' pick based on where the line is 'soft'
    # Defaulting to favored team for ATS if spread is small (< 3)
    ats_pick = favored if abs(spread_line) < 3 else underdog
    ats_conf = 0.52 # Baseline ATS edge is always small

    return {
        "predicted_winner": favored,
        "su_confidence": round(su_conf * 100, 1),
        "ats_pick": ats_pick,
        "ats_confidence": round(ats_conf * 100, 1)
    }

def enrich_schedule_with_predictions(schedule_df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes the enriched schedule and adds prediction columns.
    """
    if schedule_df.empty:
        return schedule_df
        
    predictions = []
    for _, row in schedule_df.iterrows():
        # Only predict unplayed games
        if row.get('result') is None or row.get('result') == -1000:
            pred = predict_winner(
                row['home_team'], 
                row['away_team'], 
                row.get('spread_line'), 
                row.get('home_moneyline')
            )
            predictions.append(pred)
        else:
            predictions.append(None)
            
    # Add new columns
    schedule_df['pred_winner'] = [p['predicted_winner'] if p else None for p in predictions]
    schedule_df['pred_su_conf'] = [p['su_confidence'] if p else None for p in predictions]
    schedule_df['pred_ats_pick'] = [p['ats_pick'] if p else None for p in predictions]
    
    return schedule_df
