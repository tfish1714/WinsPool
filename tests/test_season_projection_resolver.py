"""The resolver that lets both collections carry a single meaning."""
import services.data_service as data_service


def test_prefers_model_when_present(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 13.0, "mean_wins": 12.6}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_median": 9.5, "sources": {}}})

    res = data_service.get_season_projection(2026)
    assert res["LA"]["source_type"] == "model"
    assert res["LA"]["wins"] == 12.6


def test_falls_back_to_consensus(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_median": 7.5, "sources": {"br": 10}}})

    res = data_service.get_season_projection(2020)
    assert res["ARI"]["source_type"] == "consensus"
    assert res["ARI"]["wins"] == 7.5


def test_empty_when_neither_exists(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})
    assert data_service.get_season_projection(1999) == {}


def test_mixed_teams_resolve_independently(monkeypatch):
    """A team with a model row uses it; one without falls back."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 13.0, "mean_wins": 12.6}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_median": 9.5, "sources": {}},
                                   "ARI": {"consensus_median": 7.5, "sources": {}}})

    res = data_service.get_season_projection(2026)
    assert res["LA"]["source_type"] == "model"
    assert res["ARI"]["source_type"] == "consensus"


# --- The legacy shape every UI-facing caller reads -------------------------


def test_legacy_shape_model_season(monkeypatch):
    """Model rows keep their own projected_wins -- not the unrounded mean."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 13.0, "mean_wins": 12.6,
                                          "std_dev": 2.4, "sources": {"model": "x"}}})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    res = data_service.get_season_projection_legacy_shape(2026)
    assert res["LA"] == {"projected_wins": 13.0, "mean_wins": 12.6,
                         "std_dev": 2.4, "sources": {"model": "x"}}


def test_legacy_shape_consensus_season(monkeypatch):
    """Consensus detail has no projected_wins, so it falls back to wins.

    Every value here is distinct, so a branch that read the wrong key would
    produce a visibly wrong number rather than coincidentally passing.
    """
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_median": 7.5, "consensus_mean": 8.25,
                                           "consensus_std": 1.75, "sources": {"br": 10}}})

    res = data_service.get_season_projection_legacy_shape(2017)
    assert res["ARI"] == {"projected_wins": 7.5, "mean_wins": 8.25,
                          "std_dev": 1.75, "sources": {"br": 10}}


def test_legacy_shape_never_reports_zero_for_a_real_projection(monkeypatch):
    """The regression this guards: an empty model dict must not zero the page."""
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_median": 7.5, "consensus_mean": 8.25,
                                           "consensus_std": 1.75, "sources": {}}})

    res = data_service.get_season_projection_legacy_shape(2023)
    assert res["ARI"]["projected_wins"] == 7.5
    assert res["ARI"]["std_dev"] != 0


def test_legacy_shape_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})
    assert data_service.get_season_projection_legacy_shape(1999) == {}


# --- The blended shape used by the running portfolio and draft recap ------


def test_blended_weights_toward_the_more_confident_source(monkeypatch):
    """Model is 3x more confident (half the std_dev) than consensus here, so
    the blend should land closer to the model's mean_wins than a straight
    50/50 average would."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"mean_wins": 12.0, "std_dev": 1.0,
                                          "projected_wins": 12.0, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_mean": 8.0, "consensus_median": 8.0,
                                          "consensus_std": 2.0, "sources": {"br": 8}}})

    res = data_service.get_season_projection_blended(2026)
    straight_average = (12.0 + 8.0) / 2
    assert straight_average < res["LA"]["projected_wins"] < 12.0


def test_blended_falls_back_to_model_only(monkeypatch):
    """No consensus at all -- the team must keep the model's own established
    display value (projected_wins), not get switched to mean_wins."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"mean_wins": 12.6, "std_dev": 2.4,
                                          "projected_wins": 13.0, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    res = data_service.get_season_projection_blended(2026)
    assert res["LA"]["projected_wins"] == 13.0
    assert res["LA"]["mean_wins"] == 12.6


def test_blended_falls_back_to_consensus_only(monkeypatch):
    """No model at all -- the team must keep consensus's own established
    display value (consensus_median), not get switched to consensus_mean."""
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_mean": 8.25, "consensus_median": 7.5,
                                           "consensus_std": 1.75, "sources": {"br": 10}}})

    res = data_service.get_season_projection_blended(2017)
    assert res["ARI"]["projected_wins"] == 7.5
    assert res["ARI"]["mean_wins"] == 8.25


def test_blended_falls_back_to_model_when_a_std_dev_is_missing(monkeypatch):
    """Can't inverse-variance-weight a zero/missing std_dev -- use the model
    outright rather than dividing by zero or silently mis-weighting."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"mean_wins": 12.0, "std_dev": 0,
                                          "projected_wins": 12.0, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_mean": 8.0, "consensus_median": 8.0,
                                          "consensus_std": 2.0, "sources": {}}})

    res = data_service.get_season_projection_blended(2026)
    assert res["LA"]["projected_wins"] == 12.0


def test_blended_empty_when_no_data(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})
    assert data_service.get_season_projection_blended(1999) == {}
