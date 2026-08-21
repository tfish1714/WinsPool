# Mock Draft & Projection Gating — Design Spec

**Date:** 2026-08-15
**Status:** Approved — ready for planning

---

## Problem

The 2026 season is approaching and the admin (Tom) is the only person who has
ever used the app. Before the real draft, players need a low-friction way to
get comfortable with the draft UI, and the admin needs to be able to keep
rehearsing the real draft room without their test picks being mistaken for
real ones.

While researching this, a related gap surfaced: the live draft room's
WebSocket `state` broadcast already includes `preseason_predictions` (each
team's model/consensus win projection) for *every* connected socket,
regardless of role. `static/js/ui_renderer.js` only *renders* that data when
`role === 'admin'` — a client-side display choice, not an access control. Any
signed-in non-admin player can already read the raw projection numbers
straight out of the WebSocket JSON via browser devtools. This spec closes
that gap as part of the same change, since the new mock draft feature adopts
the same "projections are admin-only, enforced server-side" rule from day
one and it would be inconsistent to leave the real draft leaking the numbers
it's modeled after.

## Goal and Scope

Two independent but related changes:

- **Part A** — stop sending `preseason_predictions` to non-admin sockets in
  the live draft room. Server-side enforcement, no visible behavior change
  for admins, no frontend changes required (the renderer already treats
  missing predictions as "render nothing").
- **Part B** — a new, standalone solo mock draft page where one player drafts
  against 9 bot-controlled slots that pick using the same
  model/consensus projection data, with a wildcard chance of a non-optimal
  pick so it feels human. No login required. Nothing is persisted. It's a
  shareable link (`/mock-draft`) — a player opens it, picks which of the 10
  draft slots they want, and drafts; at the end they're shown where their
  draft class ranked (1st-10th) against the 9 bots, graded by the same
  model+consensus projections used everywhere else — without exposing the
  underlying win-total numbers to non-admins (see "End-of-draft ranking"
  below).

Out of scope (explicitly deferred, not needed for either goal above):

- A shared "practice mode" for multiple real players to rehearse the live
  draft room together. This is already possible today with zero new code:
  `/admin/reset_draft` (existing "Mock draft reset successful" endpoint)
  wipes a season's `draft_results` and the admin can already do this
  repeatedly before the real draft. Nothing here changes that flow.
- Resetting the actual 2026 `draft_order` / `draft_order_rules` /
  `draft_results` in production Firestore. That's an operational action the
  admin performs themselves via the deployed Admin panel (`Delete Season` →
  2026) — this session has no path to production Firestore
  (`FIREBASE_CREDENTIALS` is not configured locally) and it isn't a code
  change.
- Bot logic reused for anything beyond the solo mock draft (e.g. auto-picking
  for an absent real player in the real draft).

---

## Part A — Server-side projection gating on the live draft room

### Current behavior

`services/draft_service.py::load_draft_state()` builds one state dict
(including `preseason_predictions`) and caches it in the module-level
`_CACHED_DRAFT_STATE` singleton. `routes/draft_routes.py`'s
`ConnectionManager.broadcast()` sends that exact same payload, unmodified, to
every connected WebSocket:

```python
async def broadcast(self, message: dict):
    for conn in self.active_connections:
        await conn.send_json(message)
```

There is currently no per-connection notion of role. `socket_player_id` and
the derived admin check (`_get_authenticated_admin`) are local variables
inside the `websocket_endpoint` function scope — the `ConnectionManager` has
no visibility into them.

### Design

1. **Track admin status per connection.** `ConnectionManager` gains
   `self.admin_sockets: set[WebSocket]`, alongside the existing
   `active_connections` list.
   - `connect()`: no change to admin status (new sockets start non-admin —
     the safe default).
   - `disconnect()`: also discard from `admin_sockets`.
   - New method `set_admin(ws: WebSocket, is_admin: bool)`: adds/removes
     `ws` from `admin_sockets`.

2. **Strip projections for non-admin recipients at broadcast time**, not at
   `load_draft_state()` compute time — the singleton cache stays a single
   shared object; filtering happens per-recipient in `broadcast()`:

   ```python
   async def broadcast(self, message: dict):
       stripped = None
       if message.get("type") == "state":
           stripped = {**message, "payload": {**message["payload"], "preseason_predictions": {}}}
       for conn in self.active_connections:
           out = message if conn in self.admin_sockets else (stripped or message)
           try:
               await conn.send_json(out)
           except Exception:
               pass
   ```

   Only `preseason_predictions` is stripped. `team_schedules` is left as-is
   for everyone — it's opponent/week text (`get_team_schedule()` in
   `data_service.py`), not projection data.

