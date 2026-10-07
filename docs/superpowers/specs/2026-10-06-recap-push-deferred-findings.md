# Deferred Findings From the Recap Page and Weekly Push Work

**Date:** 2026-10-06
**Status:** Backlog. Findings the per-task and whole-branch reviews logged as minor and deferred
when `docs/superpowers/plans/2026-10-06-recap-page-and-weekly-push.md` shipped
(branch `feature/recap-page-weekly-push`). None blocked the merge. Each item names the
file and the smallest sensible fix; group them into one cleanup pass or pick individually.

Already fixed before merge (not listed below): adversarial-input timing flake, a failed
event-record write now retried and logged loudly in the standings push, "1 win" grammar,
non-dict `push_prefs` guard, `//host` and `/\host` relative-link bypass, quadratic regex
backtracking, email-failure isolation in publish, e2e sentinel file cleanup.

See also `2026-10-06-sprint-followups.md` (earlier sprint) for items that still apply:
real PWA icons, browser and e2e verification, CORS headers on global 500s.

## A. Verification never done (highest value)

1. **No browser check** of any new UI at desktop and about 390px: `/recap` pages and week
   chips, the Notifications card on `/player/{me}`, the standings nudge, the admin Publish
   box, Copy button and "Sent notifications" list, and the header brand link.
2. **e2e suites not run** (no `E2E_*` env vars): `tests_e2e/test_nav_parity.py`
   (new "Recaps" entry in both nav lists) and `tests_e2e/test_admin_members_and_recap.py`
   (publish test, Copy button).
3. **No real push or Firestore run**: nothing was sent to a real device; the Firestore merge
   write (`weeks.<n>`) was reasoned from the API, not run. Do a first manual publish of a
   test week with both boxes unticked, then a real publish to yourself only.
4. **Service-worker click** behavior (`navigate`/`focus`/`openWindow`) needs a check on a real
   iPhone (installed app) and Android Chrome.

## B. Weekly standings push

5. **Missed weeks are never pushed later** (`services/standings_push_service.py::latest_complete_week`
   picks the highest complete week): job down, or a postponed game finishing after the next
   week completes. Decide whether a catch-up is wanted; at minimum add a docstring note.
6. **All deliveries failing still records the event** (e.g. a bad VAPID private key): the
   event is written with `failed=N` and never retried. Consider not recording when
   `sent + pruned == 0` and `failed > 0`.
7. **Script can exit non-zero** (`scripts/send_weekly_standings_push.py`): errors in `_load()`
   or `push_event_exists`, and `require_db()`'s `sys.exit(1)` without credentials, escape the
   try block. The step is non-required so no alert fires and the sync is unaffected, but the
   docstring claims it never exits non-zero. Either wrap the whole flow or fix the docstring.
8. **`standings_as_of` mirrors `compute_standings`** and does not exclude
   `UNDRAFTED_SENTINEL` results, and reads `games["game_type"]` unguarded (unlike
   `week_is_complete`). Harmless today (`nfl_games` stores NaN, not -1000); align if either
   function changes.
9. **Non-dry-run on a dev machine uses Firestore** (`require_db()` forces
   `USE_LOCAL_DATA=False`), so a real run with credentials in `.env` sends real pushes; the
   spec said local runs do nothing. Add a docstring sentence or a `--yes-really` guard.
10. **Leader shown as "Sam L."**: uses `abbreviate_player_name`. Switch to
    `fullName.split()[0]` if the owner prefers first names only after reviewing the first real
    send.
11. **Ties get distinct ranks** (rank is row order after `apply_tiebreakers`, matching the
    standings page). Revisit only if "tied for 2nd" wording is wanted.

## C. Push service, events, and admin list

12. `push_service.send_to_subscribers`: the "broadcast complete" log line is written for
    every call including per-player sends (kept because an existing test asserts it);
    an unknown `_deliver` status now counts as failed without a log line; it streams the
    whole `players` collection per send (fine at pool size).
13. `push_events.record_push_event`: `extra` can silently overwrite `kind`, `sent_at` or
    `counts` (guard with a reserved-key check). `get_push_event` / `push_event_exists` do not
    catch Firestore read errors (only the writer does): make consistent.
14. Publish response: `push` does not say whether the event record was written; add
    `"recorded": bool`. `email.recipients` counts addresses attempted, not delivered.
15. `routes/admin_routes.py::_first_sentence`: the markup-stripping regex also removes
    underscores inside words ("snake_case" becomes "snakecase") and splits on "e.g. ".
16. Admin UI: `loadPushEvents` reads `events.length` outside its try block;
    `#recap-year` has `min=2024` while the e2e test uses 2000 (browser marks it invalid).
