# Recap Page, Publish Flow, and Weekly Push Notifications Design

**Date:** 2026-10-06
**Status:** Draft, awaiting owner review. Design approved in conversation; this
spec is the written form.
**Related:** `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`
section 3 (push), `docs/superpowers/specs/2026-08-21-weekly-recap-automation-followup.md`
(recap automation), `docs/superpowers/specs/2026-10-06-sprint-followups.md`.

## Intent and Success Criteria

The owner writes each weekly recap by copying the admin prompt into an outside AI
chat, then emails it by hand. The app only shows a recap saved through the admin
"Save and Broadcast" button, so that workflow never reaches the app.

Goal: players can read every week's recap in the app and get a push when a recap
or their weekly standing is ready, without changing how the owner writes recaps.

Success means:
- The owner pastes a finished recap into the admin panel, clicks Publish, and it
  appears at `/recap/{year}/{week}` and on the standings card.
- Players who opted in get a "recap ready" push that opens that page.
- Players who opted in get one personal standings push per week after the last
  game is final.
- Players choose their notifications on their own page; a dismissible nudge on
  the standings page invites those who have not subscribed.
- No Gemini call and no email happens unless the owner ticks it.

Decisions made (owner answers): paste-and-publish flow; weekly personal standings
push; player-page opt-in card plus a one-time nudge. Out of scope: close-game
alerts, per-game or daily rank pushes, a full preferences center, automating the
recap itself.

## Global Constraints

- No emojis in code, comments, commits, docs.
- Firestore is the source of truth; local pkl is a mirror via
  `scripts/refresh_local_pkls.py`. Any new collection or field is added to that
  script, and writes under `.local_db` use `services/local_paths.py::local_db_dir()`.
- Services and routes never read `rawdata/` CSVs.
- Any script writing to Firestore forces `USE_LOCAL_DATA=False` via `require_db()`.
- Nav entries are added to both `static/js/main.js` (`_updateMoreDropdown`) and
  the `templates/base.html` drawer; `tests_e2e/test_nav_parity.py` must pass.
- New CSS uses theme tokens (`tests/test_theme*.py`).
- A generic try/except returning `server_error()` is not added to routes; the
  global handler covers unhandled errors. Domain-specific handling stays.
- Push sends are best-effort: a failure logs and never fails the caller.

## Components

### 1. Recap storage, listing, and read cost

New collection `season_recaps`, one doc per season (doc id `{year}`):

```
season_recaps/2026 = {
  year: 2026,
  weeks: {
    "5": {summary: "<recap text as written>", timestamp: <epoch>, source: "published"},
    "6": {...}
  },
  updated_at: <epoch>
}
```

Why: one read returns the week picker data and every recap body for the season
(a season is at most about 22 recaps of a few KB, far below Firestore's 1 MiB doc
limit), and publishing sets one field path (`weeks.<n>`) without rewriting other
weeks and without a read-modify-write. Week keys are strings (Firestore map keys).

Access layer in `db_service` (public signatures kept stable for existing callers):
- `get_season_recaps(year) -> {week:int -> {summary, timestamp, source}}` (one doc
  read; empty dict if none).
- `get_weekly_recap(year, week)` keeps its signature and return shape
  (`{year, week, summary, timestamp}` or `None`) and is implemented on top of
  `get_season_recaps`.
- `list_recap_weeks(year)` returns `sorted(weeks)` from the same read.
- `save_weekly_recap(year, week, summary, source=None)` keeps its signature (the
  existing Save and Broadcast flow keeps working) and now writes
  `weeks.<week>` on the season doc with `set(..., merge=True)` semantics via a
  nested map merge, creating the doc if absent.
- All three read through one in-process cached getter (5 minute TTL via
  `cache_service`), including the standings page's latest-recap read, which today
  is an uncached read per page view. Any save clears the cache and signals other
  instances with the existing `_invalidate_static()`-style clear-plus-
  `signal_data_update` pattern (`tests/test_db_cache_signals.py` pins it).
- Season page switching uses `year_picker`; no cross-season scan ever runs.

Local mirror: JSON, one file per season, `.local_db/season_recaps_{year}.json`
(the same convention as `game_predictions_{year}.json`), written through
`local_db_dir()` and rebuilt by `scripts/refresh_local_pkls.py` (add
`season_recaps`). The Firestore and local paths return identical shapes.

Migration: the legacy `weekly_recaps/{year}_{week}` docs remain readable.
- Reads: `get_season_recaps` falls back to assembling a season from legacy docs
  only when no `season_recaps/{year}` doc exists (cached, so at most about 22 reads
  once per TTL).
