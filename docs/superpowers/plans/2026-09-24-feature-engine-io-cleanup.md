# Feature Engine I/O Deduplication and Pipeline Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove redundant rawdata CSV reads inside one `build_master_feature_table()` run, replace two row-wise `apply` passes with one vectorized pass, share one subprocess-step helper in `cache_builder.py`, and close the listed test and observability gaps, with no change to model inputs or job behavior.

**Architecture:** A run-scoped cache inside `services/nn_feature_engine.py::_load_multi_season` (active only while `build_master_feature_table()` runs, cleared on exit, returns copies) removes duplicate reads without retaining memory afterwards. The QB-flag OR becomes one helper (`_apply_qb_availability`) using a keyed lookup instead of `.apply(axis=1)`. `scripts/cache_builder.py` gains `_run_subprocess_step` used by both subprocess call sites. Test-only and logging changes fill the coverage gaps.

**Tech Stack:** Python, pandas, numpy, pytest.

**Spec:** `docs/superpowers/specs/2026-09-17-qb-availability-followup-cleanup.md` (Performance/duplicated I/O, Test coverage gaps, Observability sections)

## Global Constraints

- No emojis in code, comments, docs, or commit messages.
- Zero-deletion policy: no existing test, function, or feature is removed. The one allowed removal is the dead local variable `tuesday` in `test_runs_backfill_on_tuesday` (Task 4), which the spec calls out.
- No behavior change for model inputs: the feature table produced by `build_master_feature_table()` must be value-identical before and after (Task 1 and Task 2 prove this with tests, not by inspection).
- `_load_multi_season(pattern, rawdata_dir)` keeps its signature and return contract: an empty `DataFrame()` when no file matches, a fresh concatenated frame otherwise. Callers mutate the returned frame in place today, so every call must return an independent frame.
- `_sync_rawdata()` and `_run_weekly_backfill_if_tuesday()` keep their exact current semantics: `_sync_rawdata` lets a `subprocess.TimeoutExpired` propagate (a hung sync must still fail the job so Cloud Run retries it) while a non-zero exit is only a warning; the weekly backfill swallows every failure. Printed lines keep their current wording.
- Do not touch anything under `static/`, `templates/`, `routes/`, or the model files.
- Tests run with `python -m pytest <files> -q -p no:cacheprovider` from the worktree root. The worktree has no `models/*.pkl` scalers and no Firebase credentials, so `tests/test_loaded_version.py` (2 tests) and the Firebase-schema tests (5 errors) fail in any worktree regardless of changes; `test_player_analytics` is intermittently flaky under `-n auto` on untouched `main`. Compare any other failure against the untouched base commit before calling it a regression.

## Findings that shape the plan

1. `_load_multi_season("injuries/injuries_*.csv")` is called by both `_load_injury_flags` and `_load_qb_report_status`; `snap_counts/snap_counts_*.csv` by `_load_qb_snap_shares` and `build_master_feature_table` itself; `rosters/roster_*.csv` by `_load_qb_snap_shares` and `build_master_feature_table`; `stats_team/stats_team_week_*.csv` by three other loaders. All are read under one `build_master_feature_table()` call. The per-season loader `_load_profile_shared_inputs` (already deduplicated per season in an earlier fix) reads different, per-year files and is out of scope.
2. Callers mutate what `_load_multi_season` returns (`df["team"] = df["team"].apply(...)` in `_load_qb_report_status`, column filters and assignments elsewhere), so a cache must hand out copies.
3. A process-lifetime cache would retain hundreds of MB of snap counts after the build; a run-scoped cache does not.
4. `_sync_rawdata` has no `except` around `subprocess.run`, so a timeout there raises today. The spec's "shared helper" must preserve that asymmetry, hence the `swallow_errors` flag.
5. Tests run in a worktree without model files; nothing in this plan needs them.

## File Structure

- Modify `services/nn_feature_engine.py`: run-scoped cache, `_apply_qb_availability`, wrapper for `build_master_feature_table`, warning in `_load_qb_snap_shares`.
- Modify `scripts/cache_builder.py`: `_run_subprocess_step` and its two call sites.
- Modify tests: `tests/test_nn_feature_engine.py` (Tasks 1, 2, 5), `tests/test_cache_builder.py` (Tasks 3, 4), `tests/test_preseason_profiles.py` (Task 5).

---

### Task 1: Run-scoped file cache in `_load_multi_season`

**Files:** Modify `services/nn_feature_engine.py` (around line 175 and `build_master_feature_table` at ~2204); Test `tests/test_nn_feature_engine.py`

