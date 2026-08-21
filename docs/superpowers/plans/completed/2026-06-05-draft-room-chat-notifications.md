# Draft Room Chat + Push Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live chat feed (system events + player messages) and web push notifications to the draft room so players know when it's their turn even with the tab closed.

**Architecture:** Chat messages are persisted to Firestore subcollection `draft_chat/{season}/messages/` and broadcast via the existing WebSocket. Push notifications use the Web Push API with VAPID keys; player subscriptions are stored on their Firestore document; `pywebpush` sends the notification server-side as a fire-and-forget background task after each successful pick.

**Tech Stack:** Python/FastAPI, Firestore, pywebpush, vanilla JS (ES6 modules), Web Push API, Service Worker.

---

## File Map

| File | Change |
|---|---|
| `services/chat_service.py` | New — Firestore chat read/write |
| `services/push_service.py` | New — pywebpush send + subscription lookup |
| `static/sw.js` | New — service worker for push events |
| `static/js/chat.js` | New — chat panel render + input handling |
| `tests/test_chat_service.py` | New — unit tests for chat_service |
| `requirements.txt` | Add `pywebpush` |
| `routes/draft_routes.py` | WebSocket: chat history on connect, chat action, system msgs after pick, push trigger |
| `routes/api_routes.py` | Add `POST /api/draft/push-subscribe` |
| `main.py` | Add `/sw.js` static route at root scope |
| `templates/index.html` | Chat panel HTML; VAPID public key meta tag |
| `static/js/main.js` | Handle `chat_history` and `chat_message` WS messages; init push subscription |

---

## Key Data Facts

**Firestore chat document schema:**
```
draft_chat/{season}/messages/{auto-id}
  type:       "chat" | "system"
  playerName: str
  text:       str
  timestamp:  datetime (server time)
```

**WebSocket message types added:**
- Server→client: `{"type": "chat_history", "messages": [...]}` — sent on connect
- Server→client: `{"type": "chat_message", "msgType": "chat"|"system", "playerName": "...", "text": "...", "timestamp": "..."}` — broadcast on new message
- Client→server: `{"action": "chat", "text": "..."}` — player sends message

**Push subscription shape** (standard Web Push API object stored on player doc):
```json
{"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}}
```

**`VAPID_PUBLIC_KEY`** env var — URL-safe base64 public key. Exposed via `<meta name="vapid-public-key">` in `index.html`.

**`save_pick()` is called at two places** in `websocket_endpoint` (draft_routes.py): line 556 (`force_pick`) and line 592 (`pick`). Both need the post-pick chat + push logic.

**Local dev:** Chat writes go to Firestore even in `USE_LOCAL_DATA=True` mode. `push_service.py` is a no-op when env vars are absent — failure is silently swallowed.

---

## PART A — CHAT FEED

---

## Task 1: `chat_service.py` + tests

**Files:**
- Create: `services/chat_service.py`
- Create: `tests/test_chat_service.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_service.py`:

```python
"""tests/test_chat_service.py"""
import pytest
from unittest.mock import MagicMock, patch


class TestPostSystemMessage:
    def test_writes_system_type_to_firestore(self):
        from services.chat_service import post_system_message
        mock_col = MagicMock()
        mock_doc = MagicMock()
        mock_col.return_value.add.return_value = (None, mock_doc)
        mock_doc.get.return_value.to_dict.return_value = {
            "type": "system", "playerName": "System",
            "text": "Test msg", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection = mock_col
            result = post_system_message(2026, "Test msg")
        assert result["type"] == "system"
        assert result["playerName"] == "System"
        assert result["text"] == "Test msg"

    def test_returns_none_on_firestore_error(self):
        from services.chat_service import post_system_message
        with patch("services.chat_service.get_db", side_effect=Exception("db down")):
            result = post_system_message(2026, "Test msg")
        assert result is None


class TestPostChatMessage:
    def test_writes_chat_type_with_player_name(self):
        from services.chat_service import post_chat_message
        mock_col = MagicMock()
        mock_doc = MagicMock()
        mock_col.return_value.add.return_value = (None, mock_doc)
        mock_doc.get.return_value.to_dict.return_value = {
            "type": "chat", "playerName": "Alice",
            "text": "Hello", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection = mock_col
            result = post_chat_message(2026, "Alice", "Hello")
        assert result["type"] == "chat"
        assert result["playerName"] == "Alice"


class TestGetRecentMessages:
    def test_returns_list_of_dicts(self):
        from services.chat_service import get_recent_messages
        mock_snap = MagicMock()
        mock_snap.to_dict.return_value = {
            "type": "system", "playerName": "System",
            "text": "Pick made", "timestamp": "2026-01-01T00:00:00",
        }
        with patch("services.chat_service.get_db") as mock_db:
            mock_db.return_value.collection.return_value\
                .document.return_value.collection.return_value\
                .order_by.return_value.limit.return_value\
                .stream.return_value = [mock_snap]
            result = get_recent_messages(2026, limit=50)
        assert isinstance(result, list)
        assert result[0]["type"] == "system"

    def test_returns_empty_list_on_error(self):
        from services.chat_service import get_recent_messages
        with patch("services.chat_service.get_db", side_effect=Exception("db down")):
            result = get_recent_messages(2026)
        assert result == []
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_chat_service.py -v
```
Expected: ImportError — `chat_service` not found.

- [ ] **Step 3: Implement `services/chat_service.py`**

```python
"""services/chat_service.py — Firestore-backed draft room chat."""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _messages_ref(season: int):
    from services.db_service import get_db
    return get_db().collection("draft_chat").document(str(season)).collection("messages")


def post_system_message(season: int, text: str) -> dict | None:
    """Write a system event to draft_chat/{season}/messages. Returns the written doc or None."""
    try:
        ref = _messages_ref(season)
        data = {
            "type": "system",
            "playerName": "System",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post system message")
        return None


def post_chat_message(season: int, player_name: str, text: str) -> dict | None:
    """Write a player chat message to draft_chat/{season}/messages. Returns written doc or None."""
    try:
        ref = _messages_ref(season)
        data = {
            "type": "chat",
            "playerName": player_name,
            "text": text[:500],  # cap length
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _, doc_ref = ref.add(data)
        written = doc_ref.get().to_dict() or data
        written.setdefault("timestamp", data["timestamp"])
        return written
    except Exception:
        logger.exception("chat_service: failed to post chat message")
        return None


def get_recent_messages(season: int, limit: int = 50) -> list[dict]:
    """Return the most recent messages ordered by timestamp ascending."""
    try:
        from google.cloud.firestore import Query
        docs = (
            _messages_ref(season)
            .order_by("timestamp", direction=Query.ASCENDING)
            .limit(limit)
            .stream()
        )
        return [d.to_dict() for d in docs]
    except Exception:
        logger.exception("chat_service: failed to get messages")
        return []
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_chat_service.py -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add services/chat_service.py tests/test_chat_service.py
git commit -m "feat: add chat_service for Firestore draft chat read/write"
```

---

## Task 2: WebSocket — chat history on connect + `chat` action

**Files:**
- Modify: `routes/draft_routes.py`

- [ ] **Step 1: Add import at top of `draft_routes.py`**

After the existing imports (around line 17), add:

```python
from services.chat_service import post_system_message, post_chat_message, get_recent_messages
```

- [ ] **Step 2: Send chat history on connect**

In `websocket_endpoint`, after line 442:
```python
await websocket.send_json({"type": "state", "payload": load_draft_state(connected_players)})
```

Add immediately after:
```python
        history = get_recent_messages(load_draft_state(connected_players).get("season", 2026))
        await websocket.send_json({"type": "chat_history", "messages": history})
```

- [ ] **Step 3: Add `chat` action handler**

In the `while True` loop, after the final `elif action == "pick":` block (after line 593), add a new `elif`:

