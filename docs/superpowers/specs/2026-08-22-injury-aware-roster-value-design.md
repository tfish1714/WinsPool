# Graded Injury-Aware Roster Value — Design Spec

**Date:** 2026-08-22
**Status:** Approved — ready for planning

---

## Origin

Combines and supersedes two prior backlog stubs:
- `docs/superpowers/specs/2026-06-02-inseason-weekly-profile-updates-design.md` — in-season weekly profile rebuilds incorporating injuries/real data.
- `docs/superpowers/specs/2026-08-19-espn-pregame-injury-signal-design.md` — using ESPN's fresher per-game injury data to catch last-minute scratches nflverse's daily cadence would miss.

Both turned out to be facets of the same problem — how much a player's current availability should count toward their team's projected strength — and are combined here rather than designed separately, so they share one weighting mechanism instead of two incompatible ones.

## Key finding that reshaped scope

Most of what Spec 1 asked for already exists. `services/roster_value_service.py::compute_roster_value()` already builds a per-player composite score for every position group (EPA-based for skill positions, sacks/hits/TFL-based for defense), blends prior-season into current-season as games accumulate, age-adjusts it, and rolls it up via depth-discount weighting into team-level `off_roster_value`/`def_roster_value`/`st_value`/`qb_resilience`, z-scored per week. This is a real, already-shipped "per-player rating rolls up into roster quality" pipeline.

What it does *not* do: `_load_weekly_roster()`'s `_ACTIVE_STATUSES = {"ACT", "INA", "PUP"}` filter only distinguishes "on the 53-man roster" from "on IR/practice squad" — it does not grade *why* a rostered player might be limited. A player ruled Out or Doubtful this week, but not on IR, still contributes their full rolling-score weight today. This spec fixes that gap rather than building a new mechanism.

This also means Spec 2 (ESPN pregame signal) is narrower than originally scoped: since `winspool-predict-daily` already reruns daily and again at kickoff−60min (`winspool-schedule-kickoffs`), a player's `report_status` already flows through the graded system on its own cadence — a Questionable-Wednesday player who's upgraded to Full by Saturday gets that reflected automatically on the next scheduled run. ESPN's per-game data only needs to cover the narrow window *after* the last scheduled run and *before* kickoff.

## Goal and Scope

**Part A** — grade the existing weekly roster-value computation by real injury severity (all positions, nflverse-sourced, weekly cadence).

**Part B0** — (discovered during implementation, see below) wire the live schedule page's upcoming-game prediction to the already-built, already-tested `simulate_season()` + real-results mechanism, and make its roster-value inputs week-aware — the necessary foundation for Part B, and a real fix in its own right independent of injuries.

**Part B** — a narrow, same-day ESPN check for the window between the last scheduled predict run and kickoff, feeding the same weight scale, followed by a re-simulate that publishes through the existing prediction store.

**Bundled final step** — retrain all three models once, after Part A ships (see "Retrain" section).

Out of scope:
- `home_qb_injury_flag`/`away_qb_injury_flag` (`nn_feature_engine.py`) — a separate, already-trained binary QB feature. Not touched.
- `services/cache_service.py`'s `analytics_cache` collection / `prediction_snapshot` — see "Related finding" below. Not touched by this spec.
- A new Cloud Run Job or Docker image for the narrow repredict — reuses the existing `winspool-predict-daily` job.
- Deciding the exact kickoff-offset minute number for Part B's trigger now — left as a measure-then-decide step (see Part B).

---

## Part A — Graded weekly availability weighting

### Current behavior

In `compute_roster_value()`'s per-player loop (`roster_value_service.py`, the loop building `off_by_grp`/`def_by_grp`/`qb_entries`), each player's contribution is:

```python
adj = score * amlt   # score = blended current/prior rolling EPA-style composite; amlt = age multiplier
```

`_load_weekly_roster()` only excludes players whose `status` is outside `{"ACT", "INA", "PUP"}` (i.e. IR / practice squad). A player on the report as Out or Doubtful, but still on the active roster, is not excluded and gets no discount — `adj` is computed as if they were fully healthy.

### Design

