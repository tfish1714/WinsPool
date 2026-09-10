# Historical depth_charts Schema Compatibility — Design Spec

**Date:** 2026-08-23
**Status:** Approved, ready for implementation plan.

## Origin

User asked to review/recalibrate `PRESEASON_ELO_WEIGHTS` (`services/constants.py`),
the hand-picked weights controlling how `compute_preseason_player_profiles()`'s
7 quality dimensions (qb_tier, off_pass_epa, def_pass_epa, dl_perf, ol_av,
off_rush_epa, def_rush_epa) combine into the preseason Elo boost that seeds
`NNProjectionEngine.simulate_season()` for a season with no in-season data yet.

Investigating what data was available to validate any candidate weight set
against surfaced a blocking bug: `compute_preseason_player_profiles()`
(`services/nn_feature_engine.py`) throws `KeyError('team')` for every season
2014-2024, and only succeeds for 2025-2026.

## Root cause

nflverse changed the `depth_charts` release schema starting with the 2025
file. `compute_preseason_player_profiles()` and its two builders,
`_preseason_offense()`/`_preseason_defense()`, hardcode the new schema:

| | 2018-2024 (`rawdata/depth_charts/depth_charts_{year}.csv`) | 2025-2026 |
|---|---|---|
| team | `club_code` | `team` |
| player name | `full_name` (+ `first_name`/`last_name`) | `player_name` |
| position code | `depth_position` | `pos_abb` |
| depth rank | `depth_team` (int 1/2/3) | `pos_rank` (int) |
| snapshot versioning | `week` + `game_type` (one row per player per week) | `dt` (timestamp; latest = current) |
| player id | `gsis_id` (present in both) | `gsis_id` |

This means the preseason-profile path — which is genuinely load-bearing: it's
the exact code that produces *this season's* live preseason projections,
right now, every year, until that season's own `snap_counts` file stops being
empty — has never been validated against a season with a known outcome. The
current weights were hand-set at design time and have zero backtest evidence
behind them. (2025 does technically work now that the season is complete, but
one season / 32 teams is too thin a sample to fit or trust 7 weights against
on its own.)

## Goal and scope

Make `compute_preseason_player_profiles()` work for 2014-2024 (matching
however far back `rawdata/depth_charts/` actually goes with the old schema),
so `PRESEASON_ELO_WEIGHTS` can be validated against ~10 real completed
seasons (320 team-seasons) instead of effectively zero.

**Explicitly out of scope for this spec:** the actual weight recalibration
(correlation/regression analysis, candidate weight sets, walk-forward
validation of the winner). That's a separate follow-on piece of work that
becomes possible once this unblocks it — not designed here.

## Verified: old schema carries equivalent information

Before designing a fix, checked whether the old schema's `depth_position`
column is a genuinely different position taxonomy or just a rename. It's a
rename: every specific position code the builders filter on
(`_OFF_OL_POS = {LT, LG, C, RG, RT}`, `_DEF_DL_POS = {LDE, RDE, LDT, RDT, NT}`,
`_DEF_LB_POS = {WLB, MLB, SLB, LILB, RILB}`, `_DEF_CB_POS = {LCB, RCB, NB}`,
`_DEF_S_POS = {SS, FS}`, plus direct `QB`/`WR`/`TE`/`RB` checks) appears
verbatim in `depth_position`'s value set for 2018-2024. `depth_team` (1/2/3)
is a direct, exact analog of `pos_rank`. Old-schema junk/generic codes not in
the used-code list (`OLB`, `LB`, `CB`, `S`, `DL`, whitespace garbage, a stray
`WR\8` typo) simply won't match any filter — same as today, no special
handling needed.

Also verified `rosters/`, `snap_counts/`, and `pfr_advstats/` — the three
other inputs `_preseason_offense`/`_preseason_defense` read — have **stable**
schemas across 2018-2026. Only `depth_charts` changed.

Spot-checked 2023 week-1 REG: 32 teams, 32 QB1 starters (one team has a
harmless duplicate QB1 row — a real data quirk, not something this fix
introduces; the existing "iterate all matching rows" code in
`_preseason_offense` already tolerates duplicates at a rank without crashing,
it would just average in one extra data point for that one team in that one
season).

## Design

Add one normalization step inside `compute_preseason_player_profiles()`,
before the depth-chart DataFrame reaches `_preseason_offense`/
`_preseason_defense`. **No changes to either builder function** — they keep
consuming the exact same column shape (`team`, `player_name`, `gsis_id`,
`pos_abb`, `pos_rank`) they do today.

```python
def _normalize_depth_chart(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize either depth_charts schema to the shared shape
    _preseason_offense/_preseason_defense expect: team, player_name,
    gsis_id, pos_abb, pos_rank.

    New schema (2025+): dt, team, player_name, pos_abb, pos_rank already
    match -- dedup to the latest dt snapshot per player (current behavior,
    unchanged).

    Old schema (pre-2025): club_code, full_name, depth_position, depth_team,
    week, game_type. No "latest" timestamp -- use week 1 REG instead, the
    earliest chart of the season and the correct preseason analog (using any
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
        df = df.sort_values("dt").drop_duplicates(subset=["gsis_id"], keep="last")
    return df
```

`compute_preseason_player_profiles()` calls this right after
`pd.read_csv(dc_path)`, replacing the current inline
`if "dt" in depth_chart.columns ...` dedup block. Everything downstream
(`_normalize_team()` on the `team` column, the calls into
`_preseason_offense`/`_preseason_defense`) is untouched.

### Why not branch inside the builders instead

Duplicating the old/new distinction into both `_preseason_offense` and
`_preseason_defense` (each already long, with multi-season blending and
position-specific scoring logic) would mean two copies of the same
schema-detection logic and twice the surface area for the two shapes to
drift apart. Normalizing once at the single entry point keeps both builders
schema-agnostic, matching how they're written today.

## Testing

- Unit tests for `_normalize_depth_chart()` directly: old-schema input (with
  multiple weeks present) returns only week-1 REG rows, correctly renamed;
  new-schema input dedups by latest `dt` (regression test for current
  behavior); a row set with junk `depth_position` values (whitespace, `WR\8`)
  passes through undropped (filtering-by-non-membership already handles
  this, just confirming no crash).
- `compute_preseason_player_profiles()` integration test: run against real
  2023 rawdata (already on disk), assert it returns non-empty profiles for
  all 32 teams with the expected dimension keys — mirrors the existing
  `tests/test_preseason_profiles.py` pattern for 2025/2026.
- Regression: existing `tests/test_preseason_profiles.py` and
  `tests/test_rank_position_groups.py` must still pass unchanged (2025/2026
  behavior is a no-op change under this design).
- Manual verification: `python scripts/rank_position_groups.py --season 2023`
  now producing a real leaderboard (currently errors) — sanity-check a few
  well-known 2023 rosters' rankings look plausible (e.g. that season's
  strong/weak QB tiers) before trusting the data for the follow-on weight
  analysis.

## Non-goals

- Not fixing or improving the DL/LB/CB scoring formulas themselves — only
  making them runnable against 2014-2024 data.
- Not extending support further back than wherever `rawdata/depth_charts/`
  actually has the old `club_code` schema (need to confirm the exact earliest
  year during implementation — spot-checked 2018 above, not yet confirmed how
  much further back the old schema goes uninterrupted, e.g. do 2001-2011 also
  use it or is there a *third* schema variant even further back).
- Not the actual `PRESEASON_ELO_WEIGHTS` recalibration — follow-on work once
  this is merged.
