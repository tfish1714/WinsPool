# Consensus Benchmark — Design Spec

**Date:** 2026-08-11
**Status:** Approved — ready for implementation planning
**Follow-on:** `2026-08-11-walk-forward-validation-design.md` (Spec B, stub)

---

## Problem

There is no season in which the WinsPool model's projections and the analyst
consensus both exist, so the question "does the model agree with what the sports
sites are saying, and where does it differ?" cannot be answered at all.

The root cause is that `preseason_predictions` carries two different meanings:

| Seasons | `projected_wins` means | `sources` field holds |
|---|---|---|
| 2017–2025 | cross-analyst consensus average | per-analyst dict, e.g. `{'Clay': 7.5, 'SI': 6.0, 'O/U': 8.5, ...}` |
| 2026 | ML ensemble output | `{'model': 'nn_xgb_lr_ensemble'}` |

The ML model displaced the consensus pipeline rather than sitting alongside it.

Three secondary problems surfaced while investigating:

1. **Stale roster inputs.** `compute_preseason_player_profiles()` keys off the
   latest depth-chart snapshot per player — the mechanism that places traded
   players on their new team. `rawdata/depth_charts/depth_charts_2026.csv` spans
   `2026-03-22` → `2026-06-03`, an offseason placeholder from before training
   camp. Every trade and depth-chart move since June 3 is invisible to the model.
   `rawdata/snap_counts/snap_counts_2025.csv` is missing entirely.
2. **Uncalibrated predictions in production.** Elo constants were recalibrated on
   2026-06-08 (`HFA` 48→41.5, `TRAVEL`→0, `ELO_TO_SPREAD` 25→26.2,
   `SPREAD_TO_PROB_SCALE` 7.5→6.94) but `game_predictions_2026.json` dates from
   2026-06-06. Every 2026 prediction currently served was generated with the old
   constants.
3. **A dead admin button that reports success.** `POST /api/admin/scrape_predictions`
   → `aggregate_predictions_pipeline()` → `fetch_espn_fpi()` requests
   `https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/fpi`, which
   returns **HTTP 404** (verified live 2026-08-11). The exception is swallowed,
   the pipeline returns early, and the route still responds *"Vegas Odds
   successfully scraped and injected into Firestore!"*. Even when the endpoint
   worked it wrote a doc with no top-level `season` field, which
   `get_preseason_predictions()` filters out — so the write was never readable.

---

## Goals

1. Separate analyst consensus from model output into distinct collections.
2. Migrate the 2017–2025 consensus already stored in `sources` — no re-entry.
3. Seed 2026 consensus from a hand-maintained CSV.
4. Give the admin an at-a-glance model-vs-consensus comparison for a season.
5. Score every stored projection against actual wins for completed seasons,
   establishing the analyst accuracy baseline the model must beat.
6. Deprecate the overloaded `preseason_predictions` schema: repoint consumers,
   then remove the historical consensus rows and the `sources` union field.
7. Make the full preseason refresh a single repeatable, diff-reporting command,
   with a data-freshness preflight.
8. Remove the dead scraper rather than leave a button that lies.

## Non-Goals

- HTML scraping or paid odds APIs. Consensus is entered by hand.
- Any player-facing display.
- Feeding consensus back into the model as a feature or prior. This is
  deliberate: the May 2026 de-Vegas pass removed `spread_line` precisely because
  the model was restating the market. Consensus is a benchmark, never an input.
- Power rankings and Super Bowl futures. The available data is all in projected
  wins, and the Monte Carlo simulation covers the regular season only — there is
  no playoff bracket, so no model championship probability exists to compare a
  futures price against.
- Walk-forward validation and historical **model** projections — Spec B. Spec A
  scores consensus against actuals, which needs no retraining; scoring the model
  against actuals requires out-of-sample model projections that do not yet exist.

---

## Data Model

New Firestore collection **`consensus_projections`**, one document per
season+team, doc id `{season}_{team}` — matching the existing
`preseason_predictions` convention so `refresh_local_pkls.py` can mirror it with
the established `(collection, "season")` pattern.

