import pandas as pd
from unittest.mock import patch


class TestBuildPredictionsMapStamping:
    def test_fresh_entries_get_stamped(self):
        from scripts.backfill_schedule_predictions import _build_predictions_map

        ft_lookup = {
            (2025, 1, "KC", "BAL"): {
                "pred_prob": 0.6, "pred_winner": "KC", "pred_su_conf": 60.0,
                "pred_ats_pick": "KC", "model_spread": 2.0, "edge_vs_vegas": 1.0,
            }
        }
        result = _build_predictions_map(
            2025, ft_lookup, pd.DataFrame(), pd.DataFrame(), force=True,
            ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="abc1234",
        )
        entry = result["W01_KC_BAL"]
        assert entry["ensemble_version"] == "nn_v14+xgb_v9+lr_v7"
        assert entry["feature_version"] == "abc1234"

    def test_preserved_locked_entries_are_not_restamped(self):
        """A --force=False run must carry forward an already-locked entry
        from a prior run untouched -- it must NOT be overwritten with this
        run's version, since it wasn't produced by this run."""
        from scripts.backfill_schedule_predictions import _build_predictions_map

        old_entry = {
            "pred_prob": 0.4, "pred_winner": "BAL", "locked": True,
            "ensemble_version": "nn_v10+xgb_v4+lr_v2", "feature_version": "oldsha0000",
        }
        with patch(
            "scripts.backfill_schedule_predictions.get_game_predictions",
            return_value={"W02_KC_BAL": old_entry},
        ):
            result = _build_predictions_map(
                2025, {}, pd.DataFrame(), pd.DataFrame(), force=False,
                ensemble_version="nn_v14+xgb_v9+lr_v7", feature_version="newsha1111",
            )
        assert result["W02_KC_BAL"]["ensemble_version"] == "nn_v10+xgb_v4+lr_v2"
        assert result["W02_KC_BAL"]["feature_version"] == "oldsha0000"
