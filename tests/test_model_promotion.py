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
            {"feature_columns": cols, "metrics": {"test_accuracy": 0.55, "test_auc": 0.55}},
            {"feature_columns": cols, "metrics": {"test_accuracy": 0.60, "test_auc": 0.60}},
            {"feature_columns": ["a"], "metrics": {"test_accuracy": 0.99, "test_auc": 0.99}},  # different schema, ignored
        ]
        result = find_same_schema_best(entries, cols)
        assert result == {"test_accuracy": 0.60, "test_auc": 0.60}

    def test_falls_back_to_test_accuracy_when_test_auc_missing(self):
        """NN entries never carry test_auc -- they must still be a valid
        candidate as long as test_accuracy is present."""
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [{"feature_columns": cols, "metrics": {"test_accuracy": 0.7}}]
        assert find_same_schema_best(entries, cols) == {"test_accuracy": 0.7}

    def test_ignores_entries_missing_both_test_auc_and_test_accuracy(self):
        """The real 'nothing to gate against' case now that the filter's
        ignore-condition is keyed on test_accuracy instead of test_auc."""
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [{"feature_columns": cols, "metrics": {"season_r2": 0.9}}]
        assert find_same_schema_best(entries, cols) is None

    def test_ranks_by_test_accuracy_when_all_candidates_lack_test_auc(self):
        """NN-shaped entries (no test_auc at all): higher test_accuracy wins."""
        from services.model_promotion import find_same_schema_best
        cols = ["a", "b"]
        entries = [
            {"feature_columns": cols, "metrics": {"test_accuracy": 0.55}},
            {"feature_columns": cols, "metrics": {"test_accuracy": 0.65}},
        ]
        result = find_same_schema_best(entries, cols)
        assert result == {"test_accuracy": 0.65}


class TestAssertPromotionReady:
    def test_noop_when_best_is_none(self):
        from services.model_promotion import assert_promotion_ready
        assert_promotion_ready({"test_auc": 0.1}, None, "XGB")  # must not raise

    def test_logs_when_no_same_schema_baseline(self, caplog):
        """Finding 1 (final review): the no-op path must be observable --
        otherwise a first-of-generation model (or every NN entry, which
        currently all lack feature_columns) silently never gets gated, with
        no way to tell "gate skipped" from "gate ran and passed"."""
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_auc": 0.1}, None, "XGB")
        assert any(
            "XGB" in record.getMessage() and "promotion gate skipped" in record.getMessage()
            for record in caplog.records
        )

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


class TestPromotionGateLogging:
    @staticmethod
    def _gate_records(caplog):
        return [r for r in caplog.records if "promotion_gate" in r.getMessage()]

    @classmethod
    def _decisions(cls, caplog):
        return [r for r in cls._gate_records(caplog) if "decision=" in r.getMessage()]

    def test_promoted_decision_logs_candidate_and_baseline(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.58, "test_auc": 0.59},
                                   {"test_accuracy": 0.5625, "test_auc": 0.5764}, "XGB")
        line = self._decisions(caplog)[0].getMessage()
        assert "decision=PROMOTED" in line and "model=XGB" in line
        assert "candidate_accuracy=0.5800" in line and "baseline_accuracy=0.5625" in line
        assert "candidate_auc=0.5900" in line and "baseline_auc=0.5764" in line
        assert "tolerances=accuracy:-0.05,auc:-0.02" in line

    def test_rejected_decision_is_a_warning_naming_failed_metrics(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"), pytest.raises(ValueError, match="regressed"):
            assert_promotion_ready({"test_accuracy": 0.50, "test_auc": 0.50},
                                   {"test_accuracy": 0.60, "test_auc": 0.60}, "LR")
        decisions = self._decisions(caplog)
        assert len(decisions) == 1
        rec = decisions[0]
        assert rec.levelname == "WARNING"
        assert "decision=REJECTED" in rec.getMessage()
        assert "model=LR" in rec.getMessage()
        assert "failed=test_accuracy,test_auc" in rec.getMessage()

    def test_skipped_no_baseline_decision_keeps_legacy_message(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.55}, None, "NN")
        decisions = self._decisions(caplog)
        assert len(decisions) == 1
        line = decisions[0].getMessage()
        assert "decision=SKIPPED_NO_BASELINE" in line
        assert "candidate_accuracy=0.5500" in line and "candidate_auc=n/a" in line
        assert "promotion gate skipped" in line

    def test_missing_metrics_render_as_na_not_a_crash(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.6}, {"test_accuracy": 0.6}, "NN")
        line = self._decisions(caplog)[0].getMessage()
        assert "decision=PROMOTED" in line
        assert "candidate_auc=n/a" in line and "baseline_auc=n/a" in line

    def test_baseline_without_auc_and_non_float_value_do_not_crash(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.6, "test_auc": "oops"},
                                   {"test_accuracy": 0.6}, "NN")
        line = self._decisions(caplog)[0].getMessage()
        assert "candidate_auc=n/a" in line and "baseline_auc=n/a" in line

    def test_schema_check_line_reports_match_counts(self, caplog):
        from services.model_promotion import find_same_schema_best
        entries = [
            {"feature_columns": ["a", "b"], "metrics": {"test_accuracy": 0.5, "test_auc": 0.5}},
            {"feature_columns": ["a", "b"], "metrics": {"test_accuracy": 0.6, "test_auc": 0.6}},
            {"feature_columns": ["a", "c"], "metrics": {"test_accuracy": 0.7, "test_auc": 0.7}},
        ]
        with caplog.at_level("INFO"):
            best = find_same_schema_best(entries, ["a", "b"], model_name="XGB")
        assert best["test_auc"] == 0.6
        line = next(r.getMessage() for r in self._gate_records(caplog) if "schema_check" in r.getMessage())
        assert "model=XGB" in line and "entries=3" in line and "same_schema=2" in line and "feature_columns=2" in line

    def test_schema_check_with_no_match_says_zero(self, caplog):
        from services.model_promotion import find_same_schema_best
        with caplog.at_level("INFO"):
            assert find_same_schema_best([], ["a"], model_name="NN") is None
        line = next(r.getMessage() for r in self._gate_records(caplog) if "schema_check" in r.getMessage())
        assert "same_schema=0" in line and "entries=0" in line

    def test_find_same_schema_best_still_works_without_model_name(self):
        from services.model_promotion import find_same_schema_best
        assert find_same_schema_best([], ["a"]) is None

    def test_exactly_one_decision_line_per_call(self, caplog):
        from services.model_promotion import assert_promotion_ready
        with caplog.at_level("INFO"):
            assert_promotion_ready({"test_accuracy": 0.6, "test_auc": 0.6},
                                   {"test_accuracy": 0.6, "test_auc": 0.6}, "XGB")
        assert len(self._decisions(caplog)) == 1
