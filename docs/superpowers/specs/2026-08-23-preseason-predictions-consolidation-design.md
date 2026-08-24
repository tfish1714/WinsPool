# Preseason Predictions Consolidation — Design Spec

**Date:** 2026-08-23
**Status:** Approved, ready for implementation plan.

## Origin

Investigating why the mock draft showed no change after today's model
retrain / weight recalibration / bug fixes surfaced that
`preseason_predictions` (per-team full-season win projections — the
number the mock draft, real draft, and admin `/admin/forecast` all
read) is written **only** by manually running `scripts/predict_season.py`.
No scheduled job calls it. Firestore's 2026 docs were stamped
`model_version: nn_v14+xgb_v8+lr_v6`, `generated_at: 2026-08-15` — over
a week stale, predating this entire session's work.

Further investigation found this isn't a missing-automation gap so much
as **duplicated, diverged logic**: `scripts/cache_builder.py`'s daily
`winspool-predict-daily` job already computes a version of this same
number every day (`NNProjectionEngine.get_team_projected_wins()`,
`services/nn_projection_engine.py:887-907`, explicitly docstringed
`"for Draft logic"`), but discards everything except the median before
writing it to `analytics_cache`'s `prediction_snapshot` analytic — which
nothing in the app reads (confirmed: zero references in `routes/`,
`templates/`, `static/js/`). Meanwhile the actually-read collection,
`preseason_predictions`, only gets its richer stats (mean, std_dev,
floor/ceiling, percentiles) from the separate manual script.

## Goal and scope

Make the daily automated job the single source of truth for
`preseason_predictions`: extend the wrapper cache_builder.py already
calls to preserve full stats (not just median), write those into
`preseason_predictions` with the same locking discipline every other
prediction store in this app already has, and stop relying on a human
remembering to run `predict_season.py`.

**Explicitly out of scope:**
- Any change to how `preseason_predictions` is *read*
  (`services/data_service.py`'s `get_preseason_predictions()` /
  `get_season_projection()` / `get_season_projection_legacy_shape()` /
  `get_season_projection_dual()`, and every route/service that calls
  them) — this is a write-side-only change, verified against every
  known caller (see Blast Radius below).
- Retiring `scripts/predict_season.py` — kept as a manual override tool,
  same role `backfill_schedule_predictions.py` plays alongside the
  automated `game_predictions` path. Its unconditional-overwrite
  behavior is unchanged; that's now its intentional purpose (a manual
  override should be able to override, including a locked doc).
- Deleting or repurposing the dead `prediction_snapshot` analytic —
  separate, smaller cleanup, not bundled here.
- Eliminating the redundant `simulate_season()` call this creates
  (cache_builder.py's `schedule_enriched` block already runs one
  `simulate_season()` for `game_predictions`; this adds a second,
  independent one for `preseason_predictions` inside the same
  `build_year()` call, matching `prediction_snapshot`'s existing
  pattern today). A real efficiency win, but changing that coupling is
  a separate, riskier refactor than this spec's goal.

## Blast radius (why this needs care, not why it's blocked)

`preseason_predictions` is read by, per direct grep:

| Caller | File:line | What breaks if the data is wrong |
|---|---|---|
| Admin forecast page | `routes/admin_routes.py:645` | Wrong team projections shown to admin |
| Admin consensus comparison | `routes/admin_routes.py:693` | Wrong model-vs-consensus scoring |
| Draft recap | `routes/draft_routes.py:381` | Wrong historical "Draft Value Calculus" |
| History views | `routes/history_routes.py:205` | Wrong historical player-season display |
| Mock draft setup | `routes/mock_draft_routes.py:111-112` | Wrong projections shown pre-draft |
| **Mock draft bot AI** | `services/mock_draft_service.py:130` (`bot_pick`) | **Bots rank and pick teams by this number** — wrong data means wrong bot behavior, not just wrong display |
| Mock draft results grading | `services/mock_draft_service.py:157` | `graded=False` for every roster if this returns `{}` |
| **Real draft room** | `services/draft_service.py:153,159` | Sent verbatim to the live draft room frontend (`static/js/main.js`, `ui_renderer.js`) — board display and portfolio totals |
| Weekly recap | `services/recap_service.py:128` | Wrong recap numbers |