3. **Wire `set_admin()` at the two points a socket's identity becomes
   known**, in `websocket_endpoint` (`draft_routes.py`):
   - `verify_code` handler: after `_get_authenticated_admin` would resolve
     true/false for the newly-set `socket_player_id`, call
     `manager.set_admin(websocket, is_admin)` before the subsequent
     `manager.broadcast(...)` call.
   - `reauthenticate` handler: same, right after `socket_player_id` is set.
   - The very first `state` message sent directly to a fresh connection
     (`await websocket.send_json(...)`, before any auth) is sent before
     `set_admin` is ever called for that socket, so it must be stripped
     manually the same way — construct it via the same stripping helper
     rather than sending `initial_state` raw.

4. **Extract the stripping logic into one helper** (e.g.
   `strip_admin_only_fields(payload: dict) -> dict` in `draft_service.py`) so
   both the initial per-connection send and `ConnectionManager.broadcast()`
   use the identical code path instead of duplicating the field list.

### Non-goals for Part A

- No change to `ui_renderer.js` / `main.js`. They already do nothing with a
  missing/empty `preseasonPredictions` for a given team — every call site is
  written as `predictions && predictions[team] ? ... : ''`-style, so an
  empty dict degrades to "no projection shown," which is already correct for
  non-admins today by convention and becomes correct-and-enforced after this
  change.
- No change to `renderAdminPortfolio` gating (`main.js` already only calls it
  when `this.user.role === 'admin'`).
- No change to how `_get_authenticated_admin` resolves admin status — reused
  as-is.

### Testing

Extend `tests/test_draft_websocket.py`: connect two sockets, verify one as
admin and one as a plain player (or leave unauthenticated), trigger a state
broadcast (e.g. via a pick), and assert the admin socket's received payload
contains non-empty `preseason_predictions` while the other socket's payload
has it stripped to `{}`. Also cover the pre-auth initial state message.

---

## Part B — Solo mock draft

### Why a new, separate page

The live draft room's architecture — one module-level `_CACHED_DRAFT_STATE`
singleton, one global `connected_players` set, one `ConnectionManager`
broadcasting identically to everyone — is built for exactly one draft
happening at a time, shared by everyone connected. That's the right shape for
the real draft (and for a future live group rehearsal, which already works
via `/admin/reset_draft`). It's the wrong shape for solo practice: two people
mock-drafting at once would stomp on each other's boards if bolted onto the
same singleton.

Instead, the mock draft is a **new, fully isolated, stateless page**:
`/mock-draft`. No login required (real benefit — several players don't have
passwords set up yet), nothing written to Firestore or `.local_db`, no
WebSocket. A page refresh simply restarts it. Concurrent mock drafts across
different browsers/players never interact with each other or with the real
draft room at all.

### Draft order — reuse `draft_order_rules`, not an invented snake

`draft_order_rules` already encodes, per season, a fixed mapping from
`draftOrder` slot (1-10) to that slot's three overall pick numbers
(`pickOne`/`pickTwo`/`pickThree`) — e.g. slot 1 → picks 1, 20, 26. Looking at
`routes/admin_routes.py::create_new_season()`, this pattern is *copied
forward* from the prior season when a new season is created — only *which
player* sits in which slot (`draft_order`) is freshly randomized
(`random.shuffle(player_ids)`); the pick-number pattern itself is stable
year over year.

The mock draft reuses this pattern directly instead of inventing snake-order
logic:

- Pull `draft_order_rules` for **whichever season currently has rows**
  (`max()` over the `season` column present in that collection) —
  deliberately decoupled from which season's *team projections* are used.
  This means the mock draft keeps working unaffected by the admin resetting
  2026's `draft_order`/`draft_order_rules` via `Delete Season` — it just
  falls back to the most recent season that still has a rules pattern (e.g.
  2025).
- Melt the 10 `(draftOrder, pickOne, pickTwo, pickThree)` rows into 30
  `(pick_number, slot)` pairs and sort by `pick_number` to get the full pick
  sequence — this is the same melt `load_draft_state()` already does for
  `draft_order`/`draft_order_rules`, minus the player-identity join.