1. Add `_load_injury_report(rawdata_dir, target_season) -> Dict[Tuple[int, str], float]`, keyed by `(week, gsis_id)`, returning an availability multiplier:

   | `report_status` | multiplier |
   |---|---|
   | `Out` | 0.0 |
   | `Doubtful` | 0.15 |
   | `Questionable` | 0.5 |
   | anything else / not listed | 1.0 |

   Sourced from `rawdata/injuries/injuries_{target_season}.csv`, joined on `gsis_id` — the same field `_load_weekly_roster()` already uses, no new ID-mapping work. Applies to every position group in `_POS_GROUP` (QB, WR, TE, RB, edge, dl, lb, cb, s) — not QB-only, since the mechanism is generic and QB already carries outsized weight through `_OFF_WEIGHTS["QB"] = 0.40` plus the depth discount.

2. In the per-player loop, change the contribution to:

   ```python
   avail_mult = injury_report.get((week, pid), 1.0)
   adj = score * amlt * avail_mult
   ```

3. `_ACTIVE_STATUSES` keeps its current job (on-roster vs. IR/practice-squad) — unchanged. The two checks answer different questions: "is this player even a roster consideration" vs. "how much should they count this week."

### Non-goals for Part A

- No change to `home_qb_injury_flag`/`away_qb_injury_flag`.
- No change to `_depth_discount()` or the age-multiplier logic — `avail_mult` is a third independent factor, not a replacement.

### Testing

Extend `roster_value_service.py`'s test coverage: synthetic `injuries.csv` rows for Out/Doubtful/Questionable/unlisted, verify each yields the correct multiplier and that it correctly reduces `off_roster_value`/`def_roster_value`/`st_value`/`qb_resilience` proportionally to the affected player's share of the team score. Cover a case where the *only* contributor to a group is Out (score should drop toward 0, not just get discounted) and a case where a backup covers for a Doubtful starter (depth discount already handles the backup's own weight — verify the two don't double-count).

---

## Part B0 — the discovered prerequisite: upcoming-game predictions don't consume in-season data at all

**This section documents a finding made during implementation (after Part A shipped and Task 3 was reviewed), not something known when this spec was first written.** It changes Part B's design and is a real, load-bearing correction — not an incremental addition.

### What was assumed vs. what's actually true

The original Part B design assumed a live upcoming game's prediction flows through the same pipeline as a completed game's: `build_master_feature_table()` → ensemble lookup. It does not. `build_master_feature_table()` unconditionally drops every row without a final score (`sched.dropna(subset=["home_win"])`, `nn_feature_engine.py`) — it is structurally a training/completed-games-only table. `build_ensemble_lookup()`'s own docstring confirms this: "feature table has no rows for unplayed games."

The actual mechanism behind every "predicted winner" shown today for an upcoming game is `NNProjectionEngine.game_win_probabilities_batch()`, called from `cache_builder.py::_apply_predictions()`'s fallback branch. This method builds **one static per-team profile, averaged from the entire prior season** (`NNProjectionEngine.initialize()`: `build_master_feature_table(min_season=2020, max_season=season-1)` — the target season's own already-played weeks are never included), computed once and reused unchanged for the rest of the season. It explicitly zeroes injury-flag features ("unknown for future games; model trained on 0-mean baseline") and pulls every roster-value feature from that same frozen prior-season average. **This is true today, independent of anything in this spec** — the live app's per-game prediction for an upcoming game already ignores this season's form and this week's injuries entirely.

### The mechanism that should be used already exists and is already tested

`NNProjectionEngine.simulate_season(schedule_df, n_sims, completed_results)` accepts `completed_results: {game_key: margin}` for already-played games this season. It walks weeks in ascending order: for a week already in `completed_results`, it applies the real margin deterministically and updates that team's Elo/EPA/margin state from it; for a week not yet played, it predicts from whatever state has accumulated so far (which already reflects every real result up to that point) and simulates forward. `tests/test_simulate_season.py::test_completed_results_applied_deterministically` proves this. `scripts/backfill_schedule_predictions.py` already calls it correctly, building `completed_results` from real `nfl_games` scores and writing the result into `game_predictions` — but that script is a manual command (per CLAUDE.md's Commands section), not one of the four automated Cloud Run Jobs. The automated daily job (`winspool-predict-daily` → `cache_builder.py`) never calls it.

Separately, `simulate_season()`'s own per-game feature construction (`_precompute_static_features()`) has the same staleness problem as the fallback method it's replacing, just for a different feature group: it also pulls `off_roster_value_delta`/`def_roster_value_delta`/`st_value_delta`/`qb_resilience_delta`/`roster_talent_delta`/`trench_dominance_metric` from the same frozen prior-season-only `_team_profiles` average (comment in the code: "Roster value deltas ... from prior season"). The Elo/EPA momentum state evolves correctly via `completed_results`; the roster-value features next to it do not evolve at all.

