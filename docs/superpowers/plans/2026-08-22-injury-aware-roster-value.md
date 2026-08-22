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
        # Two teams (KC, DEN), each with one strong QB established via
        # >= MIN_QB_ATTEMPTS. TWO teams is required, not incidental: compute_roster_value()
        # z-scores off_roster_value across all teams present for that week
        # (services/roster_value_service.py::_zscore), and a single-team
        # series has std=0 -- _zscore's own code returns a flat 0.0 for
        # every team in that case, which would make this test's assertion
        # vacuously true/false regardless of the injury weighting. DEN's
        # QB2 is identical in every other respect and never injured, so it
        # exists purely to give z-scoring something to differentiate KC
        # against.
        prior = tmp_path / "stats_player"
        prior.mkdir(parents=True, exist_ok=True)
        rows = []
        for pid, epa in [("QB1", 5.0), ("QB2", 3.0)]:
            for wk in range(1, 18):
                rows.append({
                    "player_id": pid, "position": "QB", "season_type": "REG", "week": wk,
                    "passing_epa": epa, "attempts": 30,
                })
        pd.DataFrame(rows).to_csv(prior / f"stats_player_week_{season - 1}.csv", index=False)
        pd.DataFrame(columns=["player_id", "position", "season_type", "week",
                               "passing_epa", "attempts"]).to_csv(
            prior / f"stats_player_week_{season}.csv", index=False)

        rosters = tmp_path / "weekly_rosters"
        rosters.mkdir(parents=True, exist_ok=True)
        roster_rows = []
        for wk in range(1, 5):
            roster_rows.append({
                "season": season, "week": wk, "team": "KC", "gsis_id": "QB1",
                "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
            })
            roster_rows.append({
                "season": season, "week": wk, "team": "DEN", "gsis_id": "QB2",
                "position": "QB", "status": "ACT", "birth_date": "1995-01-01",
            })
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

Add to `tests/test_nn_feature_engine.py`. Reuses the existing
`_make_minimal_feature_table_inputs(tmp_path)` helper already defined in
this file (around line 207 — writes minimal `stats_team`/`schedules`/
`elo_computed.csv` fixtures and returns the `tmp_path` root to pass as
`rawdata_dir`) — do not invent a new fixture helper:

```python
def test_build_master_feature_table_threads_espn_overrides(tmp_path, monkeypatch):
    """espn_overrides must reach compute_roster_value() unchanged -- this is
    a plumbing test, not a behavior test (Task 1 already covers the actual
    override-precedence behavior inside compute_roster_value())."""
    from services.nn_feature_engine import build_master_feature_table

    captured = {}

    def fake_compute_rv(season, rd, espn_overrides=None):
        captured["espn_overrides"] = espn_overrides
        return {}

    monkeypatch.setattr(
        "services.roster_value_service.compute_roster_value", fake_compute_rv
    )

    rd = _make_minimal_feature_table_inputs(tmp_path)
    overrides = {(3, "QB1"): 0.0}
    build_master_feature_table(
        rawdata_dir=str(rd), min_season=2024, max_season=2024,
        espn_overrides=overrides,
    )
    assert captured["espn_overrides"] == overrides
```

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

### Task 4: Week-aware roster value in `NNProjectionEngine`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Test: `tests/test_simulate_season.py`

**Interfaces:**
- Consumes: `compute_roster_value(target_season, rawdata_dir, espn_overrides=None) -> Dict[Tuple[int,int,str], dict]` (Task 1, returns `{(season, week, team): {off_roster_value, def_roster_value, st_value, qb_resilience}}`).
- Produces: `NNProjectionEngine.initialize(season: int, espn_overrides: Optional[Dict[Tuple[int, str], float]] = None)` — new optional 2nd parameter. New instance attributes `self._season: Optional[int]` and `self._roster_value_cache: Dict[Tuple[int, int, str], dict]`, consumed internally by `_precompute_static_features()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_simulate_season.py` (reuses the existing `mock_engine` fixture already in that file):