```python
            elif action == "chat":
                if socket_player_id is None:
                    await websocket.send_json({"type": "error", "message": "Sign in before chatting."})
                    continue
                state = load_draft_state(connected_players, year=target_year)
                text = str(msg.get("text", "")).strip()
                if not text:
                    continue
                # /teams command — post system message listing available teams
                if text.lower().startswith("/teams"):
                    teams_str = ", ".join(sorted(state.get("available_teams", [])))
                    msg_doc = post_system_message(
                        state["season"], f"📋 Available teams: {teams_str}"
                    )
                else:
                    player_info = next(
                        (p for p in state.get("all_players", []) if p["playerId"] == socket_player_id),
                        None,
                    )
                    player_name = player_info["playerName"] if player_info else f"Player {socket_player_id}"
                    msg_doc = post_chat_message(state["season"], player_name, text)
                if msg_doc:
                    await manager.broadcast({
                        "type": "chat_message",
                        "msgType": msg_doc["type"],
                        "playerName": msg_doc["playerName"],
                        "text": msg_doc["text"],
                        "timestamp": msg_doc["timestamp"],
                    })

            elif action == "teams_list":
                state = load_draft_state(connected_players, year=target_year)
                teams_str = ", ".join(sorted(state.get("available_teams", [])))
                msg_doc = post_system_message(
                    state["season"], f"📋 Available teams: {teams_str}"
                )
                if msg_doc:
                    await manager.broadcast({
                        "type": "chat_message",
                        "msgType": "system",
                        "playerName": "System",
                        "text": msg_doc["text"],
                        "timestamp": msg_doc["timestamp"],
                    })
```

- [ ] **Step 4: Manual smoke test**

Start the server: `uvicorn main:app --reload`

Open the draft room, open browser DevTools console, confirm:
- On connect, the WS receives `{"type": "chat_history", "messages": [...]}` (may be empty array if no messages yet)
- No errors in server console

- [ ] **Step 5: Commit**

```bash
git add routes/draft_routes.py
git commit -m "feat: WebSocket chat history on connect and chat/teams_list actions"
```

---

## Task 3: WebSocket — system messages after each pick

**Files:**
- Modify: `routes/draft_routes.py`

After `save_pick()` is called in both the `force_pick` and `pick` handlers, post system messages. There are two call sites:

**Call site 1 (`force_pick`, around line 556):**

Find:
```python
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=player.get("playerName", "Admin"))
                    await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
```

Replace with:
```python
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=player.get("playerName", "Admin"))
                    new_state = load_draft_state(connected_players, year=target_year)
                    await manager.broadcast({"type": "state", "payload": new_state})
                    await _broadcast_pick_messages(manager, state, new_state, team)
```

**Call site 2 (`pick`, around line 592):**

Find:
```python
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=executed_by)
                    await manager.broadcast({"type": "state", "payload": load_draft_state(connected_players, year=target_year)})
```

Replace with:
```python
                    save_pick(state["season"], active_pick, target_pid, team, executed_by=executed_by)
                    new_state = load_draft_state(connected_players, year=target_year)
                    await manager.broadcast({"type": "state", "payload": new_state})
                    await _broadcast_pick_messages(manager, state, new_state, team)
```

**Add the helper function** before `websocket_endpoint` (around line 436):