### Design (Part B0)

1. **`NNProjectionEngine.initialize(season, espn_overrides=None)`** gains a second data source: in addition to the existing prior-season `_team_profiles`, compute `self._roster_value_cache = compute_roster_value(season, RAWDATA_DIR, espn_overrides=espn_overrides)` — the **target** season's own per-week roster value (already alpha-blended prior→current per player, and after Part A, already injury-graded).
2. **`_precompute_static_features()`** is changed to look up each game's roster-value-family features from `self._roster_value_cache[(season, week, team)]` (keyed by that specific game's own `week`) instead of the flat `_team_profiles` average. The Elo/EPA dynamic-state mechanics (`_build_initial_state`, `_vectorized_elo_update`/`_vectorized_epa_update`) are untouched — they already transition correctly via `completed_results`; only the previously-frozen roster-value inputs change to be week-aware.
3. **`cache_builder.py::_apply_predictions()`**'s fallback branch is changed to build `completed_results` from `schedule_df`'s own real scores and call `fallback_engine.simulate_season(schedule_df, n_sims=SIMULATE_SEASON_N_SIMS, completed_results=completed_results)` once (not per-row), then look up each unplayed row's prediction from the returned `game_probs_out` by its `W{wk:02d}_{home}_{away}` key — replacing the `game_win_probabilities_batch()` call entirely for this call site. `game_win_probabilities_batch()` itself is not removed (still used by `project_portfolio_wins()` for per-draft-pool portfolio math, a different, higher-volume use case where a full season simulation per player would be too expensive) — only this one call site changes.

This is the real fix for "predictions should improve as the season progresses," independent of anything to do with injuries — and it is the necessary foundation Part B's ESPN signal needs, since there is no week-aware roster-value input to adjust otherwise.

---

## Part B — ESPN last-hour pregame check + narrow repredict

### What's genuinely needed here

Given Part A's weekly grading and Part B0's week-aware wiring together cover the injury-report-to-model pipeline on the existing daily/kickoff−60min cadence, the only remaining gap is the window *after* the last scheduled predict run and *before* kickoff — e.g. a true last-minute inactive-list scratch, or nflverse's `injuries` file simply not yet reflecting a change that ESPN already shows.

### Signal

For each game in the upcoming kickoff cluster, hit ESPN's per-game summary endpoint (`https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}`) and read its `injuries[]` array. Map each listed player's status onto the *same* weight scale as Part A (Out=0.0/Doubtful=0.15/Questionable=0.5/Full=1.0). Same position scope as Part A — no QB-only restriction. ESPN's per-game injury entries are joined to nflverse's `gsis_id` via the exact `espn_id` column already present in `weekly_rosters/roster_weekly_{season}.csv` — no fuzzy name matching (resolved during Part A/Task 3 implementation; the dtype-coercion pitfall of that join, confirmed against real data, is documented in the implementation plan).

### Repredict

Feed the refreshed `espn_overrides` into `NNProjectionEngine.initialize(season, espn_overrides=...)` (Part B0), then re-run `simulate_season()` for the current season — the same mechanism Part B0 wires into the daily job, just re-invoked on demand with fresher overrides and a lower `n_sims` (this refresh doesn't need daily-job-grade trial counts; it only needs to move the specific affected games' numbers). Publish only the affected games' entries from the resulting `game_probs_out` through the **same** path the full job already uses: `get_game_predictions()` → `merge_thin_game_predictions()` → `write_game_predictions()`, plus the same `metadata/cache_control` cache-invalidation write. No new store, no parallel publish path.

Explicitly does **not** touch `prediction_snapshot` (the separate draft-portfolio Monte Carlo cache) — a single game's availability change doesn't warrant a full player-portfolio re-simulation, and (per the related finding below) nothing reads that cache today regardless.

**Correction (post-implementation):** `--resimulate` is NOT a cheap, narrowly-scoped operation as actually implemented. It calls `engine.initialize(year)`, which still runs a full `build_master_feature_table(min_season=2020, max_season=year-1)` (a 6-season rebuild) plus `compute_roster_value()` — `simulate_season()` being scoped to one season doesn't avoid either of those, since `initialize()` runs them unconditionally before `simulate_season()` is ever called. Worse, since the Cloud Task's container-args override *replaces* the container's configured args entirely (not appends to them), `--skip-sync` is never passed by the enqueued resimulate task, so a full `_sync_rawdata()` (up to a 300-second subprocess timeout) also runs first. The actual cost has **not been measured**, and `RESIMULATE_LEAD_MINUTES` (currently 20) is an unvalidated placeholder pending that measurement — see `scripts/schedule_kickoffs.py`'s own comment on the constant.

