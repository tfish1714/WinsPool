import pytest
import pandas as pd
from services.analysis_service import calculate_playoff_race, get_remaining_games, get_enriched_schedule

def test_get_remaining_games():
    """
    A player who owns both teams in one real remaining game (a divisional
    matchup where they drafted both sides) still only has ONE game left --
    counting it twice used to inflate downstream max_wins by crediting the
    impossible outcome of both of their teams winning the same game.
    """
    df = pd.DataFrame([
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "Opp"},
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "TFish"}, # Technically illogical in NFL, but covers logic
        {"result": 10.0, "fullName_away": "TFish", "fullName_home": "Opp"}
    ])

    # 1 game where TFish is away, 1 game where TFish owns both sides (still 1 real game)
    assert get_remaining_games("TFish", df) == 2

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


def test_calculate_playoff_race_self_matchup_caps_max_wins_at_one():
    """A player who owns both teams in one remaining real game can win that
    game AT MOST once -- one of their two teams beats the other, they can
    never get credit for both. max_wins must reflect that ceiling, not
    double-count the single game as two separate winnable opportunities."""
    schedule = pd.DataFrame([
        # TFish owns both AAA and BBB, who play each other next week --
        # exactly one team wins, so TFish's ceiling from this game is +1.
        {"result": pd.NA, "fullName_home": "TFish", "fullName_away": "TFish"},
    ])
    race = calculate_playoff_race(schedule, pd.DataFrame())
    tfish = next(r for r in race if r["player"] == "TFish")
    assert tfish["current_wins"] == 0
    assert tfish["remaining_games"] == 1
    assert tfish["max_wins"] == 1


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


def test_get_remaining_games_treats_sentinel_result_as_unplayed():
    """get_enriched_schedule() (the real caller in production) fills an
    unplayed game's NaN result with UNDRAFTED_SENTINEL (-1000), same as it
    does for an undrafted team's games -- compute_team_records() and
    calculate_playoff_race()'s own win-counting both already account for
    this by checking `.notna() & != UNDRAFTED_SENTINEL` together.
    get_remaining_games() checked only `.isna()` and missed the sentinel
    half, so it silently saw zero remaining games for every player once the
    schedule had passed through get_enriched_schedule -- this is what
    actually breaks the Playoff Race page's remaining-games/elimination
    math from week 1 onward."""
    from services.constants import UNDRAFTED_SENTINEL
    df = pd.DataFrame([
        {"result": UNDRAFTED_SENTINEL, "fullName_away": "TFish", "fullName_home": "Opp"},
        {"result": 10.0, "fullName_away": "TFish", "fullName_home": "Opp"},
    ])
    assert get_remaining_games("TFish", df) == 1


def _race_schedule(games):
    """games: list of (away_owner, home_owner, result). result None = unplayed.

    result > 0 means the home owner won, result < 0 means the away owner won.
    """
    return pd.DataFrame([
        {"fullName_away": a, "fullName_home": h,
         "result": (-1000 if r is None else r), "week": i + 1}
        for i, (a, h, r) in enumerate(games)
    ])


def _hand_built_race():
    # Hand-computed state (current_wins / remaining / max_wins):
    #   A: 2 / 1 / 3   (beat B twice at home; C@A unplayed)
    #   B: 0 / 1 / 1   (D@B unplayed)
    #   C: 1 / 1 / 2   (won at D; C@A unplayed)
    #   D: 0 / 1 / 1   (lost to C; D@B unplayed)
    schedule = _race_schedule([
        ("B", "A", 3), ("B", "A", 7), ("C", "D", -4),
        ("C", "A", None), ("D", "B", None),
    ])
    return {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}


def test_magic_number_concrete_values_hand_computed():
    race = _hand_built_race()
    assert (race["A"]["current_wins"], race["A"]["max_wins"]) == (2, 3)
    assert (race["B"]["current_wins"], race["B"]["max_wins"]) == (0, 1)
    assert (race["C"]["current_wins"], race["C"]["max_wins"]) == (1, 2)
    assert (race["D"]["current_wins"], race["D"]["max_wins"]) == (0, 1)
    # first place: best opponent max - own wins + 1
    assert race["A"]["magic_number"] == 1   # 2 - 2 + 1
    assert race["B"]["magic_number"] == 4   # 3 - 0 + 1
    assert race["C"]["magic_number"] == 3   # 3 - 1 + 1
    assert race["D"]["magic_number"] == 4   # 3 - 0 + 1
    # podium: 3rd-highest opponent max - own wins + 1, clamped at 0
    assert race["A"]["podium_magic_number"] == 0   # opp max 2,1,1 -> 1 - 2 + 1 = 0
    assert race["B"]["podium_magic_number"] == 2   # opp max 3,2,1 -> 1 - 0 + 1
    assert race["C"]["podium_magic_number"] == 1   # opp max 3,1,1 -> 1 - 1 + 1
    assert race["D"]["podium_magic_number"] == 2   # opp max 3,2,1 -> 1 - 0 + 1