```python
class TestWeekAwareRosterValue:
    def test_precompute_static_features_uses_week_specific_cache(self, mock_engine):
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {
            (2025, 3, "STRONG"): {"off_roster_value": 2.0, "def_roster_value": 1.0,
                                   "st_value": 0.5, "qb_resilience": 0.9},
            (2025, 3, "WEAK"):   {"off_roster_value": -2.0, "def_roster_value": -1.0,
                                   "st_value": -0.5, "qb_resilience": 0.2},
        }
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        feat = static_feats["W03_STRONG_WEAK"]
        col_idx = {c: i for i, c in enumerate(NN_FC)}

        assert feat[col_idx["off_roster_value_delta"]] == pytest.approx(4.0)
        assert feat[col_idx["def_roster_value_delta"]] == pytest.approx(2.0)
        assert feat[col_idx["st_value_delta"]]         == pytest.approx(1.0)
        assert feat[col_idx["qb_resilience_delta"]]    == pytest.approx(0.7)

    def test_precompute_static_features_ignores_other_weeks(self, mock_engine):
        """A cache entry for a DIFFERENT week than the game being featured must
        not leak in -- this is what makes the blend actually week-aware instead
        of just season-aware."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {
            (2025, 9, "STRONG"): {"off_roster_value": 99.0},  # week 9, not week 3
        }
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["off_roster_value_delta"]] == pytest.approx(0.0)

    def test_precompute_static_features_defaults_to_zero_when_cache_empty(self, mock_engine):
        """Graceful degradation: an empty/missing roster-value cache (e.g.
        compute_roster_value() failed) must not crash -- deltas fall back to 0.0,
        same as every other hp.get(col, 0.0) default in this method."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {}
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["off_roster_value_delta"]] == pytest.approx(0.0)

    def test_roster_talent_delta_still_uses_team_profiles_not_roster_value_cache(self, mock_engine):
        """roster_talent_delta is a separate, performance-grade-based feature
        computed in build_master_feature_table() -- NOT part of
        compute_roster_value()'s output. It must keep reading from
        _team_profiles, unaffected by this task."""
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        mock_engine._season = 2025
        mock_engine._roster_value_cache = {}
        mock_engine._team_profiles.loc[
            mock_engine._team_profiles["team"] == "STRONG", "roster_talent_delta"
        ] = 5.0
        schedule = pd.DataFrame([
            {"home_team": "STRONG", "away_team": "WEAK", "week": 3, "game_type": "REG"},
        ])
        static_feats = mock_engine._precompute_static_features(schedule)
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        assert static_feats["W03_STRONG_WEAK"][col_idx["roster_talent_delta"]] == pytest.approx(5.0)


class TestInitializeBuildsRosterValueCache:
    def test_initialize_computes_and_threads_espn_overrides(self):
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine, RAWDATA_DIR

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        captured = {}

        def fake_compute_rv(season, rawdata_dir, espn_overrides=None):
            captured["args"] = (season, rawdata_dir, espn_overrides)
            return {(2025, 1, "KC"): {"off_roster_value": 1.0}}

        overrides = {(1, "QB1"): 0.0}
        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=fake_compute_rv):
            engine.initialize(2025, espn_overrides=overrides)

        assert captured["args"] == (2025, RAWDATA_DIR, overrides)
        assert engine._roster_value_cache == {(2025, 1, "KC"): {"off_roster_value": 1.0}}
        assert engine._season == 2025

    def test_initialize_defaults_espn_overrides_to_none(self):
        """Existing callers that don't pass espn_overrides must be unaffected."""
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        captured = {}

        def fake_compute_rv(season, rawdata_dir, espn_overrides=None):
            captured["espn_overrides"] = espn_overrides
            return {}

        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=fake_compute_rv):
            engine.initialize(2025)

        assert captured["espn_overrides"] is None

    def test_initialize_degrades_gracefully_when_compute_roster_value_fails(self):
        from unittest.mock import patch
        import pandas as pd
        from services.nn_projection_engine import NNProjectionEngine

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            engine = NNProjectionEngine()

        with patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch.object(engine, "_build_team_profiles",
                         return_value=pd.DataFrame(columns=["team"])), \
             patch("services.roster_value_service.compute_roster_value",
                   side_effect=Exception("rawdata unavailable")):
            engine.initialize(2025)  # must not raise

        assert engine._roster_value_cache == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_simulate_season.py::TestWeekAwareRosterValue tests/test_simulate_season.py::TestInitializeBuildsRosterValueCache -v`
Expected: FAIL — `_roster_value_cache`/`_season` don't exist yet; `initialize()` doesn't accept `espn_overrides`.

- [ ] **Step 3: Implement**

In `services/nn_projection_engine.py`, add `Tuple` to the typing import:

```python
from typing import Dict, List, Optional, Tuple
```

In `__init__`, add two new default attributes right after `self._team_profiles = pd.DataFrame()`:

```python
        self._team_profiles = pd.DataFrame()
        self._season: Optional[int] = None
        self._roster_value_cache: Dict[Tuple[int, int, str], dict] = {}
```

Change `initialize()`'s signature and add the roster-value cache computation:

```python
    def initialize(self, season: int, espn_overrides: Optional[Dict[Tuple[int, str], float]] = None):
        """Pre-compute the feature profiles required for predictions.

        Args:
            season: The target NFL season (e.g. 2026).
            espn_overrides: Optional {(week, gsis_id): availability_weight} from
                services.espn_injury_service, applied on top of this season's
                own nflverse-graded weekly injury report (see
                services/roster_value_service.py) when building the per-week
                roster-value cache below.
        """
        self._season = season
        feature_table = build_master_feature_table(min_season=2020, max_season=season - 1)
        self._team_profiles = self._build_team_profiles(feature_table, season - 1)

        # Week-aware roster value for the TARGET season itself (not the
        # prior-season feature_table above) -- compute_roster_value() already
        # blends prior-season into current-season per player as games
        # accumulate, and (since Part A) grades that blend by real injury
        # severity. _precompute_static_features() looks this up per
        # (week, team) instead of the flat prior-season _team_profiles
        # average, which is what actually lets in-season form and injuries
        # move a season-simulation prediction (see
        # docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md,
        # Part B0).
        try:
            from services.roster_value_service import compute_roster_value
            self._roster_value_cache = compute_roster_value(
                season, RAWDATA_DIR, espn_overrides=espn_overrides,
            )
        except Exception as exc:
            logger.warning("Week-aware roster value unavailable for %d: %s", season, exc)
            self._roster_value_cache = {}

        snap_path = RAWDATA_DIR / "snap_counts" / f"snap_counts_{season}.csv"
        snap_empty = not snap_path.exists() or pd.read_csv(snap_path, nrows=1).empty
        if snap_empty:
            try:
                self._preseason_profiles = compute_preseason_player_profiles(season, RAWDATA_DIR)
                if self._preseason_profiles:
                    logger.info(
                        "Preseason player profiles built for %d teams (season %d)",
                        len(self._preseason_profiles), season,
                    )
            except Exception as exc:
                logger.warning("Preseason player profile build failed: %s", exc)
                self._preseason_profiles = {}
```

In `_precompute_static_features()`, replace the "Roster value deltas" block (the 5 lines starting `feat[col_idx["roster_talent_delta"]] = (` through `feat[col_idx["qb_resilience_delta"]] = float(hp.get("qb_resilience_delta", 0.0))`) with:

```python
            # Roster value: week-aware per-team lookup from
            # roster_value_service.compute_roster_value() (already alpha-blended
            # prior->current per player and, since Part A, injury-graded) instead
            # of the flat prior-season _team_profiles average used everywhere
            # else in this method -- this is what lets a team's in-season form
            # and injuries actually move this season-simulation's per-game
            # prediction (see
            # docs/superpowers/specs/2026-08-22-injury-aware-roster-value-design.md,
            # Part B0). roster_talent_delta is intentionally untouched here --
            # it's a separate, performance-grade-based feature computed in
            # build_master_feature_table(), not part of compute_roster_value()'s
            # output.
            h_rv = self._roster_value_cache.get((self._season, int(wk), ht), {})
            a_rv = self._roster_value_cache.get((self._season, int(wk), at), {})
            feat[col_idx["roster_talent_delta"]]     = (
                float(hp.get("roster_talent_delta", 0.0)) - float(ap.get("roster_talent_delta", 0.0))
            )
            feat[col_idx["off_roster_value_delta"]]  = float(
                h_rv.get("off_roster_value", 0.0) - a_rv.get("off_roster_value", 0.0)
            )
            feat[col_idx["def_roster_value_delta"]]  = float(
                h_rv.get("def_roster_value", 0.0) - a_rv.get("def_roster_value", 0.0)
            )
            feat[col_idx["st_value_delta"]]          = float(
                h_rv.get("st_value", 0.0) - a_rv.get("st_value", 0.0)
            )
            feat[col_idx["qb_resilience_delta"]]     = float(
                h_rv.get("qb_resilience", 0.0) - a_rv.get("qb_resilience", 0.0)
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_simulate_season.py -v`
Expected: PASS — all tests in the file, including the new `TestWeekAwareRosterValue`/`TestInitializeBuildsRosterValueCache` classes and every pre-existing test (the `_pp_z` preseason-profile override block, which runs only when `_preseason_profiles` is populated, is untouched by this change).

- [ ] **Step 5: Run the broader NN projection test suite to check for regressions**

Run: `pytest tests/test_nn_projection_engine.py tests/test_simulate_season.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add services/nn_projection_engine.py tests/test_simulate_season.py
git commit -m "feat: make simulate_season's roster-value features week-aware and injury-gradeable"
```

---

### Task 5: Wire `cache_builder.py`'s upcoming-game prediction to `simulate_season()`

**Files:**
- Modify: `scripts/cache_builder.py`
- Test: `tests/test_cache_builder.py`

