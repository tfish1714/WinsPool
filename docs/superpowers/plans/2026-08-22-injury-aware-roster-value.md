# Graded Injury-Aware Roster Value Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grade `roster_value_service.py`'s weekly roster-quality computation by real injury severity instead of binary active/inactive, add a narrow same-day ESPN check + cheap single-slate repredict for the window between the last scheduled predict run and kickoff, and retrain all three models once to pick up both this change and four pre-existing (2026-08-15) feature-engine bug fixes.

**Architecture:** One new availability-weight factor threaded through the existing per-player scoring loop in `roster_value_service.py`, sourced two ways: a weekly nflverse `injuries_*.csv` grade (always active) and an optional ESPN-sourced override for a specific `(week, gsis_id)` that takes precedence when present (fresher signal, closer to kickoff). A new scoped `--games` mode in `cache_builder.py` builds one season's feature table (not the full multi-year historical table) and publishes only the requested games through the existing `game_predictions` store. `schedule_kickoffs.py` gains a third enqueued Cloud Task per kickoff cluster that invokes this scoped mode via a Cloud Run Jobs `:run` argument override, reusing the existing `winspool-predict-daily` job — no new job, image, or Firestore collection.

**Tech Stack:** Python (existing stack), pandas, `requests` (ESPN calls, already a dependency), Cloud Run Jobs Admin API `:run` overrides (already used by `schedule_kickoffs.py`, extended here to carry container args).

**Spec:** `docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md`

## Global Constraints

- Any script that writes to Firestore must set `os.environ["USE_LOCAL_DATA"] = "False"` before importing `services.db_service` (CLAUDE.md gotcha) — already true of `cache_builder.py` and `schedule_kickoffs.py`; no new script in this plan needs this since both new modules (`espn_injury_service.py` and the `roster_value_service.py` changes) are pure computation, called from code that already sets this.
- The availability-weight scale is fixed across both Part A and Part B: `Out` → 0.0, `Doubtful` → 0.15, `Questionable` → 0.5, anything else / not listed → 1.0. Do not introduce a second scale.
- The narrow repredict path must publish through the existing `get_game_predictions()` / `merge_thin_game_predictions()` / `write_game_predictions()` helpers in `services/cache_service.py` — no new prediction store, no bypassing `merge_thin_game_predictions()` (it preserves richer fields like `model_spread`/`edge_vs_vegas` that a thin overwrite would destroy).
- ESPN calls (`services/espn_injury_service.py`) are unofficial/undocumented and must degrade gracefully: any per-game or per-call failure returns an empty result, never raises, never calls `send_alert_email()`.
- `services/cache_service.py`'s `analytics_cache` collection (`wins_pool_standings`, `player_winlossmatrix`, `schedule_enriched`, `weekbyweek`, `prediction_snapshot`) is out of scope — not read by any live route, not touched by the new `--games` mode.
- `home_qb_injury_flag`/`away_qb_injury_flag` (`nn_feature_engine.py`) is a separate, already-trained feature — not modified.

---

### Task 1: Graded availability weighting in `roster_value_service.py`

**Files:**
- Modify: `services/roster_value_service.py`
- Test: `tests/test_roster_value_service.py` (new file)