```python
async def _broadcast_pick_messages(manager, old_state: dict, new_state: dict, team: str) -> None:
    """Post pick + on-the-clock system messages and broadcast them."""
    season = old_state["season"]
    active_pick = old_state["active_pick"]
    picker = next(
        (p for p in old_state.get("all_players", [])
         if p["playerId"] == next(
             (x["playerId"] for x in old_state["draft_board"] if x["pick"] == active_pick), None
         )),
        None,
    )
    picker_name = picker["playerName"] if picker else "Unknown"
    pick_msg = post_system_message(season, f"🏈 {picker_name} picks {team} — Pick #{active_pick}")
    if pick_msg:
        await manager.broadcast({
            "type": "chat_message",
            "msgType": "system",
            "playerName": "System",
            "text": pick_msg["text"],
            "timestamp": pick_msg["timestamp"],
        })
    # On-the-clock message for next pick (if draft is not complete)
    new_active = new_state.get("active_pick", active_pick + 1)
    if new_active <= 30:
        next_player = next(
            (p for p in new_state.get("all_players", [])
             if p["playerId"] == next(
                 (x["playerId"] for x in new_state["draft_board"] if x["pick"] == new_active), None
             )),
            None,
        )
        if next_player:
            clock_msg = post_system_message(
                season, f"⏰ {next_player['playerName']}, you're on the clock! Pick #{new_active}"
            )
            if clock_msg:
                await manager.broadcast({
                    "type": "chat_message",
                    "msgType": "system",
                    "playerName": "System",
                    "text": clock_msg["text"],
                    "timestamp": clock_msg["timestamp"],
                })
```

- [ ] **Step 2: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing.

- [ ] **Step 3: Commit**

```bash
git add routes/draft_routes.py
git commit -m "feat: post system chat messages after each draft pick"
```

---

## Task 4: Chat UI panel + `chat.js`

**Files:**
- Modify: `templates/index.html`
- Create: `static/js/chat.js`
- Modify: `static/js/main.js`

- [ ] **Step 1: Add chat panel to `index.html`**

In `templates/index.html`, add a chat section after the admin portfolio section (before `</main>`):

```html
        <!-- Chat Feed -->
        <section class="card-glass" id="chat-section" style="margin-top: 1rem;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
                <h2 style="margin:0;">
                    Draft Chat
                    <span id="chat-unread-badge" style="display:none; background:var(--accent-red); color:#fff;
                        border-radius:99px; font-size:0.65rem; padding:2px 7px; vertical-align:middle;
                        margin-left:6px;">0</span>
                </h2>
                <button id="chat-collapse-btn" style="background:none; border:none; color:var(--text-secondary);
                    cursor:pointer; font-size:0.8rem;">Hide</button>
            </div>
            <div id="chat-body">
                <div id="chat-messages" style="height:220px; overflow-y:auto; display:flex; flex-direction:column;
                    gap:4px; margin-bottom:0.75rem; padding-right:4px;"></div>
                <div style="display:flex; gap:0.5rem; align-items:center;">
                    <input id="chat-input" type="text" maxlength="500" placeholder="Message…"
                        style="flex:1; background:rgba(255,255,255,0.07); border:1px solid var(--glass-border);
                        border-radius:6px; padding:0.4rem 0.75rem; color:var(--ink); font-size:0.85rem;"/>
                    <button id="chat-send-btn" class="btn-primary"
                        style="padding:0.4rem 0.9rem; font-size:0.82rem;">Send</button>
                    <button id="chat-teams-btn" class="btn-primary"
                        style="padding:0.4rem 0.75rem; font-size:0.82rem;
                        background:rgba(255,255,255,0.08); border-color:var(--glass-border);">📋</button>
                </div>
            </div>
        </section>
```

- [ ] **Step 2: Create `static/js/chat.js`**