**Interfaces:**
- Produces: context manager `_multi_season_cache_scope()`; module state `_RUN_CACHE: dict | None`; `build_master_feature_table(...)` keeps its exact signature and now delegates to `_build_master_feature_table_impl(...)` (the previous body, unchanged) inside the scope.
- `_load_multi_season` behavior: with no active scope it behaves exactly as today. With an active scope it memoizes the concatenated frame by `(str(rawdata_dir), pattern)` and returns `cached.copy()` on every call (including the first).

- [x] **Step 1: Write the failing tests** (append to `tests/test_nn_feature_engine.py`; reuse its existing imports, add missing ones)

```python
class TestMultiSeasonRunCache:
    """_load_multi_season is read by sibling loaders that each parse the same
    injuries/rosters/snap_counts files; inside one build_master_feature_table()
    run those files must be parsed once, and outside a run nothing is retained."""

    def _write_injuries(self, tmp_path):
        d = tmp_path / "injuries"
        d.mkdir()
        # position/report_status are read by _load_injury_flags and
        # _load_qb_report_status; add any other column those loaders need if a
        # test below shows they raise on this fixture (do not weaken assertions).
        pd.DataFrame({"season": [2024], "week": [1], "team": ["KC"], "gsis_id": ["g1"],
                      "position": ["QB"], "report_status": ["Out"]}).to_csv(
            d / "injuries_2024.csv", index=False)
        pd.DataFrame({"season": [2025], "week": [1], "team": ["BUF"], "gsis_id": ["g2"],
                      "position": ["QB"], "report_status": ["Doubtful"]}).to_csv(
            d / "injuries_2025.csv", index=False)

    def test_scope_parses_each_file_once(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        calls = []
        real = nfe._read_csv_safe
        monkeypatch.setattr(nfe, "_read_csv_safe", lambda p, *a, **k: (calls.append(p), real(p, *a, **k))[1])

        with nfe._multi_season_cache_scope():
            a = nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
            b = nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)

        assert len(calls) == 2  # two files, each parsed exactly once
        pd.testing.assert_frame_equal(a, b)

    def test_without_a_scope_nothing_is_cached(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        calls = []
        real = nfe._read_csv_safe
        monkeypatch.setattr(nfe, "_read_csv_safe", lambda p, *a, **k: (calls.append(p), real(p, *a, **k))[1])

        nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
        nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)

        assert len(calls) == 4

    def test_returned_frames_are_independent_copies(self, tmp_path):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        with nfe._multi_season_cache_scope():
            first = nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
            first["team"] = "MUTATED"
            first["extra"] = 1
            second = nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)

        assert "extra" not in second.columns
        assert set(second["team"]) == {"KC", "BUF"}

    def test_cache_is_cleared_when_the_scope_exits(self, tmp_path):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        with nfe._multi_season_cache_scope():
            nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
            assert nfe._RUN_CACHE
        assert nfe._RUN_CACHE is None

    def test_cache_is_cleared_when_the_scope_body_raises(self, tmp_path):
        import services.nn_feature_engine as nfe
        with pytest.raises(RuntimeError):
            with nfe._multi_season_cache_scope():
                raise RuntimeError("boom")
        assert nfe._RUN_CACHE is None

    def test_nested_scope_reuses_the_outer_cache_and_does_not_clear_it(self, tmp_path):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        with nfe._multi_season_cache_scope():
            nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
            with nfe._multi_season_cache_scope():
                pass
            assert nfe._RUN_CACHE  # inner exit must not wipe the outer run's cache

    def test_different_directories_do_not_collide(self, tmp_path):
        import services.nn_feature_engine as nfe
        a, b = tmp_path / "a", tmp_path / "b"
        for root, team in ((a, "KC"), (b, "BUF")):
            (root / "injuries").mkdir(parents=True)
            pd.DataFrame({"season": [2024], "week": [1], "team": [team], "gsis_id": ["g"]}).to_csv(
                root / "injuries" / "injuries_2024.csv", index=False)
        with nfe._multi_season_cache_scope():
            ra = nfe._load_multi_season("injuries/injuries_*.csv", a)
            rb = nfe._load_multi_season("injuries/injuries_*.csv", b)
        assert list(ra["team"]) == ["KC"] and list(rb["team"]) == ["BUF"]

    def test_no_matching_files_still_returns_an_empty_frame(self, tmp_path):
        import services.nn_feature_engine as nfe
        with nfe._multi_season_cache_scope():
            out = nfe._load_multi_season("injuries/injuries_*.csv", tmp_path)
        assert out.empty

    def test_build_master_feature_table_runs_inside_a_scope(self, tmp_path, monkeypatch):
        """The public entry point wraps the previous body in the scope, so
        sibling loaders share one read per file for the whole build."""
        import services.nn_feature_engine as nfe
        seen = {}

        def fake_impl(*args, **kwargs):
            seen["active"] = nfe._RUN_CACHE is not None
            return pd.DataFrame({"ok": [1]})

        monkeypatch.setattr(nfe, "_build_master_feature_table_impl", fake_impl)
        out = nfe.build_master_feature_table(str(tmp_path), 2020, 2021)

        assert seen["active"] is True
        assert nfe._RUN_CACHE is None
        assert list(out["ok"]) == [1]

    def test_sibling_injury_loaders_share_one_read_per_file_in_a_scope(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as nfe
        self._write_injuries(tmp_path)
        calls = []
        real = nfe._read_csv_safe
        monkeypatch.setattr(nfe, "_read_csv_safe", lambda p, *a, **k: (calls.append(p), real(p, *a, **k))[1])

        with nfe._multi_season_cache_scope():
            nfe._load_injury_flags(tmp_path)
            nfe._load_qb_report_status(tmp_path)

        assert len(calls) == 2  # not 4
```