**Interfaces:**
- Produces: `_load_injury_report(rawdata_dir: pathlib.Path, target_season: int) -> Dict[Tuple[int, str], float]` — keyed by `(week, gsis_id)`, availability multiplier per the fixed scale.
- Produces: `compute_roster_value(target_season: int, rawdata_dir: pathlib.Path, espn_overrides: Optional[Dict[Tuple[int, str], float]] = None) -> Dict[Tuple[int, int, str], dict]` — same return shape as before; `espn_overrides` is a new optional 3rd parameter, consumed by Task 2's plumbing and Task 5's wiring, defaulting to `None` (no behavior change for any existing caller that doesn't pass it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_roster_value_service.py`:

```python
import pandas as pd
import pytest
from services.roster_value_service import _load_injury_report, compute_roster_value


def _write_injuries_csv(tmp_path, rows):
    d = tmp_path / "injuries"
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "injuries_2025.csv", index=False)


class TestLoadInjuryReport:
    def test_out_status_maps_to_zero(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "QB", "report_status": "Out"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.0

    def test_doubtful_status_maps_to_point_one_five(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "WR", "report_status": "Doubtful"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.15

    def test_questionable_status_maps_to_point_five(self, tmp_path):
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "LB", "report_status": "Questionable"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert result[(3, "P1")] == 0.5

    def test_player_not_on_report_is_absent_not_defaulted(self, tmp_path):
        """Absence means 'not listed' -- callers must default missing keys to
        1.0 themselves; this loader only returns players who ARE listed."""
        _write_injuries_csv(tmp_path, [
            {"season": 2025, "week": 3, "team": "KC", "gsis_id": "P1",
             "position": "QB", "report_status": "Out"},
        ])
        result = _load_injury_report(tmp_path, 2025)
        assert (3, "P2") not in result

    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = _load_injury_report(tmp_path, 2099)
        assert result == {}


class TestComputeRosterValueAvailabilityWeighting:
    """Integration-level: an Out starting QB with no other QB on the roster
    must drop that team's off_roster_value well below a healthy team's,
    holding everything else equal."""

    def _write_common_fixtures(self, tmp_path, season=2025, injured_status=None):
        # Prior-season EPA: one strong QB, established via >= MIN_QB_ATTEMPTS.
        prior = tmp_path / "stats_player"
        prior.mkdir(parents=True, exist_ok=True)
        rows = []
        for wk in range(1, 18):
            rows.append({
                "player_id": "QB1", "position": "QB", "season_type": "REG", "week": wk,
                "passing_epa": 5.0, "attempts": 30,
            })
        pd.DataFrame(rows).to_csv(prior / f"stats_player_week_{season - 1}.csv", index=False)
        pd.DataFrame(columns=["player_id", "position", "season_type", "week",
                               "passing_epa", "attempts"]).to_csv(
            prior / f"stats_player_week_{season}.csv", index=False)

        rosters = tmp_path / "weekly_rosters"
        rosters.mkdir(parents=True, exist_ok=True)
        roster_rows = [{
            "season": season, "week": wk, "team": "KC", "gsis_id": "QB1",
            "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
        } for wk in range(1, 5)]
        pd.DataFrame(roster_rows).to_csv(rosters / f"roster_weekly_{season}.csv", index=False)

        if injured_status:
            self._write_injuries(tmp_path, season, "QB1", 3, injured_status)

    def _write_injuries(self, tmp_path, season, gsis_id, week, status):
        d = tmp_path / "injuries"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{
            "season": season, "week": week, "team": "KC", "gsis_id": gsis_id,
            "position": "QB", "report_status": status,
        }]).to_csv(d / f"injuries_{season}.csv", index=False)

    def test_out_qb_drops_off_roster_value_vs_healthy(self, tmp_path):
        healthy_dir = tmp_path / "healthy"
        out_dir = tmp_path / "out"
        (healthy_dir).mkdir()
        (out_dir).mkdir()
        for base in (healthy_dir, out_dir):
            (base / "stats_player").mkdir()
            (base / "weekly_rosters").mkdir()

        self._write_common_fixtures(healthy_dir)
        self._write_common_fixtures(out_dir, injured_status="Out")

        healthy = compute_roster_value(2025, healthy_dir)
        out = compute_roster_value(2025, out_dir)

        # Week 3 is when the injury applies; both teams are otherwise identical.
        assert out[(2025, 3, "KC")]["off_roster_value"] < healthy[(2025, 3, "KC")]["off_roster_value"]

    def test_espn_override_takes_precedence_over_nflverse_report(self, tmp_path):
        """A player listed Questionable on nflverse's report, but overridden
        to Out by a fresher ESPN check for that exact (week, gsis_id), must
        use the ESPN weight, not the nflverse one."""
        base = tmp_path
        (base / "stats_player").mkdir()
        (base / "weekly_rosters").mkdir()
        self._write_common_fixtures(base, injured_status="Questionable")

        without_override = compute_roster_value(2025, base)
        with_override = compute_roster_value(
            2025, base, espn_overrides={(3, "QB1"): 0.0},
        )

        assert with_override[(2025, 3, "KC")]["off_roster_value"] < \
            without_override[(2025, 3, "KC")]["off_roster_value"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_roster_value_service.py -v`
Expected: FAIL — `_load_injury_report` does not exist (ImportError), and `compute_roster_value()` does not accept `espn_overrides`.

- [ ] **Step 3: Implement `_load_injury_report()` and wire it into `compute_roster_value()`**

In `services/roster_value_service.py`, add near the other loaders (after `_load_weekly_roster`, before the "Main entry point" section):

```python
# Availability weight scale -- applied on top of the existing age multiplier
# and depth discount as a third, independent factor. A player ruled Out or
# Doubtful is still on the 53-man roster (so _ACTIVE_STATUSES doesn't
# exclude them) but shouldn't count at full value for the week they're
# actually out -- this is the gap _ACTIVE_STATUSES alone can't close, since
# it only distinguishes "on roster" from "on IR/practice squad".
_AVAILABILITY_WEIGHTS: Dict[str, float] = {"Out": 0.0, "Doubtful": 0.15, "Questionable": 0.5}


def _load_injury_report(rawdata_dir: pathlib.Path, target_season: int) -> Dict[Tuple[int, str], float]:
    """(week, gsis_id) -> availability multiplier, from injuries_{season}.csv's
    report_status. Only players actually listed on the report appear in the
    returned dict -- callers must default a missing key to 1.0 (full go)."""
    path = rawdata_dir / "injuries" / f"injuries_{target_season}.csv"
    df = _read_safe(str(path))
    if df.empty:
        return {}

    df = df.dropna(subset=["week", "gsis_id", "report_status"])
    result: Dict[Tuple[int, str], float] = {}
    for row in df.itertuples():
        weight = _AVAILABILITY_WEIGHTS.get(row.report_status, 1.0)
        result[(int(row.week), str(row.gsis_id))] = weight
    return result
```

Change the `compute_roster_value()` signature and add the injury-report load near its other data loads:

```python
def compute_roster_value(
    target_season: int,
    rawdata_dir: pathlib.Path,
    espn_overrides: Optional[Dict[Tuple[int, str], float]] = None,
) -> Dict[Tuple[int, int, str], dict]:
    """
    ...(existing docstring)...
    """
    prior_season = target_season - 1
    logger.info("roster_value: building weekly profiles for %d (prior=%d)", target_season, prior_season)

    # ---- Load data ----
    prior_epa    = _load_prior_epa(rawdata_dir, prior_season)
    prior_qb_dur = _load_prior_qb_durability(rawdata_dir, prior_season)
    current_epa  = _load_current_rolling_epa(rawdata_dir, target_season)
    kicker_roll  = _load_kicker_rolling(rawdata_dir, target_season, prior_season)
    punter_map   = _load_punter_prior(rawdata_dir, prior_season)
    weekly_ros   = _load_weekly_roster(rawdata_dir, target_season)
    injury_report = _load_injury_report(rawdata_dir, target_season)
    espn_overrides = espn_overrides or {}
```

(Everything else in `compute_roster_value()` before the per-player loop is unchanged.)

Inside the per-player loop (where `adj = score * amlt` currently sits), change to:

```python
                adj = score * amlt

                # ESPN's same-day check (when available) always wins over the
                # nflverse weekly report for the exact (week, player) it
                # covers -- it runs strictly closer to kickoff. Otherwise fall
                # back to nflverse's graded report, defaulting to 1.0 (full
                # go) for anyone not listed.
                avail_mult = espn_overrides.get(
                    (week, pid), injury_report.get((week, pid), 1.0)
                )
                adj = adj * avail_mult
```

Add `Optional` to the existing `typing` import line at the top of the file (`from typing import Dict, Tuple` → `from typing import Dict, Optional, Tuple`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_roster_value_service.py -v`
Expected: PASS (5 + 2 = 7 tests)

- [ ] **Step 5: Run the full existing test suite to check for regressions**

Run: `pytest tests/ -k "roster_value or nn_feature_engine or cache_builder"`
Expected: PASS — `espn_overrides` defaults to `None`/`{}`, so no existing caller's output changes.

- [ ] **Step 6: Commit**

```bash
git add services/roster_value_service.py tests/test_roster_value_service.py
git commit -m "feat: grade roster-value availability by injury report status, not just active/inactive"
```

---

### Task 2: Plumb `espn_overrides` through `build_master_feature_table()`

**Files:**
- Modify: `services/nn_feature_engine.py`
- Test: `tests/test_nn_feature_engine.py`

**Interfaces:**
- Consumes: `compute_roster_value(target_season, rawdata_dir, espn_overrides=...)` from Task 1.
- Produces: `build_master_feature_table(rawdata_dir=None, min_season=2006, max_season=2025, espn_overrides: Optional[Dict[Tuple[int, str], float]] = None)` — new optional 4th parameter, default `None` (no behavior change for the train scripts and `backfill_schedule_predictions.py`, which never pass it).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_nn_feature_engine.py`:

```python
def test_build_master_feature_table_threads_espn_overrides(monkeypatch):
    """espn_overrides must reach compute_roster_value() unchanged -- this is
    a plumbing test, not a behavior test (Task 1 already covers the actual
    override-precedence behavior inside compute_roster_value())."""
    from unittest.mock import MagicMock
    import services.nn_feature_engine as fe

    captured = {}

    def fake_compute_rv(season, rd, espn_overrides=None):
        captured["espn_overrides"] = espn_overrides
        return {}

    monkeypatch.setattr(
        "services.roster_value_service.compute_roster_value", fake_compute_rv
    )

    sched = MagicMock()
    # Minimal stand-in: only exercise the roster-value block by calling the
    # real function requires a full schedule; instead call compute_roster_value's
    # import site directly by invoking build_master_feature_table with a
    # tiny real schedule fixture already used elsewhere in this test file
    # (see existing `_minimal_schedule_fixture` helper below in this file).
    overrides = {(3, "QB1"): 0.0}
    fe.build_master_feature_table(
        rawdata_dir=str(FIXTURES_DIR), min_season=2024, max_season=2024,
        espn_overrides=overrides,
    )
    assert captured["espn_overrides"] == overrides
```

Note for the implementer: check the top of `tests/test_nn_feature_engine.py` for the existing fixture/rawdata-dir convention this file already uses for other `build_master_feature_table()` tests (there should be at least one, since the function is exercised elsewhere) and reuse the same `FIXTURES_DIR`/schedule setup rather than inventing a new one — copy the exact pattern an existing passing test in that file already uses to build a minimal `min_season=max_season` schedule, since `build_master_feature_table()` raises `ValueError("No schedule data found.")` on an empty schedule.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_nn_feature_engine.py::test_build_master_feature_table_threads_espn_overrides -v`
Expected: FAIL — `build_master_feature_table()` raises `TypeError: unexpected keyword argument 'espn_overrides'`.

- [ ] **Step 3: Implement the plumbing**

In `services/nn_feature_engine.py`, change the signature:

```python
def build_master_feature_table(
    rawdata_dir: Optional[str] = None,
    min_season: int = 2006,
    max_season: int = 2025,
    espn_overrides: Optional[Dict[Tuple[int, str], float]] = None,
) -> pd.DataFrame:
```

And in the roster-value block (the `try:` block that calls `_compute_rv`):

```python
    try:
        from services.roster_value_service import compute_roster_value as _compute_rv
        rv_cache: dict = {}
        for _season in sorted(sched["season"].unique()):
            try:
                rv_cache.update(_compute_rv(int(_season), rd, espn_overrides=espn_overrides))
            except Exception as _e:
                logger.debug("roster_value skipped for %d: %s", _season, _e)
```

Confirm `Dict`/`Tuple`/`Optional` are already imported from `typing` at the top of `nn_feature_engine.py` (they are, per existing use elsewhere in the file) — no new import needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_nn_feature_engine.py::test_build_master_feature_table_threads_espn_overrides -v`
Expected: PASS

- [ ] **Step 5: Run the full feature-engine test suite to check for regressions**

Run: `pytest tests/test_nn_feature_engine.py -v`
Expected: PASS — default `None` preserves existing behavior for every other test.

- [ ] **Step 6: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: thread optional espn_overrides through build_master_feature_table"
```

---

### Task 3: `services/espn_injury_service.py` — ESPN per-game injury signal

**Files:**
- Create: `services/espn_injury_service.py`
- Test: `tests/test_espn_injury_service.py` (new file)

**Interfaces:**
- Consumes: `services.live_score_service.fetch_espn_scores()` (existing), `services.utils.normalize_team_abbr()` (existing).
- Produces: `get_espn_injury_overrides(target_games: List[Tuple[str, str]], season: int, week: int, rawdata_dir: pathlib.Path) -> Dict[Tuple[int, str], float]` — consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_espn_injury_service.py`:

```python
import pandas as pd
import pytest
from unittest.mock import patch
from services.espn_injury_service import (
    _status_to_weight, _extract_status, _load_espn_id_crosswalk,
    _find_event_ids, _fetch_game_injuries, get_espn_injury_overrides,
)


class TestStatusMapping:
    def test_out_maps_to_zero(self):
        assert _status_to_weight("Out") == 0.0

    def test_doubtful_maps_to_point_one_five(self):
        assert _status_to_weight("Doubtful") == 0.15

    def test_questionable_maps_to_point_five(self):
        assert _status_to_weight("Questionable") == 0.5

    def test_unknown_status_maps_to_full_go(self):
        assert _status_to_weight("Probable") == 1.0

    def test_extract_status_from_plain_string(self):
        assert _extract_status({"status": "Questionable"}) == "Questionable"

    def test_extract_status_from_nested_dict(self):
        assert _extract_status({"status": {"description": "Out"}}) == "Out"

    def test_extract_status_missing_returns_empty(self):
        assert _extract_status({}) == ""


class TestLoadEspnIdCrosswalk:
    def test_maps_espn_id_to_gsis_id_for_requested_week(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
            {"season": 2025, "week": 4, "gsis_id": "G2", "espn_id": "E2"},  # wrong week
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        result = _load_espn_id_crosswalk(tmp_path, 2025, 3)
        assert result == {"E1": "G1"}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert _load_espn_id_crosswalk(tmp_path, 2099, 1) == {}


class TestFindEventIds:
    def test_matches_target_game_by_normalized_abbr(self):
        fake_scoreboard = {
            "events": [{
                "id": "401555",
                "competitions": [{
                    "competitors": [
                        {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                        {"homeAway": "away", "team": {"abbreviation": "KC"}},
                    ]
                }],
            }]
        }
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard):
            result = _find_event_ids([("WAS", "KC")])
        assert result == {("WAS", "KC"): "401555"}

    def test_no_scoreboard_data_returns_empty(self):
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=None):
            assert _find_event_ids([("WAS", "KC")]) == {}

    def test_unmatched_game_is_absent(self):
        fake_scoreboard = {"events": [{
            "id": "1", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "SF"}},
                {"homeAway": "away", "team": {"abbreviation": "LAC"}},
            ]}],
        }]}
        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard):
            result = _find_event_ids([("WAS", "KC")])
        assert result == {}


class TestFetchGameInjuries:
    def test_parses_nested_team_injuries(self):
        fake_summary = {"injuries": [
            {"injuries": [
                {"athlete": {"id": "E1"}, "status": "Out"},
                {"athlete": {"id": "E2"}, "status": "Questionable"},
            ]},
        ]}
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = _fetch_game_injuries("401555")
        assert result == [
            {"espn_id": "E1", "status": "Out"},
            {"espn_id": "E2", "status": "Questionable"},
        ]

    def test_http_failure_returns_empty_list(self):
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = False
            assert _fetch_game_injuries("401555") == []

    def test_network_exception_returns_empty_list(self):
        with patch("services.espn_injury_service.requests.get", side_effect=Exception("timeout")):
            assert _fetch_game_injuries("401555") == []

    def test_missing_athlete_id_is_skipped_not_raised(self):
        fake_summary = {"injuries": [{"injuries": [{"status": "Out"}]}]}
        with patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            assert _fetch_game_injuries("401555") == []


class TestGetEspnInjuryOverrides:
    def test_end_to_end_maps_status_to_weight_via_gsis_id(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        fake_scoreboard = {"events": [{
            "id": "401555", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                {"homeAway": "away", "team": {"abbreviation": "KC"}},
            ]}],
        }]}
        fake_summary = {"injuries": [{"injuries": [
            {"athlete": {"id": "E1"}, "status": "Out"},
        ]}]}

        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard), \
             patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)

        assert result == {(3, "G1"): 0.0}

    def test_no_matching_scoreboard_event_returns_empty(self, tmp_path):
        with patch("services.espn_injury_service.fetch_espn_scores", return_value={"events": []}):
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)
        assert result == {}

    def test_player_with_no_crosswalk_entry_is_skipped(self, tmp_path):
        d = tmp_path / "weekly_rosters"
        d.mkdir(parents=True)
        pd.DataFrame([
            {"season": 2025, "week": 3, "gsis_id": "G1", "espn_id": "E1"},
        ]).to_csv(d / "roster_weekly_2025.csv", index=False)

        fake_scoreboard = {"events": [{
            "id": "401555", "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": "WSH"}},
                {"homeAway": "away", "team": {"abbreviation": "KC"}},
            ]}],
        }]}
        fake_summary = {"injuries": [{"injuries": [
            {"athlete": {"id": "UNKNOWN"}, "status": "Out"},
        ]}]}

        with patch("services.espn_injury_service.fetch_espn_scores", return_value=fake_scoreboard), \
             patch("services.espn_injury_service.requests.get") as mock_get:
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = fake_summary
            result = get_espn_injury_overrides([("WAS", "KC")], 2025, 3, tmp_path)

        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_espn_injury_service.py -v`
Expected: FAIL — `services/espn_injury_service.py` does not exist yet (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `services/espn_injury_service.py`**

```python
"""services/espn_injury_service.py -- ESPN per-game injury signal for the
narrow window between the last scheduled predict run and kickoff.

nflverse's injuries_{season}.csv updates daily, not continuously -- a
starter ruled out 90 minutes before kickoff might not be reflected until
the *next* daily sync. ESPN's per-game summary endpoint carries a fresher,
same-day signal. This module fetches it and maps it onto the exact same
availability-weight scale roster_value_service.py's weekly grade already
uses (see docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md),
as an override that wins for the specific (week, player) it covers.

Unofficial, undocumented ESPN API -- no SLA, must degrade gracefully. Every
network call is isolated so one game's or one player's failure never blocks
the rest of a slate.
"""
import logging
import pathlib
from typing import Dict, List, Tuple

import pandas as pd
import requests

from services.live_score_service import fetch_espn_scores
from services.utils import normalize_team_abbr

logger = logging.getLogger(__name__)

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"

# Same scale as roster_value_service.py's _AVAILABILITY_WEIGHTS -- kept as a
# separate copy deliberately (not imported) so this module has no dependency
# on roster_value_service.py, and vice versa; both are wired together only
# by the caller (cache_builder.py's --games mode).
_AVAILABILITY_WEIGHTS: Dict[str, float] = {"Out": 0.0, "Doubtful": 0.15, "Questionable": 0.5}


def _status_to_weight(status: str) -> float:
    return _AVAILABILITY_WEIGHTS.get(status, 1.0)


def _extract_status(entry: dict) -> str:
    """ESPN's injuries[].status has been observed as a plain string during
    manual verification; handle a nested {status: {description}} shape
    defensively too, since this is an undocumented endpoint with no
    guaranteed schema."""
    status = entry.get("status")
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        return status.get("description") or status.get("name") or ""
    return ""


def _load_espn_id_crosswalk(rawdata_dir: pathlib.Path, season: int, week: int) -> Dict[str, str]:
    """{espn_id: gsis_id} for the given (season, week) -- weekly_rosters
    carries both IDs per player per week, giving an exact join instead of
    fuzzy name matching."""
    path = rawdata_dir / "weekly_rosters" / f"roster_weekly_{season}.csv"
    try:
        df = pd.read_csv(path, usecols=["season", "week", "gsis_id", "espn_id"], low_memory=False)
    except Exception as e:
        logger.warning("espn_injury_service: cannot read %s -- %s", path, e)
        return {}

    df = df[df["week"] == week].dropna(subset=["gsis_id", "espn_id"])
    return {str(row.espn_id): str(row.gsis_id) for row in df.itertuples()}


def _find_event_ids(target_games: List[Tuple[str, str]]) -> Dict[Tuple[str, str], str]:
    """Match (home, away) team pairs (already nflverse-normalized) to ESPN
    scoreboard event ids. A game absent from today's scoreboard (e.g. not
    yet close enough to kickoff) is silently absent from the result."""
    data = fetch_espn_scores()
    if not data:
        return {}

    wanted = set(target_games)
    matches: Dict[Tuple[str, str], str] = {}
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        home = away = None
        for c in comp.get("competitors", []):
            abbr = normalize_team_abbr(c.get("team", {}).get("abbreviation", ""))
            if c.get("homeAway") == "home":
                home = abbr
            else:
                away = abbr
        if home and away and (home, away) in wanted:
            matches[(home, away)] = event.get("id")
    return matches


def _fetch_game_injuries(espn_event_id: str) -> List[dict]:
    """Raw parse of one game's injuries[] -- flat list of {espn_id, status}.
    Any failure (network, HTTP error, malformed shape) returns [] rather
    than raising -- this is a per-game, best-effort signal."""
    try:
        resp = requests.get(SUMMARY_URL, params={"event": espn_event_id}, timeout=10)
        if not resp.ok:
            return []
        data = resp.json()
    except Exception as e:
        logger.warning("espn_injury_service: summary fetch failed for event %s -- %s", espn_event_id, e)
        return []

    rows: List[dict] = []
    for team_block in data.get("injuries", []):
        for entry in team_block.get("injuries", []):
            espn_id = entry.get("athlete", {}).get("id")
            if not espn_id:
                continue
            rows.append({"espn_id": str(espn_id), "status": _extract_status(entry)})
    return rows


def get_espn_injury_overrides(
    target_games: List[Tuple[str, str]],
    season: int,
    week: int,
    rawdata_dir: pathlib.Path,
) -> Dict[Tuple[int, str], float]:
    """Main entry point. target_games: [(home_team, away_team), ...],
    already nflverse-normalized. Returns {(week, gsis_id): availability_weight}
    ready to pass straight through to
    roster_value_service.compute_roster_value(..., espn_overrides=...).

    Degrades to {} at any stage (no scoreboard match, a game's summary fetch
    failing, a missing espn_id->gsis_id crosswalk row) -- this is a cosmetic
    freshness improvement on top of the already-graded nflverse weekly
    weight (Task 1), never the sole source of truth.
    """
    event_ids = _find_event_ids(target_games)
    if not event_ids:
        return {}

    crosswalk = _load_espn_id_crosswalk(rawdata_dir, season, week)
    if not crosswalk:
        return {}

    overrides: Dict[Tuple[int, str], float] = {}
    for _game, event_id in event_ids.items():
        for row in _fetch_game_injuries(event_id):
            gsis_id = crosswalk.get(row["espn_id"])
            if not gsis_id:
                continue
            overrides[(week, gsis_id)] = _status_to_weight(row["status"])
    return overrides
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_espn_injury_service.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Manually verify the real ESPN response shape**

This endpoint is undocumented — before trusting `_extract_status()`/`_fetch_game_injuries()`'s assumed shape in production, run one manual check against a real, currently-live or recently-played game:

```bash
python -c "
import requests
# Use any real event id from https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard
resp = requests.get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary', params={'event': '<real_event_id>'})
import json
print(json.dumps(resp.json().get('injuries', []), indent=2)[:2000])
"
```

Compare the printed shape against what `_fetch_game_injuries()` expects (`injuries[].injuries[].athlete.id` / `.status`). If it differs, update `_extract_status()`/`_fetch_game_injuries()` and their tests to match before proceeding — do not skip this check, since the whole override mechanism silently degrades to "no override" if the shape is wrong, which would never surface as a test failure (by design, per the graceful-degradation requirement).

- [ ] **Step 6: Commit**

```bash
git add services/espn_injury_service.py tests/test_espn_injury_service.py
git commit -m "feat: add ESPN per-game injury signal service"
```

---

### Task 4: Scoped `--games` repredict mode in `cache_builder.py`

**Files:**
- Modify: `scripts/cache_builder.py`
- Test: `tests/test_cache_builder.py`

**Interfaces:**
- Consumes: `build_master_feature_table(..., espn_overrides=...)` from Task 2, `get_game_predictions`/`merge_thin_game_predictions`/`write_game_predictions` (existing, `services/cache_service.py`).
- Produces: `_publish_games(game_ids: list[str], games: pd.DataFrame, year: int, pred_lookup: dict) -> int` — returns count of predictions published; a new `--games` CLI arg on `main()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_builder.py`:

```python
import pandas as pd


def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_03_KC_WAS", "season": 2026, "week": 3,
         "home_team": "WAS", "away_team": "KC", "game_type": "REG"},
        {"game_id": "2026_03_SF_LAC", "season": 2026, "week": 3,
         "home_team": "LAC", "away_team": "SF", "game_type": "REG"},
    ])


