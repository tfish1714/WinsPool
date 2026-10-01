# QB Availability + Prediction Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead QB-starter-flag code with a real "sticky reference starter" signal that feeds the model, fix the roster-snapshot drift that corrupts already-graded predictions, keep the betting screener's `explanation` data in sync with the top-level prediction fields, and add a weekly job that grades each week's predictions once it's actually over.

**Architecture:** One new function (`compute_qb_availability_flags`) in `services/nn_feature_engine.py`, built from four small private loaders (depth chart, injury report, reserve status, snap share), consumed by three call sites (historical feature table, live projection engine, backfill's display fallback). A second, independent change threads a `week` parameter through the existing roster-profile function so historical grading uses that week's own roster snapshot instead of "whatever the roster file says today." A third change carries the `explanation` dict through the daily cache-builder write instead of dropping it. A fourth adds a Tuesday-only subprocess call to the existing daily prediction job.

**Tech Stack:** Python, pandas, pytest (existing test suite conventions — `tmp_path` fixtures for rawdata CSVs, `unittest.mock.patch`/`MagicMock` for service injection).

**Spec:** `docs/superpowers/specs/2026-09-17-qb-availability-and-prediction-freshness-design.md` (read this first — it has the full rationale, evidence, and decided edge cases; this plan only restates what's needed to implement each task).

## Global Constraints

- No new feature column, no model retraining (spec Part A) — the QB availability signal is OR'd into the existing `home_qb_injury_flag`/`away_qb_injury_flag`.
- Snap-count leg of the availability check only applies to already-played weeks (spec Part A, step 3) — never for a future/unplayed game.
- Historical roster snapshot (Part B) only changes the *completed-game* grading path; `NNProjectionEngine.simulate_season()`'s future-game path must keep using the latest roster snapshot unchanged.
- Fail open on missing/unmatched data throughout (log a warning, return `0.0`/skip) — matches every existing loader in `nn_feature_engine.py`.
- All new pandas-based loaders live in `services/nn_feature_engine.py` and follow its existing helper conventions (`_normalize_team`, `_load_multi_season`, `_read_csv_safe`).

---

## Task 1: Depth-chart declared-starter loader

**Files:**
- Modify: `services/nn_feature_engine.py` (add `_load_declared_starters`, near `_load_schedule` at line 1509)
- Test: `tests/test_nn_feature_engine.py` (new `TestLoadDeclaredStarters` class)

**Interfaces:**
- Produces: `_load_declared_starters(rd: Path) -> pd.DataFrame` with columns `season, week, team, gsis_id` — one row per (season, week, team) naming that week's depth-chart-declared starting QB.

- [ ] **Step 1: Write the failing tests**

```python
class TestLoadDeclaredStarters:
    def test_old_schema_uses_own_week_directly(self, tmp_path):
        """Pre-2025 depth charts are already per-week -- no dt resolution needed."""
        from services.nn_feature_engine import _load_declared_starters
        dc_dir = tmp_path / "depth_charts"
        dc_dir.mkdir()
        pd.DataFrame([
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Alpha", "gsis_id": "00-0001", "depth_position": "QB"},
            {"club_code": "AAA", "week": 2, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Bravo", "gsis_id": "00-0002", "depth_position": "QB"},
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 2,
             "full_name": "QB Backup", "gsis_id": "00-0003", "depth_position": "QB"},
        ]).to_csv(dc_dir / "depth_charts_2023.csv", index=False)

        result = _load_declared_starters(tmp_path)
        row = result[(result["season"] == 2023) & (result["week"] == 1) & (result["team"] == "AAA")]
        assert row.iloc[0]["gsis_id"] == "00-0001"
        row2 = result[(result["season"] == 2023) & (result["week"] == 2) & (result["team"] == "AAA")]
        assert row2.iloc[0]["gsis_id"] == "00-0002"

    def test_new_schema_resolves_snapshot_before_kickoff(self, tmp_path):
        """New-schema depth charts are dt-timestamped, not week-indexed --
        resolve against the schedule's own kickoff date via merge_asof."""
        from services.nn_feature_engine import _load_declared_starters
        dc_dir = tmp_path / "depth_charts"
        dc_dir.mkdir()
        pd.DataFrame([
            {"dt": "2026-09-05T00:00:00Z", "team": "SEA", "player_name": "Sam Darnold",
             "gsis_id": "00-0100", "pos_abb": "QB", "pos_rank": 1},
            {"dt": "2026-09-14T00:00:00Z", "team": "SEA", "player_name": "Drew Lock",
             "gsis_id": "00-0101", "pos_abb": "QB", "pos_rank": 1},
        ]).to_csv(dc_dir / "depth_charts_2026.csv", index=False)

        sched_dir = tmp_path / "schedules"
        sched_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "home_team": "SEA", "away_team": "NE",
             "game_type": "REG", "gameday": "2026-09-09", "location": "Home"},
            {"season": 2026, "week": 2, "home_team": "ARI", "away_team": "SEA",
             "game_type": "REG", "gameday": "2026-09-20", "location": "Home"},
        ]).to_csv(sched_dir / "games.csv", index=False)

        result = _load_declared_starters(tmp_path)
        wk1 = result[(result["season"] == 2026) & (result["week"] == 1) & (result["team"] == "SEA")]
        assert wk1.iloc[0]["gsis_id"] == "00-0100"  # Sept 5 snapshot -- before Sept 9 kickoff
        wk2 = result[(result["season"] == 2026) & (result["week"] == 2) & (result["team"] == "SEA")]
        assert wk2.iloc[0]["gsis_id"] == "00-0101"  # Sept 14 snapshot -- before Sept 20 kickoff

    def test_missing_depth_charts_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_declared_starters
        result = _load_declared_starters(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id"]
        assert result.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nn_feature_engine.py::TestLoadDeclaredStarters -v`
Expected: FAIL with `ImportError: cannot import name '_load_declared_starters'`

- [ ] **Step 3: Implement `_load_declared_starters`**

Add after `_load_schedule` (services/nn_feature_engine.py, after line 1558):

```python
def _load_declared_starters(rd: Path) -> pd.DataFrame:
    """Depth-chart-declared starting QB per (season, week, team).

    Old schema (pre-2025: club_code/depth_team/week/game_type) is already
    per-week -- used directly, no timestamp resolution needed.
    New schema (2025+: dt/gsis_id) is a continuous snapshot timeline --
    resolved against the schedule's own earliest kickoff date per week via
    merge_asof, picking the latest snapshot strictly before that date.

    Returns columns: season, week, team, gsis_id.
    """
    empty = pd.DataFrame(columns=["season", "week", "team", "gsis_id"])
    dc = _load_multi_season("depth_charts/depth_charts_*.csv", rd)
    if dc.empty:
        return empty

    starters = []

    if "club_code" in dc.columns:
        old = dc[(dc.get("game_type") == "REG") & (dc.get("depth_position") == "QB")].copy()
        old = old[old["depth_team"].astype(str).isin(["1", "1.0"])]
        old["season"] = pd.to_numeric(old["season"], errors="coerce")
        old["week"] = pd.to_numeric(old["week"], errors="coerce")
        old["team"] = old["club_code"].apply(_normalize_team)
        starters.append(
            old.dropna(subset=["season", "week"])[["season", "week", "team", "gsis_id"]]
        )

    if "dt" in dc.columns and "gsis_id" in dc.columns and "pos_abb" in dc.columns:
        schedule = _load_schedule(rd)
        if not schedule.empty:
            kickoffs = (
                schedule.groupby(["season", "week"])["gameday"]
                .min().reset_index().rename(columns={"gameday": "kickoff_date"})
            )
            kickoffs["kickoff_date"] = pd.to_datetime(kickoffs["kickoff_date"], errors="coerce")

            new = dc[dc["pos_abb"] == "QB"].copy()
            new["team"] = new["team"].apply(_normalize_team)
            new["season"] = pd.to_numeric(new["season"], errors="coerce")
            new["pos_rank"] = pd.to_numeric(new["pos_rank"], errors="coerce")
            new = new[new["pos_rank"] == 1]
            new["dt"] = pd.to_datetime(new["dt"], utc=True, errors="coerce").dt.tz_localize(None)
            new = new.dropna(subset=["season", "dt"])

            if not new.empty:
                teams_per_season = new[["season", "team"]].drop_duplicates()
                targets = kickoffs.merge(teams_per_season, on="season", how="inner")
                targets = targets.dropna(subset=["kickoff_date"])

                matched = pd.merge_asof(
                    targets.sort_values("kickoff_date"),
                    new.sort_values("dt")[["season", "team", "dt", "gsis_id"]],
                    left_on="kickoff_date", right_on="dt",
                    by=["season", "team"], direction="backward",
                )
                starters.append(
                    matched.dropna(subset=["gsis_id"])[["season", "week", "team", "gsis_id"]]
                )

    if not starters:
        return empty
    return pd.concat(starters, ignore_index=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nn_feature_engine.py::TestLoadDeclaredStarters -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: add depth-chart-based declared-starter loader"
```

---

## Task 2: Injury report, reserve status, and snap-share loaders

**Files:**
- Modify: `services/nn_feature_engine.py` (add three loaders, near `_load_injury_flags` at line 1920)
- Test: `tests/test_nn_feature_engine.py` (new test classes)

**Interfaces:**
- Produces: `_load_qb_report_status(rd) -> DataFrame[season, week, team, gsis_id, report_status]`
- Produces: `_load_qb_reserve_status(rd) -> DataFrame[season, week, team, gsis_id]` (presence = on Reserve/IR that week)
- Produces: `_load_qb_snap_shares(rd) -> DataFrame[season, week, team, gsis_id, snap_share]`

- [ ] **Step 1: Write the failing tests**

```python
class TestLoadQbReportStatus:
    def test_returns_qb_rows_with_status(self, tmp_path):
        from services.nn_feature_engine import _load_qb_report_status
        inj_dir = tmp_path / "injuries"
        inj_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0100", "report_status": "Out"},
            {"season": 2026, "week": 2, "team": "SEA", "position": "WR",
             "gsis_id": "00-0200", "report_status": "Questionable"},
        ]).to_csv(inj_dir / "injuries_2026.csv", index=False)

        result = _load_qb_report_status(tmp_path)
        assert len(result) == 1
        assert result.iloc[0]["gsis_id"] == "00-0100"
        assert result.iloc[0]["report_status"] == "Out"

    def test_missing_file_returns_empty_frame(self, tmp_path):
        from services.nn_feature_engine import _load_qb_report_status
        result = _load_qb_report_status(tmp_path)
        assert list(result.columns) == ["season", "week", "team", "gsis_id", "report_status"]
        assert result.empty


class TestLoadQbReserveStatus:
    def test_returns_only_reserve_rows(self, tmp_path):
        from services.nn_feature_engine import _load_qb_reserve_status
        wr_dir = tmp_path / "weekly_rosters"
        wr_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0100", "status": "RES"},
            {"season": 2026, "week": 2, "team": "SEA", "position": "QB",
             "gsis_id": "00-0101", "status": "ACT"},
        ]).to_csv(wr_dir / "roster_weekly_2026.csv", index=False)

        result = _load_qb_reserve_status(tmp_path)
        assert len(result) == 1
        assert result.iloc[0]["gsis_id"] == "00-0100"


class TestLoadQbSnapShares:
    def test_computes_share_of_team_qb_snaps(self, tmp_path):
        from services.nn_feature_engine import _load_qb_snap_shares
        sc_dir = tmp_path / "snap_counts"
        sc_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "DarnSa00", "offense_snaps": 5},
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "LockDr00", "offense_snaps": 45},
        ]).to_csv(sc_dir / "snap_counts_2026.csv", index=False)

        roster_dir = tmp_path / "rosters"
        roster_dir.mkdir()
        pd.DataFrame([
            {"pfr_id": "DarnSa00", "gsis_id": "00-0100"},
            {"pfr_id": "LockDr00", "gsis_id": "00-0101"},
        ]).to_csv(roster_dir / "roster_2026.csv", index=False)

        result = _load_qb_snap_shares(tmp_path)
        darnold = result[result["gsis_id"] == "00-0100"].iloc[0]
        lock = result[result["gsis_id"] == "00-0101"].iloc[0]
        assert darnold["snap_share"] == pytest.approx(0.1)
        assert lock["snap_share"] == pytest.approx(0.9)

    def test_unmatched_pfr_id_dropped_not_crashed(self, tmp_path):
        """A snap-count player with no roster ID crosswalk entry is dropped,
        not treated as 0% -- fails open at the caller instead."""
        from services.nn_feature_engine import _load_qb_snap_shares
        sc_dir = tmp_path / "snap_counts"
        sc_dir.mkdir()
        pd.DataFrame([
            {"season": 2026, "week": 1, "team": "SEA", "position": "QB",
             "game_type": "REG", "pfr_player_id": "Unknown00", "offense_snaps": 50},
        ]).to_csv(sc_dir / "snap_counts_2026.csv", index=False)
        roster_dir = tmp_path / "rosters"
        roster_dir.mkdir()
        pd.DataFrame([{"pfr_id": "SomeoneElse00", "gsis_id": "00-9999"}]).to_csv(
            roster_dir / "roster_2026.csv", index=False)

        result = _load_qb_snap_shares(tmp_path)
        assert result.empty
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nn_feature_engine.py -k "QbReportStatus or QbReserveStatus or QbSnapShares" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the three loaders**

Add after `_load_injury_flags` (services/nn_feature_engine.py, after line 1957):

```python
def _load_qb_report_status(rd: Path) -> pd.DataFrame:
    """Per-player weekly official injury report status for QBs.

    Returns columns: season, week, team, gsis_id, report_status.
    Covers 2009+ (injuries availability); earlier seasons return empty.
    """
    empty = pd.DataFrame(columns=["season", "week", "team", "gsis_id", "report_status"])
    df = _load_multi_season("injuries/injuries_*.csv", rd)
    if df.empty:
        return empty
    df = df[df["position"] == "QB"].copy()
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["team"] = df["team"].apply(_normalize_team)
    return df.dropna(subset=["season", "week"])[
        ["season", "week", "team", "gsis_id", "report_status"]
    ]


def _load_qb_reserve_status(rd: Path) -> pd.DataFrame:
    """Per-player weekly Reserve/IR roster status for QBs -- catches
    season-ending injuries that often drop off the weekly injury report
    entirely rather than staying tagged 'Out'.

    Returns columns: season, week, team, gsis_id (row presence == on
    Reserve/IR that week). Covers 2002+ (weekly_rosters availability).
    """
    empty = pd.DataFrame(columns=["season", "week", "team", "gsis_id"])
    df = _load_multi_season("weekly_rosters/roster_weekly_*.csv", rd)
    if df.empty:
        return empty
    df = df[(df["position"] == "QB") & (df["status"] == "RES")].copy()
    df["season"] = pd.to_numeric(df["season"], errors="coerce")
    df["week"] = pd.to_numeric(df["week"], errors="coerce")
    df["team"] = df["team"].apply(_normalize_team)
    return df.dropna(subset=["season", "week"])[["season", "week", "team", "gsis_id"]]


def _load_qb_snap_shares(rd: Path) -> pd.DataFrame:
    """Per-player weekly share of the team's QB offensive snaps.

    snap_counts is keyed by pfr_player_id, not gsis_id -- cross-walked via
    rosters/roster_{year}.csv, which carries both IDs per player. A player
    with no crosswalk match is dropped from the result (the caller treats a
    missing row as "no snap data available," not "0% snaps").

    Returns columns: season, week, team, gsis_id, snap_share.
    """
    empty = pd.DataFrame(columns=["season", "week", "team", "gsis_id", "snap_share"])
    sc = _load_multi_season("snap_counts/snap_counts_*.csv", rd)
    if sc.empty:
        return empty
    sc = sc[sc["position"] == "QB"].copy()
    if "game_type" in sc.columns:
        sc = sc[sc["game_type"] == "REG"]
    sc["season"] = pd.to_numeric(sc["season"], errors="coerce")
    sc["week"] = pd.to_numeric(sc["week"], errors="coerce")
    sc["team"] = sc["team"].apply(_normalize_team)
    sc["offense_snaps"] = pd.to_numeric(sc.get("offense_snaps", 0), errors="coerce").fillna(0.0)
    sc = sc.dropna(subset=["season", "week", "pfr_player_id"])
    if sc.empty:
        return empty

    roster = _load_multi_season("rosters/roster_*.csv", rd)
    if roster.empty or "pfr_id" not in roster.columns or "gsis_id" not in roster.columns:
        return empty
    crosswalk = (
        roster.dropna(subset=["pfr_id", "gsis_id"])
        .drop_duplicates(subset=["pfr_id"])[["pfr_id", "gsis_id"]]
    )
    sc = sc.merge(crosswalk, left_on="pfr_player_id", right_on="pfr_id", how="inner")
    if sc.empty:
        return empty

    totals = (
        sc.groupby(["season", "week", "team"])["offense_snaps"]
        .sum().reset_index().rename(columns={"offense_snaps": "total_snaps"})
    )
    sc = sc.merge(totals, on=["season", "week", "team"], how="left")
    sc["total_snaps"] = sc["total_snaps"].clip(lower=1)
    sc["snap_share"] = sc["offense_snaps"] / sc["total_snaps"]
    return sc[["season", "week", "team", "gsis_id", "snap_share"]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nn_feature_engine.py -k "QbReportStatus or QbReserveStatus or QbSnapShares" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: add injury report, reserve status, and snap share loaders for QB availability"
```

---

## Task 3: `compute_qb_availability_flags` — the sticky-reference algorithm

**Files:**
- Modify: `services/nn_feature_engine.py` (add `compute_qb_availability_flags`; remove dead `compute_starter_qb_flags` and its call site)
- Test: `tests/test_nn_feature_engine.py` (new `TestComputeQbAvailabilityFlags` class)

**Interfaces:**
- Consumes: `_load_declared_starters`, `_load_qb_report_status`, `_load_qb_reserve_status`, `_load_qb_snap_shares` (Tasks 1-2)
- Produces: `compute_qb_availability_flags(seasons: list, rawdata_dir) -> Dict[Tuple[int, int, str], float]`

- [ ] **Step 1: Write the failing tests**

These tests patch the four loaders directly (unit-testing the algorithm, not the CSV parsing already covered in Tasks 1-2):

```python
class TestComputeQbAvailabilityFlags:
    def _patch_loaders(self, declared, report, reserve, snaps):
        import services.nn_feature_engine as nfe
        return [
            patch.object(nfe, "_load_declared_starters", return_value=pd.DataFrame(declared)),
            patch.object(nfe, "_load_qb_report_status", return_value=pd.DataFrame(report)),
            patch.object(nfe, "_load_qb_reserve_status", return_value=pd.DataFrame(reserve)),
            patch.object(nfe, "_load_qb_snap_shares", return_value=pd.DataFrame(snaps)),
        ]

    def test_healthy_starter_all_season_flags_zero(self, tmp_path):
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "QB1"} for w in [1, 2, 3]]
        patches = self._patch_loaders(declared, [], [], [])
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result == {
            (2026, 1, "AAA"): 0.0, (2026, 2, "AAA"): 0.0, (2026, 3, "AAA"): 0.0,
        }

    def test_starter_hurt_mid_game_week1_still_flags_via_snap_share(self, tmp_path):
        """Regression for the SEA/MIN case: week-1 starter identified from
        the depth chart (not from who happened to play the most snaps that
        game), then flagged unavailable in week 2 via the injury report."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD"}]
        report = [{"season": 2026, "week": 2, "team": "SEA", "gsis_id": "DARNOLD",
                   "report_status": "Out"}]
        # Week 1's own snaps show the backup played most of the game -- must
        # NOT be used to override the depth-chart-declared starter.
        snaps = [
            {"season": 2026, "week": 1, "team": "SEA", "gsis_id": "DARNOLD", "snap_share": 0.10},
            {"season": 2026, "week": 1, "team": "SEA", "gsis_id": "LOCK", "snap_share": 0.90},
        ]
        patches = self._patch_loaders(declared, report, [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 1, "SEA")] == 1.0  # Darnold at 10% snaps -> unavailable that week too
        assert result[(2026, 2, "SEA")] == 1.0  # Darnold on injury report

    def test_reserve_status_flags_when_injury_report_silent(self, tmp_path):
        """A season-ending IR move often drops off the weekly injury report
        entirely -- the Reserve/IR roster status leg must catch it anyway."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "SEA", "gsis_id": "DARNOLD"} for w in [1, 2]]
        reserve = [{"season": 2026, "week": 2, "team": "SEA", "gsis_id": "DARNOLD"}]
        patches = self._patch_loaders(declared, [], reserve, [])
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        assert result[(2026, 2, "SEA")] == 1.0

    def test_single_week_healthy_rest_does_not_flip_reference(self, tmp_path):
        """One week of a healthy backup (no injury/reserve entry for the
        starter) must not permanently reassign the reference -- only two
        CONSECUTIVE weeks of a >65% snap-share challenger does."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3]]
        snaps = [
            {"season": 2026, "week": 1, "team": "AAA", "gsis_id": "STARTER", "snap_share": 1.0},
            {"season": 2026, "week": 2, "team": "AAA", "gsis_id": "STARTER", "snap_share": 0.05},
            {"season": 2026, "week": 2, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 0.95},
            {"season": 2026, "week": 3, "team": "AAA", "gsis_id": "STARTER", "snap_share": 1.0},
        ]
        patches = self._patch_loaders(declared, [], [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        # Week 2 itself reads as unavailable (starter under 20% snaps that week)...
        assert result[(2026, 2, "AAA")] == 1.0
        # ...but week 3 the starter is back and the reference never flipped.
        assert result[(2026, 3, "AAA")] == 0.0

    def test_two_consecutive_weeks_of_healthy_benching_flips_reference(self, tmp_path):
        """A genuine benching (or a resolving preseason committee): the
        reference flips to the new starter, and the flag then tracks THEM,
        not the original starter."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3, 4]]
        snaps = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER", "snap_share": 0.05}
            for w in [2, 3, 4]
        ] + [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 0.95}
            for w in [2, 3, 4]
        ]
        patches = self._patch_loaders(declared, [], [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        # Week 4: reference has flipped to BACKUP, who is playing at 95% ->
        # available, even though the ORIGINAL starter is still at 5%.
        assert result[(2026, 4, "AAA")] == 0.0

    def test_reference_stays_pinned_while_starter_has_any_injury_entry(self, tmp_path):
        """Even if a backup holds the job for weeks, the reference does NOT
        flip while the original starter still shows an Out/Doubtful entry --
        only a healthy-but-benched starter can lose the reference."""
        from services.nn_feature_engine import compute_qb_availability_flags
        declared = [{"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER"} for w in [1, 2, 3, 4]]
        report = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "STARTER", "report_status": "Out"}
            for w in [2, 3, 4]
        ]
        snaps = [
            {"season": 2026, "week": w, "team": "AAA", "gsis_id": "BACKUP", "snap_share": 1.0}
            for w in [2, 3, 4]
        ]
        patches = self._patch_loaders(declared, report, [], snaps)
        with patches[0], patches[1], patches[2], patches[3]:
            result = compute_qb_availability_flags([2026], tmp_path)
        for w in [2, 3, 4]:
            assert result[(2026, w, "AAA")] == 1.0, f"week {w} should still flag -- starter still Out"

    def test_missing_declared_starter_fails_open(self, tmp_path):
        from services.nn_feature_engine import compute_qb_availability_flags
        result = compute_qb_availability_flags([2026], tmp_path)
        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_nn_feature_engine.py::TestComputeQbAvailabilityFlags -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `compute_qb_availability_flags`, remove dead code**

