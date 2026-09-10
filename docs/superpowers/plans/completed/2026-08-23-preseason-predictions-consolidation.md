# Preseason Predictions Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/cache_builder.py`'s daily automated job the single source of truth for the `preseason_predictions` Firestore collection (currently written only by manually running `scripts/predict_season.py`), so the mock draft's bot AI, the real draft room, and the admin forecast page all get automatically-refreshed team win projections instead of relying on a human remembering to run a script.

**Architecture:** Add `NNProjectionEngine.get_team_win_projections()`, a full-stats sibling of the existing `get_team_projected_wins()` that preserves mean/std_dev/percentiles instead of collapsing to median only. Add `services/db_service.py::set_preseason_predictions()`, a locking-aware Firestore writer mirroring the existing `set_consensus_projections()` pattern. Wire both into `cache_builder.py`'s `build_year()`, reusing the `final_flag` it already computes to decide locking, and threading a `model_version` string down from `main()`.

**Tech Stack:** Python (existing stack), pandas, Firestore (`firebase_admin`).

**Spec:** `docs/superpowers/specs/2026-08-23-preseason-predictions-consolidation-design.md`

## Global Constraints

- Zero changes to any reader of `preseason_predictions` (`services/data_service.py`'s `get_preseason_predictions()` / `get_season_projection()` / `get_season_projection_legacy_shape()` / `get_season_projection_dual()`, or any route/service calling them). This is a write-side-only change.
- `scripts/predict_season.py` is unchanged — it stays the manual-override path, unconditional overwrite is now its intentional purpose.
- Field names written must exactly match what `get_preseason_predictions()` (`services/data_service.py:364-384`) already reads and what `predict_season.py:224-238` already writes: `projected_wins` (=median), `mean_wins`, `std_dev`, `floor` (=p5), `p25`, `p75`, `ceiling` (=p95).
- A team's existing Firestore doc with `locked=True` must never be overwritten unless `force=True` — this is what protects a completed season's preseason call from being silently rewritten by a later model retrain.
- Any new write path to Firestore must call `services.db_service.signal_data_update()` on success, matching `set_consensus_projections()`'s existing convention (the deployed app's in-memory cache has a 1-hour TTL; skipping this means fresh data doesn't appear on the live site for up to an hour). The spec's pseudocode omitted this — confirmed against `services/db_service.py:119-130` (`signal_data_update()`) and `tests/test_consensus_storage.py:74-78`'s explicit regression-guard comment for the sibling function; this plan corrects that omission.
- `scripts/refresh_local_pkls.py` already registers `("preseason_predictions", "season")` (`scripts/refresh_local_pkls.py:44`) — no changes needed there.

---

### Task 1: `get_team_win_projections()` + `set_preseason_predictions()`

**Files:**
- Modify: `services/nn_projection_engine.py` (add `get_team_win_projections()` near the existing `get_team_projected_wins()` at line 887)
- Modify: `services/db_service.py` (add `set_preseason_predictions()` near the existing `set_consensus_projections()` at line 559)
- Test: `tests/test_simulate_season.py` (new test class for the engine method)
- Test: `tests/test_consensus_storage.py` (new test functions for the writer — same file the sibling `set_consensus_projections()` tests live in, despite the filename; that's the established location for this class of storage round-trip test)

**Interfaces:**
- Produces: `NNProjectionEngine.get_team_win_projections(self, schedule_df: pd.DataFrame, n_sims: int = 5000) -> Dict[str, dict]` — `{team: {projected_wins, mean_wins, std_dev, floor, p25, p75, ceiling}}`. Empty `schedule_df` returns `{}` without calling `simulate_season()`.
- Produces: `services.db_service.set_preseason_predictions(season: int, projections: Dict[str, dict], model_version: str, locked: bool, force: bool = False) -> int` — returns count of docs actually written (skipped-due-to-lock docs don't count).
- Consumes: `self.simulate_season(schedule_df, n_sims=n_sims)` (existing method, already used by `get_team_projected_wins()` at `services/nn_projection_engine.py:903`) and `services.db_service.get_db()` / `signal_data_update()` (existing).

- [ ] **Step 1: Write the failing tests for `get_team_win_projections()`**

Add to `tests/test_simulate_season.py`, after the existing `TestPublicLookupRosterValue` class (or any existing class — placement doesn't matter, this is a new independent class):

```python
class TestGetTeamWinProjections:
    def test_maps_full_stats_from_simulate_season(self, mock_engine):
        mock_engine.simulate_season = lambda *a, **k: {
            "team_stats": {
                "KC": {"median_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                       "p5": 7.0, "p25": 9.0, "p75": 12.0, "p95": 14.0},
                "TEN": {"median_wins": 5.0, "mean_wins": 5.3, "std_dev": 2.1,
                        "p5": 2.0, "p25": 4.0, "p75": 7.0, "p95": 9.0},
            }
        }
        import pandas as pd
        schedule = pd.DataFrame([{"home_team": "KC", "away_team": "TEN", "week": 1}])
        result = mock_engine.get_team_win_projections(schedule)

        assert result["KC"] == {
            "projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
            "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0,
        }
        assert result["TEN"] == {
            "projected_wins": 5.0, "mean_wins": 5.3, "std_dev": 2.1,
            "floor": 2.0, "p25": 4.0, "p75": 7.0, "ceiling": 9.0,
        }

    def test_empty_schedule_returns_empty_dict_without_simulating(self, mock_engine):
        import pandas as pd
        called = []
        mock_engine.simulate_season = lambda *a, **k: called.append(1) or {}
        result = mock_engine.get_team_win_projections(pd.DataFrame())
        assert result == {}
        assert called == []

    def test_passes_through_n_sims(self, mock_engine):
        import pandas as pd
        captured = {}
        def fake_simulate(schedule_df, n_sims=5000):
            captured["n_sims"] = n_sims
            return {"team_stats": {}}
        mock_engine.simulate_season = fake_simulate
        schedule = pd.DataFrame([{"home_team": "KC", "away_team": "TEN", "week": 1}])
        mock_engine.get_team_win_projections(schedule, n_sims=1234)
        assert captured["n_sims"] == 1234
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_simulate_season.py::TestGetTeamWinProjections -v`
Expected: FAIL with `AttributeError: 'NNProjectionEngine' object has no attribute 'get_team_win_projections'`

- [ ] **Step 3: Implement `get_team_win_projections()`**

In `services/nn_projection_engine.py`, add this method immediately after `get_team_projected_wins()` (which currently ends at line 907):

```python
    def get_team_win_projections(self, schedule_df: pd.DataFrame, n_sims: int = 5000) -> Dict[str, dict]:
        """Full-stats sibling of get_team_projected_wins() -- same simulate_season()
        call, but preserves mean/std_dev/percentiles instead of collapsing to
        median only. Used to populate preseason_predictions
        (services/db_service.py::set_preseason_predictions()) from the daily
        automated job instead of the manual scripts/predict_season.py path.

        Returns {team: {projected_wins, mean_wins, std_dev, floor, p25, p75,
        ceiling}}, field names matching scripts/predict_season.py's existing
        mapping exactly (projected_wins=median, floor=p5, ceiling=p95).
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_simulate_season.py::TestGetTeamWinProjections -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing tests for `set_preseason_predictions()`**

Add to `tests/test_consensus_storage.py`, at the end of the file (after the existing `test_refresh_local_pkls_registers_consensus_collection`):

```python
def test_set_preseason_predictions_writes_full_stats(monkeypatch):
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
    monkeypatch.setattr(db_service, "signal_data_update", lambda: signaled.append(True))

    count = db_service.set_preseason_predictions(
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
    assert signaled == [True]


def test_set_preseason_predictions_skips_locked_team_without_force(monkeypatch):
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
    monkeypatch.setattr(db_service, "signal_data_update", lambda: None)

    count = db_service.set_preseason_predictions(
        2026,
        {
            "KC":  {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                    "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
            "TEN": {"projected_wins": 5.0, "mean_wins": 5.3, "std_dev": 2.1,
                    "floor": 2.0, "p25": 4.0, "p75": 7.0, "ceiling": 9.0},
        },
        model_version="nn_v15+xgb_v9+lr_v7", locked=True,
    )

    # KC is locked and force=False (default) -- skipped. TEN has no existing
    # doc -- written.
    assert count == 1
    assert "2026_KC" not in written
    assert written["2026_TEN"]["team"] == "TEN"


def test_set_preseason_predictions_force_overwrites_locked(monkeypatch):
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
    monkeypatch.setattr(db_service, "signal_data_update", lambda: None)

    count = db_service.set_preseason_predictions(
        2026,
        {"KC": {"projected_wins": 12.0, "mean_wins": 11.5, "std_dev": 1.8,
                "floor": 8.0, "p25": 10.0, "p75": 13.0, "ceiling": 15.0}},
        model_version="nn_v15+xgb_v9+lr_v7", locked=True, force=True,
    )

    assert count == 1
    assert written["2026_KC"]["projected_wins"] == 12.0


def test_set_preseason_predictions_no_db_returns_zero(monkeypatch):
    import services.db_service as db_service
    monkeypatch.setattr(db_service, "get_db", lambda: None)

    count = db_service.set_preseason_predictions(
        2026, {"KC": {"projected_wins": 11.0}},
        model_version="nn_v15+xgb_v9+lr_v7", locked=False,
    )

    assert count == 0
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `pytest tests/test_consensus_storage.py -v -k set_preseason_predictions`
Expected: FAIL with `AttributeError: module 'services.db_service' has no attribute 'set_preseason_predictions'`

- [ ] **Step 7: Implement `set_preseason_predictions()`**

In `services/db_service.py`, add this function immediately after `set_consensus_projections()`:

```python
def set_preseason_predictions(season: int, projections: dict, model_version: str,
                               locked: bool, force: bool = False) -> int:
    """Write preseason_predictions docs for a season, respecting per-team locks.

    A team's existing doc is skipped (not overwritten) when it's already
    locked=True and force=False -- this preserves "what we predicted before a
    completed season started" once that season is over, the same protection
    game_predictions' locked flag and analytics_cache's is_cache_final() gate
    already give every other prediction store in this app. locked=True is
    stamped on every doc this call DOES write, set to the `locked` param
    (callers pass whatever final_flag they've already computed for the
    season).

    projections: {team: {projected_wins, mean_wins, std_dev, floor, p25,
    p75, ceiling}} -- see NNProjectionEngine.get_team_win_projections().

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

    batch = db.batch()
    written = 0
    for team, stats in projections.items():
        if team in existing_locked:
            continue
        ref = db.collection("preseason_predictions").document(f"{season}_{team}")
        batch.set(ref, {
            "season": season,
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
        signal_data_update()
    return written
```

Note: `time` and `logger` are already imported/defined at module level in
`services/db_service.py` (used by `signal_data_update()` and other
functions in this file) -- no new imports needed.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_consensus_storage.py -v`
Expected: PASS (all tests in the file, including the 4 new ones)

- [ ] **Step 9: Run the full affected-file suite and commit**

Run: `pytest tests/test_simulate_season.py tests/test_consensus_storage.py -v`
Expected: PASS, all tests, pristine output.

```bash
git add services/nn_projection_engine.py services/db_service.py tests/test_simulate_season.py tests/test_consensus_storage.py
git commit -m "$(cat <<'EOF'
feat: add get_team_win_projections() and set_preseason_predictions()

Full-stats sibling of NNProjectionEngine.get_team_projected_wins() that
preserves mean_wins/std_dev/percentiles instead of collapsing to median
only, plus a locking-aware Firestore writer for preseason_predictions
mirroring the existing set_consensus_projections() pattern. Not yet
wired into cache_builder.py -- that's the next task.
EOF
)"
```

---

### Task 2: Wire into `cache_builder.py`

**Files:**
- Modify: `scripts/cache_builder.py` (import, `main()`'s model-loading block, `build_year()`'s signature and `prediction_snapshot` block, the `build_year()` call site)
- Test: `tests/test_cache_builder.py`

**Interfaces:**
- Consumes: `NNProjectionEngine.get_team_win_projections()` and `services.db_service.set_preseason_predictions()` (both from Task 1, already committed).
- Produces: `build_year(..., model_version: str = None)` — new optional parameter, `None` when model loading failed (existing `except` branch at `scripts/cache_builder.py:570-572`) so callers can detect "skip preseason_predictions this run" the same way `pred_lookup = {}` already signals "skip game_predictions predictions this run".

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_builder.py`, after the existing `TestYearsToBuildWiring` class:

```python
class TestPreseasonPredictionsWiring:
    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_writes_unlocked_for_current_season(self, mock_engine_cls, mock_set):
        from scripts.cache_builder import build_year
        import pandas as pd

        fake_engine = MagicMock()
        fake_engine.get_team_win_projections.return_value = {
            "KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                   "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
        }
        mock_engine_cls.return_value = fake_engine

        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2026, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version="nn_v15+xgb_v9+lr_v7",
        )

        mock_set.assert_called_once()
        call_kwargs = mock_set.call_args.kwargs
        assert call_kwargs["locked"] is False
        assert call_kwargs["model_version"] == "nn_v15+xgb_v9+lr_v7"
        assert call_kwargs["force"] is False

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_writes_locked_for_past_season(self, mock_engine_cls, mock_set):
        from scripts.cache_builder import build_year
        import pandas as pd

        fake_engine = MagicMock()
        fake_engine.get_team_win_projections.return_value = {
            "KC": {"projected_wins": 11.0, "mean_wins": 10.8, "std_dev": 1.95,
                   "floor": 7.0, "p25": 9.0, "p75": 12.0, "ceiling": 14.0},
        }
        mock_engine_cls.return_value = fake_engine

        games = pd.DataFrame([
            {"season": 2024, "week": 18, "home_team": "KC", "away_team": "TEN",
             "result": 7.0, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2024, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version="nn_v15+xgb_v9+lr_v7",
        )

        mock_set.assert_called_once()
        assert mock_set.call_args.kwargs["locked"] is True

    @patch("scripts.cache_builder.set_preseason_predictions")
    @patch("scripts.cache_builder.NNProjectionEngine")
    def test_skips_write_when_model_version_none(self, mock_engine_cls, mock_set):
        """model_version=None signals model loading failed this run (mirrors
        pred_lookup={} for game_predictions) -- must not attempt the write."""
        from scripts.cache_builder import build_year
        import pandas as pd

        games = pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "KC", "away_team": "TEN",
             "result": None, "game_type": "REG"},
        ])
        build_year(
            standings=pd.DataFrame(), games=games, players=pd.DataFrame(),
            draft_order=pd.DataFrame(), draft_results=pd.DataFrame(),
            draft_order_rules=pd.DataFrame(), year=2026, current_year=2026,
            all_games=games, force=False, pred_lookup={},
            model_version=None,
        )

        mock_set.assert_not_called()

    @patch("scripts.cache_builder.load_data")
    @patch("scripts.cache_builder.get_available_years", return_value=[2024, 2025])
    @patch("scripts.cache_builder.NNPredictionService")
    @patch("scripts.cache_builder.XGBPredictionService")
    @patch("scripts.cache_builder.LRPredictionService")
    @patch("scripts.cache_builder.build_year")
    @patch("scripts.cache_builder._sync_rawdata")
    def test_main_threads_model_version_string(
        self, mock_sync, mock_build_year, mock_lr_cls, mock_xgb_cls, mock_nn_cls,
        mock_avail, mock_load, monkeypatch,
    ):
        from scripts.cache_builder import main
        import pandas as pd
        import sys

        mock_nn_cls.return_value = MagicMock(loaded_version="v15")
        mock_xgb_cls.return_value = MagicMock(loaded_version="v9")
        mock_lr_cls.return_value = MagicMock(loaded_version="v7")

        games = pd.DataFrame([{"season": 2025, "week": 1, "game_id": "a"}])
        mock_load.return_value = (
            pd.DataFrame(), pd.DataFrame(), games, pd.DataFrame(),
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
        )
        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--skip-sync"])
        main()

        for call in mock_build_year.call_args_list:
            assert call.kwargs.get("model_version") == "nn_v15+xgb_v9+lr_v7"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cache_builder.py -v -k PreseasonPredictionsWiring`
Expected: FAIL — `build_year()` doesn't accept a `model_version` keyword argument yet (`TypeError: build_year() got an unexpected keyword argument 'model_version'`).

- [ ] **Step 3: Add the import**

In `scripts/cache_builder.py`, add to the existing imports block (after line 44's `from services.data_service import ...`):

```python
from services.db_service import set_preseason_predictions
```

- [ ] **Step 4: Thread `model_version` through `main()`**

In `scripts/cache_builder.py`, replace this block (currently lines 559-572):

```python
    try:
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        min_ft = min(years_to_build)
        max_ft = max(years_to_build)
        print(f"[cache_builder] Building feature table ({min_ft}-{max_ft})...")
        ft = build_master_feature_table(min_season=min_ft, max_season=max_ft)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)
        print(f"[cache_builder] {len(pred_lookup)} game predictions pre-computed.")
    except Exception as e:
        print(f"[cache_builder] WARNING: ML models unavailable ({e}). Predictions will be skipped.")
        pred_lookup = {}
```

with:

```python
    try:
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        model_version = f"nn_{nn_svc.loaded_version}+xgb_{xgb_svc.loaded_version}+lr_{lr_svc.loaded_version}"

        min_ft = min(years_to_build)
        max_ft = max(years_to_build)
        print(f"[cache_builder] Building feature table ({min_ft}-{max_ft})...")
        ft = build_master_feature_table(min_season=min_ft, max_season=max_ft)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)
        print(f"[cache_builder] {len(pred_lookup)} game predictions pre-computed.")
    except Exception as e:
        print(f"[cache_builder] WARNING: ML models unavailable ({e}). Predictions will be skipped.")
        pred_lookup = {}
        model_version = None
```

- [ ] **Step 5: Pass `model_version` at the `build_year()` call site**

In `scripts/cache_builder.py`, replace (currently around line 578-580):

```python
            build_year(yr_standings, yr_games, players, draft_order, draft_results,
                       draft_order_rules, year, current_year, all_games=games,
                       force=args.force, pred_lookup=pred_lookup)
```

with:

```python
            build_year(yr_standings, yr_games, players, draft_order, draft_results,
                       draft_order_rules, year, current_year, all_games=games,
                       force=args.force, pred_lookup=pred_lookup,
                       model_version=model_version)
```

- [ ] **Step 6: Add the `model_version` parameter and the new write block in `build_year()`**

In `scripts/cache_builder.py`, change `build_year()`'s signature (currently lines 259-261):

```python
def build_year(standings, games, players, draft_order, draft_results,
               draft_order_rules, year: int, current_year: int,
               all_games=None, force: bool = False, pred_lookup: dict = None):
```

to:

```python
def build_year(standings, games, players, draft_order, draft_results,
               draft_order_rules, year: int, current_year: int,
               all_games=None, force: bool = False, pred_lookup: dict = None,
               model_version: str = None):
```

Then, inside `build_year()`, immediately after the existing `prediction_snapshot` block's `try/except` (which currently ends at line 430 with `print(f"  [err]  {analytic}: {e}")`), add a new block:

```python
    # --- Preseason predictions (full-season win totals for the draft) ---
    # model_version is None when this run's model loading failed (see main()'s
    # except branch) -- mirrors pred_lookup={} silently skipping game_predictions
    # for the same reason, rather than writing under an unknown model version.
    if model_version:
        try:
            engine = _get_engine()
            yr_games = full_games[full_games['season'] == year].copy() if not full_games.empty else pd.DataFrame()
            if not yr_games.empty:
                yr_games = yr_games.drop_duplicates(subset=['week', 'home_team', 'away_team'])
            full_projections = engine.get_team_win_projections(yr_games, n_sims=5000)
            if full_projections:
                n = set_preseason_predictions(
                    year, full_projections, model_version=model_version,
                    locked=final_flag, force=force,
                )
                print(f"  [ok]   preseason_predictions year={year} ({n} teams written)")
        except Exception as e:
            print(f"  [err]  preseason_predictions: {e}")
```

This reuses `_get_engine()` (the same cached-per-year engine instance already
built for `schedule_enriched`/`prediction_snapshot`, so this doesn't
re-initialize a second engine) and `final_flag` (already computed at the top
of `build_year()`, line 294) -- no new state, just a new consumer of what's
already there.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS, all tests including the 4 new ones, pristine output.

- [ ] **Step 8: Run the broader regression check**

Run: `pytest tests/test_simulate_season.py tests/test_consensus_storage.py tests/test_cache_builder.py tests/test_nn_projection_engine.py -v`
Expected: PASS, no regressions.

- [ ] **Step 9: Manual verification against real data**

This step is manual verification -- not a pytest step, but required before
considering this done, since the whole point is fixing what the mock draft
actually displays.

```bash
python scripts/cache_builder.py --year 2026 --skip-sync
```

Expected: log includes a line like
`  [ok]   preseason_predictions year=2026 (32 teams written)`.

Then refresh local dev cache and spot-check:

```bash
python scripts/refresh_local_pkls.py
python -c "
import pickle
with open('.local_db/preseason_predictions_2026.pkl', 'rb') as f:
    import pandas as pd
    df = pickle.load(f)
print(df[['team', 'projected_wins', 'model_version', 'generated_at']].head(10))
"
```

Expected: `model_version` shows the current models (not `nn_v14+xgb_v8+lr_v6`),
`generated_at` is a fresh timestamp, `projected_wins` values look like
plausible NFL win totals (roughly 3-14 range).

- [ ] **Step 10: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "$(cat <<'EOF'
feat: write preseason_predictions from the daily automated job

Wires get_team_win_projections()/set_preseason_predictions() (previous
commit) into cache_builder.py's build_year(), reusing the already-cached
per-year NNProjectionEngine and the already-computed final_flag for
locking. This was the missing piece: predict_season.py was the only
writer of preseason_predictions -- the collection the mock draft's bot
AI, the real draft room, and the admin forecast page all read -- and
nothing scheduled it, so it silently went stale relative to every model
retrain. cache_builder.py already computed a lossy (median-only) version
of the same number into a dead analytic (analytics_cache's
prediction_snapshot, confirmed unread by any route/template) -- this
consolidates onto one automated, full-stats, locking-aware write instead
of two divergent paths.

predict_season.py is unchanged and remains available as a manual
override tool.
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** The spec's two new functions (`get_team_win_projections()`,
  `set_preseason_predictions()`), the locking design (reusing `final_flag`,
  skip-if-locked-unless-force), the `model_version` threading, and the
  try/except wrapping are all covered by Tasks 1-2. The spec's Testing
  section (unit tests for both new functions, integration test in
  `test_cache_builder.py` for past-vs-current-season locking, manual
  verification) maps directly to the steps above.
- **Correction over the spec's pseudocode:** the spec's
  `set_preseason_predictions()` sketch omitted the `signal_data_update()`
  call. Confirmed against `services/db_service.py:119-130` and the sibling
  `set_consensus_projections()`'s test file (`tests/test_consensus_storage.py:74-78`'s
  explicit regression-guard comment: skipping this means fresh data doesn't
  appear on the live site for up to an hour, since the deployed app's
  in-memory cache has a 1-hour TTL) that this is a real, established
  requirement for any new Firestore writer in this app, not an optional
  extra. Added to Task 1 Step 7 and to this plan's Global Constraints.
- **Explicitly out of scope, confirmed still out of scope:** no reader-side
  changes, `predict_season.py` unchanged, `prediction_snapshot` not deleted,
  the two independent `simulate_season()` calls per year (one for
  `game_predictions`, one for `preseason_predictions`) not deduplicated.
