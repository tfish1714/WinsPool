# New Prediction Features — Investigation Stub

**Date:** 2026-09-18
**Status:** Not designed — needs a proper brainstorming pass. This is a
stub to seed that investigation, not a design or a commitment to build any
of it.

## Origin

Surfaced across two threads: (1) the user has floated a DVOA-like
(opponent-adjusted, offense + defense) efficiency metric more than once
(see [[project_dvoa_feature_idea]], 2026-09-09), most recently promoted to
"Stage 1b" of
`docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md`'s
feature-engineering audit and explicitly deferred there as its own
follow-up; (2) that same review's Rollout item #9 confirmed the user wants
it kept as tracked backlog, not folded into the current stamp/gate rollout,
"so it doesn't get lost." This doc is that landing spot, widened to also
capture other plausible new/computed features worth the same kind of look
— not just the one everyone already remembers.

## Headline candidate: DVOA-mimic opponent-adjusted EPA

**What exists today, and why it's not enough.** `FEATURE_COLUMNS`
(`services/nn_feature_engine.py:128-152`, 27 features) has
`pass_epa_matchup`/`rush_epa_matchup`/`early_down_matchup`, computed as a
**raw** matchup delta — `(home_off − away_def) − (away_off − home_def)` —
not opponent-adjusted. No strength-of-schedule regression, no situational
weighting (down/distance), no garbage-time exclusion. A team's raw
offensive EPA against a string of bad defenses reads identically to the
same raw EPA against good ones.

**What real DVOA does that this doesn't:** opponent-adjusts every play
against the specific defense faced (not just a season-average opponent
strength), weights by situation (down/distance/field position define
"success"), and excludes garbage-time plays (blowout-score situations
where both offenses stop trying to execute their real game plan).

**Why this isn't a quick add:** real DVOA is proprietary (Football
Outsiders/FTN) with no public API or free feed — this means building an
approximation from scratch, not ingesting the real thing. It also changes
`FEATURE_COLUMNS`, which per CLAUDE.md and the ML De-Vegas Pass's prior
cycle means a full retrain of NN/XGB/LR plus walk-forward validation
(`scripts/walk_forward_validate.py`) before it's trustworthy.

**Data already available, confirmed synced:** `rawdata/pbp/play_by_play_{year}.csv`
is synced locally back to 1999 (confirmed 2026-09-18, no gap to fill
before starting) — the right source for a from-scratch opponent
adjustment, since it carries per-play EPA with the actual opponent faced
that play, not just a season-level average.

## Other feature candidates worth the same kind of look

Not scoped, not vetted for data availability beyond a first pass — listed
so the eventual brainstorming session has more than one thing to compare
against, and can decide relative priority instead of investigating them
one at a time as each is separately remembered.

- **Garbage-time-excluded EPA.** A prerequisite/component of the DVOA-mimic
  effort above, but worth calling out on its own: even without full
  opponent-adjustment, filtering blowout-situation plays out of the
  *existing* `pass_epa_matchup`/`rush_epa_matchup`/`early_down_matchup`
  calculation could reduce noise in what's already there. Possibly a
  smaller, faster win to validate before committing to the full DVOA-mimic
  scope.
- **Red-zone / goal-to-go efficiency.** Offense scoring rate and defense
  stop rate inside the 20 (or inside the 10) aren't represented anywhere
  in the current 27 features — `pass_epa_matchup`/`rush_epa_matchup` are
  whole-field averages. Likely sourced from `rawdata/pbp/` (field position
  is a native play-by-play column) or `pfr_advstats`.
- **Special-teams *performance*, not just roster value.** `st_value_delta`
  (already in `FEATURE_COLUMNS`) is a roster-talent proxy — it doesn't
  capture actual return yardage, field-goal accuracy by distance, or
  net-punting performance in a given season. `stats_team_week_*.csv`
  likely has enough for a first cut.
