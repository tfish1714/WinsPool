import pytest
import pandas as pd
from services.analysis_service import calculate_playoff_race, get_remaining_games, get_enriched_schedule

def test_get_remaining_games():
    """
    Verify that the remaining games algorithm aggressively aggregates 
    un-played target team schedules, properly handling divisional matchups
    amounting to 2 instances.
    """
    df = pd.DataFrame([
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "Opp"},
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "TFish"}, # Technically illogical in NFL, but covers logic
        {"result": 10.0, "fullName_away": "TFish", "fullName_home": "Opp"}
    ])
    
    # 1 game where TFish is away, 1 game where TFish is BOTH (counts as 2)
    assert get_remaining_games("TFish", df) == 3

def test_calculate_playoff_race_logic():
    """
    Verify the math assessing Magic Numbers generates a mathematically
    sound boolean map of permutations mapping current wins natively.
    """
    schedule = pd.DataFrame([
        # TFish has 2 wins, 0 remaining
        {"result": 10, "fullName_home": "TFish", "fullName_away": "Other"},
        {"result": -5, "fullName_home": "Other", "fullName_away": "TFish"},
        
        # Bob has 0 wins, 1 remaining
        {"result": pd.NA, "fullName_home": "Bob", "fullName_away": "Other"}
    ])
    
    standings = pd.DataFrame() # Not strictly required by internal logic in this stub
    
    race = calculate_playoff_race(schedule, standings)
    
    tfish_record = next(r for r in race if r["player"] == "TFish")
    assert tfish_record["current_wins"] == 2
    assert tfish_record["remaining_games"] == 0
    
    bob_record = next(r for r in race if r["player"] == "Bob")
    assert bob_record["current_wins"] == 0
    assert bob_record["remaining_games"] == 1
    
    # Bob max_wins is 1. TFish current is 2. Bob cannot catch TFish.
    catch_tfish_race = next(r for r in bob_record["race"] if r["target_player"] == "TFish")
    assert catch_tfish_race["can_pass"] is False


def test_get_enriched_schedule_preserves_live_score_fields():
    """live_home_score/live_away_score are NaN for the common case (game not
    yet touched by the live-score sync); the blanket UNDRAFTED_SENTINEL
    fillna must not clobber a real in-progress score with -1000."""
    games = pd.DataFrame([{
        "game_id": "2026_01_SF_LA", "season": 2026, "week": 1, "game_type": "REG",
        "gameday": "2026-09-10", "home_team": "LA", "away_team": "SF",
        "home_score": None, "away_score": None, "result": None,
        "is_live": True, "clock": "4:18", "period": 3, "possession": "home",
        "live_home_score": 7, "live_away_score": 17,
    }])
    draft_results = pd.DataFrame(columns=["season", "team", "playerId"])
    players = pd.DataFrame(columns=["playerId", "fullName"])

    sched = get_enriched_schedule(games, draft_results, players, 2026)

    row = sched.iloc[0]
    assert row["live_home_score"] == 7
    assert row["live_away_score"] == 17
    assert row["is_live"] == True