```json
{
  "season": 2026,
  "team": "BUF",
  "as_of": "2026-08-11",
  "sources": {
    "br": 12, "fpi": 10.6, "si": 12, "nfl_bhanpuri": 15, "nfl_rank": 12,
    "athletic": 11.2, "pff": 11.6, "usa_today": 13, "vegas_ou": 11.5, "clay": 11.9
  },
  "n_sources": 10,
  "consensus_mean": 12.08,
  "consensus_median": 11.95,
  "consensus_min": 10.6,
  "consensus_max": 15.0,
  "consensus_std": 1.14
}
```

(Values above are the real 2025 Buffalo row, shown as a worked example of the
derived statistics. `consensus_std` is the population standard deviation.)

Derived statistics are computed once at write time — mirroring how
`preseason_predictions` stores `floor`/`ceiling`/`p25`/`p75` — so the read path
is a plain load with no recomputation.

`sources` is an open map. Source sets differ by season (2025 has a single `NFL`
column; 2026 has separate `nfl_bhanpuri` and `nfl_rank`), and adding or dropping
an analyst must never require a schema migration.

**Local mirror:** `.local_db/consensus_projections.pkl`, a DataFrame with one row
per season+team — the same shape `refresh_local_pkls.py` already produces for
`preseason_predictions`.

### Source registry

`data/consensus_sources.json` maps canonical source keys to display metadata.
Stable across seasons; the only place a source is named.

```json
{
  "sources": {
    "br":           { "name": "Bleacher Report",    "type": "analyst" },
    "fpi":          { "name": "ESPN FPI",           "type": "model"   },
    "si":           { "name": "Sports Illustrated", "type": "analyst" },
    "nfl_bhanpuri": { "name": "NFL.com (Bhanpuri)", "type": "analyst" },
    "nfl_rank":     { "name": "NFL.com (Adam Rank)","type": "analyst" },
    "athletic":     { "name": "The Athletic",       "type": "analyst" },
    "pff":          { "name": "PFF",                "type": "analyst" },
    "usa_today":    { "name": "USA Today",          "type": "analyst" },
    "vegas_ou":     { "name": "Vegas O/U",          "type": "market"  },
    "clay":         { "name": "Mike Clay",          "type": "analyst" },
    "cbs":          { "name": "CBS Sports",         "type": "analyst" },
    "espn":         { "name": "ESPN",               "type": "analyst" },
    "nfl":          { "name": "NFL.com",            "type": "analyst" }
  }
}
```

**Dependency constraint.** JSON rather than YAML because PyYAML is neither
installed nor listed in `requirements.txt`, and a 13-entry config file does not
justify a new dependency.

The same constraint governs `consensus_service.py`: it is imported by
`admin_routes.py`, so it may use only **pandas and numpy**. `requirements.txt`
declares neither scipy, TensorFlow, scikit-learn nor XGBoost — the ML services
import those behind `TF_AVAILABLE` / `SKLEARN_AVAILABLE` guards and degrade
gracefully in the deployed app. Spearman correlation is therefore computed as
Pearson over `pandas.Series.rank()` values rather than via `scipy.stats`, and the
freshness preflight uses `urllib.request`, matching `sync_nflverse_data.py`,
rather than `httpx`.

`cbs`, `espn` and `nfl` exist only in the migrated 2017–2025 data.

### Seed file

`data/consensus_2026.csv` — spreadsheet-shaped for direct paste from Excel:

```csv
team,br,fpi,si,nfl_bhanpuri,nfl_rank,athletic,pff,usa_today,vegas_ou,clay
BUF,12,10.6,12,15,12,11.2,11.6,13,11.5,11.9
BAL,13,10.4,12,11,10,11,11.2,14,11.5,12.3
...
```

The rows above are 2025 values shown for format only — the 2026 file must be
filled with 2026 numbers.

Blank cells are allowed and mean "this source did not publish a number for this
team"; they are excluded from the derived statistics rather than treated as zero.

---

## Components

