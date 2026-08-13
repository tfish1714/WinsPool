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