**Interfaces:**
- Consumes: `NNProjectionEngine.simulate_season(schedule_df, n_sims, completed_results) -> {"team_stats": ..., "game_probs": {game_key: {mean_prob, model_spread, home_team, away_team, week}}}` (existing, unmodified signature — Task 4 changed its internals, not its interface).
- Produces: `_build_completed_results(games: pd.DataFrame, year: int) -> dict` — `{game_key: margin}`, reused by Task 6.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cache_builder.py` (add `from unittest.mock import patch, MagicMock` at the top if not already present — it already is, per the existing `@patch` decorators):

```python
class TestBuildCompletedResults:
    def test_extracts_completed_reg_games_only(self):
        from scripts.cache_builder import _build_completed_results
        games = pd.DataFrame([
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "WAS",
             "away_team": "KC", "result": 3.0},
            {"season": 2026, "week": 4, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": None},
            {"season": 2026, "week": 3, "game_type": "POST", "home_team": "DAL",
             "away_team": "NYG", "result": -7.0},
        ])
        result = _build_completed_results(games, 2026)
        assert result == {"W03_WAS_KC": 3.0}

    def test_filters_to_requested_season(self):
        from scripts.cache_builder import _build_completed_results
        games = pd.DataFrame([
            {"season": 2025, "week": 3, "game_type": "REG", "home_team": "WAS",
             "away_team": "KC", "result": 3.0},
            {"season": 2026, "week": 3, "game_type": "REG", "home_team": "SF",
             "away_team": "LAC", "result": -7.0},
        ])
        result = _build_completed_results(games, 2026)
        assert result == {"W03_SF_LAC": -7.0}


