# Testing Quick Win — Issue #39

**Date:** 2026-05-27
**Status:** Approved
**Closes:** GitHub issue #39
**Scope:** `tests/test_draft_websocket.py` (new), `tests/test_set_member_paid.py` (new)

## Overview

Add test coverage for two untested code paths:
1. **WebSocket draft flow** — `routes/draft_routes.py` WebSocket endpoint and `ConnectionManager`
2. **Payment tracking** — `services/db_service.py::set_member_paid()` and `routes/admin_routes.py::update_member_paid`

The existing `tests/test_draft_auth.py` covers admin security invariants; this spec covers the message flow and data mutation paths.

---

## Component 1 — WebSocket tests (`tests/test_draft_websocket.py`)

Use FastAPI's `TestClient` with `websocket_connect` context manager. Import `app` from `main.py`.

### Setup

The WebSocket endpoint requires:
- A valid room code (from `ROOM_CODE` env var). Set `os.environ["ROOM_CODE"] = "test"` at module level in the test file (before `app` is imported) so the endpoint reads the test value.
- A valid `player_id` established via the `"verify_code"` handshake before admin actions

Use `monkeypatch` to control the draft state: mock `draft_service.get_draft_state()` and `draft_service.save_pick()` to avoid Firestore calls.

### Test cases

**`TestConnectionManager`**

- `test_connect_adds_to_active_connections` — After `connect()`, the WebSocket is in `manager.active_connections`
- `test_disconnect_removes_from_active_connections` — After `disconnect()`, the WebSocket is removed

**`TestWebSocketPickFlow`**

- `test_verify_code_sets_socket_player_id` — After sending `{"action": "verify_code", "code": ROOM_CODE, "playerId": 1}`, response contains no error
- `test_pick_accepted_and_broadcast` — After verify, sending a valid pick action results in a broadcast to connected clients
- `test_duplicate_pick_rejected` — Sending the same pick twice returns an error message on the second attempt

**`TestWebSocketAdminActions`**

- `test_undo_pick_requires_admin` — Non-admin socket_player_id attempting `"undo_pick"` receives an error
- `test_admin_can_undo_pick` — Admin player after verification can send `"undo_pick"` and receives success broadcast

### Key constraint

The server uses `socket_player_id` (server-assigned after `verify_code`), not a client-supplied value, for authorization. Tests must go through the `verify_code` handshake to establish identity — never pass `playerId` directly to admin action messages and expect it to be honored.

---

## Component 2 — Payment tests

### `tests/test_set_member_paid.py` — unit tests for `set_member_paid()`

Mock both `get_collection_df` (returns a controlled DataFrame) and `get_db` (returns a mock Firestore client). Do not hit real Firestore.

**`TestSetMemberPaid`**

- `test_happy_path_sets_paid_true` — Given a draft_order row for (season=2024, player_id=1), calling `set_member_paid(2024, 1, True)` returns `True` and calls `db.collection("draft_order").document(...).update({"paid": True})`
- `test_happy_path_sets_paid_false` — Same but `paid=False`
- `test_returns_false_when_player_not_in_draft_order` — Given a DataFrame that has no row matching (season, player_id), returns `False` without calling Firestore
- `test_returns_false_when_draft_order_empty` — Given an empty DataFrame, returns `False`
- `test_local_pkl_updated` — Verifies `_save_df_to_local` is called with updated DataFrame

### `tests/test_admin_routes.py` additions — endpoint tests for `update_member_paid`

Add to the existing `test_admin_routes.py` (do not create a new file):

- `test_update_member_paid_success` — POST to `/admin/members/paid` with valid admin token and `{season: 2024, targetPlayerId: 1, paid: true}` returns `{"ok": true}`
- `test_update_member_paid_requires_admin` — Same request with a non-admin token returns 401/403
- `test_update_member_paid_missing_player_returns_ok_false` — When `set_member_paid` returns `False` (player not found), endpoint returns `{"ok": false}`

---

## What this does NOT include

- Tests for the full multi-client broadcast scenario (requires async test runner, deferred)
- Tests for WebSocket reconnection behavior
- Tests for `ConnectionManager.broadcast()` with multiple simultaneous connections
- Changes to production code