- The human is assigned (their choice, or "random") to one of the 10 slots.
  The other 9 slots are bots, labeled generically (`Bot 2`, `Bot 3`, ...,
  never a real player's name — avoids implying an absent real player made
  that pick).

### Team projections — admin-gated, server-enforced

Same rule as Part A: the raw `projected_wins` numbers never leave the server
for a non-admin request. A request is treated as admin only if it carries a
valid admin session cookie/Bearer token (checked via a small non-raising
variant of `require_admin` — mock draft must not *require* auth, so it can't
use the existing `require_admin` dependency, which raises 401/403).

- Team projections come from `get_season_projection_legacy_shape(season)`
  (same resolver the live draft board uses — model output, falling back to
  analyst consensus), for whichever season is the current active/most recent
  season in `draft_order` (independent of which season supplied the rules
  pattern above).
- Non-admin responses omit the projections field entirely (not an empty
  object masking real data client-side — the number is simply never
  serialized).

### API surface (new)

Both endpoints live in a new `routes/mock_draft_routes.py`, mounted under
`/api/mock-draft`, and both are stateless (no session/DB writes).

**`GET /api/mock-draft/setup`**
No auth required. Response:

```json
{
  "pickSequence": [{"pick": 1, "slot": 3}, {"pick": 2, "slot": 7}, ...],
  "teams": ["ARI", "ATL", ..., "WAS"],
  "season": 2026,
  "projections": {"ARI": {"projected_wins": 7.4, "std_dev": 1.9}, ...} | omitted
}
```
`projections` is present only if the requester's session resolves to an
admin; otherwise the key is absent from the response body entirely.

**`POST /api/mock-draft/pick`**
Body: `{"season": 2026, "availableTeams": ["ARI", "ATL", ...], "wildcardsSoFar": 0, "botPicksRemaining": 27}`.
No auth required (this never returns raw projection numbers, only a chosen
team code, so it carries none of the leak risk Part A/B's gating is about).
`wildcardsSoFar` / `botPicksRemaining` exist to guarantee a minimum number of
wildcard picks per draft — see "Bot pick algorithm" below; the endpoint is
otherwise stateless, so the client is the source of truth for these running
counts.
Response: `{"team": "KC", "wasWildcard": false}`. The client increments its
own `wildcardsSoFar` whenever `wasWildcard` is `true` and decrements
`botPicksRemaining` after every bot turn, then passes the updated values on
the next call.

**`POST /api/mock-draft/results`**
Body: `{"season": 2026, "rosters": {"1": ["ARI", "KC", "DAL"], "2": [...], ...}}`
— one 3-team roster per slot (1-10), keyed by the slot number assigned during
setup. No auth required. Server computes each slot's total via
`get_season_projection_legacy_shape(season)` and ranks all 10 slots
descending. Response shape depends on requester role:

```json
// non-admin
{"rankings": [{"slot": 1, "rank": 1}, {"slot": 7, "rank": 2}, ...]}

// admin
{"rankings": [{"slot": 1, "rank": 1, "totalProjectedWins": 24.8}, ...]}
```

Non-admin responses carry rank position only — never the underlying win
totals used to compute it, consistent with `/setup`.

### Bot pick algorithm

New `services/mock_draft_service.py::bot_pick(season, available_teams,
wildcards_so_far, bot_picks_remaining)`, returning `(team, was_wildcard)`:

1. Resolve `get_season_projection_legacy_shape(season)`, filter to
   `available_teams`, sort descending by `projected_wins`.
2. **Minimum-wildcard guarantee (at least 2 per draft, of the 27 bot
   picks):** let `needed = max(0, 2 - wildcards_so_far)`. If
   `needed >= bot_picks_remaining` (i.e. this is one of the last picks where
   the minimum could still be missed), force `was_wildcard = True` this
   pick — a pity mechanic that makes the guarantee unconditional regardless
   of how the earlier random rolls went.
3. Otherwise, roll a small flat probability (~8%) of `was_wildcard = True`
   anyway, so wildcards aren't only ever the forced end-of-draft ones.
4. If `was_wildcard`, return a uniform-random pick from `available_teams`
   (ignoring ranking entirely — mimics a human reach/sleeper pick).
5. Otherwise, weighted-sample from the ranked list with tapering weights
   (top-ranked team most likely, decreasing down the list) rather than
   always taking the single best team — so the non-wildcard picks don't
   produce an identical, predictable "best team available" draft every time.
6. If `season` has no projection data at all (e.g. brand new season with
   nothing computed yet), fall back to a uniform-random pick across
   `available_teams` regardless of the above — the endpoint must never fail
   a pick.

This is the same algorithm regardless of who's asking (admin or not) — only
the *display* of the underlying numbers to a human is gated, never the bot's
own decision quality. Note the guarantee is "at least 2 wildcards among the
27 bot picks," not per-bot — a single bot could account for more than one,
or the 2+ could be spread across different bots.

### Frontend

New `templates/mock_draft.html` + `static/js/mock_draft.js` (new isolated
module, does not import `websocket_service.js` or touch the live draft's
`main.js` state). Flow:

1. Fetch `GET /api/mock-draft/setup` once on load.
2. Let the user pick a slot (1-10) or "Random".
3. Drive the 30-pick loop entirely client-side using the fetched
   `pickSequence`:
   - Human's turn: render available teams (with projection numbers if
     `projections` was present in `setup`, i.e. admin only — otherwise a
     plain alphabetical list), wait for a click.
   - Bot's turn: brief "Bot N is picking…" delay (~600ms), call
     `POST /api/mock-draft/pick` with the running `wildcardsSoFar` /
     `botPicksRemaining` counters (client-tracked, starting at `0` /
     `27`), apply the result, update the counters from the response, move
     on.
