# Session Lookup Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop authenticated requests from re-reading the entire `players` collection whenever the static data cache is cold (which every login triggers), while keeping session revocation (`token_version`) semantics. Also correct a stale doc value.

**Architecture:** In `services/session_service.py` the session-currency check gets its own tiny per-player cache: `{player_id: (token_version, exists, fetched_at)}` with a 30 second TTL, fed by a single-document read (`players/{id}`) on a miss instead of the full-frame `_get_players_df()` fetch. The in-process password-change path writes through (drops the entry) so a revocation takes effect immediately in the process that made the change; other instances see it within the TTL. Local mode (`USE_LOCAL_DATA=True`) keeps reading the local frame.

**Tech Stack:** Python, FastAPI, google-cloud-firestore, pytest.

## Global Constraints

- NO emojis. Zero deletion of existing tests/features (exact replacements only, explain any). TDD: failing test first. Never deploy, never write Firestore, never `git add` `.superpowers/`, `.local_db/`, `*.png`. Commit trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Docs need `git add -f`.
- Work only in `C:\Users\fisch\OneDrive\Documents\Code\WinsPool\.claude\worktrees\session-lookup` (branch `sprint-session-lookup`). Run targeted tests plus one final full suite (`python -m pytest tests/ -n auto -q -p no:cacheprovider --color=no`); compare failing test ids with the 34 known environment-caused ids in the SDD workspace `baseline_failure_ids.txt` (strip FAILED/ERROR prefixes; note one known load flake `tests/test_live_standings_route.py::test_standings_page_exposes_refresh_hooks` passes alone).
- Preserve the current contract exactly: real revocation (row missing, or `tv` mismatch) -> 401 with `X-Session-State: revoked`; infrastructure failure to read -> `SessionCheckUnavailable` -> 503 without a session-state header; `get_is_admin` and `decode_current_token` never raise (fail closed / anonymous); legacy tokens without `tv` work while the stored version is 0; type coercion via the existing `_as_version` (including OverflowError).
- Do not make the machine crawl: the user's computer is under load; do not start servers or browsers; run pytest without extra parallelism beyond `-n auto` on the final run only.

## Review Focus

- A password change (`update_player_profile(..., bump_token_version=True)`) must invalidate that player's cache entry in the same process immediately (no 30 second window where the old token is honored in the process that performed the change); a fresh token minted right after must validate.
- Stale cache must not resurrect a revoked token: entries are only ever REPLACED by fresher reads or dropped, never re-stamped as newer than they are; a negative result (player deleted) is cached for the TTL at most and never longer.
- Multi-instance staleness is bounded by the TTL (30 s) rather than the static cache's old unbounded-until-signal behavior; document it (CLAUDE.md Auth section currently describes a "60 seconds after a request that calls load_data()" lag; correct it to the new bounded behavior).
- Thread safety: FastAPI runs sync endpoints in a threadpool; use a lock or atomic dict operations; a thundering herd of concurrent misses may issue duplicate single-doc reads but must not corrupt state.
- One document read per miss (assert `db.collection("players").document(str(id)).get` is used and the collection is not streamed) and no read at all on a hit.
- Remote mode with `get_db()` None, a Firestore exception, or a malformed document -> `SessionCheckUnavailable` (503), never a false revocation.

---

## Task 1: Per-player session cache with single-document reads

**Files:** Modify `services/session_service.py`, `services/db_service.py` (invalidation hook in the token-version bump path of `update_player_profile`), `tests/test_session_revocation.py` and a new `tests/test_session_lookup_cache.py`, `CLAUDE.md`.

- [ ] Tests first (`tests/test_session_lookup_cache.py`, mocking the Firestore client, no real network): hit avoids DB; miss does one `.document(id).get()`; TTL expiry refetches (monkeypatch the clock/time function); bump write-through drops the entry so the very next check in-process sees the new version (old token 401 revoked immediately, new token valid); a deleted player (snapshot `exists` False) -> revoked and cached negatively for at most the TTL; exception -> 503-path (`SessionCheckUnavailable`) and NOT cached; remote mode `get_db()` None -> unavailable; local mode reads the local frame as before; legacy `tv` missing works; concurrent access from threads does not raise; the existing revocation tests still pass (adjust only patches, keep assertions).
- [ ] Implement per Architecture and Review Focus; keep the `_lookup_player` / `_load_player_from_db` seams that tests patch, or replace them with equivalent seams and update the conftest autouse stub accordingly (equivalent behavior).
- [ ] Docs: update the CLAUDE.md Auth text about token_version lag to the new bounded behavior; also fix the stale scheduled-jobs wording in CLAUDE.md that says the resimulation fires at "kickoff-20min": the code (`scripts/schedule_kickoffs.py::RESIMULATE_LEAD_MINUTES`) is 30 minutes; verify the constant and correct every place in CLAUDE.md that says 20.
- [ ] Run targeted tests (`tests/test_session_lookup_cache.py tests/test_session_revocation.py tests/test_session_service.py tests/test_auth.py tests/test_auth_guard.py tests/test_admin_routes.py`), then the full suite once with the id comparison. Commit.
