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
from typing import Optional

PROMOTION_GATES: dict[str, float] = {
    # metric_name: max allowed regression vs. same-schema best
    # (negative tolerance -- new must not be more than this much worse)
    "test_accuracy": -0.02,
    "test_auc": -0.02,
}


def find_same_schema_best(entries: list[dict], new_feature_columns: list[str]) -> Optional[dict]:
    """Return the metrics dict of the best entry that shares
    new_feature_columns exactly, or None if no entry shares this schema
    (the first model of its generation -- nothing to gate against yet).

    Ranks by test_auc when every candidate has it (XGB/LR); falls back to
    test_accuracy otherwise (NN, which never computes test_auc) -- both
    metrics live on a comparable 0-1 scale, so the fallback doesn't change
    what "best" means, only what's measurable for that model type.
    """
    candidates = [
        e for e in entries
        if e.get("feature_columns") == new_feature_columns
        and e.get("metrics", {}).get("test_accuracy") is not None
    ]
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
        raise ValueError(
            f"{model_name}: new model regressed vs. same-schema best on held-out "
            f"test set: {failures}. Not promoting -- investigate before overriding "
            f"with --force-promote."
        )