- [x] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_nn_feature_engine.py -q -p no:cacheprovider -k MultiSeasonRunCache`
Expected: FAIL (`_multi_season_cache_scope` / `_RUN_CACHE` do not exist).

- [x] **Step 3: Implement.** Replace `_load_multi_season` and add the scope (imports: `contextlib` at the top if missing):

```python
# Run-scoped cache for _load_multi_season. Sibling loaders inside one
# build_master_feature_table() call read the same injuries/rosters/snap_counts
# files; while a scope is active each (rawdata_dir, pattern) is parsed once.
# It is None outside a run, so nothing is retained afterwards (snap_counts
# alone is hundreds of MB). Single-threaded by design, like the pipeline.
_RUN_CACHE: Optional[dict] = None


@contextlib.contextmanager
def _multi_season_cache_scope():
    """Enable the per-run file cache for the duration of the block. Re-entrant:
    a nested scope reuses the outer cache and leaves clearing to the outermost."""
    global _RUN_CACHE
    if _RUN_CACHE is not None:
        yield
        return
    _RUN_CACHE = {}
    try:
        yield
    finally:
        _RUN_CACHE = None


def _load_multi_season(pattern: str, rawdata_dir: Path) -> pd.DataFrame:
    key = (str(rawdata_dir), pattern)
    if _RUN_CACHE is not None and key in _RUN_CACHE:
        return _RUN_CACHE[key].copy()
    files = sorted(glob.glob(str(rawdata_dir / pattern)))
    if not files:
        return pd.DataFrame()
    frames = [_read_csv_safe(f) for f in files]
    frames = [f for f in frames if not f.empty]
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if _RUN_CACHE is None:
        return result
    _RUN_CACHE[key] = result
    return result.copy()