```javascript
/**
 * chat.js — Draft room chat panel.
 * Exported functions are called from main.js handleWsMessage and onWsOpen.
 */

let _ws = null;
let _unreadCount = 0;
let _collapsed = false;

export function initChat(ws) {
    _ws = ws;
    _wireButtons();
}

export function loadHistory(messages) {
    const box = document.getElementById('chat-messages');
    if (!box) return;
    box.innerHTML = '';
    messages.forEach(m => _appendMessage(m.msgType ?? m.type, m.playerName, m.text, m.timestamp, false));
    _scrollBottom();
}

export function appendMessage(msgType, playerName, text, timestamp) {
    _appendMessage(msgType, playerName, text, timestamp, true);
}

function _appendMessage(msgType, playerName, text, timestamp, animate) {
    const box = document.getElementById('chat-messages');
    if (!box) return;

    const isSystem = msgType === 'system';
    const ts = _relTime(timestamp);
    const el = document.createElement('div');
    el.style.cssText = `font-size:0.8rem; padding:3px 6px; border-radius:5px; word-break:break-word;
        ${isSystem
            ? 'color:var(--text-secondary); font-style:italic; background:rgba(255,255,255,0.03);'
            : 'background:rgba(255,255,255,0.05);'}`;
    el.innerHTML = isSystem
        ? `<span>${text}</span> <span style="color:var(--text-secondary);font-size:0.7rem;">${ts}</span>`
        : `<span style="font-weight:600; color:var(--accent-gold);">${_esc(playerName)}</span>
           <span style="color:var(--text-secondary);"> ${ts}</span><br>${_esc(text)}`;
    box.appendChild(el);

    const atBottom = box.scrollHeight - box.clientHeight - box.scrollTop < 60;
    if (atBottom || !animate) {
        _scrollBottom();
    } else if (animate && _collapsed) {
        _bumpUnread();
    }
}

function _wireButtons() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send-btn');
    const teamsBtn = document.getElementById('chat-teams-btn');
    const collapseBtn = document.getElementById('chat-collapse-btn');

    sendBtn?.addEventListener('click', _sendMessage);
    input?.addEventListener('keydown', e => { if (e.key === 'Enter') _sendMessage(); });

    teamsBtn?.addEventListener('click', () => {
        _ws?.send({ action: 'teams_list' });
    });

    collapseBtn?.addEventListener('click', () => {
        const body = document.getElementById('chat-body');
        if (!body) return;
        _collapsed = !_collapsed;
        body.style.display = _collapsed ? 'none' : '';
        collapseBtn.textContent = _collapsed ? 'Show' : 'Hide';
        if (!_collapsed) _clearUnread();
    });
}

function _sendMessage() {
    const input = document.getElementById('chat-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;
    _ws?.send({ action: 'chat', text });
    input.value = '';
}

function _scrollBottom() {
    const box = document.getElementById('chat-messages');
    if (box) box.scrollTop = box.scrollHeight;
}

function _bumpUnread() {
    _unreadCount++;
    const badge = document.getElementById('chat-unread-badge');
    if (badge) { badge.textContent = _unreadCount; badge.style.display = 'inline'; }
}

function _clearUnread() {
    _unreadCount = 0;
    const badge = document.getElementById('chat-unread-badge');
    if (badge) badge.style.display = 'none';
}

function _relTime(iso) {
    if (!iso) return '';
    const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    return `${Math.floor(diff / 3600)}h ago`;
}

function _esc(str) {
    return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
```

- [ ] **Step 3: Wire chat into `main.js`**

Add import at the top of `static/js/main.js`:
```javascript
import { initChat, loadHistory, appendMessage } from './chat.js';
```

In `handleWsMessage`, add two new `else if` branches:
```javascript
    handleWsMessage(msg) {
        if (msg.type === 'state') {
            this.lastDraftState = msg.payload;
            this.renderDraftState(msg.payload);
        } else if (msg.type === 'error') {
            alert(msg.message);
        } else if (msg.type === 'chat_history') {
            loadHistory(msg.messages);
        } else if (msg.type === 'chat_message') {
            appendMessage(msg.msgType, msg.playerName, msg.text, msg.timestamp);
        }
    }
```

In `onWsOpen` (or wherever the WebSocket is first connected), add after the `ws` is established — find where `this.ws` is assigned and the `DraftApp` class is initialized, then add `initChat(this.ws)` after the WebSocket connects. The safest place is in `onWsOpen`:

```javascript
    onWsOpen() {
        if (this.user.playerId) {
            this.ws.send({ action: 'reauthenticate', playerId: this.user.playerId });
        }
        this.updateStatusBanner('Connected. Waiting for state...');
        initChat(this.ws);
    }
```

- [ ] **Step 4: Manual test**