None of these change in this spec — they all keep reading the same
functions in the same shape. The care required here is entirely on the
write side: getting the new automated write correct on the first try,
since a bad write reaches the real draft room and bot AI, not just a
report.

## Design

### 1. New engine method: full-stats team projections

Add `NNProjectionEngine.get_team_win_projections()` alongside (not
replacing) the existing `get_team_projected_wins()` — same signature
shape, same `simulate_season()` call underneath, but preserves the full
per-team stats dict instead of collapsing to median only:

```python
def get_team_win_projections(self, schedule_df: pd.DataFrame, n_sims: int = 5000) -> Dict[str, dict]:
    """Full-stats sibling of get_team_projected_wins() -- same simulate_season()
    call, but preserves mean/std_dev/percentiles instead of collapsing to
    median only. Used to populate preseason_predictions (services/db_service.py's
    set_preseason_predictions()) from the daily automated job instead of the
    manual scripts/predict_season.py path.

    Returns {team: {projected_wins, mean_wins, std_dev, floor, p25, p75, ceiling}},
    field names matching scripts/predict_season.py's existing mapping exactly
    (projected_wins=median, floor=p5, ceiling=p95).
    """
    if schedule_df.empty:
        return {}
    result = self.simulate_season(schedule_df, n_sims=n_sims)
    out = {}
    for team, stats in result.get("team_stats", {}).items():
        out[team] = {
            "projected_wins": round(float(stats["median_wins"]), 1),
            "mean_wins":       round(float(stats["mean_wins"]), 1),
            "std_dev":         round(float(stats["std_dev"]), 2),
            "floor":           round(float(stats["p5"]), 1),
            "p25":             round(float(stats["p25"]), 1),
            "p75":             round(float(stats["p75"]), 1),
            "ceiling":         round(float(stats["p95"]), 1),
        }
    return out
```

`get_team_projected_wins()` itself is untouched — still used wherever
it's used today (the `prediction_snapshot` block keeps calling it;
that's out of scope to change per the Goal above).

### 2. New writer: `services/db_service.py::set_preseason_predictions()`

Mirrors `set_consensus_projections()`'s existing pattern (routes writes
through `db_service.py` rather than a script owning its own raw
Firestore client, unlike `predict_season.py`'s current approach, which
is left as-is since it's now explicitly the manual-override path).

```python
def set_preseason_predictions(season: int, projections: Dict[str, dict],
                               model_version: str, locked: bool, force: bool = False) -> int:
    """Write preseason_predictions docs for a season, respecting per-team locks.

    A team's existing doc is skipped (not overwritten) when it's already
    locked=True and force=False -- this is what preserves "what we predicted
    before a completed season started" once that season is over, the same
    protection game_predictions' locked flag and the analytics_cache
    is_cache_final() gate already give every other prediction store in this
    app. locked=True is stamped on every doc this call DOES write, set to the
    `locked` param (callers pass whatever final_flag they've already computed
    for the season -- see cache_builder.py Wiring below).

    Returns the number of docs actually written (skipped-due-to-lock docs
    don't count).
    """
    db = get_db()
    if db is None:
        logger.warning("No database connection; preseason predictions not written.")
        return 0

    existing_locked = set()
    if not force:
        for doc in db.collection("preseason_predictions").where("season", "==", season).stream():
            data = doc.to_dict()
            if data.get("locked"):
                existing_locked.add(data.get("team"))

    written = 0
    batch = db.batch()
    for team, stats in projections.items():
        if team in existing_locked:
            continue
        ref = db.collection("preseason_predictions").document(f"{season}_{team}")
        batch.set(ref, {
            "season": season, "team": team,
            **stats,
            "model_version": model_version,
            "generated_at": time.time(),
            "locked": locked,
        })
        written += 1
        if written % 400 == 0:
            batch.commit()
            batch = db.batch()
    if written % 400 != 0:
        batch.commit()
    return written
```

