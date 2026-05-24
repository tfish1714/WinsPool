# tests/test_analysis_perf.py
"""
Correctness-guard tests for vectorized rewrites of analysis_service hot paths.
Each test establishes a fixture with known inputs and asserts exact expected output.
Run BEFORE refactoring to confirm the test passes with the existing loop implementation,
then run again after refactoring to confirm the vectorized version matches.
"""
import pytest
import pandas as pd
import numpy as np
from services.analysis_service import (
    player_winsbyWeek,
    get_remaining_games,
    player_winlossmatrix,
)


# -- player_winsbyWeek --------------------------------------------------------

@pytest.fixture
def simple_schedule():
    """
    Two players, two weeks.
    Week 1: Alice beats Bob (result=-7, away=Alice wins)
    Week 2: Bob beats Alice (result=3, home=Bob wins)
    """
    return pd.DataFrame([
        {"week": 1, "fullName_away": "Alice", "fullName_home": "Bob", "result": -7.0},
        {"week": 2, "fullName_away": "Alice", "fullName_home": "Bob", "result": 3.0},
    ])


def test_wins_by_week_shape(simple_schedule):
    """Result must have rows = weeks+1 (Total row) and cols = players."""
    result = player_winsbyWeek(simple_schedule)
    # Rows: Total + Week 1 + Week 2 = 3
    assert len(result) == 3
    # Columns: Alice, Bob
    assert set(result.columns) == {"Alice", "Bob"}


def test_wins_by_week_alice_week1_cell(simple_schedule):
    """Alice wins week 1 away, so Week 1 cell = '1-0 (1-0)'."""
    result = player_winsbyWeek(simple_schedule)
    assert result.loc["Week 1", "Alice"] == "1-0 (1-0)"


def test_wins_by_week_bob_week2_cell(simple_schedule):
    """Bob wins week 2 at home, so Week 2 cell = '1-0 (1-1)'."""
    result = player_winsbyWeek(simple_schedule)
    # After week 1 Bob was 0-1, after week 2 he is 1-1 cumulative
    assert result.loc["Week 2", "Bob"] == "1-0 (1-1)"


def test_wins_by_week_total_row(simple_schedule):
    """Total row: Alice 1-1, Bob 1-1."""
    result = player_winsbyWeek(simple_schedule)
    assert result.loc["Total", "Alice"] == "1-1"
    assert result.loc["Total", "Bob"] == "1-1"


# -- get_remaining_games ------------------------------------------------------

def test_remaining_games_basic():
    """Existing test: 1 away game + 1 both-player game = 3 remaining."""
    df = pd.DataFrame([
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "Opp"},
        {"result": pd.NA, "fullName_away": "TFish", "fullName_home": "TFish"},
        {"result": 10.0, "fullName_away": "TFish", "fullName_home": "Opp"},  # played
    ])
    assert get_remaining_games("TFish", df) == 3


def test_remaining_games_no_remaining():
    """All games played -- remaining = 0."""
    df = pd.DataFrame([
        {"result": 10.0, "fullName_away": "A", "fullName_home": "B"},
    ])
    assert get_remaining_games("A", df) == 0


# -- player_winlossmatrix -----------------------------------------------------

@pytest.fixture
def matrix_schedule():
    """Three games: Alice beats Bob twice, Bob beats Carol once."""
    return pd.DataFrame([
        {"fullName_away": "Alice", "fullName_home": "Bob", "result": -3.0},   # Alice wins
        {"fullName_away": "Alice", "fullName_home": "Bob", "result": -7.0},   # Alice wins
        {"fullName_away": "Carol", "fullName_home": "Bob", "result": 5.0},    # Bob wins
    ])


def test_winlossmatrix_shape(matrix_schedule):
    """Matrix must be square: players x players + 1 overall column."""
    result = player_winlossmatrix(matrix_schedule)
    players = {"Alice", "Bob", "Carol"}
    assert players.issubset(set(result.index))
    assert "Overall Record" in result.columns


def test_winlossmatrix_alice_vs_bob(matrix_schedule):
    """Alice beat Bob twice -> matrix[Alice][Bob] = '2-0'."""
    result = player_winlossmatrix(matrix_schedule)
    assert result.loc["Alice", "Bob"] == "2-0"


def test_winlossmatrix_bob_overall(matrix_schedule):
    """Bob's overall record: 1 win (vs Carol), 2 losses (vs Alice)."""
    result = player_winlossmatrix(matrix_schedule)
    assert result.loc["Bob", "Overall Record"] == "1-2"


def test_winlossmatrix_pure_tie():
    """When two players only tied, H2H cell should be '0-0-1' not '0-0'."""
    df = pd.DataFrame([
        {"fullName_away": "Alice", "fullName_home": "Bob", "result": 0.0},
    ])
    result = player_winlossmatrix(df)
    assert result.loc["Alice", "Bob"] == "0-0-1"
    assert result.loc["Bob", "Alice"] == "0-0-1"


def test_winlossmatrix_win_plus_tie_loser():
    """When Alice beat Bob once AND tied once, Bob's cell vs Alice should be '0-1-1'."""
    df = pd.DataFrame([
        {"fullName_away": "Alice", "fullName_home": "Bob", "result": -3.0},  # Alice wins
        {"fullName_away": "Alice", "fullName_home": "Bob", "result": 0.0},   # Tie
    ])
    result = player_winlossmatrix(df)
    assert result.loc["Bob", "Alice"] == "0-1-1"
    assert result.loc["Alice", "Bob"] == "1-0-1"
