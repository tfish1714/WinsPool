"""services/live_score_service.py::sync_live_scores_to_df() -- team
abbreviation matching. ESPN returns raw abbreviations (LAR/WSH/JAC) that
differ from nflverse's (LA/WAS/JAX, the repo's own canonical form) -- see
services/utils.py::normalize_team_abbr(). The repo side of games_df is
already in canonical form; only the ESPN side needs normalizing before the
two are compared."""
import pandas as pd

import services.live_score_service as lss


def _repo_game(home, away):
    return pd.DataFrame([
        {"home_team": home, "away_team": away, "home_score": 0, "away_score": 0, "result": None},
    ])


def test_rams_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'LA' must match ESPN's raw 'LAR'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("LAR", "SF"): {"home_score": 20, "away_score": 17, "status": "STATUS_IN_PROGRESS",
                        "clock": "5:23", "period": 3},
    })

    result = lss.sync_live_scores_to_df(_repo_game("LA", "SF"))

    assert result.at[0, "home_score"] == 20
    assert result.at[0, "away_score"] == 17
    assert result.at[0, "is_live"] == True


def test_commanders_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'WAS' must match ESPN's raw 'WSH'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("WSH", "DAL"): {"home_score": 14, "away_score": 10, "status": "STATUS_IN_PROGRESS",
                        "clock": "2:00", "period": 2},
    })

    result = lss.sync_live_scores_to_df(_repo_game("WAS", "DAL"))

    assert result.at[0, "home_score"] == 14
    assert result.at[0, "away_score"] == 10
    assert result.at[0, "is_live"] == True


def test_jaguars_game_matches_after_espn_key_normalization(monkeypatch):
    """Repo's canonical 'JAX' must match ESPN's raw 'JAC'."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("JAC", "TEN"): {"home_score": 7, "away_score": 3, "status": "STATUS_IN_PROGRESS",
                        "clock": "10:00", "period": 1},
    })

    result = lss.sync_live_scores_to_df(_repo_game("JAX", "TEN"))

    assert result.at[0, "home_score"] == 7
    assert result.at[0, "away_score"] == 3
    assert result.at[0, "is_live"] == True


def test_already_matching_abbreviations_still_work(monkeypatch):
    """Regression guard: a team whose abbreviation needs no normalization
    (e.g. KC) must keep matching exactly as before this fix."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 24, "away_score": 21, "status": "STATUS_IN_PROGRESS",
                        "clock": "1:00", "period": 4},
    })

    result = lss.sync_live_scores_to_df(_repo_game("KC", "BUF"))

    assert result.at[0, "home_score"] == 24
    assert result.at[0, "away_score"] == 21


def test_no_matching_game_leaves_scores_unchanged(monkeypatch):
    """A game ESPN doesn't report on at all must be left untouched."""
    monkeypatch.setattr(lss, "get_live_updates", lambda: {
        ("KC", "BUF"): {"home_score": 24, "away_score": 21, "status": "STATUS_IN_PROGRESS",
                        "clock": "1:00", "period": 4},
    })

    result = lss.sync_live_scores_to_df(_repo_game("LA", "SF"))

    assert result.at[0, "home_score"] == 0
    assert result.at[0, "away_score"] == 0


def test_empty_games_df_returns_unchanged():
    result = lss.sync_live_scores_to_df(pd.DataFrame())
    assert result.empty


def test_no_live_data_returns_games_df_unchanged(monkeypatch):
    monkeypatch.setattr(lss, "get_live_updates", lambda: {})
    games = _repo_game("LA", "SF")
    result = lss.sync_live_scores_to_df(games)
    pd.testing.assert_frame_equal(result, games)
