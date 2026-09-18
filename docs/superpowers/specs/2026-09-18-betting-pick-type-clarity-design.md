# Betting Pick Type Clarity (ATS vs. Moneyline/SU)

**Date:** 2026-09-18
**Status:** Not designed — backlog stub, split out while fixing the betting-edge
alert email's unhighlighted-pick bug so the quick fix could ship first. Needs
a full look across every betting surface before implementation.

## Origin

Fixed a narrower bug the same day (`services/email_service.py::send_betting_edge_email`):
the "validated angle matches" tier rendered `matched_sides` as the literal
strings `(home)`/`(away)` instead of the actual team name, and neither tier
visually distinguished the recommended pick from the rest of the sentence.
Both fixed — team name now bolded/colored in both tiers.

After receiving the fixed email, the deeper question: **is a given pick a
moneyline/straight-up bet (who wins the game outright) or an against-the-spread
bet (who covers the Vegas line)?** These are different wagers with different
payouts and different risk — conflating them, or leaving it ambiguous which
one a "pick" means, is a real usability problem, not just a cosmetic one.

## Where this shows up (confirmed, not exhaustive)

- **Betting-edge alert email, "raw edge outliers" tier** — always spread-based
  (`edge_vs_vegas`, `model_spread` vs. `vegas_line`), so it's implicitly
  always an ATS signal, but the word "ATS" never appears in that section.
  The sentence reads "model favors **BUF** by +4.5 vs Vegas" — a reader has
  to infer "vs Vegas" means "against the spread," not stated directly.
- **Betting-edge alert email, "validated angle matches" tier** — genuinely
  mixed: `pattern_scanner_service.scan_angles` produces both an `ats_leaderboard`
  and an `su_leaderboard`, and today's fix added a "Bet" (ATS) vs. "Pick" (SU)
  verb distinction — but that's one word, easy to miss, and the metric type
  (`ATS`/`SU`) is only spelled out in a separate parenthetical later in the
  same line, not next to the pick itself.
- **Admin betting screener** (`static/js/admin_betting.js`, `services/betting_screener_service.py`)
  — `grade_bet()` always grades against `spread_line`, so this whole tool is
  ATS-only by construction, but nothing in the UI says "ATS" anywhere; a
  "Favorite" column shows the spread but doesn't connect it to what "Match"
  means for the pick shown in the adjacent column.
- **Admin ML Accuracy per-game table** (`static/js/admin_accuracy.js`) — the
  one surface that already does this correctly: separate "Model Pick"/"SU
  ✓/✗" columns and "ATS Pick"/"ATS ✓/✗" columns, clearly labeled. Worth using
  as the reference pattern for the other three surfaces instead of inventing
  a new convention.

## What "designed" would need to answer

1. **Consistent, explicit labeling** — every surface that shows a pick should
   say ATS or Moneyline/SU in the same place, using the same words, not left
   to be inferred from context or a single verb choice. Decide the exact
   wording convention once (e.g. "ATS Pick" / "Moneyline Pick", not "Bet"
   vs. "Pick") and apply it everywhere, matching what `admin_accuracy.js`
   already does.
2. **Visual treatment, not just wording** — does a badge/tag (e.g. a colored
   pill reading "ATS" or "ML" next to the team name) communicate this faster
   than prose, especially in the email where there's no interactive
   affordance (tooltips, hover) to lean on?
3. **Should the raw-edge-outliers email tier say "ATS" explicitly** — cheap,
   likely a one-line addition once the wording convention from #1 is picked.
4. **Should the admin betting screener add an explicit "ATS" label somewhere
   in its header/columns**, given the whole tool is implicitly ATS-only?
5. **Audit for any other betting-adjacent surface** not listed above (this
   list came from a quick grep, not a full sweep) — the schedule page's
   "Why TEAM?" explain modal shows an "ATS Pick" field already (per Stage 4
   of `docs/superpowers/specs/2026-09-18-model-prediction-e2e-review-design.md`)
   and should be checked for the same clarity question while that stage runs,
   rather than duplicating the audit here.

## Non-goals

- Not redesigning the underlying pick logic (`derive_prediction_scalars()`,
  `scan_angles`, `screen_games`) — already verified correct (ATS sign
  convention) elsewhere in the E2E review. This is purely a presentation/
  clarity problem layered on top of already-correct data.
- Not adding a moneyline-specific screener tool — the admin screener staying
  ATS-only is fine; it just needs to say so.
