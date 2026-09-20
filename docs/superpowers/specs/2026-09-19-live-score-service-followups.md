# `live_score_service.py` Follow-Ups (Post Abbreviation-Bug Fix)

**Date:** 2026-09-19
**Status:** Not designed — backlog, split out of `docs/superpowers/specs/completed/2026-08-20-scheduled-jobs-hardening-followups.md` when that spec's work was completed and archived, so these two remaining open questions don't get lost. Needs its own pass when picked up, not decided here.

## Origin

`docs/superpowers/plans/completed/2026-09-19-scheduled-jobs-hardening-followups.md` fixed the team-abbreviation bug in `services/live_score_service.py::sync_live_scores_to_df()` (it was normalizing the repo's already-canonical side of a dict-key match instead of ESPN's raw side, so Rams/Commanders/Jaguars live-score updates never matched) and closed four unrelated test-coverage gaps. Its final whole-branch review approved the merge but surfaced two things that predate the fix and are explicitly out of that plan's scope — both need to be decided, not silently dropped when the source spec gets archived.

## 1. Is `sync_live_scores_to_df()` still needed at all?

Carried forward verbatim from the original spec's open question, still unanswered:

`scripts/cache_builder.py` still calls `services.live_score_service.sync_live_scores_to_df()` during its nightly analytics build (`build_year()`). This is a separate, older live-score path from `scripts/sync_live_scores.py` (the winspool-live-scores Cloud Run Job, which already writes `is_live`/`clock`/`period` to `nfl_games` every 5 minutes with its own, already-correct ESPN-normalization logic and a real "don't clobber a final game" guard). The two paths now coexist.

**Question for whoever picks this up:** could `cache_builder.py`'s nightly build just read what `sync_live_scores.py` already wrote to Firestore, instead of doing its own separate ESPN fetch through this older function?

## 2. `sync_live_scores_to_df()` writes scores/`result` unconditionally, with no "hasn't kicked off yet" guard

Found during the abbreviation-bug fix's final review, and newly relevant *because* of that fix: this function writes `home_score`/`away_score`/`result` onto a row unconditionally, before any status check:

```python
df.at[idx, 'home_score'] = update['home_score']
df.at[idx, 'away_score'] = update['away_score']
df.at[idx, 'result'] = update['home_score'] - update['away_score']
```

`get_live_updates()` applies no status filter, so ESPN's scoreboard entries for games that have **not kicked off yet** (0-0, `STATUS_SCHEDULED`) write `result = 0` onto those rows. That is not inert downstream: `services/analysis_service.py::compute_team_records` treats `result.notna()` as *played* and `result == 0` as a **tie**, and `scripts/cache_builder.py`'s `week_is_complete()` gates on `result.notna().all()`. `cache_builder.py:316` calls this function for the current year, feeding `get_enriched_schedule()`.

**Why this is a follow-up, not a fix to the abbreviation-bug plan:** this bug is pre-existing for every team whose ESPN abbreviation already matched the repo's before the abbreviation fix (i.e., the other 29 teams) — the abbreviation fix doesn't introduce it, it just makes the exposure uniform across all 32 teams instead of 29. Fixing it was explicitly out of scope for that plan (whose only allowed change was the match direction, "no other behavior change"). This spec is where that fix belongs.

**The fix, when this is picked up:** add the same "don't clobber a not-yet-started or already-final game" guard `scripts/sync_live_scores.py::overlay_espn_live_fields()` already has (`if pd.notna(row.get("result")): continue`) — or, if question 1 above resolves to "delete this function," this question is moot.

## Non-goals

- Not re-litigating the abbreviation-matching fix itself — that's done, tested, and merged.
- Not a general audit of `services/live_score_service.py` beyond these two specific, already-identified items.