Delete the entire `compute_starter_qb_flags` function (services/nn_feature_engine.py, the "Snap-Count QB Starter Flag" section, lines 1430-1503) and replace it with:

```python
# ---------------------------------------------------------------------------
# QB Starter Availability (sticky reference starter)
# ---------------------------------------------------------------------------

def compute_qb_availability_flags(seasons: list, rawdata_dir) -> dict:
    """Per-team, per-week QB starter availability signal.

    See docs/superpowers/specs/2026-09-17-qb-availability-and-prediction-
    freshness-design.md Part A for the full design and decided edge cases.

    A team's "reference starter" starts as whoever the depth chart declared
    pre-week-1, and only reassigns after two consecutive weeks of both (a)
    the current reference having no Out/Doubtful/Reserve entry and (b) a
    different QB holding >65% of the team's QB snaps.

    Returns {(season, week, team): 1.0 if the week's reference starter is
    unavailable that week, else 0.0}. The snap-share leg of the
    availability check (not the flip-detection leg) only fires for weeks
    with real snap data -- i.e. it naturally never fires for a future,
    unplayed game.
    """
    rd = Path(rawdata_dir)
    declared = _load_declared_starters(rd)
    report   = _load_qb_report_status(rd)
    reserve  = _load_qb_reserve_status(rd)
    snaps    = _load_qb_snap_shares(rd)

    declared_map = {
        (int(r.season), int(r.week), r.team): r.gsis_id
        for r in declared.itertuples(index=False)
    }

    out_rows = report[report["report_status"].isin(["Out", "Doubtful"])] if not report.empty else report
    report_out = set(zip(out_rows["season"], out_rows["week"], out_rows["gsis_id"])) if not out_rows.empty else set()
    reserve_set = set(zip(reserve["season"], reserve["week"], reserve["gsis_id"])) if not reserve.empty else set()

    snap_by_team_week: dict = {}
    for r in snaps.itertuples(index=False):
        snap_by_team_week.setdefault((r.season, r.week, r.team), {})[r.gsis_id] = r.snap_share

    flags: dict = {}
    for season in seasons:
        season_keys = [(s, w, t) for (s, w, t) in declared_map if s == season]
        if not season_keys:
            continue
        teams = sorted({t for (_, _, t) in season_keys})
        weeks = sorted({w for (_, _, t) in season_keys})

        for team in teams:
            reference = declared_map.get((season, weeks[0], team))
            challenger_streak: list = []

            for wk in weeks:
                if reference is None:
                    flags[(season, wk, team)] = 0.0
                    continue

                ref_out     = (season, wk, reference) in report_out
                ref_reserve = (season, wk, reference) in reserve_set
                week_shares = snap_by_team_week.get((season, wk, team), {})
                ref_share   = week_shares.get(reference)
                ref_low_snaps = ref_share is not None and ref_share < 0.20

                flags[(season, wk, team)] = (
                    1.0 if (ref_out or ref_reserve or ref_low_snaps) else 0.0
                )

                if ref_out or ref_reserve:
                    challenger_streak = []
                    continue

                challenger = max(
                    ((gid, share) for gid, share in week_shares.items()
                     if gid != reference and share > 0.65),
                    key=lambda x: x[1], default=None,
                )
                challenger_streak = (challenger_streak + [challenger[0] if challenger else None])[-2:]

                if (len(challenger_streak) == 2
                        and challenger_streak[0] is not None
                        and challenger_streak[0] == challenger_streak[1]):
                    reference = challenger_streak[0]
                    challenger_streak = []

    return flags
```

