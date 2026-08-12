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
5. Make the full preseason refresh a single repeatable, diff-reporting command.
6. Remove the dead scraper rather than leave a button that lies.

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
- Walk-forward validation and historical model projections — Spec B.

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

`data/consensus_sources.yaml` maps canonical source keys to display metadata.
Stable across seasons; the only place a source is named.

```yaml
sources:
  br:           { name: "Bleacher Report",       type: analyst }
  fpi:          { name: "ESPN FPI",              type: model }
  si:           { name: "Sports Illustrated",    type: analyst }
  nfl_bhanpuri: { name: "NFL.com (Bhanpuri)",    type: analyst }
  nfl_rank:     { name: "NFL.com (Adam Rank)",   type: analyst }
  athletic:     { name: "The Athletic",          type: analyst }
  pff:          { name: "PFF",                   type: analyst }
  usa_today:    { name: "USA Today",             type: analyst }
  vegas_ou:     { name: "Vegas O/U",             type: market }
  clay:         { name: "Mike Clay",             type: analyst }
  cbs:          { name: "CBS Sports",            type: analyst }
  espn:         { name: "ESPN",                  type: analyst }
  nfl:          { name: "NFL.com",               type: analyst }
```

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
| `data/consensus_sources.yaml` | **new** — canonical source registry |
| `data/consensus_2026.csv` | **new** — 32 rows, hand-maintained |
| `scripts/seed_consensus.py` | **new** — CSV → validate → derive → write |
| `scripts/migrate_consensus.py` | **new** — one-shot 2017–2025 migration |
| `services/consensus_service.py` | **new** — `build_comparison(season)` |
| `services/data_service.py` | add `get_consensus_projections(season)` |
| `scripts/refresh_local_pkls.py` | register `("consensus_projections", "season")` |
| `routes/admin_routes.py` | add `GET /api/admin/consensus/{season}`; delete `scrape_predictions` |
| `templates/admin.html` | new Consensus tab |
| `static/js/admin_main.js` | tab render; delete `scrapePredictions()` |
| `static/js/api.js` | delete `scrapePredictions()` |
| `static/style.css` | comparison table styling; bump `?v=` on the `<link>` in `base.html` |
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

One-shot. For each season, reads `preseason_predictions`, skips any row whose
`sources` dict contains a `model` key, maps stored source names to canonical keys
(`'O/U'`→`vegas_ou`, `'BR'`→`br`, `'SI'`→`si`, `'FPI'`→`fpi`, `'PFF'`→`pff`,
`'Clay'`→`clay`, `'NFL'`→`nfl`, `'CBS'`→`cbs`, `'ESPN'`→`espn`), computes derived
statistics, and writes to `consensus_projections`. Unrecognized source names abort
the migration rather than being dropped silently.

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

**Migration copies; it does not move.** The 2017–2025 rows stay in
`preseason_predictions`, because `draft_service` and `recap_service` read that
collection to show historical projections and would otherwise lose them.
`consensus_projections` becomes the canonical analyst record; `preseason_predictions`
keeps meaning "the projection the app displays for this season". Spec B may later
replace the historical rows with model output — that is a deliberate separate
decision, not a side effect of this migration.

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

For 2026 there are no actual results, so the comparison measures **agreement
only**, never accuracy. Scoring against outcomes is Spec B.

### Admin surface

`GET /api/admin/consensus/{season}`, behind `require_admin`, returns
`build_comparison(season)`. Rendered as a new Consensus tab in `admin.html`
alongside Elo Ratings and ML Accuracy: one row per team, sorted by
`|outlier_z|` descending, with the summary block above the table and a source
legend from `consensus_sources.yaml`.

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

`tests/test_seed_consensus.py`
- rejects missing team, unknown team, unknown source key, out-of-range value,
  fully-blank row
- blank cells excluded from derived statistics rather than counted as zero
- team abbreviations normalized before write
- migration maps a known 2025 row (ARI: `BR` 10, `FPI` 8.3, `SI` 6, `O/U` 8.5,
  `Clay` 7.5) to canonical keys with correct derived statistics
- migration skips rows whose `sources` contains `model`

`tests/test_admin_routes.py` (extend)
- `GET /api/admin/consensus/{season}` returns 401 without auth
- populated and empty-state response shapes

Removal: delete `tests/test_aggregate_scraper.py` with the service.

The 2025 board is a natural fixture — it is real data with known derived values.

---

## Verification

After implementation, seeded 2026 consensus and a completed refresh run should
satisfy:

1. `consensus_projections` holds 32 teams for 2026 and for every migrated season
   2017–2025.
2. `preseason_predictions` for 2026 still contains model rows only — the
   migration did not write to it.
3. The Consensus tab renders 32 rows with a summary block.
4. The refresh diff prints a non-empty before/after table, confirming the
   recalibrated Elo constants and refreshed depth charts moved the projections.
5. `pytest tests/` passes.

A large `outlier_z` on a specific team is a finding to investigate, not a
failure — the tab exists precisely to surface those.

---

## Open Risk

The refresh depends on nflverse having published meaningful 2026 depth charts. If
step 1 pulls data that still ends in early June, the profile features will not
move and the diff will be near-empty. That outcome is informative rather than a
bug — it means the roster signal is genuinely unavailable — but it should be
checked before concluding the model is unresponsive to trades.
