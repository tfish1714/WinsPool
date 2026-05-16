import os
import pathlib
import pandas as pd
import datetime
from services.nn_projection_engine import NNProjectionEngine
from services.constants import UNDRAFTED_SENTINEL

_MOCK_SCHEDULE_PATH = pathlib.Path(__file__).parent.parent / "rawdata" / "2026_mock_schedule.csv"

def get_sandbox_2026_schedule():
    """Generates an enriched schedule structure for the 2026 Mock Schedule."""
    mock_path = str(_MOCK_SCHEDULE_PATH)
    if not os.path.exists(mock_path):
        raise FileNotFoundError("2026 Mock Schedule CSV not found.")
        
    schedule_df = pd.read_csv(mock_path)
    engine = NNProjectionEngine()
    engine.initialize(2026)
    
    mock_games = []
    base_date = datetime.datetime(2026, 9, 10)
    
    for _, row in schedule_df.iterrows():
        ht = row['home_team']
        at = row['away_team']
        wk = row['week']
        prob = engine.game_win_probability(ht, at)
        
        win_prob = prob['home_win_prob']
        pred_winner = ht if win_prob >= 0.5 else at
        su_conf = round(max(win_prob, 1.0 - win_prob) * 100, 1)
        
        gameday = base_date + datetime.timedelta(days=(wk - 1) * 7)
        
        mock_games.append({
            "season": 2026,
            "week": wk,
            "gameday": gameday,
            "home_team": ht,
            "away_team": at,
            "home_score": UNDRAFTED_SENTINEL,
            "away_score": UNDRAFTED_SENTINEL,
            "result": UNDRAFTED_SENTINEL,
            "spread_line": 0,
            "total_line": None,
            "pred_winner": pred_winner,
            "pred_su_conf": su_conf,
            "pred_ats_pick": "PK",
            "home_record": "0-0",
            "away_record": "0-0",
            "fullName_home": "—",
            "fullName_away": "—"
        })
    
    return pd.DataFrame(mock_games)
