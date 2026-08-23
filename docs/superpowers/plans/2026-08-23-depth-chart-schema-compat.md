# Historical depth_charts Schema Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `compute_preseason_player_profiles()` (`services/nn_feature_engine.py`) work against the pre-2025 nflverse `depth_charts` schema (2001-2024: `club_code`/`depth_position`/`depth_team`), not just the 2025+ schema (`team`/`pos_abb`/`pos_rank`) it currently hardcodes, so `PRESEASON_ELO_WEIGHTS` (`services/constants.py`) can eventually be validated against ~10 real completed seasons instead of effectively zero.

**Architecture:** One new normalization function, `_normalize_depth_chart()`, called once at the top of `compute_preseason_player_profiles()` right after the raw CSV is read. It detects which schema the DataFrame is in (presence of `club_code` vs. `dt`) and returns a DataFrame in the single shared shape (`team`, `player_name`, `gsis_id`, `pos_abb`, `pos_rank`) that `_preseason_offense()`/`_preseason_defense()` already expect. Neither builder function changes at all.

**Tech Stack:** Python (existing stack), pandas.

**Spec:** `docs/superpowers/specs/2026-08-23-depth-chart-schema-compat-design.md`

## Global Constraints

- `_normalize_depth_chart()` must not change behavior for the current (2025+) schema in any way — the existing `dt`-based dedup logic moves into the function unchanged, byte-for-byte equivalent to what `compute_preseason_player_profiles()` does today.
- For the old schema, use the season's **week 1, `game_type == "REG"`** snapshot only — never a later week. A later week would leak in-season information into what is supposed to be a preseason-only estimate, which would silently invalidate any historical backtest built on top of this fix.
- `_preseason_offense()` and `_preseason_defense()` (`services/nn_feature_engine.py`) must not be modified. If a column they need turns out to be missing after normalization, that's a bug in `_normalize_depth_chart()`, not a reason to touch the builders.
- No behavior change to any currently-passing test in `tests/test_preseason_profiles.py` or `tests/test_rank_position_groups.py`.

---

### Task 1: `_normalize_depth_chart()` + wiring + historical validation

**Files:**
- Modify: `services/nn_feature_engine.py` (add `_normalize_depth_chart()`, wire into `compute_preseason_player_profiles()`)
- Test: `tests/test_preseason_profiles.py` (add new test classes)

**Interfaces:**
- Produces: `_normalize_depth_chart(df: pd.DataFrame) -> pd.DataFrame` — takes a raw depth_charts DataFrame (either schema, or an already-normalized new-schema DataFrame with no `dt` column at all, which existing test fixtures use), returns one with `team`, `player_name`, `gsis_id`, `pos_abb`, `pos_rank` columns. Old-schema input is filtered to week-1 REG first. New-schema input with a `dt` column is deduped to the latest snapshot per `gsis_id` (existing behavior, moved verbatim). Anything else passes through unchanged.
- Consumes: nothing new — reads only pandas and the DataFrame passed in.

- [ ] **Step 1: Write the failing unit tests for `_normalize_depth_chart()`**

Add near the top of `tests/test_preseason_profiles.py` (after the existing imports, before `TestLoadPlayerEpa`):

