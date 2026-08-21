# Consensus Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate analyst consensus from ML model output into distinct Firestore collections, score both against actual results, and surface a model-vs-consensus comparison in the admin portal.

**Architecture:** A new `consensus_projections` collection holds analyst win projections (migrated from the `sources` dict already stored in `preseason_predictions` for 2017–2025, seeded from CSV for 2026). `preseason_predictions` is deprecated down to model output only, with a `get_season_projection()` resolver keeping historical views working. A pure-function `consensus_service` computes agreement and accuracy metrics, exposed through one admin endpoint and tab. A `refresh_preseason.py` orchestrator chains the existing scripts and prints an attributable before/after diff.

**Tech Stack:** Python 3.12, FastAPI, pandas, numpy, Firestore (firebase-admin), pytest, vanilla ES6 JS, Jinja2.

**Spec:** `docs/superpowers/specs/completed/2026-08-11-consensus-benchmark-design.md`

## Global Constraints

- **`services/consensus_service.py` may import only pandas and numpy.** It is imported by `routes/admin_routes.py`, so its dependencies land in the Cloud Run image and the request path. The Dockerfile installs `requirements.txt` only — the ML stack lives in `requirements-ml.txt` and is deliberately absent from production, which is why the prediction services guard their imports. Compute Spearman as Pearson over `pandas.Series.rank()`; its default `method='average'` matches `scipy.stats.spearmanr` tie handling exactly, so this is equivalent, not a workaround.
- **No new runtime dependencies.** Config files are JSON, not YAML (PyYAML is not installed). HTTP uses `urllib.request`, matching `scripts/sync_nflverse_data.py`. Scripts under `scripts/` are not deployed and may use the ML stack freely.
- **Firestore is the source of truth.** All writes go to Firestore first; `.local_db/*.pkl` is a read-only mirror rebuilt by `scripts/refresh_local_pkls.py`. Any new collection must be registered there.
- **Services and routes must never read `rawdata/` CSVs.** Scripts may.
- **Canonical team abbreviations** come from `services.utils.normalize_team_abbr`. Store only normalized values (`LA`, not `LAR`; `WAS`, not `WSH`).
- **Tests must not hit the live network.** Mock all HTTP.
- **Bump `?v=N`** on the `style.css` `<link>` in `templates/base.html` whenever CSS changes.
- Run tests with `pytest tests/ -q` from the repo root.

---

## File Structure

| File | Responsibility |
|---|---|
| `data/consensus_sources.json` | Canonical source key → display name + type. The only place a source is named. |
| `data/consensus_2026.csv` | Hand-maintained 2026 numbers, one row per team, one column per source. |
| `services/consensus_service.py` | Pure functions: derived statistics, agreement comparison, accuracy scoring. No I/O. |
| `services/data_service.py` | Read accessors: `get_consensus_projections()`, `get_season_projection()` resolver. |
| `services/db_service.py` | Write path: `set_consensus_projections()`. |
| `scripts/migrate_consensus.py` | One-shot 2017–2025 migration out of `preseason_predictions`. |
| `scripts/seed_consensus.py` | CSV → validate → derive → write, for a single season. |
| `scripts/deprecate_preseason_consensus.py` | Staged final step: delete migrated consensus rows. |
| `scripts/refresh_preseason.py` | Orchestrator + nflverse freshness preflight + projection diff. |
| `routes/admin_routes.py` | `GET /api/admin/consensus/{season}`. |
| `templates/admin.html`, `static/js/admin_main.js` | Consensus tab. |

Task order is dependency-driven: independent fixes first (1–2), then the data layer bottom-up (3–6), then presentation (7–8), then the deprecation sequence (9–10), then orchestration (11).

---

## Task 1: Fix hardcoded Elo constants in the season simulation

The June 2026 calibration updated `prediction_service.py` but never reached the Monte Carlo simulation, which carries its own literals. This lands first so the later refresh diff is attributable to roster data rather than a constants change.

**Files:**
- Modify: `services/nn_projection_engine.py:460-495` (`_vectorized_elo_update`)
- Test: `tests/test_nn_projection_engine.py`

**Interfaces:**
- Consumes: `ELO_K`, `ELO_HOME_ADVANTAGE` from `services.prediction_service`
- Produces: no signature change — behavioral only

- [ ] **Step 1: Write the failing test**

Create `tests/test_nn_projection_engine.py` (the file does not yet exist):

```python
"""Tests for NNProjectionEngine internals that do not require TensorFlow."""
import inspect
import re

from services.nn_projection_engine import NNProjectionEngine
from services.prediction_service import ELO_HOME_ADVANTAGE, ELO_K


def test_elo_update_uses_calibrated_home_advantage():
    """The simulation must not carry its own copy of the HFA constant."""
    src = inspect.getsource(NNProjectionEngine._vectorized_elo_update)
    assert "48.0" not in src, "hardcoded home advantage still present"
    assert "ELO_HOME_ADVANTAGE" in src


def test_elo_update_uses_calibrated_k_factor():
    src = inspect.getsource(NNProjectionEngine._vectorized_elo_update)
    assert not re.search(r"\b20\.0\s*\*", src), "hardcoded K factor still present"
    assert "ELO_K" in src


def test_calibrated_constants_have_expected_values():
    """Guards against a silent revert of the June 2026 calibration."""
    assert ELO_HOME_ADVANTAGE == 41.5
    assert ELO_K == 20.6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_nn_projection_engine.py -v`
Expected: FAIL — `test_elo_update_uses_calibrated_home_advantage` asserts "hardcoded home advantage still present"

- [ ] **Step 3: Replace the literals**

In `services/nn_projection_engine.py`, add to the imports near the top of the file:

```python
from services.prediction_service import ELO_HOME_ADVANTAGE, ELO_K
```

If that import creates a circular import at module load, import inside the method body instead (the codebase already uses local imports for this reason — see `_precompute_static_features`).

Then in `_vectorized_elo_update`, replace:

```python
        # Elo diff from winner's perspective (home advantage = 48 pts)
        winner_elo_diff = np.where(
            home_wins,
            h_elo - a_elo + 48.0,   # home won: home advantage helps them
            a_elo - h_elo - 48.0,   # away won: home advantage hurt them
        )
```

with:

```python
        # Elo diff from winner's perspective, using the calibrated home advantage
        winner_elo_diff = np.where(
            home_wins,
            h_elo - a_elo + ELO_HOME_ADVANTAGE,  # home won: advantage helps them
            a_elo - h_elo - ELO_HOME_ADVANTAGE,  # away won: advantage hurt them
        )
```

and replace:

```python
        shift = 20.0 * (1.0 - expected) * mov_mult  # K = 20
```

with:

```python
        shift = ELO_K * (1.0 - expected) * mov_mult
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nn_projection_engine.py -v`
Expected: 3 passed

Then check nothing else regressed: `pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/nn_projection_engine.py tests/test_nn_projection_engine.py
git commit -m "fix: season simulation used hardcoded Elo HFA 48.0 and K 20.0

The June 2026 calibration set ELO_HOME_ADVANTAGE=41.5 and ELO_K=20.6 in
prediction_service.py but nn_projection_engine._vectorized_elo_update kept
its own literals, so every simulated game used a home advantage 15% too high
across all Monte Carlo trials."
```

---

## Task 2: Remove the dead ESPN FPI scraper

`fetch_espn_fpi()` requests an endpoint that returns HTTP 404. The exception is swallowed, the pipeline returns early, and the admin route reports success regardless. Even when the endpoint worked, it wrote a document with no top-level `season` field, which `get_preseason_predictions()` filters out — so the write was never readable.

**Files:**
- Delete: `services/aggregate_scraper.py`, `tests/test_aggregate_scraper.py`
- Modify: `routes/admin_routes.py:265-275`, `static/js/api.js:105`, `static/js/admin_main.js:213,453-460`, `templates/admin.html`

**Interfaces:**
- Consumes: nothing
- Produces: `POST /api/admin/scrape_predictions` no longer exists

- [ ] **Step 1: Write the failing test**

Add to `tests/test_admin_routes.py`:

```python
def test_scrape_predictions_endpoint_removed(admin_token):
    """The ESPN FPI endpoint it called returns 404; the button reported false success."""
    resp = client.post(
        "/api/admin/scrape_predictions",
        headers={"Authorization": admin_token},
    )
    assert resp.status_code == 404
```

Two conventions in this file, both already established: `client` is a
module-level `TestClient(app)`, not a fixture, so do not add it as a parameter.
And the `admin_token` fixture already includes the `"Bearer "` prefix — pass it
straight through as the header value.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_admin_routes.py::test_scrape_predictions_endpoint_removed -v`
Expected: FAIL — the route still exists, returning 200 or 500 rather than 404

- [ ] **Step 3: Delete the scraper and every reference**

```bash
git rm services/aggregate_scraper.py tests/test_aggregate_scraper.py
```

In `routes/admin_routes.py`, delete the whole `@router.post("/admin/scrape_predictions")` decorator and its `scrape_predictions` function (around lines 265–275).

In `static/js/api.js`, delete the `scrapePredictions` method (around line 105).

In `static/js/admin_main.js`, delete the listener registration at line 213:

```javascript
document.getElementById('scrape-predictions-btn')?.addEventListener('click', () => this.scrapePredictions());
```

and the whole `async scrapePredictions() { ... }` method (around lines 453–460).

In `templates/admin.html`, delete the element with `id="scrape-predictions-btn"`. Find it with:

```bash
grep -n "scrape-predictions-btn" templates/admin.html
```

- [ ] **Step 4: Verify no references remain**

```bash
grep -rn "scrape_predictions\|scrapePredictions\|aggregate_scraper\|aggregate_predictions_pipeline" \
  routes/ services/ scripts/ static/ templates/ tests/
```

Expected: no output.

Run: `pytest tests/ -q`
Expected: all pass, including the new 404 test

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove dead ESPN FPI scraper and its admin button

The endpoint returns HTTP 404 (verified live 2026-08-11). fetch_espn_fpi
swallowed the failure, the pipeline returned early, and the route still
responded 'Vegas Odds successfully scraped and injected into Firestore!'.
Its write also lacked a top-level season field, so get_preseason_predictions
filtered it out and nothing ever read it."
```

---

## Task 3: Source registry and derived statistics

The pure computational core. No I/O, no Firestore — everything downstream builds on these functions.