- `scripts/migrate_weekly_recaps.py` (dry run by default, `--firestore` to write,
  forces `USE_LOCAL_DATA=False` via `require_db()`) folds every legacy doc into its
  season doc, idempotently, and never deletes the legacy docs. Run it once at
  deploy time; after it runs the fallback is never used. Removal of the legacy
  collection is a separate, later, owner-approved step.

### 2. Recap pages

- `GET /recap` redirects to the latest available recap (404-style empty state page
  if none exist).
- `GET /recap/{year}/{week}` renders `templates/recap.html` (extends `base.html`):
  week picker (all weeks that have a recap, via a select or prev/next), the
  rendered recap body, and the existing `.recap-card-glass` visual style.
- Auth follows the standings pages. Unknown year/week returns a friendly empty state,
  not a 500.
- Rendering: a single helper `services/recap_render.py::render_recap_html(text)` (a
  Jinja filter `recap_html`) converts text to HTML (paragraph and line breaks, plus
  `**bold**`, `*italic*`, simple lists and headings if present) and sanitizes it to
  an allowlist (`p, br, strong, em, ul, ol, li, h3, h4, a[href http(s)/relative,
  rel=noopener]`). It is used by BOTH the recap page and the standings card. Today
  the standings card renders `{{ recap | safe }}` on raw stored text, a latent
  injection gap and a loss of line breaks; this replaces it. Implementation
  (dependency vs minimal in-house) is decided in the plan; pasted raw HTML is
  escaped, not honored.
- Nav: add "Recaps" to `_updateMoreDropdown`'s `moreLinks` and to the drawer in
  `base.html`; standings recap card gets a "Read full recap" link to the page.

### 3. Admin publish

- `POST /api/admin/recap/publish` (admin) body `{year, week, text, send_push: bool,
  send_email: bool}` (year and week default like the preview route:
  `get_active_season` and `get_most_recent_completed_week`).
- Body handling: `summary` stays the recap text exactly as written (plain text, or
  light markdown), the same contract as existing recaps (the Gemini prompt forbids
  markdown and `build_recap_html` escapes the text, so the email path and the stored
  value are plain text). It is NOT converted at save time. HTML is produced only at
  render time (see section 2), so the email path is unaffected. Empty or
  whitespace-only text returns 422.
- Overwrites an existing recap for that week (idempotent re-publish). Re-publish
  sends a push only when `send_push` is true again.
- `send_email` reuses `email_service.send_weekly_recap_email` with the existing
  enrolled-player recipient logic from `save_and_broadcast_recap`; default false.