class TestApplyPredictionsFallback:
    def test_unplayed_game_uses_simulate_season_not_batch_method(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        fallback_engine = MagicMock()
        fallback_engine.simulate_season.return_value = {
            "game_probs": {
                "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                               "home_team": "WAS", "away_team": "KC", "week": 3},
            },
            "team_stats": {},
        }
        out = _apply_predictions(schedule, 2026, {}, fallback_engine=fallback_engine)

        fallback_engine.simulate_season.assert_called_once()
        fallback_engine.game_win_probabilities_batch.assert_not_called()
        assert out.iloc[0]["pred_winner"] == "WAS"
        assert out.iloc[0]["pred_prob"] == 0.62

    def test_completed_game_never_touches_fallback_engine(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": 3.0},
        ])
        pred_lookup = {(2026, 3, "WAS", "KC"): {
            "pred_winner": "WAS", "pred_su_conf": 70.0,
            "pred_ats_pick": "WAS", "pred_prob": 0.7,
        }}
        fallback_engine = MagicMock()
        out = _apply_predictions(schedule, 2026, pred_lookup, fallback_engine=fallback_engine)
        fallback_engine.simulate_season.assert_not_called()
        assert out.iloc[0]["pred_winner"] == "WAS"

    def test_simulate_season_failure_leaves_predictions_none_not_raises(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None},
        ])
        fallback_engine = MagicMock()
        fallback_engine.simulate_season.side_effect = Exception("model unavailable")
        out = _apply_predictions(schedule, 2026, {}, fallback_engine=fallback_engine)
        assert out.iloc[0]["pred_winner"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder.py::TestBuildCompletedResults tests/test_cache_builder.py::TestApplyPredictionsFallback -v`
Expected: FAIL — `_build_completed_results` doesn't exist; `_apply_predictions` still calls `game_win_probabilities_batch`.

- [ ] **Step 3: Implement**

In `scripts/cache_builder.py`, add near `_build_pred_lookup`:

```python
SIMULATE_SEASON_N_SIMS = 5000


def _build_completed_results(games: pd.DataFrame, year: int) -> dict:
    """{game_key: margin} for every completed REG game in `year` -- the shape
    NNProjectionEngine.simulate_season() expects for its completed_results
    parameter. Reused by the --resimulate mode (Task 6)."""
    completed = {}
    yr_games = games[games["season"] == year] if "season" in games.columns else games
    for _, row in yr_games.iterrows():
        res = row.get("result")
        if pd.notna(res) and res != UNDRAFTED_SENTINEL and row.get("game_type") == "REG":
            ht = _normalize_team(str(row.get("home_team", "") or ""))
            at = _normalize_team(str(row.get("away_team", "") or ""))
            wk = row.get("week")
            if ht and at and wk is not None:
                completed[f"W{int(wk):02d}_{ht}_{at}"] = float(res)
    return completed
```

Replace `_apply_predictions()`'s fallback branch (from `fallback_idx, fallback_pairs, fallback_spreads = [], [], []` through the end of the `if fallback_pairs:` block) with:

```python
def _apply_predictions(schedule_df: pd.DataFrame, year: int, pred_lookup: dict,
                       fallback_engine=None) -> pd.DataFrame:
    """Inject ML predictions into every row of schedule_df.

    For games found in pred_lookup (feature-table predictions), uses those.
    For unplayed games not in pred_lookup, falls back to fallback_engine's
    NNProjectionEngine.simulate_season() -- run ONCE across every unplayed row
    (not per-row), seeded with completed_results from this schedule's own
    real scores so the state feeding each future week's prediction has
    already absorbed every real result up to that point. Completed games
    with no feature data get None. If simulate_season() itself fails, every
    pending row gets None rather than being retried individually -- a
    systemic model/scaler failure isn't going to resolve itself on the next
    row.
    """
    n = len(schedule_df)
    pred_winners: list = [None] * n
    pred_confs: list = [None] * n
    pred_ats: list = [None] * n
    pred_probs: list = [None] * n

    unplayed_idx: list = []

    for i, (_, row) in enumerate(schedule_df.iterrows()):
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')

        pred = pred_lookup.get((year, int(wk), ht, at)) if (ht and at and wk is not None) else None

        if pred:
            pred_winners[i] = pred['pred_winner']
            pred_confs[i]   = pred['pred_su_conf']
            pred_ats[i]     = pred['pred_ats_pick']
            pred_probs[i]   = pred['pred_prob']
            continue

        # Not in feature table — unplayed future game, queue for simulate_season()
        result = row.get('result')
        is_unplayed = pd.isna(result) or result == UNDRAFTED_SENTINEL
        if is_unplayed and ht and at and fallback_engine:
            unplayed_idx.append(i)

    if unplayed_idx and fallback_engine:
        try:
            completed_results = _build_completed_results(schedule_df, year)
            sim = fallback_engine.simulate_season(
                schedule_df, n_sims=SIMULATE_SEASON_N_SIMS, completed_results=completed_results,
            )
            game_probs = sim.get("game_probs", {})
        except Exception:
            game_probs = {}

        for i in unplayed_idx:
            row = schedule_df.iloc[i]
            ht = _normalize_team(str(row.get('home_team', '') or ''))
            at = _normalize_team(str(row.get('away_team', '') or ''))
            wk = row.get('week')
            if not (ht and at and wk is not None):
                continue
            key = f"W{int(wk):02d}_{ht}_{at}"
            gp = game_probs.get(key)
            if not gp:
                continue
            hp = gp['mean_prob']
            ms = gp['model_spread']
            winner = ht if hp >= 0.5 else at
            conf = round(max(hp, 1.0 - hp) * 100, 1)
            spread = row.get('spread_line')
            ats = winner
            if pd.notna(spread):
                try:
                    sl_val = float(spread)
                    ats = ht if ms > sl_val else at
                except (ValueError, TypeError):
                    pass
            pred_winners[i] = winner
            pred_confs[i]   = conf
            pred_ats[i]     = ats
            pred_probs[i]   = round(hp, 4)

    out = schedule_df.copy()
    out['pred_winner']  = pred_winners
    out['pred_su_conf'] = pred_confs
    out['pred_ats_pick'] = pred_ats
    out['pred_prob']    = pred_probs
    return out
```

`build_year()`'s call site (`fallback_engine = _get_engine() if year >= current_year else None`, then `_apply_predictions(schedule_df, year, pred_lookup, fallback_engine=fallback_engine)`) is unchanged — only `_apply_predictions()`'s internals change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS

- [ ] **Step 5: Run the full cache_builder test suite to check for regressions**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS — the completed-game path (`pred_lookup` hits) is untouched code.

- [ ] **Step 6: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "feat: wire cache_builder's upcoming-game fallback to simulate_season + real results"
```

---

### Task 6: ESPN-aware re-simulate mode + `schedule_kickoffs.py` trigger

**Files:**
- Modify: `scripts/cache_builder.py`
- Modify: `scripts/schedule_kickoffs.py`
- Test: `tests/test_cache_builder.py`
- Test: `tests/test_schedule_kickoffs.py`

**Interfaces:**
- Consumes: `get_espn_injury_overrides(target_games, season, week, rawdata_dir)` (Task 3), `NNProjectionEngine.initialize(season, espn_overrides=...)` and `.simulate_season(...)` (Task 4), `_build_completed_results(games, year)` (Task 5).
- Produces: `_publish_game_probs(game_ids: list, games: pd.DataFrame, year: int, game_probs: dict) -> int`; a new `--resimulate <comma-separated-game-ids>` CLI arg on `cache_builder.py`'s `main()`; `enqueue_task(tasks_client, run_at, job_name, job_args=None)` (existing function, gains a 4th optional parameter); `compute_kickoff_clusters_with_games(games, season, week) -> list[tuple[datetime, list]]` (new).

- [ ] **Step 1: Write the failing tests for `cache_builder.py`**

Add to `tests/test_cache_builder.py`:

```python
def _games_df():
    return pd.DataFrame([
        {"game_id": "2026_03_KC_WAS", "season": 2026, "week": 3,
         "home_team": "WAS", "away_team": "KC", "game_type": "REG"},
        {"game_id": "2026_03_SF_LAC", "season": 2026, "week": 3,
         "home_team": "LAC", "away_team": "SF", "game_type": "REG"},
    ])


class TestPublishGameProbs:
    def test_publishes_only_requested_game(self):
        from scripts.cache_builder import _publish_game_probs

        game_probs = {
            "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                           "home_team": "WAS", "away_team": "KC", "week": 3},
            "W03_LAC_SF": {"mean_prob": 0.55, "model_spread": -1.0,
                           "home_team": "LAC", "away_team": "SF", "week": 3},
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, game_probs)

        assert n == 1
        mock_write.assert_called_once()
        year, merged = mock_write.call_args[0]
        assert year == 2026
        assert "W03_WAS_KC" in merged
        assert "W03_LAC_SF" not in merged  # the other game was never touched

    def test_preserves_existing_richer_fields_for_untouched_games(self):
        from scripts.cache_builder import _publish_game_probs

        existing = {"W03_LAC_SF": {"model_spread": -3.5, "edge_vs_vegas": 1.2}}
        game_probs = {
            "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                           "home_team": "WAS", "away_team": "KC", "week": 3},
        }
        with patch("scripts.cache_builder.get_game_predictions", return_value=existing), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, game_probs)

        _year, merged = mock_write.call_args[0]
        assert merged["W03_LAC_SF"] == existing["W03_LAC_SF"]

    def test_no_matching_game_id_returns_zero(self):
        from scripts.cache_builder import _publish_game_probs
        with patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["nonexistent"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()

    def test_no_game_probs_entry_for_requested_game_returns_zero(self):
        from scripts.cache_builder import _publish_game_probs
        with patch("scripts.cache_builder.get_game_predictions", return_value={}), \
             patch("scripts.cache_builder.write_game_predictions") as mock_write:
            n = _publish_game_probs(["2026_03_KC_WAS"], _games_df(), 2026, {})
        assert n == 0
        mock_write.assert_not_called()


class TestResimulateModeWiring:
    def test_resimulate_flag_skips_full_multi_year_build(self, monkeypatch):
        """--resimulate must not call build_year() (the full standings/analytics
        rebuild) at all -- only the scoped ESPN-check + re-simulate + publish path."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year") as mock_build_year, \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("scripts.cache_builder._publish_game_probs", return_value=0) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()

        mock_build_year.assert_not_called()
        mock_engine_cls.return_value.initialize.assert_called_once()
        mock_publish.assert_called_once()

    def test_resimulate_flag_fetches_espn_overrides_and_passes_to_initialize(self, monkeypatch):
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("services.espn_injury_service.get_espn_injury_overrides",
                   return_value={(3, "QB1"): 0.0}) as mock_espn, \
             patch("scripts.cache_builder._publish_game_probs", return_value=1), \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()

        mock_espn.assert_called_once()
        init_kwargs = mock_engine_cls.return_value.initialize.call_args.kwargs
        assert init_kwargs["espn_overrides"] == {(3, "QB1"): 0.0}

    def test_resimulate_flag_espn_failure_still_publishes(self, monkeypatch):
        """ESPN fetch failing must not abort the re-simulate -- it just proceeds
        with no overrides, matching the established graceful-degradation pattern."""
        import sys
        from scripts.cache_builder import main

        monkeypatch.setattr(sys, "argv", ["cache_builder.py", "--resimulate", "2026_03_KC_WAS", "--skip-sync"])
        with patch("scripts.cache_builder.load_data") as mock_load_data, \
             patch("scripts.cache_builder.build_year"), \
             patch("scripts.cache_builder.NNProjectionEngine") as mock_engine_cls, \
             patch("services.espn_injury_service.get_espn_injury_overrides",
                   side_effect=Exception("ESPN down")), \
             patch("scripts.cache_builder._publish_game_probs", return_value=1) as mock_publish, \
             patch("scripts.cache_builder._fs"):
            mock_load_data.return_value = (
                pd.DataFrame(), pd.DataFrame(), _games_df(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            )
            mock_engine_cls.return_value.simulate_season.return_value = {"game_probs": {}}
            main()  # must not raise

        mock_publish.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder.py::TestPublishGameProbs tests/test_cache_builder.py::TestResimulateModeWiring -v`
Expected: FAIL — `_publish_game_probs` doesn't exist; `main()` has no `--resimulate` argument; `NNProjectionEngine` isn't imported into `cache_builder.py` yet.

- [ ] **Step 3: Implement the `cache_builder.py` side**

`NNProjectionEngine` is already imported at module level (`from services.nn_projection_engine import NNProjectionEngine`, used by `_get_engine()` inside `build_year()`) — no new import needed.

Add near `_build_completed_results`:

```python
RESIMULATE_N_SIMS = 2000


def _publish_game_probs(game_ids: list, games: pd.DataFrame, year: int, game_probs: dict) -> int:
    """Publish only game_ids' entries from a simulate_season() game_probs_out
    dict into game_predictions, via the same merge-preserving path
    build_year() already uses -- every other stored game (including richer
    fields like model_spread/edge_vs_vegas the merge preserves) is untouched.
    """
    target = games[games["game_id"].astype(str).isin(game_ids)].copy()
    if target.empty:
        print(f"[cache_builder] --resimulate: no matching rows for {game_ids}")
        return 0

    pmap = {}
    for _, row in target.iterrows():
        ht = _normalize_team(str(row.get('home_team', '') or ''))
        at = _normalize_team(str(row.get('away_team', '') or ''))
        wk = row.get('week')
        if not (ht and at and wk is not None):
            continue
        key = f"W{int(wk):02d}_{ht}_{at}"
        gp = game_probs.get(key)
        if not gp:
            continue
        hp = gp['mean_prob']
        ms = gp['model_spread']
        winner = ht if hp >= 0.5 else at
        conf = round(max(hp, 1.0 - hp) * 100, 1)
        spread = row.get('spread_line')
        ats = winner
        if pd.notna(spread):
            try:
                sl_val = float(spread)
                ats = ht if ms > sl_val else at
            except (ValueError, TypeError):
                pass
        pmap[key] = {
            'pred_prob':     round(hp, 4),
            'pred_winner':   winner,
            'pred_su_conf':  conf,
            'pred_ats_pick': ats,
            'model_spread':  ms,
        }

    if not pmap:
        print("[cache_builder] --resimulate: no predictions produced for requested games")
        return 0

    existing = get_game_predictions(year)
    merged = merge_thin_game_predictions(existing, pmap)
    write_game_predictions(year, merged)
    return len(pmap)
```

Add the CLI argument to `main()`'s `argparse` block:

```python
    parser.add_argument('--resimulate', type=str, default=None,
                        help="Comma-separated game_ids to re-run simulate_season() for "
                             "with a fresh ESPN injury check, publishing only those "
                             "games' predictions. For the close-to-kickoff last-mile refresh.")
```

Insert the new branch in `main()`, right after `standings, teams, games, players, draft_order, draft_results, draft_order_rules = load_data()` and before `available_years = get_available_years(draft_results)`:

```python
    if args.resimulate:
        game_ids = [g.strip() for g in args.resimulate.split(",") if g.strip()]
        target_rows = games[games["game_id"].astype(str).isin(game_ids)]
        if target_rows.empty:
            print(f"[cache_builder] --resimulate: no matching rows for {game_ids}; nothing to do")
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

        engine = NNProjectionEngine()
        engine.initialize(year, espn_overrides=espn_overrides)

        yr_games = games[games["season"] == year].copy()
        completed_results = _build_completed_results(yr_games, year)
        sim = engine.simulate_season(yr_games, n_sims=RESIMULATE_N_SIMS, completed_results=completed_results)

        n = _publish_game_probs(game_ids, games, year, sim.get("game_probs", {}))
        print(f"[cache_builder] --resimulate: published {n} prediction(s).")

        db = _fs.client()
        db.collection("metadata").document("cache_control").set({"last_update": time.time()})
        return

    available_years = get_available_years(draft_results)
```

- [ ] **Step 4: Run the `cache_builder.py` tests**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing tests for `schedule_kickoffs.py`**

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
                      job_args=["--resimulate", "2026_03_KC_WAS"])

        task = client.create_task.call_args.kwargs["request"]["task"]
        assert "body" in task["http_request"]
        import json
        body = json.loads(task["http_request"]["body"])
        assert body["overrides"]["containerOverrides"][0]["args"] == ["--resimulate", "2026_03_KC_WAS"]

    def test_no_job_args_omits_body(self, monkeypatch):
        """Existing sync/predict calls (no job_args) must be unaffected -- no
        body means the job runs with its normal configured command."""
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

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: FAIL — `enqueue_task()` doesn't accept `job_args`; `compute_kickoff_clusters_with_games` doesn't exist.

