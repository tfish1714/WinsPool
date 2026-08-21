# Preseason Player Profiles — Design Spec
**Date:** 2026-06-02
**Status:** Approved for implementation

---

## Problem

Preseason win projections currently start from 2025 **team-level** stat averages. A team that traded away their star DE, signed a new QB, or drafted a top WR looks identical in the model to last year's version of that team. The bottom-up roster signal is only used for OL/DL trench metrics; QB, skill positions, coverage, and run-stopping are all ignored.

---

## Requirements

1. Build per-team offensive and defensive EPA estimates from the **actual projected 2026 roster** (roster_2026.csv + depth_charts_2026.csv) and **2025 individual player performance** (advstats_week_*_2025.csv).
2. Identify starters from depth chart rank — draft picks taking a starting spot are captured automatically.
3. Player performance is portable across teams — a traded DE carries his 2025 sack/pressure rate to his new team.
4. Rookies and players with no 2025 data get position-group league average × 0.75 rookie discount.
5. Output must be in the same numerical units as the model's training data (EPA per play scale) — no model retraining required.
6. Graceful degradation: missing files or unmatched players fall back to league averages, never crash.
7. Data sync (`sync_nflverse_data.py --seasons 2025 2026`) is a **manual prerequisite** — the function does not trigger sync automatically.
8. No changes to `predict_season.py`, `backfill_schedule_predictions.py`, or the simulation loop.

---

## Architecture

### New function: `compute_preseason_player_profiles(target_season, rawdata_dir) -> dict`

Lives in `services/nn_feature_engine.py` alongside the existing `compute_preseason_roster_features()`. Replaces that call in `NNProjectionEngine.initialize()`.

Orchestrates two private helpers:

```
_preseason_offense(roster, depth_charts, pass_advstats, rec_advstats,
                   rush_advstats, snap_counts)
    → {team: {off_pass_epa, off_rush_epa, qb_tier, ol_av}}

_preseason_defense(roster, depth_charts, def_advstats, snap_counts)
    → {team: {def_pass_epa, def_rush_epa, dl_perf}}
```

**Output schema:**
```python
{
    team: {
        "off_pass_epa": float,  # replaces off_pass_epa_roll in starting state
        "off_rush_epa": float,  # replaces off_rush_epa_roll
        "def_pass_epa": float,  # replaces def_pass_epa_roll
        "def_rush_epa": float,  # replaces def_rush_epa_roll
        "ol_av":        float,  # feeds trench_dominance_metric (existing)
        "dl_perf":      float,  # feeds trench_dominance_metric (existing)
        "qb_tier":      float,  # QB EPA per dropback, feeds qb_resilience_delta
    }
}
```

---

## Data Sources

| File | Used for |
|---|---|
| `rawdata/rosters/roster_{season}.csv` | Who is on each team, age, birth_date, pfr_id |
| `rawdata/depth_charts/depth_charts_{season}.csv` | Starter/backup rank per position |
| `rawdata/pfr_advstats/advstats_week_pass_{prior}.csv` | QB passing EPA, dropbacks |
| `rawdata/pfr_advstats/advstats_week_rec_{prior}.csv` | WR/TE/RB receiving EPA, targets |
| `rawdata/pfr_advstats/advstats_week_rush_{prior}.csv` | RB/QB rushing EPA, carries |
| `rawdata/pfr_advstats/advstats_week_def_{prior}.csv` | DL/LB/CB sacks, pressures, coverage |
| `rawdata/snap_counts/snap_counts_{prior}.csv` | Per-player season snap totals (for per-play rates) |

Where `prior = target_season - 1`.

---

## Position-Group Methodology

### Snap weighting principle

- **Depth chart is the source of projected 2026 snap share** — starter vs. backup ranks determine how much each player contributes regardless of which team they were on in 2025.
- **2025 advstats are the source of player quality** — performance rate (EPA per play, pressures per snap) is fully portable across teams.
- **Snap counts are used for per-play normalization only** — divide 2025 cumulative stats by 2025 snaps to get a per-play rate, then apply the depth-chart-based 2026 snap allocation.

### `_preseason_offense()`

**QB → `off_pass_epa` (65% weight) + `qb_tier`**
- Identify starter: depth_chart_position = QB, depth_chart_position_rank = 1
- `qb_epa_per_dropback` = total passing EPA / total dropbacks from `advstats_week_pass`
- `qb_tier` = raw EPA per dropback
- No 2025 data (rookie / new signing) → league-average QB EPA per dropback × 0.75

**WR/TE → `off_pass_epa` (35% weight)**
- Depth chart ranks 1–3 at WR, ranks 1–2 at TE
- `receiver_epa_per_target` from `advstats_week_rec`
- Snap allocation weights: WR1=40%, WR2=25%, WR3=10%, TE1=20%, TE2=5%
- No 2025 data → league-average receiver EPA per target × 0.75

**RB + OL → `off_rush_epa`**
- RB1 (rank 1): `rushing_epa_per_carry` from `advstats_week_rush`, weight 50%
- RB2 (rank 2): same, weight 25%
- OL quality: existing `ol_av` logic (snap × age multiplier per OL player), normalized, weight 25%
- No 2025 data at RB → league-average rushing EPA per carry × 0.75