| File | Change |
|---|---|
| `data/consensus_sources.json` | **new** — canonical source registry |
| `data/consensus_2026.csv` | **new** — 32 rows, hand-maintained |
| `scripts/seed_consensus.py` | **new** — CSV → validate → derive → write |
| `scripts/migrate_consensus.py` | **new** — one-shot 2017–2025 migration |
| `services/consensus_service.py` | **new** — `build_comparison(season)` |
| `services/data_service.py` | add `get_consensus_projections(season)` and `get_season_projection(season)` resolver |
| `services/draft_service.py` | repoint line 136 to `get_season_projection()` |
| `services/recap_service.py` | repoint line 128 to `get_season_projection()` |
| `scripts/predict_season.py` | write `model_version` string; stop writing the `sources` dict |
| `scripts/deprecate_preseason_consensus.py` | **new** — staged step 4: delete migrated 2017–2025 rows |
| `scripts/refresh_local_pkls.py` | register `("consensus_projections", "season")` |
| `routes/admin_routes.py` | add `GET /api/admin/consensus/{season}`; delete `scrape_predictions` |
| `templates/admin.html` | new Consensus tab |
| `static/js/admin_main.js` | tab render; delete `scrapePredictions()` |
| `static/js/api.js` | delete `scrapePredictions()` |
| `static/style.css` | comparison table styling; bump `?v=` on the `<link>` in `base.html` |
| `services/nn_projection_engine.py` | replace hardcoded Elo constants in `_vectorized_elo_update` with the calibrated ones |
| `scripts/refresh_preseason.py` | **new** — orchestrator |
| `services/aggregate_scraper.py` | **delete** |
| `tests/test_aggregate_scraper.py` | **delete** |
| `tests/test_consensus_service.py` | **new** |
| `tests/test_seed_consensus.py` | **new** |

### `scripts/seed_consensus.py`

```
python scripts/seed_consensus.py --season 2026 [--firestore] [--dry-run]
```

Reads `data/consensus_{season}.csv`, normalizes team abbreviations through
`services.utils.normalize_team_abbr`, validates, computes derived statistics, and
writes. Without `--firestore` it writes only the local pkl, so the file can be
checked before it reaches production.

Team abbreviations are normalized on the way in because issue #101 documents
three diverging normalization mappings in the repo; the stored keys must match
what `preseason_predictions` uses or the join silently drops teams.

### `scripts/migrate_consensus.py`

```
python scripts/migrate_consensus.py [--seasons 2017 2025] [--firestore] [--dry-run]
```

One-shot. For each season, reads `preseason_predictions`, keeps only `sources`
entries whose **value is numeric**, maps stored source names to canonical keys
(`'O/U'`→`vegas_ou`, `'BR'`→`br`, `'SI'`→`si`, `'FPI'`→`fpi`, `'PFF'`→`pff`,
`'Clay'`→`clay`, `'NFL'`→`nfl`, `'CBS'`→`cbs`, `'ESPN'`→`espn`), computes derived
statistics, and writes to `consensus_projections`. A row with zero numeric source
entries is not consensus and is skipped. Unrecognized source *names* abort the
migration rather than being dropped silently.

The numeric-value rule is what excludes the 2026 model rows, whose `sources` is
`{'model': 'nn_xgb_lr_ensemble'}` — a string, not a projection. Filtering on the
literal key `model` would work today but encodes a magic string and would miss
any future non-consensus marker; testing the value type states the actual
intent, which is "a source is an analyst who published a number".

The stored key set was enumerated across all of 2017–2025 and is exactly these
nine, so the mapping above is complete:

| Stored key | Canonical | Seasons present |
|---|---|---|
| `BR` | `br` | 2017–2025 (9) |
| `CBS` | `cbs` | 2017–2025 (9) |
| `SI` | `si` | 2017–2025 (9) |
| `FPI` | `fpi` | 2017–2025 (9) |
| `ESPN` | `espn` | 2017–2025 (8) |
| `NFL` | `nfl` | 2018–2025 (6) |
| `PFF` | `pff` | 2018–2025 (6) |
| `O/U` | `vegas_ou` | 2021–2025 (5) |
| `Clay` | `clay` | 2024–2025 (2) |

Coverage is uneven: `Clay` appears in two seasons and `O/U` in five, so
`n_sources` varies by season. `consensus_std` is therefore **not comparable
across seasons** — a season averaging four sources will show tighter spread than
one averaging ten, independent of how much analysts actually disagreed. Within a
season it is comparable across teams, which is all `outlier_z` requires. Spec B
must account for this when scoring historical seasons against each other.

### Deprecating the overloaded schema

The migration **moves**, in staged steps, and the old dual-meaning schema is
retired rather than left in place. End state:

| Collection | Sole meaning |
|---|---|
| `consensus_projections` | analyst consensus, any season |
| `preseason_predictions` | model output, any season |