```

  Then rename the existing `def build_master_feature_table(` to `def _build_master_feature_table_impl(` (body unchanged, docstring may stay) and add above it a public wrapper with the identical signature `(rawdata_dir: Optional[str] = None, min_season: int = 2006, max_season: int = 2025) -> pd.DataFrame` and the original docstring's first paragraph, whose body is `with _multi_season_cache_scope(): return _build_master_feature_table_impl(rawdata_dir, min_season, max_season)`. Confirm the signature by reading the current `def` first.

- [x] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_nn_feature_engine.py -q -p no:cacheprovider`
Expected: all pass, output free of new warnings.

- [x] **Step 5: Equivalence check on real code paths.** Run every test file that exercises the feature engine: `python -m pytest tests/test_nn_feature_engine.py tests/test_preseason_profiles.py tests/test_qb_availability.py tests/test_feature_audit_service.py -q -p no:cacheprovider` (use whichever of these exist; `git grep -l "build_master_feature_table\|_load_multi_season" -- tests` lists them). Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "perf: run-scoped file cache so sibling loaders parse each rawdata CSV once"
```

---

### Task 2: One vectorized pass for the QB-availability flag OR

**Files:** Modify `services/nn_feature_engine.py` (the block after `qb_avail = compute_qb_availability_flags(...)`, ~line 2404-2420 in the impl); Test `tests/test_nn_feature_engine.py`

**Interfaces:**
- Consumes: `qb_avail: dict[(int season, int week, str team), float]` from `compute_qb_availability_flags`.
- Produces: `_apply_qb_availability(sched: pd.DataFrame, qb_avail: dict) -> pd.DataFrame`. It sets `home_qb_injury_flag` and `away_qb_injury_flag` on `sched` to `max(existing_flag, qb_avail.get((season, week, team), 0.0))` and returns `sched`. Requires columns `season`, `week`, `home_team`, `away_team`, `home_qb_injury_flag`, `away_qb_injury_flag`.

- [x] **Step 1: Write the failing tests** (append to `tests/test_nn_feature_engine.py`)

```python
class TestApplyQbAvailability:
    def _sched(self):
        return pd.DataFrame({
            "season": [2024, 2024, 2025],
            "week": [1, 2, 1],
            "home_team": ["KC", "BUF", "KC"],
            "away_team": ["BUF", "KC", "DEN"],
            "home_qb_injury_flag": [0.0, 1.0, 0.0],
            "away_qb_injury_flag": [0.0, 0.0, 1.0],
        })

    def test_takes_the_max_of_existing_flag_and_availability(self):
        from services.nn_feature_engine import _apply_qb_availability
        qb_avail = {(2024, 1, "KC"): 1.0, (2024, 2, "KC"): 1.0, (2025, 1, "DEN"): 0.0}

        out = _apply_qb_availability(self._sched(), qb_avail)

        assert list(out["home_qb_injury_flag"]) == [1.0, 1.0, 0.0]  # KC wk1 raised, BUF wk2 stays 1.0
        assert list(out["away_qb_injury_flag"]) == [0.0, 1.0, 1.0]  # KC away wk2 raised, DEN keeps 1.0

    def test_missing_keys_default_to_zero_and_never_lower_a_flag(self):
        from services.nn_feature_engine import _apply_qb_availability
        out = _apply_qb_availability(self._sched(), {})
        assert list(out["home_qb_injury_flag"]) == [0.0, 1.0, 0.0]
        assert list(out["away_qb_injury_flag"]) == [0.0, 0.0, 1.0]

    def test_matches_the_previous_row_wise_formula_on_random_data(self):
        """Equivalence with the two .apply(axis=1) passes this replaced."""
        import numpy as np
        from services.nn_feature_engine import _apply_qb_availability
        rng = np.random.default_rng(7)
        teams = ["KC", "BUF", "DEN", "SF", "LA"]
        n = 300
        sched = pd.DataFrame({
            "season": rng.integers(2020, 2024, n),
            "week": rng.integers(1, 19, n),
            "home_team": rng.choice(teams, n),
            "away_team": rng.choice(teams, n),
            "home_qb_injury_flag": rng.integers(0, 2, n).astype(float),
            "away_qb_injury_flag": rng.integers(0, 2, n).astype(float),
        })
        qb_avail = {(int(s), int(w), t): float(rng.integers(0, 2))
                    for s in range(2020, 2024) for w in range(1, 19) for t in teams
                    if rng.random() < 0.3}

        expected = sched.copy()
        expected["home_qb_injury_flag"] = expected.apply(
            lambda r: max(r["home_qb_injury_flag"],
                          qb_avail.get((int(r["season"]), int(r["week"]), r["home_team"]), 0.0)), axis=1)
        expected["away_qb_injury_flag"] = expected.apply(
            lambda r: max(r["away_qb_injury_flag"],
                          qb_avail.get((int(r["season"]), int(r["week"]), r["away_team"]), 0.0)), axis=1)

        out = _apply_qb_availability(sched.copy(), qb_avail)

        pd.testing.assert_series_equal(out["home_qb_injury_flag"], expected["home_qb_injury_flag"])
        pd.testing.assert_series_equal(out["away_qb_injury_flag"], expected["away_qb_injury_flag"])

    def test_empty_schedule_is_a_noop(self):
        from services.nn_feature_engine import _apply_qb_availability
        empty = self._sched().iloc[0:0]
        out = _apply_qb_availability(empty.copy(), {(2024, 1, "KC"): 1.0})
        assert out.empty
```

- [x] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_nn_feature_engine.py -q -p no:cacheprovider -k ApplyQbAvailability`
Expected: FAIL (ImportError).

- [x] **Step 3: Implement.** Add above `_build_master_feature_table_impl`:

```python
def _apply_qb_availability(sched: pd.DataFrame, qb_avail: dict) -> pd.DataFrame:
    """OR the sticky-reference QB availability signal into the two QB injury
    flags: each flag becomes max(existing flag, availability for that team in
    that week), 0.0 when the (season, week, team) key is absent.

    One keyed lookup per side instead of a row-wise .apply(axis=1) pass per
    flag, which was the hot path of this function on a full multi-season build.
    """
    if sched.empty:
        return sched
    seasons = sched["season"].astype(int).to_numpy()
    weeks = sched["week"].astype(int).to_numpy()
    for side in ("home", "away"):
        avail = np.fromiter(
            (qb_avail.get((s, w, t), 0.0)
             for s, w, t in zip(seasons, weeks, sched[f"{side}_team"].to_numpy())),
            dtype=float, count=len(sched),
        )
        flag = f"{side}_qb_injury_flag"
        sched[flag] = np.maximum(sched[flag].to_numpy(dtype=float), avail)
    return sched
```

  and replace the two `sched["home_qb_injury_flag"] = sched.apply(...)` / `sched["away_qb_injury_flag"] = sched.apply(...)` statements in the impl (keeping the `qb_avail = compute_qb_availability_flags(...)` line above them) with `sched = _apply_qb_availability(sched, qb_avail)`. Keep the surrounding comment. `np` is already imported in the module.

- [x] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_nn_feature_engine.py tests/test_preseason_profiles.py -q -p no:cacheprovider`
Expected: all pass (plus any qb-availability test file found by `git grep -l "compute_qb_availability_flags" -- tests`).

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "perf: vectorize the QB availability flag OR into one keyed pass"
```

---

### Task 3: Shared `_run_subprocess_step` in `scripts/cache_builder.py`

**Files:** Modify `scripts/cache_builder.py` (`_sync_rawdata` ~432, `_run_weekly_backfill_if_tuesday` ~470); Test `tests/test_cache_builder.py`

**Interfaces:**
- Produces: `_run_subprocess_step(cmd: list, label: str, timeout: int, *, swallow_errors: bool = False, timeout_note: str = "") -> bool`.
  - Runs `subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(SCRIPTS_DIR.parent))`.
  - `script` = `pathlib.Path(cmd[1]).name` (used in warnings).
  - When `swallow_errors` is true: `subprocess.TimeoutExpired` prints `[warn] {script} timed out after {timeout}s (non-fatal){timeout_note}` and returns False; any other `Exception` prints `[warn] {script} could not be run (non-fatal): {e}` and returns False. When false, both propagate unchanged.
  - After a completed run: prints `[cache_builder] {label} summary:` then the last 20 stdout lines, each indented two spaces; if `returncode != 0` prints `[warn] {script} exited non-zero (non-fatal): {stderr[:500]}` (stderr stripped, `None`-safe). Returns `returncode == 0`.
- `_sync_rawdata()` becomes `_run_subprocess_step([sys.executable, str(SCRIPTS_DIR / "sync_nflverse_data.py")], "nflverse sync", 300)` (keep its long docstring). `_run_weekly_backfill_if_tuesday()` keeps its weekday gate and command construction and calls `_run_subprocess_step(cmd, "weekly backfill", 600, swallow_errors=True, timeout_note=" -- the daily build itself already completed")`; keep the "[cache_builder] Tuesday -- running weekly backfill lock-in..." print before it.

- [x] **Step 1: Write the failing tests** (append a new class to `tests/test_cache_builder.py`; it already imports `patch`, `MagicMock`, `pd`)

```python
class TestRunSubprocessStep:
    def _cb(self):
        import scripts.cache_builder as cb
        return cb

    def test_success_prints_a_labelled_tail_and_returns_true(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="\n".join(f"line{i}" for i in range(30)), stderr="")
            ok = cb._run_subprocess_step(["py", "/x/sync_nflverse_data.py"], "nflverse sync", 300)
        out = capsys.readouterr().out
        assert ok is True
        assert "[cache_builder] nflverse sync summary:" in out
        assert "  line29" in out and "line9\n" not in out  # only the last 20 lines
        assert "[warn]" not in out

    def test_passes_timeout_and_cwd_through(self):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cb._run_subprocess_step(["py", "s.py"], "x", 123)
        assert run.call_args.kwargs["timeout"] == 123
        assert run.call_args.kwargs["capture_output"] is True
        assert run.call_args.kwargs["cwd"] == str(cb.SCRIPTS_DIR.parent)

    def test_nonzero_exit_warns_with_script_name_and_returns_false(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="404 not found")
            ok = cb._run_subprocess_step(["py", "/x/sync_nflverse_data.py"], "nflverse sync", 300)
        out = capsys.readouterr().out
        assert ok is False
        assert "[warn] sync_nflverse_data.py exited non-zero (non-fatal): 404 not found" in out

    def test_none_stdout_and_stderr_are_tolerated(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=1, stdout=None, stderr=None)
            assert cb._run_subprocess_step(["py", "s.py"], "x", 5) is False

    def test_timeout_propagates_by_default(self):
        import subprocess as sp
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=300)):
            try:
                cb._run_subprocess_step(["py", "s.py"], "x", 300)
            except sp.TimeoutExpired:
                return
        raise AssertionError("TimeoutExpired must propagate when swallow_errors is False")

    def test_swallowed_timeout_warns_with_the_note_and_returns_false(self, capsys):
        import subprocess as sp
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=600)):
            ok = cb._run_subprocess_step(["py", "/x/backfill_schedule_predictions.py"], "weekly backfill", 600,
                                         swallow_errors=True, timeout_note=" -- daily build done")
        assert ok is False
        assert "[warn] backfill_schedule_predictions.py timed out after 600s (non-fatal) -- daily build done" in capsys.readouterr().out

    def test_swallowed_unexpected_error_warns_and_returns_false(self, capsys):
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=OSError("no interpreter")):
            ok = cb._run_subprocess_step(["py", "/x/backfill_schedule_predictions.py"], "weekly backfill", 600,
                                         swallow_errors=True)
        assert ok is False
        assert "could not be run (non-fatal): no interpreter" in capsys.readouterr().out

    def test_unexpected_error_propagates_by_default(self):
        cb = self._cb()
        with patch.object(cb.subprocess, "run", side_effect=OSError("boom")):
            try:
                cb._run_subprocess_step(["py", "s.py"], "x", 5)
            except OSError:
                return
        raise AssertionError("OSError must propagate when swallow_errors is False")


class TestSubprocessCallSitesUseTheSharedStep:
    def test_sync_rawdata_keeps_timeout_propagation(self):
        """A hung nflverse sync must still fail the job (Cloud Run retries it)."""
        import subprocess as sp
        import scripts.cache_builder as cb
        with patch.object(cb.subprocess, "run", side_effect=sp.TimeoutExpired(cmd="s", timeout=300)):
            try:
                cb._sync_rawdata()
            except sp.TimeoutExpired:
                return
        raise AssertionError("_sync_rawdata must let TimeoutExpired propagate")

    def test_sync_rawdata_uses_the_300s_timeout_and_label(self, capsys):
        import scripts.cache_builder as cb
        with patch.object(cb.subprocess, "run") as run:
            run.return_value = MagicMock(returncode=0, stdout="Sync complete", stderr="")
            cb._sync_rawdata()
        assert run.call_args.kwargs["timeout"] == 300
        assert "[cache_builder] nflverse sync summary:" in capsys.readouterr().out

    def test_weekly_backfill_labels_its_summary_and_uses_600s(self, capsys):
        from datetime import datetime, timezone
        import scripts.cache_builder as cb
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, patch.object(cb.subprocess, "run") as run:
            mock_dt.now.return_value = tuesday
            run.return_value = MagicMock(returncode=0, stdout="done", stderr="")
            cb._run_weekly_backfill_if_tuesday(2026)
        assert run.call_args.kwargs["timeout"] == 600
        assert "[cache_builder] weekly backfill summary:" in capsys.readouterr().out
```

- [x] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_cache_builder.py -q -p no:cacheprovider -k "RunSubprocessStep or SharedStep"`
Expected: FAIL (`_run_subprocess_step` missing).

- [x] **Step 3: Implement** `_run_subprocess_step` per the Interfaces block and rewrite the two call sites. Keep every existing `TestSyncRawdata` and `TestWeeklyBackfillStep` test passing unchanged.

- [x] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_cache_builder.py -q -p no:cacheprovider`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "refactor: share one subprocess-step helper between rawdata sync and weekly backfill"
```

---

### Task 4: Close the `tests/test_cache_builder.py` gaps

**Files:** Modify `tests/test_cache_builder.py`

- [x] **Step 1: Dead variable.** In `TestWeeklyBackfillStep.test_runs_backfill_on_tuesday`, delete the unused local `tuesday = datetime(...)` (and the now-unused `from datetime import datetime, timezone` line if nothing else in that test uses it). Rename the test to `test_main_invokes_weekly_backfill_step` and correct its docstring/comment to say it checks that `main()` calls the (mocked) step, not the Tuesday gate (the gate is covered by `test_subprocess_only_runs_on_tuesday` and `test_subprocess_invoked_with_firestore_flag_on_tuesday`). Do not change what it asserts.

- [x] **Step 2: Isolate `TestPredictionsActiveSignal`.** Add `@patch("scripts.cache_builder._run_weekly_backfill_if_tuesday")` to `test_full_run_signals_predictions_active` (a new leading mock argument) so a real Tuesday can never spawn the backfill subprocess. Match the pattern in `TestWeeklyBackfillStep.test_runs_after_the_cache_invalidation_signal`. Add an assertion that the mocked step was called once.

- [x] **Step 3 (SUPERSEDED during execution, see note): Prove the isolation.**

  **Note (2026-09-24):** review found this step's test vacuous (the real step was mocked, so the pinned Tuesday and the raising `subprocess.run` never came into play, and `swallow_errors=True` would have swallowed the sentinel anyway). It was replaced by two `main()` wiring tests that leave `_run_weekly_backfill_if_tuesday` real: `test_main_runs_the_weekly_backfill_subprocess_on_a_tuesday` and `test_main_does_not_run_the_weekly_backfill_subprocess_on_a_monday`, verified by mutating the weekday gate. Original step text kept below for the record.
 Add a new test in the same class that runs `main()` with `datetime` patched to a Tuesday and `cb.subprocess.run` patched to raise `AssertionError("real subprocess must not run")`, with `_run_weekly_backfill_if_tuesday` left patched as in Step 2's decorator: it must pass (no subprocess). (This documents why the decorator matters.)

- [x] **Step 4: Run**

Run: `python -m pytest tests/test_cache_builder.py -q -p no:cacheprovider`
Expected: all pass, one test renamed, none removed.

- [x] **Step 5: Commit**

```bash
git add tests/test_cache_builder.py
git commit -m "test: tighten weekly-backfill test isolation and drop a dead variable"
```

---

### Task 5: Observability warning and the week-filtered empty-table test

**Files:** Modify `services/nn_feature_engine.py` (`_load_qb_snap_shares`, `~2181-2183`); Test `tests/test_nn_feature_engine.py`, `tests/test_preseason_profiles.py`

- [x] **Step 1: Write the failing tests.**

  In `tests/test_nn_feature_engine.py`:

```python
class TestQbSnapSharesCrosswalkWarning:
    def _write(self, tmp_path, roster_cols):
        (tmp_path / "snap_counts").mkdir()
        pd.DataFrame({
            "season": [2024], "week": [1], "team": ["KC"], "position": ["QB"],
            "game_type": ["REG"], "offense_snaps": [60], "pfr_player_id": ["P1"],
        }).to_csv(tmp_path / "snap_counts" / "snap_counts_2024.csv", index=False)
        (tmp_path / "rosters").mkdir()
        pd.DataFrame({c: ["x"] for c in roster_cols}).to_csv(
            tmp_path / "rosters" / "roster_2024.csv", index=False)

    def test_warns_when_the_crosswalk_columns_are_absent(self, tmp_path, caplog):
        import logging
        from services.nn_feature_engine import _load_qb_snap_shares
        self._write(tmp_path, ["full_name"])  # no pfr_id / gsis_id

        with caplog.at_level(logging.WARNING, logger="services.nn_feature_engine"):
            out = _load_qb_snap_shares(tmp_path)

        assert out.empty
        assert any("pfr_id" in r.message and "gsis_id" in r.message for r in caplog.records)

    def test_warns_when_the_roster_files_are_missing_entirely(self, tmp_path, caplog):
        import logging
        from services.nn_feature_engine import _load_qb_snap_shares
        self._write(tmp_path, ["pfr_id", "gsis_id"])
        (tmp_path / "rosters" / "roster_2024.csv").unlink()

        with caplog.at_level(logging.WARNING, logger="services.nn_feature_engine"):
            out = _load_qb_snap_shares(tmp_path)

        assert out.empty
        assert any("roster" in r.message.lower() for r in caplog.records)

    def test_no_warning_when_the_crosswalk_is_present(self, tmp_path, caplog):
        import logging
        from services.nn_feature_engine import _load_qb_snap_shares
        (tmp_path / "snap_counts").mkdir()
        pd.DataFrame({"season": [2024], "week": [1], "team": ["KC"], "position": ["QB"],
                      "game_type": ["REG"], "offense_snaps": [60], "pfr_player_id": ["P1"]}).to_csv(
            tmp_path / "snap_counts" / "snap_counts_2024.csv", index=False)
        (tmp_path / "rosters").mkdir()
        pd.DataFrame({"pfr_id": ["P1"], "gsis_id": ["G1"]}).to_csv(
            tmp_path / "rosters" / "roster_2024.csv", index=False)

        with caplog.at_level(logging.WARNING, logger="services.nn_feature_engine"):
            out = _load_qb_snap_shares(tmp_path)

        assert len(out) == 1
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
```

  In `tests/test_preseason_profiles.py`, add to `TestWeekAwareRosterSnapshot`:

```python
    def test_weekly_rosters_with_no_rows_for_the_requested_week_returns_empty(self, tmp_path):
        """The file exists and parses, but holds only OTHER weeks: distinct from
        the file being missing entirely (covered above)."""
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_common_files(tmp_path)
        other_week = pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True)
        other_week["week"] = 2
        (tmp_path / "weekly_rosters").mkdir(exist_ok=True)
        other_week.to_csv(tmp_path / "weekly_rosters" / f"roster_weekly_{2026}.csv", index=False)

        assert compute_preseason_player_profiles(2026, tmp_path, week=1) == {}
        assert "AAA" in compute_preseason_player_profiles(2026, tmp_path, week=2)  # the file itself is fine

    def test_z_table_skips_a_week_whose_weekly_roster_slice_is_empty(self, tmp_path):
        """_build_profile_z_table must skip (not raise on, not fabricate) a
        (season, week) pair whose roster slice is empty."""
        from services.nn_feature_engine import _build_profile_z_table
        self._write_common_files(tmp_path)
        other_week = pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True)
        other_week["week"] = 2
        (tmp_path / "weekly_rosters").mkdir(exist_ok=True)
        other_week.to_csv(tmp_path / "weekly_rosters" / f"roster_weekly_{2026}.csv", index=False)

        table = _build_profile_z_table([(2026, 1), (2026, 2)], tmp_path)

        assert not [k for k in table if k[:2] == (2026, 1)]
        assert [k for k in table if k[:2] == (2026, 2)]