```python
class TestNormalizeDepthChart:
    def test_old_schema_filters_to_week1_reg_and_renames_columns(self):
        from services.nn_feature_engine import _normalize_depth_chart
        df = pd.DataFrame([
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Alpha", "gsis_id": "00-0001", "depth_position": "QB"},
            {"club_code": "AAA", "week": 2, "game_type": "REG", "depth_team": 1,
             "full_name": "QB Alpha", "gsis_id": "00-0001", "depth_position": "QB"},
            {"club_code": "AAA", "week": 1, "game_type": "WC", "depth_team": 1,
             "full_name": "QB Alpha", "gsis_id": "00-0001", "depth_position": "QB"},
        ])
        result = _normalize_depth_chart(df)
        assert len(result) == 1
        assert set(result.columns) >= {"team", "player_name", "pos_abb", "pos_rank", "gsis_id"}
        row = result.iloc[0]
        assert row["team"] == "AAA"
        assert row["player_name"] == "QB Alpha"
        assert row["pos_abb"] == "QB"
        assert row["pos_rank"] == 1

    def test_new_schema_dedups_to_latest_dt(self):
        from services.nn_feature_engine import _normalize_depth_chart
        df = pd.DataFrame([
            {"dt": "2026-01-01T00:00:00Z", "team": "AAA", "player_name": "QB Alpha",
             "gsis_id": "00-0001", "pos_abb": "QB", "pos_rank": 2},
            {"dt": "2026-03-01T00:00:00Z", "team": "AAA", "player_name": "QB Alpha",
             "gsis_id": "00-0001", "pos_abb": "QB", "pos_rank": 1},
        ])
        result = _normalize_depth_chart(df)
        assert len(result) == 1
        assert result.iloc[0]["pos_rank"] == 1  # the later dt snapshot wins

    def test_new_schema_without_dt_passes_through_unchanged(self):
        from services.nn_feature_engine import _normalize_depth_chart
        df = pd.DataFrame([
            {"team": "AAA", "player_name": "QB Alpha", "gsis_id": "00-0001",
             "pos_abb": "QB", "pos_rank": 1},
        ])
        result = _normalize_depth_chart(df)
        pd.testing.assert_frame_equal(
            result.reset_index(drop=True), df.reset_index(drop=True)
        )

    def test_old_schema_junk_position_codes_survive_the_rename(self):
        """Junk depth_position values (whitespace-only, typos) rename to
        pos_abb like any other value -- they simply won't match any of the
        specific position-code filters downstream (_OFF_OL_POS, _DEF_DL_POS,
        etc.), same as today. No special-casing needed here."""
        from services.nn_feature_engine import _normalize_depth_chart
        df = pd.DataFrame([
            {"club_code": "AAA", "week": 1, "game_type": "REG", "depth_team": 3,
             "full_name": "Scrub Guy", "gsis_id": "00-0099", "depth_position": "\n    "},
        ])
        result = _normalize_depth_chart(df)
        assert len(result) == 1
        assert result.iloc[0]["pos_abb"] == "\n    "
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_preseason_profiles.py::TestNormalizeDepthChart -v`
Expected: FAIL with `ImportError: cannot import name '_normalize_depth_chart'` (function doesn't exist yet).

- [ ] **Step 3: Implement `_normalize_depth_chart()`**

In `services/nn_feature_engine.py`, add the function directly above
`compute_preseason_player_profiles` (which currently starts at line 1184):

```python
def _normalize_depth_chart(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize either nflverse depth_charts schema to the shared shape
    _preseason_offense()/_preseason_defense() expect: team, player_name,
    gsis_id, pos_abb, pos_rank.

    New schema (2025+): dt, team, player_name, pos_abb, pos_rank -- already
    match. Dedup to the latest dt snapshot per player (this is the same
    logic compute_preseason_player_profiles() used to do inline; moved here
    unchanged). A DataFrame with no dt column (e.g. already-normalized test
    fixtures) passes through as-is.

    Old schema (2001-2024): club_code, full_name, depth_position, depth_team,
    week, game_type. No "latest" timestamp exists -- use week 1 REG instead,
    the earliest chart of the season and the correct preseason analog (a
    later week would leak in-season info into what's supposed to be a
    preseason-only estimate).
    """
    if "club_code" in df.columns:
        df = df[(df["week"] == 1) & (df["game_type"] == "REG")].copy()
        df = df.rename(columns={
            "club_code": "team",
            "full_name": "player_name",
            "depth_position": "pos_abb",
            "depth_team": "pos_rank",
        })
    elif "dt" in df.columns and "gsis_id" in df.columns:
        df = (
            df
            .sort_values("dt")
            .drop_duplicates(subset=["gsis_id"], keep="last")
        )
    return df
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_preseason_profiles.py::TestNormalizeDepthChart -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write the failing integration tests against old-schema data**

Add this helper near `_fake_depth_chart()`/`_fake_def_depth_chart()` in
`tests/test_preseason_profiles.py` (after `_fake_def_depth_chart()`, before
`class TestPreseasonOffense`... actually place it right before
`class TestComputePreseasonPlayerProfiles`, since it's only used there):

```python
def _fake_depth_chart_old_schema(week=1, game_type="REG") -> pd.DataFrame:
    """Same players as _fake_depth_chart()+_fake_def_depth_chart(), in the
    pre-2025 nflverse depth_charts schema (club_code/depth_position/depth_team)."""
    combined = pd.concat([_fake_depth_chart(), _fake_def_depth_chart()], ignore_index=True)
    return pd.DataFrame([
        {
            "club_code": row["team"], "week": week, "game_type": game_type,
            "depth_team": row["pos_rank"], "full_name": row["player_name"],
            "gsis_id": row["gsis_id"], "depth_position": row["pos_abb"],
        }
        for _, row in combined.iterrows()
    ])
```

Then add this test class right after `class TestComputePreseasonPlayerProfiles`
(after its last method, `test_returns_empty_dict_if_files_missing`, before
`class TestNNProjectionEngineInitialize`):

```python
class TestComputePreseasonPlayerProfilesOldSchema:
    """Same coverage as TestComputePreseasonPlayerProfiles but with the
    pre-2025 nflverse depth_charts schema (club_code/depth_position/depth_team)
    -- proves compute_preseason_player_profiles() works for 2001-2024 data,
    not just 2025+."""

    def _write_files(self, tmp_path, season=2023):
        prior = season - 1
        (tmp_path / "stats_player").mkdir(exist_ok=True)
        _fake_player_stats().to_csv(
            tmp_path / "stats_player" / f"stats_player_regpost_{prior}.csv", index=False)

        (tmp_path / "depth_charts").mkdir(exist_ok=True)
        _fake_depth_chart_old_schema(week=1, game_type="REG").to_csv(
            tmp_path / "depth_charts" / f"depth_charts_{season}.csv", index=False)

        (tmp_path / "rosters").mkdir(exist_ok=True)
        pd.concat([_fake_roster(), _fake_def_roster()], ignore_index=True).to_csv(
            tmp_path / "rosters" / f"roster_{season}.csv", index=False)

        (tmp_path / "pfr_advstats").mkdir(exist_ok=True)
        _fake_def_advstats().to_csv(
            tmp_path / "pfr_advstats" / f"advstats_week_def_{prior}.csv", index=False)

        (tmp_path / "snap_counts").mkdir(exist_ok=True)
        pd.concat([_fake_snap_counts(), _fake_def_snap_counts()], ignore_index=True).to_csv(
            tmp_path / "snap_counts" / f"snap_counts_{prior}.csv", index=False)

    def test_returns_dict_with_teams(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2023, tmp_path)
        assert isinstance(result, dict)
        assert "AAA" in result

    def test_all_required_keys_present(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2023, tmp_path)
        for key in ("off_pass_epa", "off_rush_epa", "def_pass_epa",
                    "def_rush_epa", "ol_av", "dl_perf", "qb_tier"):
            assert key in result["AAA"], f"Missing: {key}"

    def test_qb_tier_matches_new_schema_equivalent(self, tmp_path):
        """Same fixture data, same expected qb_tier as the new-schema
        test_qb_tier_matches_qb_pass_epa_rate (pass_epa_rate = 0.20) --
        proves the old-schema path produces numerically equivalent output,
        not just a non-crashing one."""
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2023, tmp_path)
        assert result["AAA"]["qb_tier"] == pytest.approx(0.20, abs=0.01)

    def test_only_week1_snapshot_used_not_later_weeks(self, tmp_path):
        """A later-week depth chart entry for a different (e.g. injury-
        replacement) player at the same slot must not leak into the
        preseason estimate -- proves the week==1 REG filter is doing real
        work, not just happening to match because it's the only week in the
        fixture."""
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)

        raw_path = tmp_path / "depth_charts" / "depth_charts_2023.csv"
        raw = pd.read_csv(raw_path)
        week2_bad_qb = pd.DataFrame([{
            "club_code": "AAA", "week": 2, "game_type": "REG", "depth_team": 1,
            "full_name": "Backup QB", "gsis_id": "99-9999", "depth_position": "QB",
        }])
        pd.concat([raw, week2_bad_qb], ignore_index=True).to_csv(raw_path, index=False)

        result = compute_preseason_player_profiles(2023, tmp_path)
        # Still QB Alpha's rate (0.20) -- not contaminated by the week-2 entry,
        # which has no player_epa match and would pull qb_tier toward the
        # rookie-discount default if it leaked in.
        assert result["AAA"]["qb_tier"] == pytest.approx(0.20, abs=0.01)
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `pytest tests/test_preseason_profiles.py::TestComputePreseasonPlayerProfilesOldSchema -v`
Expected: FAIL — `KeyError: 'team'` (the bug this plan fixes; `compute_preseason_player_profiles()` hasn't been wired to `_normalize_depth_chart()` yet).

- [ ] **Step 7: Wire `_normalize_depth_chart()` into `compute_preseason_player_profiles()`**

In `services/nn_feature_engine.py`, inside `compute_preseason_player_profiles()`
(currently around line 1203-1215), replace:

```python
    roster      = pd.read_csv(roster_path, low_memory=False)
    depth_chart = pd.read_csv(dc_path,     low_memory=False)

    # Keep only the latest depth-chart snapshot per player (dt = daily timestamp).
    # This ensures traded players appear on their current team, not their old one.
    if "dt" in depth_chart.columns and "gsis_id" in depth_chart.columns:
        depth_chart = (
            depth_chart
            .sort_values("dt")
            .drop_duplicates(subset=["gsis_id"], keep="last")
        )

    depth_chart["team"] = depth_chart["team"].apply(_normalize_team)
```

with:

```python
    roster      = pd.read_csv(roster_path, low_memory=False)
    depth_chart = pd.read_csv(dc_path,     low_memory=False)

    depth_chart = _normalize_depth_chart(depth_chart)

    depth_chart["team"] = depth_chart["team"].apply(_normalize_team)
```

- [ ] **Step 8: Run the full preseason-profile test suite to verify pass with no regressions**

Run: `pytest tests/test_preseason_profiles.py -v`
Expected: PASS, all tests including the pre-existing `TestComputePreseasonPlayerProfiles`
(2025/2026-shaped fixtures, unaffected by this change) and the new
`TestNormalizeDepthChart` / `TestComputePreseasonPlayerProfilesOldSchema` classes.

Also run: `pytest tests/test_rank_position_groups.py -v`
Expected: PASS, unchanged (this script only calls `compute_preseason_player_profiles()`,
doesn't touch depth_charts directly).

- [ ] **Step 9: Validate against real historical rawdata**

This step is manual verification against the real files already on disk in
`rawdata/depth_charts/` — not a pytest step, but required before considering
this done, since the whole point is unlocking real historical data.

Run this to confirm every season from 2001-2024 now produces a non-empty
result (some early years may have thin `dl_perf`/`def_*` quality since
`pfr_advstats` only starts 2018 and `snap_counts` availability varies by
year — that degradation is expected and already handled gracefully by the
existing `.exists()` guards on those optional inputs; the only thing this
step checks is that nothing crashes and every team gets a profile):

```bash
python -c "
import pathlib
from services.nn_feature_engine import compute_preseason_player_profiles
RAWDATA_DIR = pathlib.Path('rawdata')
for yr in range(2001, 2025):
    try:
        p = compute_preseason_player_profiles(yr, RAWDATA_DIR)
        print(yr, len(p), 'teams' if p else '(empty)')
    except Exception as e:
        print(yr, 'ERROR', repr(e))
"
```

Expected: every year prints a team count (ideally 32, though very early
2000s seasons may differ if `rawdata/rosters/`/`rawdata/depth_charts/` don't
cover all current-format teams that far back) — no `ERROR` lines. Record in
the commit message (Step 10) the actual earliest year that comes back clean,
since the spec left this as an open question.

Then spot-check plausibility on one well-known season:

```bash
python scripts/rank_position_groups.py --season 2023
```

Expected: a real leaderboard (this currently raises `KeyError('team')`).
Skim it for sanity — teams known for a bad 2023 season shouldn't be at the
top, teams known for a strong 2023 roster shouldn't be at the bottom. This
is a smell test, not a formal check; if something looks clearly wrong,
stop and investigate before moving on rather than committing a fix that
produces plausible-looking-but-wrong data.

- [ ] **Step 10: Commit**

```bash
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "$(cat <<'EOF'
fix: support pre-2025 nflverse depth_charts schema in preseason profiles

nflverse changed the depth_charts release schema starting with the 2025
file (club_code/depth_position/depth_team -> team/pos_abb/pos_rank).
compute_preseason_player_profiles() never adapted, so it only worked for
2025-2026 -- every earlier season threw KeyError('team'), leaving zero
historical data to validate PRESEASON_ELO_WEIGHTS against.

Adds _normalize_depth_chart(), called once before the depth chart reaches
_preseason_offense()/_preseason_defense() (both unchanged) -- the two
schemas' position-code vocabularies are already ~identical, this is a
column rename plus a week==1 REG snapshot filter for the old schema
(closest analog to the new schema's "latest dt" for a file with no
timestamp column), not a taxonomy remapping.

Verified against real rawdata/depth_charts/ back to <YEAR> with no crashes.
EOF
)"
```

Fill in `<YEAR>` in the commit message with whatever Step 9 actually found.

---

## Self-Review Notes

- **Spec coverage:** The spec's single design (one normalization function,
  old schema -> week-1-REG + rename, new schema -> existing dt-dedup moved
  verbatim, zero changes to `_preseason_offense`/`_preseason_defense`) is
  fully covered by Task 1. The spec's Testing section (unit tests for the
  normalizer, an integration test against real 2023 data, regression
  coverage of existing 2025/2026 tests, manual `rank_position_groups.py`
  sanity check) maps directly to Steps 1-9. The spec's open question ("how
  far back does the old schema go uninterrupted") is resolved by Step 9's
  full-range sweep rather than assumed.
- **Explicitly out of scope, confirmed still out of scope:** the actual
  `PRESEASON_ELO_WEIGHTS` recalibration. This plan only unblocks it.
