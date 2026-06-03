# Preseason Player Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace 2025 team-level EPA averages with bottom-up 2026 team quality estimates built from the actual projected 2026 roster and 2025 individual player performance, so that trades and draft picks are reflected in preseason win projections.

**Architecture:** Two private helpers (`_preseason_offense`, `_preseason_defense`) aggregate per-player EPA rates from `stats_player_regpost_{prior}.csv`, weighted by depth chart rank from `depth_charts_{season}.csv`. An orchestrating `compute_preseason_player_profiles()` function merges both, normalizes each EPA dimension league-wide, and returns a 32-team dict that `NNProjectionEngine.initialize()` stores as `_preseason_profiles`. `_build_initial_state()` then uses those values to override the starting simulation state.

**Tech Stack:** Python, pandas, NumPy, pytest. No model changes.

---

## File Map

| File | Change |
|---|---|
| `services/nn_feature_engine.py` | Add `_load_player_epa()`, `_preseason_offense()`, `_preseason_defense()`, `compute_preseason_player_profiles()` |
| `services/nn_projection_engine.py` | Update `initialize()`, `_build_initial_state()`, `_precompute_static_features()` |
| `tests/test_preseason_profiles.py` | New — all tests for this feature |

---

## Key Data Facts (verified)

**`rawdata/stats_player/stats_player_regpost_{year}.csv`**
- Filter to `season_type == 'REG'`
- `player_id` = gsis_id format (`00-0022942`) — matches `depth_charts gsis_id` directly
- EPA columns: `passing_epa`, `rushing_epa`, `receiving_epa` (cumulative per game row)
- Other columns: `player_display_name`, `position`, `recent_team`, `attempts`, `carries`, `targets`

**`rawdata/depth_charts/depth_charts_{season}.csv`**
- `pos_abb` values used:
  - Offense: `QB`, `WR`, `TE`, `RB`, `LT`, `LG`, `C`, `RG`, `RT`
  - Defense: `LDE`, `RDE`, `LDT`, `RDT`, `NT`, `WLB`, `MLB`, `SLB`, `LILB`, `RILB`, `LCB`, `RCB`, `NB`, `SS`, `FS`
- `pos_rank` = 1 (starter), 2 (backup), etc.
- `gsis_id` links to `stats_player player_id` (primary) and to `roster gsis_id`
- `player_name` used as name-based fallback when gsis_id doesn't match

**`rawdata/pfr_advstats/advstats_week_def_{prior}.csv`**
- `pfr_player_id`, `pfr_player_name`
- DL/LB: `def_sacks`, `def_pressures`, `def_times_hitqb`, `def_tackles_combined`
- CB/S: `def_targets`, `def_yards_allowed_per_tgt`, `def_passer_rating_allowed`

**`rawdata/snap_counts/snap_counts_{prior}.csv`**
- `pfr_player_id`, `player` (name), `offense_snaps`, `defense_snaps`
- Used to compute per-snap rates for DL/LB/CB (divide advstats season totals by season snap total)

**`rawdata/rosters/roster_{season}.csv`**
- `gsis_id`, `pfr_id`, `full_name`, `position`, `birth_date`, `years_exp`
- Bridge: `gsis_id` → `pfr_id` (to link depth_chart players to advstats)

**Position group constants** (define once, reuse across tasks):
```python
OL_POS  = {"LT", "LG", "C", "RG", "RT"}
DL_POS  = {"LDE", "RDE", "LDT", "RDT", "NT"}
LB_POS  = {"WLB", "MLB", "SLB", "LILB", "RILB"}
CB_POS  = {"LCB", "RCB", "NB"}
S_POS   = {"SS", "FS"}
```

---

## Task 1: Data Loading Helpers

**Files:**
- Modify: `services/nn_feature_engine.py`
- Create: `tests/test_preseason_profiles.py`

Two helpers that load and aggregate the raw data, called by both offense and defense sub-functions.

- [ ] **Step 1: Write failing tests**

Create `tests/test_preseason_profiles.py`:

```python
"""tests/test_preseason_profiles.py -- Tests for compute_preseason_player_profiles()."""
import numpy as np
import pandas as pd
import pytest
from pathlib import Path


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fake_player_stats() -> pd.DataFrame:
    """Minimal player_stats rows (REG season only)."""
    return pd.DataFrame([
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "AAA", "season_type": "REG", "season": 2025,
         "passing_epa": 80.0, "rushing_epa": 5.0, "receiving_epa": 0.0,
         "attempts": 400, "carries": 30, "targets": 0},
        {"player_id": "00-0002", "player_display_name": "WR Beta", "position": "WR",
         "recent_team": "AAA", "season_type": "REG", "season": 2025,
         "passing_epa": 0.0, "rushing_epa": 0.0, "receiving_epa": 60.0,
         "attempts": 0, "carries": 0, "targets": 100},
        {"player_id": "00-0003", "player_display_name": "RB Gamma", "position": "RB",
         "recent_team": "BBB", "season_type": "REG", "season": 2025,
         "passing_epa": 0.0, "rushing_epa": 30.0, "receiving_epa": 5.0,
         "attempts": 0, "carries": 150, "targets": 20},
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "AAA", "season_type": "POST", "season": 2025,
         "passing_epa": 20.0, "rushing_epa": 2.0, "receiving_epa": 0.0,
         "attempts": 80, "carries": 5, "targets": 0},
    ])


# ── _load_player_epa tests ─────────────────────────────────────────────────────

class TestLoadPlayerEpa:
    def test_filters_to_reg_only(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        stats = _fake_player_stats()
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        # POST row for QB Alpha should be excluded
        assert len(result) == 3

    def test_aggregates_per_player_season_totals(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        # Two REG rows for same player (two weeks)
        stats = pd.DataFrame([
            {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
             "recent_team": "AAA", "season_type": "REG", "season": 2025,
             "passing_epa": 40.0, "rushing_epa": 2.0, "receiving_epa": 0.0,
             "attempts": 200, "carries": 10, "targets": 0},
            {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
             "recent_team": "AAA", "season_type": "REG", "season": 2025,
             "passing_epa": 40.0, "rushing_epa": 3.0, "receiving_epa": 0.0,
             "attempts": 200, "carries": 10, "targets": 0},
        ])
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        qb = result[result["player_id"] == "00-0001"].iloc[0]
        assert qb["passing_epa"] == pytest.approx(80.0)
        assert qb["attempts"] == 400

    def test_returns_empty_df_if_file_missing(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        result = _load_player_epa(2025, tmp_path)
        assert result.empty

    def test_computes_epa_per_play_rates(self, tmp_path):
        from services.nn_feature_engine import _load_player_epa
        stats = _fake_player_stats()
        path = tmp_path / "stats_player" / "stats_player_regpost_2025.csv"
        path.parent.mkdir()
        stats.to_csv(path, index=False)

        result = _load_player_epa(2025, tmp_path)
        qb = result[result["player_id"] == "00-0001"].iloc[0]
        # 80 EPA / 400 attempts = 0.2 EPA per dropback
        assert qb["pass_epa_rate"] == pytest.approx(0.2)
        wr = result[result["player_id"] == "00-0002"].iloc[0]
        # 60 EPA / 100 targets = 0.6 EPA per target
        assert wr["recv_epa_rate"] == pytest.approx(0.6)
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestLoadPlayerEpa -v
```
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Implement `_load_player_epa()`**