### Trigger

`schedule_kickoffs.py` gains a third enqueued Cloud Task per kickoff cluster, hitting the existing `winspool-predict-daily` job via the Cloud Run Jobs Admin API's `:run` endpoint with an argument override instead of provisioning a new job. Exact minute-offset before kickoff is **not pinned in this spec** — it depends on the actual runtime of the re-simulate mode, which doesn't exist yet to measure. The implementation plan must include measuring this once built before a final offset is chosen, rather than guessing. As a starting anchor: the NFL's official inactive list is public by rule at kickoff−90min league-wide, which the existing kickoff−75min sync / kickoff−60min predict schedule is already timed after.

### Failure handling

Matches the established pattern from `sync_live_scores.py`'s ESPN overlay: any fetch/parse failure for a given game's ESPN summary is caught per-game and no-ops (keep the last known weight for that game), never raises, never triggers `send_alert_email()`. This is cosmetic-refinement, not the authoritative prediction path.

### Testing

Unit tests for ESPN status→weight mapping (including malformed/missing `injuries[]`), per-game failure isolation (one game's ESPN failure doesn't affect others in the same slate), and the re-simulate mode writing only the targeted games into `game_predictions` via `merge_thin_game_predictions` without disturbing other games' stored predictions (including previously-stored richer fields like `model_spread`/`edge_vs_vegas`, which the merge already preserves).

---

## Related finding (not part of this spec's scope)

While tracing which downstream store a repredict needs to update, found that `services/cache_service.py::get_cached()` — the reader for the `analytics_cache` collection `cache_builder.py` writes every run (`wins_pool_standings`, `player_winlossmatrix`, `schedule_enriched`, `weekbyweek`, `prediction_snapshot`) — is called nowhere in `routes/` or `services/`, only from a debug script (`tests/inspect_cache.py`) and its own unit tests. Live routes (`standings_routes.py`, `prediction_routes.py`, `api_routes.py`) compute these analytics live instead, via `analysis.*()` functions against `data_service.load_data()`'s own 3-tier cache — a separate caching layer from `analytics_cache`. The only `cache_builder.py` output actually read live is `game_predictions`.

This means `cache_builder.py` is writing several analytics to Firestore every single day (and on every kickoff-triggered run) that nothing reads — real, ongoing waste, and a drift from the original "web app never reads raw Firestore" design principle documented in `cache_service.py`'s own module docstring. Flagged here as a pointer for a future cleanup pass; not investigated or fixed as part of this spec.

---

## Retrain (bundled final step)

The currently-deployed models (NN v14 / XGB v8 / LR v6) were trained 2026-06-04. Four real bug fixes landed in the preseason profile computation on 2026-08-15 (offense and DL multi-season blends had lost their minimum-sample-size gate; DL-quality scoring conflated injury absence with poor talent; recency/reliability/injury-discount blending was extended to all preseason position groups) — none of which the deployed models have been retrained against. That's existing train/serve skew, independent of this spec.

Part A changes `off_roster_value_delta`/`def_roster_value_delta`/`st_value_delta`/`qb_resilience_delta` training feature values again. Rather than retrain twice, bundle both changes into one retrain pass after Part A ships: **NN v15 / XGB v9 / LR v7**, verified through the existing `weekly_model_eval.py` / walk-forward workflow before promoting to `latest`/`best` in the model registries.

---

## Non-goals

- Not a general audit of `analytics_cache` usage — the related finding above is a pointer, not a task.
- Not retraining immediately for the 2026-08-15 fixes alone — bundled with Part A per above.
- Not building a new Cloud Run Job/image, new Firestore collection, or new publish path for Part B — reuses `winspool-predict-daily` and `game_predictions`.
- Not extending grading to `home_qb_injury_flag`/`away_qb_injury_flag` or retiring that feature.
- Not solving `services/live_score_service.py::sync_live_scores_to_df()`'s known abbreviation-normalization bug (`docs/superpowers/specs/2026-08-20-scheduled-jobs-hardening-followups.md` §1) — unrelated, pre-existing, separately tracked.