```

  If the actual behavior differs from what these tests assume (for example the empty-slice case raises `KeyError` instead of returning `{}`), STOP and report NEEDS_CONTEXT with the observed behavior rather than adjusting the assertion to fit.

- [x] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_nn_feature_engine.py tests/test_preseason_profiles.py -q -p no:cacheprovider -k "CrosswalkWarning or no_rows_for_the_requested_week or empty"`
Expected: the two warning tests FAIL (no warning logged); the week-filtered tests may already PASS (they close a coverage gap for existing behavior) or reveal a real difference.

- [x] **Step 3: Implement the warning.** In `_load_qb_snap_shares`, where it currently returns `empty` when the roster is empty or lacks `pfr_id`/`gsis_id`, log first:

```python
    roster = _load_multi_season("rosters/roster_*.csv", rd)
    if roster.empty or "pfr_id" not in roster.columns or "gsis_id" not in roster.columns:
        logger.warning(
            "_load_qb_snap_shares: roster crosswalk unavailable (roster empty=%s, "
            "has pfr_id=%s, has gsis_id=%s) -- the snap-share leg of the QB "
            "availability signal is disabled for this run",
            roster.empty, "pfr_id" in roster.columns, "gsis_id" in roster.columns,
        )
        return empty
```

  `logger` already exists in the module (`logging.getLogger(__name__)`); confirm.

- [x] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_nn_feature_engine.py tests/test_preseason_profiles.py -q -p no:cacheprovider`
Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: warn when the QB snap-share crosswalk is unavailable; test empty week slices"
```

---

### Task 6: Verify, document, archive

- [ ] `python -m pytest tests/ -n auto -q -p no:cacheprovider`; the only failures may be the environment-only ones listed in the Global Constraints (and the known `test_player_analytics` flake).
- [ ] Add a status line to `docs/superpowers/specs/2026-09-17-qb-availability-followup-cleanup.md`: resolved by this branch: the three Performance items (duplicated I/O, the two `.apply` passes, the subprocess helper), the three Test-coverage items it names (dead variable, backfill patching, empty week-filtered table), and the `_load_qb_snap_shares` warning. Still open: the malformed `weekly_rosters` missing-`week`-column `KeyError`, the `_load_declared_starters` duplicate-schema ordering note, the two documentation items, and the informational behavior notes.
- [ ] Move this plan to `docs/superpowers/plans/completed/`; leave the spec in place (items remain open).
- [ ] Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch (do not merge or push without being asked).