In `services/nn_feature_engine.py`, add near the other `compute_preseason_*` functions (after line ~418):

```python
def _load_player_epa(prior_season: int, rawdata_dir) -> pd.DataFrame:
    """Load and aggregate per-player season EPA totals from stats_player.

    Returns one row per player with cumulative REG-season EPA totals and
    per-play rate columns (pass_epa_rate, recv_epa_rate, rush_epa_rate).
    Returns empty DataFrame if file not found.
    """
    path = Path(rawdata_dir) / "stats_player" / f"stats_player_regpost_{prior_season}.csv"
    if not path.exists():
        return pd.DataFrame()

    df = pd.read_csv(path, low_memory=False)
    df = df[df["season_type"] == "REG"].copy()
    if df.empty:
        return pd.DataFrame()

    for col in ("passing_epa", "rushing_epa", "receiving_epa", "attempts", "carries", "targets"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    agg = df.groupby(["player_id", "player_display_name", "position", "recent_team"], as_index=False).agg(
        passing_epa=("passing_epa", "sum"),
        rushing_epa=("rushing_epa", "sum"),
        receiving_epa=("receiving_epa", "sum"),
        attempts=("attempts", "sum"),
        carries=("carries", "sum"),
        targets=("targets", "sum"),
    )

    agg["pass_epa_rate"] = agg["passing_epa"] / agg["attempts"].clip(lower=1)
    agg["recv_epa_rate"] = agg["receiving_epa"] / agg["targets"].clip(lower=1)
    agg["rush_epa_rate"] = agg["rushing_epa"] / agg["carries"].clip(lower=1)

    return agg
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestLoadPlayerEpa -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add _load_player_epa() helper for preseason player profiles"
```

---

## Task 2: `_preseason_offense()`

**Files:**
- Modify: `services/nn_feature_engine.py`
- Modify: `tests/test_preseason_profiles.py`

Computes per-team `off_pass_epa`, `off_rush_epa`, `qb_tier`, and `ol_av` from depth chart starters and player EPA rates.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
# ── Fixtures shared across offense/defense tests ──────────────────────────────

def _fake_depth_chart() -> pd.DataFrame:
    """Minimal 2026 depth chart for teams AAA and BBB."""
    rows = [
        # AAA offense
        {"team": "AAA", "pos_abb": "QB", "pos_rank": 1, "player_name": "QB Alpha",  "gsis_id": "00-0001"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 1, "player_name": "WR Beta",   "gsis_id": "00-0002"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 2, "player_name": "WR Zeta",   "gsis_id": "00-0010"},
        {"team": "AAA", "pos_abb": "WR", "pos_rank": 3, "player_name": "WR Kappa",  "gsis_id": "00-0011"},
        {"team": "AAA", "pos_abb": "TE", "pos_rank": 1, "player_name": "TE Delta",  "gsis_id": "00-0012"},
        {"team": "AAA", "pos_abb": "TE", "pos_rank": 2, "player_name": "TE Eta",    "gsis_id": "00-0013"},
        {"team": "AAA", "pos_abb": "RB", "pos_rank": 1, "player_name": "RB Gamma",  "gsis_id": "00-0003"},
        {"team": "AAA", "pos_abb": "RB", "pos_rank": 2, "player_name": "RB Theta",  "gsis_id": "00-0014"},
        # OL for AAA
        {"team": "AAA", "pos_abb": "LT", "pos_rank": 1, "player_name": "OL1", "gsis_id": "00-0020"},
        {"team": "AAA", "pos_abb": "LG", "pos_rank": 1, "player_name": "OL2", "gsis_id": "00-0021"},
        {"team": "AAA", "pos_abb": "C",  "pos_rank": 1, "player_name": "OL3", "gsis_id": "00-0022"},
        {"team": "AAA", "pos_abb": "RG", "pos_rank": 1, "player_name": "OL4", "gsis_id": "00-0023"},
        {"team": "AAA", "pos_abb": "RT", "pos_rank": 1, "player_name": "OL5", "gsis_id": "00-0024"},
    ]
    return pd.DataFrame(rows)


def _fake_player_epa() -> pd.DataFrame:
    """Aggregated player EPA rates (output of _load_player_epa)."""
    return pd.DataFrame([
        {"player_id": "00-0001", "player_display_name": "QB Alpha", "position": "QB",
         "recent_team": "OLD", "pass_epa_rate": 0.20, "recv_epa_rate": 0.0, "rush_epa_rate": 0.05,
         "passing_epa": 80.0, "receiving_epa": 0.0, "rushing_epa": 5.0,
         "attempts": 400, "targets": 0, "carries": 100},
        {"player_id": "00-0002", "player_display_name": "WR Beta", "position": "WR",
         "recent_team": "AAA", "pass_epa_rate": 0.0, "recv_epa_rate": 0.60, "rush_epa_rate": 0.0,
         "passing_epa": 0.0, "receiving_epa": 60.0, "rushing_epa": 0.0,
         "attempts": 0, "targets": 100, "carries": 0},
        {"player_id": "00-0003", "player_display_name": "RB Gamma", "position": "RB",
         "recent_team": "BBB", "pass_epa_rate": 0.0, "recv_epa_rate": 0.15, "rush_epa_rate": 0.10,
         "passing_epa": 0.0, "receiving_epa": 3.0, "rushing_epa": 15.0,
         "attempts": 0, "targets": 20, "carries": 150},
    ])


def _fake_roster() -> pd.DataFrame:
    """Minimal roster with age/pfr_id for OL players."""
    return pd.DataFrame([
        {"gsis_id": "00-0001", "pfr_id": "AlphQB00", "full_name": "QB Alpha", "position": "QB",
         "birth_date": "1995-01-01", "years_exp": 5},
        {"gsis_id": "00-0020", "pfr_id": "OL000001", "full_name": "OL1", "position": "T",
         "birth_date": "1997-06-15", "years_exp": 3},
        {"gsis_id": "00-0021", "pfr_id": "OL000002", "full_name": "OL2", "position": "G",
         "birth_date": "1996-03-20", "years_exp": 4},
        {"gsis_id": "00-0022", "pfr_id": "OL000003", "full_name": "OL3", "position": "C",
         "birth_date": "1994-09-10", "years_exp": 6},
        {"gsis_id": "00-0023", "pfr_id": "OL000004", "full_name": "OL4", "position": "G",
         "birth_date": "1998-02-28", "years_exp": 2},
        {"gsis_id": "00-0024", "pfr_id": "OL000005", "full_name": "OL5", "position": "T",
         "birth_date": "1993-11-05", "years_exp": 7},
    ])


def _fake_snap_counts() -> pd.DataFrame:
    """Minimal snap counts for OL players."""
    rows = []
    for pid, name in [("OL000001","OL1"),("OL000002","OL2"),("OL000003","OL3"),
                      ("OL000004","OL4"),("OL000005","OL5")]:
        rows.append({"pfr_player_id": pid, "player": name, "position": "OL",
                     "team": "AAA", "offense_snaps": 900, "defense_snaps": 0,
                     "game_type": "REG"})
    return pd.DataFrame(rows)