**Files:**
- Create: `data/consensus_sources.json`, `services/consensus_service.py`
- Test: `tests/test_consensus_service.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `load_source_registry() -> dict[str, dict]` — `{key: {"name": str, "type": str}}`
  - `compute_derived(sources: dict[str, float]) -> dict` — keys `n_sources`, `consensus_mean`, `consensus_median`, `consensus_min`, `consensus_max`, `consensus_std`
  - `numeric_sources(raw: dict) -> dict[str, float]` — keeps only numeric-valued entries
  - `CANONICAL_SOURCE_KEYS: set[str]`

- [ ] **Step 1: Create the source registry**

Create `data/consensus_sources.json`:

```json
{
  "sources": {
    "br":           { "name": "Bleacher Report",     "type": "analyst" },
    "cbs":          { "name": "CBS Sports",          "type": "analyst" },
    "espn":         { "name": "ESPN",                "type": "analyst" },
    "fpi":          { "name": "ESPN FPI",            "type": "model"   },
    "si":           { "name": "Sports Illustrated",  "type": "analyst" },
    "nfl":          { "name": "NFL.com",             "type": "analyst" },
    "nfl_bhanpuri": { "name": "NFL.com (Bhanpuri)",  "type": "analyst" },
    "nfl_rank":     { "name": "NFL.com (Adam Rank)", "type": "analyst" },
    "athletic":     { "name": "The Athletic",        "type": "analyst" },
    "pff":          { "name": "PFF",                 "type": "analyst" },
    "usa_today":    { "name": "USA Today",           "type": "analyst" },
    "clay":         { "name": "Mike Clay",           "type": "analyst" },
    "vegas_ou":     { "name": "Vegas O/U",           "type": "market"  }
  }
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_consensus_service.py`:

```python
"""Tests for consensus derived statistics and comparison math."""
import math

import pytest

from services import consensus_service as cs


def test_registry_loads_expected_keys():
    reg = cs.load_source_registry()
    assert "vegas_ou" in reg
    assert reg["vegas_ou"]["name"] == "Vegas O/U"
    assert reg["vegas_ou"]["type"] == "market"
    assert reg["fpi"]["type"] == "model"


def test_canonical_keys_match_registry():
    assert cs.CANONICAL_SOURCE_KEYS == set(cs.load_source_registry())


def test_numeric_sources_drops_non_numeric():
    """A model marker row is {'model': 'nn_xgb_lr_ensemble'} -- a string, not a projection."""
    assert cs.numeric_sources({"model": "nn_xgb_lr_ensemble"}) == {}


def test_numeric_sources_keeps_mixed_row_numerics():
    raw = {"br": 10, "note": "revised", "vegas_ou": 8.5}
    assert cs.numeric_sources(raw) == {"br": 10.0, "vegas_ou": 8.5}


def test_numeric_sources_excludes_bools():
    """bool is a subclass of int; a flag is not a projection."""
    assert cs.numeric_sources({"br": 10, "verified": True}) == {"br": 10.0}


def test_compute_derived_matches_real_2025_buffalo_row():
    """Worked example from the spec: BUF 2025."""
    sources = {
        "br": 12, "fpi": 10.6, "si": 12, "nfl_bhanpuri": 15, "nfl_rank": 12,
        "athletic": 11.2, "pff": 11.6, "usa_today": 13, "vegas_ou": 11.5, "clay": 11.9,
    }
    d = cs.compute_derived(sources)
    assert d["n_sources"] == 10
    assert d["consensus_mean"] == pytest.approx(12.08, abs=0.01)
    assert d["consensus_median"] == pytest.approx(11.95, abs=0.01)
    assert d["consensus_min"] == pytest.approx(10.6)
    assert d["consensus_max"] == pytest.approx(15.0)
    assert d["consensus_std"] == pytest.approx(1.14, abs=0.01)


def test_compute_derived_single_source_has_zero_std():
    d = cs.compute_derived({"vegas_ou": 9.5})
    assert d["n_sources"] == 1
    assert d["consensus_std"] == 0.0
    assert d["consensus_mean"] == 9.5


def test_compute_derived_empty_returns_zero_sources():
    d = cs.compute_derived({})
    assert d["n_sources"] == 0
    assert d["consensus_mean"] is None
    assert d["consensus_std"] is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_consensus_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.consensus_service'`

- [ ] **Step 4: Implement**

Create `services/consensus_service.py`:

```python
"""Analyst consensus projections: derived statistics and model comparison.

Import constraint: this module is imported by routes/admin_routes.py, so it may
depend only on pandas and numpy. requirements.txt declares neither scipy nor the
ML stack. Spearman correlation is Pearson over pandas rank values.
"""
import json
import logging
import pathlib
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_REGISTRY_PATH = pathlib.Path(__file__).parent.parent / "data" / "consensus_sources.json"
_registry_cache: Optional[dict] = None


def load_source_registry() -> dict:
    """Return {source_key: {"name": str, "type": str}} from data/consensus_sources.json."""
    global _registry_cache
    if _registry_cache is None:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            _registry_cache = json.load(f)["sources"]
    return _registry_cache


CANONICAL_SOURCE_KEYS = set(load_source_registry())


def numeric_sources(raw: dict) -> dict:
    """Keep only entries whose value is a real number.

    This is what separates consensus rows from model rows: a model row's sources
    is {'model': 'nn_xgb_lr_ensemble'}, a string, so it yields {}. Testing the
    value type states the intent -- "a source is an analyst who published a
    number" -- rather than hardcoding the key 'model'. bool is excluded because
    it subclasses int but is a flag, not a projection.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, val in raw.items():
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)) and not (isinstance(val, float) and np.isnan(val)):
            out[key] = float(val)
    return out


def compute_derived(sources: dict) -> dict:
    """Summary statistics over one team's per-source projections.

    consensus_std is the population standard deviation, so a single source gives
    0.0 rather than NaN.
    """
    vals = np.array(list(sources.values()), dtype=float)
    if vals.size == 0:
        return {
            "n_sources": 0,
            "consensus_mean": None,
            "consensus_median": None,
            "consensus_min": None,
            "consensus_max": None,
            "consensus_std": None,
        }
    return {
        "n_sources": int(vals.size),
        "consensus_mean": float(np.mean(vals)),
        "consensus_median": float(np.median(vals)),
        "consensus_min": float(np.min(vals)),
        "consensus_max": float(np.max(vals)),
        "consensus_std": float(np.std(vals)),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_consensus_service.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add data/consensus_sources.json services/consensus_service.py tests/test_consensus_service.py
git commit -m "feat: add consensus source registry and derived statistics

JSON not YAML because PyYAML is neither installed nor declared. Module is
pandas/numpy only since admin_routes imports it and requirements.txt has no
scipy or ML stack."
```

---

## Task 4: Consensus storage — read and write

**Files:**
- Modify: `services/db_service.py` (append `set_consensus_projections`), `services/data_service.py` (append `get_consensus_projections`), `scripts/refresh_local_pkls.py:34-44`
- Test: `tests/test_consensus_storage.py`

**Interfaces:**
- Consumes: `compute_derived`, `numeric_sources` from Task 3
- Produces:
  - `db_service.set_consensus_projections(season: int, rows: list[dict]) -> int` — rows have keys `team`, `sources`, `as_of`; returns count written
  - `data_service.get_consensus_projections(season: int) -> dict[str, dict]` — `{team: {sources, n_sources, consensus_mean, consensus_median, consensus_min, consensus_max, consensus_std}}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_consensus_storage.py`:

```python
"""Storage round-trip for consensus_projections."""
import pandas as pd
import pytest

import services.data_service as data_service


def test_get_consensus_projections_shapes_rows(monkeypatch):
    fake = pd.DataFrame([
        {"season": 2026, "team": "BUF", "sources": {"br": 12, "vegas_ou": 11.5},
         "n_sources": 2, "consensus_mean": 11.75, "consensus_median": 11.75,
         "consensus_min": 11.5, "consensus_max": 12.0, "consensus_std": 0.25},
    ])
    monkeypatch.setattr(data_service, "get_collection_df", lambda *a, **k: fake)

    res = data_service.get_consensus_projections(2026)
    assert set(res) == {"BUF"}
    assert res["BUF"]["consensus_mean"] == 11.75
    assert res["BUF"]["sources"]["vegas_ou"] == 11.5


def test_get_consensus_projections_empty_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(data_service, "get_collection_df", lambda *a, **k: pd.DataFrame())
    assert data_service.get_consensus_projections(2026) == {}


def test_set_consensus_projections_writes_derived_stats(monkeypatch):
    written = {}

    class FakeDoc:
        def __init__(self, doc_id):
            self.doc_id = doc_id

    class FakeBatch:
        def set(self, ref, payload):
            written[ref.doc_id] = payload

        def commit(self):
            pass

    class FakeCollection:
        def document(self, doc_id):
            return FakeDoc(doc_id)

    class FakeDB:
        def collection(self, name):
            assert name == "consensus_projections"
            return FakeCollection()

        def batch(self):
            return FakeBatch()

    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_db", lambda: FakeDB())

    count = db_service.set_consensus_projections(2026, [
        {"team": "BUF", "sources": {"br": 12, "vegas_ou": 11.5}, "as_of": "2026-08-12"},
    ])

    assert count == 1
    payload = written["2026_BUF"]
    assert payload["season"] == 2026
    assert payload["team"] == "BUF"
    assert payload["n_sources"] == 2
    assert payload["consensus_mean"] == pytest.approx(11.75)
    assert payload["as_of"] == "2026-08-12"


def test_refresh_local_pkls_registers_consensus_collection():
    from scripts.refresh_local_pkls import COLLECTIONS
    assert ("consensus_projections", "season") in COLLECTIONS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_consensus_storage.py -v`
Expected: FAIL — `AttributeError: module 'services.data_service' has no attribute 'get_consensus_projections'`

- [ ] **Step 3: Implement the write path**

Append to `services/db_service.py`:

```python
def set_consensus_projections(season: int, rows: list) -> int:
    """Write analyst consensus to Firestore, doc id {season}_{team}.

    rows: [{"team": str, "sources": {source_key: wins}, "as_of": "YYYY-MM-DD"}]
    Derived statistics are computed here so the read path is a plain load,
    matching how preseason_predictions stores floor/ceiling/p25/p75.

    Returns the number of documents written.
    """
    from services.consensus_service import compute_derived

    db = get_db()
    if db is None:
        logger.warning("No database connection; consensus not written.")
        return 0

    batch = db.batch()
    count = 0
    for row in rows:
        team = row["team"]
        sources = row["sources"]
        payload = {
            "season": int(season),
            "team": team,
            "as_of": row.get("as_of"),
            "sources": sources,
            **compute_derived(sources),
        }
        ref = db.collection("consensus_projections").document(f"{season}_{team}")
        batch.set(ref, payload)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()

    if count % 400 != 0:
        batch.commit()

    logger.info("Wrote %d consensus rows for %s.", count, season)
    return count
```

- [ ] **Step 4: Implement the read path**

Append to `services/data_service.py`, directly after `get_preseason_predictions`:

```python
def get_consensus_projections(season: int) -> Dict[str, dict]:
    """Retrieve analyst consensus projections for a season, keyed by team."""
    df = get_collection_df("consensus_projections", filters=[("season", "==", season)])
    if df.empty:
        return {}

    res = {}
    for _, row in df.iterrows():
        res[row["team"]] = {
            "sources":          row.get("sources", {}),
            "n_sources":        int(row.get("n_sources", 0) or 0),
            "consensus_mean":   row.get("consensus_mean"),
            "consensus_median": row.get("consensus_median"),
            "consensus_min":    row.get("consensus_min"),
            "consensus_max":    row.get("consensus_max"),
            "consensus_std":    row.get("consensus_std"),
        }
    return res
```

- [ ] **Step 5: Register the local mirror**

In `scripts/refresh_local_pkls.py`, add to the `COLLECTIONS` list after the `preseason_predictions` entry:

```python
    ("consensus_projections", "season"),
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_consensus_storage.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add services/db_service.py services/data_service.py scripts/refresh_local_pkls.py tests/test_consensus_storage.py
git commit -m "feat: add consensus_projections read/write and local mirror registration"
```

---

## Task 5: Migrate 2017–2025 consensus

Nine seasons of analyst consensus already live in the `sources` dict of `preseason_predictions`. This copies them into the new collection. It does not delete anything — that is Task 10, after consumers are repointed.

**Files:**
- Create: `scripts/migrate_consensus.py`
- Test: `tests/test_migrate_consensus.py`

**Interfaces:**
- Consumes: `numeric_sources`, `CANONICAL_SOURCE_KEYS` (Task 3); `set_consensus_projections` (Task 4)
- Produces: `map_source_key(stored: str) -> str`, `build_migration_rows(df) -> tuple[list[dict], list[str]]` returning `(rows, errors)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migrate_consensus.py`:

```python
"""Migration of stored consensus out of preseason_predictions."""
import pandas as pd
import pytest

from scripts.migrate_consensus import build_migration_rows, map_source_key


def test_maps_every_known_stored_key():
    """These nine are the complete stored key set across 2017-2025."""
    assert map_source_key("O/U") == "vegas_ou"
    assert map_source_key("BR") == "br"
    assert map_source_key("CBS") == "cbs"
    assert map_source_key("ESPN") == "espn"
    assert map_source_key("FPI") == "fpi"
    assert map_source_key("NFL") == "nfl"
    assert map_source_key("PFF") == "pff"
    assert map_source_key("SI") == "si"
    assert map_source_key("Clay") == "clay"


def test_unknown_source_key_is_reported_not_dropped():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI", "sources": {"BR": 10, "MysteryPundit": 9}},
    ])
    rows, errors = build_migration_rows(df)
    assert rows == []
    assert any("MysteryPundit" in e for e in errors)


def test_migrates_known_2025_arizona_row():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI",
         "sources": {"BR": 10, "FPI": 8.3, "SI": 6, "O/U": 8.5, "Clay": 7.5}},
    ])
    rows, errors = build_migration_rows(df)
    assert errors == []
    assert len(rows) == 1
    assert rows[0]["team"] == "ARI"
    assert rows[0]["sources"] == {
        "br": 10.0, "fpi": 8.3, "si": 6.0, "vegas_ou": 8.5, "clay": 7.5,
    }


def test_skips_model_rows():
    df = pd.DataFrame([
        {"season": 2026, "team": "LA", "sources": {"model": "nn_xgb_lr_ensemble"}},
    ])
    rows, errors = build_migration_rows(df)
    assert rows == []
    assert errors == []


def test_normalizes_team_abbreviations():
    df = pd.DataFrame([
        {"season": 2025, "team": "LAR", "sources": {"BR": 9}},
    ])
    rows, _ = build_migration_rows(df)
    assert rows[0]["team"] == "LA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_migrate_consensus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.migrate_consensus'`

- [ ] **Step 3: Implement**

Create `scripts/migrate_consensus.py`:

```python
#!/usr/bin/env python3
"""Migrate analyst consensus out of preseason_predictions into consensus_projections.

One-shot. For 2017-2025, preseason_predictions.sources holds a per-analyst dict;
for 2026 it holds {'model': ...}. Only numeric-valued entries are consensus.

This copies. Deleting the migrated rows is a separate, gated step --
scripts/deprecate_preseason_consensus.py -- run only after consumers are
repointed at the resolver.

Usage:
    python scripts/migrate_consensus.py --dry-run
    python scripts/migrate_consensus.py --firestore
    python scripts/migrate_consensus.py --seasons 2017 2025 --firestore
"""
import argparse
import logging
import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import CANONICAL_SOURCE_KEYS, numeric_sources  # noqa: E402
from services.db_service import get_collection_df, set_consensus_projections    # noqa: E402
from services.utils import normalize_team_abbr                                  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# The complete stored key set across 2017-2025, verified against the data.
SOURCE_KEY_MAP = {
    "BR": "br",
    "CBS": "cbs",
    "ESPN": "espn",
    "FPI": "fpi",
    "NFL": "nfl",
    "O/U": "vegas_ou",
    "PFF": "pff",
    "SI": "si",
    "Clay": "clay",
}


def map_source_key(stored: str) -> str:
    """Map a stored source name to its canonical key, or '' if unrecognized."""
    if stored in SOURCE_KEY_MAP:
        return SOURCE_KEY_MAP[stored]
    lowered = str(stored).strip().lower()
    return lowered if lowered in CANONICAL_SOURCE_KEYS else ""


def build_migration_rows(df):
    """Convert preseason_predictions rows into consensus rows.

    Returns (rows, errors). A non-empty errors list means the migration must
    abort -- an unrecognized source is never dropped silently.
    """
    rows, errors = [], []
    for _, row in df.iterrows():
        nums = numeric_sources(row.get("sources", {}))
        if not nums:
            continue  # model row, or nothing numeric -- not consensus

        mapped, bad = {}, []
        for stored_key, val in nums.items():
            canon = map_source_key(stored_key)
            if not canon:
                bad.append(stored_key)
            else:
                mapped[canon] = val

        if bad:
            errors.append(
                f"season {row.get('season')} team {row.get('team')}: "
                f"unrecognized source(s) {sorted(bad)}"
            )
            continue

        rows.append({
            "season": int(row["season"]),
            "team": normalize_team_abbr(str(row["team"])),
            "sources": mapped,
            "as_of": None,  # historical: original capture date unknown
        })
    return rows, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs=2, type=int, metavar=("START", "END"),
                    default=[2017, 2025])
    ap.add_argument("--firestore", action="store_true", help="Write to Firestore")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    args = ap.parse_args()

    start, end = args.seasons
    df = get_collection_df("preseason_predictions")
    if df.empty:
        log.error("preseason_predictions is empty -- nothing to migrate.")
        sys.exit(1)

    df = df[(df["season"] >= start) & (df["season"] <= end)]
    rows, errors = build_migration_rows(df)

    if errors:
        log.error("Migration aborted -- %d unrecognized source(s):", len(errors))
        for e in errors:
            log.error("  %s", e)
        log.error("Add the source to data/consensus_sources.json and SOURCE_KEY_MAP, then rerun.")
        sys.exit(1)

    by_season = {}
    for r in rows:
        by_season.setdefault(r["season"], []).append(r)

    for season in sorted(by_season):
        season_rows = by_season[season]
        srcs = sorted({k for r in season_rows for k in r["sources"]})
        log.info("%s: %d teams, sources %s", season, len(season_rows), srcs)
        if args.dry_run or not args.firestore:
            continue
        set_consensus_projections(season, season_rows)

    log.info("Total: %d rows across %d seasons.", len(rows), len(by_season))
    if args.dry_run or not args.firestore:
        log.info("Nothing written (pass --firestore to commit).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_migrate_consensus.py -v`
Expected: 5 passed

- [ ] **Step 5: Dry-run against real data**

```bash
python scripts/migrate_consensus.py --dry-run
```

Expected: 32 teams per season for 2017–2025, no errors, and source lists matching the coverage table in the spec — `clay` only in 2024–2025, `vegas_ou` only from 2021.

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_consensus.py tests/test_migrate_consensus.py
git commit -m "feat: add consensus migration from preseason_predictions

Filters on numeric source values rather than the literal key 'model', and
aborts on any unrecognized source name rather than dropping it silently."
```

---

## Task 6: Seed 2026 consensus from CSV

**Files:**
- Create: `scripts/seed_consensus.py`, `data/consensus_2026.csv`
- Test: `tests/test_seed_consensus.py`

**Interfaces:**
- Consumes: `CANONICAL_SOURCE_KEYS` (Task 3), `set_consensus_projections` (Task 4)
- Produces: `validate_and_build(df, season) -> tuple[list[dict], list[str]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_seed_consensus.py`:

```python
"""Validation of the hand-maintained consensus CSV."""
import pandas as pd
import pytest

from scripts.seed_consensus import ALL_TEAMS, validate_and_build


def _full_frame(**overrides):
    """32 valid teams, one source column, with optional per-team overrides."""
    rows = [{"team": t, "vegas_ou": 8.5} for t in ALL_TEAMS]
    for team, patch in overrides.items():
        for r in rows:
            if r["team"] == team:
                r.update(patch)
    return pd.DataFrame(rows)


def test_valid_frame_builds_all_teams():
    rows, errors = validate_and_build(_full_frame(), 2026)
    assert errors == []
    assert len(rows) == 32


def test_missing_team_is_rejected():
    df = _full_frame().iloc[1:]
    rows, errors = validate_and_build(df, 2026)
    assert rows == []
    assert any("missing" in e.lower() for e in errors)


def test_unknown_team_is_rejected():
    df = pd.concat([_full_frame(), pd.DataFrame([{"team": "XYZ", "vegas_ou": 8.5}])])
    _, errors = validate_and_build(df, 2026)
    assert any("XYZ" in e for e in errors)


def test_unknown_source_column_is_rejected():
    df = _full_frame()
    df["mystery_pundit"] = 9.0
    _, errors = validate_and_build(df, 2026)
    assert any("mystery_pundit" in e for e in errors)


def test_out_of_range_value_is_rejected():
    _, errors = validate_and_build(_full_frame(BUF={"vegas_ou": 21.0}), 2026)
    assert any("BUF" in e and "21" in e for e in errors)


def test_row_with_no_sources_is_rejected():
    _, errors = validate_and_build(_full_frame(BUF={"vegas_ou": None}), 2026)
    assert any("BUF" in e for e in errors)


def test_blank_cells_excluded_from_derived_stats():
    """A blank means 'not published', not zero."""
    df = _full_frame()
    df["br"] = 10.0
    df.loc[df["team"] == "BUF", "br"] = None
    rows, errors = validate_and_build(df, 2026)
    assert errors == []
    buf = next(r for r in rows if r["team"] == "BUF")
    assert "br" not in buf["sources"]
    assert buf["sources"] == {"vegas_ou": 8.5}


def test_team_abbreviations_are_normalized():
    df = _full_frame()
    df.loc[df["team"] == "LA", "team"] = "LAR"
    rows, errors = validate_and_build(df, 2026)
    assert errors == []
    assert any(r["team"] == "LA" for r in rows)
    assert not any(r["team"] == "LAR" for r in rows)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_seed_consensus.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.seed_consensus'`

- [ ] **Step 3: Implement**

Create `scripts/seed_consensus.py`:

```python
#!/usr/bin/env python3
"""Seed analyst consensus for one season from a hand-maintained CSV.

The CSV is spreadsheet-shaped for direct paste out of Excel:

    team,br,fpi,si,vegas_ou,clay
    BUF,12,10.6,12,11.5,11.9

Blank cells mean "this source did not publish a number for this team" and are
excluded from the derived statistics rather than counted as zero.

Usage:
    python scripts/seed_consensus.py --season 2026 --dry-run
    python scripts/seed_consensus.py --season 2026 --firestore
"""
import argparse
import logging
import pathlib
import sys
from datetime import date

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import CANONICAL_SOURCE_KEYS      # noqa: E402
from services.db_service import set_consensus_projections         # noqa: E402
from services.utils import normalize_team_abbr                    # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = pathlib.Path(__file__).parent.parent / "data"

ALL_TEAMS = [
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LV", "LAC", "LA", "MIA", "MIN",
    "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS",
]

MIN_WINS, MAX_WINS = 0.0, 17.0


def validate_and_build(df: pd.DataFrame, season: int):
    """Validate the CSV frame and build consensus rows.

    Returns (rows, errors). A non-empty errors list means abort -- a partially
    seeded season is worse than an unseeded one.
    """
    errors = []

    if "team" not in df.columns:
        return [], ["CSV has no 'team' column"]

    source_cols = [c for c in df.columns if c != "team"]
    unknown_cols = [c for c in source_cols if c not in CANONICAL_SOURCE_KEYS]
    if unknown_cols:
        errors.append(
            f"unknown source column(s) {sorted(unknown_cols)} -- "
            f"add them to data/consensus_sources.json first"
        )

    df = df.copy()
    df["team"] = df["team"].apply(lambda t: normalize_team_abbr(str(t).strip()))

    seen = set(df["team"])
    missing = sorted(set(ALL_TEAMS) - seen)
    if missing:
        errors.append(f"missing team(s): {missing}")
    unknown_teams = sorted(seen - set(ALL_TEAMS))
    if unknown_teams:
        errors.append(f"unknown team(s): {unknown_teams}")

    if errors:
        return [], errors

    today = date.today().isoformat()
    rows = []
    for _, row in df.iterrows():
        team = row["team"]
        sources = {}
        for col in source_cols:
            val = row.get(col)
            if pd.isna(val):
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                errors.append(f"{team}/{col}: non-numeric value {val!r}")
                continue
            if not (MIN_WINS <= fval <= MAX_WINS):
                errors.append(f"{team}/{col}: {fval} outside {MIN_WINS}-{MAX_WINS} wins")
                continue
            sources[col] = fval

        if not sources:
            errors.append(f"{team}: no source values -- every column is blank")
            continue

        rows.append({"season": season, "team": team, "sources": sources, "as_of": today})

    if errors:
        return [], errors
    return rows, []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--firestore", action="store_true", help="Write to Firestore")
    ap.add_argument("--dry-run", action="store_true", help="Validate only")
    args = ap.parse_args()

    csv_path = DATA_DIR / f"consensus_{args.season}.csv"
    if not csv_path.exists():
        log.error("Not found: %s", csv_path)
        sys.exit(1)

    df = pd.read_csv(csv_path)
    rows, errors = validate_and_build(df, args.season)

    if errors:
        log.error("Validation failed -- nothing written:")
        for e in errors:
            log.error("  %s", e)
        sys.exit(1)

    srcs = sorted({k for r in rows for k in r["sources"]})
    log.info("%s: %d teams validated, sources %s", args.season, len(rows), srcs)

    if args.dry_run or not args.firestore:
        log.info("Nothing written (pass --firestore to commit).")
        return

    set_consensus_projections(args.season, rows)
    log.info("Seeded %s. Run scripts/refresh_local_pkls.py to update the local mirror.", args.season)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_seed_consensus.py -v`
Expected: 8 passed

- [ ] **Step 5: Create the 2026 CSV template**

Create `data/consensus_2026.csv` with all 32 teams and the header below. Leave the values blank for now — the user fills them from their spreadsheet. Blank cells are valid, but a team with *every* column blank fails validation, so this template will not seed until filled.

```csv
team,br,fpi,si,nfl_bhanpuri,nfl_rank,athletic,pff,usa_today,vegas_ou,clay
ARI,,,,,,,,,,
ATL,,,,,,,,,,
BAL,,,,,,,,,,
BUF,,,,,,,,,,
CAR,,,,,,,,,,
CHI,,,,,,,,,,
CIN,,,,,,,,,,
CLE,,,,,,,,,,
DAL,,,,,,,,,,
DEN,,,,,,,,,,
DET,,,,,,,,,,
GB,,,,,,,,,,
HOU,,,,,,,,,,
IND,,,,,,,,,,
JAX,,,,,,,,,,
KC,,,,,,,,,,
LV,,,,,,,,,,
LAC,,,,,,,,,,
LA,,,,,,,,,,
MIA,,,,,,,,,,
MIN,,,,,,,,,,
NE,,,,,,,,,,
NO,,,,,,,,,,
NYG,,,,,,,,,,
NYJ,,,,,,,,,,
PHI,,,,,,,,,,
PIT,,,,,,,,,,
SF,,,,,,,,,,
SEA,,,,,,,,,,
TB,,,,,,,,,,
TEN,,,,,,,,,,
WAS,,,,,,,,,,
```

- [ ] **Step 6: Commit**

```bash
git add scripts/seed_consensus.py data/consensus_2026.csv tests/test_seed_consensus.py
git commit -m "feat: add consensus CSV seeding with validation

Aborts on missing/unknown teams, unknown source columns, out-of-range values,
and fully-blank rows. Blank cells are excluded from derived stats, not zeroed."
```

---

## Task 7: Comparison and accuracy scoring

**Files:**
- Modify: `services/consensus_service.py`
- Test: `tests/test_consensus_service.py`

**Interfaces:**
- Consumes: `compute_derived` (Task 3)
- Produces:
  - `spearman(x: list[float], y: list[float]) -> float | None`
  - `build_comparison(model: dict, consensus: dict, actuals: dict | None = None) -> dict` with keys `available`, `teams`, `summary`, `source_scores`
  - Each team entry: `team`, `model_wins`, `consensus_mean/median/min/max/std`, `n_sources`, `delta`, `in_range`, `outlier_z`, `model_rank`, `consensus_rank`, `rank_delta`, and when actuals exist `actual_wins`, `model_error`, `consensus_error`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_consensus_service.py`:

```python
def _model(**kw):
    return {t: {"mean_wins": w} for t, w in kw.items()}


def _consensus(**kw):
    """kw maps team -> dict of source values."""
    return {
        t: {"sources": s, **cs.compute_derived(s)}
        for t, s in kw.items()
    }


def test_build_comparison_computes_delta_and_ranks():
    model = _model(BUF=12.0, KC=9.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 10.0},
        KC={"br": 11.0, "vegas_ou": 11.0},
    )
    out = cs.build_comparison(model, consensus)
    assert out["available"] is True
    teams = {t["team"]: t for t in out["teams"]}

    assert teams["BUF"]["delta"] == pytest.approx(2.0)
    assert teams["KC"]["delta"] == pytest.approx(-2.0)
    assert teams["BUF"]["model_rank"] == 1
    assert teams["BUF"]["consensus_rank"] == 2
    assert teams["BUF"]["rank_delta"] == 1


def test_in_range_boundaries_are_inclusive():
    consensus = _consensus(BUF={"br": 9.0, "vegas_ou": 11.0})
    at_min = cs.build_comparison(_model(BUF=9.0), consensus)["teams"][0]
    at_max = cs.build_comparison(_model(BUF=11.0), consensus)["teams"][0]
    outside = cs.build_comparison(_model(BUF=11.5), consensus)["teams"][0]
    assert at_min["in_range"] is True
    assert at_max["in_range"] is True
    assert outside["in_range"] is False


def test_outlier_z_is_none_when_std_is_zero():
    """A single source gives zero spread -- no division by zero."""
    out = cs.build_comparison(_model(BUF=12.0), _consensus(BUF={"vegas_ou": 9.5}))
    assert out["teams"][0]["outlier_z"] is None


def test_outlier_z_scales_by_analyst_disagreement():
    """Same raw gap counts for more where analysts agree than where they are split.

    Both sets have mean 9.0, so the model's raw delta is identical; only the
    spread differs. The tight set must not have zero spread or outlier_z would
    be None and the comparison would raise.
    """
    tight = _consensus(BUF={"br": 8.8, "si": 9.0, "vegas_ou": 9.2, "pff": 9.0})
    split = _consensus(BUF={"br": 5.0, "si": 10.0, "vegas_ou": 9.5, "pff": 11.5})
    z_tight = cs.build_comparison(_model(BUF=11.0), tight)["teams"][0]["outlier_z"]
    z_split = cs.build_comparison(_model(BUF=11.0), split)["teams"][0]["outlier_z"]
    assert z_tight > z_split


def test_team_missing_from_consensus_is_excluded_from_summary():
    model = _model(BUF=12.0, KC=9.0)
    consensus = _consensus(BUF={"br": 10.0, "vegas_ou": 10.0})
    out = cs.build_comparison(model, consensus)
    teams = {t["team"]: t for t in out["teams"]}
    assert teams["KC"]["consensus_mean"] is None
    assert teams["KC"]["delta"] is None
    assert out["summary"]["n_compared"] == 1


def test_no_consensus_returns_unavailable():
    out = cs.build_comparison(_model(BUF=12.0), {})
    assert out["available"] is False
    assert out["teams"] == []


def test_summary_mae_and_bias():
    model = _model(BUF=12.0, KC=8.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 10.0},
        KC={"br": 10.0, "vegas_ou": 10.0},
    )
    s = cs.build_comparison(model, consensus)["summary"]
    assert s["mae"] == pytest.approx(2.0)
    assert s["bias"] == pytest.approx(0.0)