Then remove the dead call site at line 1995 (`starter_qb_flags = compute_starter_qb_flags(snap_counts)`) entirely — it's superseded by the wiring added in Task 4.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_nn_feature_engine.py::TestComputeQbAvailabilityFlags -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full test suite to find every other reference to the deleted function**

Run: `pytest tests/test_nn_feature_engine.py -v` — expect PASS, no other test in this file references `compute_starter_qb_flags`.

Run: `grep -rn "compute_starter_qb_flags" tests/` — this finds three call sites in a *different* file that mock the now-deleted function directly:

```
tests/test_preseason_profiles.py:1094:             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
tests/test_preseason_profiles.py:1125:             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
tests/test_preseason_profiles.py:1154:             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
```

Run: `pytest tests/test_preseason_profiles.py -v`
Expected: FAIL — all 3 tests in `TestProfileZTableOverrideInFeatureTable` (`test_override_applied_when_profiles_available`, `test_off_roster_value_delta_is_differential_not_home_only`, `test_roster_talent_delta_uses_all_5_dims`) raise `AttributeError: <module 'services.nn_feature_engine'> does not have the attribute 'compute_starter_qb_flags'`.

- [ ] **Step 6: Remove the dead patch from those 3 tests**

In `tests/test_preseason_profiles.py`, each of the three `with patch.object(...)` blocks (around lines 1087-1097, 1118-1128, 1147-1157) has this line:

```python
             patch.object(fe, "compute_starter_qb_flags", return_value={}), \
```

Delete that line from all three blocks (the surrounding patches — `_load_schedule`, `_load_elo`, `_build_profile_z_table`, etc. — are unchanged; `compute_qb_availability_flags` doesn't need patching here since it's called through `_load_multi_season`, which these tests already patch to return an empty frame, so it fails open to `{}` on its own).

- [ ] **Step 7: Run both test files to confirm everything passes**

Run: `pytest tests/test_nn_feature_engine.py tests/test_preseason_profiles.py -v`
Expected: PASS (`test_preseason_profiles.py`'s three tests pass again — note they'll be touched *again* in Task 6, Step 8, once `_build_profile_z_table`'s key shape changes; this step only fixes the `AttributeError` from the deletion)

- [ ] **Step 8: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: replace dead QB starter flag with sticky-reference availability algorithm"
```

---

## Task 4: Wire the availability signal into `build_master_feature_table`

**Files:**
- Modify: `services/nn_feature_engine.py:2144-2164` (the existing injury-flag merge block)
- Test: `tests/test_nn_feature_engine.py` (new test)

**Interfaces:**
- Consumes: `compute_qb_availability_flags` (Task 3)

- [ ] **Step 1: Write the failing test**

```python
def test_qb_availability_signal_ors_into_injury_flag(tmp_path):
    """A team whose declared starter is benched-via-snap-count (no official
    injury report entry at all) must still show home_qb_injury_flag == 1.0
    after this merge -- proving the new signal actually reaches the table,
    not just the official injury report."""
    from services.nn_feature_engine import build_master_feature_table
    rd = _make_minimal_feature_table_inputs(tmp_path)

    dc_dir = rd / "depth_charts"
    dc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"club_code": "KC", "week": 1, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "KC", "week": 2, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "KC", "week": 3, "game_type": "REG", "depth_team": 1,
         "full_name": "KC Starter", "gsis_id": "KC-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 1, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 2, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
        {"club_code": "BUF", "week": 3, "game_type": "REG", "depth_team": 1,
         "full_name": "BUF Starter", "gsis_id": "BUF-STARTER", "depth_position": "QB"},
    ]).to_csv(dc_dir / "depth_charts_2024.csv", index=False)

    sc_dir = rd / "snap_counts"
    sc_dir.mkdir(parents=True, exist_ok=True)
    roster_dir = rd / "rosters"
    roster_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"pfr_id": "KCBACKUP0", "gsis_id": "KC-BACKUP"},
        {"pfr_id": "KCSTART00", "gsis_id": "KC-STARTER"},
    ]).to_csv(roster_dir / "roster_2024.csv", index=False)
    pd.DataFrame([
        # KC starter benched for weeks 2 and 3 (healthy -- no injury report entry)
        {"season": 2024, "week": 2, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCBACKUP0", "offense_snaps": 60},
        {"season": 2024, "week": 3, "team": "KC", "position": "QB", "game_type": "REG",
         "pfr_player_id": "KCBACKUP0", "offense_snaps": 60},
    ]).to_csv(sc_dir / "snap_counts_2024.csv", index=False)

    df = build_master_feature_table(rawdata_dir=str(rd), min_season=2024, max_season=2024)
    wk3 = df[df["week"] == 3].iloc[0]
    assert wk3["home_qb_injury_flag"] == 1.0  # KC is home in the shared fixture
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_nn_feature_engine.py::test_qb_availability_signal_ors_into_injury_flag -v`
Expected: FAIL (`home_qb_injury_flag` is `0.0` — the new signal isn't wired in yet)

- [ ] **Step 3: Wire it into the merge**

In `build_master_feature_table` (services/nn_feature_engine.py:2144-2164), after the existing injury-flags merge block and before the legacy alias lines, add:

```python
    # --- QB Starter Availability (sticky-reference signal, OR'd into the
    # same two flags -- see compute_qb_availability_flags' docstring) ---
    qb_avail = compute_qb_availability_flags(
        sorted(sched["season"].dropna().unique().astype(int).tolist()), rd
    )
    sched["home_qb_injury_flag"] = sched.apply(
        lambda r: max(
            r["home_qb_injury_flag"],
            qb_avail.get((int(r["season"]), int(r["week"]), r["home_team"]), 0.0),
        ),
        axis=1,
    )
    sched["away_qb_injury_flag"] = sched.apply(
        lambda r: max(
            r["away_qb_injury_flag"],
            qb_avail.get((int(r["season"]), int(r["week"]), r["away_team"]), 0.0),
        ),
        axis=1,
    )

    # Legacy aux cols for any downstream that still reads them
    sched["home_qb_out"] = sched["home_qb_injury_flag"]
    sched["away_qb_out"] = sched["away_qb_injury_flag"]
```

(This replaces the existing two `sched["home_qb_out"] = ...` / `sched["away_qb_out"] = ...` lines — same lines, just placed after the new block instead of directly after the old injury-flags merge.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_nn_feature_engine.py::test_qb_availability_signal_ors_into_injury_flag -v`
Expected: PASS

- [ ] **Step 5: Run the full existing QB-injury-flag tests to confirm no regression**

Run: `pytest tests/test_nn_feature_engine.py -k "qb_injury" -v`
Expected: PASS (`test_qb_injury_split_two_flags`, `test_both_qbs_injured_distinguishable` unaffected — they don't set up depth-chart/snap-count fixtures, so `qb_avail` is empty and `max(x, 0.0) == x`)

- [ ] **Step 6: Commit**

```bash
git add services/nn_feature_engine.py tests/test_nn_feature_engine.py
git commit -m "feat: wire QB availability signal into build_master_feature_table"
```

---

## Task 5: Wire the availability signal into live predictions (`NNProjectionEngine`)

**Files:**
- Modify: `services/nn_projection_engine.py` (import, `__init__`, `initialize`, `_precompute_static_features`)
- Test: `tests/test_nn_projection_engine.py` (new test)

**Interfaces:**
- Consumes: `compute_qb_availability_flags` (Task 3)
- Produces: `NNProjectionEngine._qb_availability: Dict[Tuple[int, int, str], float]` (new attribute, readable by tests)

- [ ] **Step 1: Write the failing test**

```python
def test_precompute_static_features_uses_qb_availability_not_hardcoded_zero():
    """Regression: home_qb_injury_flag/away_qb_injury_flag were hardcoded to
    0.0 for every future game -- this is the actual reason a real starter's
    injury never reached a live (not-yet-played) prediction."""
    import pandas as pd
    from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
    from services.nn_projection_engine import NNProjectionEngine

    engine = NNProjectionEngine(nn_svc=MagicMock(), xgb_svc=MagicMock(), lr_svc=MagicMock())
    engine._season = 2026
    engine._team_profiles = pd.DataFrame([{"team": "SEA"}, {"team": "ARI"}])
    engine._qb_availability = {(2026, 2, "SEA"): 1.0}

    schedule_df = pd.DataFrame([
        {"home_team": "ARI", "away_team": "SEA", "week": 2, "div_game": 0, "surface_type": 0},
    ])
    feats = engine._precompute_static_features(schedule_df)
    col_idx = {c: i for i, c in enumerate(NN_FC)}
    key = "W02_ARI_SEA"
    assert feats[key][col_idx["away_qb_injury_flag"]] == 1.0  # SEA is away
    assert feats[key][col_idx["home_qb_injury_flag"]] == 0.0  # ARI has no entry -> fails open
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_nn_projection_engine.py::test_precompute_static_features_uses_qb_availability_not_hardcoded_zero -v`
Expected: FAIL (`away_qb_injury_flag` is `0.0`, hardcoded)

- [ ] **Step 3: Wire it in**

In `services/nn_projection_engine.py`:

1. Add to the import block (line 19-26):

```python
from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
    compute_preseason_roster_features,
    compute_preseason_player_profiles,
    compute_qb_availability_flags,
)
```

2. In `__init__` (after line 61, `self._preseason_profiles: dict = {}`), add:

```python
        self._qb_availability: Dict[Tuple[int, int, str], float] = {}
```

3. In `initialize()` (after line 78, `self._season = season`), add:

```python
        try:
            self._qb_availability = compute_qb_availability_flags([season], RAWDATA_DIR)
        except Exception as exc:
            logger.warning("QB availability flags unavailable for %d: %s", season, exc)
            self._qb_availability = {}
```

4. In `_precompute_static_features()`, replace lines 346-347:

```python
            feat[col_idx["home_qb_injury_flag"]]    = 0.0
            feat[col_idx["away_qb_injury_flag"]]    = 0.0
```

with:

```python
            feat[col_idx["home_qb_injury_flag"]] = self._qb_availability.get(
                (self._season, int(wk), ht), 0.0
            )
            feat[col_idx["away_qb_injury_flag"]] = self._qb_availability.get(
                (self._season, int(wk), at), 0.0
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_nn_projection_engine.py::test_precompute_static_features_uses_qb_availability_not_hardcoded_zero -v`
Expected: PASS

- [ ] **Step 5: Run the full projection engine test file**

Run: `pytest tests/test_nn_projection_engine.py -v`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add services/nn_projection_engine.py tests/test_nn_projection_engine.py
git commit -m "fix: feed real QB availability into live predictions instead of hardcoded 0.0"
```

---

## Task 6: Week-aware roster snapshot for historical grading (Part B)

**Files:**
- Modify: `services/nn_feature_engine.py` (`compute_preseason_player_profiles`, `_build_profile_z_table`, and the `_apply_profile_overrides` call site around line 2370-2394)
- Test: `tests/test_preseason_profiles.py` (new test class)

**Interfaces:**
- `compute_preseason_player_profiles(target_season: int, rawdata_dir, week: int = None) -> dict` — new optional `week` param; `None` (default) preserves existing "latest roster" behavior.
- `_build_profile_z_table(season_weeks: list, rawdata_dir) -> dict` — signature changes from `(seasons: list, ...)` to a list of `(season, week)` tuples; return key changes from `(season, team)` to `(season, week, team)`.

- [ ] **Step 1: Write the failing test**

```python
class TestWeekAwareRosterSnapshot:
    def _write_common_files(self, tmp_path, season=2026):
        """depth_chart/advstats/snap_counts shared by both roster scenarios below."""
        prior = season - 1
        (tmp_path / "depth_charts").mkdir(exist_ok=True)
        pd.concat([_fake_depth_chart(), _fake_def_depth_chart()], ignore_index=True).to_csv(
            tmp_path / "depth_charts" / f"depth_charts_{season}.csv", index=False)
        (tmp_path / "pfr_advstats").mkdir(exist_ok=True)
        _fake_def_advstats().to_csv(
            tmp_path / "pfr_advstats" / f"advstats_week_def_{prior}.csv", index=False)
        (tmp_path / "snap_counts").mkdir(exist_ok=True)
        pd.concat([_fake_snap_counts(), _fake_def_snap_counts()], ignore_index=True).to_csv(
            tmp_path / "snap_counts" / f"snap_counts_{prior}.csv", index=False)

    def test_week_param_reads_weekly_rosters_not_current_roster_file(self, tmp_path):
        """The 'current' rosters/roster_{year}.csv is mutated to a DIFFERENT
        roster than week 1's weekly_rosters snapshot -- week=1 must use the
        weekly_rosters content, not the mutated current file."""
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_common_files(tmp_path)

        week1_roster = pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True)
        week1_roster["week"] = 1

        # "Current" roster file has since drifted (e.g. an OL cut, replaced
        # by a much less experienced player) -- simulates today's roster
        # file no longer matching what existed at week 1's kickoff.
        drifted_roster = week1_roster.copy()
        drifted_roster.loc[drifted_roster["position"] == "T", "years_exp"] = 0

        (tmp_path / "weekly_rosters").mkdir(exist_ok=True)
        week1_roster.to_csv(tmp_path / "weekly_rosters" / f"roster_weekly_{2026}.csv", index=False)
        (tmp_path / "rosters").mkdir(exist_ok=True)
        drifted_roster.to_csv(tmp_path / "rosters" / f"roster_{2026}.csv", index=False)

        week1_result = compute_preseason_player_profiles(2026, tmp_path, week=1)
        latest_result = compute_preseason_player_profiles(2026, tmp_path, week=None)

        assert week1_result["AAA"]["ol_av"] != latest_result["AAA"]["ol_av"], (
            "week=1 must use weekly_rosters' week-1 snapshot, not the "
            "mutated 'current' rosters/roster_{year}.csv"
        )

    def test_week_param_omitted_keeps_reading_current_roster_file(self, tmp_path):
        """Default behavior (week=None) must be unchanged -- this is the
        path NNProjectionEngine.simulate_season() relies on for future games."""
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_common_files(tmp_path)
        (tmp_path / "rosters").mkdir(exist_ok=True)
        pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True).to_csv(
            tmp_path / "rosters" / f"roster_{2026}.csv", index=False)

        result = compute_preseason_player_profiles(2026, tmp_path)  # no weekly_rosters/ dir at all
        assert "AAA" in result

    def test_missing_weekly_rosters_file_returns_empty_when_week_given(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_common_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path, week=1)
        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_preseason_profiles.py::TestWeekAwareRosterSnapshot -v`
Expected: FAIL (`TypeError: compute_preseason_player_profiles() got an unexpected keyword argument 'week'`)

- [ ] **Step 3: Implement the `week` parameter**

In `compute_preseason_player_profiles` (services/nn_feature_engine.py:1270-1291), change the signature and roster-path selection:

```python
def compute_preseason_player_profiles(target_season: int, rawdata_dir, week: int = None) -> dict:
    """Build per-team EPA quality estimates from projected roster + prior-season player stats.

    Replaces compute_preseason_roster_features() for all position groups.
    Returns {team: {off_pass_epa, off_rush_epa, def_pass_epa, def_rush_epa,
                    ol_av, dl_perf, qb_tier}}.
    Returns {} if required files (roster or depth_charts) are missing.

    Args:
        week: When given, uses that week's own roster snapshot
            (weekly_rosters/roster_weekly_{target_season}.csv, filtered to
            this week) instead of the rolling "current" roster file --
            needed to grade an already-played week without picking up
            roster changes that happened afterward. None (default) keeps
            reading the latest snapshot, which is correct when projecting a
            game that hasn't been played yet.
    """
    prior = target_season - 1
    rd = Path(rawdata_dir)

    dc_path      = rd / "depth_charts" / f"depth_charts_{target_season}.csv"
    adv_def_path = rd / "pfr_advstats" / f"advstats_week_def_{prior}.csv"
    snap_path    = rd / "snap_counts"  / f"snap_counts_{prior}.csv"

    if week is not None:
        roster_path = rd / "weekly_rosters" / f"roster_weekly_{target_season}.csv"
    else:
        roster_path = rd / "rosters" / f"roster_{target_season}.csv"

    if not roster_path.exists() or not dc_path.exists():
        return {}

    roster = pd.read_csv(roster_path, low_memory=False)
    if week is not None:
        roster = roster[pd.to_numeric(roster["week"], errors="coerce") == week]
        if roster.empty:
            return {}
    depth_chart = pd.read_csv(dc_path, low_memory=False)