4. End of draft: client has the full 10-slot roster (it tracked every pick,
   human and bot, locally throughout the loop). It calls
   `POST /api/mock-draft/results` with all 10 rosters and renders the
   summary screen: the human's 3 teams, their rank ("You finished 3rd of
   10!"), and — admin only — the actual total projected wins per slot. A
   "Draft Again" button re-fetches `setup` and restarts.

No nav-gating decisions needed beyond adding a "Mock Draft" link to the nav
that's always visible (it doesn't depend on `draft_active`, which continues
to gate only the real draft link as it does today).

### Testing

New `tests/test_mock_draft.py`:
- `GET /setup` as anonymous/non-admin never contains a `projections` key.
- `GET /setup` with an admin session token includes `projections`.
- `pickSequence` has exactly 30 entries and reuses whatever `draft_order_rules`
  season is available in the test fixture DB.
- `POST /pick` always returns a team from the `availableTeams` list given,
  across repeated calls (ranking + wildcard both respected), and degrades to
  uniform-random without erroring when no projection data exists for the
  given season.
- `POST /pick` forces `wasWildcard: true` when `wildcardsSoFar=0` and
  `botPicksRemaining=1` (last-chance pity case), and when
  `wildcardsSoFar=1, botPicksRemaining=2` — the boundary of the guarantee.
- Simulate a full 27-call bot sequence (decrementing `botPicksRemaining` from
  27 to 1, incrementing `wildcardsSoFar` on each `wasWildcard`) and assert at
  least 2 of the 27 responses came back as wildcards, across many repeated
  simulated drafts (to catch a probability off-by-one, not just the forced
  boundary cases).
- `POST /results` as non-admin: every entry has `rank`, none has
  `totalProjectedWins`. As admin: every entry has both, and `rank` is
  consistent with `totalProjectedWins` sorted descending.

---

## Files touched

**Part A**
- `services/draft_service.py` — add `strip_admin_only_fields()` helper.
- `routes/draft_routes.py` — `ConnectionManager` admin tracking + stripped
  broadcast; wire `set_admin()` at `verify_code`/`reauthenticate`; strip the
  pre-auth initial state send.
- `tests/test_draft_websocket.py` — new coverage.

**Part B**
- `services/mock_draft_service.py` — new (bot pick algorithm, pick-sequence
  derivation from `draft_order_rules`).
- `routes/mock_draft_routes.py` — new (`GET /setup`, `POST /pick`,
  `POST /results`).
- `services/session_service.py` — small non-raising `is_admin_session(...)`
  helper (mock draft can't use `require_admin`, which raises).
- `templates/mock_draft.html`, `static/js/mock_draft.js` — new.
- `main.py` — register the new router.
- Nav template — add "Mock Draft" link.
- `tests/test_mock_draft.py` — new.
