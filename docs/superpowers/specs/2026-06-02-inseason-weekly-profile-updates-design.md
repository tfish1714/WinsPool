# In-Season Weekly Profile Updates — Design Spec (STUB)
**Date:** 2026-06-02
**Status:** Stub — pending brainstorm session to finalize

---

## Problem

The preseason player profiles (Spec 1) are built once at season start and don't update as:
- Players get injured during the season
- Actual 2026 game data accumulates and outweighs preseason projections
- Roster moves occur mid-season (trades, releases, IR placements)

Once the season is underway, weekly profile rebuilds should incorporate real 2026 performance data and injury status so future game predictions stay current.

---

## Intended Approach (to be refined in brainstorm)

### Weekly profile rebuild trigger
- Run after each week's games complete (integrate with `scripts/run_cron.py` or similar)
- Blend preseason projections with actual 2026 advstats, with actual-data weight increasing each week
- Transition: week 1 = 100% preseason, week 6 = ~50/50, week 12 = ~90% actual

### Injury data integration
- Source: `rawdata/injuries/injuries_{season}.csv` (nflverse, updated weekly)
- Fields: `report_status` (Out/Doubtful/Questionable), `practice_status`, per player per week
- Availability weights: Out=0.0, Doubtful=0.15, Questionable=0.50, Full/Not listed=1.0
- Applied to each player's contribution when computing that week's team profile
- Covers all positions: QB, WR, TE, RB, OL, DE, DT, LB, CB, S

### Active roster status
- Source: `rawdata/weekly_rosters/roster_weekly_{season}.csv`
- Players on IR, practice squad, or waived should be excluded
- Flags: `status` column (Active, IR, PS, etc.)

### Blend mechanism (to be designed)
- Option A: Simple weekly weight ramp (preseason weight decreases linearly)
- Option B: Bayesian update — actual stats update prior (preseason projection)
- Option C: Hard switch at week 6 — pure actual data from that point

### Output
- Same schema as preseason profiles: `{team: {off_pass_epa, off_rush_epa, def_pass_epa, def_rush_epa, ...}}`
- Feeds `NNProjectionEngine.initialize()` → `_build_initial_state()` same integration point

---

## Data Sources (confirmed available)

| File | Content |
|---|---|
| `rawdata/injuries/injuries_{season}.csv` | Per-player per-week injury status (Out/Doubtful/Questionable), 2009+ |
| `rawdata/weekly_rosters/roster_weekly_{season}.csv` | Active/IR/PS status per player per week, 2010+ |
| `rawdata/pfr_advstats/advstats_week_*_{season}.csv` | In-season player performance, available mid-season |
| `rawdata/snap_counts/snap_counts_{season}.csv` | In-season snap share, available mid-season |

---

## Open Questions for Brainstorm

1. What week threshold triggers the switch from preseason-dominant to actual-data-dominant profiles?
2. Blend approach: linear ramp, Bayesian, or hard switch?
3. How to handle a player who was healthy preseason but injured in week 3 — do we retroactively adjust their team's projected remaining-season quality?
4. Should injury weights be applied to the team's future game projections only, or also affect how we interpret completed games?
5. Does this run as part of the existing daily cron (`run_cron.py`) or a separate weekly job?
6. How do we handle multi-week injuries (player listed as Out for weeks 4–8)?

---

## Dependencies

- **Spec 1 (preseason player profiles)** must be shipped first — this spec extends that architecture
- Same `compute_preseason_player_profiles()` infrastructure, adapted for mid-season use

---

## Out of Scope

- Model retraining (Spec C)
- UI changes to show injury impact on predictions
