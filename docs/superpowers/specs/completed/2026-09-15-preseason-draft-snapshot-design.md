# Preseason Draft Snapshot (#N)

**Status:** Draft
**Date:** 2026-09-15

## Context

`docs/superpowers/specs/2026-08-23-preseason-predictions-inseason-locking-followup.md`
identified a real gap left after the preseason-predictions-consolidation work
(`docs/superpowers/plans/completed/2026-08-23-preseason-predictions-consolidation.md`):
`preseason_predictions` is one doc per `{season}_{team}` that `scripts/cache_builder.py`'s
daily job overwrites every single day, all season long, right up until
`latest_week >= 18` locks it. For the *currently live* season, the number a
user sees anywhere in the app as "the preseason projection" is actually
today's model output re-run against today's roster/injury state — not a
frozen snapshot of what the model said before the draft happened. That
follow-up spec explicitly deferred design to a brainstorming pass; this spec
is that pass.

## What this is actually for

The blast-radius table in the consolidation design doc lists 9 readers of
`preseason_predictions`. Working through them with the project owner surfaced
that they split cleanly into two groups with genuinely different needs — this
was not obvious going in, and is the key finding that shapes this design:

**Needs a frozen, pre-draft snapshot (fairness/consistency):**
- Real draft room (`services/draft_service.py:153,159`)
- Mock draft setup (`routes/mock_draft_routes.py:111-112`)
- Mock draft bot AI (`services/mock_draft_service.py:130`) — bots rank and
  pick teams by this number, so a live-drifting value would make bot
  behavior inconsistent with what a human saw when they drafted
- Mock draft results grading (`services/mock_draft_service.py:157`)
- Draft recap (`routes/draft_routes.py:381`)
- Draft results / history views (`routes/history_routes.py:205`)

**Wants live, continuously-updating model output:**
- Admin forecast page (`routes/admin_routes.py:645`)
- Admin consensus comparison (`routes/admin_routes.py:693`)
- Weekly recap (`services/recap_service.py:128`)

The real complaint was never "the live number moves" — most of these readers
*need* it to move. The real gap is that there is nowhere for the six
"frozen" readers to get a number that stays stable once it matters: once a
real player has drafted against it, or a mock draft bot has picked by it, or
a draft recap has cited it, that number cannot legitimately keep changing
underneath them.

## Design

### New collection: `draft_snapshot_predictions`

One doc per `{season}_{team}`, same schema as `preseason_predictions`
(`projected_wins`/`mean_wins`/`std_dev`/`floor`/`p25`/`p75`/`ceiling`), plus a
`locked: bool` field. `preseason_predictions` itself is untouched by this
work — it keeps behaving exactly as it does today, daily-overwritten,
feeding the three "live" readers unchanged. Zero code changes for admin
forecast, consensus comparison, or weekly recap.

This mirrors the codebase's existing pattern for a new Firestore collection
(see CLAUDE.md's "Rules for any new Firestore collection" and precedents
like `elo_history`/`nn_weekly_accuracy`): its own local pkl mirror
(`draft_snapshot_predictions_{year}.pkl`), wired into
`scripts/refresh_local_pkls.py`, read via `services/data_service.py`/
`services/cache_service.py` alongside the existing predictions plumbing.

### Write path: `scripts/refresh_preseason.py`

Gains one more step after it refreshes `preseason_predictions` for a season:
copy that season's freshly-written docs into `draft_snapshot_predictions` —
**unless that season's snapshot is already `locked`**, in which case the
write is skipped (logged, not silently dropped) for that season.

This means every re-run of `refresh_preseason.py` before the real draft
starts re-copies the latest numbers into the snapshot. An admin can tune
rosters/injuries and re-run this multiple times pre-draft, and mock draft /
the real draft room always see the newest pre-draft numbers — the snapshot
isn't "locked on first write," it's locked on first *draft activity*.

### Lock condition: has this season's real draft actually started?

Checked via `draft_results` having any picks yet for the target season —
not the transient, global `draft_active` config flag. `draft_active` is a
single app-wide toggle an admin flips on/off around the live draft session;
it doesn't reliably answer "has drafting for season X ever begun," since it
could be off between picks or reused across whatever season is currently
active. `draft_results` having rows for a season is a persistent,
season-scoped fact that can't be un-set by toggling a flag.

Concretely: `refresh_preseason.py`'s new step, per season being refreshed,
queries whether `draft_results` has any rows for that season. If yes, and
the snapshot isn't already `locked`, this run performs one final overwrite
**and** stamps `locked=True` — the last pre-draft-detected state becomes
permanent. If yes and already `locked`, skip entirely. If no picks yet,
overwrite normally (still unlocked).

This resolves the spec's open question #4 for free: `scripts/predict_season.py`'s
known footgun (a manual re-run writes a payload with no `locked` field at
all, silently clearing `preseason_predictions`' own lock) cannot touch
`draft_snapshot_predictions`, because `predict_season.py` never writes to
this new collection at all.

### Readers switch

Real draft room, mock draft (setup + bot AI + grading), draft recap, and
draft results/history views all read `draft_snapshot_predictions` instead of
`preseason_predictions`. Same field names, so each call site is a
collection-name swap, not a shape change.

### Historical backfill

A one-time script copies every already-`locked` past season's
`preseason_predictions` docs into `draft_snapshot_predictions` with
`locked=True` — those seasons are already frozen in `preseason_predictions`
today (nothing rewrites a doc once `final_flag` hits `latest_week >= 18`),
so this is a pure copy, not a recomputation. This keeps all four
season-spanning "frozen" readers (draft results/history views in
particular, which display any past season) uniform: always read
`draft_snapshot_predictions`, never branch on "is this season new enough to
have a snapshot."

## What happens in an edge case: draft happens, then admin re-runs `refresh_preseason.py` again

Nothing — the lock check skips the write for that season. This was the
specific case walked through with the project owner: re-running the refresh
after the real draft has already started must never retroactively change
what the draft room, mock draft, or draft recap show, even though it's still
technically "before kickoff." The lock boundary is the real draft, not the
season's first game.

## Out of scope

- No per-week snapshot series (spec's original option (b)) — one snapshot
  per season is sufficient once the actual need (fairness/consistency
  during and after the draft) was identified; weekly granularity was never
  actually requested.
- Not a general prediction-versioning/historical-tracking system — see the
  separate, still-unscoped `docs/superpowers/specs/2026-08-22-feature-computation-versioning-design.md`
  if that turns out to be a related but distinct need later.
- Not reopening the historical-season overwrite guard already fixed in
  `cache_builder.py` (`year >= current_year or force`) — that's done and
  correct, unrelated to this collection.
- Not changing `cache_builder.py`'s daily write behavior for
  `preseason_predictions` at all — the live number's daily refresh, and its
  own eventual end-of-season lock, are both untouched.