def test_accuracy_columns_present_only_with_actuals():
    model = _model(BUF=12.0)
    consensus = _consensus(BUF={"br": 10.0, "vegas_ou": 10.0})

    without = cs.build_comparison(model, consensus)
    assert without["teams"][0].get("actual_wins") is None
    assert without["source_scores"] == []

    with_actuals = cs.build_comparison(model, consensus, actuals={"BUF": 11})
    t = with_actuals["teams"][0]
    assert t["actual_wins"] == 11
    assert t["model_error"] == pytest.approx(1.0)
    assert t["consensus_error"] == pytest.approx(-1.0)


def test_source_scores_report_mae_with_n():
    model = _model(BUF=12.0, KC=8.0)
    consensus = _consensus(
        BUF={"br": 10.0, "vegas_ou": 12.0},
        KC={"br": 6.0},
    )
    out = cs.build_comparison(model, consensus, actuals={"BUF": 12, "KC": 8})
    scores = {s["source"]: s for s in out["source_scores"]}

    # br: |10-12| = 2, |6-8| = 2 -> MAE 2.0 over n=2
    assert scores["br"]["mae"] == pytest.approx(2.0)
    assert scores["br"]["n"] == 2
    # vegas_ou only covers BUF -> MAE 0.0 over n=1
    assert scores["vegas_ou"]["n"] == 1
    assert "consensus_avg" in scores