```bash
uvicorn main:app --reload
```

Open the draft room. Confirm:
- Chat panel is visible below the portfolio section
- Messages area is empty (or shows history if any exist in Firestore)
- Typing a message and pressing Send → `{"action": "chat", "text": "..."}` sent (check Network > WS frames)
- 📋 button sends `{"action": "teams_list"}`
- Hide/Show toggle collapses the panel

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/js/chat.js static/js/main.js
git commit -m "feat: add draft chat UI panel with message feed, input, and teams button"
```

---

## PART B — PUSH NOTIFICATIONS

---

## Task 5: VAPID key generation + `push_service.py`

**Files:**
- Modify: `requirements.txt`
- Create: `services/push_service.py`

- [ ] **Step 1: Add `pywebpush` to requirements**

In `requirements.txt`, add after the last line:
```
pywebpush>=2.0.0
```

Install locally:
```bash
pip install pywebpush>=2.0.0
```

- [ ] **Step 2: Generate VAPID keys (one-time setup)**

```bash
python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('VAPID_PUBLIC_KEY =', v.public_key)
print('VAPID_PRIVATE_KEY =', v.private_key)
"
```

Copy the output and add to your `.env` file:
```
VAPID_PUBLIC_KEY=<paste public key>
VAPID_PRIVATE_KEY=<paste private key>
VAPID_CLAIMS_EMAIL=mailto:fischerthomasg@gmail.com
```

These will also need to be added as Cloud Run secrets before deploying push support.

- [ ] **Step 3: Create `services/push_service.py`**

```python
"""services/push_service.py — Web Push notifications via pywebpush + VAPID."""
import json
import logging
import os

logger = logging.getLogger(__name__)

_VAPID_PUBLIC  = os.environ.get("VAPID_PUBLIC_KEY", "")
_VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE_KEY", "")
_VAPID_EMAIL   = os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:admin@example.com")


def save_push_subscription(player_id: int, subscription: dict) -> bool:
    """Store the browser's push subscription object on the player's Firestore document."""
    try:
        from services.db_service import get_db
        get_db().collection("players").document(str(player_id)).update(
            {"push_subscription": subscription}
        )
        return True
    except Exception:
        logger.exception("push_service: failed to save subscription for player %s", player_id)
        return False


def send_push_notification(player_id: int, title: str, body: str) -> bool:
    """Send a web push notification to player_id. Returns True on success.

    Returns False silently if VAPID keys are missing, no subscription is stored,
    or the push fails — callers must never block on this.
    """
    if not _VAPID_PUBLIC or not _VAPID_PRIVATE:
        return False
    try:
        from services.db_service import get_db
        doc = get_db().collection("players").document(str(player_id)).get()
        if not doc.exists:
            return False
        sub = doc.to_dict().get("push_subscription")
        if not sub:
            return False

        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=_VAPID_PRIVATE,
            vapid_claims={"sub": _VAPID_EMAIL},
        )
        return True
    except Exception:
        logger.debug("push_service: push failed for player %s (subscription may be expired)", player_id)
        return False
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt services/push_service.py
git commit -m "feat: add push_service with VAPID web push and subscription storage"
```

---

## Task 6: Service worker + `/sw.js` route

**Files:**
- Create: `static/sw.js`
- Modify: `main.py`

- [ ] **Step 1: Create `static/sw.js`**

```javascript
self.addEventListener('push', event => {
    const data = event.data ? event.data.json() : { title: 'WinsPool', body: "You're on the clock!" };
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            icon: '/static/fishbone.png',
            badge: '/static/fishbone.png',
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(clients.openWindow('/'));
});
```

- [ ] **Step 2: Serve `/sw.js` at root scope**

Service workers must be served from the root URL scope (not `/static/`). In `main.py`, after the `/static` mount, add:

```python
from fastapi.responses import FileResponse

