# Unpaid Entry Visibility ("Shame" in the Later Weeks) - Spec

**Date:** 2026-09-27
**Status:** Specification. Not started.
**Target:** in-season 2026, live before the default start week (week 10) so it can be switched on by the commissioner. Effort: M (about half a day plus review).
**Relates to:** the pool fee tracker (`services/pool_service.py`, `GET /api/pool/status`), the per-season pool config (`config/settings.pool_config`, admin Pool tab), the standings page (`templates/wins_pool.html`, `static/js/standings_refresh.js`).

## Goal

Late in the season, when entries are still unpaid, let the commissioner turn on gently escalating public visibility of who has not paid, so the pot gets collected without the commissioner having to chase people privately. It is off by default, controlled per season by the commissioner, and never exposes anything before the commissioner turns it on.

## Current behavior (what must stay true unless the feature is on)

* A member sees only counts (`paid_count`, `total_count`) and their own `my_paid` on `/api/pool/status`. No other player's paid flag is exposed anywhere to non-admins; a test pins the exact key set of that response. Admins see every player's flag in the Members tab.
* The standings chip shows `Pot $X | You: Paid / Not yet paid`.

## Behavior when enabled

Two stages, driven by the current NFL week (`get_latest_season_and_week`) and the commissioner's settings:

1. **Nudge stage (from `nudge_week`, default 8):** private only. Each unpaid player sees a stronger reminder on their own pages (a dismissible-per-session notice at the top of the standings page and on their player page: "Your $200 entry is still unpaid" with the amount from the pool config). Optional push notification and email reminder (see Nudges) sent at most once per week.
2. **Public stage (from `public_week`, default 10):** the standings page shows a small text pill "Unpaid" next to each unpaid player's name, and the pool chip text becomes `Pot $X | 7 of 10 paid`. From `banner_week` (default 13) a one-line note below the header lists the unpaid names: "Still owed: Name, Name". Both are informational, neutral wording, no color alarms (muted warning tone from the existing tokens).

Settings are per season, stored beside the fee and payouts:

```
config/settings.pool_config["<season>"].unpaid_visibility = {
  "enabled": false,          # master switch; default off
  "nudge_week": 8,
  "public_week": 10,
  "banner_week": 13
}
```

Validation: integers 1 to 22, `nudge_week <= public_week <= banner_week`; anything invalid falls back to defaults with `enabled: false`.

## Requirements

### R1. Config and admin controls
* Extend `services/pool_service.py` (`get_pool_config` / `set_pool_config`) and the admin endpoints `GET/POST /api/admin/pool/config` with the `unpaid_visibility` object (camelCase in the request model: `unpaidVisibility`), same read-modify-write per season behavior, same admin-only auth.
* Admin Pool tab (`static/js/admin_pool.js`, `templates/admin.html`): a checkbox "Show unpaid entries to members", three small number fields for the weeks, and a live preview line ("Nudge from week 8, names shown from week 10, list from week 13"). DOM via `textContent`; mobile friendly.

### R2. Server-side exposure, gated
* Add a new endpoint `GET /api/pool/unpaid?season=` (auth required) that returns `{"enabled": bool, "stage": "off"|"nudge"|"public"|"banner", "week": int, "amount": float, "unpaid": [{"playerId", "name"}] | []}`. The `unpaid` list is populated ONLY when the stage is `public` or `banner` (and `enabled`); in `off` and `nudge` it is an empty list, so the data cannot leak by editing the client. In the `nudge` stage the endpoint returns an additional `"me_unpaid": true|false` for the caller only.
* Do NOT add other players' paid flags to `/api/pool/status`; its key-set test stays exactly as is. The new endpoint is the only place they can appear, and only under the gate above.
* Admins always get the full unpaid list regardless of stage (they already can via the Members tab); the response marks `"admin_view": true`.
* Week and season come from existing helpers; the stage is a pure function `compute_unpaid_stage(week, settings)` (unit tested at the boundaries).

### R3. Standings page
* `templates/wins_pool.html` renders nothing extra by default. A small script (new `static/js/unpaid_notice.js`, or added to `pool_fee.js`) fetches `/api/pool/unpaid` after load and, by stage:
  * `nudge` and `me_unpaid`: show a notice strip for the caller.
  * `public`: add a text pill "Unpaid" (a `<span>`, never a link) next to matching player names in all three renderings (leader card, desktop rows, stacked mobile cards), matched by `data-player-id`.
  * `banner`: also show the "Still owed" line.
* Mobile rules from the standings link work apply: the pill is not a tap target and must not sit next to the player-name link so as to be mistakable for it (place it after the name with spacing; the name link keeps its 44px area).
* The 30 second live refresh must not remove or duplicate the pills: apply them idempotently and re-apply after each refresh (look at how `standings_refresh.js` patches text in place).
* Failure to load or an `off` stage renders nothing (the page never shows an error for this).

### R4. Player page
* The own-page pool card already shows "Your entry: Paid / Not yet paid"; in stage `nudge` and later it also shows the amount owed and a short line on how to pay (a free-text field `payment_note` in the season pool config, for example a Venmo handle; optional, plain text, escaped, max 200 chars; shown only to unpaid players and admins).

### R5. Nudges (optional, can ship after R1-R4)
* A weekly job step (piggyback on the existing `winspool-schedule-kickoffs` Tuesday run, non-fatal like the betting alert) that, when the stage is `nudge` or later, sends each unpaid player a private reminder using the existing push (`services/push_service.py`) and email (`services/email_service.py`, respecting `DISABLE_OUTBOUND_EMAIL`), at most once per player per week (record last-nudged week on the player document or in `pool_config`), and never after the player is marked paid.

## Non-goals and open questions

* Not changing who can win or receive payouts. Open question for the commissioner: should an unpaid entry be ineligible for the payout? (Currently not enforced anywhere; out of scope here.)
* No naming of unpaid players anywhere else (recaps, emails to the group, push broadcasts).
* Tone and wording are the commissioner's call; the wording above is neutral by default and lives in one constants block so it is easy to change.

## Tests

* `compute_unpaid_stage` boundaries (before, at, after each week; invalid settings; disabled).
* `/api/pool/unpaid`: `off` and `nudge` return an empty list for a non-admin even when players are unpaid; `public` returns names; `admin_view` for admins; unauthenticated 401; the exact key-set test on `/api/pool/status` is unchanged and still passes (no leak there).
* Admin config round trip for `unpaidVisibility` including invalid values and season isolation (saving one season does not alter another).
* Markup/source contracts: pills are `<span>`, not anchors; the script applies pills idempotently and does not use `innerHTML` with server data.
* Cache correctness: marking a player paid via the Members tab is reflected on the next `/api/pool/unpaid` call (same static-cache invalidation path that `set_member_paid` already uses).
* Nudge job (R5): once per week per player, skips paid players, never runs when disabled, failure is non-fatal.

## Rollout

Deploy with `enabled: false`; verify the admin controls; the commissioner turns it on for the season when ready. Because it is off by default and fail-closed (`unpaid` empty unless the gate passes), shipping it early has no visible effect.

## Suggested files

`services/pool_service.py`, `routes/api_routes.py` (new route beside `/pool/status`), `routes/admin_routes.py`, `routes/models.py`, `static/js/admin_pool.js`, `templates/admin.html`, `static/js/pool_fee.js` or new `static/js/unpaid_notice.js`, `templates/wins_pool.html`, `templates/player_profile.html`, `static/style.css` (`.unpaid-pill`, `.unpaid-notice`), `scripts/schedule_kickoffs.py` (R5), `tests/test_unpaid_visibility.py`, `CLAUDE.md`.