### `_preseason_defense()`

**DL → `def_pass_epa` (45% weight) + `def_rush_epa` (60% weight) + `dl_perf`**
- Starters: depth chart rank 1–2 at DE and DT
- Pass rush score: sacks×6 + pressures×1.5 + qb_hits×1 (existing `dl_perf` formula)
- Run stop score: tackles-for-loss + run_stops from `advstats_week_def`
- Per-snap rates via `snap_counts`
- No 2025 data → league average × 0.75

**LB → `def_pass_epa` (20% weight) + `def_rush_epa` (40% weight)**
- Depth chart rank 1–2 at LB/ILB/OLB
- Pass rush contribution: sacks + pressures from `advstats_week_def`
- Run stop contribution: tackles + TFL from `advstats_week_def`
- Per-snap rates via `snap_counts`

**CB/S → `def_pass_epa` (35% weight)**
- Depth chart rank 1–2 at CB, rank 1 at S
- Coverage quality: yards allowed per coverage snap (inverted — lower is better) from `advstats_week_def`
- Targets allowed per coverage snap as secondary signal
- No 2025 data → league-average coverage rate × 0.75

### Normalization

1. All per-play rates are computed as `cumulative_stat / season_snaps` from `snap_counts_{prior}`
2. Each EPA dimension is scaled to match the 2025 league distribution: z-score using 2025 team EPA mean and std, then convert back to EPA units
3. This ensures `off_pass_epa`, `def_pass_epa`, etc. sit in the same numerical range as the model's training features

---

## Integration with `NNProjectionEngine`

### `initialize()` change

`_preseason_profiles` replaces both `_preseason_roster` and `_preseason_norm` — normalization is now done inside `compute_preseason_player_profiles()`, so the norm tuple is no longer needed externally.

```python
# Replace:
if snap_empty:
    self._preseason_roster = compute_preseason_roster_features(season, RAWDATA_DIR)
    if self._preseason_roster:
        # ... build _preseason_norm ...

# With:
if snap_empty:
    self._preseason_profiles = compute_preseason_player_profiles(season, RAWDATA_DIR)
    if self._preseason_profiles:
        logger.info("Preseason player profiles built for %d teams", len(self._preseason_profiles))
    # _preseason_roster and _preseason_norm no longer set
```

### `_build_initial_state()` change

After building `state_template` from `_team_profiles`, check for `_preseason_profiles` and override the four EPA dimensions + `ol_av` + `dl_perf`:

```python
if hasattr(self, "_preseason_profiles") and self._preseason_profiles:
    for team, idx in team_idx.items():
        p = self._preseason_profiles.get(team, {})
        if p:
            state_template[idx, 1] = p.get("off_pass_epa", state_template[idx, 1])
            state_template[idx, 2] = p.get("off_rush_epa", state_template[idx, 2])
            state_template[idx, 3] = p.get("def_pass_epa", state_template[idx, 3])
            state_template[idx, 4] = p.get("def_rush_epa", state_template[idx, 4])
```

### `_precompute_static_features()` change

The `trench_dominance_metric` computation already checks `self._preseason_roster`. Update to check `self._preseason_profiles` instead:

```python
if hasattr(self, "_preseason_profiles") and self._preseason_profiles:
    h_pr = self._preseason_profiles.get(ht, {})
    a_pr = self._preseason_profiles.get(at, {})
    # use ol_av and dl_perf from profiles
```

---

## Graceful Degradation

| Scenario | Behavior |
|---|---|
| `roster_{season}.csv` missing | Returns `{}` → `initialize()` keeps 2025 team averages |
| `depth_charts_{season}.csv` missing | Returns `{}` → fallback |
| Player has no 2025 advstats match | Uses position-group league average × 0.75 |
| Rookie (no prior season) | Same as above |
| Team not in depth chart | Uses 2025 team-average profile for that team |

---

## Data Sync Prerequisite

Run before generating 2026 preseason predictions, especially after trades:

```bash
python scripts/sync_nflverse_data.py --seasons 2025 2026
```

This refreshes `roster_2026.csv`, `depth_charts_2026.csv`, and snap counts. The function does not auto-sync.

---

## Files Changed

| File | Change |
|---|---|
| `services/nn_feature_engine.py` | Add `compute_preseason_player_profiles()`, `_preseason_offense()`, `_preseason_defense()` |
| `services/nn_projection_engine.py` | Update `initialize()`, `_build_initial_state()`, `_precompute_static_features()` |
| `tests/test_preseason_profiles.py` | New test file |

No changes to `scripts/predict_season.py`, `scripts/backfill_schedule_predictions.py`, or any routes/templates.

---

## Out of Scope (separate specs)

- **Spec 3:** In-season weekly profile rebuilds using actual 2026 advstats + injury data
- **Spec C:** Model retrain adding bottom-up features (qb_tier, WR talent index, DL pressure rate, CB coverage rate) as new FEATURE_COLUMNS
