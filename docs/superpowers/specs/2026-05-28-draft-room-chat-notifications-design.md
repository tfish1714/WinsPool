# Draft Room Chat Feed + Push Notifications — Issue #9

**Date:** 2026-05-28
**Status:** Approved
**Closes:** GitHub issue #9
**Scope:** `services/push_service.py` (new), `services/chat_service.py` (new), `static/sw.js` (new), `routes/draft_routes.py`, `routes/api_routes.py`, `templates/index.html`, `static/js/websocket_service.js`

---

## Overview

Two complementary features that make the draft room the definitive place to experience the draft:

1. **Live chat feed** — a unified activity feed combining auto-posted system events (picks, on-the-clock alerts, available teams) with player free-text messages, persisted to Firestore for the season
2. **Web Push notifications** — native OS notifications fire when it's a player's turn, even if the browser tab is closed

Both features share the same trigger point: after `save_pick()` succeeds in the WebSocket handler.

---

## Component 1 — Chat Feed

### Data model

Firestore collection: `draft_chat/{season}/messages/{auto-id}`

| Field | Type | Description |
|---|---|---|
| `type` | `"chat"` \| `"system"` | Player message vs. auto-posted event |
| `playerName` | `string` | Display name (system messages use `"System"`) |
| `text` | `string` | Message content |
| `timestamp` | `datetime` | Server time |

### `services/chat_service.py`

Three public functions:

```python
def post_system_message(season: int, text: str) -> None:
    """Write a system event message to draft_chat/{season}/messages."""

def post_chat_message(season: int, player_name: str, text: str) -> None:
    """Write a player chat message to draft_chat/{season}/messages."""

def get_recent_messages(season: int, limit: int = 50) -> list[dict]:
    """Return the most recent `limit` messages ordered by timestamp ascending."""
```

Both write functions also return the written document so the WebSocket can broadcast without a second read.

### WebSocket changes (`routes/draft_routes.py`)

**On connect:** after sending the initial `state` message, load and send chat history:
```json
{"type": "chat_history", "messages": [...last 50...]}
```

**New `chat` action:** verified players only. If `text.strip().startswith("/teams")` or the action is `"teams_list"`, post a system message listing `state["available_teams"]` sorted alphabetically. Otherwise post a player chat message.

Broadcast format for new messages:
```json
{"type": "chat_message", "msgType": "chat"|"system", "playerName": "...", "text": "...", "timestamp": "..."}
```

**After each successful `save_pick()`:** post two system messages and broadcast both:
1. `"🏈 {player} picks {team} — Pick #{n}"`
2. `"⏰ {next_player}, you're on the clock! Pick #{n+1}"` — only if draft is not complete

### Available teams command

Triggered by either:
- Player types `/teams` (detected server-side on the `chat` action)
- Player sends `{"action": "teams_list"}` (wired to the "Post teams" button in the UI)

System message format:
```
📋 Available teams: ARI, ATL, BAL, BUF, ...
```
Teams are sorted alphabetically.

---

## Component 2 — Push Notifications

### VAPID keys

Generate once locally:
```bash
python -c "from py_vapid import Vapid; v = Vapid(); v.generate_keys(); print(v.public_key); print(v.private_key)"
```

Store as env vars: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIMS_EMAIL` (e.g. `mailto:fischerthomasg@gmail.com`). Add to Cloud Run secrets.

### Service worker (`static/sw.js`)

Served at `/sw.js` via a dedicated FastAPI route (not under `/static/`) — browsers require the SW to be at the root scope.

Listens for `push` events and calls `self.registration.showNotification(title, options)`.

```javascript
self.addEventListener('push', event => {
    const data = event.data.json();
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/icon.png',
        })
    );
});
```

### `services/push_service.py`

```python
def send_push_notification(player_id: int, title: str, body: str) -> bool:
    """Look up the player's push subscription from Firestore and send via pywebpush.

    Returns True on success, False if no subscription is stored or send fails.
    """
```

Reads `players/{player_id}` for a `push_subscription` field (the browser's subscription object: `{endpoint, keys: {p256dh, auth}}`). Uses `pywebpush.webpush()` with the VAPID keys from env.

### Push subscribe endpoint (`routes/api_routes.py`)

```
POST /api/draft/push-subscribe
Body: {"playerId": int, "subscription": {...}}
Auth: required (any role)
```

Stores `subscription` on the player's Firestore document at `push_subscription`. Returns `{"ok": true}`.

### Frontend subscription flow (`static/js/websocket_service.js` or a new `push_service.js`)

On draft room load:
1. Check `'serviceWorker' in navigator && 'PushManager' in window`
2. Register `/sw.js`
3. Request notification permission (only ask once — skip if already granted/denied)
4. Subscribe with `VAPID_PUBLIC_KEY` (exposed via a `<meta>` tag in the template)
5. POST subscription to `/api/draft/push-subscribe`

### Trigger

After `save_pick()` succeeds in the WebSocket handler, determine the next player from `state["draft_board"]` using the new `active_pick` value. Call `send_push_notification(next_player_id, "⏰ You're on the clock!", f"Pick #{new_active_pick} — open WinsPool to make your pick.")` as a non-blocking background task (fire-and-forget; failure must not affect the WebSocket flow).

---

## UI — Chat Panel (`templates/index.html`)

- Collapsible panel alongside the draft board on desktop; stacks below on mobile (≤480px)
- Scrollable message list with auto-scroll to bottom on new messages
- Unread badge on the collapse toggle when minimized and new messages arrive
- Input bar with text field + Send button + "📋 Post teams" button
- System messages styled differently from player messages (muted color, italic)
- Timestamps shown as relative time (e.g. "2m ago")

---

## What This Does NOT Include

- Chat moderation or message deletion
- Read receipts or typing indicators
- Push notifications for non-draft events (game updates, standings changes)
- Chat visible outside the draft room
- Storing more than the current season's chat history
