import math
import os
import time
import pandas as pd
from unittest.mock import patch, MagicMock
from services.draft_service import load_draft_state, sanitize_state

@patch("services.draft_service.pd.read_csv")
def test_load_draft_state(mock_read_csv):
    """Verify that the draft state resolves successfully against the CSVs without needing local file paths."""
    mock_connected_players = {1, 2, 3}
    
    mock_read_csv.return_value = pd.DataFrame([{"playerId": i} for i in range(10)])
    
    state = load_draft_state(mock_connected_players)
    
    assert "draft_board" in state
    assert "available_teams" in state
    assert "draft_ready" in state
    assert "all_players" in state
    
    # The draft engine now loads True automatically if an order exists.
    assert state["draft_ready"] is True
    assert len(state["all_players"]) > 0


def test_draft_board_entries_include_time_taken():
    """Each draft board entry must have a time_taken_seconds key (float or None)."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    for entry in state['draft_board']:
        assert 'time_taken_seconds' in entry, f"missing time_taken_seconds on pick {entry['pick']}"
        assert entry['time_taken_seconds'] is None or isinstance(entry['time_taken_seconds'], float)


def test_state_includes_connected_count():
    """State must include connected_count equal to the size of the connected_players set."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    assert state['connected_count'] == 3

    state2 = load_draft_state(set())
    assert state2['connected_count'] == 0


# ── Issue #90: sanitize_state NaN/Inf handling ───────────────────────────────

class TestSanitizeState:

    def test_nan_float_replaced_with_none(self):
        result = sanitize_state({"val": float("nan")})
        assert result["val"] is None

    def test_pos_inf_replaced_with_none(self):
        result = sanitize_state({"val": float("inf")})
        assert result["val"] is None

    def test_neg_inf_replaced_with_none(self):
        result = sanitize_state({"val": float("-inf")})
        assert result["val"] is None

    def test_nan_in_nested_list(self):
        result = sanitize_state({"picks": [float("nan"), 1.0, float("inf")]})
        assert result["picks"] == [None, 1.0, None]

    def test_normal_values_pass_through(self):
        obj = {"name": "KC", "wins": 12, "pct": 0.75, "items": [1, 2, 3]}
        assert sanitize_state(obj) == obj

    def test_string_nan_not_replaced(self):
        result = sanitize_state({"label": "nan"})
        assert result["label"] == "nan"


# ── Issue #94: singleton cache hit + save_pick time_taken ────────────────────

class TestLoadDraftStateSingleton:

    def test_singleton_cache_hit_returns_deepcopy_with_connected_players(self):
        """When _CACHED_DRAFT_STATE is set and year=None, returns deepcopy without DB calls."""
        import services.draft_service as ds

        cached = {
            "season": 2025,
            "active_pick": 5,
            "draft_board": [],
            "available_teams": ["KC"],
            "all_players": [{"playerId": 1, "fullName": "A"}, {"playerId": 2, "fullName": "B"}],
            "picks": [],
            "pick_start_time": int(time.time()),
            "draft_ready": True,
            "connected_count": 0,
            "connected_players": [],
        }
        ds._CACHED_DRAFT_STATE = cached
        try:
            state = ds.load_draft_state({1, 2})
        finally:
            ds._CACHED_DRAFT_STATE = None

        assert state["connected_count"] == 2
        assert set(state["connected_players"]) == {1, 2}
        assert state is not cached  # deepcopy, not the same object


# ── Draft-room payload must preserve true projected_wins, not mean_wins ──────

class TestPreseasonPredictionsShape:

    @patch("services.data_service.get_season_projection")
    def test_preseason_predictions_preserves_true_projected_wins(self, mock_get_season_projection):
        """ui_renderer.js:195 renders `${pred.projected_wins}W` straight from this
        payload. The resolver's "wins" field prefers the unrounded mean_wins (e.g.
        6.7), but the payload must still surface the original rounded projected_wins
        (e.g. 7.0) that `detail` carries -- substituting mean_wins here would silently
        change what the live 2026 draft room displays.

        Patched at the resolver rather than at draft_service's import so this still
        exercises the shared adapter (get_season_projection_legacy_shape) that the
        draft room, player profile and draft recap all now go through.
        """
        mock_get_season_projection.return_value = {
            "ARI": {
                "wins": 6.7,
                "source_type": "model",
                "detail": {
                    "projected_wins": 7.0,
                    "mean_wins": 6.7,
                    "std_dev": 2.6,
                    "sources": {"model": "nn_xgb_lr_ensemble"},
                },
            }
        }

        state = load_draft_state(set(), year=2023)

        assert state["preseason_predictions"]["ARI"]["projected_wins"] == 7.0


class TestSavePickTimeTaken:

    def test_cold_cache_produces_none_time_taken(self):
        """With no cached state, time_taken passed to add_draft_result is None."""
        import services.draft_service as ds
        ds._CACHED_DRAFT_STATE = None

        with patch("services.draft_service.add_draft_result") as mock_add:
            ds.save_pick(2025, 1, 99, "KC")

        _, _, _, _, _, time_taken = mock_add.call_args[0]
        assert time_taken is None

    def test_warm_cache_produces_elapsed_time_taken(self):
        """With pick_start_time set, time_taken is approximately the elapsed seconds."""
        import services.draft_service as ds
        start = time.time() - 10
        ds._CACHED_DRAFT_STATE = {"pick_start_time": start}

        with patch("services.draft_service.add_draft_result") as mock_add:
            ds.save_pick(2025, 1, 99, "KC")

        _, _, _, _, _, time_taken = mock_add.call_args[0]
        assert time_taken is not None
        assert 9 < time_taken < 15  # rough check: ~10 seconds elapsed