`preseason_predictions` also loses its `sources` union field. Model rows carry a
plain `model_version` string (`"nn_v14+xgb_v8+lr_v6"`) instead of
`{'model': 'nn_xgb_lr_ensemble'}`, so the field has one type and one meaning.

**Consumers.** Two services read the collection for its "whatever projection we
have for this season" meaning:

| Site | Current call |
|---|---|
| `services/draft_service.py:136` | `get_preseason_predictions(int(season))` |
| `services/recap_service.py:128` | `get_preseason_predictions(year)` |

Both are repointed at a new resolver in `data_service`:

```python
get_season_projection(season) -> {team: {wins, source_type, detail}}
#   source_type: "model" | "consensus" | None
```

It returns model projections when they exist for the season, otherwise consensus,
and labels which. That keeps the two stores single-meaning while preserving the
historical numbers those views display — 2017–2025 resolve to consensus today,
and flip to model output automatically once Spec B backfills them, with the
`source_type` label making the switch visible rather than silent.

**Staging.** Ordered so nothing breaks mid-flight:

1. Write `consensus_projections` (migration, additive — nothing reads it yet).
2. Add `get_season_projection()`; repoint `draft_service` and `recap_service`.
3. Verify historical draft rooms and recaps render unchanged.
4. Only then delete the 2017–2025 rows from `preseason_predictions` and drop the
   `sources` field from the write path in `predict_season.py`.

Step 4 is the irreversible one and gets its own verification gate. Until it runs,
the two collections overlap harmlessly.

### `services/consensus_service.py`

`build_comparison(season) -> dict` joins `preseason_predictions` (model rows only
— those whose `sources` contains `model`) to `consensus_projections`.

Per team:

| Field | Definition |
|---|---|
| `model_wins` | `mean_wins` — the unrounded value, not `projected_wins` |
| `consensus_mean` / `median` / `min` / `max` / `std` | from the stored doc |
| `delta` | `model_wins − consensus_median` |
| `in_range` | `consensus_min ≤ model_wins ≤ consensus_max` |
| `outlier_z` | `(model_wins − consensus_mean) / consensus_std` |
| `model_rank` / `consensus_rank` / `rank_delta` | dense ranks on wins, descending |

`outlier_z` is the primary sort key. It asks whether the model disagrees with the
analysts by more than they disagree with each other, which distinguishes a real
divergence from ordinary spread. Where the analysts are split — 2025 Houston ran
BR 5, SI 10, USA Today 10, O/U 9.5 — a large `delta` means little; where they
cluster, the same `delta` is a genuine outlier.

Summary block: `mae` (model vs `consensus_median`), `bias` (mean signed delta),
`spearman` (rank correlation), `n_outside_range`, `n_delta_over_2`.

### Accuracy against actual results

For any **completed** season, `build_comparison` additionally scores every stored
projection against actual wins from `nfl_standings`. This needs no model backfill
— consensus and actuals both already exist for 2017–2025 — so the analyst
accuracy baseline lands in Spec A. Spec B adds only the model's side of it.

Per team, when actuals exist: `actual_wins`, plus `consensus_error` and
`model_error` (signed). Per season, a per-source scoreboard of MAE and Pearson r,
including the consensus average as its own row.

Measured baseline over 2017–2025 (mean absolute error in wins):

| Source | MAE | n |
|---|---|---|
| CBS | 2.18 | 285 |
| **consensus average** | **2.18** | 285 |
| Vegas O/U | 2.24 | 160 |
| FPI | 2.24 | 285 |
| NFL.com | 2.30 | 191 |
| Bleacher Report | 2.37 | 285 |
| ESPN | 2.38 | 253 |
| PFF | 2.49 | 191 |
| SI | 2.49 | 285 |
| Clay | 2.85 | 64 |

**MAE ≈ 2.18 is the bar.** Note the `n` column: sources with few observations
move around a lot between seasons — on 2024 alone Vegas O/U led at MAE 1.84,
which does not survive the full sample. Per-season figures must be read with the
sample size beside them, and the tab displays `n` next to every MAE for that
reason.

For 2026 there are no actuals yet, so that season shows **agreement only** —
model vs consensus, no accuracy columns. The tab must not imply otherwise.

### Elo constants in the season simulation

The June 2026 calibration updated `prediction_service.py` but never reached the
Monte Carlo simulation. `nn_projection_engine._vectorized_elo_update` hardcodes
its own values:

| Constant | Calibrated (`prediction_service.py`) | Hardcoded (`nn_projection_engine.py`) |
|---|---|---|
| Home advantage | `ELO_HOME_ADVANTAGE = 41.5` | `48.0` |
| K factor | `ELO_K = 20.6` | `20.0` |

Home advantage is 15% too high, applied to every simulated game across every
trial — 272 games × N trials per projection run. K is within noise of the
calibrated value, but should be sourced from the same constant rather than left
as a second literal.

Both are replaced with imports of the calibrated constants. This is in scope for
Spec A because it changes 2026 projections and must land **before** the refresh
run, otherwise the before/after diff mixes a constants fix in with the roster
refresh and neither effect is attributable.

Sequencing — two runs, two attributable diffs:

```bash
# 1. constants-only delta (no new data)
python scripts/refresh_preseason.py --season 2026 --skip-sync

# 2. roster delta (fresh depth charts on top)
python scripts/refresh_preseason.py --season 2026
```

This is the primary motivation for the `--skip-sync` flag.

### Admin surface

`GET /api/admin/consensus/{season}`, behind `require_admin`, returns
`build_comparison(season)`. Rendered as a new Consensus tab in `admin.html`
alongside Elo Ratings and ML Accuracy: one row per team, sorted by
`|outlier_z|` descending, with the summary block above the table and a source
legend from `consensus_sources.json`.

### `scripts/refresh_preseason.py`

```
python scripts/refresh_preseason.py --season 2026 [--skip-sync] [--dry-run]
```

Follows the declarative `STEPS` pattern already in `scripts/run_cron.py`
(subprocess dispatch, per-step `required` flag, logging to `logs/`):

| # | Step | Required |
|---|---|---|
| 1 | `sync_nflverse_data.py` — refresh rosters, depth charts, injuries, snap counts | no |
| 2 | `compute_elo.py` | yes |
| 3 | `predict_season.py --season N` | yes |
| 4 | `backfill_schedule_predictions.py --seasons N N --firestore --force` | yes |
| 5 | `cache_builder.py --year N --force` | no |
| 6 | `refresh_local_pkls.py` | no |

`--force` on step 4 is what finally propagates the June 8 Elo recalibration into
stored predictions.

Before step 3 the command snapshots the season's existing `preseason_predictions`;
after step 3 it prints a per-team before/after table of `mean_wins` sorted by
absolute movement. **That diff is how trades become visible** — it is the primary
output of the command, not a log detail.

Intended cadence: once now, again immediately before the draft, and again after
the 53-man cutdown in early September, since depth-chart truth firms up across
that window.

---

## Error Handling

| Condition | Behavior |
|---|---|
| Seed CSV missing a canonical team, or containing an unknown one | abort, non-zero exit, name the team |
| Win value outside `0–17`, or non-numeric | abort, name team and source |
| Team row with zero populated sources | abort |
| Unknown source key in CSV or migration | abort — never drop silently |
| `consensus_std == 0` (single source) | `outlier_z` is `null`, not a division by zero |
| Team present in one collection only | row renders with `—`, excluded from summary statistics |
| No consensus seeded for the season | API returns `{available: false, ...}`; tab shows an empty state naming the expected CSV path |
| Required refresh step fails | chain aborts, remaining steps skipped, non-zero exit |

---

## Testing

`tests/test_consensus_service.py`
- join produces one row per team; deltas, ranks and `rank_delta` correct
- `in_range` boundary cases (model exactly at `min` and at `max`)
- `outlier_z` correct, and `null` when `consensus_std == 0`
- summary `mae`, `bias`, `spearman` against hand-computed fixtures
- team in one collection only → rendered, excluded from summary
- season with no consensus → `available: false`

- accuracy columns present for a completed season, absent for 2026
- per-source MAE and Pearson r against a fixture with known values
- each MAE is reported with its `n`

`tests/test_seed_consensus.py`
- rejects missing team, unknown team, unknown source key, out-of-range value,
  fully-blank row
- blank cells excluded from derived statistics rather than counted as zero
- team abbreviations normalized before write
- migration maps a known 2025 row (ARI: `BR` 10, `FPI` 8.3, `SI` 6, `O/U` 8.5,
  `Clay` 7.5) to canonical keys with correct derived statistics