def test_spearman_perfect_and_inverse():
    assert cs.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert cs.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert cs.spearman([1, 1, 1], [1, 2, 3]) is None  # zero variance
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_consensus_service.py -v`
Expected: FAIL — `AttributeError: module 'services.consensus_service' has no attribute 'build_comparison'`

- [ ] **Step 3: Implement**

Add `import pandas as pd` to the import block at the **top** of
`services/consensus_service.py` (beside `import numpy as np`), not mid-file.
Then append the rest:

```python
def spearman(x, y):
    """Rank correlation, computed as Pearson over ranks.

    scipy is not a declared dependency, so this uses pandas ranking plus
    numpy's corrcoef. Returns None when either series has zero variance.
    """
    if len(x) < 2 or len(y) < 2:
        return None
    rx = pd.Series(x).rank().to_numpy()
    ry = pd.Series(y).rank().to_numpy()
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def _pearson(x, y):
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(np.asarray(x, dtype=float), np.asarray(y, dtype=float))[0, 1])


def _dense_rank_desc(values: dict) -> dict:
    """Rank teams by wins, highest first. Ties share the lower rank number."""
    if not values:
        return {}
    s = pd.Series(values)
    return s.rank(ascending=False, method="min").astype(int).to_dict()


def build_comparison(model: dict, consensus: dict, actuals: dict = None) -> dict:
    """Compare model projections against analyst consensus for one season.

    model:     {team: {"mean_wins": float}}   -- from preseason_predictions
    consensus: {team: {"sources": {...}, "consensus_mean": float, ...}}
    actuals:   {team: int} for completed seasons, else None

    When actuals is None the result measures agreement only, never accuracy --
    an in-progress season has no truth to score against.
    """
    if not consensus:
        return {"available": False, "teams": [], "summary": {}, "source_scores": []}

    model_wins = {t: float(v["mean_wins"]) for t, v in model.items()
                  if v.get("mean_wins") is not None}
    cons_center = {t: v["consensus_median"] for t, v in consensus.items()
                   if v.get("consensus_median") is not None}

    model_ranks = _dense_rank_desc(model_wins)
    cons_ranks = _dense_rank_desc(cons_center)

    teams = []
    for team in sorted(set(model_wins) | set(consensus)):
        c = consensus.get(team, {})
        mw = model_wins.get(team)
        median = c.get("consensus_median")
        mean = c.get("consensus_mean")
        std = c.get("consensus_std")

        delta = (mw - median) if (mw is not None and median is not None) else None

        outlier_z = None
        if mw is not None and mean is not None and std:
            outlier_z = (mw - mean) / std

        in_range = None
        if mw is not None and c.get("consensus_min") is not None:
            in_range = bool(c["consensus_min"] <= mw <= c["consensus_max"])

        m_rank = model_ranks.get(team)
        c_rank = cons_ranks.get(team)
        entry = {
            "team": team,
            "model_wins": mw,
            "n_sources": c.get("n_sources", 0),
            "consensus_mean": mean,
            "consensus_median": median,
            "consensus_min": c.get("consensus_min"),
            "consensus_max": c.get("consensus_max"),
            "consensus_std": std,
            "delta": delta,
            "in_range": in_range,
            "outlier_z": outlier_z,
            "model_rank": m_rank,
            "consensus_rank": c_rank,
            "rank_delta": (c_rank - m_rank) if (m_rank and c_rank) else None,
            "actual_wins": None,
            "model_error": None,
            "consensus_error": None,
        }

        if actuals and team in actuals:
            actual = float(actuals[team])
            entry["actual_wins"] = actual
            if mw is not None:
                entry["model_error"] = mw - actual
            if median is not None:
                entry["consensus_error"] = median - actual

        teams.append(entry)

    teams.sort(key=lambda t: abs(t["outlier_z"]) if t["outlier_z"] is not None else -1,
               reverse=True)

    compared = [t for t in teams if t["delta"] is not None]
    deltas = [t["delta"] for t in compared]
    summary = {
        "n_compared": len(compared),
        "mae": float(np.mean(np.abs(deltas))) if deltas else None,
        "bias": float(np.mean(deltas)) if deltas else None,
        "spearman": spearman(
            [t["model_wins"] for t in compared],
            [t["consensus_median"] for t in compared],
        ),
        "n_outside_range": sum(1 for t in compared if t["in_range"] is False),
        "n_delta_over_2": sum(1 for t in compared if abs(t["delta"]) > 2),
        "has_actuals": bool(actuals),
    }

    return {
        "available": True,
        "teams": teams,
        "summary": summary,
        "source_scores": _score_sources(consensus, model_wins, actuals) if actuals else [],
    }


