import pytest


class TestFindSameSchemaBest:
    def test_returns_none_when_no_entries(self):
        from services.model_promotion import find_same_schema_best
        assert find_same_schema_best([], ["a", "b"]) is None

    def test_ignores_entries_with_different_schema(self):
        from services.model_promotion import find_same_schema_best
        entries = [
            {"feature_columns": ["a"], "metrics": {"test_auc": 0.9}},
        ]
        assert find_same_schema_best(entries, ["a", "b"]) is None

    def test_picks_highest_test_auc_among_matching_schema(self):
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [
            {"feature_columns": cols, "metrics": {"test_auc": 0.55}},
            {"feature_columns": cols, "metrics": {"test_auc": 0.60}},
            {"feature_columns": ["a"], "metrics": {"test_auc": 0.99}},  # different schema, ignored
        ]
        result = find_same_schema_best(entries, cols)
        assert result == {"test_auc": 0.60}

    def test_ignores_entries_missing_test_auc(self):
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [{"feature_columns": cols, "metrics": {"test_accuracy": 0.7}}]
        assert find_same_schema_best(entries, cols) is None


class TestAssertPromotionReady:
    def test_noop_when_best_is_none(self):
        from services.model_promotion import assert_promotion_ready
        assert_promotion_ready({"test_auc": 0.1}, None, "XGB")  # must not raise

    def test_raises_on_regression_beyond_tolerance(self):
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.50, "test_auc": 0.50}
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        with pytest.raises(ValueError, match="regressed"):
            assert_promotion_ready(new_metrics, best_metrics, "XGB")

    def test_allows_regression_within_tolerance(self):
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.591, "test_auc": 0.591}
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        assert_promotion_ready(new_metrics, best_metrics, "XGB")  # 0.9pp < 2pp tolerance, must not raise

    def test_skips_metric_missing_from_new_metrics(self):
        """NN's _eval_metrics never has test_auc -- the gate must silently
        skip that one metric rather than raising over a missing key."""
        from services.model_promotion import assert_promotion_ready
        new_metrics = {"test_accuracy": 0.591}  # no test_auc key at all
        best_metrics = {"test_accuracy": 0.60, "test_auc": 0.60}
        assert_promotion_ready(new_metrics, best_metrics, "NN")  # must not raise