```

(The rest of the function body — depth_chart normalization onward — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_preseason_profiles.py::TestWeekAwareRosterSnapshot -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the existing preseason-profile tests to confirm the default path is unaffected**

Run: `pytest tests/test_preseason_profiles.py -v`
Expected: PASS (all existing tests call `compute_preseason_player_profiles(season, tmp_path)` without `week`, so they're unaffected)

- [ ] **Step 6: Make `_build_profile_z_table` and its call site week-aware**

Change `_build_profile_z_table` (services/nn_feature_engine.py:1401-1427):

```python
def _build_profile_z_table(season_weeks: list, rawdata_dir) -> dict:
    """Compute cross-team z-scores for 5 profile dimensions for each
    (season, week) pair, using that week's own roster snapshot.

    Returns {(season, week, team): {dl_perf, qb_tier, ol_av, off_pass_epa, def_pass_epa}}
    where each value is that team's z-score within that week's 32-team
    distribution. Pairs where compute_preseason_player_profiles returns {}
    are skipped silently.
    """
    _DIMS = ["dl_perf", "qb_tier", "ol_av", "off_pass_epa", "def_pass_epa"]
    table: dict = {}
    for season, week in season_weeks:
        try:
            profiles = compute_preseason_player_profiles(season, rawdata_dir, week=week)
        except Exception:
            continue
        if not profiles:
            continue
        teams = list(profiles.keys())
        vals = {d: np.array([profiles[t].get(d, 0.0) for t in teams], dtype=float)
                for d in _DIMS}
        mu  = {d: float(np.mean(v)) for d, v in vals.items()}
        sig = {d: max(float(np.std(v)), 1e-6) for d, v in vals.items()}
        for team in teams:
            table[(season, week, team)] = {
                d: float((profiles[team].get(d, mu[d]) - mu[d]) / sig[d])
                for d in _DIMS
            }
    return table
```

Update its call site (services/nn_feature_engine.py, the `_profile_seasons`/`_apply_profile_overrides` block around line 2370-2394):

```python
    _profile_rows = sched[sched["season"] >= 2020][["season", "week"]].drop_duplicates()
    _season_weeks = list(_profile_rows.itertuples(index=False, name=None))
    if _season_weeks:
        _pz = _build_profile_z_table(_season_weeks, rd)
        if _pz:
            def _apply_profile_overrides(row):
                hz = _pz.get((int(row["season"]), int(row["week"]), row["home_team"]))
                az = _pz.get((int(row["season"]), int(row["week"]), row["away_team"]))
                if hz is None or az is None:
                    return row
                row["def_pressure_diff"]      = hz["dl_perf"] - az["dl_perf"]
                row["qb_pressure_advantage"]  = az["dl_perf"] - hz["dl_perf"]
                row["off_roster_value_delta"] = (
                    (0.7 * hz["qb_tier"] + 0.3 * hz["ol_av"])
                    - (0.7 * az["qb_tier"] + 0.3 * az["ol_av"])
                )
                row["def_roster_value_delta"] = (
                    (0.6 * hz["dl_perf"] + 0.4 * (-hz["def_pass_epa"]))
                    - (0.6 * az["dl_perf"] + 0.4 * (-az["def_pass_epa"]))
                )
                h_q = (hz["qb_tier"] + hz["off_pass_epa"] + (-hz["def_pass_epa"])
                       + hz["dl_perf"] + hz["ol_av"]) / 5.0
                a_q = (az["qb_tier"] + az["off_pass_epa"] + (-az["def_pass_epa"])
                       + az["dl_perf"] + az["ol_av"]) / 5.0
                row["roster_talent_delta"] = h_q - a_q
                return row

            mask = sched["season"] >= 2020
            sched.loc[mask] = sched.loc[mask].apply(_apply_profile_overrides, axis=1)
