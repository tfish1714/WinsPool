"""get_active_season(): the shared 'current season' resolver used across
standings, schedule, draft, playoff-race, and head-to-head pages.

A season with zero games played (the week between draft day and kickoff)
must still become active once its draft is fully complete -- otherwise
every one of those pages stays pointed at last season until the first
game finishes.
"""
import pandas as pd

from services.data_service import get_active_season

GAMES = pd.DataFrame([
    {"season": 2025, "result": 3.0},
    {"season": 2026, "result": None},
])

PRIOR_SEASON_DRAFT = [{"season": 2025, "team": f"T{i}"} for i in range(30)]
PRIOR_SEASON_RULES = [{"season": 2025, "draftOrder": i} for i in range(10)]


def _draft(new_season_picks: int):
    rows = PRIOR_SEASON_DRAFT + [{"season": 2026, "team": f"T{i}"} for i in range(new_season_picks)]
    return pd.DataFrame(rows)


def _rules(new_season_players: int = 10):
    rows = PRIOR_SEASON_RULES + [{"season": 2026, "draftOrder": i} for i in range(new_season_players)]
    return pd.DataFrame(rows)


def test_advances_to_new_season_once_its_draft_is_fully_complete():
    assert get_active_season(GAMES, _draft(30), _rules()) == 2026


def test_stays_on_prior_season_while_new_season_draft_is_partial():
    assert get_active_season(GAMES, _draft(12), _rules()) == 2025


def test_stays_on_prior_season_when_rules_not_passed():
    """Backward compatible: callers that don't pass rules keep the old,
    games-results-only behavior."""
    assert get_active_season(GAMES, _draft(30)) == 2025


def test_stays_on_prior_season_when_new_season_has_no_rules_configured():
    """No draft_order_rules row for 2026 -- picks_expected is 0, so
    completeness can't be evaluated; don't advance on a false positive."""
    assert get_active_season(GAMES, _draft(30), pd.DataFrame(PRIOR_SEASON_RULES)) == 2025