@app.get("/sw.js", include_in_schema=False)
async def serve_service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript")
```

- [ ] **Step 3: Verify the route**

```bash
uvicorn main:app --reload
curl http://localhost:8000/sw.js
```
Expected: JavaScript file contents returned (the service worker code).

- [ ] **Step 4: Commit**

```bash
git add static/sw.js main.py
git commit -m "feat: add service worker for push notifications at /sw.js"
```

---

## Task 7: Push subscribe API endpoint

**Files:**
- Modify: `routes/api_routes.py`

- [ ] **Step 1: Add the endpoint**

In `routes/api_routes.py`, find a suitable location (e.g., near the end of the file before any catch-all routes) and add:

```python
@router.post("/draft/push-subscribe")
async def push_subscribe(request: Request, _auth: dict = Depends(require_auth)):
    """Store a browser push subscription on the player's Firestore document."""
    try:
        body = await request.json()
        player_id = body.get("playerId")
        subscription = body.get("subscription")
        if not player_id or not subscription:
            return bad_request("playerId and subscription are required.")
        from services.push_service import save_push_subscription
        ok = save_push_subscription(int(player_id), subscription)
        if ok:
            return JSONResponse(content={"ok": True})
        return server_error()
    except Exception:
        logger.exception("push_subscribe error")
        return server_error()
```

- [ ] **Step 2: Run full tests**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing.

- [ ] **Step 3: Commit**

```bash
git add routes/api_routes.py
git commit -m "feat: add POST /api/draft/push-subscribe endpoint"
```

---

## Task 8: Frontend push subscription + VAPID meta tag

**Files:**
- Modify: `templates/index.html`
- Modify: `routes/draft_routes.py` (pass VAPID key to template)
- Modify: `static/js/main.js`

- [ ] **Step 1: Pass VAPID key to the draft template**

In `routes/draft_routes.py`, find `serve_draft_board` (around line 80):

```python
async def serve_draft_board(request: Request):
    return templates.TemplateResponse(request, "index.html", {})
```

Replace with:

```python
async def serve_draft_board(request: Request):
    import os
    return templates.TemplateResponse(request, "index.html", {
        "vapid_public_key": os.environ.get("VAPID_PUBLIC_KEY", ""),
    })
```

- [ ] **Step 2: Add VAPID meta tag to `index.html`**

In `templates/index.html`, in the `{% block content %}` section, add at the very top (before the `<div class="container">`):

```html
{% if vapid_public_key %}
<meta name="vapid-public-key" content="{{ vapid_public_key }}">
{% endif %}
```

- [ ] **Step 3: Add push subscription logic to `main.js`**

Add a new method `initPushNotifications()` to the `DraftApp` class in `static/js/main.js`:

```javascript
    async initPushNotifications() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
        const vapidKey = document.querySelector('meta[name="vapid-public-key"]')?.content;
        if (!vapidKey) return;

        try {
            const reg = await navigator.serviceWorker.register('/sw.js');
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') return;

            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: _urlBase64ToUint8Array(vapidKey),
            });

            const token = AuthService.getToken();
            await fetch('/api/draft/push-subscribe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    playerId: this.user.playerId,
                    subscription: sub.toJSON(),
                }),
            });
        } catch (e) {
            console.warn('[Push] Subscription failed:', e);
        }
    }
```

Add the helper function (outside the class, at module scope in `main.js`):

```javascript
function _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
```

Call `initPushNotifications()` in `onWsOpen` after the existing code:

```javascript
    onWsOpen() {
        if (this.user.playerId) {
            this.ws.send({ action: 'reauthenticate', playerId: this.user.playerId });
        }
        this.updateStatusBanner('Connected. Waiting for state...');
        initChat(this.ws);
        this.initPushNotifications();
    }
