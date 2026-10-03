import pytest

from services.constants import make_game_key


def test_make_game_key_basic_and_zero_pad():
    assert make_game_key(1, "KC", "BUF") == "W01_KC_BUF"
    assert make_game_key(12, "KC", "BUF") == "W12_KC_BUF"


def test_make_game_key_coerces_week_types():
    assert make_game_key("3", "KC", "BUF") == "W03_KC_BUF"
    assert make_game_key(3.0, "KC", "BUF") == "W03_KC_BUF"
    assert make_game_key("03", "KC", "BUF") == "W03_KC_BUF"


def test_make_game_key_normalizes_team_abbreviations():
    assert make_game_key(5, "oak", " wsh ") == "W05_LV_WAS"
    assert make_game_key(5, "KC", "BUF") == make_game_key(5, "kc", "buf")


def test_make_game_key_invalid_week_raises():
    with pytest.raises(ValueError):
        make_game_key("abc", "KC", "BUF")