Exact field names (`projected_wins`, `mean_wins`, `std_dev`, `floor`,
`p25`, `p75`, `ceiling`) match `predict_season.py:224-238`'s existing
mapping exactly, and match what `get_preseason_predictions()`
(`services/data_service.py:364-384`) already reads — no reader-side
change needed. The only new field is `locked`, which every existing
reader already tolerates (they read named keys off the dict; an extra
key is invisible to them).

### 3. Wiring into `cache_builder.py`

In `build_year()`'s existing `prediction_snapshot` block
(`scripts/cache_builder.py:394-430`), after the existing
`get_team_projected_wins()` call, add a call to the new full-stats
method and writer:

```python
full_projections = engine.get_team_win_projections(yr_games, n_sims=5000)
if full_projections:
    n = set_preseason_predictions(
        year, full_projections, model_version=model_version,
        locked=final_flag, force=force,
    )
    print(f"  [ok]   preseason_predictions year={year} ({n} teams written)")
```

Reuses `final_flag` (`is_past_season or (latest_week >= 18)`), already
computed once per year at `cache_builder.py:294` and already used to
gate the other 4 analytics — no new "is this season over" logic
needed, just reusing what's already there.

`model_version` needs threading into `build_year()` as a new parameter
(a string built once in `main()` from `nn_svc.loaded_version` /
`xgb_svc.loaded_version` / `lr_svc.loaded_version` — those service
objects are already loaded in `main()` at `cache_builder.py:560-562`,
just not currently passed down into `build_year()`; only the derived
`pred_lookup` dict is). Format matches `predict_season.py`'s
`_model_version_string()`: `f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"`.

Wrap this block in the same try/except pattern the other 4 analytics
already use (`except Exception as e: print(f"  [err]  ...")`) — a
failure here must not abort the rest of `build_year()` or the whole
job, matching existing graceful-degradation convention throughout this
file.

## Testing

- Unit tests for `get_team_win_projections()`: mocked `simulate_season()`
  return value → assert every field maps correctly (especially the
  `floor`=p5 / `ceiling`=p95 naming, easy to get backwards), and that an
  empty `schedule_df` returns `{}` without calling `simulate_season()`.
- Unit tests for `set_preseason_predictions()`: a team with an existing
  `locked=True` doc is skipped when `force=False`, written when
  `force=True`; a team with no existing doc or `locked=False` is always
  written; `locked` param value is stamped correctly on newly-written
  docs; return value counts only actually-written docs.
- Integration test in `tests/test_cache_builder.py` (matching this
  file's established mocking conventions): `build_year()` called with a
  mocked engine and mocked `set_preseason_predictions`, asserting it's
  called with the right `year`/`locked`/`force` values for both a past
  (should lock) and current (should not lock) season.
- No changes needed to any existing test of the read-side functions —
  confirm they still pass unmodified as a regression check.
- Manual verification: after implementation, run
  `python scripts/cache_builder.py --year 2026` locally (no `--force`,
  correct local sklearn environment) and confirm
  `.local_db/preseason_predictions_2026.pkl` (via
  `refresh_local_pkls.py` afterward) shows fresh `model_version`/
  `generated_at`, then spot-check the mock draft page shows updated
  numbers.

## Non-goals

- Not touching `predict_season.py`'s own behavior (still unconditional
  overwrite, now its intentional manual-override role).
- Not touching any reader function or any route/service that consumes
  `preseason_predictions`.
- Not deduplicating the two independent `simulate_season()` calls
  `build_year()` now makes per year (one for `game_predictions`, one
  for `preseason_predictions`) — a real efficiency opportunity, but a
  separate, riskier change than this spec's scope.
- Not deleting the dead `prediction_snapshot` analytic.
