# Preseason Draft Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the six "frozen" preseason-projection readers (real draft room,
mock draft setup/bot-AI/grading, draft recap, draft results/history views) a
number that stops moving once a real draft (or a mock draft bot) has used it,
while the three "live" readers (admin forecast, admin consensus comparison,
weekly recap) keep reading today's continuously-updated model output
unchanged.

**Architecture:** A new `draft_snapshot_predictions` Firestore collection
mirrors `preseason_predictions`'s schema plus a `locked: bool` field.
`scripts/refresh_preseason.py` gains a step that copies that season's
freshly-written `preseason_predictions` docs into the snapshot collection,
skipping the copy once `draft_results` shows the season's draft has actually
started (and stamping `locked=True` on that final copy). The six frozen
readers all resolve their projections through four existing
`services/data_service.py` resolver functions
(`get_season_projection`/`_legacy_shape`/`_dual`/`_blended`); each gains a
`frozen: bool = False` parameter that swaps their model-side source from
`get_preseason_predictions()` to a new `get_draft_snapshot_predictions()`
without touching the consensus side or changing the returned shape. This
makes every reader swap a one-line `frozen=True` kwarg, not a rewrite. The
copy-and-lock logic lives once, in `services/db_service.py::sync_draft_snapshot_for_season()`,
so `scripts/write_draft_snapshot.py` (the scheduled/manual CLI path) and a
new admin-only `POST /api/admin/draft_snapshot/sync` endpoint (an
out-of-band manual trigger, e.g. after tuning rosters without waiting for
the next `refresh_preseason.py` run) call the identical function rather than
duplicating the copy/lock rule in two places.

**Tech Stack:** Python, FastAPI, Firestore (`google-cloud-firestore` via
`firebase_admin`), pandas, pytest (`unittest.mock.patch` /
`monkeypatch.setattr` for Firestore/resolver fakes — this codebase never
spins up a real or emulated Firestore in tests).

**Spec:** `docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md`

## Global Constraints

- `preseason_predictions` itself must not change behavior at all — the three
  live readers (`routes/admin_routes.py:698,746`, `services/recap_service.py:128`)
  keep calling the existing resolvers with `frozen` left at its default
  (`False`); do not add `frozen=True` to any of them.
- Any script that writes to Firestore must force
  `os.environ["USE_LOCAL_DATA"] = "False"` **before** importing anything from
  `services.db_service` — see CLAUDE.md's "any script that writes to
  Firestore must force USE_LOCAL_DATA=False" gotcha. Every new script in this
  plan follows the same pattern as `scripts/compute_elo.py --firestore` /
  `scripts/refresh_local_pkls.py`.