```

- [ ] **Step 4: Commit**

```bash
git add templates/index.html routes/draft_routes.py static/js/main.js
git commit -m "feat: register service worker and subscribe to push notifications on draft room load"
```

---

## Task 9: Wire push notification trigger after each pick

**Files:**
- Modify: `routes/draft_routes.py`

The `_broadcast_pick_messages` helper from Task 3 is the right place to trigger push. Update it to fire the push notification for the next player as a background task.

- [ ] **Step 1: Update `_broadcast_pick_messages` to trigger push**

In `routes/draft_routes.py`, add import at top:

```python
import asyncio
```

Find the `_broadcast_pick_messages` helper added in Task 3. After the on-the-clock message broadcast, add a fire-and-forget push:

```python
async def _broadcast_pick_messages(manager, old_state: dict, new_state: dict, team: str) -> None:
    """Post pick + on-the-clock system messages and broadcast them."""
    season = old_state["season"]
    active_pick = old_state["active_pick"]
    picker = next(
        (p for p in old_state.get("all_players", [])
         if p["playerId"] == next(
             (x["playerId"] for x in old_state["draft_board"] if x["pick"] == active_pick), None
         )),
        None,
    )
    picker_name = picker["playerName"] if picker else "Unknown"
    pick_msg = post_system_message(season, f"🏈 {picker_name} picks {team} — Pick #{active_pick}")
    if pick_msg:
        await manager.broadcast({
            "type": "chat_message",
            "msgType": "system",
            "playerName": "System",
            "text": pick_msg["text"],
            "timestamp": pick_msg["timestamp"],
        })
    new_active = new_state.get("active_pick", active_pick + 1)
    if new_active <= 30:
        next_entry = next(
            (x for x in new_state["draft_board"] if x["pick"] == new_active), None
        )
        next_pid = next_entry["playerId"] if next_entry else None
        next_player = next(
            (p for p in new_state.get("all_players", []) if p["playerId"] == next_pid),
            None,
        )
        if next_player:
            clock_msg = post_system_message(
                season, f"⏰ {next_player['playerName']}, you're on the clock! Pick #{new_active}"
            )
            if clock_msg:
                await manager.broadcast({
                    "type": "chat_message",
                    "msgType": "system",
                    "playerName": "System",
                    "text": clock_msg["text"],
                    "timestamp": clock_msg["timestamp"],
                })
            # Fire-and-forget push notification — never block the WebSocket flow
            if next_pid is not None:
                asyncio.get_event_loop().run_in_executor(
                    None,
                    _send_push_sync,
                    next_pid,
                    "⏰ You're on the clock!",
                    f"Pick #{new_active} — open WinsPool to make your pick.",
                )


def _send_push_sync(player_id: int, title: str, body: str) -> None:
    """Synchronous wrapper for push send — runs in thread pool executor."""
    try:
        from services.push_service import send_push_notification
        send_push_notification(player_id, title, body)
    except Exception:
        pass
```

- [ ] **Step 2: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```
Expected: all passing.

- [ ] **Step 3: Commit + push**

```bash
git add routes/draft_routes.py
git commit -m "feat: send push notification to next player after each pick (fire-and-forget)"
git push origin main
```

---

## Task 10: End-to-end validation

No code changes — manual validation only.

- [ ] **Step 1: Chat smoke test**

Start server with Firestore access (`USE_LOCAL_DATA=False`). Open draft room in two browser tabs. In one tab, type a message and send. Confirm both tabs receive the message in the chat feed immediately.

- [ ] **Step 2: System message smoke test**

Simulate a pick (or use admin force_pick). Confirm:
- "🏈 {player} picks {team} — Pick #N" appears in chat feed in both tabs
- "⏰ {next_player}, you're on the clock! Pick #N+1" appears immediately after

- [ ] **Step 3: Teams command test**

Type `/teams` in the chat input. Confirm a system message listing all available teams appears.

- [ ] **Step 4: Push subscription test (if VAPID keys configured)**

Open the draft room in a browser. Accept the notification permission prompt. Confirm `/api/draft/push-subscribe` returns 200. Trigger a pick. Confirm the next player receives a browser push notification.

- [ ] **Step 5: Deploy**

```bash
git push origin main
# Then run the deploy skill
```
