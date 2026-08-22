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

**Part B** — a narrow, same-day ESPN check for the window between the last scheduled predict run and kickoff, feeding the same weight scale, followed by a cheap single-slate repredict that publishes through the existing prediction store.

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

## Part B — ESPN last-hour pregame check + narrow repredict

### What's genuinely needed here

Given Part A's weekly grading already covers the injury-report-to-model pipeline on the existing daily/kickoff−60min cadence, the only remaining gap is the window *after* the last scheduled predict run and *before* kickoff — e.g. a true last-minute inactive-list scratch, or nflverse's `injuries` file simply not yet reflecting a change that ESPN already shows.

### Signal

For each game in the upcoming kickoff cluster, hit ESPN's per-game summary endpoint (`https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}`) and read its `injuries[]` array. Map each listed player's status onto the *same* weight scale as Part A (Out=0.0/Doubtful=0.15/Questionable=0.5/Full=1.0). Same position scope as Part A — no QB-only restriction.

**Open implementation question:** ESPN's injuries payload uses ESPN's own player IDs, not nflverse's `gsis_id` directly. Confirm during implementation whether ESPN's summary endpoint exposes a cross-reference (many ESPN endpoints include external IDs), or whether a name+team match against the current week's roster is needed. If neither is reliable, this should degrade to a no-op for that game rather than guess.

### Repredict

Feed the refreshed availability weights into `roster_value_service.py`'s existing computation for just the affected `(season, week, team)` keys. Add a scoped mode to `cache_builder.py` (e.g. `--games <game_ids>`) that builds features for only those games — skipping the full multi-year historical rebuild `build_master_feature_table()` otherwise does — and publishes through the **same** path the full job already uses: `get_game_predictions()` → `merge_thin_game_predictions()` → `write_game_predictions()`, plus the same `metadata/cache_control` cache-invalidation write. No new store, no parallel publish path.

Explicitly does **not** touch `prediction_snapshot` (season-level Monte Carlo win-total projections) — a single game's availability change doesn't warrant re-running a 5000-trial season simulation, and (per the related finding below) nothing reads that cache today regardless.

### Trigger

`schedule_kickoffs.py` gains a third enqueued Cloud Task per kickoff cluster, hitting the existing `winspool-predict-daily` job via the Cloud Run Jobs Admin API's `:run` endpoint with an argument override (`--games`) instead of provisioning a new job. Exact minute-offset before kickoff is **not pinned in this spec** — it depends on the actual runtime of the new scoped mode, which doesn't exist yet to measure. The implementation plan must include measuring this once built (e.g. timing a real invocation against a live slate) before a final offset is chosen, rather than guessing. As a starting anchor: the NFL's official inactive list is public by rule at kickoff−90min league-wide, which the existing kickoff−75min sync / kickoff−60min predict schedule is already timed after.

### Failure handling

Matches the established pattern from `sync_live_scores.py`'s ESPN overlay: any fetch/parse failure for a given game's ESPN summary is caught per-game and no-ops (keep the last known weight for that game), never raises, never triggers `send_alert_email()`. This is cosmetic-refinement, not the authoritative prediction path.

### Testing

Unit tests for ESPN status→weight mapping (including malformed/missing `injuries[]`), per-game failure isolation (one game's ESPN failure doesn't affect others in the same slate), and the scoped `cache_builder.py --games` mode writing only the targeted games into `game_predictions` via `merge_thin_game_predictions` without disturbing other games' stored predictions (including previously-stored richer fields like `model_spread`/`edge_vs_vegas`, which the merge already preserves — verify the scoped mode doesn't regress that).

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