17. Tests: the traversal test accepts 404 or 422 (loose); no test that
    `extract_weekly_data` / `send_weekly_recap_email` are NOT called when `send_email` is
    false, or for an empty recipient list with `send_email` true; unused import `FF` in a
    push_service test helper.

## D. Recap storage and rendering

18. `db_service.get_season_recaps` returns a shallow copy (`dict(...)`): per-week entry dicts
    are shared with the cache, so a caller that mutates one corrupts other readers for up to
    5 minutes. Use `{k: dict(v) ...}`. No current caller mutates.
19. `_normalize_recap_weeks` raises on a non-dict `weeks` field (a 500 on the standings page);
    guard with `isinstance(raw, dict)`. Read errors from `get_db()` / `doc.get()` propagate
    (pre-existing behavior): consider returning `{}` and logging so an optional recap never
    500s a page.
20. Re-saving a week without `source` leaves an earlier `source` in place (Firestore merge is
    per leaf field). Write `source` always (None included) or replace the whole week map, if
    `source` ever drives behavior.
21. Local mode: a `season_recaps_{y}.json` file hides legacy pkl-only weeks for that year
    (run `refresh_local_pkls.py` after migrating); the local JSON write is not atomic and a
    JSON decode error is cached as empty for 5 minutes (write temp then `os.replace`).
22. Migration script: the report lists all legacy weeks, not the weeks written, prints
    "written" even if all were skipped, and does not set `updated_at`; the legacy query uses
    `int(year)` only. (Your production dry run found no legacy docs, so this is unused today.)
23. Renderer: emphasis markers inside a URL rewrite the `href` text (`*bold*` in a query
    string); `***a***` mis-nests; a heading followed by a bullet in one block is not
    detected; the quote-escape test's first assertion is weak. Stored recaps that already
    contain HTML tags now show as escaped text (accepted by spec).
24. Cross-instance freshness is TTL-only (5 minutes) by ruling; the spec's cross-instance
    signal was not implemented. Revisit only if the web service ever runs more than one
    instance.
25. `routes/recap_routes.py`: `_active_season()` calls `load_data()` unguarded (like the other
    routes); negative year/week are accepted (they show the empty state); no tests for
    non-numeric (422) or week 0.

## E. Notifications card and nudge

26. `static/js/push_card.js`: "subscribed" is checked before this browser's permission, so a
    subscription from another device (or permission revoked here) shows "on" with no Enable
    button; only one `push_subscription` is stored per player, so enabling on a second device
    replaces the first. If multi-device matters, store a list of subscriptions.
27. No in-flight protection on Enable or the toggles (double click; a toggle changed during a
    save can revert wrongly); Enable is not wired after an initial status-fetch failure (no
    retry without a reload).
28. Prefs cannot be saved in local-data mode (500, matching `push_subscribe`), so the card
    reverts toggles in local dev.
29. `main.js` has an unused `getAuthHeaders` import; no test asserts that the draft page
    renders exactly one `vapid-public-key` meta (logic traced correct).
30. **Pre-existing, not introduced here:** `POST /api/draft/push-subscribe` trusts `playerId`
    from the request body instead of the JWT `sub`. Any logged-in user could overwrite another
    player's subscription. Derive the id from the authenticated caller (like the new
    `push-status` / `push-prefs` routes) and keep the body field optional for compatibility.
    This is the one item here with a security flavor; do it first.

## F. Small cleanups

31. Unused imports left from verbatim moves: `html_module` in `routes/admin_routes.py`,
    `html` in `scripts/generate_weekly_summary.py`.
32. `scripts/generate_weekly_summary.py` email footer whitespace differs slightly from the old
    inline template (renders the same).
33. `scripts/refresh_local_pkls.py` writes `season_recaps` via a module-level `LOCAL_DB` path
    rather than `local_db_dir()` (matches its sibling dump functions; tests monkeypatch it).
34. The admin Publish box has "Send push" ticked by default; correct now that the server
    sends, but worth a one-line hint in the UI that players who opted out are skipped.
35. Docs only on this machine: `docs/frontend.md`, `docs/architecture.md` and
    `docs/reference_manual.md` are untracked (`docs/` is gitignored) and were edited locally.
    Decide whether to track them or move their content into tracked docs.

## Suggested order

1. Item 30 (draft push-subscribe trusts body `playerId`), then A (verification) before the
   first real push.
2. B 6-7 and C 13-14 (robustness of the weekly push and its records).
3. D 18-19 (cache copy and malformed-doc guards), E 26-27 (card behavior).
4. Everything else as a single cleanup pass.