class TestPublishGames:
    def test_publishes_only_requested_game(self):
        from scripts.cache_builder import _publish_games

        pred_lookup = {
            (2026, 3, "WAS", "KC"): {
                "pred_winner": "KC", "pred_su_conf": 62.0,
                "pred_ats_pick": "KC", "pred_prob": 0.62,
            },
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, pred_lookup)

        assert n == 1
        mock_write.assert_called_once()
        year, merged = mock_write.call_args[0]
        assert year == 2026
        assert "W03_WAS_KC" in merged
        assert "W03_LAC_SF" not in merged  # the other game was never touched

    def test_preserves_existing_richer_fields_for_untouched_games(self):
        from scripts.cache_builder import _publish_games

        existing = {"W03_LAC_SF": {"model_spread": -3.5, "edge_vs_vegas": 1.2}}
        pred_lookup = {
            (2026, 3, "WAS", "KC"): {
                "pred_winner": "KC", "pred_su_conf": 62.0,
                "pred_ats_pick": "KC", "pred_prob": 0.62,
            },
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value=existing), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, pred_lookup)

        _year, merged = mock_write.call_args[0]
        assert merged["W03_LAC_SF"] == existing["W03_LAC_SF"]

    def test_no_matching_game_id_returns_zero(self):
        from scripts.cache_builder import _publish_games
        with patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["nonexistent"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()

    def test_no_prediction_available_for_requested_game_returns_zero(self):
        from scripts.cache_builder import _publish_games
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_games(["2026_03_KC_WAS"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()


class TestGamesModeWiring:
    def test_games_flag_skips_full_multi_year_build(self, monkeypatch):
        """--games must not call build_year() (the full standings/analytics
        rebuild) at all -- only the scoped publish path."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--games", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year") as mock_build_year, \
             patch("scripts.cache_builder.NNPredictionService"), \
             patch("scripts.cache_builder.XGBPredictionService"), \
             patch("scripts.cache_builder.LRPredictionService"), \
             patch("scripts.cache_builder.build_master_feature_table", return_value=pd.DataFrame()), \
             patch("scripts.cache_builder._build_pred_lookup", return_value={}), \
             patch("scripts.cache_builder._publish_games", return_value=0) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            main()

        mock_build_year.assert_not_called()
        mock_publish.assert_called_once()

    def test_games_flag_scopes_feature_table_to_one_season(self, monkeypatch):
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--games", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNPredictionService"), \
             patch("scripts.cache_builder.XGBPredictionService"), \
             patch("scripts.cache_builder.LRPredictionService"), \
             patch("scripts.cache_builder.build_master_feature_table", return_value=pd.DataFrame()) as mock_bmft, \
             patch("scripts.cache_builder._build_pred_lookup", return_value={}), \
             patch("scripts.cache_builder._publish_games", return_value=1), \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            main()

        _args, kwargs = mock_bmft.call_args
        assert kwargs["min_season"] == 2026
        assert kwargs["max_season"] == 2026
```

Add `from unittest.mock import patch` to the top of `tests/test_cache_builder.py` if not already imported (it already is, per the existing `@patch` decorators — confirm before adding a duplicate import).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder.py -v`
Expected: FAIL — `_publish_games` does not exist; `main()` has no `--games` argument.

- [ ] **Step 3: Implement `_publish_games()` and the `--games` mode**

In `scripts/cache_builder.py`, add `RAWDATA_DIR` to the existing `nn_feature_engine` import:

```python
from services.nn_feature_engine import (
    build_master_feature_table, FEATURE_COLUMNS, _normalize_team, RAWDATA_DIR,
)
```

Add near `_build_pred_lookup` (same section of the file):

```python
def _publish_games(game_ids: list, games: pd.DataFrame, year: int, pred_lookup: dict) -> int:
    """Scoped publish: apply predictions to just game_ids and write only
    those rows into game_predictions, without touching wins_pool_standings/
    player_winlossmatrix/weekbyweek/prediction_snapshot or the
    analytics_cache schedule_enriched blob (unread by any live route -- see
    docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md).
    """
    target = games[games["game_id"].astype(str).isin(game_ids)].copy()
    if target.empty:
        print(f"[cache_builder] --games: no matching rows for {game_ids}")
        return 0

    pred_winners, pred_confs, pred_ats, pred_probs = [], [], [], []
    for _, row in target.iterrows():
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')
        pred = pred_lookup.get((year, int(wk), ht, at)) if (ht and at and wk is not None) else None
        pred_winners.append(pred['pred_winner'] if pred else None)
        pred_confs.append(pred['pred_su_conf'] if pred else None)
        pred_ats.append(pred['pred_ats_pick'] if pred else None)
        pred_probs.append(pred['pred_prob'] if pred else None)

    target['pred_winner'] = pred_winners
    target['pred_su_conf'] = pred_confs
    target['pred_ats_pick'] = pred_ats
    target['pred_prob'] = pred_probs

    pmap = {}
    for _, r in target.dropna(subset=['pred_winner']).iterrows():
        ht = _normalize_team(str(r.get('home_team', '') or ''))
        at = _normalize_team(str(r.get('away_team', '') or ''))
        wk = r.get('week')
        pmap[f"W{int(wk):02d}_{ht}_{at}"] = {
            'pred_prob':     r.get('pred_prob'),
            'pred_winner':   r.get('pred_winner'),
            'pred_su_conf':  r.get('pred_su_conf'),
            'pred_ats_pick': r.get('pred_ats_pick'),
        }

    if not pmap:
        print("[cache_builder] --games: no predictions produced for requested games")
        return 0

    existing = get_game_predictions(year)
    merged = merge_thin_game_predictions(existing, pmap)
    write_game_predictions(year, merged)
    return len(pmap)
```

In `main()`, add the new argument and branch. Add to the `argparse` block:

```python
    parser.add_argument('--games', type=str, default=None,
                        help="Comma-separated game_ids to scope a cheap single-slate "
                             "repredict to, instead of the full multi-year rebuild. "
                             "Publishes only to game_predictions.")
```

After the existing `if not args.skip_sync:` block and the `standings, teams, games, ... = load_data()` line, insert the scoped branch before the normal `available_years`/full-build flow:

```python
    print("[cache_builder] Loading raw data from Firestore / local cache...")
    standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()

    if args.games:
        game_ids = [g.strip() for g in args.games.split(",") if g.strip()]
        target_rows = games[games["game_id"].astype(str).isin(game_ids)]
        if target_rows.empty:
            print(f"[cache_builder] --games: no matching rows for {game_ids}; nothing to do")
            return

        year = int(target_rows["season"].iloc[0])
        week = int(target_rows["week"].iloc[0])
        espn_overrides = {}
        try:
            from services.espn_injury_service import get_espn_injury_overrides
            target_pairs = list(zip(
                target_rows["home_team"].map(_normalize_team),
                target_rows["away_team"].map(_normalize_team),
            ))
            espn_overrides = get_espn_injury_overrides(target_pairs, year, week, RAWDATA_DIR)
        except Exception as e:
            print(f"[cache_builder] ESPN override fetch failed (non-fatal): {e}")

        print("[cache_builder] Loading ML models...")
        nn_svc  = NNPredictionService();  nn_svc.load_model()
        xgb_svc = XGBPredictionService(); xgb_svc.load_model()
        lr_svc  = LRPredictionService();  lr_svc.load_model()

        print(f"[cache_builder] Building feature table (scoped to {year})...")
        ft = build_master_feature_table(min_season=year, max_season=year, espn_overrides=espn_overrides)
        pred_lookup = _build_pred_lookup(ft, nn_svc, xgb_svc, lr_svc)

        n = _publish_games(game_ids, games, year, pred_lookup)
        print(f"[cache_builder] --games: published {n} prediction(s).")

        db = _fs.client()
        db.collection("metadata").document("cache_control").set({"last_update": time.time()})
        return

    available_years = get_available_years(draft_results)
```

(The rest of `main()` — the full multi-year build — is unchanged, and is skipped entirely by the early `return` above when `--games` is passed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS

- [ ] **Step 5: Run the full cache_builder test suite to check for regressions**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS — the default (no `--games`) path through `main()` is untouched code, only the new early-return branch was added above it.

- [ ] **Step 6: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "feat: add scoped --games repredict mode to cache_builder.py"
```

---

### Task 5: `schedule_kickoffs.py` — enqueue the scoped repredict close to kickoff

**Files:**
- Modify: `scripts/schedule_kickoffs.py`
- Test: `tests/test_schedule_kickoffs.py`

**Interfaces:**
- Consumes: `compute_kickoff_clusters()`, `enqueue_task()` (existing, both modified here).
- Produces: `enqueue_task(tasks_client, run_at, job_name, job_args=None)` — existing function gains a 4th optional parameter; `compute_kickoff_clusters_with_games(games, season, week) -> list[tuple[datetime, list[str]]]` — new function returning each kickoff cluster paired with its game_ids, needed so `main()` knows which `--games` value to pass per cluster.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_schedule_kickoffs.py`:

```python
class TestEnqueueTaskWithOverrides:
    def _kickoff(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 9, 20, 13, 0, tzinfo=ZoneInfo("America/New_York"))

    def test_job_args_produce_container_override_body(self, monkeypatch):
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"

        enqueue_task(client, self._kickoff(), "winspool-predict-daily",
                      job_args=["--games", "2026_03_KC_WAS"])

        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "body" in task["http_request"]
        import json
        body = json.loads(task["http_request"]["body"])
        assert body["overrides"]["containerOverrides"][0]["args"] == ["--games", "2026_03_KC_WAS"]

    def test_no_job_args_omits_body(self, monkeypatch):
        """Existing sync/predict calls (no job_args) must be unaffected --
        no body means the job runs with its normal configured command."""
        from unittest.mock import MagicMock
        from scripts.schedule_kickoffs import enqueue_task

        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_PROJECT", "test-project")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_REGION", "us-east1")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_TASKS_QUEUE", "test-queue")
        monkeypatch.setattr("scripts.schedule_kickoffs.GCP_SCHEDULER_SERVICE_ACCOUNT", "sa@test.iam.gserviceaccount.com")

        client = MagicMock()
        client.queue_path.return_value = "projects/test-project/locations/us-east1/queues/test-queue"

        enqueue_task(client, self._kickoff(), "winspool-sync-daily")

        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "body" not in task["http_request"]


class TestComputeKickoffClustersWithGames:
    def test_pairs_each_cluster_with_its_game_ids(self):
        from scripts.schedule_kickoffs import compute_kickoff_clusters_with_games
        games = pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g1",
             "gameday": "2026-09-20", "gametime": "13:00"},
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g2",
             "gameday": "2026-09-20", "gametime": "13:00"},
            {"season": 2026, "week": 3, "game_type": "REG", "game_id": "g3",
             "gameday": "2026-09-20", "gametime": "16:25"},
        ])
        result = compute_kickoff_clusters_with_games(games, 2026, 3)
        assert len(result) == 2
        early = next(g for dt, g in result if dt.hour == 13)
        late = next(g for dt, g in result if dt.hour == 16)
        assert sorted(early) == ["g1", "g2"]
        assert late == ["g3"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: FAIL — `enqueue_task()` doesn't accept `job_args`; `compute_kickoff_clusters_with_games` doesn't exist.

- [ ] **Step 3: Implement**

In `scripts/schedule_kickoffs.py`, add near `PREDICT_LEAD_MINUTES`:

```python
PREDICT_LEAD_MINUTES = 60
# How close to kickoff the ESPN check + scoped repredict runs. Anchored to
# the NFL's official inactive-list deadline (kickoff-90min, league rule) --
# this leaves 20 minutes of margin after that deadline before this task
# fires. NOT yet validated against a measured runtime of the new --games
# mode (Task 4) in production: before relying on this in-season, time a
# real invocation (see Step 5 below) and adjust this constant if it runs
# longer than the margin allows.
REPREDICT_LEAD_MINUTES = 70
```

Add `compute_kickoff_clusters_with_games()` next to `compute_kickoff_clusters()`:

```python
def compute_kickoff_clusters_with_games(games: pd.DataFrame, season: int, week: int) -> list[tuple[datetime, list]]:
    """Same clustering as compute_kickoff_clusters(), but paired with each
    cluster's game_ids so the caller knows what to pass to --games."""
    wk = games[
        (games["season"] == season)
        & (games["week"] == week)
        & (games["game_type"] == "REG")
    ]
    clusters: dict = {}
    for _, row in wk.iterrows():
        key_dt = datetime.strptime(
            f"{row['gameday']} {row['gametime']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=_EASTERN)
        clusters.setdefault(key_dt, []).append(row["game_id"])
    return sorted(clusters.items())
```

Change `enqueue_task()`'s signature and body to accept optional `job_args`:

```python
def enqueue_task(tasks_client, run_at: datetime, job_name: str, job_args: list = None) -> None:
    from google.api_core.exceptions import AlreadyExists
    from google.cloud import tasks_v2
    from google.protobuf import timestamp_pb2
    import json

    parent = tasks_client.queue_path(GCP_PROJECT, GCP_REGION, GCP_TASKS_QUEUE)
    ts = timestamp_pb2.Timestamp()
    ts.FromDatetime(run_at.astimezone(timezone.utc))

    task_id = f"{job_name}-{run_at.strftime('%Y%m%dT%H%M')}"
    task_name = (
        f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/"
        f"queues/{GCP_TASKS_QUEUE}/tasks/{task_id}"
    )

    http_request = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": _run_url(job_name),
        "oauth_token": {"service_account_email": GCP_SCHEDULER_SERVICE_ACCOUNT},
    }
    if job_args:
        # Cloud Run Jobs Admin API's :run RunJobRequest body -- overrides
        # the container's configured args for just this execution, so the
        # scoped repredict can reuse winspool-predict-daily's existing job
        # instead of provisioning a new one.
        body = {"overrides": {"containerOverrides": [{"args": job_args}]}}
        http_request["body"] = json.dumps(body).encode("utf-8")

    task = {
        "name": task_name,
        "http_request": http_request,
        "schedule_time": ts,
    }
    try:
        tasks_client.create_task(request={"parent": parent, "task": task})
    except AlreadyExists:
        print(f"[skip] task already enqueued: {task_id}")
```

Update `main()`'s enqueue loop:

```python
        client = tasks_v2.CloudTasksClient()
        for kickoff, game_ids in compute_kickoff_clusters_with_games(games, season, week):
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")
            enqueue_task(
                client, kickoff - timedelta(minutes=REPREDICT_LEAD_MINUTES), "winspool-predict-daily",
                job_args=["--games", ",".join(str(g) for g in game_ids)],
            )

        print(f"Enqueued {len(compute_kickoff_clusters(games, season, week))} kickoff cluster(s) x 3 tasks for {season} week {week}.")
```

(`compute_kickoff_clusters()` itself is unchanged and still used by the print statement above for the cluster count — no need to remove it, since `compute_kickoff_clusters_with_games()` is a superset used only where the game_ids are actually needed.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: PASS

- [ ] **Step 5: Measure the scoped mode's real runtime and finalize `REPREDICT_LEAD_MINUTES`**

Once Task 4 is deployed (or runnable locally against real `rawdata/`), time a real invocation:

```bash
time python scripts/cache_builder.py --games <a_real_game_id> --skip-sync
```

If the measured wall-clock time plus a safety margin (at least 2x measured, to account for cold model loads on Cloud Run) exceeds `REPREDICT_LEAD_MINUTES` (70) minutes, increase the constant in `scripts/schedule_kickoffs.py` accordingly and re-run this plan's Step 4 to confirm tests still pass (they assert behavior, not the exact constant value, so they should be unaffected either way).

- [ ] **Step 6: Commit**

```bash
git add scripts/schedule_kickoffs.py tests/test_schedule_kickoffs.py
git commit -m "feat: enqueue scoped ESPN-aware repredict close to kickoff"
```

---

### Task 6: Retrain NN v15 / XGB v9 / LR v7

**Files:**
- No new files — runs existing training/evaluation scripts.

**Interfaces:**
- Consumes: `scripts/train_nn_model.py`, `scripts/weekly_model_eval.py`, `models/model_registry.json` / `models/xgb_registry.json` / `models/lr_registry.json` (all existing, unmodified by this task except the registries' own auto-increment).

- [ ] **Step 1: Confirm rawdata is current**

```bash
python scripts/sync_nflverse_data.py --seasons 2020 2026
python scripts/compute_elo.py
```

- [ ] **Step 2: Retrain the NN**

```bash
python scripts/train_nn_model.py
```

Expected: registers a new version (v15, since `models/model_registry.json`'s current `latest` is v14) in `models/model_registry.json`, produces `models/nn_v15.keras` + scaler.

- [ ] **Step 3: Retrain XGBoost**

```bash
python scripts/train_xgb_model.py
```

(Confirm the exact script name against `scripts/` — this repo's train scripts are per-model per CLAUDE.md's Commands section; use whichever exact filename trains the XGB model, following the same pattern as `train_nn_model.py`.)

Expected: registers v9 in `models/xgb_registry.json`.

- [ ] **Step 4: Retrain the LR model**

```bash
python scripts/train_lr_model.py
```

Expected: registers v7 in `models/lr_registry.json`.

- [ ] **Step 5: Evaluate the new ensemble against the existing baseline**

```bash
python scripts/weekly_model_eval.py --season 2025 --week 1 18
```

Compare the resulting `reports/nn_weekly_accuracy.csv` row for this run against the pre-retrain baseline already in that file. The new ensemble should not be meaningfully worse — if it is, do not promote it (leave `latest`/`best` pointing at v14/v8/v6 in the registries and investigate before proceeding; this is a real go/no-go gate, not a formality).

- [ ] **Step 6: Promote and backfill**

```bash
python scripts/backfill_schedule_predictions.py --force
python scripts/backfill_schedule_predictions.py --force --firestore
```

- [ ] **Step 7: Commit the new model artifacts and registries**

```bash
git add models/nn_v15.keras models/nn_v15_scaler.pkl models/model_registry.json \
        models/xgb_v9.json models/xgb_v9_scaler.pkl models/xgb_registry.json \
        models/lr_v7.pkl models/lr_v7_scaler.pkl models/lr_registry.json \
        reports/nn_weekly_accuracy.csv
git commit -m "feat: retrain NN v15 + XGB v9 + LR v7 (graded injury weighting + Aug 15 profile fixes)"
```

(Adjust the exact model/scaler filenames above to match whatever `train_nn_model.py`/etc. actually produce — check each script's own save-path convention if it differs from the `nn_v{N}.keras` pattern shown in CLAUDE.md's Module Layout section.)

---

## Self-Review Notes

- **Spec coverage:** Part A → Task 1. Plumbing needed to make Part A's data reach the model → Task 2. Part B's ESPN signal → Task 3. Part B's scoped repredict + publish-through-existing-store → Task 4. Part B's trigger/timing → Task 5. Bundled retrain → Task 6. The spec's "related finding" (dead `analytics_cache` reads) and all non-goals are explicitly *not* tasks, matching the spec.
- **Type consistency:** `espn_overrides`/`avail_mult` keyed as `Tuple[int, str]` (`(week, gsis_id)`) consistently across Task 1 (`_load_injury_report`, `compute_roster_value`), Task 2 (`build_master_feature_table`), and Task 3 (`get_espn_injury_overrides`'s return shape). `game_ids` are `list[str]` (nflverse `game_id` values) consistently across Task 4 and Task 5.
- **No placeholders:** `REPREDICT_LEAD_MINUTES`'s value (70) is a real, reasoned default (kickoff−90min official deadline + 20min margin), not a TBD — Task 5 Step 5 makes tuning it an explicit, concrete follow-up action rather than leaving it unset.