def _score_sources(consensus: dict, model_wins: dict, actuals: dict) -> list:
    """Per-source MAE and Pearson r against actual wins, plus the consensus average.

    Every MAE carries its n: source coverage is uneven across seasons, and a
    figure computed over 64 team-seasons is not comparable to one over 285.
    """
    per_source = {}
    for team, c in consensus.items():
        if team not in actuals:
            continue
        actual = float(actuals[team])
        for src, val in (c.get("sources") or {}).items():
            per_source.setdefault(src, {"pred": [], "act": []})
            per_source[src]["pred"].append(float(val))
            per_source[src]["act"].append(actual)

        if c.get("consensus_mean") is not None:
            per_source.setdefault("consensus_avg", {"pred": [], "act": []})
            per_source["consensus_avg"]["pred"].append(float(c["consensus_mean"]))
            per_source["consensus_avg"]["act"].append(actual)

    model_pred, model_act = [], []
    for team, mw in model_wins.items():
        if team in actuals:
            model_pred.append(mw)
            model_act.append(float(actuals[team]))
    if model_pred:
        per_source["** model **"] = {"pred": model_pred, "act": model_act}

    registry = load_source_registry()
    out = []
    for src, d in per_source.items():
        pred = np.asarray(d["pred"], dtype=float)
        act = np.asarray(d["act"], dtype=float)
        out.append({
            "source": src,
            "name": registry.get(src, {}).get("name", src),
            "mae": float(np.mean(np.abs(pred - act))),
            "r": _pearson(pred, act),
            "n": int(pred.size),
        })
    out.sort(key=lambda s: s["mae"])
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_consensus_service.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add services/consensus_service.py tests/test_consensus_service.py
git commit -m "feat: add model-vs-consensus comparison and per-source accuracy scoring

outlier_z scales the model's deviation by how much analysts disagree with each
other, so a gap on a team the analysts are split on ranks below the same gap
where they cluster. Every MAE carries its n -- source coverage is uneven."
```

---

## Task 8: Admin endpoint and Consensus tab

**Files:**
- Modify: `routes/admin_routes.py`, `templates/admin.html`, `static/js/admin_main.js`, `static/style.css`, `templates/base.html`
- Test: `tests/test_admin_routes.py`

**Interfaces:**
- Consumes: `build_comparison` (Task 7), `get_consensus_projections` (Task 4)
- Produces: `GET /api/admin/consensus/{season}` returning the `build_comparison` dict

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_admin_routes.py`:

```python
def test_consensus_endpoint_requires_auth():
    resp = client.get("/api/admin/consensus/2026")
    assert resp.status_code == 401


def test_consensus_endpoint_empty_state(admin_token, monkeypatch):
    import routes.admin_routes as ar
    monkeypatch.setattr(ar, "get_consensus_projections", lambda season: {})
    monkeypatch.setattr(ar, "get_preseason_predictions", lambda season: {})

    resp = client.get(
        "/api/admin/consensus/2026",
        headers={"Authorization": admin_token},
    )
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_consensus_endpoint_populated(admin_token, monkeypatch):
    import routes.admin_routes as ar
    from services.consensus_service import compute_derived

    srcs = {"br": 10.0, "vegas_ou": 11.0}
    monkeypatch.setattr(ar, "get_consensus_projections",
                        lambda season: {"BUF": {"sources": srcs, **compute_derived(srcs)}})
    monkeypatch.setattr(ar, "get_preseason_predictions",
                        lambda season: {"BUF": {"mean_wins": 12.0}})

    resp = client.get(
        "/api/admin/consensus/2026",
        headers={"Authorization": admin_token},
    )
    body = resp.json()
    assert body["available"] is True
    assert body["teams"][0]["team"] == "BUF"
    assert body["summary"]["n_compared"] == 1
```

As in Task 2: `client` is module-level, and `admin_token` already carries the
`"Bearer "` prefix.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_routes.py -k consensus -v`
Expected: FAIL — 404, the route does not exist

- [ ] **Step 3: Add the endpoint**

In `routes/admin_routes.py`, extend the `services.data_service` import to include the new accessors:

```python
from services.data_service import (
    load_data, get_active_season, get_preseason_predictions,
    get_consensus_projections,
)
```

Then add the route (place it beside the other `@router.get("/admin/...")` handlers):

```python
@router.get("/admin/consensus/{season}")
async def get_consensus_comparison(season: int, _: dict = Depends(require_admin)):
    """Compare model projections against analyst consensus for a season.

    Scores both against actual wins when the season is complete; otherwise
    reports agreement only.
    """
    try:
        from services.consensus_service import build_comparison

        consensus = get_consensus_projections(season)
        model = {t: v for t, v in get_preseason_predictions(season).items()}

        actuals = None
        standings_df = load_data().standings
        if not standings_df.empty and "season" in standings_df.columns:
            season_rows = standings_df[standings_df["season"].astype(int) == season]
            if not season_rows.empty:
                actuals = {
                    str(r["team"]): int(r["wins"])
                    for _, r in season_rows.iterrows()
                }

        return JSONResponse(content=sanitize_state(
            build_comparison(model, consensus, actuals)
        ))
    except Exception:
        logger.exception("Unhandled error building consensus comparison")
        return server_error()
```

`load_data()` returns `DataBundle`, a `NamedTuple` whose **first** field is
`standings` (order: standings, teams, games, players, draft_order, draft_results,
draft_order_rules). Use attribute access — the positional unpacking used
elsewhere in this file is easy to get wrong by one slot.

**Required change to the accessor.** `get_preseason_predictions()` currently
returns only `projected_wins`, `std_dev` and `sources` — it does **not** expose
`mean_wins`, which `build_comparison` needs because the unrounded value is the
meaningful one for a delta. In `services/data_service.py`, add it to the dict
built in that function:

```python
        res[row["team"]] = {
            "projected_wins": float(row.get("projected_wins", 0)),
            "mean_wins": float(row.get("mean_wins", row.get("projected_wins", 0))),
            "std_dev": float(row.get("std_dev", 0)),
            "sources": row.get("sources", {})
        }
```

The fallback to `projected_wins` covers migrated historical rows, which have no
`mean_wins` column.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_routes.py -k consensus -v`
Expected: 3 passed

- [ ] **Step 5: Add the tab markup**

In `templates/admin.html`, add a button to the tab bar after the ML Accuracy button:

```html
        <button class="admin-tab-btn tab-btn" data-tab="consensus-section">Consensus</button>
```

Then add the section, following the pattern of the sibling `elo-section` block:

```html
    <div id="consensus-section" class="tab-content card-glass hidden" style="height: auto;">
        <h2>Model vs Consensus</h2>
        <p>How the WinsPool projection compares with published analyst win totals.
           Sorted by outlier score — how far the model sits from the analysts,
           relative to how much they disagree with each other.</p>

        <div style="display: flex; gap: 1rem; align-items: flex-end; margin-top: 1rem;">
            <div style="flex: 0 0 160px;">
                <label style="display: block; font-size: 0.8rem; color: var(--ink-2); margin-bottom: 4px;">Season</label>
                <select id="consensus-season" class="admin-input"></select>
            </div>
        </div>

        <div id="consensus-summary" style="margin-top: 1.25rem;"></div>
        <div id="consensus-sources" style="margin-top: 1.25rem;"></div>
        <div id="consensus-table-wrap" style="margin-top: 1.25rem; overflow-x: auto;"></div>
    </div>
```

- [ ] **Step 6: Wire the tab in JS**

In `static/js/admin_main.js`, inside `setupTabHandlers()`, extend the existing lazy-load hook:

```javascript
                if (target === 'members-section') {
                    this.initMembersTab();
                }
                if (target === 'consensus-section') {
                    this.initConsensusTab();
                }
```

Then add these methods to the same class:

```javascript
    /* ------------------------------------------------------------------
       Consensus Tab
       ------------------------------------------------------------------ */
    async initConsensusTab() {
        const sel = document.getElementById('consensus-season');
        if (sel && !sel.options.length) {
            const now = new Date().getFullYear();
            for (let y = now; y >= 2017; y--) {
                const opt = document.createElement('option');
                opt.value = y;
                opt.textContent = y;
                sel.appendChild(opt);
            }
            sel.onchange = () => this.loadConsensus(sel.value);
        }
        await this.loadConsensus(sel ? sel.value : new Date().getFullYear());
    },

    async loadConsensus(season) {
        const wrap = document.getElementById('consensus-table-wrap');
        const summaryEl = document.getElementById('consensus-summary');
        const sourcesEl = document.getElementById('consensus-sources');
        wrap.innerHTML = '<p style="color: var(--ink-3);">Loading…</p>';
        sourcesEl.innerHTML = '';

        let data;
        try {
            data = await ApiService.getConsensus(season, this.playerId);
        } catch (e) {
            wrap.innerHTML = `<p style="color: var(--neg);">Failed to load: ${e.message}</p>`;
            return;
        }

        if (!data.available) {
            summaryEl.innerHTML = '';
            wrap.innerHTML = `<p style="color: var(--ink-3);">
                No consensus seeded for ${season}. Fill <code>data/consensus_${season}.csv</code>
                and run <code>python scripts/seed_consensus.py --season ${season} --firestore</code>.
            </p>`;
            return;
        }

        const s = data.summary;
        const f = (v, d = 2) => (v === null || v === undefined) ? '—' : Number(v).toFixed(d);
        summaryEl.innerHTML = `
            <div style="display: flex; gap: 1.5rem; flex-wrap: wrap;">
                <div><strong>${s.n_compared}</strong> teams compared</div>
                <div>MAE vs consensus <strong>${f(s.mae)}</strong></div>
                <div>Bias <strong>${f(s.bias)}</strong></div>
                <div>Rank corr <strong>${f(s.spearman)}</strong></div>
                <div><strong>${s.n_outside_range}</strong> outside analyst range</div>
            </div>`;

        if (data.source_scores.length) {
            sourcesEl.innerHTML = `
                <h3 style="margin-bottom: 0.5rem;">Accuracy vs actual wins</h3>
                <table class="admin-table"><thead><tr>
                    <th>Source</th><th>MAE</th><th>r</th><th>n</th>
                </tr></thead><tbody>` +
                data.source_scores.map(r => `<tr>
                    <td>${r.name}</td><td>${f(r.mae)}</td><td>${f(r.r)}</td><td>${r.n}</td>
                </tr>`).join('') + '</tbody></table>';
        }

        const hasActuals = s.has_actuals;
        wrap.innerHTML = `
            <table class="admin-table"><thead><tr>
                <th>Team</th><th>Model</th><th>Consensus</th><th>Range</th>
                <th>Δ</th><th>Outlier z</th><th>Rank Δ</th><th>n</th>
                ${hasActuals ? '<th>Actual</th><th>Model err</th><th>Cons err</th>' : ''}
            </tr></thead><tbody>` +
            data.teams.map(t => `<tr>
                <td><strong>${t.team}</strong></td>
                <td>${f(t.model_wins, 1)}</td>
                <td>${f(t.consensus_median, 1)}</td>
                <td>${t.consensus_min === null ? '—' : `${f(t.consensus_min, 1)}–${f(t.consensus_max, 1)}`}</td>
                <td class="${t.delta > 0 ? 'pos' : t.delta < 0 ? 'neg' : ''}">${f(t.delta, 1)}</td>
                <td>${f(t.outlier_z)}</td>
                <td>${t.rank_delta === null ? '—' : t.rank_delta}</td>
                <td>${t.n_sources}</td>
                ${hasActuals ? `<td>${t.actual_wins === null ? '—' : t.actual_wins}</td>
                                <td>${f(t.model_error, 1)}</td>
                                <td>${f(t.consensus_error, 1)}</td>` : ''}
            </tr>`).join('') + '</tbody></table>';
    },
```

Match the surrounding class syntax exactly — if the existing methods do not use trailing commas (i.e. it is an ES6 `class` rather than an object literal), drop them.

In `static/js/api.js`, add alongside the other admin calls:

```javascript
    getConsensus(season, playerId) {
        return fetchWithTimeout(`${API_BASE}/admin/consensus/${season}`, {
            headers: this.authHeaders(playerId),
        }).then(r => r.json());
    },
```

Copy the exact header-construction and response handling of the neighbouring method — issue #52 tracks the duplication, so match local convention rather than inventing a new one.

- [ ] **Step 7: Bump the CSS cache-buster**

Only if you added CSS. If `.admin-table`, `.pos` and `.neg` already exist in `static/style.css`, no change is needed — check first:

```bash
grep -n "\.admin-table\|^\.pos\|^\.neg" static/style.css | head
```

If you add rules, bump `?v=N` on the `style.css` `<link>` in `templates/base.html`.

- [ ] **Step 8: Verify in the browser**

```bash
USE_LOCAL_DATA=True uvicorn main:app --reload
```

Open `/admin`, click Consensus, choose 2025. Expect 32 rows with accuracy columns. Choose 2026 before seeding: expect the empty state naming the CSV path.

- [ ] **Step 9: Commit**

```bash
git add routes/admin_routes.py templates/admin.html static/js/admin_main.js static/js/api.js static/style.css templates/base.html tests/test_admin_routes.py
git commit -m "feat: add admin Consensus tab and comparison endpoint"
```

---

## Task 9: Season projection resolver and consumer repoint

Both services that read `preseason_predictions` for its "whatever projection exists" meaning move to a resolver, so the two collections can each carry a single meaning.

**Files:**
- Modify: `services/data_service.py`, `services/draft_service.py:136`, `services/recap_service.py:128`
- Test: `tests/test_season_projection_resolver.py`

**Interfaces:**
- Consumes: `get_consensus_projections` (Task 4), existing `get_preseason_predictions`
- Produces: `get_season_projection(season: int) -> dict[str, dict]` where each value is `{"wins": float, "source_type": "model"|"consensus", "detail": dict}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_season_projection_resolver.py`:

```python
"""The resolver that lets both collections carry a single meaning."""
import services.data_service as data_service


def test_prefers_model_when_present(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 13.0, "mean_wins": 12.6}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_median": 9.5, "sources": {}}})

    res = data_service.get_season_projection(2026)
    assert res["LA"]["source_type"] == "model"
    assert res["LA"]["wins"] == 12.6


def test_falls_back_to_consensus(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_median": 7.5, "sources": {"br": 10}}})

    res = data_service.get_season_projection(2020)
    assert res["ARI"]["source_type"] == "consensus"
    assert res["ARI"]["wins"] == 7.5


def test_empty_when_neither_exists(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})
    assert data_service.get_season_projection(1999) == {}


def test_mixed_teams_resolve_independently(monkeypatch):
    """A team with a model row uses it; one without falls back."""
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 13.0, "mean_wins": 12.6}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_median": 9.5, "sources": {}},
                                   "ARI": {"consensus_median": 7.5, "sources": {}}})

    res = data_service.get_season_projection(2026)
    assert res["LA"]["source_type"] == "model"
    assert res["ARI"]["source_type"] == "consensus"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_season_projection_resolver.py -v`
Expected: FAIL — no attribute `get_season_projection`

- [ ] **Step 3: Implement the resolver**

Append to `services/data_service.py`:

```python
def get_season_projection(season: int) -> Dict[str, dict]:
    """Resolve the best available win projection for a season, per team.

    Model output wins when it exists; analyst consensus is the fallback. This
    lets preseason_predictions mean "model output" and consensus_projections
    mean "analyst consensus" without historical views losing their numbers.

    Returns {team: {"wins": float, "source_type": "model"|"consensus", "detail": dict}}
    """
    model = get_preseason_predictions(season)
    consensus = get_consensus_projections(season)

    out = {}
    for team in set(model) | set(consensus):
        if team in model:
            row = model[team]
            wins = row.get("mean_wins")
            if wins is None:
                wins = row.get("projected_wins")
            out[team] = {
                "wins": float(wins) if wins is not None else None,
                "source_type": "model",
                "detail": row,
            }
        else:
            row = consensus[team]
            out[team] = {
                "wins": row.get("consensus_median"),
                "source_type": "consensus",
                "detail": row,
            }
    return out
```

- [ ] **Step 4: Repoint the consumers**

Read both call sites first — the shape each expects differs, and the repoint must preserve it:

```bash
sed -n '125,150p' services/draft_service.py
sed -n '120,140p' services/recap_service.py
```

In each, replace the `get_preseason_predictions(...)` call with `get_season_projection(...)` and adapt the downstream access to read `["wins"]` instead of `["projected_wins"]`. Update the import at the top of each file.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ -q`
Expected: all pass. `tests/test_draft_service.py` and `tests/test_recap_service.py` cover both call sites; if either fails, the shape adaptation in step 4 is wrong — fix it rather than changing the test.

- [ ] **Step 6: Verify historical views render unchanged**

```bash
USE_LOCAL_DATA=True uvicorn main:app --reload
```

Open a historical draft results page (2023 or 2024) and confirm projected wins still display. This is the regression Task 10 depends on.

- [ ] **Step 7: Commit**

```bash
git add services/data_service.py services/draft_service.py services/recap_service.py tests/test_season_projection_resolver.py
git commit -m "feat: add get_season_projection resolver; repoint draft and recap services

Model output when it exists, analyst consensus as fallback, with source_type
labelling which. Lets each collection carry a single meaning without historical
views losing their projections."
```

---

## Task 10: Delete the migrated rows and drop the `sources` union

The irreversible step. Run only after Task 9 is verified — the consumers must already be on the resolver.

**Files:**
- Create: `scripts/deprecate_preseason_consensus.py`
- Modify: `scripts/predict_season.py:126-137`
- Test: `tests/test_deprecate_preseason_consensus.py`

**Interfaces:**
- Consumes: `numeric_sources` (Task 3)
- Produces: `find_consensus_doc_ids(df) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_deprecate_preseason_consensus.py`:

```python
"""The gated deletion of migrated consensus rows."""
import pandas as pd