- **Havoc rate / negative-play rate (defense).** Forced fumbles + INTs +
  TFLs per defensive snap, distinct from `qb_pressure_advantage`/
  `def_pressure_diff` (which are pass-rush-specific) — a broader
  "how often does this defense blow up a play" signal.
- **Pace / tempo mismatch.** Plays-per-game or seconds-per-play for each
  team, which affects how much variance is baked into a given matchup
  (more plays = more regression to the mean) — not currently represented;
  `rest_advantage`/`week` don't capture this.
- **Short-week flag distinct from general rest.** `rest_advantage` is
  already in `FEATURE_COLUMNS` as a continuous rest-differential signal,
  but a dedicated Thursday-game / short-week binary flag might capture a
  nonlinear effect a continuous rest differential smooths over.
- **"Motivation"/meaningless-game flag.** `playoff_flag` exists, but
  nothing flags a Week 17-18 game where one or both teams are already
  eliminated or have locked their seed (a common source of backup-player
  performance noise in real outcomes vs. what the roster-strength features
  would predict).
- **Weather.** Wind speed in particular for passing-heavy games; dome vs.
  outdoor. Partial data availability from nflverse schedules needs
  confirming before this goes further than a list item.

## What's genuinely needed here

A decision on scope and sequencing before any of this touches
`FEATURE_COLUMNS`: which candidate(s) get investigated first, whether any
is worth a fast, low-risk validation pass (e.g. garbage-time exclusion on
existing features, which doesn't add a new column) before committing to a
full new-feature-plus-retrain cycle, and whether these get bundled into one
retrain or landed and validated independently.

## Open questions to resolve when this is picked up

These need an actual brainstorming pass (clarifying questions → approaches
→ design), not decided here:

1. **Which candidate(s) first, and why?** The DVOA-mimic EPA is the one
   the user has raised repeatedly, but it's also the largest scope item on
   this list (full opponent-adjustment methodology, not a single new
   column). Worth explicitly deciding whether to start there or with a
   smaller, faster candidate (garbage-time exclusion, red-zone efficiency)
   to validate the retrain/walk-forward workflow on a lower-stakes change
   first.
2. **Opponent-adjustment methodology for the DVOA-mimic feature**, if/when
   it's picked up: a regression-based strength-of-schedule adjustment
   (closer to real DVOA's methodology) vs. a simpler weighted-opponent-average
   proxy (faster to build, less rigorous). Not decided here.
3. **Bundle vs. sequential rollout.** If multiple candidates from this list
   move forward, do they share one retrain + walk-forward validation cycle
   (fewer total retrains, harder to attribute which feature caused which
   metric change) or land one at a time (clean attribution, more retrain
   cycles)? This plan's own recent history (`project_ml_de_vegas`,
   Stage 2's honest-cohort AUC investigation) suggests attribution matters
   a lot in practice once `nn_feature_engine.py` has had several changes
   land close together — worth weighing before choosing "bundle."
4. **Data-availability confirmation** for the non-headline candidates above
   (red-zone, special-teams, havoc rate, pace, weather) — only the
   DVOA-mimic EPA's data source (`rawdata/pbp/`) has been confirmed synced
   and sufficient; the others are first-pass guesses at likely sources
   (`stats_team_week_*.csv`, `pfr_advstats`, nflverse schedules) that need
   verifying before they're real candidates rather than ideas.

## Non-goals

- Not a commitment to add any of these features — this is an investigation
  stub, not a design or a plan.
- Not ingesting real DVOA data — it's proprietary with no public/free feed;
  any DVOA-like feature here is a from-scratch proxy, not the real metric.
- Not touching `FEATURE_COLUMNS` as part of creating this stub — any actual
  change requires the full retrain + walk-forward validation cycle CLAUDE.md
  and the ML De-Vegas Pass's history already establish as mandatory, and
  that only starts once a real brainstorming/design pass picks this up.