def test_eliminated_flags_hand_computed():
    race = _hand_built_race()
    assert race["A"]["eliminated"] is False        # nobody has 3 wins
    assert race["B"]["eliminated"] is True         # A(2), C(1) already > B max 1
    # A(2) only EQUALS C's max (2): C can still tie A and win on tiebreakers,
    # so C is not eliminated (elimination needs an opponent's current wins
    # strictly greater than this player's max wins).
    assert race["C"]["eliminated"] is False
    assert race["D"]["eliminated"] is True
    # only 2 opponents above B's max, need PODIUM_SIZE (3) for podium elimination
    assert all(race[p]["podium_eliminated"] is False for p in "ABCD")


def test_podium_eliminated_when_three_opponents_already_exceed_max():
    schedule = _race_schedule([
        ("X", "P1", 5), ("X", "P2", 5), ("X", "P3", 5), ("X", "P1", None),
        ("X", "P1", 5), ("X", "P2", 5), ("X", "P3", 5),
    ])
    # P1..P3 each have 2 wins, strictly above X's max of 1.
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    assert race["X"]["max_wins"] == 1
    assert race["X"]["eliminated"] is True
    assert race["X"]["podium_eliminated"] is True
    assert race["P1"]["podium_eliminated"] is False


def test_magic_number_zero_when_clinched():
    # A already has 3 wins and nobody else can reach 3.
    schedule = _race_schedule([("B", "A", 1), ("B", "A", 1), ("B", "A", 1)])
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    assert race["A"]["current_wins"] == 3
    assert race["A"]["magic_number"] == 0
    assert race["A"]["podium_magic_number"] == 0


def test_magic_number_single_player_does_not_crash():
    schedule = _race_schedule([("A", "A", 5)])
    race = calculate_playoff_race(schedule, pd.DataFrame())
    assert len(race) == 1
    assert race[0]["magic_number"] == 0
    assert race[0]["podium_magic_number"] == 0
    assert race[0]["eliminated"] is False
    assert race[0]["podium_eliminated"] is False


def test_magic_number_single_player_with_remaining_games():
    schedule = _race_schedule([("A", "A", None), ("A", "A", None)])
    race = calculate_playoff_race(schedule, pd.DataFrame())
    assert len(race) == 1
    assert race[0]["magic_number"] == 0
    assert race[0]["podium_magic_number"] == 0
    assert race[0]["eliminated"] is False


def test_magic_number_two_players_podium_trivially_clinched():
    # Fewer than PODIUM_SIZE opponents: podium is trivially clinched.
    schedule = _race_schedule([("B", "A", 1), ("A", "B", None)])
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    assert race["A"]["podium_magic_number"] == 0
    assert race["B"]["podium_magic_number"] == 0
    assert race["B"]["podium_eliminated"] is False


def test_existing_playoff_race_keys_unchanged():
    schedule = _race_schedule([("B", "A", -1), ("A", "B", None)])
    rec = calculate_playoff_race(schedule, pd.DataFrame())[0]
    for key in ("player", "current_wins", "remaining_games", "max_wins", "race", "rank"):
        assert key in rec


def test_playoff_constants_importable():
    from services.constants import PLAYOFF_RACE_MIN_WEEK, PODIUM_SIZE
    assert PLAYOFF_RACE_MIN_WEEK == 10
    assert PODIUM_SIZE == 3


def test_completed_season_three_way_tie_at_top_nobody_eliminated():
    # Cyclic results, 1 win each, 0 games left. Ties are decided by
    # tiebreakers, so no one is eliminated (including the eventual champion).
    schedule = _race_schedule([("B", "A", 1), ("C", "B", 1), ("A", "C", 1)])
    race = {r["player"]: r for r in calculate_playoff_race(schedule, pd.DataFrame())}
    for p in "ABC":
        assert race[p]["current_wins"] == race[p]["max_wins"] == 1
        assert race[p]["eliminated"] is False
        assert race[p]["podium_eliminated"] is False
        assert race[p]["magic_number"] == 1