from scripts.deprecate_preseason_consensus import find_consensus_doc_ids


def test_identifies_consensus_rows_only():
    df = pd.DataFrame([
        {"season": 2025, "team": "ARI", "sources": {"BR": 10, "O/U": 8.5}},
        {"season": 2026, "team": "LA", "sources": {"model": "nn_xgb_lr_ensemble"}},
    ])
    assert find_consensus_doc_ids(df) == ["2025_ARI"]


def test_row_without_sources_is_left_alone():
    df = pd.DataFrame([{"season": 2026, "team": "LA", "model_version": "nn_v14"}])
    assert find_consensus_doc_ids(df) == []


def test_returns_empty_for_empty_frame():
    assert find_consensus_doc_ids(pd.DataFrame()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_deprecate_preseason_consensus.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

Create `scripts/deprecate_preseason_consensus.py`:

```python
#!/usr/bin/env python3
"""Delete migrated consensus rows from preseason_predictions.

Final step of the schema deprecation, and the only irreversible one. Run ONLY
after scripts/migrate_consensus.py --firestore has succeeded AND draft_service
and recap_service are confirmed working on get_season_projection().

After this, preseason_predictions means model output and nothing else.

Usage:
    python scripts/deprecate_preseason_consensus.py --dry-run
    python scripts/deprecate_preseason_consensus.py --confirm
"""
import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from services.consensus_service import numeric_sources                    # noqa: E402
from services.data_service import get_consensus_projections               # noqa: E402
from services.db_service import get_collection_df, get_db                 # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def find_consensus_doc_ids(df) -> list:
    """Doc ids of rows whose sources dict holds analyst numbers."""
    if df is None or len(df) == 0 or "sources" not in getattr(df, "columns", []):
        return []
    ids = []
    for _, row in df.iterrows():
        if numeric_sources(row.get("sources", {})):
            ids.append(f"{int(row['season'])}_{row['team']}")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true",
                    help="Required to actually delete")
    args = ap.parse_args()

    df = get_collection_df("preseason_predictions")
    doc_ids = find_consensus_doc_ids(df)
    if not doc_ids:
        log.info("No consensus rows found in preseason_predictions. Nothing to do.")
        return

    seasons = sorted({int(d.split("_")[0]) for d in doc_ids})
    log.info("Found %d consensus rows across seasons %s", len(doc_ids), seasons)

    # Safety gate: refuse to delete anything not already migrated.
    for season in seasons:
        migrated = get_consensus_projections(season)
        expected = sum(1 for d in doc_ids if d.startswith(f"{season}_"))
        if len(migrated) < expected:
            log.error(
                "Season %s: consensus_projections has %d teams but %d rows are "
                "queued for deletion. Run migrate_consensus.py --firestore first.",
                season, len(migrated), expected,
            )
            sys.exit(1)
        log.info("  %s verified: %d migrated rows present", season, len(migrated))

    if args.dry_run or not args.confirm:
        log.info("Dry run -- nothing deleted. Pass --confirm to proceed.")
        return

    db = get_db()
    if db is None:
        log.error("No database connection.")
        sys.exit(1)

    batch = db.batch()
    for i, doc_id in enumerate(doc_ids, start=1):
        batch.delete(db.collection("preseason_predictions").document(doc_id))
        if i % 400 == 0:
            batch.commit()
            batch = db.batch()
    if len(doc_ids) % 400 != 0:
        batch.commit()

    log.info("Deleted %d rows. Run scripts/refresh_local_pkls.py to update the mirror.",
             len(doc_ids))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Drop the `sources` union from the write path**

In `scripts/predict_season.py`, inside `_upload_predictions`, replace:

```python
            "sources": {"model": "nn_xgb_lr_ensemble"},
```

with:

```python
            "model_version": _model_version_string(),
```

and add this helper above `_upload_predictions`:

```python
def _model_version_string() -> str:
    """Concrete ensemble versions, e.g. 'nn_v14+xgb_v8+lr_v6'.

    Replaces the old sources={'model': ...} marker, which gave the field two
    different types depending on the season.
    """
    import json
    from pathlib import Path

    root = Path(__file__).parent.parent / "models"
    parts = []
    for fname, prefix, key in (
        ("model_registry.json", "nn", "latest"),
        ("xgb_registry.json", "xgb", "latest"),
        ("lr_registry.json", "lr", "latest"),
    ):
        try:
            with open(root / fname) as f:
                reg = json.load(f)
            ver = reg.get(key)
            if ver:
                parts.append(f"{prefix}_{ver}")
        except Exception:
            continue
    return "+".join(parts) if parts else "unknown"
```

Note the two registry shapes differ — `model_registry.json` has a top-level `latest` alongside a `models` list, while `xgb_registry.json` and `lr_registry.json` key versions directly and may lack `latest`. The `.get(key)` returning `None` is handled: that model is simply omitted from the string.

- [ ] **Step 5: Run tests**

Run: `pytest tests/ -q`
Expected: all pass

- [ ] **Step 6: Execute the migration and deprecation against Firestore**

Ordered, with verification between each:

```bash
python scripts/migrate_consensus.py --dry-run
python scripts/migrate_consensus.py --firestore
python scripts/refresh_local_pkls.py
```

Verify the migration reproduces the known baseline before deleting anything:

```bash
python -c "
import pickle, numpy as np, pandas as pd
c = pickle.load(open('.local_db/consensus_projections.pkl','rb'))
errs = []
for S in range(2017, 2026):
    sub = c[c['season'] == S]
    if sub.empty: continue
    a = pickle.load(open(f'.local_db/nfl_standings_{S}.pkl','rb'))[['team','wins']]
    m = sub[['team','consensus_mean']].merge(a, on='team')
    errs.extend((m['consensus_mean'] - m['wins']).abs().tolist())
print(f'consensus MAE {np.mean(errs):.2f} over n={len(errs)} (expect 2.18 / 285)')
"
```

**Gate:** MAE must be ≈2.18 over n=285. A material deviation means the migration lost or altered data — stop and investigate rather than proceeding to the deletion.

```bash
python scripts/deprecate_preseason_consensus.py --dry-run
python scripts/deprecate_preseason_consensus.py --confirm
python scripts/refresh_local_pkls.py
```

- [ ] **Step 7: Commit**

```bash
git add scripts/deprecate_preseason_consensus.py scripts/predict_season.py tests/test_deprecate_preseason_consensus.py
git commit -m "feat: deprecate consensus rows in preseason_predictions

Gated deletion refuses to run unless consensus_projections already holds the
rows. predict_season now writes model_version as a string instead of the
sources={'model': ...} marker that gave the field two types."
```

---

## Task 11: Preseason refresh orchestrator with freshness preflight

**Files:**
- Create: `scripts/refresh_preseason.py`
- Test: `tests/test_refresh_preseason.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `check_asset_freshness(tag, filename, local_path, fetch=None) -> dict`, `depth_chart_max_dt(path) -> str | None`, `diff_projections(before, after) -> list[dict]`, `STEPS`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_refresh_preseason.py`:

```python
"""Freshness preflight and projection diff. No test may hit the network."""
import json

import pytest

from scripts.refresh_preseason import (
    STEPS, check_asset_freshness, diff_projections,
)


def _fake_fetch(assets):
    def _fetch(tag):
        return {"assets": assets}
    return _fetch


def test_reports_remote_newer_than_local(tmp_path):
    local = tmp_path / "depth_charts_2026.csv"
    local.write_text("x")
    import os, time
    old = time.time() - 90 * 86400
    os.utime(local, (old, old))

    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", local,
        fetch=_fake_fetch([{"name": "depth_charts_2026.csv",
                            "updated_at": "2026-08-11T08:09:03Z"}]),
    )
    assert res["remote_updated_at"] == "2026-08-11T08:09:03Z"
    assert res["status"] == "stale"


def test_missing_remote_asset_is_reported_not_fatal(tmp_path):
    res = check_asset_freshness(
        "snap_counts", "snap_counts_2026.csv", tmp_path / "nope.csv",
        fetch=_fake_fetch([{"name": "snap_counts_2025.csv",
                            "updated_at": "2026-02-09T13:39:51Z"}]),
    )
    assert res["status"] == "absent"
    assert res["remote_updated_at"] is None


def test_unreachable_api_does_not_raise(tmp_path):
    def _boom(tag):
        raise OSError("network down")

    res = check_asset_freshness(
        "depth_charts", "depth_charts_2026.csv", tmp_path / "nope.csv", fetch=_boom
    )
    assert res["status"] == "unknown"


def test_diff_projections_sorts_by_absolute_movement():
    before = {"LA": 12.6, "BUF": 10.3, "KC": 9.5}
    after = {"LA": 13.1, "BUF": 10.2, "KC": 11.9}
    rows = diff_projections(before, after)
    assert [r["team"] for r in rows] == ["KC", "LA", "BUF"]
    assert rows[0]["change"] == pytest.approx(2.4)


def test_diff_handles_new_and_dropped_teams():
    rows = diff_projections({"LA": 12.0}, {"LA": 12.0, "NEW": 8.0})
    teams = {r["team"]: r for r in rows}
    assert teams["NEW"]["before"] is None