- [ ] **Step 7: Implement the `schedule_kickoffs.py` side**

Add near `PREDICT_LEAD_MINUTES`:

```python
PREDICT_LEAD_MINUTES = 60
# How close to kickoff the ESPN check + re-simulate runs. Anchored to the
# NFL's official inactive-list deadline (kickoff-90min, league rule) -- this
# leaves 20 minutes of margin after that deadline before this task fires.
# NOT yet validated against a measured runtime of --resimulate (Task 6) in
# production: before relying on this in-season, time a real invocation (see
# Step 9 below) and adjust this constant if it runs longer than the margin
# allows.
RESIMULATE_LEAD_MINUTES = 70
```

Add `compute_kickoff_clusters_with_games()` next to `compute_kickoff_clusters()`:

```python
def compute_kickoff_clusters_with_games(games: pd.DataFrame, season: int, week: int) -> list[tuple[datetime, list]]:
    """Same clustering as compute_kickoff_clusters(), but paired with each
    cluster's game_ids so the caller knows what to pass to --resimulate."""
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
        # Cloud Run Jobs Admin API's :run RunJobRequest body -- overrides the
        # container's configured args for just this execution, so the
        # re-simulate can reuse winspool-predict-daily's existing job instead
        # of provisioning a new one.
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
        clusters_with_games = compute_kickoff_clusters_with_games(games, season, week)
        for kickoff, game_ids in clusters_with_games:
            enqueue_task(client, kickoff - timedelta(minutes=SYNC_LEAD_MINUTES), "winspool-sync-daily")
            enqueue_task(client, kickoff - timedelta(minutes=PREDICT_LEAD_MINUTES), "winspool-predict-daily")
            enqueue_task(
                client, kickoff - timedelta(minutes=RESIMULATE_LEAD_MINUTES), "winspool-predict-daily",
                job_args=["--resimulate", ",".join(str(g) for g in game_ids)],
            )

        print(f"Enqueued {len(clusters_with_games)} kickoff cluster(s) x 3 tasks for {season} week {week}.")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_schedule_kickoffs.py -v`