- UI: a "Publish recap" box in the AI Recaps tab (`#recap-section`) under the
  prompt preview: textarea, week/year inputs (shared with the tab's), two
  checkboxes, a Publish button, and a success line linking to the recap page. Human
  authority stays: nothing is sent without the owner clicking Publish.
- Existing Generate and Save and Broadcast flows are unchanged.

### 4. Notification preferences and opt-in

- Player record gains `push_prefs: {recap: bool, standings: bool}` (absent means
  both true once subscribed, so existing subscribers keep working and get the new
  pushes; the draft-turn alert is unaffected and has no toggle).
- `POST /api/profile/push-prefs` (auth) saves the caller's prefs; the player page
  (`templates/player_profile.html`, own-page-only block) gets a Notifications card:
  Enable button, status (not supported / blocked / on), and two toggles.
- The Enable button runs the subscribe flow already in
  `main.js::initPushNotifications` (register `/sw.js`, request permission, POST to
  `/api/draft/push-subscribe`). Refactor that flow into a reusable
  `enablePushNotifications()` so the draft page and the card share one code path.
  The iOS install banner logic (`ios_push_hint.js`) applies: on iOS Safari tabs the
  card shows the Add to Home Screen guidance instead of the Enable button.
- Nudge: a dismissible banner on the standings page for logged-in players with no
  subscription, supported browser (or iOS needing install) and not dismissed
  (localStorage, try/catch). Links to the player page card. Never prompts
  permission by itself.
- `GET /api/profile/push-status` (auth) returns `{subscribed, prefs}` for the card
  and the nudge (never another player's data).

### 5. Recap-ready push

On publish with `send_push` true: send to every player whose subscription exists
and `push_prefs.recap` is not false, using `push_service` (add
`broadcast_push_notification(title, body, *, pref=None, url=None)`; existing
callers keep working). Title "Week N recap is ready"; body is the first sentence of
the recap, truncated; payload carries `url: "/recap/{year}/{week}"`. The publish
response reports `{sent, failed, pruned}`. The same event record format is written
to `push_events/{year}_w{week}_recap` (title, body, counts, no per-player text
needed since the message is identical for everyone).

### 5b. Admin review of sent pushes

`GET /api/admin/push-events?season=` (admin, read-only) lists `push_events` docs
newest first (`id`, `kind`, `week`, `sent_at`, `counts`) and
`GET /api/admin/push-events/{id}` returns one with its `messages`. A small read-only
"Sent notifications" list in the admin AI Recaps tab links to them. A
`--dry-run` run of `scripts/send_weekly_standings_push.py` prints the same messages
without sending or writing the event doc, for template review before the first real
send.

### 6. Service worker: deep link

`static/sw.js` `notificationclick` currently always opens `/`. Change `push` to
keep `data.url` in `notification.data`, and `notificationclick` to focus an open
window and navigate to that URL, else `openWindow(url || '/')`. Only same-origin
relative URLs are honored. Bump any SW cache or version identifier if one exists.

### 7. Weekly personal standings push

- Runs as a new step in `scripts/run_cron.py` (winspool-sync-daily, 9:00 UTC)
  after `daily_nfl_sync.py`, via a new `scripts/send_weekly_standings_push.py`. The
  step is non-required: a failure alerts per existing `job_runner` rules but never
  blocks the data sync.
- Trigger rule: the latest regular-season week in `nfl_games` where every game is
  final and no marker exists. The marker is the event record
  `push_events/{season}_w{week}_standings`, written after sending (only when sends
  were attempted, to make reruns idempotent). It is also the review record: it
  stores `sent_at`, `counts {total, sent, failed, pruned}` and a `messages` map of
  `player_id -> {title, body, status}` with the exact text sent to each player (one
  doc, small: tens of players), so the owner can review the first send and tune the
  template. Nothing else retains sent notifications; browsers and push services keep
  no queryable history. Week 1 has no prior rank, so its message omits the rank change
  and the wins-gained delta (rank and total wins only).
- Content per player: rank now vs rank after the previous week, total wins, wins
  gained this week, leader's name. Example: "You moved up to 2nd (14 wins, +2).
  Leader: Sam." Ties use the same tiebreak as the standings page. Reuse
  `analysis_service.calculate_wins_pool_standings` for both snapshots (current and
  as-of the prior week); do not reimplement rankings.
- Recipients: players with a subscription and `push_prefs.standings` not false;
  link `/wins-pool/{season}`.
- Gated off while `draft_active` is set or the draft is incomplete.
- Local runs under `USE_LOCAL_DATA` do nothing (push needs Firestore); the script
  supports `--dry-run` printing messages without sending, and `--force-week`.

### 8. Deploy and infrastructure

- `winspool-sync-daily` needs `VAPID_PUBLIC_KEY`, `VAPID_CLAIMS_EMAIL` env vars and
  `VAPID_PRIVATE_KEY` from the `vapid-private-key` secret, with the job's service
  account granted `secretmanager.secretAccessor`. `deploy.ps1` only swaps images
  for jobs, so this is a one-time `gcloud run jobs update` documented in
  `DEPLOY.md` (and CLAUDE.md Scheduled Jobs).
- `pywebpush` is already in `requirements.txt` (the sync image installs it).
- Fail open: if VAPID is missing in the job, the step logs a warning and exits 0.

## Existing Code Touchpoints (weekly recap)

Everything that reads or writes recaps today, and what this work does to it:

| Touchpoint | Today | Change |
|---|---|---|
| `db_service.save_weekly_recap` / `get_weekly_recap` (`services/db_service.py` ~576-625) | per-week doc `weekly_recaps/{y}_{w}`; offline path reads `.local_db/weekly_recaps.pkl` | Rewritten onto `season_recaps/{year}`; same signatures and return shapes. Offline path reads `season_recaps_{year}.json`. |
| `routes/standings_routes.py:100` | uncached read per page view; template renders `recap \| safe` | Uses the cached getter and the `recap_html` filter. Tests that patch `db.get_weekly_recap` keep working (`test_standings_routes.py`, `test_live_standings_route.py`, `test_standings_player_links.py`, `test_wins_pool_missing_standings.py`). |
| `routes/admin_routes.py::save_and_broadcast_recap` (~365) | saves then emails; builds its own email HTML inline | Unchanged behavior. The inline email HTML duplicates `scripts/generate_weekly_summary.py::build_recap_html`; the new Publish route's optional email reuses one shared builder rather than a third copy (extract to `services/email_service.py`, keep both callers working). |
| `scripts/generate_weekly_summary.py` (CLI, ~line 83) | second writer: `save_weekly_recap` after an interactive confirm | Works unchanged through the same function. It runs in another process, so cache freshness relies on the cross-instance signal plus the 5 minute TTL. |
| `services/recap_service.py` | imports `save_weekly_recap` (line 11) | Verify it is still used; keep the import working. |
| `scripts/refresh_local_pkls.py:43` | mirrors `weekly_recaps` as a pkl keyed by `year` | Add `season_recaps` (JSON per season). Keep the `weekly_recaps` entry while the legacy fallback exists. |
| `services/cache_service.py` ~468-480 | deliberately gives recaps no in-memory cache domain (low-frequency reader) | Reconcile: the 5 minute TTL is a read-through cache local to the recap getters, not a new cache domain; update that comment. |
| `db_service.delete_season_data` | clears draft_order, draft_order_rules, draft_results only | No change; recaps are intentionally kept when a season's draft data is reset. |
| `docs/database.md` (`weekly_recaps` section), `docs/reference_manual.md` (~81-82) | document the old shape | Document `season_recaps`, mark `weekly_recaps` legacy. |
| Admin AI Recaps tab (`admin.html`, `admin_main.js`) | preview, Generate, Save and Broadcast, Copy (this sprint) | Add Publish box and Sent notifications list; existing buttons untouched. |
| Email footer text | says "generated by Gemini AI" | A published, human-edited recap should not claim that; the shared builder takes an optional footer line (default unchanged for the Gemini flows). |

## Data Model Additions

| Where | Field | Notes |
|---|---|---|
| `players/{id}` | `push_prefs {recap, standings}` | optional; absent means both on |
| `season_recaps/{year}` | `weeks.<n>.{summary, timestamp, source}`, `updated_at` | new; replaces per-week docs; legacy `weekly_recaps` kept read-only as fallback |
| `push_events/{season}_w{week}_standings` and `{year}_w{week}_recap` | `kind`, `sent_at`, `counts`, `messages` (standings only) | new collection; written by Firestore only; add to `refresh_local_pkls.py` only if a local reader is needed (admin list reads Firestore directly) |

## Error Handling

- Publish: validation errors 422; unexpected errors fall to the global handler; a
  push or email failure after a successful save returns 200 with `push.failed`
  counts so the recap is never lost.
- Standings push: per-player failures are counted and logged, never stop the loop;
  dead subscriptions are pruned by `push_service` as today.
- Pages: missing recap shows an empty state; sanitizer output is the only HTML
  injected with `|safe`.

## Testing

- Unit: season recap save/get/list (Firestore mock and local JSON), saving week 6
  leaves week 5 intact, legacy fallback assembly, migration script idempotency and
  dry run, cache hit and invalidation on save, publish route (defaults,
  empty text 422, sanitizer strips `<script>` and `onclick`, overwrite, push/email
  flags), prefs routes, standings-push message builder (rank up/down/same, first
  week, ties, no previous week), idempotency marker, draft-active gate, the
  trigger-week rule with a mixed complete/incomplete schedule.
- Event record: standings push stores per-player message text and counts; rerun
  does not resend; `--dry-run` writes nothing; admin list/detail routes are
  admin-only. Recap read cache: second read within TTL does not hit the DB, publish
  invalidates.
- Template: recap page renders, nav link present in both More menu markup source and
  drawer; standings card link.
- Node: sw.js deep-link handling (same-origin only), shared `enablePushNotifications`
  status logic.
- e2e (when env available): admin publishes a recap, `/recap/{y}/{w}` shows it;
  nav parity still passes.
- All writes to `.local_db` go through `local_db_dir()`.

## Review Focus

- Recap text containing raw HTML, scripts, or javascript: links must be neutralized.
- Publishing the same week twice must not duplicate pushes unless asked.
- Re-running the daily sync after a week's standings push must not send again.
- A player with no subscription, a dead subscription, or prefs set to false must
  not break or receive the send.
- A week with a postponed or unfinished game must not trigger the weekly push.
- Existing plain-text recaps (line breaks, `<`, `&`) must render correctly and safely
  on both the standings card and the recap page after the filter change.
- The existing admin Save and Broadcast flow and the CLI script must keep writing
  recaps that appear on the new page.
- The notification click must open the intended page, including when the app is
  already open, and must reject off-origin URLs.

## Open Items for the Plan

- Pick the markdown and sanitizer implementation (dependency vs minimal in-house).
- Add `season_recaps` to `refresh_local_pkls.py` and decide the legacy-collection
  removal date (not part of this work).
- Confirm the exact as-of-previous-week call shape for
  `calculate_wins_pool_standings`.
- Confirm sync job service account and secret IAM binding at deploy time.