def test_required_steps_are_marked():
    by_name = {s["name"]: s for s in STEPS}
    assert by_name["Elo Recompute"]["required"] is True
    assert by_name["Season Projection"]["required"] is True
    assert by_name["nflverse Raw Data Sync"]["required"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refresh_preseason.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

Create `scripts/refresh_preseason.py`:

```python
#!/usr/bin/env python3
"""Full preseason refresh for one season, with an attributable before/after diff.

Chains the existing scripts in dependency order, following the STEPS pattern in
scripts/run_cron.py. Prints which teams moved and by how much -- that diff is
the point of the command, not a log detail.

Run twice for attributable deltas:
    python scripts/refresh_preseason.py --season 2026 --skip-sync   # constants only
    python scripts/refresh_preseason.py --season 2026               # + fresh rosters

Usage:
    python scripts/refresh_preseason.py --season 2026
    python scripts/refresh_preseason.py --season 2026 --check-freshness
"""
import argparse
import json
import logging
import pathlib
import pickle
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

ROOT = pathlib.Path(__file__).parent.parent
SCRIPTS_DIR = ROOT / "scripts"
LOCAL_DB = ROOT / ".local_db"
RAWDATA = ROOT / "rawdata"

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"refresh_preseason_{datetime.now():%Y%m%d}.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

NFLVERSE_RELEASES = "https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}"
STALE_AFTER_DAYS = 30

# (release tag, filename template) required by the preseason profile builder.
REQUIRED_ASSETS = [
    ("depth_charts", "depth_charts_{season}.csv", "depth_charts"),
    ("rosters", "roster_{season}.csv", "rosters"),
    ("weekly_rosters", "roster_weekly_{season}.csv", "weekly_rosters"),
    ("snap_counts", "snap_counts_{season}.csv", "snap_counts"),
    ("injuries", "injuries_{season}.csv", "injuries"),
]

STEPS = [
    {"name": "nflverse Raw Data Sync", "script": SCRIPTS_DIR / "sync_nflverse_data.py",
     "args": [], "required": False},
    {"name": "Elo Recompute", "script": SCRIPTS_DIR / "compute_elo.py",
     "args": [], "required": True},
    {"name": "Season Projection", "script": SCRIPTS_DIR / "predict_season.py",
     "args": ["--season", "{season}"], "required": True},
    {"name": "Game Prediction Backfill", "script": SCRIPTS_DIR / "backfill_schedule_predictions.py",
     "args": ["--seasons", "{season}", "{season}", "--firestore", "--force"], "required": True},
    {"name": "Analytics Cache Build", "script": SCRIPTS_DIR / "cache_builder.py",
     "args": ["--year", "{season}", "--force"], "required": False},
    {"name": "Local Mirror Refresh", "script": SCRIPTS_DIR / "refresh_local_pkls.py",
     "args": [], "required": False},
]


def _fetch_release(tag: str) -> dict:
    """GET the nflverse release metadata. urllib, not httpx -- no new dependency."""
    req = urllib.request.Request(
        NFLVERSE_RELEASES.format(tag=tag),
        headers={"User-Agent": "WinsPool/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def check_asset_freshness(tag: str, filename: str, local_path: pathlib.Path, fetch=None) -> dict:
    """Compare one nflverse asset's remote timestamp against the local file.

    Never raises: a GitHub outage must not block a refresh.
    """
    fetch = fetch or _fetch_release
    out = {
        "tag": tag, "filename": filename,
        "remote_updated_at": None, "local_mtime": None, "status": "unknown",
    }

    if local_path.exists():
        out["local_mtime"] = datetime.fromtimestamp(
            local_path.stat().st_mtime, tz=timezone.utc
        ).isoformat()

    try:
        release = fetch(tag)
    except Exception as e:
        log.warning("  %s: releases API unreachable (%s)", tag, e)
        return out

    asset = next((a for a in release.get("assets", []) if a["name"] == filename), None)
    if asset is None:
        out["status"] = "absent"
        return out

    out["remote_updated_at"] = asset["updated_at"]
    if out["local_mtime"] is None:
        out["status"] = "stale"
    else:
        remote = datetime.fromisoformat(asset["updated_at"].replace("Z", "+00:00"))
        local = datetime.fromisoformat(out["local_mtime"])
        out["status"] = "stale" if remote > local else "current"
    return out


def depth_chart_max_dt(path: pathlib.Path):
    """Latest snapshot timestamp inside the depth-chart CSV.

    A recently downloaded file can still hold only stale snapshots, and the
    profile builder keys off the latest per-player snapshot -- so this, not the
    file mtime, is what determines whether trades are visible.
    """
    if not path.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(path, usecols=["dt"], low_memory=False)
        return str(df["dt"].max())
    except Exception as e:
        log.warning("  could not read dt from %s: %s", path.name, e)
        return None


def run_freshness_preflight(season: int) -> None:
    log.info("-" * 60)
    log.info("Data freshness preflight")
    for tag, template, subdir in REQUIRED_ASSETS:
        filename = template.format(season=season)
        local = RAWDATA / subdir / filename
        res = check_asset_freshness(tag, filename, local)
        log.info("  %-16s %-28s remote=%s local=%s [%s]",
                 tag, filename,
                 res["remote_updated_at"] or "none",
                 (res["local_mtime"] or "none")[:19],
                 res["status"])
        if res["status"] == "absent":
            log.warning("    no 2026 asset published yet -- normal before games are played")

    dc = RAWDATA / "depth_charts" / f"depth_charts_{season}.csv"
    max_dt = depth_chart_max_dt(dc)
    if max_dt:
        log.info("  depth-chart latest snapshot: %s", max_dt)
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(str(max_dt).replace("Z", "+00:00"))).days
            if age > STALE_AFTER_DAYS:
                log.warning(
                    "    snapshot is %d days old -- profile features will barely move, "
                    "so an empty projection diff is expected", age)
        except ValueError:
            pass
    log.info("-" * 60)


def snapshot_projections(season: int) -> dict:
    """Current mean_wins per team, read from the local mirror."""
    path = LOCAL_DB / f"preseason_predictions_{season}.pkl"
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        return {r["team"]: float(r.get("mean_wins", r.get("projected_wins", 0)))
                for _, r in df.iterrows()}
    except Exception as e:
        log.warning("Could not snapshot projections: %s", e)
        return {}


def diff_projections(before: dict, after: dict) -> list:
    """Per-team before/after, sorted by absolute movement descending."""
    rows = []
    for team in sorted(set(before) | set(after)):
        b, a = before.get(team), after.get(team)
        change = (a - b) if (b is not None and a is not None) else None
        rows.append({"team": team, "before": b, "after": a, "change": change})
    rows.sort(key=lambda r: abs(r["change"]) if r["change"] is not None else 999,
              reverse=True)
    return rows


def run_step(step: dict, season: int) -> bool:
    script = step["script"]
    name = step["name"]
    if not script.exists():
        log.warning("[%s] Script not found: %s -- skipping", name, script)
        return False

    args = [a.format(season=season) for a in step["args"]]
    log.info("[%s] Starting...", name)
    try:
        result = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True, text=True, timeout=1800, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        log.error("[%s] FAILED: timed out after 1800s", name)
        return False

    for line in (result.stdout or "").strip().splitlines():
        log.info("  %s", line)
    for line in (result.stderr or "").strip().splitlines():
        log.warning("  [stderr] %s", line)

    if result.returncode != 0:
        log.error("[%s] FAILED with exit code %d", name, result.returncode)
        return False
    log.info("[%s] Complete", name)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--skip-sync", action="store_true",
                    help="Skip the nflverse sync, isolating non-data changes")
    ap.add_argument("--check-freshness", action="store_true",
                    help="Run the preflight and exit")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("Preseason refresh -- season %s", args.season)
    log.info("=" * 60)

    run_freshness_preflight(args.season)
    if args.check_freshness:
        return

    before = snapshot_projections(args.season)
    log.info("Snapshotted %d teams before refresh.", len(before))

    for step in STEPS:
        if args.skip_sync and step["name"] == "nflverse Raw Data Sync":
            log.info("[%s] Skipped (--skip-sync)", step["name"])
            continue
        if not run_step(step, args.season) and step["required"]:
            log.error("Required step '%s' failed. Aborting.", step["name"])
            sys.exit(1)

    after = snapshot_projections(args.season)
    rows = diff_projections(before, after)
    moved = [r for r in rows if r["change"] is not None and abs(r["change"]) >= 0.05]

    log.info("=" * 60)
    log.info("Projection changes -- %d of %d teams moved", len(moved), len(rows))
    log.info("%-6s %8s %8s %8s", "TEAM", "BEFORE", "AFTER", "CHANGE")
    for r in rows:
        b = f"{r['before']:.2f}" if r["before"] is not None else "—"
        a = f"{r['after']:.2f}" if r["after"] is not None else "—"
        c = f"{r['change']:+.2f}" if r["change"] is not None else "—"
        log.info("%-6s %8s %8s %8s", r["team"], b, a, c)
    log.info("=" * 60)

    if not moved:
        log.warning("No team moved. Check the preflight above -- if the depth-chart "
                    "snapshot is stale, the roster signal genuinely has not changed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refresh_preseason.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the preflight against real data**

```bash
python scripts/refresh_preseason.py --season 2026 --check-freshness
```

Expected: `depth_charts` and `rosters` report `stale` (remote newer than the June local copy); `snap_counts` and `injuries` report `absent`, which is normal preseason.

- [ ] **Step 6: Commit**

```bash
git add scripts/refresh_preseason.py tests/test_refresh_preseason.py
git commit -m "feat: add preseason refresh orchestrator with nflverse freshness preflight

Preflight reads the depth-chart max dt, not just file mtime -- the profile
builder keys off the latest per-player snapshot, so a freshly downloaded file
can still hold stale data. urllib not httpx: no new dependency."
```

---

## Task 12: Update project documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the new collection to the Firestore table**

In `CLAUDE.md`, in the "Firestore collections and their local equivalents" table, add after the `preseason_predictions` row:

```markdown
| `consensus_projections` | `.local_db/consensus_projections.pkl` + `_{year}.pkl` | Analyst win projections; `preseason_predictions` is model output only |
```

- [ ] **Step 2: Document the new scripts**

In the "Scripts (run individually as needed)" section, add under ML predictions:

```bash
# Consensus benchmark
python scripts/seed_consensus.py --season 2026 --firestore    # Seed analyst consensus from data/consensus_2026.csv
python scripts/migrate_consensus.py --firestore               # One-shot: move 2017-2025 consensus out of preseason_predictions
python scripts/refresh_preseason.py --season 2026             # Full preseason refresh + freshness preflight + projection diff
python scripts/refresh_preseason.py --season 2026 --check-freshness   # Preflight only
```

- [ ] **Step 3: Document the split dependency files**

`requirements-ml.txt` was added alongside this plan; record why it exists so
nobody folds it back into `requirements.txt`. Add to the "Stack" section of
`CLAUDE.md`, after the ML bullet:

```markdown
### Dependencies
- `requirements.txt` — web app only; this is what the Dockerfile installs.
- `requirements-ml.txt` — TensorFlow, scikit-learn, XGBoost, scipy. Install where you train or run batch predictions: `pip install -r requirements.txt -r requirements-ml.txt`. **Deliberately excluded from the deployed image** — Cloud Run reads stored predictions from Firestore and never loads a model, which is why the prediction services guard their imports behind `TF_AVAILABLE` / `SKLEARN_AVAILABLE`. TensorFlow is pinned because the `.keras` artifact format has changed across minor versions and `models/nn_v*.keras` were trained under 2.21.0.
```

- [ ] **Step 4: Verify no stale references**

```bash
grep -n "aggregate_scraper\|scrape_predictions" CLAUDE.md docs/*.md
```

Expected: no output. Remove any hits.

- [ ] **Step 5: Verify the deployed image is unchanged**

```bash
grep -n "requirements" Dockerfile
```

Expected: only `requirements.txt` — `requirements-ml.txt` must **not** appear,
or the image gains ~700MB in the request path.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document consensus_projections collection, new scripts, and dependency split"
```

---

## Verification

Run after all tasks complete.

- [ ] `pytest tests/ -q` passes
- [ ] `consensus_projections` holds 32 teams for every migrated season 2017–2025
- [ ] Consensus MAE reproduces **2.18 over n=285** (the gate in Task 10, step 6)
- [ ] `preseason_predictions` holds model rows only — `find_consensus_doc_ids` returns `[]`
- [ ] Historical draft results and recaps render projections unchanged
- [ ] Admin Consensus tab renders 32 rows for 2025 with accuracy columns, and the empty state for an unseeded 2026
- [ ] `refresh_preseason.py --season 2026 --skip-sync` produces a non-empty diff attributable to the Elo constants fix
- [ ] `refresh_preseason.py --season 2026` produces a second non-empty diff attributable to the roster sync
- [ ] `grep -rn "scrape_predictions\|aggregate_scraper" .` returns nothing outside `.git`

A large `outlier_z` on a specific team is a finding to investigate, not a failure — the tab exists to surface those.