Expected: PASS

- [ ] **Step 9: Measure the re-simulate mode's real runtime and finalize `RESIMULATE_LEAD_MINUTES`**

Once this task is deployed (or runnable locally against real `rawdata/`), time a real invocation:

```bash
time python scripts/cache_builder.py --resimulate <a_real_game_id> --skip-sync
```

If the measured wall-clock time plus a safety margin (at least 2x measured, to account for cold model loads on Cloud Run) exceeds `RESIMULATE_LEAD_MINUTES` (70) minutes, increase the constant in `scripts/schedule_kickoffs.py` accordingly and re-run this task's tests to confirm they still pass (they assert behavior, not the exact constant value).

- [ ] **Step 10: Commit**

```bash
git add scripts/cache_builder.py scripts/schedule_kickoffs.py tests/test_cache_builder.py tests/test_schedule_kickoffs.py
git commit -m "feat: ESPN-aware re-simulate mode enqueued close to kickoff"
```

---

### Task 7: Retrain NN v15 / XGB v9 / LR v7

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

- **Spec coverage:** Part A → Task 1. Plumbing needed to make Part A's data reach the model → Task 2. Part B's ESPN signal → Task 3. Part B0 (the discovered prerequisite — week-aware roster value inside `NNProjectionEngine`) → Task 4. Part B0's daily-job wiring (`simulate_season()` + real results replacing the static fallback) → Task 5. Part B's ESPN-aware re-simulate + trigger/timing → Task 6. Bundled retrain → Task 7. The spec's "related finding" (dead `analytics_cache` reads) and all non-goals are explicitly *not* tasks, matching the spec.
- **Revision history:** Tasks 4-7 were rewritten after Task 4's original design (a scoped `--games` feature-table repredict) was reviewed and found unable to work — `build_master_feature_table()` unconditionally drops unplayed games, so that design could never repredict the very games Part B exists to serve. The original Task 4 commit was reverted (`git revert`, preserved in history) rather than force-edited in place. See the spec's "Part B0" section for the full trace.
- **Type consistency:** `espn_overrides`/`avail_mult` keyed as `Tuple[int, str]` (`(week, gsis_id)`) consistently across Task 1 (`_load_injury_report`, `compute_roster_value`), Task 2 (`build_master_feature_table`), Task 3 (`get_espn_injury_overrides`'s return shape), and Task 4 (`NNProjectionEngine.initialize`). The roster-value cache is keyed `Tuple[int, int, str]` (`(season, week, team)`) consistently between Task 1's `compute_roster_value()` return shape and Task 4's `_precompute_static_features()` lookup. `game_ids` are `list[str]` (nflverse `game_id` values) consistently across Task 6. `game_probs`/`game_probs_out` entries (`{mean_prob, model_spread, home_team, away_team, week}`) are used identically by Task 5's `_apply_predictions()` and Task 6's `_publish_game_probs()`.
- **No placeholders:** `RESIMULATE_LEAD_MINUTES`'s value (70) is a real, reasoned default (kickoff−90min official deadline + 20min margin), not a TBD — Task 6 Step 9 makes tuning it an explicit, concrete follow-up action rather than leaving it unset. `SIMULATE_SEASON_N_SIMS` (5000, Task 5) and `RESIMULATE_N_SIMS` (2000, Task 6) are both real, functioning defaults with a stated rationale (daily-job thoroughness vs. last-mile refresh speed), not guesses left for later.
