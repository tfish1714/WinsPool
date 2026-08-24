# Preseason Predictions: In-Season Locking Semantics

**Date:** 2026-08-23
**Status:** Not designed — backlog item, split out of the preseason-predictions-consolidation final review so it doesn't get lost. Needs a proper brainstorming pass before implementation.

## Origin

Surfaced during the final whole-branch review of `docs/superpowers/plans/2026-08-23-preseason-predictions-consolidation.md` (implemented and merged, commit range `9a842f7..311f462`) as Important finding #3, deliberately parked rather than fixed in that pass:

> Locking semantics don't actually preserve "what we predicted before the season started" for the LIVE season — it rewrites unlocked daily all season, locks whatever week-18 says.

The consolidation work fixed the higher-severity bug (a missing scope guard that would have let the daily job silently overwrite and permanently lock 5+ *already-completed* historical seasons). This item is the smaller, remaining piece: for the *currently live* season, the design as shipped still has a real gap.

## What's true today (as of commit 311f462)

- `preseason_predictions` is one doc per `{season}_{team}`, holding a single season-long win-total projection (`projected_wins`/`mean_wins`/`std_dev`/`floor`/`p25`/`p75`/`ceiling`). There is no week dimension on this collection at all — see `services/db_service.py::set_preseason_predictions()`.
- `scripts/cache_builder.py`'s daily job writes/overwrites this doc for the current season (`year >= current_year`) every single day, all season long, right up through week 18 — nothing freezes it once the real draft has happened and the season is underway.
- `locked=True` is only stamped once `final_flag` is true (`is_past_season or latest_week >= 18`), i.e. once the season is completely over.
- Net effect: the number a user sees in the admin model-vs-consensus comparison or the draft recap as "preseason projection" for the *current* season is actually today's model output re-run against today's roster/injury state, not a frozen snapshot of what the model said before the draft happened. It silently drifts all season and only stops moving once the season is already finished — at which point "preseason" is a slight misnomer for what got locked.

## The user's proposed direction (2026-08-23 conversation)

Lock already-played ("historical") weeks of the *current* season, rather than only locking once the whole season is final. Worth noting going in: this doesn't map cleanly onto today's schema, since `preseason_predictions` has no per-week granularity to lock piece-by-piece — that's exactly the kind of thing the brainstorming pass needs to resolve, not something to assume the shape of here.

## Open questions to resolve when this is picked up

1. **What should actually get locked, and when?** Candidate approaches, not decided here:
   - **(a) Freeze-at-kickoff:** snapshot the doc once at the season's true pre-draft moment (or first kickoff) and never touch it again until the normal end-of-season lock — closest to what "preseason projection" literally means, but loses any signal of how the projection evolved in-season.
   - **(b) Weekly snapshots:** add a week dimension (new doc shape, e.g. `{season}_{week}_{team}` or a subcollection) so each week's projection is preserved once that week is in the past, while the "current" pointer keeps moving. Answers the user's literal framing ("historical weeks... should be locked") but is a real schema change, not a locking-flag tweak, and has its own blast-radius questions (who reads which doc today — does everything need updating to know about "current" vs "historical" shapes, or does a new collection avoid touching existing readers?).
   - **(c) Do nothing extra, just rename the concept:** stop calling the in-season, still-moving number "preseason" in the UI, and let it be an explicitly-labeled "current projection" until the season-end lock. Cheapest option; doesn't address the underlying complaint if the user actually wants historical visibility.
2. **Who actually reads this data, and does drift matter to them?** The blast-radius table in `docs/superpowers/specs/2026-08-23-preseason-predictions-consolidation-design.md` lists every current reader (mock draft, real draft room, admin comparison, draft recap). Confirm which of these specifically care about a frozen pre-draft snapshot vs. a live-updating in-season number — they may not all want the same behavior.
3. **If a weekly-snapshot shape is chosen, what triggers a week becoming "historical"?** The existing `final_flag` pattern uses `latest_week >= 18` for a whole season; an analogous per-week signal would need its own definition (last game of that week completed? all games of that week completed and no longer live?).
4. **Does this interact with `predict_season.py`'s existing footgun?** (Manually re-running it clears any lock, since its payload has no `locked` field — documented in `CLAUDE.md`'s Scheduled Jobs table as of commit 311f462.) A weekly-snapshot shape might need the same footgun re-documented for whatever new write path replaces or joins it.

## Non-goals

- Not re-opening the historical-season guard fixed in the consolidation work (`year >= current_year or force` in `cache_builder.py`) — that part is done and correct.
- Not a general historical-analytics/versioning feature — scoped specifically to the in-season drift of the "preseason" win-total projection, not a broader "track every prediction over time" system (see the separate, also-unscoped `docs/superpowers/specs/2026-08-22-feature-computation-versioning-design.md` stub if that turns out to be the same underlying need).
