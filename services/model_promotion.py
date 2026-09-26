"""services/model_promotion.py -- Training-time promotion gate.

Stops a newly-trained model from being written as the resolved
"latest"/"best" version when it regresses vs. the best model sharing its
EXACT feature schema. Comparing across a feature-schema change (e.g.
spread_line removed in the "ML De-Vegas Pass") is comparing against
leakage, not a quality bar -- see
docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md
"Folded-in designs" > "Model quality gating" for the incident (an invalid
XGB v9-vs-v3 comparison) this exists to prevent.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

PROMOTION_GATES: dict[str, float] = {
    # metric_name: max allowed regression vs. same-schema best
    # (negative tolerance -- new must not be more than this much worse)
    #
    # test_accuracy gets a wider tolerance than test_auc: it's quantized to
    # whole games on a held-out test split (~1/48-1/64 per game depending on
    # the model), so a tight tolerance triggers on ordinary single-game
    # sampling noise, not just real regressions. This matters most for NN,
    # which never computes test_auc (only test_r2/test_mae/test_accuracy),
    # so it has no continuous metric to stabilize the gate the way XGB/LR's
    # test_auc does. test_auc's tolerance stays tight -- it's a continuous
    # probability-based metric with no per-game quantization, and -0.02 was
    # independently confirmed against real historical registry data to
    # correctly catch a real regression (XGB v9).
    "test_accuracy": -0.05,
    "test_auc": -0.02,
}


def _fmt(value) -> str:
    """Format a metric for a log line; None or non-numeric renders as n/a."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{value:.4f}"


def _decision_fields(model_name: str, new_metrics: dict, best_metrics: Optional[dict]) -> str:
    best = best_metrics or {}
    return (
        f"model={model_name} "
        f"candidate_accuracy={_fmt(new_metrics.get('test_accuracy'))} "
        f"baseline_accuracy={_fmt(best.get('test_accuracy'))} "
        f"candidate_auc={_fmt(new_metrics.get('test_auc'))} "
        f"baseline_auc={_fmt(best.get('test_auc'))}"
    )


def find_same_schema_best(
    entries: list[dict], new_feature_columns: list[str], model_name: str = ""
) -> Optional[dict]:
    """Return the metrics dict of the best entry that shares
    new_feature_columns exactly, or None if no entry shares this schema
    (the first model of its generation -- nothing to gate against yet).

    Ranks by test_auc when every candidate has it (XGB/LR); falls back to
    test_accuracy otherwise (NN, which never computes test_auc) -- both
    metrics live on a comparable 0-1 scale, so the fallback doesn't change
    what "best" means, only what's measurable for that model type.

    Logs a `promotion_gate schema_check` line. `entries` in that line is
    the list the caller passed in; the XGB/LR services pre-filter to
    registry entries that carry feature_columns, so it reflects that
    filtered list, not the raw registry size.
    """
    candidates = [
        e for e in entries
        if e.get("feature_columns") == new_feature_columns
        and e.get("metrics", {}).get("test_accuracy") is not None
    ]
    logger.info(
        "promotion_gate schema_check model=%s entries=%d same_schema=%d feature_columns=%d",
        model_name or "?", len(entries), len(candidates), len(new_feature_columns),
    )
    if not candidates:
        return None
    rank_key = "test_auc" if all(
        e.get("metrics", {}).get("test_auc") is not None for e in candidates
    ) else "test_accuracy"
    return max(candidates, key=lambda e: e["metrics"][rank_key])["metrics"]


def assert_promotion_ready(new_metrics: dict, best_metrics: Optional[dict], model_name: str) -> None:
    """Raise ValueError if new_metrics regresses vs. best_metrics beyond
    PROMOTION_GATES' tolerance on any gated metric present in both.

    No-ops if best_metrics is None (nothing comparable exists yet) or a
    gated metric is missing from either side -- e.g. NN's _eval_metrics
    never computes test_auc, so only test_accuracy gates it.
    """
    if best_metrics is None:
        logger.info(
            "promotion_gate decision=SKIPPED_NO_BASELINE %s "
            "(%s: no same-schema baseline in registry; promotion gate skipped)",
            _decision_fields(model_name, new_metrics, None), model_name,
        )
        return
    failures = {}
    for metric, tolerance in PROMOTION_GATES.items():
        new_v = new_metrics.get(metric)
        best_v = best_metrics.get(metric)
        if new_v is None or best_v is None:
            continue
        if new_v < best_v + tolerance:
            failures[metric] = {"new": new_v, "best": best_v, "tolerance": tolerance}
    if failures:
        logger.warning(
            "promotion_gate decision=REJECTED %s failed=%s",
            _decision_fields(model_name, new_metrics, best_metrics), ",".join(sorted(failures)),
        )
        raise ValueError(
            f"{model_name}: new model regressed vs. same-schema best on held-out "
            f"test set: {failures}. Not promoting -- investigate before overriding "
            f"with --force-promote."
        )
    logger.info(
        "promotion_gate decision=PROMOTED %s tolerances=%s",
        _decision_fields(model_name, new_metrics, best_metrics),
        ",".join(f"{m.replace('test_', '')}:{t}" for m, t in PROMOTION_GATES.items()),
    )