# ── _preseason_offense tests ───────────────────────────────────────────────────

class TestPreseasonOffense:
    def test_returns_expected_teams(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        assert "AAA" in result

    def test_off_pass_epa_influenced_by_qb(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # QB Alpha has pass_epa_rate = 0.20 (good QB) → AAA off_pass_epa should be positive
        assert result["AAA"]["off_pass_epa"] > 0.0

    def test_qb_tier_matches_qb_pass_epa_rate(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # qb_tier = starter's pass_epa_rate
        assert result["AAA"]["qb_tier"] == pytest.approx(0.20)

    def test_rookie_qb_gets_discount(self):
        from services.nn_feature_engine import _preseason_offense
        # Depth chart with unknown QB (no player_epa match → rookie discount)
        dc = pd.DataFrame([
            {"team": "BBB", "pos_abb": "QB", "pos_rank": 1,
             "player_name": "Rookie QB", "gsis_id": "99-9999"},
        ])
        result = _preseason_offense(
            dc, _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        # Should use league_avg × 0.75 (not crash)
        assert "BBB" in result
        assert result["BBB"]["qb_tier"] > 0  # league avg × 0.75 is still > 0

    def test_output_has_required_keys(self):
        from services.nn_feature_engine import _preseason_offense
        result = _preseason_offense(
            _fake_depth_chart(), _fake_player_epa(), _fake_roster(), _fake_snap_counts(), season=2026
        )
        for key in ("off_pass_epa", "off_rush_epa", "qb_tier", "ol_av"):
            assert key in result["AAA"], f"Missing key: {key}"
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonOffense -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_preseason_offense()`**

Add to `services/nn_feature_engine.py` after `_load_player_epa()`:

```python
# Position group sets used by preseason profile builders
_OFF_OL_POS = {"LT", "LG", "C", "RG", "RT"}

# Depth-chart snap allocation weights for pass game (QB=65%, WR/TE=35%)
_WR_TE_WEIGHTS = {"WR_1": 0.40, "WR_2": 0.25, "WR_3": 0.10,
                  "TE_1": 0.20, "TE_2": 0.05}

# Depth-chart snap allocation weights for rush game (RB=75%, OL normalised=25%)
_RB_WEIGHTS = {"RB_1": 0.50, "RB_2": 0.25}


def _preseason_offense(
    depth_charts: pd.DataFrame,
    player_epa: pd.DataFrame,
    roster: pd.DataFrame,
    snap_counts: pd.DataFrame,
    season: int,
) -> dict:
    """Build per-team offensive EPA estimates from depth chart + prior-season player stats.

    Returns {team: {off_pass_epa, off_rush_epa, qb_tier, ol_av}}.
    """
    # Build lookup: gsis_id → EPA rates, with player_name fallback
    epa_by_id:   dict = {}
    epa_by_name: dict = {}
    if not player_epa.empty:
        for _, row in player_epa.iterrows():
            epa_by_id[str(row["player_id"])] = row.to_dict()
            epa_by_name[str(row["player_display_name"]).lower()] = row.to_dict()

    # League averages for rookie/unknown fallback
    def _lg_avg(col: str, min_vol: str, min_val: int) -> float:
        if player_epa.empty:
            return 0.0
        qualified = player_epa[player_epa[min_vol] >= min_val]
        if qualified.empty:
            return 0.0
        return float(qualified[col].mean())

    lg_pass_rate  = _lg_avg("pass_epa_rate", "attempts", 100)
    lg_recv_rate  = _lg_avg("recv_epa_rate", "targets",  20)
    lg_rush_rate  = _lg_avg("rush_epa_rate", "carries",  50)

    ROOKIE_DISC = 0.75

    def _lookup(gsis_id: str, name: str) -> dict | None:
        row = epa_by_id.get(str(gsis_id))
        if row is None:
            row = epa_by_name.get(str(name).lower())
        return row

    # OL: existing snap × age multiplier logic (reused from compute_preseason_roster_features)
    sep1 = pd.Timestamp(f"{season}-09-01")
    roster_cp = roster.copy()
    roster_cp["birth_date"] = pd.to_datetime(roster_cp["birth_date"], errors="coerce")
    roster_cp["age"] = ((sep1 - roster_cp["birth_date"]).dt.days / 365.25)
    pfr_to_snaps = {}
    if not snap_counts.empty:
        sc = snap_counts.copy()
        if "game_type" in sc.columns:
            sc = sc[sc["game_type"] == "REG"]
        sc["offense_snaps"] = pd.to_numeric(sc["offense_snaps"], errors="coerce").fillna(0)
        pfr_to_snaps = sc.groupby("pfr_player_id")["offense_snaps"].sum().to_dict()
        name_snaps = sc.groupby("player")["offense_snaps"].sum().to_dict()
    else:
        name_snaps = {}
    ol_vets = pd.to_numeric(
        pd.Series(list(pfr_to_snaps.values())), errors="coerce"
    ).dropna()
    ol_median = float(ol_vets.median()) if not ol_vets.empty else 300.0

    gsis_to_pfr = {}
    if not roster_cp.empty and "gsis_id" in roster_cp.columns and "pfr_id" in roster_cp.columns:
        gsis_to_pfr = {str(r["gsis_id"]): str(r["pfr_id"])
                       for _, r in roster_cp.iterrows()
                       if pd.notna(r.get("pfr_id"))}

    result = {}
    for team, grp in depth_charts.groupby("team"):
        off_pass_epa = 0.0
        off_rush_epa = 0.0
        qb_tier      = lg_pass_rate * ROOKIE_DISC
        ol_av        = 0.0

        # QB (65% of off_pass_epa)
        qb_rows = grp[(grp["pos_abb"] == "QB") & (grp["pos_rank"] == 1)]
        if not qb_rows.empty:
            r = qb_rows.iloc[0]
            data = _lookup(r["gsis_id"], r["player_name"])
            rate = data["pass_epa_rate"] if data and data.get("attempts", 0) >= 100 \
                else lg_pass_rate * ROOKIE_DISC
            qb_tier = rate
            off_pass_epa += 0.65 * rate

        # WR/TE (35% of off_pass_epa split by slot weights)
        for pos_abb, base_key in [("WR", "WR"), ("TE", "TE")]:
            for rank in [1, 2, 3]:
                weight_key = f"{base_key}_{rank}"
                if weight_key not in _WR_TE_WEIGHTS:
                    continue
                slot_rows = grp[(grp["pos_abb"] == pos_abb) & (grp["pos_rank"] == rank)]
                if slot_rows.empty:
                    rate = lg_recv_rate * ROOKIE_DISC
                else:
                    data = _lookup(slot_rows.iloc[0]["gsis_id"], slot_rows.iloc[0]["player_name"])
                    rate = data["recv_epa_rate"] if data and data.get("targets", 0) >= 10 \
                        else lg_recv_rate * ROOKIE_DISC
                off_pass_epa += _WR_TE_WEIGHTS[weight_key] * rate

        # RB (50% of off_rush_epa)
        for rank, weight in [(1, 0.50), (2, 0.25)]:
            rb_rows = grp[(grp["pos_abb"] == "RB") & (grp["pos_rank"] == rank)]
            if rb_rows.empty:
                rate = lg_rush_rate * ROOKIE_DISC
            else:
                data = _lookup(rb_rows.iloc[0]["gsis_id"], rb_rows.iloc[0]["player_name"])
                rate = data["rush_epa_rate"] if data and data.get("carries", 0) >= 30 \
                    else lg_rush_rate * ROOKIE_DISC
            off_rush_epa += weight * rate

        # OL snap × age quality (25% of off_rush_epa; also stored as ol_av for trench)
        ol_grp = grp[grp["pos_abb"].isin(_OFF_OL_POS)]
        for _, p in ol_grp.iterrows():
            gid  = str(p["gsis_id"])
            name = str(p["player_name"])
            pfr  = gsis_to_pfr.get(gid, "")
            # Match age from roster
            roster_match = roster_cp[roster_cp["gsis_id"] == gid]
            age = float(roster_match["age"].iloc[0]) if not roster_match.empty \
                and pd.notna(roster_match["age"].iloc[0]) else 26.0
            mult = compute_age_multiplier(age, "T")
            snaps = pfr_to_snaps.get(pfr) or name_snaps.get(name) or (ol_median * 0.5)
            ol_av += float(snaps) * mult

        off_rush_epa += 0.25 * (ol_av / max(ol_median * 5, 1))  # normalized contribution

        result[team] = {
            "off_pass_epa": off_pass_epa,
            "off_rush_epa": off_rush_epa,
            "qb_tier":      qb_tier,
            "ol_av":        ol_av,
        }

    return result
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonOffense -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add _preseason_offense() for bottom-up team offensive EPA estimates"
```

---

## Task 3: `_preseason_defense()`

**Files:**
- Modify: `services/nn_feature_engine.py`
- Modify: `tests/test_preseason_profiles.py`

Computes per-team `def_pass_epa`, `def_rush_epa`, and `dl_perf` from depth chart + advstats_week_def.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
def _fake_def_advstats() -> pd.DataFrame:
    """Minimal advstats_week_def for DL/LB/CB players."""
    return pd.DataFrame([
        # DL (good pass rusher)
        {"pfr_player_id": "DEF00001", "pfr_player_name": "DE Star",
         "game_type": "REG", "def_sacks": 12.0, "def_pressures": 40.0,
         "def_times_hitqb": 15.0, "def_tackles_combined": 30.0,
         "def_targets": 0.0, "def_yards_allowed_per_tgt": 0.0,
         "def_passer_rating_allowed": 0.0},
        # CB (good coverage — low yards/target allowed)
        {"pfr_player_id": "DEF00002", "pfr_player_name": "CB Good",
         "game_type": "REG", "def_sacks": 0.0, "def_pressures": 0.0,
         "def_times_hitqb": 0.0, "def_tackles_combined": 20.0,
         "def_targets": 60.0, "def_yards_allowed_per_tgt": 5.0,
         "def_passer_rating_allowed": 70.0},
        # CB (bad coverage — high yards/target allowed)
        {"pfr_player_id": "DEF00003", "pfr_player_name": "CB Bad",
         "game_type": "REG", "def_sacks": 0.0, "def_pressures": 0.0,
         "def_times_hitqb": 0.0, "def_tackles_combined": 20.0,
         "def_targets": 60.0, "def_yards_allowed_per_tgt": 12.0,
         "def_passer_rating_allowed": 110.0},
    ])


def _fake_def_snap_counts() -> pd.DataFrame:
    return pd.DataFrame([
        {"pfr_player_id": "DEF00001", "player": "DE Star",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 700},
        {"pfr_player_id": "DEF00002", "player": "CB Good",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 600},
        {"pfr_player_id": "DEF00003", "player": "CB Bad",
         "game_type": "REG", "offense_snaps": 0, "defense_snaps": 580},
    ])


def _fake_def_depth_chart() -> pd.DataFrame:
    return pd.DataFrame([
        # DL
        {"team": "AAA", "pos_abb": "LDE", "pos_rank": 1,
         "player_name": "DE Star", "gsis_id": "00-0030"},
        {"team": "AAA", "pos_abb": "RDE", "pos_rank": 1,
         "player_name": "DE Backup", "gsis_id": "00-0031"},
        # CB
        {"team": "AAA", "pos_abb": "LCB", "pos_rank": 1,
         "player_name": "CB Good", "gsis_id": "00-0040"},
        {"team": "AAA", "pos_abb": "RCB", "pos_rank": 1,
         "player_name": "CB Bad",  "gsis_id": "00-0041"},
        # SS/FS
        {"team": "AAA", "pos_abb": "SS", "pos_rank": 1,
         "player_name": "Safety One", "gsis_id": "00-0050"},
    ])


def _fake_def_roster() -> pd.DataFrame:
    return pd.DataFrame([
        {"gsis_id": "00-0030", "pfr_id": "DEF00001", "full_name": "DE Star",
         "position": "DE", "birth_date": "1997-01-01", "years_exp": 4},
        {"gsis_id": "00-0040", "pfr_id": "DEF00002", "full_name": "CB Good",
         "position": "CB", "birth_date": "1998-06-01", "years_exp": 3},
        {"gsis_id": "00-0041", "pfr_id": "DEF00003", "full_name": "CB Bad",
         "position": "CB", "birth_date": "1999-01-01", "years_exp": 2},
    ])


class TestPreseasonDefense:
    def test_returns_expected_teams(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        assert "AAA" in result

    def test_output_has_required_keys(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        for key in ("def_pass_epa", "def_rush_epa", "dl_perf"):
            assert key in result["AAA"], f"Missing key: {key}"

    def test_good_dl_produces_positive_dl_perf(self):
        from services.nn_feature_engine import _preseason_defense
        result = _preseason_defense(
            _fake_def_depth_chart(), _fake_def_advstats(),
            _fake_def_roster(), _fake_def_snap_counts(), season=2026
        )
        # DE Star has 12 sacks + 40 pressures → high dl_perf
        assert result["AAA"]["dl_perf"] > 0

    def test_good_cb_improves_def_pass_epa(self):
        from services.nn_feature_engine import _preseason_defense
        # Build two teams: AAA with good CB, BBB with bad CB
        dc_good = pd.DataFrame([
            {"team": "AAA", "pos_abb": "LCB", "pos_rank": 1,
             "player_name": "CB Good", "gsis_id": "00-0040"},
        ])
        dc_bad = pd.DataFrame([
            {"team": "BBB", "pos_abb": "LCB", "pos_rank": 1,
             "player_name": "CB Bad", "gsis_id": "00-0041"},
        ])
        dc = pd.concat([dc_good, dc_bad], ignore_index=True)
        result = _preseason_defense(
            dc, _fake_def_advstats(), _fake_def_roster(),
            _fake_def_snap_counts(), season=2026
        )
        # Good CB → better (lower) def_pass_epa (less EPA allowed)
        assert result["AAA"]["def_pass_epa"] < result["BBB"]["def_pass_epa"]

    def test_missing_player_does_not_crash(self):
        from services.nn_feature_engine import _preseason_defense
        dc = pd.DataFrame([
            {"team": "CCC", "pos_abb": "LDE", "pos_rank": 1,
             "player_name": "Unknown DE", "gsis_id": "99-9999"},
        ])
        result = _preseason_defense(
            dc, _fake_def_advstats(), _fake_def_roster(),
            _fake_def_snap_counts(), season=2026
        )
        assert "CCC" in result
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonDefense -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_preseason_defense()`**

Add to `services/nn_feature_engine.py` after `_preseason_offense()`:

```python
_DEF_DL_POS = {"LDE", "RDE", "LDT", "RDT", "NT"}
_DEF_LB_POS = {"WLB", "MLB", "SLB", "LILB", "RILB"}
_DEF_CB_POS = {"LCB", "RCB", "NB"}
_DEF_S_POS  = {"SS", "FS"}

# def_pass_epa weights: DL=45%, LB=20%, CB/S=35%
_DEF_PASS_WEIGHTS = {"dl": 0.45, "lb": 0.20, "cb_s": 0.35}
# def_rush_epa weights: DL=60%, LB=40%
_DEF_RUSH_WEIGHTS = {"dl": 0.60, "lb": 0.40}


def _preseason_defense(
    depth_charts: pd.DataFrame,
    def_advstats: pd.DataFrame,
    roster: pd.DataFrame,
    snap_counts: pd.DataFrame,
    season: int,
) -> dict:
    """Build per-team defensive EPA estimates from depth chart + prior-season advstats.

    Returns {team: {def_pass_epa, def_rush_epa, dl_perf}}.
    def_pass_epa is negative (less EPA allowed = better defense), inverted from the
    coverage quality signals so that a better defense produces a lower (more negative) value.
    """
    # Build advstats lookups: pfr_player_id → stats, pfr_player_name → stats
    adv_by_pfr:  dict = {}
    adv_by_name: dict = {}
    if not def_advstats.empty:
        adv = def_advstats.copy()
        if "game_type" in adv.columns:
            adv = adv[adv["game_type"] == "REG"]
        num_cols = ["def_sacks", "def_pressures", "def_times_hitqb",
                    "def_tackles_combined", "def_targets",
                    "def_yards_allowed_per_tgt", "def_passer_rating_allowed"]
        for c in num_cols:
            if c in adv.columns:
                adv[c] = pd.to_numeric(adv[c], errors="coerce").fillna(0.0)
        agg = adv.groupby("pfr_player_id")[num_cols].sum().reset_index()
        # Compute per-target rates from season totals (total_yards / total_targets)
        if "def_targets" in agg.columns:
            agg["def_yards_per_tgt_avg"] = (
                agg["def_yards_allowed_per_tgt"] / agg["def_targets"].clip(lower=1)
            )
            agg["def_passer_rtg_avg"] = (
                agg["def_passer_rating_allowed"] / agg["def_targets"].clip(lower=1)
            )

        for _, row in agg.iterrows():
            adv_by_pfr[str(row["pfr_player_id"])] = row.to_dict()
        name_agg = adv.groupby("pfr_player_name")[num_cols].sum().reset_index()
        for _, row in name_agg.iterrows():
            adv_by_name[str(row["pfr_player_name"]).lower()] = row.to_dict()

    # Snap totals for per-snap rates
    snap_by_pfr: dict = {}
    if not snap_counts.empty:
        sc = snap_counts.copy()
        if "game_type" in sc.columns:
            sc = sc[sc["game_type"] == "REG"]
        sc["defense_snaps"] = pd.to_numeric(sc["defense_snaps"], errors="coerce").fillna(0)
        snap_by_pfr = sc.groupby("pfr_player_id")["defense_snaps"].sum().to_dict()

    # gsis_id → pfr_id bridge
    gsis_to_pfr = {}
    if not roster.empty and "gsis_id" in roster.columns and "pfr_id" in roster.columns:
        gsis_to_pfr = {str(r["gsis_id"]): str(r["pfr_id"])
                       for _, r in roster.iterrows()
                       if pd.notna(r.get("pfr_id"))}

    # League average DL pressure score per snap (for fallback)
    all_dl_scores = [
        (v.get("def_sacks", 0) * DL_SACK_WEIGHT
         + v.get("def_pressures", 0) * DL_PRESSURE_WEIGHT
         + v.get("def_times_hitqb", 0) * DL_HIT_WEIGHT)
        / max(snap_by_pfr.get(k, 1), 1)
        for k, v in adv_by_pfr.items()
        if snap_by_pfr.get(k, 0) > 100
    ]
    lg_dl_score_per_snap = float(np.mean(all_dl_scores)) if all_dl_scores else 0.01

    # League average CB coverage: yards allowed per target (lower = better)
    all_cb_ytgt = [
        v.get("def_yards_per_tgt_avg", v.get("def_yards_allowed_per_tgt", 9.0))
        for v in adv_by_pfr.values()
        if v.get("def_targets", 0) >= 20
    ]
    lg_cb_ytgt = float(np.mean(all_cb_ytgt)) if all_cb_ytgt else 9.0

    ROOKIE_DISC = 0.75

    def _get_adv(gsis_id: str, name: str) -> dict | None:
        pfr = gsis_to_pfr.get(str(gsis_id), "")
        row = adv_by_pfr.get(pfr)
        if row is None:
            row = adv_by_name.get(str(name).lower())
        return row

    def _dl_score(gsis_id: str, name: str) -> float:
        pfr  = gsis_to_pfr.get(str(gsis_id), "")
        snps = snap_by_pfr.get(pfr, 0)
        adv  = _get_adv(gsis_id, name)
        if adv is None or snps < 50:
            return lg_dl_score_per_snap * ROOKIE_DISC * 500  # median snaps
        raw = (adv.get("def_sacks", 0) * DL_SACK_WEIGHT
               + adv.get("def_pressures", 0) * DL_PRESSURE_WEIGHT
               + adv.get("def_times_hitqb", 0) * DL_HIT_WEIGHT)
        return float(raw)

    def _cb_coverage_score(gsis_id: str, name: str) -> float:
        """Returns inverted coverage quality: negative = good (less EPA allowed)."""
        adv = _get_adv(gsis_id, name)
        if adv is None or adv.get("def_targets", 0) < 10:
            return -(lg_cb_ytgt * ROOKIE_DISC - lg_cb_ytgt)  # 0 (league avg)
        ytgt = adv.get("def_yards_per_tgt_avg", adv.get("def_yards_allowed_per_tgt", lg_cb_ytgt))
        # Invert: better coverage → more negative score (lower EPA allowed)
        return -(ytgt - lg_cb_ytgt) / max(lg_cb_ytgt, 1.0)

    result = {}
    for team, grp in depth_charts.groupby("team"):
        dl_pass_score = 0.0
        dl_rush_score = 0.0
        lb_pass_score = 0.0
        lb_rush_score = 0.0
        cb_s_score    = 0.0
        dl_perf_total = 0.0

        # DL
        dl_grp = grp[grp["pos_abb"].isin(_DEF_DL_POS) & (grp["pos_rank"] <= 2)]
        for _, p in dl_grp.iterrows():
            score = _dl_score(p["gsis_id"], p["player_name"])
            pfr   = gsis_to_pfr.get(str(p["gsis_id"]), "")
            snps  = snap_by_pfr.get(pfr, 500)
            per_snap = score / max(snps, 1)
            dl_pass_score += per_snap
            dl_rush_score += score / max(snps, 1)  # run stops from tackle stats
            dl_perf_total += score

        # LB
        lb_grp = grp[grp["pos_abb"].isin(_DEF_LB_POS) & (grp["pos_rank"] <= 2)]
        for _, p in lb_grp.iterrows():
            adv = _get_adv(p["gsis_id"], p["player_name"])
            pfr = gsis_to_pfr.get(str(p["gsis_id"]), "")
            snps = snap_by_pfr.get(pfr, 500)
            if adv:
                rush_contrib = (adv.get("def_tackles_combined", 0)
                                + adv.get("def_sacks", 0) * 2) / max(snps, 1)
                pass_contrib = (adv.get("def_pressures", 0)
                                + adv.get("def_sacks", 0) * 3) / max(snps, 1)
            else:
                rush_contrib = pass_contrib = lg_dl_score_per_snap * ROOKIE_DISC
            lb_pass_score += pass_contrib
            lb_rush_score += rush_contrib

        # CB/S
        cb_grp = grp[grp["pos_abb"].isin(_DEF_CB_POS | _DEF_S_POS) & (grp["pos_rank"] == 1)]
        for _, p in cb_grp.iterrows():
            cb_s_score += _cb_coverage_score(p["gsis_id"], p["player_name"])
        if len(cb_grp) > 0:
            cb_s_score /= len(cb_grp)  # average across starters

        # Blend into EPA dimensions
        def_pass_epa = (
            _DEF_PASS_WEIGHTS["dl"]   * dl_pass_score
            + _DEF_PASS_WEIGHTS["lb"]   * lb_pass_score
            + _DEF_PASS_WEIGHTS["cb_s"] * cb_s_score
        )
        def_rush_epa = (
            _DEF_RUSH_WEIGHTS["dl"] * dl_rush_score
            + _DEF_RUSH_WEIGHTS["lb"] * lb_rush_score
        )

        result[team] = {
            "def_pass_epa": def_pass_epa,
            "def_rush_epa": def_rush_epa,
            "dl_perf":      dl_perf_total,
        }

    return result
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonDefense -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add _preseason_defense() for bottom-up team defensive EPA estimates"
```

---

## Task 4: `compute_preseason_player_profiles()` Orchestrator + Normalization

**Files:**
- Modify: `services/nn_feature_engine.py`
- Modify: `tests/test_preseason_profiles.py`

Orchestrates offense + defense, merges output, normalizes each EPA dimension to be centered at the league mean (so the average team = 0, comparable to the model's training scale).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestComputePreseasonPlayerProfiles:
    def _write_files(self, tmp_path, season=2026):
        prior = season - 1
        (tmp_path / "stats_player").mkdir(exist_ok=True)
        _fake_player_stats().to_csv(
            tmp_path / "stats_player" / f"stats_player_regpost_{prior}.csv", index=False)

        (tmp_path / "depth_charts").mkdir(exist_ok=True)
        dc = pd.concat([_fake_depth_chart(), _fake_def_depth_chart()], ignore_index=True)
        dc.to_csv(tmp_path / "depth_charts" / f"depth_charts_{season}.csv", index=False)

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
        result = compute_preseason_player_profiles(2026, tmp_path)
        assert isinstance(result, dict)
        assert "AAA" in result

    def test_all_required_keys_present(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path)
        for key in ("off_pass_epa", "off_rush_epa", "def_pass_epa",
                    "def_rush_epa", "ol_av", "dl_perf", "qb_tier"):
            assert key in result["AAA"], f"Missing: {key}"

    def test_epa_values_are_floats(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        self._write_files(tmp_path)
        result = compute_preseason_player_profiles(2026, tmp_path)
        for key in ("off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa"):
            assert isinstance(result["AAA"][key], float), f"{key} is not float"

    def test_returns_empty_dict_if_files_missing(self, tmp_path):
        from services.nn_feature_engine import compute_preseason_player_profiles
        result = compute_preseason_player_profiles(2026, tmp_path)
        assert result == {}
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestComputePreseasonPlayerProfiles -v
```
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `compute_preseason_player_profiles()`**

Add to `services/nn_feature_engine.py` after `_preseason_defense()`:

```python
def compute_preseason_player_profiles(target_season: int, rawdata_dir) -> dict:
    """Build per-team EPA quality estimates from projected roster + prior-season player stats.

    Replaces compute_preseason_roster_features() for all position groups.
    Returns {team: {off_pass_epa, off_rush_epa, def_pass_epa, def_rush_epa,
                    ol_av, dl_perf, qb_tier}}.
    Returns {} if required files (roster or depth_charts) are missing.
    """
    prior = target_season - 1
    rd = Path(rawdata_dir)

    roster_path  = rd / "rosters"     / f"roster_{target_season}.csv"
    dc_path      = rd / "depth_charts"/ f"depth_charts_{target_season}.csv"
    adv_def_path = rd / "pfr_advstats"/ f"advstats_week_def_{prior}.csv"
    snap_path    = rd / "snap_counts" / f"snap_counts_{prior}.csv"

    if not roster_path.exists() or not dc_path.exists():
        return {}

    roster      = pd.read_csv(roster_path, low_memory=False)
    depth_chart = pd.read_csv(dc_path,     low_memory=False)
    depth_chart["team"] = depth_chart["team"].apply(_normalize_team)
    roster["team"]      = roster["team"].apply(_normalize_team) \
        if "team" in roster.columns else roster.get("recent_team", pd.Series())

    def_advstats = pd.read_csv(adv_def_path, low_memory=False) \
        if adv_def_path.exists() else pd.DataFrame()
    snap_counts  = pd.read_csv(snap_path,    low_memory=False) \
        if snap_path.exists() else pd.DataFrame()

    player_epa = _load_player_epa(prior, rawdata_dir)

    off = _preseason_offense(depth_chart, player_epa, roster, snap_counts, target_season)
    dfe = _preseason_defense(depth_chart, def_advstats, roster, snap_counts, target_season)

    # Merge offense + defense per team
    all_teams = set(off) | set(dfe)
    raw: dict = {}
    for team in all_teams:
        raw[team] = {
            **off.get(team, {"off_pass_epa": 0.0, "off_rush_epa": 0.0,
                             "qb_tier": 0.0, "ol_av": 0.0}),
            **dfe.get(team, {"def_pass_epa": 0.0, "def_rush_epa": 0.0, "dl_perf": 0.0}),
        }

    if not raw:
        return {}

    # Normalize each EPA dimension: subtract league mean → average team = 0
    for dim in ("off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa"):
        vals = [v[dim] for v in raw.values()]
        mu = float(np.mean(vals))
        for team in raw:
            raw[team][dim] = float(raw[team][dim] - mu)

    return raw
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestComputePreseasonPlayerProfiles -v
```
Expected: PASS (4 tests)

- [ ] **Step 5: Run all profile tests so far**

```
pytest tests/test_preseason_profiles.py -v
```
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```
git add services/nn_feature_engine.py tests/test_preseason_profiles.py
git commit -m "feat: add compute_preseason_player_profiles() orchestrator with league normalization"
```

---

## Task 5: Update `NNProjectionEngine.initialize()`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_preseason_profiles.py`

Replace the `compute_preseason_roster_features()` call with `compute_preseason_player_profiles()`. Remove `_preseason_roster` and `_preseason_norm` attributes.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestNNProjectionEngineInitialize:
    def test_preseason_profiles_set_when_snap_empty(self, tmp_path, monkeypatch):
        """When no 2026 snap data exists, initialize() should set _preseason_profiles."""
        import services.nn_projection_engine as eng_mod
        from unittest.mock import patch, MagicMock

        fake_profiles = {"KC": {"off_pass_epa": 0.1, "off_rush_epa": 0.05,
                                "def_pass_epa": -0.1, "def_rush_epa": -0.05,
                                "ol_av": 1200.0, "dl_perf": 80.0, "qb_tier": 0.18}}

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"), \
             patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch("services.nn_projection_engine.compute_preseason_player_profiles",
                   return_value=fake_profiles) as mock_fn, \
             patch.object(eng_mod.NNProjectionEngine, "_build_team_profiles",
                          return_value=pd.DataFrame()):
            from services.nn_projection_engine import NNProjectionEngine
            engine = NNProjectionEngine()
            engine.initialize(2026)

        mock_fn.assert_called_once()
        assert hasattr(engine, "_preseason_profiles")
        assert engine._preseason_profiles == fake_profiles

    def test_preseason_roster_and_norm_not_set(self, tmp_path, monkeypatch):
        """_preseason_roster and _preseason_norm should not be set after initialize()."""
        import services.nn_projection_engine as eng_mod
        from unittest.mock import patch

        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"), \
             patch("services.nn_projection_engine.build_master_feature_table",
                   return_value=pd.DataFrame()), \
             patch("services.nn_projection_engine.compute_preseason_player_profiles",
                   return_value={}), \
             patch.object(eng_mod.NNProjectionEngine, "_build_team_profiles",
                          return_value=pd.DataFrame()):
            from services.nn_projection_engine import NNProjectionEngine
            engine = NNProjectionEngine()
            engine.initialize(2026)

        assert not hasattr(engine, "_preseason_roster") or engine._preseason_roster == {}
        assert not hasattr(engine, "_preseason_norm") or engine._preseason_norm is None
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestNNProjectionEngineInitialize -v
```
Expected: FAIL

- [ ] **Step 3: Update `initialize()` in `services/nn_projection_engine.py`**

Find the existing `initialize()` method. Replace the `if snap_empty:` block:

```python
# REMOVE these imports at top of file if present (no longer needed):
# from services.nn_feature_engine import compute_preseason_roster_features

# ADD to the imports block:
from services.nn_feature_engine import (
    build_master_feature_table,
    RAWDATA_DIR,
    _read_csv_safe,
    _normalize_team,
    compute_preseason_roster_features,   # keep temporarily for fallback
    compute_preseason_player_profiles,   # new
)
```

Replace the `snap_empty` block in `initialize()`:

```python
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

Also update `__init__()` to initialize `_preseason_profiles` and remove `_preseason_roster`/`_preseason_norm`:

```python
    def __init__(self):
        self.svc = NNPredictionService()
        self.svc.load_model()
        self.xgb_svc = XGBPredictionService()
        self.xgb_svc.load_model()
        self.lr_svc = LRPredictionService()
        self.lr_svc.load_model()
        self._team_profiles = pd.DataFrame()
        self._preseason_profiles: dict = {}
        # Kept as empty defaults so legacy fallback in _precompute_static_features doesn't AttributeError
        self._preseason_roster: dict = {}
        self._preseason_norm = None
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_preseason_profiles.py::TestNNProjectionEngineInitialize -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```
git add services/nn_projection_engine.py tests/test_preseason_profiles.py
git commit -m "feat: NNProjectionEngine.initialize() uses compute_preseason_player_profiles()"
```

---

## Task 6: Update `_build_initial_state()` and `_precompute_static_features()`

**Files:**
- Modify: `services/nn_projection_engine.py`
- Modify: `tests/test_preseason_profiles.py`

Wire `_preseason_profiles` into the two methods that set up the simulation starting state.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_preseason_profiles.py`:

```python
class TestPreseasonProfilesWiredIntoSimulation:
    def _make_engine_with_profiles(self, profiles: dict):
        """Return a mock engine with preseason profiles set."""
        from unittest.mock import patch
        with patch("services.nn_projection_engine.NNPredictionService"), \
             patch("services.nn_projection_engine.XGBPredictionService"), \
             patch("services.nn_projection_engine.LRPredictionService"):
            from services.nn_projection_engine import NNProjectionEngine
            from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
            engine = NNProjectionEngine()

        engine._team_profiles = pd.DataFrame([
            {"team": "KC",  "elo_pre": 1580.0,
             "off_pass_epa_roll": 0.05, "off_rush_epa_roll": 0.02,
             "def_pass_epa_roll": 0.03, "def_rush_epa_roll": 0.01,
             "margin_roll": 5.0,
             **{c: 0.0 for c in NN_FC}},
            {"team": "TEN", "elo_pre": 1420.0,
             "off_pass_epa_roll": -0.05, "off_rush_epa_roll": -0.02,
             "def_pass_epa_roll": -0.03, "def_rush_epa_roll": -0.01,
             "margin_roll": -5.0,
             **{c: 0.0 for c in NN_FC}},
        ])
        engine._preseason_profiles = profiles
        engine._preseason_norm = None
        return engine

    def test_preseason_epa_overrides_profile_epa(self):
        """_build_initial_state() should use preseason_profiles EPA, not team_profiles EPA."""
        profiles = {
            "KC":  {"off_pass_epa": 0.25, "off_rush_epa": 0.10,
                    "def_pass_epa": -0.15, "def_rush_epa": -0.08,
                    "ol_av": 1500.0, "dl_perf": 100.0, "qb_tier": 0.25},
            "TEN": {"off_pass_epa": -0.20, "off_rush_epa": -0.05,
                    "def_pass_epa": 0.10, "def_rush_epa": 0.05,
                    "ol_av": 800.0, "dl_perf": 40.0, "qb_tier": -0.05},
        }
        engine = self._make_engine_with_profiles(profiles)
        state, team_list, team_idx = engine._build_initial_state()

        kc  = team_idx["KC"]
        ten = team_idx["TEN"]
        # Dim 1 = off_pass_epa: should be from preseason_profiles, not team_profiles
        assert state[kc, 1] == pytest.approx(0.25, abs=0.01)
        assert state[ten, 1] == pytest.approx(-0.20, abs=0.01)

    def test_elo_not_overridden_by_profiles(self):
        """Elo (dim 0) should come from team_profiles elo_pre, not preseason_profiles."""
        profiles = {
            "KC":  {"off_pass_epa": 0.1, "off_rush_epa": 0.0,
                    "def_pass_epa": 0.0, "def_rush_epa": 0.0,
                    "ol_av": 1000.0, "dl_perf": 50.0, "qb_tier": 0.1},
            "TEN": {"off_pass_epa": -0.1, "off_rush_epa": 0.0,
                    "def_pass_epa": 0.0, "def_rush_epa": 0.0,
                    "ol_av": 800.0, "dl_perf": 30.0, "qb_tier": -0.05},
        }
        engine = self._make_engine_with_profiles(profiles)
        state, _, team_idx = engine._build_initial_state()

        assert state[team_idx["KC"],  0] == pytest.approx(1580.0)
        assert state[team_idx["TEN"], 0] == pytest.approx(1420.0)

    def test_trench_metric_uses_profiles_ol_dl(self):
        """_precompute_static_features() uses ol_av/dl_perf from _preseason_profiles."""
        profiles = {
            "KC":  {"off_pass_epa": 0.1, "off_rush_epa": 0.0,
                    "def_pass_epa": 0.0, "def_rush_epa": 0.0,
                    "ol_av": 2000.0, "dl_perf": 200.0, "qb_tier": 0.1},
            "TEN": {"off_pass_epa": -0.1, "off_rush_epa": 0.0,
                    "def_pass_epa": 0.0, "def_rush_epa": 0.0,
                    "ol_av": 500.0, "dl_perf": 20.0, "qb_tier": -0.1},
        }
        engine = self._make_engine_with_profiles(profiles)
        sched = pd.DataFrame([
            {"home_team": "KC", "away_team": "TEN", "week": 1, "game_type": "REG"}
        ])
        feats = engine._precompute_static_features(sched)
        from services.nn_feature_engine import FEATURE_COLUMNS as NN_FC
        col_idx = {c: i for i, c in enumerate(NN_FC)}
        trench = feats["W01_KC_TEN"][col_idx["trench_dominance_metric"]]
        # KC has much better OL+DL than TEN → positive trench for KC as home team
        assert trench > 0
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_preseason_profiles.py::TestPreseasonProfilesWiredIntoSimulation -v
```
Expected: FAIL

- [ ] **Step 3: Update `_build_initial_state()`**

In `services/nn_projection_engine.py`, after building `state_template` from `_team_profiles`, add the override block:

```python
    def _build_initial_state(self) -> tuple:
        profile_dict = {row["team"]: row.to_dict() for _, row in self._team_profiles.iterrows()}
        team_list = sorted(profile_dict.keys())
        team_idx = {t: i for i, t in enumerate(team_list)}

        state_template = np.zeros((len(team_list), 6), dtype=np.float32)
        for team, idx in team_idx.items():
            p = profile_dict[team]
            state_template[idx, 0] = float(p.get("elo_pre",           1500.0))
            state_template[idx, 1] = float(p.get("off_pass_epa_roll",    0.0))
            state_template[idx, 2] = float(p.get("off_rush_epa_roll",    0.0))
            state_template[idx, 3] = float(p.get("def_pass_epa_roll",    0.0))
            state_template[idx, 4] = float(p.get("def_rush_epa_roll",    0.0))
            state_template[idx, 5] = float(p.get("margin_roll",          0.0))

        # Override EPA dims 1-4 with bottom-up preseason player profiles when available
        if self._preseason_profiles:
            for team, idx in team_idx.items():
                pp = self._preseason_profiles.get(team, {})
                if pp:
                    state_template[idx, 1] = float(pp.get("off_pass_epa", state_template[idx, 1]))
                    state_template[idx, 2] = float(pp.get("off_rush_epa", state_template[idx, 2]))
                    state_template[idx, 3] = float(pp.get("def_pass_epa", state_template[idx, 3]))
                    state_template[idx, 4] = float(pp.get("def_rush_epa", state_template[idx, 4]))

        return state_template, team_list, team_idx
```

- [ ] **Step 4: Update `_precompute_static_features()` trench block**

Find the trench section in `_precompute_static_features()` that currently checks `self._preseason_roster and self._preseason_norm`. Replace it:

```python
        # Trench: use preseason_profiles ol_av/dl_perf when available
        if self._preseason_profiles:
            h_pr = self._preseason_profiles.get(ht, {})
            a_pr = self._preseason_profiles.get(at, {})
            if h_pr and a_pr:
                # Normalise within the current set of profiles
                all_ol = [v.get("ol_av", 0.0) for v in self._preseason_profiles.values()]
                all_dl = [v.get("dl_perf", 0.0) for v in self._preseason_profiles.values()]
                ol_mu, ol_sig = float(np.mean(all_ol)), max(float(np.std(all_ol)), 1.0)
                dl_mu, dl_sig = float(np.mean(all_dl)), max(float(np.std(all_dl)), 1.0)
                h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                       + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                       + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
                feat[col_idx["trench_dominance_metric"]] = float(h_z - a_z)
            else:
                feat[col_idx["trench_dominance_metric"]] = (
                    float(hp.get("trench_score", 0.0)) - float(ap.get("trench_score", 0.0))
                )
        elif self._preseason_roster and self._preseason_norm:
            # Legacy fallback (compute_preseason_roster_features output)
            ol_mu, ol_sig, dl_mu, dl_sig = self._preseason_norm
            h_pr = self._preseason_roster.get(ht, {})
            a_pr = self._preseason_roster.get(at, {})
            h_z = ((h_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                   + (h_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
            a_z = ((a_pr.get("ol_av", ol_mu) - ol_mu) / ol_sig
                   + (a_pr.get("dl_perf", dl_mu) - dl_mu) / dl_sig)
            feat[col_idx["trench_dominance_metric"]] = float(h_z - a_z)
        else:
            feat[col_idx["trench_dominance_metric"]] = (
                float(hp.get("trench_score", 0.0)) - float(ap.get("trench_score", 0.0))
            )
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_preseason_profiles.py::TestPreseasonProfilesWiredIntoSimulation -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Run full test suite**

```
pytest tests/ -x -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing, no regressions

- [ ] **Step 7: Commit**

```
git add services/nn_projection_engine.py tests/test_preseason_profiles.py
git commit -m "feat: wire preseason player profiles into _build_initial_state() and trench metric"
```

---

## Task 7: Validate and Push

**Files:** No code changes — validation only.

- [ ] **Step 1: Sync latest roster/depth chart data**

```
python scripts/sync_nflverse_data.py --seasons 2025 2026
```

Expected: no errors, updated files in `rawdata/rosters/roster_2026.csv` and `rawdata/depth_charts/depth_charts_2026.csv`

- [ ] **Step 2: Run predict_season dry-run and check DE trade is captured**

```
python scripts/predict_season.py --season 2026 --simulations 1000 --dry-run
```

Look for the two teams involved in the big DE trade:
- The team that LOST the DE should show a lower projected win total
- The team that GAINED the DE should show a higher projected win total

Also confirm: win range is still ≥ 5 points wide and std dev ≥ 2.0.

- [ ] **Step 3: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing

- [ ] **Step 4: Commit and push**

```
git add .
git commit -m "test: validate preseason player profiles reflect 2026 roster and DE trade"
git push origin main
```

- [ ] **Step 5: Rebuild local cache**

```
python scripts/predict_season.py --season 2026
python scripts/backfill_schedule_predictions.py --seasons 2026 2026 --firestore
python scripts/refresh_local_pkls.py
```