```

(Only the two `_pz.get(...)` lookup lines and the `_season_weeks` construction change from the original — the override math itself is unchanged.)

- [ ] **Step 7: Add a test for `_build_profile_z_table`'s new week-aware key shape**

```python
def test_build_profile_z_table_keys_include_week(tmp_path):
    from services.nn_feature_engine import _build_profile_z_table
    with patch(
        "services.nn_feature_engine.compute_preseason_player_profiles",
        return_value={"AAA": {"dl_perf": 1.0, "qb_tier": 1.0, "ol_av": 1.0,
                               "off_pass_epa": 1.0, "def_pass_epa": 1.0},
                      "BBB": {"dl_perf": -1.0, "qb_tier": -1.0, "ol_av": -1.0,
                              "off_pass_epa": -1.0, "def_pass_epa": -1.0}},
    ):
        result = _build_profile_z_table([(2026, 1), (2026, 2)], tmp_path)
    assert (2026, 1, "AAA") in result
    assert (2026, 2, "AAA") in result
```

Run: `pytest tests/test_preseason_profiles.py::test_build_profile_z_table_keys_include_week -v`
Expected: PASS

- [ ] **Step 8: Fix the existing `TestBuildProfileZTable`/`TestProfileZTableOverrideInFeatureTable` classes for the new key shape**

Run: `pytest tests/test_preseason_profiles.py -k "BuildProfileZTable or ProfileZTableOverride" -v`
Expected: FAIL — `_build_profile_z_table` now takes `(season, week)` tuples and returns `(season, week, team)` keys; these 8 existing tests still use the old `(season, team)` shape.

Replace `TestBuildProfileZTable` (`tests/test_preseason_profiles.py:1000-1061`) in full:

```python
class TestBuildProfileZTable:
    def _make_raw_profiles(self):
        """Minimal two-team profile dict as returned by compute_preseason_player_profiles."""
        return {
            "AAA": {"off_pass_epa": 0.10, "off_rush_epa": 0.02,
                    "def_pass_epa": -0.05, "def_rush_epa": -0.02,
                    "qb_tier": 0.18, "ol_av": 350000.0, "dl_perf": 500.0},
            "BBB": {"off_pass_epa": -0.10, "off_rush_epa": -0.02,
                    "def_pass_epa":  0.05, "def_rush_epa":  0.02,
                    "qb_tier": -0.05, "ol_av": 150000.0, "dl_perf": 200.0},
        }

    def test_returns_z_scores_for_each_team(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([(2024, 1)], rawdata_dir=".")
        assert (2024, 1, "AAA") in table
        assert (2024, 1, "BBB") in table

    def test_z_scores_sum_to_zero_across_teams(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([(2024, 1)], rawdata_dir=".")
        dims = ["dl_perf", "qb_tier", "ol_av", "off_pass_epa", "def_pass_epa"]
        for d in dims:
            total = table[(2024, 1, "AAA")][d] + table[(2024, 1, "BBB")][d]
            assert abs(total) < 1e-6, f"{d} z-scores don't sum to 0: {total}"

    def test_stronger_team_has_positive_dl_z(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([(2024, 1)], rawdata_dir=".")
        # AAA has higher dl_perf (500 vs 200) so should have positive z
        assert table[(2024, 1, "AAA")]["dl_perf"] > 0
        assert table[(2024, 1, "BBB")]["dl_perf"] < 0

    def test_empty_profiles_returns_no_entry_for_season(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value={}):
            table = _build_profile_z_table([(2024, 1)], rawdata_dir=".")
        assert not any(k[0] == 2024 for k in table)

    def test_multiple_season_weeks_all_populated(self):
        from services.nn_feature_engine import _build_profile_z_table
        from unittest.mock import patch
        profiles = self._make_raw_profiles()
        with patch("services.nn_feature_engine.compute_preseason_player_profiles",
                   return_value=profiles):
            table = _build_profile_z_table([(2023, 1), (2024, 1), (2024, 2)], rawdata_dir=".")
        assert any(k[0] == 2023 for k in table)
        assert (2024, 1, "AAA") in table
        assert (2024, 2, "AAA") in table
```

Replace `TestProfileZTableOverrideInFeatureTable` (`tests/test_preseason_profiles.py:1064-1164`) in full — each of the three tests loses the `compute_starter_qb_flags` patch (already removed in Task 3, Step 6) and its `profile_table` keys gain the week dimension (`week=1`, matching `_make_schedule`'s fixture row):

```python
class TestProfileZTableOverrideInFeatureTable:
    """Verify that build_master_feature_table() applies profile z-score overrides for 2020+."""

    def _make_schedule(self):
        return pd.DataFrame([{
            "season": 2024, "week": 1, "home_team": "KC", "away_team": "BAL",
            "game_type": "REG", "home_win": 1,
            "result": 7.0, "spread_line": -3.0,
        }])

    def test_override_applied_when_profiles_available(self, tmp_path, monkeypatch):
        """def_pressure_diff should reflect profile dl_perf z-scores, not rolling stats."""
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        sched = self._make_schedule()

        kc_z  = {"dl_perf": 1.5, "qb_tier": 1.0, "ol_av": 0.5,
                  "off_pass_epa": 0.8, "def_pass_epa": -0.3}
        bal_z = {"dl_perf": -0.5, "qb_tier": 0.2, "ol_av": -0.3,
                  "off_pass_epa": 0.3, "def_pass_epa": 0.1}
        profile_table = {(2024, 1, "KC"): kc_z, (2024, 1, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        assert not result.empty
        row = result.iloc[0]
        # def_pressure_diff = kc_dl_z - bal_dl_z = 1.5 - (-0.5) = 2.0
        assert abs(row["def_pressure_diff"] - 2.0) < 0.01
        # qb_pressure_advantage = bal_dl_z - kc_dl_z = -0.5 - 1.5 = -2.0
        assert abs(row["qb_pressure_advantage"] - (-2.0)) < 0.01

    def test_off_roster_value_delta_is_differential_not_home_only(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        sched = self._make_schedule()
        kc_z  = {"dl_perf": 0.0, "qb_tier": 1.0, "ol_av": 1.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        bal_z = {"dl_perf": 0.0, "qb_tier": -1.0, "ol_av": -1.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        profile_table = {(2024, 1, "KC"): kc_z, (2024, 1, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        row = result.iloc[0]
        # off_roster_value_delta = (0.7*1 + 0.3*1) - (0.7*(-1) + 0.3*(-1)) = 1.0 - (-1.0) = 2.0
        assert abs(row["off_roster_value_delta"] - 2.0) < 0.01

    def test_roster_talent_delta_uses_all_5_dims(self, tmp_path, monkeypatch):
        import services.nn_feature_engine as fe
        from unittest.mock import patch

        sched = self._make_schedule()
        # KC better on all dims, BAL zero on all
        kc_z  = {"dl_perf": 1.0, "qb_tier": 1.0, "ol_av": 1.0,
                  "off_pass_epa": 1.0, "def_pass_epa": -1.0}
        bal_z = {"dl_perf": 0.0, "qb_tier": 0.0, "ol_av": 0.0,
                  "off_pass_epa": 0.0, "def_pass_epa": 0.0}
        profile_table = {(2024, 1, "KC"): kc_z, (2024, 1, "BAL"): bal_z}

        with patch.object(fe, "_load_schedule", return_value=sched), \
             patch.object(fe, "_load_elo", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_box_stats_from_weekly", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_pressure_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_rolling_epa", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_trench_rolling_stats", return_value=pd.DataFrame()), \
             patch.object(fe, "_load_multi_season", return_value=pd.DataFrame()), \
             patch.object(fe, "compute_roster_features", return_value={}), \
             patch.object(fe, "compute_roster_performance", return_value={}), \
             patch.object(fe, "_build_profile_z_table", return_value=profile_table):
            result = fe.build_master_feature_table(min_season=2024, max_season=2024)

        row = result.iloc[0]
        # h_q = (1+1+1+1+1)/5 = 1.0 (note: def_pass_epa flipped: -(-1)=1)
        # a_q = 0
        # roster_talent_delta = 1.0 - 0.0 = 1.0
        assert abs(row["roster_talent_delta"] - 1.0) < 0.01
```

Run: `pytest tests/test_preseason_profiles.py -k "BuildProfileZTable or ProfileZTableOverride" -v`
Expected: PASS (9 tests: 5 in `TestBuildProfileZTable`, 3 in `TestProfileZTableOverrideInFeatureTable`, plus Step 7's new test)

- [ ] **Step 9: Commit**

```bash
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "fix: use each week's own roster snapshot for historical grading, not today's roster"
```

---

## Task 7: Wire the availability signal into `backfill_schedule_predictions.py`'s display fallback

**Files:**
- Modify: `scripts/backfill_schedule_predictions.py:187-216` (the MC-sim `explanation` dict)
- Test: `tests/test_backfill_predictions_map.py` (new test)

**Interfaces:**
- Consumes: `compute_qb_availability_flags` (Task 3)

- [ ] **Step 1: Write the failing test**

```python
class TestExplanationQbAvailability:
    def test_home_away_qb_out_come_from_availability_flags_not_hardcoded_zero(self):
        schedule_df = pd.DataFrame([_schedule_row("KC", "BUF", 2)])
        ft_lookup = {}

        fake_engine = MagicMock()
        fake_engine._team_profiles = pd.DataFrame(columns=["team"])
        fake_engine.lookup_roster_value.return_value = {}
        fake_engine.simulate_season.return_value = {
            "game_probs": {
                "W02_KC_BUF": {
                    "home_team": "KC", "away_team": "BUF", "week": 2,
                    "mean_prob": 0.6, "model_spread": -2.5,
                },
            },
        }
        with patch.object(bsp, "NNProjectionEngine", return_value=fake_engine), \
             patch.object(bsp, "get_game_predictions", return_value={}), \
             patch.object(bsp, "compute_qb_availability_flags",
                          return_value={(2025, 2, "KC"): 1.0}):
            result = bsp._build_predictions_map(
                2025, ft_lookup, schedule_df, pd.DataFrame(), force=True,
            )

        explanation = result["W02_KC_BUF"]["explanation"]
        assert explanation["home_qb_out"] == 1.0
        assert explanation["away_qb_out"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backfill_predictions_map.py::TestExplanationQbAvailability -v`
Expected: FAIL (`AttributeError` — `compute_qb_availability_flags` isn't imported/patchable in `bsp` yet, or `home_qb_out` is `0.0`)

- [ ] **Step 3: Wire it in**

In `scripts/backfill_schedule_predictions.py`, add to the import line (line 48):

```python
from services.nn_feature_engine import (
    build_master_feature_table, _normalize_team, compute_qb_availability_flags,
)
```

In `_build_predictions_map`, inside the `if needs_simulation:` block, right after `sim = engine.simulate_season(...)` (after line 125), add:

```python
        qb_avail = compute_qb_availability_flags([year], RAWDATA_DIR)
```

(`RAWDATA_DIR` needs importing too if not already present — check the top of the file; if absent, add `from services.nn_feature_engine import RAWDATA_DIR` alongside the import above, or use `Path(__file__).parent.parent / "rawdata"` matching whatever this file already uses elsewhere for its own rawdata path.)

Then replace lines 207-208:

```python
                    "home_qb_out":          0.0,
                    "away_qb_out":          0.0,
```

with:

```python
                    "home_qb_out":          qb_avail.get((year, int(wk), ht), 0.0),
                    "away_qb_out":          qb_avail.get((year, int(wk), at), 0.0),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backfill_predictions_map.py::TestExplanationQbAvailability -v`
Expected: PASS

- [ ] **Step 5: Run the full backfill test files**

Run: `pytest tests/test_backfill_predictions_map.py tests/test_backfill_schedule_predictions.py tests/test_backfill_features_flag.py -v`
Expected: PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add scripts/backfill_schedule_predictions.py tests/test_backfill_predictions_map.py
git commit -m "fix: display real QB availability in backfill's MC-sim explanation instead of hardcoded 0.0"
```

---

## Task 8: Keep `explanation` in sync with the daily thin-write (Part C)

**Files:**
- Modify: `scripts/cache_builder.py` (`_apply_predictions`, `build_year`, `_derive_prediction_fields`)
- Test: `tests/test_cache_builder.py` (extend `TestApplyPredictionsFallback`, add new test to the `build_year` write path)

**Interfaces:**
- `_apply_predictions(...)` now also returns an `explanation` column on the output DataFrame.
- `_derive_prediction_fields(...)` now also returns an `explanation` key.

- [ ] **Step 1: Write the failing tests**

```python
class TestApplyPredictionsCarriesExplanation:
    def test_feature_table_branch_carries_explanation_through(self):
        """pred_lookup entries already include a full explanation dict
        (built by build_ensemble_lookup) -- it must survive into the output,
        not be dropped."""
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": 3.0},
        ])
        pred_lookup = {(2026, 3, "WAS", "KC"): {
            "pred_winner": "WAS", "pred_su_conf": 70.0,
            "pred_ats_pick": "WAS", "pred_prob": 0.7,
            "model_spread": 4.5, "edge_vs_vegas": 1.0,
            "explanation": {"elo_diff": 12.3, "vegas_line": 3.0},
        }}
        out = _apply_predictions(schedule, 2026, pred_lookup, fallback_engine=MagicMock())
        assert out.iloc[0]["explanation"] == {"elo_diff": 12.3, "vegas_line": 3.0}

    def test_fallback_branch_builds_an_explanation_not_none(self):
        from scripts.cache_builder import _apply_predictions
        schedule = pd.DataFrame([
            {"home_team": "WAS", "away_team": "KC", "week": 3, "result": None,
             "spread_line": -2.5},
        ])
        fallback_engine = MagicMock()
        fallback_engine._team_profiles = pd.DataFrame(columns=["team"])
        fallback_engine.simulate_season.return_value = {
            "game_probs": {
                "W03_WAS_KC": {"mean_prob": 0.62, "model_spread": -3.0,
                               "home_team": "WAS", "away_team": "KC", "week": 3},
            },
        }
        out = _apply_predictions(schedule, 2026, {}, fallback_engine=fallback_engine)
        assert out.iloc[0]["explanation"] is not None
        assert out.iloc[0]["explanation"]["model_spread"] == -3.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder.py::TestApplyPredictionsCarriesExplanation -v`
Expected: FAIL with `KeyError: 'explanation'`

- [ ] **Step 3: Extend `_derive_prediction_fields` to build an explanation dict**

Replace `_derive_prediction_fields` (scripts/cache_builder.py:86-111):

```python
def _derive_prediction_fields(ht: str, at: str, mean_prob: float, model_spread: float, spread_line) -> dict:
    """Shared winner/confidence/ATS-pick/edge-vs-vegas/explanation derivation
    from a simulate_season() game_probs_out entry -- used by both
    _apply_predictions()'s fallback branch and _publish_game_probs() so
    the two call sites can't independently drift (previously only one of
    them wrote model_spread, and neither computed edge_vs_vegas from it,
    leaving a stale/inconsistent pair for anything reading edge_vs_vegas,
    e.g. services/betting_screener_service.py). Also builds a matching
    `explanation` dict (previously only backfill_schedule_predictions.py's
    own, separate fallback branch did this) so the daily cache_builder job
    doesn't leave `explanation` frozen at whatever a manual backfill run
    last wrote."""
    winner = ht if mean_prob >= 0.5 else at
    conf = round(max(mean_prob, 1.0 - mean_prob) * 100, 1)
    ats = winner
    edge_vs_vegas = None
    if pd.notna(spread_line):
        try:
            sl_val = float(spread_line)
            ats = ht if model_spread > sl_val else at
            edge_vs_vegas = round(model_spread - sl_val, 1)
        except (ValueError, TypeError):
            pass
    return {
        "pred_winner": winner,
        "pred_su_conf": conf,
        "pred_ats_pick": ats,
        "model_spread": model_spread,
        "edge_vs_vegas": edge_vs_vegas,
        "explanation": {
            "vegas_line": float(spread_line) if pd.notna(spread_line) else None,
            "model_spread": model_spread,
            "edge_vs_vegas": edge_vs_vegas,
            "source": "mc_simulation (daily cache_builder)",
        },
    }
```

- [ ] **Step 4: Thread `explanation` through `_apply_predictions`**

In `_apply_predictions` (scripts/cache_builder.py:159-245):

Add an `explanations: list = [None] * n` alongside the other per-row lists (after line 180).

In the feature-table branch (after line 197, `pred_edges[i] = pred.get('edge_vs_vegas')`), add:

```python
            explanations[i] = pred.get('explanation')
```

In the fallback branch (after line 236, `pred_edges[i] = derived['edge_vs_vegas']`), add:

```python
            explanations[i] = derived['explanation']
```

At the end of the function (after line 244, `out['edge_vs_vegas'] = pred_edges`), add:

```python
    out['explanation'] = explanations
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder.py::TestApplyPredictionsCarriesExplanation -v`
Expected: PASS

- [ ] **Step 6: Carry `explanation` into the daily `pmap` write in `build_year`**

Write the failing test first:

```python
class TestBuildYearWritesExplanation:
    def test_explanation_included_in_daily_pmap_when_present(self):
        """Regression: the daily thin-write only ever sent pred_winner/
        pred_su_conf/pred_ats_pick/pred_prob/model_spread/edge_vs_vegas --
        explanation silently never refreshed after the first backfill run."""
        import scripts.cache_builder as cb
        schedule_df_with_explanation = pd.DataFrame([
            {"week": 3, "home_team": "WAS", "away_team": "KC",
             "pred_winner": "WAS", "pred_su_conf": 70.0, "pred_ats_pick": "WAS",
             "pred_prob": 0.7, "model_spread": 4.5, "edge_vs_vegas": 1.0,
             "explanation": {"elo_diff": 12.3}},
        ])
        captured = {}
        with patch.object(cb, "get_game_predictions", return_value={}), \
             patch.object(cb, "merge_thin_game_predictions", side_effect=lambda existing, fresh: fresh), \
             patch.object(cb, "write_game_predictions", side_effect=lambda year, merged: captured.update(merged)), \
             patch.object(cb.analysis, "get_enriched_schedule", return_value=schedule_df_with_explanation), \
             patch.object(cb, "_apply_predictions", return_value=schedule_df_with_explanation), \
             patch.object(cb, "live_scores"):
            cb.build_year(
                pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), year=2026, current_year=2026,
                force=True,
            )
        assert captured["W03_WAS_KC"]["explanation"] == {"elo_diff": 12.3}
```

Run it to confirm it fails (`KeyError: 'explanation'`), then update the `pred_cols`/`entry` construction in `build_year` (scripts/cache_builder.py:320-348):

```python
            pred_cols = ['week', 'home_team', 'away_team', 'pred_winner',
                         'pred_su_conf', 'pred_ats_pick', 'pred_prob',
                         'model_spread', 'edge_vs_vegas', 'explanation']
            pc = [c for c in pred_cols if c in schedule_df.columns]
            if 'pred_winner' in pc:
                pmap = {}
                for _, r in schedule_df[pc].dropna(subset=['pred_winner']).iterrows():
                    ht = _normalize_team(str(r.get('home_team', '') or ''))
                    at = _normalize_team(str(r.get('away_team', '') or ''))
                    wk = r.get('week')
                    if ht and at and wk is not None:
                        entry = {
                            'pred_prob':     r.get('pred_prob'),
                            'pred_winner':   r.get('pred_winner'),
                            'pred_su_conf':  r.get('pred_su_conf'),
                            'pred_ats_pick': r.get('pred_ats_pick'),
                        }
                        # model_spread/edge_vs_vegas are only known when
                        # this row came from a source that computed them
                        # (feature-table lookup or the simulate_season
                        # fallback) -- omit rather than write NaN over a
                        # previously-stored value from --resimulate/backfill.
                        ms = r.get('model_spread')
                        if pd.notna(ms):
                            entry['model_spread'] = ms
                        ev = r.get('edge_vs_vegas')
                        if pd.notna(ev):
                            entry['edge_vs_vegas'] = ev
                        exp = r.get('explanation')
                        if isinstance(exp, dict):
                            entry['explanation'] = exp
                        pmap[f"W{int(wk):02d}_{ht}_{at}"] = entry
```

(Only the `pred_cols` list gains `'explanation'`, and the `exp = r.get('explanation')` block is new — everything else in this loop is unchanged.)

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_cache_builder.py::TestBuildYearWritesExplanation -v`
Expected: PASS

- [ ] **Step 8: Run the full cache_builder test file**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS (no regressions)

- [ ] **Step 9: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "fix: carry explanation through the daily thin-write instead of freezing it at whatever backfill last wrote"
```

---

## Task 9: Weekly lock-in — Tuesday backfill step inside `winspool-predict-daily` (Part D)

**Files:**
- Modify: `scripts/cache_builder.py` (`main()`)
- Test: `tests/test_cache_builder.py` (new test)

**Interfaces:**
- No new public interface — a day-of-week-conditional subprocess call inside `main()`.

- [ ] **Step 1: Write the failing test**

```python
class TestWeeklyBackfillStep:
    def test_runs_backfill_on_tuesday(self):
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)  # a real Tuesday
        with patch.object(cb, "_run_weekly_backfill_if_tuesday") as mock_step, \
             patch.object(cb, "load_data", return_value=(pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame(), pd.DataFrame(),
                                                          pd.DataFrame())), \
             patch.object(cb, "get_available_years", return_value=[]), \
             patch.object(cb, "_years_to_build", return_value=[]), \
             patch.object(cb, "NNPredictionService", side_effect=Exception("skip ML load")), \
             patch("sys.argv", ["cache_builder.py", "--skip-sync"]):
            cb.main()
        mock_step.assert_called_once()

    def test_subprocess_only_runs_on_tuesday(self):
        """_run_weekly_backfill_if_tuesday itself gates on weekday()."""
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        monday = datetime(2026, 9, 21, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run") as mock_run:
            mock_dt.now.return_value = monday
            cb._run_weekly_backfill_if_tuesday()
        mock_run.assert_not_called()

    def test_subprocess_invoked_with_firestore_flag_on_tuesday(self):
        import scripts.cache_builder as cb
        from datetime import datetime, timezone
        tuesday = datetime(2026, 9, 22, 9, 15, tzinfo=timezone.utc)
        with patch("scripts.cache_builder.datetime") as mock_dt, \
             patch.object(cb.subprocess, "run") as mock_run:
            mock_dt.now.return_value = tuesday
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            cb._run_weekly_backfill_if_tuesday()
        mock_run.assert_called_once()
        called_args = mock_run.call_args[0][0]
        assert "backfill_schedule_predictions.py" in called_args[1]
        assert "--firestore" in called_args
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_builder.py::TestWeeklyBackfillStep -v`
Expected: FAIL with `AttributeError: module 'scripts.cache_builder' has no attribute '_run_weekly_backfill_if_tuesday'`

- [ ] **Step 3: Implement it**

Add `from datetime import datetime, timezone` to the top-level imports (scripts/cache_builder.py, near the other stdlib imports at line 11-16).

Add a new function near `_sync_rawdata` (after line 431):

```python
def _run_weekly_backfill_if_tuesday() -> None:
    """Grade the prior week's predictions via the feature table once a
    week, on the first day every game in that week (including Monday Night
    Football) is guaranteed final. See docs/superpowers/specs/2026-09-17-
    qb-availability-and-prediction-freshness-design.md Part D for why
    Tuesday and not Thursday/Sunday, and why this runs as a conditional step
    inside winspool-predict-daily's own daily job rather than a new
    Cloud Scheduler trigger or Docker image (this job's image already has
    the ML dependencies backfill_schedule_predictions.py needs;
    winspool-schedule-kickoffs' does not).
    """
    if datetime.now(timezone.utc).weekday() != 1:  # Monday=0, Tuesday=1
        return
    print("[cache_builder] Tuesday -- running weekly backfill lock-in...")
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "backfill_schedule_predictions.py"), "--firestore"],
        capture_output=True, text=True, timeout=600,
        cwd=str(SCRIPTS_DIR.parent),
    )
    stdout_tail = result.stdout.strip().splitlines()[-20:]
    print("[cache_builder] weekly backfill summary:")
    for line in stdout_tail:
        print(f"  {line}")
    if result.returncode != 0:
        print(f"[warn] backfill_schedule_predictions.py exited non-zero (non-fatal): "
              f"{result.stderr.strip()[:500]}")
```

Call it in `main()`, after the per-year `build_year` loop (after line 558, before the "Signaling predictions_active" block):

```python
    _run_weekly_backfill_if_tuesday()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_builder.py::TestWeeklyBackfillStep -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full cache_builder test file one more time**

Run: `pytest tests/test_cache_builder.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/cache_builder.py tests/test_cache_builder.py
git commit -m "feat: run weekly prediction backfill on Tuesdays inside winspool-predict-daily"
```

---

## Task 10: Full test suite, then rollout

**Files:** None (verification + manual rollout step)

- [ ] **Step 1: Run the entire unit suite**

Run: `pytest tests/`
Expected: PASS, zero failures. If any test outside the ones touched in Tasks 1-9 fails, investigate before proceeding — a shared helper (`_load_multi_season`, `_normalize_team`, `_derive_prediction_fields`) may have a call site elsewhere in the codebase not covered above.

- [ ] **Step 2: Manual local smoke test**

Run against real local rawdata (not a fixture) to sanity-check the new signal on the SEA/MIN cases from the investigation:

```bash
python -c "
from services.nn_feature_engine import compute_qb_availability_flags, RAWDATA_DIR
flags = compute_qb_availability_flags([2026], RAWDATA_DIR)
print('SEA week 2:', flags.get((2026, 2, 'SEA')))
print('MIN week 2:', flags.get((2026, 2, 'MIN')))
"
```

Expected: both print `1.0` once that week's injury report is synced and shows Darnold/Murray as Out/Doubtful (if the injury report for that week isn't synced yet, both print `0.0` or `None` — re-run `python scripts/sync_nflverse_data.py` first).

- [ ] **Step 3: Rollout — regrade the current season's already-locked weeks**

This is a one-time manual step per the spec's Rollout section — not a recurring job (Task 9's weekly step handles new weeks going forward without `--force`, since they aren't locked yet when it runs):

```bash
python scripts/backfill_schedule_predictions.py --force --firestore
```

Expected: every already-`locked` game in the current season gets a fresh, self-consistent `pred_winner`/`model_spread`/`explanation`, now computed with the corrected week-aware roster snapshot (Task 6) and the real QB availability signal (Tasks 3-4).

- [ ] **Step 4: Verify the admin ML Accuracy page**

Manually load `/admin` → ML Accuracy tab → expand a week with a known QB change (e.g. week 1/2 SEA or MIN) and confirm the per-game explain modal now shows a non-zero `home_qb_out`/`away_qb_out` where expected, and that `Model Line`/`Model Pick` agree in direction (the original symptom that started this investigation).

---

## Plan Self-Review Notes

- **Spec coverage:** Part A (Tasks 1-5, 7), Part B (Task 6), Part C (Task 8), Part D (Task 9), Rollout (Task 10) — all four parts and the rollout step are covered.
- **Type consistency:** `compute_qb_availability_flags` returns `Dict[Tuple[int, int, str], float]` everywhere it's consumed (Tasks 4, 5, 7) — checked consistent across all three call sites.
- **Placeholder scan, caught and fixed during self-review:** deleting `compute_starter_qb_flags` in Task 3 breaks 3 existing tests in `tests/test_preseason_profiles.py` that mock it directly (found via `grep -rn "compute_starter_qb_flags" tests/`), and changing `_build_profile_z_table`'s signature in Task 6 breaks 8 more existing tests that use its old `(season, team)`-keyed return shape. Both are now full, real fixes (Task 3 Step 6, Task 6 Step 8) with complete replacement code, not left as "update the existing tests" instructions.
