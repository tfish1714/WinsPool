# Walk-Forward Validation — Design Spec

**Date:** 2026-08-11 (brainstormed and finalized 2026-08-13)
**Status:** Approved — ready for planning
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
coexist on a genuinely out-of-sample basis, so the question "has the model ever
been better than the consensus?" is unanswerable. The 2021-2025 `preseason_predictions`
rows currently in Firestore were backfilled during the consensus-benchmark work
using the current production model (`min_season=2006, max_season=2025`) — which
was trained on all of those seasons, so those numbers are in-sample and cannot
answer the question either. Spec A creates the consensus side; this spec creates
a genuinely out-of-sample model side and scores both against actual results.

Note that `game_predictions_{2018..2026}.json` do **not** fill this gap. They are
game-level predictions built from in-season features (`point_diff_advantage`,
current `elo_diff`, rest, Vegas line), so they are not preseason forecasts; and
for seasons before 2025 they are in-sample, since the models trained on those
seasons.

---

## Goal and Scope

This is a **diagnostic measurement tool, not a production change.** It answers
one question: *does the current model architecture, trained honestly on only
the data available before each season, beat the analyst consensus MAE bar
(≈2.18) on seasons it never saw?*

It does **not**:
- overwrite anything in `preseason_predictions` (the 2021-2025 backfill from
  the consensus-benchmark work is left as-is)
- change `_split_data()`, the production training scripts, or any registry's
  `latest`/`best_by` pointers
- retune ensemble weights or Elo constants
- run against the live app in any way

If this harness reports the architecture losing to consensus, *that* is the
trigger for a follow-on project to actually change the model. Doing that work
before measuring would destroy the measurement, per the guardrail below.

---

## Design

### Fold structure

Five folds, one per season **2021–2025**, expanding-window:

| Fold season `S` | Train window | Predict |
|---|---|---|
| 2021 | seasons ≤ 2020 | 2021 |
| 2022 | seasons ≤ 2021 | 2022 |
| 2023 | seasons ≤ 2022 | 2023 |
| 2024 | seasons ≤ 2023 | 2024 |
| 2025 | seasons ≤ 2024 | 2025 |

2021 is the earliest safe fold: `NNProjectionEngine.initialize()` builds its
team-profile proxy table with a hardcoded `min_season=2020` floor
(`nn_projection_engine.py:60`), and every fold's profile window
(`season - 1`) needs to land at 2020 or later. Folds before 2021 would need a
code change to that floor and are out of scope here.

This directly replaces the 2021-2025 span the consensus-benchmark backfill
already covers, so the walk-forward report can be read side-by-side with the
existing (in-sample) numbers to show the difference.

### Per-fold procedure

For each fold season `S`:

1. `build_master_feature_table(min_season=2006, max_season=S-1)` — train data
   strictly prior to `S`.
2. Train `NNPredictionService`, `XGBPredictionService`, `LRPredictionService`
   in-memory via their existing `.train()` methods against that table.
3. Save each trained model and its scaler directly to `models/walkforward/`
   (`nn_{S}.keras` + `nn_{S}_scaler.pkl`, `xgb_{S}.json` + `xgb_{S}_scaler.pkl`,
   `lr_{S}.pkl` + `lr_{S}_scaler.pkl` — same file pairing each service already
   uses in production) via the plain save primitives (`model.save()`, pickle)
   — **never** `save_versioned_model()`, so `model_registry.json` /
   `xgb_registry.json` / `lr_registry.json` and their `latest`/`best_by`
   pointers are untouched.
4. Compute feature importance for this fold (see below) from the val split
   already produced inside `.train()`.
5. Construct `NNProjectionEngine(nn_svc=trained_nn, xgb_svc=trained_xgb,
   lr_svc=trained_lr)` with the fold's freshly trained services injected, and
   call `engine.simulate_season(S)` — the real Monte Carlo / Elo-update path,
   unmodified.
6. Pull actual final wins for `S` from `nfl_standings`, and the analyst
   consensus mean for `S` from `consensus_projections`.
7. Emit one row per team: `season, team, actual_wins, model_wins,
   model_abs_err, consensus_wins, consensus_abs_err`.

### Architecture / components

One new script, `scripts/walk_forward_validate.py`, plus one small additive
change to `NNProjectionEngine.__init__`:

```python
def __init__(self, nn_svc=None, xgb_svc=None, lr_svc=None):
    self.svc = nn_svc or NNPredictionService()
    if nn_svc is None:
        self.svc.load_model()
    self.xgb_svc = xgb_svc or XGBPredictionService()
    if xgb_svc is None:
        self.xgb_svc.load_model()
    self.lr_svc = lr_svc or LRPredictionService()
    if lr_svc is None:
        self.lr_svc.load_model()
    ...
```

Every existing caller (`NNProjectionEngine()`, no args) is unaffected —
default behavior is identical to today. This is the only change to
production code this spec makes.

Everything else — feature engineering, the three `.train()` methods,
`simulate_season()`, Firestore reads — is reused unmodified.

### Data flow

```
per fold S in [2021..2025]:
  rawdata (seasons ≤ S-1) → feature table → train NN/XGB/LR
      → save to models/walkforward/ (or load if cached)
      → feature importance (per model)
      → inject into NNProjectionEngine → simulate_season(S)
      → projected wins  ─┐
  nfl_standings[S]      → actual wins       ─┼→ MAE row
  consensus_projections[S] → consensus wins ─┘

all folds → concat
  → reports/walk_forward_validation.csv
  → reports/walk_forward_feature_importance.csv
  → printed summary (model MAE vs consensus MAE, overall + per season)
```

### Feature importance reporting

Computed once per fold, immediately after that fold's models are trained (or
loaded from cache) — not tied to `simulate_season()`, so it never touches the
Monte Carlo path.

- **XGB**: native `feature_importances_`.
- **LR**: `abs(coef_)`.
- **NN**: permutation importance against that fold's own held-out validation
  split (the same val slice `.train()` already carves out internally) —
  shuffle one feature column at a time, measure MAE degradation, rank by
  impact.

Output: `reports/walk_forward_feature_importance.csv`, columns `season,
model, feature, importance_rank, importance_value`. This shows whether the
same features stay important across all 5 folds (stable signal) or jump
around (noise/overfitting to a particular training window) — useful for
understanding *why* the model tracks or fails to track actual wins, without
turning this into a feature-selection project.

### Artifact retention

Fold models and scalers are kept on disk at `models/walkforward/` for
reproducibility, but never registered in `model_registry.json` /
`xgb_registry.json` / `lr_registry.json`. Add `models/walkforward/` to
`.gitignore` (the existing `models/*.keras` / `models/*.pkl` rules don't
match a subdirectory).

`reports/walk_forward_validation.csv` and
`reports/walk_forward_feature_importance.csv` are committed despite `reports/`
being gitignored by default, following the existing precedent of
`reports/nn_weekly_accuracy.csv` — force-add them, same as that file.

### Resume support

Before training a fold, check whether that fold's model files already exist
in `models/walkforward/`; if so, load them instead of retraining. A `--force` flag skips
the check and retrains everything. 5 folds × 3 models is a real time cost
(no timing measurement exists yet — this is the first run), and re-running
just the scoring/report step while iterating on the report format shouldn't
require retraining from scratch.

### Error handling

- A fold season missing consensus data (shouldn't happen for 2021-2025, but
  defensively): log a warning, still record model MAE for that season, leave
  consensus columns null rather than aborting the run.
- A fold whose feature table build or model training fails: log the error,
  skip that fold, continue with the remaining folds. One bad fold shouldn't
  lose the whole run.
- No handling needed for incomplete seasons (`nfl_standings` partial data) —
  all five fold seasons (2021-2025) are fully complete.

### Testing

- `NNProjectionEngine()` with no args still resolves the registry `"latest"`
  exactly as before — regression guard on the constructor change.
- Injected services are used instead of the registry load — mock
  `load_model`, assert it's never called when services are passed in.
- MAE/report-row computation, against a small synthetic set of
  actual/projected/consensus wins.
- Feature-importance row shape/ranking logic, against a synthetic
  importance array.
- No test spins up a real fold training run (too slow for the suite) — the
  harness itself is validated by actually running it once, not by CI.

---

## Benchmark bar

Analyst consensus accuracy, measured from the data Spec A migrates:

| Season | Consensus MAE vs actual | r |
|---|---|---|
| 2024 | 2.68 | 0.48 |
| 2025 | 2.35 | 0.51 |

Measured over the full 2017–2025 migrated data (Spec A), per source:

| Source | MAE | n |
|---|---|---|
| CBS | 2.18 | 285 |
| **consensus average** | **2.18** | 285 |
| Vegas O/U | 2.24 | 160 |
| FPI | 2.24 | 285 |
| Bleacher Report | 2.37 | 285 |
| PFF / SI | 2.49 | 191 / 285 |

**MAE ≈ 2.18 is the bar.** The model has to beat it on seasons it never trained
toward to be worth trusting over the sites.

Single seasons are not a substitute for the pooled figure: on 2024 alone Vegas
O/U led at MAE 1.84, which does not survive the full sample. Any per-season
comparison must carry its `n`. The 5-fold walk-forward run here gives
`n = 160` team-seasons (32 teams × 5 folds) — smaller than the full pooled
285, so read the walk-forward MAE with that in mind and don't over-index on
any single fold season.

One caution established while investigating: preseason projections correlate
around **0.8 with the prior season's** results and only around **0.5 with the
season they forecast**, because analysts anchor on last year's record. A high
correlation against season `S−1` is expected anchoring, not evidence of a
labeling bug or of leakage.

---

## Methodological guardrail: do not retrain first

Retraining the production model on all data through 2025 and *then* testing on
2021–2025 makes every test season in-sample and measures memorization. Each fold
must train on `≤ S−1` only — retraining happens **inside** the harness, once per
fold, not once beforehand.

This is not hypothetical. `reports/nn_weekly_accuracy.csv` records v14 scoring
62–81% weekly on 2024, a season v14 trained on. Those figures are in-sample and
should not be read as accuracy. Likewise, today's 2021-2025 `preseason_predictions`
backfill used the current production model (trained through 2025) and should
be read the same way — this spec's whole purpose is to produce the honest
version of those same five seasons.

Retraining the production model becomes justified only *after* walk-forward
reports a result — if the architecture loses to the consensus baseline, that is
the signal to change it. Doing it first destroys the measurement that would say
so.

---

## Resolved Open Questions

The stub version of this spec left eight questions open. Resolved:

1. **Season floor** — 2021, forced by `NNProjectionEngine`'s hardcoded
   `min_season=2020` profile floor. 5 folds (2021-2025), not 11.
2. **Retrain cost** — 5 folds × 3 models = 15 trains, mitigated by resume
   support (cached artifacts, `--force` to override). No timing measurement
   exists yet; this run will produce the first one.
3. **Artifact retention** — kept, at `models/walkforward/`, outside all three
   production registries.
4. **Replaces vs. supplements `_split_data`** — neither. This is a separate,
   standalone script; `_split_data()` and the production training scripts are
   untouched.
5. **`best_by` recomputation** — out of scope. This harness makes no registry
   writes at all.
6. **Overwriting historical `preseason_predictions`** — no. Diagnostic-only;
   output goes to `reports/`, not Firestore. If walk-forward numbers should
   ever replace the production display, that's a separate follow-on decision
   made after this report exists.
7. **Ensemble weight refitting** — no. The harness measures the current fixed
   45/20/35 architecture's honest out-of-sample accuracy; retuning weights
   is model-improvement work, gated behind this report's result per the
   guardrail above.
8. **Elo constant recalibration per fold** — no, held fixed at the values
   calibrated on full history (same values Spec A repoints hardcoded
   constants at). This leaks a small amount of future information into every
   fold and is a known, accepted limitation rather than a solved problem —
   revisit only if walk-forward results are borderline enough for it to
   matter.

---

## Out of Scope

- Anything in Spec A.
- Feeding consensus into the model as a feature or prior.
- Playoff bracket simulation (would be required for a Super Bowl futures
  comparison; not needed for win-total scoring).
- Feature selection experiments (trying feature subsets to see what helps) —
  this spec reports feature *importance* of the existing feature set, it does
  not search over feature sets. A natural follow-on if walk-forward shows the
  model underperforming.
- Ensemble weight refitting and per-fold Elo recalibration (see Resolved Open
  Questions #7-8).
- Retraining/replacing the production model. This spec only measures; acting
  on the result is separate follow-on work.