- migration keeps only numeric-valued source entries, so a row whose `sources` is
  `{'model': 'nn_xgb_lr_ensemble'}` yields zero sources and is skipped
- a row mixing numeric and non-numeric entries keeps the numeric ones

`tests/test_data_service.py` (extend) — the resolver
- `get_season_projection()` returns `source_type: "model"` when model rows exist
- falls back to `source_type: "consensus"` when they do not
- returns `source_type: None` for a season with neither
- `draft_service` and `recap_service` render unchanged for a historical season
  before and after the repoint (the regression that step 4 risks)

`tests/test_refresh_preseason.py`
- freshness preflight against **mocked** GitHub API responses: asset newer than
  local, asset absent (the real 2026 snap-counts case), API unreachable
- stale local depth-chart `dt` triggers the warning
- a failing required step aborts the chain; a failing optional step does not
- no test hits the live network

`tests/test_admin_routes.py` (extend)
- `GET /api/admin/consensus/{season}` returns 401 without auth
- populated and empty-state response shapes

`tests/test_nn_projection_engine.py` (extend)
- `_vectorized_elo_update` uses `ELO_HOME_ADVANTAGE` and `ELO_K`, with no
  remaining numeric literals for either

Removal: delete `tests/test_aggregate_scraper.py` with the service.

The 2025 board is a natural fixture — it is real data with known derived values.

---

## Verification

After implementation, seeded 2026 consensus and a completed refresh run should
satisfy:

1. `consensus_projections` holds 32 teams for 2026 and for every migrated season
   2017–2025.
2. `preseason_predictions` for 2026 contains model rows only — the migration did
   not write to it.
3. The Consensus tab renders 32 rows with a summary block for 2026, and accuracy
   columns for a completed season.
4. Per-source MAE reproduces the measured baseline: consensus average **2.18**
   over 285 team-seasons. A material deviation means the migration lost or
   altered data — treat it as a failed migration, not a new finding.
5. Historical draft rooms and recaps render the same projections before and after
   the resolver repoint.
6. The constants-only refresh (`--skip-sync`) produces a non-empty diff,
   attributable solely to the Elo home-advantage fix.
7. The full refresh produces a second non-empty diff, attributable to the roster
   and depth-chart sync.
8. `pytest tests/` passes.

Item 4 is the load-bearing check on the migration — it re-derives a number
computed from the pre-migration data, so it fails loudly if anything was dropped.

A large `outlier_z` on a specific team is a finding to investigate, not a
failure — the tab exists precisely to surface those.

---

## Data Freshness Preflight

The refresh is only meaningful if nflverse has published post-camp 2026 roster
data. That is directly checkable, so it becomes a preflight step rather than an
accepted risk.

`scripts/refresh_preseason.py --check-freshness` (also run automatically as step
0 of a full refresh) queries the nflverse-data releases API:

```
GET https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}
```

and reports, per required asset: remote `updated_at`, local file mtime, and — for
depth charts specifically — the maximum `dt` value *inside* the local CSV. That
last one matters most: the profile builder keys off the latest per-player
snapshot, so a file downloaded recently can still contain only stale snapshots.

Outcomes:

| Condition | Behavior |
|---|---|
| Remote asset newer than local file | proceed, report how much newer |
| Remote asset absent for the season | warn, name the asset, continue |
| Local depth-chart max `dt` older than 30 days after sync | warn loudly — profile features will barely move |
| Releases API unreachable | warn and continue; never block the refresh on GitHub availability |

Measured 2026-08-11, establishing that fresh data does exist:

| Asset | Remote `updated_at` | Local state |
|---|---|---|
| `depth_charts_2026.csv` | 2026-08-11, 40.4 MB | June 3, max `dt` 2026-06-03 |
| `roster_2026.csv` | 2026-08-11 | June 3 |
| `roster_weekly_2026.csv` | 2026-08-11 | absent locally |
| `snap_counts` 2026 | no asset published | absent — expected, no games played |
| `injuries` 2026 | no asset published | absent — expected this early |

Depth charts are roughly ten weeks newer than the local copy, so the sync should
produce a substantial projection diff. Missing 2026 snap counts and injuries are
normal preseason conditions, not failures: `compute_preseason_player_profiles()`
already treats both as optional, falling back to prior-season files.
