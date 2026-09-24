"""The two schedule-enrichment functions that have no production caller
(services/prediction_service.py::enrich_schedule_with_predictions and
services/nn_projection_engine.py::enrich_schedule_with_nn_predictions) must
still agree with the canonical ATS rule in services/utils.py, so a stray call
can never disagree with what the live pipeline stores.

Sign convention: positive spread_line / model_spread = home favored."""
import pandas as pd
import pytest

import services.nn_projection_engine as npe
import services.prediction_service as ps
from services.utils import derive_prediction_scalars, prob_to_model_spread


class _StubService:
    """Stands in for PredictionService / NNProjectionEngine: fixed home win
    probability, no model loading."""

    prob = 0.5

    def initialize(self, *args, **kwargs):
        pass

    def game_win_probability(self, home, away, **kwargs):
        return {"home_win_prob": type(self).prob}


def _schedule(spread_line, result=-1000):
    return pd.DataFrame([{"home_team": "KC", "away_team": "BUF", "result": result,
                          "spread_line": spread_line, "week": 3}])


def _enrich(monkeypatch, which, prob, spread_line, result=-1000):
    _StubService.prob = prob
    if which == "elo":
        monkeypatch.setattr(ps, "PredictionService", _StubService)
        return ps.enrich_schedule_with_predictions(_schedule(spread_line, result), pd.DataFrame(), 2024)
    monkeypatch.setattr(npe, "NNProjectionEngine", _StubService)
    return npe.enrich_schedule_with_nn_predictions(_schedule(spread_line, result), 2024)


def _canonical(prob, spread_line):
    return derive_prediction_scalars("KC", "BUF", prob, prob_to_model_spread(prob), spread_line)


@pytest.fixture(params=["elo", "nn"])
def which(request):
    return request.param


@pytest.mark.parametrize("prob, line", [
    (0.75, -2.0),   # home favored by the model (+7.6); Vegas has the AWAY side favored
    (0.55, 5.0),    # model likes home by ~1.2, Vegas by 5 -> back away
    (0.75, 3.0),    # model +7.6 vs Vegas +3 -> back home
    (0.30, -7.0),   # model likes away by ~3.7 (-3.7 > -7) -> home covers the big away line
    (0.5, 0.0),     # pick'em vs pick'em: tie goes to away
])
def test_ats_pick_matches_the_canonical_rule(monkeypatch, which, prob, line):
    out = _enrich(monkeypatch, which, prob, line)
    expected = _canonical(prob, line)

    assert out.iloc[0]["pred_ats_pick"] == expected["pred_ats_pick"]
    assert out.iloc[0]["pred_winner"] == expected["pred_winner"]
    assert out.iloc[0]["pred_su_conf"] == expected["pred_su_conf"]


def test_no_spread_line_falls_back_to_the_straight_up_winner(monkeypatch, which):
    out = _enrich(monkeypatch, which, 0.7, None)
    assert out.iloc[0]["pred_ats_pick"] == out.iloc[0]["pred_winner"] == "KC"


def test_nan_spread_line_falls_back_to_the_straight_up_winner(monkeypatch, which):
    out = _enrich(monkeypatch, which, 0.3, float("nan"))
    assert out.iloc[0]["pred_ats_pick"] == out.iloc[0]["pred_winner"] == "BUF"


def test_rounding_boundary_agrees_with_the_canonical_rule(monkeypatch, which):
    """The canonical spread is rounded to one decimal before the comparison.
    An unrounded comparison flips this game (implied ~3.04 vs line 3.02): the
    drift the shared helper exists to prevent."""
    prob, line = 0.6078, 3.02
    expected = _canonical(prob, line)["pred_ats_pick"]

    out = _enrich(monkeypatch, which, prob, line)

    assert expected == "BUF"  # rounded 3.0 is not > 3.02
    assert out.iloc[0]["pred_ats_pick"] == expected


def test_completed_games_still_get_no_prediction(monkeypatch, which):
    out = _enrich(monkeypatch, which, 0.7, 3.0, result=7.0)
    assert out.iloc[0]["pred_winner"] is None
    assert out.iloc[0]["pred_ats_pick"] is None
