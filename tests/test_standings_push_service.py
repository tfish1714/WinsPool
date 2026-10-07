import pandas as pd
from services import standings_push_service as sp


def _games(rows):
    return pd.DataFrame(rows, columns=["season", "week", "game_type", "home_team", "away_team",
                                       "home_score", "away_score", "result"])


def test_ordinal():
    assert [sp.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 112)] == \
        ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "112th"]


def test_week_is_complete_requires_every_game_final():
    g = _games([[2026, 1, "REG", "A", "B", 10, 3, 7],
                [2026, 1, "REG", "C", "D", None, None, None]])
    assert sp.week_is_complete(g, 2026, 1) is False
    g.loc[1, ["home_score", "away_score", "result"]] = [1, 2, -1]
    assert sp.week_is_complete(g, 2026, 1) is True
    assert sp.week_is_complete(g, 2026, 2) is False        # no games: not complete


def test_latest_complete_week_ignores_unfinished_later_week():
    g = _games([[2026, 1, "REG", "A", "B", 10, 3, 7],
                [2026, 2, "REG", "A", "C", 10, 3, 7],
                [2026, 3, "REG", "A", "D", None, None, None]])
    assert sp.latest_complete_week(g, 2026) == 2
    assert sp.latest_complete_week(_games([]), 2026) is None


def test_postponed_game_blocks_its_week():
    g = _games([[2026, 4, "REG", "A", "B", 10, 3, 7],
                [2026, 4, "REG", "C", "D", None, None, None]])
    assert sp.latest_complete_week(g, 2026) is None


def test_standings_as_of_matches_daily_sync_compute_standings():
    from scripts.daily_nfl_sync import compute_standings
    g = _games([[2026, 1, "REG", "A", "B", 24, 10, 14],
                [2026, 1, "REG", "C", "D", 17, 17, 0],
                [2026, 2, "REG", "A", "C", 3, 20, -17],
                [2026, 2, "REG", "B", "D", 9, 6, 3]])
    got = sp.standings_as_of(g, 2026, 2).sort_values("team").reset_index(drop=True)
    want = compute_standings(g).sort_values("team").reset_index(drop=True)
    assert list(got.columns) == list(want.columns)
    pd.testing.assert_frame_equal(got[want.columns], want, check_dtype=False)
    wk1 = sp.standings_as_of(g, 2026, 1)
    assert int(wk1.loc[wk1.team == "A", "wins"].iloc[0]) == 1
    assert int(wk1.loc[wk1.team == "C", "ties"].iloc[0]) == 1


def test_standings_as_of_excludes_other_seasons_nonreg_and_empty():
    g = _games([[2025, 1, "REG", "A", "B", 24, 10, 14],
                [2026, 1, "POST", "A", "B", 24, 10, 14],
                [2026, 2, "REG", "A", "B", 24, 10, 14]])
    empty = sp.standings_as_of(g, 2026, 1)
    assert empty.empty
    assert list(empty.columns) == ["season", "team", "wins", "losses", "ties",
                                   "scored", "allowed", "net", "pct"]


def test_build_messages_variants():
    cur = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 14},
           {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 13},
           {"playerId": 3, "fullName": "Bo Cox", "rank": 3, "wins": 9}]
    prev = [{"playerId": 2, "fullName": "Ann Ray", "rank": 1, "wins": 11},
            {"playerId": 1, "fullName": "Sam Lee", "rank": 3, "wins": 12},
            {"playerId": 3, "fullName": "Bo Cox", "rank": 3, "wins": 9}]
    m = sp.build_messages(6, cur, prev)
    assert m[1] == ("Week 6 standings", "You moved up to 1st (14 wins, +2). You lead the pool.")
    assert m[2] == ("Week 6 standings", "You dropped to 2nd (13 wins, +2). Leader: Sam L.")
    assert m[3] == ("Week 6 standings", "You held 3rd (9 wins, +0). Leader: Sam L.")


def test_build_messages_first_week_has_no_delta():
    cur = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 3},
           {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 2}]
    m = sp.build_messages(1, cur, None)
    assert m[2] == ("Week 1 standings", "You are 2nd with 2 wins. Leader: Sam L.")
    assert m[1] == ("Week 1 standings", "You are 1st with 3 wins. You lead the pool.")


def test_pool_ranking_orders_by_total_wins():
    standings = pd.DataFrame([
        {"season": 2026, "team": t, "wins": w, "losses": 0, "ties": 0,
         "scored": 10.0 * w, "allowed": 5.0, "net": 10.0 * w - 5.0, "pct": 1.0}
        for t, w in [("A", 3), ("B", 2), ("C", 1), ("D", 0), ("E", 2), ("F", 1)]])
    draft_results = pd.DataFrame([
        {"season": 2026, "playerId": 1, "team": "A", "draftPick": 1},
        {"season": 2026, "playerId": 1, "team": "D", "draftPick": 4},
        {"season": 2026, "playerId": 1, "team": "C", "draftPick": 5},
        {"season": 2026, "playerId": 2, "team": "B", "draftPick": 2},
        {"season": 2026, "playerId": 2, "team": "E", "draftPick": 3},
        {"season": 2026, "playerId": 2, "team": "F", "draftPick": 6},
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Sam Lee"},
                            {"playerId": 2, "fullName": "Ann Ray"}])
    ranking = sp.pool_ranking(standings, draft_results, players, 2026)
    assert [r["playerId"] for r in ranking] == [2, 1]          # 5 wins beats 4 wins
    assert [r["rank"] for r in ranking] == [1, 2]
    assert [r["wins"] for r in ranking] == [5, 4]
    assert ranking[0]["fullName"] == "Ann Ray"


def test_singular_win_grammar_in_both_forms():
    cur = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 1},
           {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 0}]
    first = sp.build_messages(1, cur, None)
    assert first[1][1] == "You are 1st with 1 win. You lead the pool."
    assert first[2][1] == "You are 2nd with 0 wins. Leader: Sam L."
    prev = [{"playerId": 1, "fullName": "Sam Lee", "rank": 1, "wins": 0},
            {"playerId": 2, "fullName": "Ann Ray", "rank": 2, "wins": 0}]
    later = sp.build_messages(2, cur, prev)
    assert later[1][1] == "You held 1st (1 win, +1). You lead the pool."
    assert later[2][1] == "You held 2nd (0 wins, +0). Leader: Sam L."
