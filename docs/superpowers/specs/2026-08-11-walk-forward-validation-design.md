# Walk-Forward Validation — Design Spec (STUB)

**Date:** 2026-08-11
**Status:** Stub — pending brainstorm session to finalize
**Depends on:** `2026-08-11-consensus-benchmark-design.md` (Spec A) for the
`consensus_projections` collection this scores against.

---

## Problem

Model version selection is being driven by a test metric too small to resolve it.

`_split_data()` in `nn_prediction_service.py` (and its twins in
`xgb_prediction_service.py` and `lr_prediction_service.py`) splits
chronologically — correctly, with no leakage — as:

- **train:** seasons `< max_season`
- **validation:** `max_season`, weeks `≤ 14`
- **test:** `max_season`, weeks `> 14`

The test set is therefore the final four weeks of a single season: roughly **64
games**. On binary outcomes that carries a standard error near 6 percentage
points, so differences between versions are indistinguishable from noise. The
model registry shows the consequence directly: `best_by.test_accuracy` is **v6**
while `latest` is **v14** — fourteen versions of selection pressure applied to a
metric that cannot separate them.

A second gap: there is no season where model projections and analyst consensus
coexist, so the question "has the model ever been better than the consensus?"
is unanswerable. Spec A creates the consensus side; this spec creates the model
side and scores both against actual results.

Note that `game_predictions_{2018..2026}.json` do **not** fill this gap. They are
game-level predictions built from in-season features (`point_diff_advantage`,
current `elo_diff`, rest, Vegas line), so they are not preseason forecasts; and
for seasons before 2025 they are in-sample, since the models trained on those
seasons.

---

## Intended Approach (to be refined in brainstorm)

### Expanding-window walk-forward

For each fold season `S` in roughly 2015–2025:

1. Train NN, XGB and LR on all seasons `≤ S−1`.
2. Predict every game of season `S` — fully out-of-sample.
3. Generate a preseason-only season projection for `S` via `NNProjectionEngine`,
   using only information available before `S` kicked off.
4. Score game-level accuracy, log loss and Brier, plus season-level projection
   MAE against actual wins.

Roughly 3,000 out-of-sample games in total, against the current 64.

### Benchmark bar

Analyst consensus accuracy, measured from the data Spec A migrates:

| Season | Consensus MAE vs actual | r |
|---|---|---|
| 2024 | 2.68 | 0.48 |
| 2025 | 2.35 | 0.51 |

Preseason boards typically land MAE ≈ 2.0–2.5. The model has to beat that on
seasons it never trained toward to be worth trusting over the sites.

One caution established while investigating: preseason projections correlate
around **0.8 with the prior season's** results and only around **0.5 with the
season they forecast**, because analysts anchor on last year's record. A high
correlation against season `S−1` is expected anchoring, not evidence of a
labeling bug or of leakage.

### Reporting

Extend `reports/` alongside `nn_weekly_accuracy.csv`, and add the historical
model-vs-consensus-vs-actual comparison to the Spec A admin Consensus tab as a
season selector.

---

## Open Questions for Brainstorm

1. Which season does the walk-forward start at? Feature availability differs —
   `roster_talent_delta` and `trench_dominance_metric` need `stats_team_week_*`
   (2020+), snap counts reach back to 2012, and the preseason profile path needs
   depth charts. Folds before the feature floor would train on a degraded table.
2. Retrain cost: 11 folds × 3 models. Needs a timing measurement before
   committing. Is a reduced fold count or a cheaper NN configuration acceptable?
3. Are per-fold models kept as artifacts, or discarded after scoring? Keeping
   33 model files has storage and registry implications.
4. Does walk-forward replace `_split_data` as the reported metric, or run
   alongside it as a separate harness invoked by its own script?
5. Should `best_by` in the three registries be recomputed from walk-forward
   results, superseding the current noise-driven selection?
6. Do historical model projections overwrite the 2017–2025 rows in
   `preseason_predictions` — changing what history views display — or land in a
   separate store? Spec A explicitly defers this decision here.
7. Ensemble weights (45% NN + 20% XGB + 35% LR) were set before any reliable
   out-of-sample estimate existed. Should walk-forward refit them?

---

## Out of Scope

- Anything in Spec A.
- Feeding consensus into the model as a feature or prior.
- Playoff bracket simulation (would be required for a Super Bowl futures
  comparison; not needed for win-total scoring).
