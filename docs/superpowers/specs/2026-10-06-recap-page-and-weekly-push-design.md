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

Keep the `weekly_recaps` collection with one doc per recap (doc `{year}_{week}`:
`year`, `week`, `summary`, `timestamp`). A single recap fetch is one document
read, so no re-modeling is needed. Cost and read rules:
- `db_service.list_weekly_recaps(year)` is scoped to one season (a `where year ==`
  query, at most about 22 docs) and projects only `year`, `week`, `timestamp` so
  recap bodies are not transferred for the week picker. The page's season selector
  (`year_picker`) switches seasons; no cross-season scan ever runs.
- `get_weekly_recap` and `list_weekly_recaps` results are cached in-process with a
  short TTL (5 minutes) through `cache_service`. The standings page's per-view
  recap read (today an uncached read on every page view) uses the same cached
  getter. Publishing clears the cache and signals other instances using the
  existing `_invalidate_static()`-style clear-plus-`signal_data_update` pattern
  (`tests/test_db_cache_signals.py` pins that pattern).
- If measured reads ever matter, a per-season index doc is the next step; not
  needed now (YAGNI).
- Firestore and local-pkl paths keep identical formats; verify `weekly_recaps` is
  mirrored in `scripts/refresh_local_pkls.py`, add it if not.
- `save_weekly_recap` gains an optional `source` value (`"published"` vs
  `"gemini"`), additive and nullable for existing docs.

### 2. Recap pages

- `GET /recap` redirects to the latest available recap (404-style empty state page
  if none exist).
- `GET /recap/{year}/{week}` renders `templates/recap.html` (extends `base.html`):
  week picker (all weeks that have a recap, via a select or prev/next), the
  rendered recap body, and the existing `.recap-card-glass` visual style.
- Auth follows the standings pages. Unknown year/week returns a friendly empty state,
  not a 500.
- Nav: add "Recaps" to `_updateMoreDropdown`'s `moreLinks` and to the drawer in
  `base.html`; standings recap card gets a "Read full recap" link to the page.

### 3. Admin publish

- `POST /api/admin/recap/publish` (admin) body `{year, week, text, send_push: bool,
  send_email: bool}` (year and week default like the preview route:
  `get_active_season` and `get_most_recent_completed_week`).
- Body handling: `text` is plain text or markdown. It is converted to HTML and
  sanitized to an allowlist (paragraphs, headings, bold, italic, lists, links with
  `rel="noopener"`, line breaks) before storage, because the page renders it for
  every player. Choose and add one small dependency pair (markdown converter plus
  sanitizer) in `requirements.txt`, or implement a minimal converter; decided in
  the plan. Empty or whitespace-only text returns 422.
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

## Data Model Additions

| Where | Field | Notes |
|---|---|---|
| `players/{id}` | `push_prefs {recap, standings}` | optional; absent means both on |
| `weekly_recaps/{y}_{w}` | `source` | optional, additive |
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

- Unit: recap list/save/get (Firestore mock and local), publish route (defaults,
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
- The notification click must open the intended page, including when the app is
  already open, and must reject off-origin URLs.

## Open Items for the Plan

- Pick the markdown and sanitizer implementation (dependency vs minimal in-house).
- Confirm `weekly_recaps` mirroring in `refresh_local_pkls.py`.
- Confirm the exact as-of-previous-week call shape for
  `calculate_wins_pool_standings`.
- Confirm sync job service account and secret IAM binding at deploy time.