- New Firestore collection rule (CLAUDE.md "Rules for any new Firestore
  collection"): write to Firestore first, register it in
  `scripts/refresh_local_pkls.py`'s `COLLECTIONS` list, and keep the local pkl
  shape identical to what `services/db_service.py::get_collection_df()`
  expects — no format divergence between the local and Firestore paths.
- No test in this plan may hit the network or a real Firestore instance —
  follow the existing fake-Firestore class pattern in
  `tests/test_consensus_storage.py` (`FakeDB`/`FakeCollection`/`FakeBatch`/
  `FakeDoc`) exactly; do not introduce a new mocking style.
- Run `pytest tests/ -n auto` after every task and confirm 0 failures before
  moving to the next task.

---

## Task 1: `set_draft_snapshot_predictions()` and `sync_draft_snapshot_for_season()` in `services/db_service.py`

**Files:**
- Modify: `services/db_service.py` (add both functions after `set_preseason_predictions`, which ends at line 721)
- Test: `tests/test_consensus_storage.py` (add tests after the existing `set_preseason_predictions` tests, which end at line 362)

**Interfaces:**
- Produces: `set_draft_snapshot_predictions(season: int, projections: dict, model_version: str, locked: bool, force: bool = False) -> int` — same contract as `set_preseason_predictions`, targeting the `draft_snapshot_predictions` collection with doc id `f"{season}_{team}"`.
- Produces: `sync_draft_snapshot_for_season(season: int) -> dict` — reads that season's `preseason_predictions`, checks `draft_results` for that season, and calls `set_draft_snapshot_predictions()` with `locked` set to whether the draft has started. Returns `{"season": int, "written": int, "locked": bool}`. This is the **one** place the copy-and-lock rule is implemented — Task 5's CLI script and Task 9's admin endpoint both call this function directly rather than each re-implementing the rule.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_consensus_storage.py`:

```python
def test_set_draft_snapshot_predictions_writes_full_stats(monkeypatch):
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
        def __init__(self):
            self._docs = []

        def document(self, doc_id):
            return FakeDoc(doc_id)

        def where(self, *a, **k):
            return self

        def stream(self):
            return iter(self._docs)

    class FakeDB:
        def __init__(self):
            self.collections = {}

        def collection(self, name):
            return self.collections.setdefault(name, FakeCollection())

        def batch(self):
            return FakeBatch()

    import services.db_service as db_service
    fake_db = FakeDB()
    monkeypatch.setattr(db_service, "get_db", lambda: fake_db)
    signaled = []
    monkeypatch.setattr(db_service, "signal_data_update", lambda domain: signaled.append(domain))

    count = db_service.set_draft_snapshot_predictions(
        2026,
        {"KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0}},
        model_version="nn_v15+xgb_v9+lr_v7", locked=False,
    )

    assert count == 1
    payload = written["2026_KC"]
    assert payload["season"] == 2026
    assert payload["team"] == "KC"
    assert payload["projected_wins"] == 11.0
    assert payload["model_version"] == "nn_v15+xgb_v9+lr_v7"
    assert payload["locked"] is False
    assert "generated_at" in payload
    from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL
    assert len(signaled) == 1
    assert signaled[0] in (DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL)


def test_set_draft_snapshot_predictions_skips_locked_team_without_force(monkeypatch):
    written = {}

    class FakeDoc:
        def __init__(self, doc_id):
            self.doc_id = doc_id

    class FakeBatch:
        def set(self, ref, payload):
            written[ref.doc_id] = payload

        def commit(self):
            pass

    class FakeExistingDoc:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return self._data

    class FakeCollection:
        def __init__(self, existing_docs):
            self._existing = existing_docs

        def document(self, doc_id):
            return FakeDoc(doc_id)

        def where(self, *a, **k):
            return self

        def stream(self):
            return iter(self._existing)

    class FakeDB:
        def __init__(self, existing_docs):
            self._existing = existing_docs

        def collection(self, name):
            return FakeCollection(self._existing)

        def batch(self):
            return FakeBatch()

    import services.db_service as db_service
    existing = [FakeExistingDoc({"season": 2026, "team": "KC", "locked": True})]
    monkeypatch.setattr(db_service, "get_db", lambda: FakeDB(existing))
    monkeypatch.setattr(db_service, "signal_data_update", lambda domain: None)

    count = db_service.set_draft_snapshot_predictions(
        2026,
        {
            "KC":  {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                    "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
            "TEN": {"projected_wins": 5.0, "mean_wins": 5.3, "std_dev": 2.1,
                    "floor": 2.0, "p25": 4.0, "p75": 7.0, "ceiling": 9.0},
        },
        model_version="nn_v15+xgb_v9+lr_v7", locked=True,
    )

    # KC is already locked (draft has started for this season) -- skipped.
    # TEN has no existing doc -- written, and locked=True is stamped on it too
    # (the whole season locks together once the draft starts).
    assert count == 1
    assert "2026_KC" not in written
    assert written["2026_TEN"]["locked"] is True


def test_set_draft_snapshot_predictions_already_fully_locked_writes_nothing(monkeypatch):
    """A re-run of refresh_preseason.py after the draft has started must be a
    pure no-op for this collection -- the exact edge case the spec calls out."""
    written = {}

    class FakeDoc:
        def __init__(self, doc_id):
            self.doc_id = doc_id

    class FakeBatch:
        def set(self, ref, payload):
            written[ref.doc_id] = payload

        def commit(self):
            pass

    class FakeExistingDoc:
        def __init__(self, data):
            self._data = data

        def to_dict(self):
            return self._data

    class FakeCollection:
        def __init__(self, existing_docs):
            self._existing = existing_docs

        def document(self, doc_id):
            return FakeDoc(doc_id)

        def where(self, *a, **k):
            return self

        def stream(self):
            return iter(self._existing)

    class FakeDB:
        def __init__(self, existing_docs):
            self._existing = existing_docs

        def collection(self, name):
            return FakeCollection(self._existing)

        def batch(self):
            return FakeBatch()

    import services.db_service as db_service
    existing = [FakeExistingDoc({"season": 2026, "team": "KC", "locked": True})]
    monkeypatch.setattr(db_service, "get_db", lambda: FakeDB(existing))
    monkeypatch.setattr(db_service, "signal_data_update", lambda domain: None)

    count = db_service.set_draft_snapshot_predictions(
        2026,
        {"KC": {"projected_wins": 99.0, "mean_wins": 99.0, "std_dev": 0,
                "floor": 0, "p25": 0, "p75": 0, "ceiling": 0}},
        model_version="nn_v16", locked=True,
    )

    assert count == 0
    assert written == {}


def test_set_draft_snapshot_predictions_no_db_returns_zero(monkeypatch):
    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_db", lambda: None)

    count = db_service.set_draft_snapshot_predictions(
        2026, {"KC": {"projected_wins": 11.0}},
        model_version="nn_v15+xgb_v9+lr_v7", locked=False,
    )

    assert count == 0


def test_sync_draft_snapshot_for_season_copies_and_locks_when_draft_started(monkeypatch):
    import services.db_service as db_service

    def fake_get_collection_df(name, filters=None):
        if name == "preseason_predictions":
            return pd.DataFrame([{"season": 2026, "team": "KC", "projected_wins": 11.0,
                                   "mean_wins": 10.8, "std_dev": 1.95, "floor": 7.0,
                                   "p25": 9.0, "p75": 12.0, "ceiling": 14.0,
                                   "model_version": "nn_v15", "locked": False}])
        if name == "draft_results":
            return pd.DataFrame([{"season": 2026, "draftPick": 1, "playerId": 1, "team": "KC"}])
        return pd.DataFrame()

    monkeypatch.setattr(db_service, "get_collection_df", fake_get_collection_df)
    captured = {}

    def fake_set(season, projections, model_version, locked, force=False):
        captured["projections"] = projections
        captured["locked"] = locked
        return len(projections)

    monkeypatch.setattr(db_service, "set_draft_snapshot_predictions", fake_set)

    result = db_service.sync_draft_snapshot_for_season(2026)

    assert result == {"season": 2026, "written": 1, "locked": True}
    assert captured["locked"] is True
    assert captured["projections"]["KC"]["projected_wins"] == 11.0
    assert "season" not in captured["projections"]["KC"]


def test_sync_draft_snapshot_for_season_stays_unlocked_with_no_draft_results(monkeypatch):
    import services.db_service as db_service

    def fake_get_collection_df(name, filters=None):
        if name == "preseason_predictions":
            return pd.DataFrame([{"season": 2026, "team": "KC", "projected_wins": 11.0,
                                   "mean_wins": 10.8, "std_dev": 1.95, "floor": 7.0,
                                   "p25": 9.0, "p75": 12.0, "ceiling": 14.0,
                                   "model_version": "nn_v15", "locked": False}])
        if name == "draft_results":
            return pd.DataFrame()
        return pd.DataFrame()

    monkeypatch.setattr(db_service, "get_collection_df", fake_get_collection_df)
    monkeypatch.setattr(db_service, "set_draft_snapshot_predictions",
                        lambda season, projections, model_version, locked, force=False: len(projections))

    result = db_service.sync_draft_snapshot_for_season(2026)

    assert result == {"season": 2026, "written": 1, "locked": False}


def test_sync_draft_snapshot_for_season_no_preseason_data_is_a_noop(monkeypatch):
    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_collection_df", lambda name, filters=None: pd.DataFrame())
    calls = []
    monkeypatch.setattr(db_service, "set_draft_snapshot_predictions",
                        lambda *a, **k: calls.append((a, k)) or 0)

    result = db_service.sync_draft_snapshot_for_season(2026)

    assert result == {"season": 2026, "written": 0, "locked": False}
    assert calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_consensus_storage.py -k draft_snapshot -v`
Expected: FAIL with `AttributeError: module 'services.db_service' has no attribute 'set_draft_snapshot_predictions'`

- [ ] **Step 3: Implement `set_draft_snapshot_predictions()` and `sync_draft_snapshot_for_season()`**

Add to `services/db_service.py` immediately after `set_preseason_predictions` (after line 721):

```python
def set_draft_snapshot_predictions(season: int, projections: dict, model_version: str,
                                    locked: bool, force: bool = False) -> int:
    """Write draft_snapshot_predictions docs for a season, respecting per-team locks.

    Identical contract to set_preseason_predictions() -- see
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.
    A team's existing doc is skipped (not overwritten) when it's already
    locked=True and force=False, so once a season's draft has started
    (locked=True was stamped on every team in one call), every later call for
    that season writes zero docs -- a pure no-op re-run is exactly the
    behavior the spec requires when refresh_preseason.py runs again after the
    real draft has begun.

    projections: {team: {projected_wins, mean_wins, std_dev, floor, p25,
    p75, ceiling}} -- same shape set_preseason_predictions() takes.

    Returns the number of docs actually written (skipped-due-to-lock docs
    don't count).
    """
    db = get_db()
    if db is None:
        logger.warning("No database connection; draft snapshot predictions not written.")
        return 0

    existing_locked = set()
    if not force:
        from google.cloud.firestore_v1.base_query import FieldFilter
        for doc in db.collection("draft_snapshot_predictions").where(filter=FieldFilter("season", "==", season)).stream():
            data = doc.to_dict()
            if data.get("locked"):
                existing_locked.add(data.get("team"))

    batch = db.batch()
    written = 0
    for team, stats in projections.items():
        if team in existing_locked:
            continue
        ref = db.collection("draft_snapshot_predictions").document(f"{season}_{team}")
        batch.set(ref, {
            "season": int(season),
            "team": team,
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

    if written:
        from services.cache_service import DOMAIN_PREDICTIONS_ACTIVE, DOMAIN_PREDICTIONS_HISTORICAL
        from services.data_service import _get_active_bucket
        domain = DOMAIN_PREDICTIONS_ACTIVE if season == _get_active_bucket()["season"] else DOMAIN_PREDICTIONS_HISTORICAL
        signal_data_update(domain)
    return written


_DRAFT_SNAPSHOT_FIELDS = ["projected_wins", "mean_wins", "std_dev", "floor", "p25", "p75", "ceiling"]


def sync_draft_snapshot_for_season(season: int) -> dict:
    """Copy season's preseason_predictions into draft_snapshot_predictions,
    locking it once draft_results shows the season's draft has started.

    The single implementation of the copy-and-lock rule -- both
    scripts/write_draft_snapshot.py (run as a refresh_preseason.py step) and
    the admin-triggered POST /api/admin/draft_snapshot/sync endpoint call
    this directly, so a manual admin re-sync behaves identically to the
    scheduled one. See
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.

    draft_results only gains rows once a real pick has been made
    (add_draft_result() above), so a non-empty result for this season is a
    persistent, season-scoped fact -- unlike the transient draft_active
    config flag -- and is what determines `locked`.

    Returns {"season": int, "written": int, "locked": bool}. `written` is 0
    when there's no preseason_predictions data yet, or when the season is
    already fully locked (every team's existing snapshot doc has locked=True).
    """
    preds_df = get_collection_df("preseason_predictions", filters=[("season", "==", season)])
    if preds_df.empty:
        return {"season": season, "written": 0, "locked": False}

    results_df = get_collection_df("draft_results", filters=[("season", "==", season)])
    draft_started = not results_df.empty

    model_version = preds_df.iloc[0].get("model_version", "unknown")
    projections = {
        row["team"]: {field: row.get(field) for field in _DRAFT_SNAPSHOT_FIELDS}
        for _, row in preds_df.iterrows()
    }

    written = set_draft_snapshot_predictions(season, projections, model_version=model_version, locked=draft_started)
    return {"season": season, "written": written, "locked": draft_started}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_consensus_storage.py -k draft_snapshot -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add services/db_service.py tests/test_consensus_storage.py
git commit -m "feat: add set_draft_snapshot_predictions write path and sync_draft_snapshot_for_season"
```

---

## Task 2: `get_draft_snapshot_predictions()` in `services/data_service.py`

**Files:**
- Modify: `services/data_service.py` (add function after `get_preseason_predictions`, which ends at line 420)
- Test: `tests/test_data_service.py` (add after the existing preseason-predictions caching tests, which end around line 377)

**Interfaces:**
- Consumes: `_get_predictions_bucket_entry(season) -> dict` (`services/data_service.py:373`), `get_collection_df(collection, filters=...)` (`services/db_service.py:138`, imported into `data_service` at line 9)
- Produces: `get_draft_snapshot_predictions(season: int) -> Dict[str, dict]` — same return shape as `get_preseason_predictions()`: `{team: {"projected_wins", "mean_wins", "std_dev", "sources"}}`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_service.py`:

```python
def test_get_draft_snapshot_predictions_shape_matches_preseason(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame([
            {"season": active_season, "team": "KC", "projected_wins": 11.0,
             "mean_wins": 10.8, "std_dev": 1.95, "locked": True},
        ])
        res = data_service.get_draft_snapshot_predictions(active_season)
        snapshot_calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "draft_snapshot_predictions"]
        assert len(snapshot_calls) == 1
        assert res["KC"]["projected_wins"] == 11.0
        assert res["KC"]["mean_wins"] == 10.8
    cs.clear_data_cache()


def test_get_draft_snapshot_predictions_empty_when_no_rows(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        assert data_service.get_draft_snapshot_predictions(active_season) == {}
    cs.clear_data_cache()


def test_get_draft_snapshot_predictions_is_cached(monkeypatch):
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_draft_snapshot_predictions(active_season)
        data_service.get_draft_snapshot_predictions(active_season)  # second call
        calls = [c for c in mock_fetch.call_args_list if c.args and c.args[0] == "draft_snapshot_predictions"]
        assert len(calls) == 1
    cs.clear_data_cache()


def test_get_draft_snapshot_predictions_does_not_share_cache_with_preseason(monkeypatch):
    """The two collections must fetch independently -- warming one must not
    mark the other as already fetched."""
    import services.cache_service as cs
    from services import data_service
    cs.clear_data_cache()
    data_service.load_data()
    active_season = cs.get_domain(cs.DOMAIN_ACTIVE)["season"]

    with patch("services.data_service.get_collection_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        data_service.get_preseason_predictions(active_season)
        data_service.get_draft_snapshot_predictions(active_season)
        calls = [c for c in mock_fetch.call_args_list
                 if c.args and c.args[0] in ("preseason_predictions", "draft_snapshot_predictions")]
        assert len(calls) == 2
    cs.clear_data_cache()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_data_service.py -k draft_snapshot -v`
Expected: FAIL with `AttributeError: module 'services.data_service' has no attribute 'get_draft_snapshot_predictions'`

- [ ] **Step 3: Implement `get_draft_snapshot_predictions()`**

Add to `services/data_service.py` immediately after `get_preseason_predictions` (after line 420, before `get_consensus_projections`):

```python
def get_draft_snapshot_predictions(season: int) -> Dict[str, dict]:
    """Frozen, pre-draft snapshot of model win projections for `season`.

    Same shape and caching pattern as get_preseason_predictions(), but reads
    draft_snapshot_predictions instead -- a number that stops moving once a
    real draft (or a mock draft bot) has used it. See
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.
    """
    entry = _get_predictions_bucket_entry(season)
    if "snapshot_df" not in entry:
        entry["snapshot_df"] = get_collection_df("draft_snapshot_predictions", filters=[("season", "==", season)])
    preds_df = entry["snapshot_df"]
    if preds_df.empty:
        return {}

    res = {}
    for _, row in preds_df.iterrows():
        mean_wins = row.get("mean_wins")
        res[row["team"]] = {
            "projected_wins": float(row.get("projected_wins", 0)),
            "mean_wins": float(mean_wins) if pd.notna(mean_wins) else float(row.get("projected_wins", 0)),
            "std_dev": float(row.get("std_dev", 0)),
            "sources": row.get("sources", {})
        }
    return res
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_data_service.py -k draft_snapshot -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add services/data_service.py tests/test_data_service.py
git commit -m "feat: add get_draft_snapshot_predictions read path"
```

---

## Task 3: `frozen` parameter on the four projection resolvers

**Files:**
- Modify: `services/data_service.py:444-578` (`get_season_projection`, `get_season_projection_legacy_shape`, `get_season_projection_dual`, `get_season_projection_blended`)
- Test: `tests/test_season_projection_resolver.py`

**Interfaces:**
- Consumes: `get_draft_snapshot_predictions(season)` (Task 2), `get_preseason_predictions(season)`, `get_consensus_projections(season)`
- Produces: `get_season_projection(season, frozen=False)`, `get_season_projection_legacy_shape(season, frozen=False)`, `get_season_projection_dual(season, frozen=False)`, `get_season_projection_blended(season, frozen=False)` — all four keep their existing return shape and default behavior; `frozen=True` is the only new behavior.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_season_projection_resolver.py`:

```python
# --- frozen=True reads draft_snapshot_predictions instead of the live model -

def test_frozen_reads_snapshot_not_live_model(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: {"LA": {"projected_wins": 99.0, "mean_wins": 99.0}})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions",
                        lambda s: {"LA": {"projected_wins": 12.0, "mean_wins": 11.6}})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    res = data_service.get_season_projection(2026, frozen=True)
    assert res["LA"]["wins"] == 11.6

    live = data_service.get_season_projection(2026, frozen=False)
    assert live["LA"]["wins"] == 99.0


def test_frozen_default_is_false(monkeypatch):
    """Every existing caller that never passes frozen must keep reading the
    live model -- this is the backward-compatibility guarantee the whole
    reader swap depends on."""
    calls = []
    monkeypatch.setattr(data_service, "get_preseason_predictions",
                        lambda s: calls.append("live") or {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions",
                        lambda s: calls.append("frozen") or {})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    data_service.get_season_projection(2026)
    assert calls == ["live"]


def test_legacy_shape_frozen_reads_snapshot(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions",
                        lambda s: {"LA": {"projected_wins": 12.0, "mean_wins": 11.6,
                                          "std_dev": 2.1, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    res = data_service.get_season_projection_legacy_shape(2026, frozen=True)
    assert res["LA"]["projected_wins"] == 12.0


def test_dual_frozen_reads_snapshot(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions",
                        lambda s: {"LA": {"projected_wins": 12.0, "mean_wins": 11.6,
                                          "std_dev": 2.1, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections", lambda s: {})

    res = data_service.get_season_projection_dual(2026, frozen=True)
    assert res["LA"]["model"]["projected_wins"] == 12.0


def test_blended_frozen_uses_snapshot_for_the_model_side(monkeypatch):
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions",
                        lambda s: {"LA": {"mean_wins": 12.0, "std_dev": 1.0,
                                          "projected_wins": 12.0, "sources": {}}})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"LA": {"consensus_mean": 8.0, "consensus_median": 8.0,
                                          "consensus_std": 2.0, "sources": {"br": 8}}})

    res = data_service.get_season_projection_blended(2026, frozen=True)
    straight_average = (12.0 + 8.0) / 2
    assert straight_average < res["LA"]["projected_wins"] < 12.0


def test_blended_frozen_falls_back_to_consensus_when_no_snapshot_exists(monkeypatch):
    """A historical, consensus-only season (2017-2023) has no
    draft_snapshot_predictions row at all -- frozen=True must still surface
    the consensus figure, not silently zero it."""
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: {"ARI": {"consensus_mean": 8.25, "consensus_median": 7.5,
                                           "consensus_std": 1.75, "sources": {"br": 10}}})

    res = data_service.get_season_projection_blended(2017, frozen=True)
    assert res["ARI"]["projected_wins"] == 7.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_season_projection_resolver.py -k frozen -v`
Expected: FAIL with `TypeError: get_season_projection() got an unexpected keyword argument 'frozen'`

- [ ] **Step 3: Add the `frozen` parameter to all four resolvers**

In `services/data_service.py`, change the four function signatures and their model-source line:

```python
def get_season_projection(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Resolve the best available win projection for a season, per team.

    Model output wins when it exists; analyst consensus is the fallback. This
    lets preseason_predictions mean "model output" and consensus_projections
    mean "analyst consensus" without historical views losing their numbers.

    frozen=True reads the model side from draft_snapshot_predictions instead
    of preseason_predictions -- see get_draft_snapshot_predictions() and
    docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.
    Consensus is never frozen; only readers that must show a stable, pre-draft
    number pass frozen=True.

    Returns {team: {"wins": float, "source_type": "model"|"consensus", "detail": dict}}
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)
```

(leave the rest of the function body unchanged).

```python
def get_season_projection_legacy_shape(season: int, frozen: bool = False) -> Dict[str, dict]:
    """get_season_projection() flattened to the shape the UI has always read.

    ...(existing docstring unchanged)...
    """
    out = {}
    for team, proj in get_season_projection(int(season), frozen=frozen).items():
```

(leave the rest of the function body unchanged).

```python
def get_season_projection_dual(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Per-team model AND consensus projections, both exposed separately --
    ...(existing docstring unchanged)...
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)
```

(leave the rest of the function body unchanged).

```python
def get_season_projection_blended(season: int, frozen: bool = False) -> Dict[str, dict]:
    """Per-team inverse-variance blend of model and analyst-consensus wins.

    ...(existing docstring unchanged)...
    """
    model = get_draft_snapshot_predictions(season) if frozen else get_preseason_predictions(season)
    consensus = get_consensus_projections(season)
```

(leave the rest of the function body unchanged).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_season_projection_resolver.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Run the full resolver + data_service suites to check for regressions**

Run: `pytest tests/test_season_projection_resolver.py tests/test_data_service.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/data_service.py tests/test_season_projection_resolver.py
git commit -m "feat: add frozen parameter to season projection resolvers"
```

---

## Task 4: Register `draft_snapshot_predictions` in `refresh_local_pkls.py`

**Files:**
- Modify: `scripts/refresh_local_pkls.py:35-46`
- Test: `tests/test_consensus_storage.py`

**Interfaces:**
- Consumes: nothing new (uses the existing generic `dump_collection()`)
- Produces: `draft_snapshot_predictions` present in the `COLLECTIONS` list, so `.local_db/draft_snapshot_predictions.pkl` + per-season slices get written by every `refresh_local_pkls.py` run.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_consensus_storage.py`:

```python
def test_refresh_local_pkls_registers_draft_snapshot_collection():
    from scripts.refresh_local_pkls import COLLECTIONS
    assert ("draft_snapshot_predictions", "season") in COLLECTIONS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_consensus_storage.py -k registers_draft_snapshot -v`
Expected: FAIL with `AssertionError`

- [ ] **Step 3: Register the collection**

In `scripts/refresh_local_pkls.py`, change the `COLLECTIONS` list (lines 35-46):

```python
COLLECTIONS = [
    ("players",               None),
    ("draft_results",         None),
    ("draft_order",           None),
    ("draft_order_rules",     None),
    ("nfl_teams",             None),
    ("nfl_standings",         "season"),
    ("nfl_games",             "season"),
    ("weekly_recaps",         "year"),
    ("preseason_predictions", "season"),
    ("consensus_projections", "season"),
    ("draft_snapshot_predictions", "season"),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_consensus_storage.py -k registers_draft_snapshot -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/refresh_local_pkls.py tests/test_consensus_storage.py
git commit -m "feat: mirror draft_snapshot_predictions to local pkl cache"
```

---

## Task 5: `scripts/write_draft_snapshot.py` — thin CLI wrapper

**Files:**
- Create: `scripts/write_draft_snapshot.py`
- Test: `tests/test_write_draft_snapshot.py`

**Interfaces:**
- Consumes: `services.db_service.sync_draft_snapshot_for_season(season)` (Task 1) — this script contains **no** copy/lock logic of its own; it is a CLI entry point that forces `USE_LOCAL_DATA=False`, calls the shared function, and prints the result. This is deliberate: Task 9's admin endpoint calls the exact same function, so the rule can only be implemented once.
- Produces: `main()` (argparse entry point), importable for the test.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_write_draft_snapshot.py`:

```python
"""scripts/write_draft_snapshot.py -- thin CLI wrapper around
services.db_service.sync_draft_snapshot_for_season(). All copy/lock logic
lives in that shared function (see tests/test_consensus_storage.py's
sync_draft_snapshot_for_season tests) -- this file only tests argument
parsing and the print branches."""
from unittest.mock import patch

from scripts.write_draft_snapshot import main


def test_main_parses_season_and_calls_sync(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 32, "locked": False}
        main()
    mock_sync.assert_called_once_with(2026)


def test_main_prints_locked_state(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 32, "locked": True}
        main()
    out = capsys.readouterr().out
    assert "locked" in out.lower()
    assert "32" in out


def test_main_prints_skip_when_nothing_written(monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys, "argv", ["write_draft_snapshot.py", "--season", "2026"])
    with patch("scripts.write_draft_snapshot.sync_draft_snapshot_for_season") as mock_sync:
        mock_sync.return_value = {"season": 2026, "written": 0, "locked": False}
        main()
    out = capsys.readouterr().out
    assert "skip" in out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_write_draft_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.write_draft_snapshot'`

- [ ] **Step 3: Implement the script**

Create `scripts/write_draft_snapshot.py`:

```python
#!/usr/bin/env python3
"""write_draft_snapshot.py -- copy one season's preseason_predictions into
draft_snapshot_predictions, locking it once that season's real draft has
started.

Run as a step of scripts/refresh_preseason.py, right after "Season
Projection" writes fresh preseason_predictions rows. All copy/lock logic
lives in services.db_service.sync_draft_snapshot_for_season() -- this script
is only a CLI entry point, so the admin-triggered
POST /api/admin/draft_snapshot/sync endpoint (routes/admin_routes.py) can
call the identical function without duplicating the rule. See
docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md.

Usage:
    python scripts/write_draft_snapshot.py --season 2026
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Must be set before importing services.db_service -- see CLAUDE.md's
# "any script that writes to Firestore must force USE_LOCAL_DATA=False" gotcha.
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import sync_draft_snapshot_for_season


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    args = ap.parse_args()

    result = sync_draft_snapshot_for_season(args.season)
    if result["written"] > 0:
        state = "locked (draft has started)" if result["locked"] else "unlocked"
        print(f"  [ok]   draft_snapshot_predictions season={args.season} "
              f"({result['written']} teams written, {state})")
    else:
        print(f"  [skip] draft_snapshot_predictions season={args.season}: 0 teams written "
              f"(no preseason_predictions yet, or season already locked)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_write_draft_snapshot.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/write_draft_snapshot.py tests/test_write_draft_snapshot.py
git commit -m "feat: add write_draft_snapshot.py CLI wrapper"
```

---

## Task 6: Wire `write_draft_snapshot.py` into `refresh_preseason.py`

**Files:**
- Modify: `scripts/refresh_preseason.py:71-84` (`STEPS` list)
- Test: `tests/test_refresh_preseason.py`

**Interfaces:**
- Consumes: `scripts/write_draft_snapshot.py` (Task 5) as a subprocess step, same pattern as the other `STEPS` entries.
- Produces: a `"Draft Snapshot Sync"` entry in `STEPS`, positioned right after `"Season Projection"`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_refresh_preseason.py`:

```python
def test_draft_snapshot_step_runs_right_after_season_projection():
    names = [s["name"] for s in STEPS]
    assert "Draft Snapshot Sync" in names
    proj_idx = names.index("Season Projection")
    snap_idx = names.index("Draft Snapshot Sync")
    assert snap_idx == proj_idx + 1


def test_draft_snapshot_step_is_required_and_targets_the_season():
    by_name = {s["name"]: s for s in STEPS}
    step = by_name["Draft Snapshot Sync"]
    assert step["required"] is True
    assert step["args"] == ["--season", "{season}"]
    assert step["script"].name == "write_draft_snapshot.py"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refresh_preseason.py -k draft_snapshot_step -v`
Expected: FAIL with `AssertionError` (or `ValueError` from `.index()` not finding `"Draft Snapshot Sync"`)

- [ ] **Step 3: Add the step**

In `scripts/refresh_preseason.py`, change `STEPS` (lines 71-84):

```python
STEPS = [
    {"name": "nflverse Raw Data Sync", "script": SCRIPTS_DIR / "sync_nflverse_data.py",
     "args": ["--season", "{season}"], "required": False},
    {"name": "Elo Recompute", "script": SCRIPTS_DIR / "compute_elo.py",
     "args": ["--max-season", "{season}"], "required": True},
    {"name": "Season Projection", "script": SCRIPTS_DIR / "predict_season.py",
     "args": ["--season", "{season}"], "required": True},
    {"name": "Draft Snapshot Sync", "script": SCRIPTS_DIR / "write_draft_snapshot.py",
     "args": ["--season", "{season}"], "required": True},
    {"name": "Game Prediction Backfill", "script": SCRIPTS_DIR / "backfill_schedule_predictions.py",
     "args": ["--seasons", "{season}", "{season}", "--firestore", "--force"], "required": True},
    {"name": "Analytics Cache Build", "script": SCRIPTS_DIR / "cache_builder.py",
     "args": ["--year", "{season}", "--force"], "required": False},
    {"name": "Local Mirror Refresh", "script": SCRIPTS_DIR / "refresh_local_pkls.py",
     "args": [], "required": False},
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refresh_preseason.py -v`
Expected: PASS (all tests, old and new)

- [ ] **Step 5: Commit**

```bash
git add scripts/refresh_preseason.py tests/test_refresh_preseason.py
git commit -m "feat: run draft snapshot sync as a refresh_preseason.py step"
```

---

## Task 7: `scripts/backfill_draft_snapshot.py` — one-time historical backfill

**Files:**
- Create: `scripts/backfill_draft_snapshot.py`
- Test: `tests/test_backfill_draft_snapshot.py`

**Interfaces:**
- Consumes: `services.db_service.get_collection_df`, `services.db_service.set_draft_snapshot_predictions` (Task 1)
- Produces: `find_locked_seasons(preds_df: pd.DataFrame) -> list[int]`, `run(dry_run: bool = True) -> dict` (returns `{season: docs_written}`), both importable for the test.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backfill_draft_snapshot.py`:

```python
"""scripts/backfill_draft_snapshot.py -- one-time copy of every already-locked
past season's preseason_predictions into draft_snapshot_predictions.

Pure copy, not a recomputation -- nothing rewrites a preseason_predictions
doc once locked=True (see scripts/cache_builder.py's set_preseason_predictions
call), so every locked season's numbers are already frozen at the source."""
import pandas as pd

from scripts.backfill_draft_snapshot import find_locked_seasons, run


def test_find_locked_seasons_only_returns_fully_locked_seasons():
    preds_df = pd.DataFrame([
        {"season": 2024, "team": "KC", "locked": True},
        {"season": 2024, "team": "SF", "locked": True},
        {"season": 2025, "team": "KC", "locked": True},
        {"season": 2025, "team": "SF", "locked": False},  # not fully locked
        {"season": 2026, "team": "KC", "locked": False},
    ])

    seasons = find_locked_seasons(preds_df)

    assert seasons == [2024]  # 2025 has an unlocked team, 2026 has none locked


def test_find_locked_seasons_treats_missing_locked_field_as_unlocked():
    """predict_season.py's manual writes never set `locked` at all (see
    CLAUDE.md's footgun note) -- those rows must not be mistaken for locked."""
    preds_df = pd.DataFrame([
        {"season": 2019, "team": "KC"},  # no `locked` column value at all
    ])
    assert find_locked_seasons(preds_df) == []


def test_run_dry_run_does_not_write(monkeypatch):
    import scripts.backfill_draft_snapshot as bds
    monkeypatch.setattr(bds, "get_collection_df", lambda name, filters=None: pd.DataFrame([
        {"season": 2024, "team": "KC", "projected_wins": 10.0, "mean_wins": 9.8,
         "std_dev": 1.5, "floor": 6.0, "p25": 8.0, "p75": 11.0, "ceiling": 13.0,
         "model_version": "nn_v9", "locked": True},
    ]))
    calls = []
    monkeypatch.setattr(bds, "set_draft_snapshot_predictions",
                        lambda *a, **k: calls.append((a, k)) or 0)

    result = bds.run(dry_run=True)

    assert result == {2024: 1}  # reports what WOULD be written
    assert calls == []


def test_run_live_writes_each_locked_season(monkeypatch):
    import scripts.backfill_draft_snapshot as bds
    monkeypatch.setattr(bds, "get_collection_df", lambda name, filters=None: pd.DataFrame([
        {"season": 2024, "team": "KC", "projected_wins": 10.0, "mean_wins": 9.8,
         "std_dev": 1.5, "floor": 6.0, "p25": 8.0, "p75": 11.0, "ceiling": 13.0,
         "model_version": "nn_v9", "locked": True},
        {"season": 2025, "team": "SF", "projected_wins": 12.0, "mean_wins": 11.5,
         "std_dev": 1.2, "floor": 9.0, "p25": 11.0, "p75": 13.0, "ceiling": 14.0,
         "model_version": "nn_v10", "locked": True},
    ]))
    captured = []

    def fake_set(season, projections, model_version, locked, force=False):
        captured.append((season, locked))
        return len(projections)

    monkeypatch.setattr(bds, "set_draft_snapshot_predictions", fake_set)

    result = bds.run(dry_run=False)

    assert result == {2024: 1, 2025: 1}
    assert (2024, True) in captured
    assert (2025, True) in captured
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_backfill_draft_snapshot.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.backfill_draft_snapshot'`

- [ ] **Step 3: Implement the script**

Create `scripts/backfill_draft_snapshot.py`:

```python
#!/usr/bin/env python3
"""backfill_draft_snapshot.py -- one-time copy of every already-locked past
season's preseason_predictions into draft_snapshot_predictions.

Pure copy, not a recomputation: nothing rewrites a preseason_predictions doc
once locked=True (scripts/cache_builder.py stamps that on the last write of a
completed season), so every locked season's numbers are already frozen at the
source. This keeps every season-spanning "frozen" reader (draft results/
history views in particular) uniform -- always read draft_snapshot_predictions,
never branch on "is this season new enough to have a snapshot."

Usage:
    python scripts/backfill_draft_snapshot.py             # dry run, prints plan
    python scripts/backfill_draft_snapshot.py --live       # actually writes
"""
import argparse
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# Must be set before importing services.db_service -- see CLAUDE.md's
# "any script that writes to Firestore must force USE_LOCAL_DATA=False" gotcha.
os.environ["USE_LOCAL_DATA"] = "False"

from services.db_service import get_collection_df, set_draft_snapshot_predictions

SNAPSHOT_FIELDS = ["projected_wins", "mean_wins", "std_dev", "floor", "p25", "p75", "ceiling"]


def find_locked_seasons(preds_df) -> list:
    """Seasons where every team's preseason_predictions row has locked=True.

    A season with even one unlocked team (still being refreshed) or with no
    `locked` column value at all (predict_season.py's manual writes never set
    it) is excluded -- only a season that is unambiguously frozen at the
    source is safe to copy.
    """
    if preds_df.empty or "season" not in preds_df.columns:
        return []
    locked_col = preds_df["locked"] if "locked" in preds_df.columns else False
    preds_df = preds_df.assign(_locked=locked_col.fillna(False) if hasattr(locked_col, "fillna") else locked_col)
    seasons = []
    for season, group in preds_df.groupby("season"):
        if bool(group["_locked"].all()):
            seasons.append(int(season))
    return sorted(seasons)


def run(dry_run: bool = True) -> dict:
    """Copy every fully-locked season's preseason_predictions into
    draft_snapshot_predictions with locked=True. Returns {season: docs_written}
    -- in a dry run this is what WOULD be written, and no write happens."""
    preds_df = get_collection_df("preseason_predictions")
    locked_seasons = find_locked_seasons(preds_df)

    result = {}
    for season in locked_seasons:
        season_df = preds_df[preds_df["season"] == season]
        model_version = season_df.iloc[0].get("model_version", "unknown")
        projections = {
            row["team"]: {field: row.get(field) for field in SNAPSHOT_FIELDS}
            for _, row in season_df.iterrows()
        }
        if dry_run:
            result[season] = len(projections)
            print(f"  [dry-run] season={season}: would write {len(projections)} teams, locked=True")
        else:
            n = set_draft_snapshot_predictions(season, projections, model_version=model_version, locked=True)
            result[season] = n
            print(f"  [ok] season={season}: wrote {n} teams, locked=True")
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Actually write (default is dry-run)")
    args = ap.parse_args()
    run(dry_run=not args.live)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_backfill_draft_snapshot.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_draft_snapshot.py tests/test_backfill_draft_snapshot.py
git commit -m "feat: add one-time backfill_draft_snapshot.py for locked seasons"
```

- [ ] **Step 6: Run the live backfill once, against production**

This is the one operational (non-code) step in this plan. After all other
tasks are merged and deployed:

```bash
python scripts/backfill_draft_snapshot.py            # review the dry-run output first
python scripts/backfill_draft_snapshot.py --live      # then actually write
python scripts/refresh_local_pkls.py                  # sync the local mirror
```

---

## Task 8: Swap the six frozen readers to `frozen=True`

**Files:**
- Modify: `services/draft_service.py:154,160`
- Modify: `routes/mock_draft_routes.py:111-112`
- Modify: `services/mock_draft_service.py:131,158`
- Modify: `routes/draft_routes.py:422`
- Modify: `routes/history_routes.py:205`
- Test: `tests/test_draft_service.py` (update two existing tests)
- Test: `tests/test_historical_projection_routes.py` (update one fixture)

**Interfaces:**
- Consumes: `frozen` parameter from Task 3.
- Produces: no new interfaces -- every change here is adding `frozen=True` to an existing call.

- [ ] **Step 1: Update the two existing tests that patch the wrong resolver**

`tests/test_draft_service.py`'s `TestPreseasonPredictionsShape` class (around
lines 135-191) currently patches `services.data_service.get_preseason_predictions`
and expects `load_draft_state()` to reflect it. Once Step 3 below makes
`draft_service.py` call `get_season_projection_blended(..., frozen=True)`,
the draft room reads `get_draft_snapshot_predictions` instead -- so these two
tests must patch that function instead. Change both `@patch` decorators in
that class:

```python
    @patch("services.data_service.get_consensus_projections")
    @patch("services.data_service.get_draft_snapshot_predictions")
    def test_preseason_predictions_preserves_true_projected_wins(
        self, mock_get_draft_snapshot_predictions, mock_get_consensus_projections
    ):
        """ui_renderer.js:195 renders `${pred.projected_wins}W` straight from this
        payload. A team with only a model projection (no consensus row) must
        keep the model's own rounded projected_wins (e.g. 7.0), not its
        unrounded mean_wins (e.g. 6.7) -- substituting mean_wins here would
        silently change what the live 2026 draft room displays.

        Patched at get_draft_snapshot_predictions, not get_preseason_predictions:
        the draft room reads the frozen snapshot (frozen=True), not the live
        model, via the shared adapter (get_season_projection_blended) that the
        draft room, player profile and draft recap all now go through.
        """
        mock_get_draft_snapshot_predictions.return_value = {
            "ARI": {
                "projected_wins": 7.0,
                "mean_wins": 6.7,
                "std_dev": 2.6,
                "sources": {"model": "nn_xgb_lr_ensemble"},
            }
        }
        mock_get_consensus_projections.return_value = {}

        state = load_draft_state(set(), year=2023)

        assert state["preseason_predictions"]["ARI"]["projected_wins"] == 7.0

    @patch("services.data_service.get_consensus_projections")
    @patch("services.data_service.get_draft_snapshot_predictions")
    def test_preseason_predictions_rounds_consensus_std_dev(
        self, mock_get_draft_snapshot_predictions, mock_get_consensus_projections
    ):
        """Consensus-sourced std_dev (consensus_std) is `float(np.std(vals))` from
        consensus_service.compute_derived -- unrounded, e.g. 1.1367210272875008.
        static/js/ui_renderer.js renders std_dev straight into the draft room with no
        .toFixed(), so a team with only a consensus row (no model) must still have
        it rounded to 2 decimals, or historical (2017-2025) draft rooms show long
        float tails like "8.5W ±1.1367210272875008" for undrafted teams.
        """
        mock_get_draft_snapshot_predictions.return_value = {}
        mock_get_consensus_projections.return_value = {
            "BUF": {
                "consensus_mean": 10.4,
                "consensus_median": 10.5,
                "consensus_std": 1.1367210272875008,
                "sources": {"br": 10.0, "vegas_ou": 10.8},
            }
        }

        state = load_draft_state(set(), year=2023)

        assert state["preseason_predictions"]["BUF"]["std_dev"] == 1.14
```

`tests/test_historical_projection_routes.py`'s `deleted_preseason_rows`
fixture (lines 34-39) feeds two of the frozen call sites (draft recap,
player profile/history) and one live call site (admin). Add the snapshot
patch so the frozen paths also see an empty model side, matching what an
un-migrated historical season actually looks like in both collections:

```python
@pytest.fixture
def deleted_preseason_rows(monkeypatch):
    """preseason_predictions and draft_snapshot_predictions both empty for
    SEASON, consensus present -- prod today for a pre-migration historical
    season."""
    monkeypatch.setattr(data_service, "get_preseason_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_draft_snapshot_predictions", lambda s: {})
    monkeypatch.setattr(data_service, "get_consensus_projections",
                        lambda s: CONSENSUS if int(s) == SEASON else {})
```

- [ ] **Step 2: Run the updated tests to verify they fail against current code**

Run: `pytest tests/test_draft_service.py tests/test_historical_projection_routes.py -v`
Expected: The two `TestPreseasonPredictionsShape` tests FAIL (their mocked
`get_draft_snapshot_predictions` is never consulted yet, so
`state["preseason_predictions"]` still reflects the real
`get_preseason_predictions`/local data). The three
`test_historical_projection_routes.py` tests still PASS at this point
(patching an extra function that isn't called yet is harmless).

- [ ] **Step 3: Add `frozen=True` at each of the six call sites**

`services/draft_service.py` (lines 154 and 160):

```python
    preseason_predictions = get_season_projection_blended(int(season), frozen=True)
    # Admin-only, separate from preseason_predictions above: exposes model and
    # consensus numbers individually (instead of collapsed to one) for the
    # available-teams grid's per-team display and sort. preseason_predictions
    # keeps its merged shape for the draft board's "Base" tag and portfolio
    # totals, which don't need this distinction.
    projection_detail = get_season_projection_dual(int(season), frozen=True)
```

`routes/mock_draft_routes.py` (lines 111-112):

```python
    if is_admin:
        content["projections"] = get_season_projection_legacy_shape(season, frozen=True)
        content["projectionsDetail"] = get_season_projection_dual(season, frozen=True)
```

`services/mock_draft_service.py` (line 131, inside `bot_pick`):

```python
    projections = get_season_projection_legacy_shape(season, frozen=True)
```

`services/mock_draft_service.py` (line 158, inside `rank_rosters`):

```python
    projections = get_season_projection_legacy_shape(season, frozen=True)
```

`routes/draft_routes.py` (line 422):

```python
    # Blends model and consensus, not get_preseason_predictions alone: this
    # page is mostly historical seasons, whose projections live in
    # consensus_projections now, and a recap benefits from both reads where
    # both exist rather than only ever showing one. frozen=True: once a draft
    # has been recapped, the projection it's compared against must not keep
    # moving underneath it.
    from services.data_service import get_season_projection_blended
    preds = get_season_projection_blended(year, frozen=True)
```

`routes/history_routes.py` (line 205):

```python
    # Resolver, not get_preseason_predictions: these are historical seasons, whose
    # projections live in consensus_projections now. frozen=True: a player's
    # past-season draft value must not keep moving as the model is retrained.
    preseason_preds = {int(s): get_season_projection_legacy_shape(int(s), frozen=True) for s in player_seasons}
```

- [ ] **Step 4: Run the full affected test files to verify everything passes**

Run: `pytest tests/test_draft_service.py tests/test_historical_projection_routes.py tests/test_mock_draft.py tests/test_mock_draft_service.py -v`
Expected: PASS. (`test_mock_draft.py` and `test_mock_draft_service.py` patch
`get_season_projection_legacy_shape`/`_dual` directly with `return_value=...`,
which ignores call arguments, so adding `frozen=True` to the call sites does
not affect them -- confirm this by reading their `patch(...)` calls if any
fail unexpectedly.)

- [ ] **Step 5: Run the full unit suite**

Run: `pytest tests/ -n auto`
Expected: 0 failures

- [ ] **Step 6: Commit**

```bash
git add services/draft_service.py routes/mock_draft_routes.py \
        services/mock_draft_service.py routes/draft_routes.py \
        routes/history_routes.py tests/test_draft_service.py \
        tests/test_historical_projection_routes.py
git commit -m "feat: route the six frozen readers to draft_snapshot_predictions"
```

---

## Task 9: Admin endpoints for manual snapshot sync + status

**Files:**
- Modify: `routes/admin_routes.py` (add two routes; add `sync_draft_snapshot_for_season` and `get_collection_df` — already imported at line 27 — to the existing import blocks)
- Test: `tests/test_admin_routes.py` (new test class, following the file's own "happy path / auth guard" pattern documented at the top of that file)

**Interfaces:**
- Consumes: `services.db_service.sync_draft_snapshot_for_season(season)` (Task 1), `services.db_service.get_collection_df` (already imported in `admin_routes.py`), `routes.models.SeasonRequest` (already defined at `routes/models.py:72-73`, `{season: int}`).
- Produces: `POST /api/admin/draft_snapshot/sync` (body: `SeasonRequest`) and `GET /api/admin/draft_snapshot/{season}`, both admin-only via the existing `require_admin` dependency.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_admin_routes.py`:

```python
# ── /api/admin/draft_snapshot ─────────────────────────────────────────────────

class TestDraftSnapshotSync:
    def test_happy_path_calls_sync_and_returns_result(self, admin_token):
        with patch("routes.admin_routes.sync_draft_snapshot_for_season") as mock_sync:
            mock_sync.return_value = {"season": 2026, "written": 32, "locked": False}
            resp = client.post(
                "/api/admin/draft_snapshot/sync",
                json={"season": 2026},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        assert resp.json() == {"season": 2026, "written": 32, "locked": False}
        mock_sync.assert_called_once_with(2026)

    def test_requires_token(self):
        resp = client.post("/api/admin/draft_snapshot/sync", json={"season": 2026})
        assert resp.status_code in (401, 403)

    def test_requires_admin_role(self, auth_token):
        resp = client.post(
            "/api/admin/draft_snapshot/sync",
            json={"season": 2026},
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_unhandled_exception_returns_server_error(self, admin_token):
        with patch("routes.admin_routes.sync_draft_snapshot_for_season", side_effect=RuntimeError("boom")):
            resp = client.post(
                "/api/admin/draft_snapshot/sync",
                json={"season": 2026},
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 500


class TestDraftSnapshotStatus:
    def test_happy_path_reports_locked_when_every_team_locked(self, admin_token):
        import pandas as pd
        df = pd.DataFrame([
            {"season": 2026, "team": "KC", "locked": True, "generated_at": 200.0},
            {"season": 2026, "team": "SF", "locked": True, "generated_at": 210.0},
        ])
        with patch("routes.admin_routes.get_collection_df", return_value=df):
            resp = client.get(
                "/api/admin/draft_snapshot/2026",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2026
        assert body["team_count"] == 2
        assert body["locked"] is True
        assert body["generated_at"] == 210.0

    def test_reports_unlocked_when_any_team_unlocked(self, admin_token):
        import pandas as pd
        df = pd.DataFrame([
            {"season": 2026, "team": "KC", "locked": True, "generated_at": 200.0},
            {"season": 2026, "team": "SF", "locked": False, "generated_at": 210.0},
        ])
        with patch("routes.admin_routes.get_collection_df", return_value=df):
            resp = client.get(
                "/api/admin/draft_snapshot/2026",
                headers={"Authorization": admin_token},
            )
        assert resp.json()["locked"] is False

    def test_no_snapshot_yet_reports_zero_teams(self, admin_token):
        import pandas as pd
        with patch("routes.admin_routes.get_collection_df", return_value=pd.DataFrame()):
            resp = client.get(
                "/api/admin/draft_snapshot/2026",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["team_count"] == 0
        assert body["locked"] is False
        assert body["generated_at"] is None

    def test_requires_token(self):
        resp = client.get("/api/admin/draft_snapshot/2026")
        assert resp.status_code in (401, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_admin_routes.py -k DraftSnapshot -v`
Expected: FAIL with 404 (routes don't exist yet) on every test.

- [ ] **Step 3: Add the imports and the two routes**

In `routes/admin_routes.py`, change the `services.data_service` import block (lines 22-25) to also pull in the new function, and keep `get_collection_df` (already present at line 27's `db_service` import — no change needed there):

```python
from services.data_service import (
    load_data, get_active_season, get_preseason_predictions,
    get_consensus_projections,
)
```

stays unchanged (`sync_draft_snapshot_for_season` lives in `db_service`, not
`data_service`) — instead add it to the existing `db_service` import block
(lines 27-30):

```python
from services.db_service import (
    add_draft_order, add_draft_rule, add_player, delete_draft_results_for_season,
    delete_season_data, get_collection_df, get_metadata, get_password_hash, save_weekly_recap,
    set_member_paid, sync_draft_snapshot_for_season, update_player_credentials, update_player_profile,
)
```

Then add the two routes near the other `SeasonRequest`-bodied routes (after
`reset_draft`, i.e. after line 325):

```python
@router.post("/admin/draft_snapshot/sync")
async def sync_draft_snapshot(body: SeasonRequest, _: dict = Depends(require_admin)):
    """Admin: manually copy this season's preseason_predictions into
    draft_snapshot_predictions right now, instead of waiting for the next
    scripts/refresh_preseason.py run. Locks automatically if draft_results
    already has rows for this season -- identical rule to the scheduled path,
    since both call services.db_service.sync_draft_snapshot_for_season()."""
    try:
        result = sync_draft_snapshot_for_season(body.season)
        return JSONResponse(content=result)
    except Exception:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()


@router.get("/admin/draft_snapshot/{season}")
async def get_draft_snapshot_status(
    season: Annotated[int, FPath(ge=2000, le=2030)],
    _: dict = Depends(require_admin),
):
    """Admin: current draft_snapshot_predictions status for a season --
    team count, whether every team is locked, and the most recent
    generated_at, so an admin can tell whether a sync is needed before
    triggering POST .../sync."""
    try:
        df = get_collection_df("draft_snapshot_predictions", filters=[("season", "==", season)])
        if df.empty:
            return JSONResponse(content={"season": season, "team_count": 0,
                                          "locked": False, "generated_at": None})
        return JSONResponse(content={
            "season": season,
            "team_count": int(len(df)),
            "locked": bool(df["locked"].all()) if "locked" in df.columns else False,
            "generated_at": float(df["generated_at"].max()) if "generated_at" in df.columns else None,
        })
    except Exception:
        logger.exception("Unhandled error in admin endpoint")
        return server_error()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_admin_routes.py -k DraftSnapshot -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full admin route suite to check for regressions**

Run: `pytest tests/test_admin_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add routes/admin_routes.py tests/test_admin_routes.py
git commit -m "feat: add admin endpoints for manual draft snapshot sync and status"
```

---

## Task 10: Document the new collection in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` (Firestore collection table in "Data Flow & Caching")

**Interfaces:** None (documentation only).

- [ ] **Step 1: Add the table row**

In `CLAUDE.md`, find the Firestore collection table (the one with rows for
`nfl_games`, `preseason_predictions`, `consensus_projections`, etc.) and add,
directly after the `preseason_predictions` row:

```markdown
| `draft_snapshot_predictions` | `.local_db/draft_snapshot_predictions.pkl` + `_{year}.pkl` | Frozen, pre-draft copy of `preseason_predictions` for the six "fairness" readers (real draft room, mock draft setup/bot AI/grading, draft recap, draft results/history); written via `services/db_service.py::sync_draft_snapshot_for_season()`, called by `scripts/write_draft_snapshot.py` (a `scripts/refresh_preseason.py` step) and by `POST /api/admin/draft_snapshot/sync` (manual trigger), and locked once `draft_results` shows the season's draft has started. See `docs/superpowers/specs/2026-09-15-preseason-draft-snapshot-design.md`. |
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document draft_snapshot_predictions collection"
```

---

## Self-Review Notes

**Spec coverage:**
- New collection + schema (`locked` bool, same fields as `preseason_predictions`) → Tasks 1, 2.
- Local pkl mirror wired into `refresh_local_pkls.py` → Task 4.
- Write path in `refresh_preseason.py`: copy after `predict_season.py`, skip when locked → Tasks 1, 5, 6.
- Lock condition via `draft_results` rows, not `draft_active` → Task 1 (`sync_draft_snapshot_for_season()`'s `draft_started` check).
- Six readers switched, live readers (admin forecast/consensus/weekly recap) untouched → Task 8, Global Constraints.
- Historical backfill of already-locked seasons → Task 7.
- Edge case (re-run after draft started is a no-op) → covered by `set_draft_snapshot_predictions`'s per-team lock skip (Task 1's `test_set_draft_snapshot_predictions_already_fully_locked_writes_nothing`), exercised identically whether triggered by `write_draft_snapshot.py` (Task 5) or the admin endpoint (Task 9).
- Admin-facing snapshot creation/locking control (explicitly requested beyond the spec's script-only design) → Task 9: `POST /api/admin/draft_snapshot/sync` triggers the same `sync_draft_snapshot_for_season()` the scheduled path uses, and `GET /api/admin/draft_snapshot/{season}` reports current lock/team-count status.
- Out-of-scope items (no weekly snapshots, not a general versioning system, not touching `cache_builder.py`'s daily write) — nothing in this plan touches those areas.

**Placeholder scan:** No TBD/TODO markers; every step has runnable code and an exact expected test outcome.

**Type consistency:** `frozen: bool = False` keyword name and default are identical across all four resolver signatures (Task 3) and every one of the six call sites (Task 8). `set_draft_snapshot_predictions()`'s signature exactly mirrors `set_preseason_predictions()`'s parameter names and order (Task 1). The copy-and-lock rule itself (read `preseason_predictions`, check `draft_results`, call `set_draft_snapshot_predictions`) is implemented exactly once, as `sync_draft_snapshot_for_season()` in `services/db_service.py` (Task 1) — `scripts/write_draft_snapshot.py` (Task 5) and the admin endpoint (Task 9) both call it directly rather than each re-implementing the rule, so there is no risk of the two paths' lock behavior drifting apart.
