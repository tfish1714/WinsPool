# ESPN Pregame Injury Signal → Prediction Pipeline

**Date:** 2026-08-19
**Status:** Not designed — backlog item, split out from the scheduled-jobs spec so it doesn't get lost. Needs a proper brainstorming pass (clarifying questions, approaches, design) before implementation.

## Origin

Split out of `docs/superpowers/specs/2026-08-19-scheduled-jobs-design.md`'s Appendix, which explicitly deferred this as out of scope for a scheduling-only spec. Original motivating question: nflverse's `injuries`/`rosters`/`depth_charts` releases update daily, not continuously — not necessarily fast enough to catch a starting QB ruled out ~90 minutes before kickoff. Is there a fresher signal, and can/should it change a prediction?

## What's already verified

- **ESPN has the data.** `https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}` (a *different* endpoint from the scoreboard one already used by `live_score_service.py`) returns a real, timestamped `injuries[]` array per game — verified live during the scheduled-jobs design session (e.g. a `"Questionable"` status timestamped same-day, hours before kickoff). Per-game, not league-wide — one call per matchup (~13 calls for a full Sunday slate), still cheap.
- Same caveat as the rest of the ESPN integration: unofficial/undocumented API, no SLA, must degrade gracefully if it changes shape or goes down.
- **The existing starter-detection mechanism can't be reused as-is.** `nn_feature_engine.py::compute_starter_qb_flags()` is the only existing "starter changed" logic in the codebase — but it's retrospective: it's built from `snap_counts` data, which only exists *after* a game is played (used to build historical training features, e.g. "this team's starter changed mid-season, here's how the model should have handled it"). There is currently no mechanism anywhere in the pipeline that takes a pregame "this player won't play today" signal and changes what goes into a not-yet-played game's prediction. This would be new, not a modification of an existing live path.

## Open questions to resolve when this is picked up

These need an actual brainstorming pass (clarifying questions → approaches → design), not decided here:

1. **What should change when a starter is flagged out?** Candidate mechanisms, roughly in order of invasiveness:
   - Swap which player's blended rate feeds `qb_tier`/`off_pass_epa` (or the equivalent for a skill-position player) for that one game's prediction, using the backup's own historical profile.
   - A cruder fallback: apply a fixed penalty/discount to the team's offensive projection when the starter is out, without needing a specific backup's profile.
   - Do nothing automatic — just surface the ESPN status as informational text near a prediction, and leave the number as-is. Lowest risk, but doesn't actually improve prediction accuracy.
2. **Trust threshold.** ESPN's own status vocabulary (Questionable/Doubtful/Out) carries real uncertainty — "Questionable" is not "Out." Does only "Out" trigger an override, or does "Doubtful" too, and with what confidence weighting?
3. **Timing/staleness.** How close to kickoff does this need to run to be worth it, given `winspool-schedule-kickoffs` (from the scheduled-jobs spec) already re-syncs+re-predicts at kickoff−60min? Is that late-enough re-run sufficient, or does this need its own tighter-to-kickoff check?
4. **Scope: which positions?** QB is the highest-leverage case (single player drives a large share of offensive projection), but the same mechanism would apply to any position the feature pipeline weights heavily. Worth deciding whether v1 is QB-only.
5. **Testing.** Needs a strategy for testing against a real "starter ruled out" historical case, plus mocking ESPN's response shape reliably (this endpoint's schema isn't documented anywhere official, only observed).
6. **Failure mode.** If ESPN's per-game summary endpoint is unreachable or malformed for some games in a slate, does the whole override step no-op for just that game, or does it need a broader circuit breaker?

## Non-goals (carried over from the scheduled-jobs spec)

- This is explicitly not about *scheduling* — the scheduled-jobs spec already covers re-running sync+predict close to kickoff. This spec is about *what the model does with a pregame injury signal once it has one*, which is a distinct, model-input-level change.
