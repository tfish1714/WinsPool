# Post-Launch Hardening (#N)

**Status:** Draft
**Date:** 2026-09-09

## Context

WinsPool is now live with real users in the app. This spec covers four
independent hardening workstreams identified as gaps now that the app has
real traffic and real players, rather than a single cohesive feature:

1. No automated UI/regression testing (`pytest` covers routes/services only).
2. No performance baseline since going live — backend, data-layer, or frontend.
3. Push notifications appear unreliable — an iPhone user reported never
   seeing one; on investigation, an Android user (project owner) doesn't
   recall seeing one either.
4. No cost tracking or budget alerting on the GCP project.

Each workstream gets its own section below and, eventually, its own
implementation plan (via `superpowers:writing-plans`) — they don't share
architecture and can be picked up independently and in any order.

---

## 1. UI Tests

### Problem
`pytest` (see `tests/`) covers routes and services only. CLAUDE.md already
documents a real incident class this creates: `static/js/main.js`'s
`updateNav()` renders the desktop nav client-side, but the mobile nav drawer
in `templates/base.html` is separate, hardcoded, server-rendered markup — a
nav link added to one has silently not appeared in the other before. There is
no automated guard against this or any other UI regression.

### Approach
Add `tests_e2e/` using Playwright's Python bindings (keeps everything in one
test runner ecosystem — no new JS toolchain, no `package.json`, consistent
with this being a Python-first repo).

Cover, at both a desktop width and the existing ~390px mobile breakpoint
CLAUDE.md calls out:
- Login flow (`/api/login`)
- Standings / leaderboard page
- Draft room: join, see state, make a pick (WebSocket-driven — needs a
  running draft fixture)
- Mock draft (`/mock-draft`) — login-free, good candidate for a fast smoke
  test
- Admin dashboard — smoke test only (loads, no 500)
- Nav parity check: assert the same set of destinations appears in both the
  desktop nav (`updateNav()`'s rendered output) and the mobile drawer
  (`.nav-drawer__links`), directly encoding the CLAUDE.md gotcha as a test
  instead of a comment.

### Integration
Wire as a pre-flight step in the existing `/deploy` flow
(`.claude/commands/deploy.md` → `deploy.ps1`), alongside `pytest` — not
CI-only, since this repo has no CI pipeline today and deploys go through the
`/deploy` slash command.

### Out of scope
Visual regression / screenshot diffing — flow correctness first; revisit if
CSS regressions keep slipping through.

---

## 2. Performance Check

### Problem
No performance baseline has been taken since the app went live and started
carrying real traffic. Cache TTLs, Firestore read patterns, and Cloud Run
sizing were all tuned pre-launch against assumptions, not measured usage.

### Approach — three layers

**Backend (Cloud Run):** Cloud Monitoring dashboard for the `winspool`
service tracking p50/p95 request latency and cold-start frequency.
Monitoring infrastructure for this project already exists (the scheduled
jobs' alert policy watching `run.googleapis.com/job/completed_execution_count`)
— this extends the same pattern to the request-serving service rather than
just the batch jobs.

**Data layer (Firestore):** Audit `data_service.py`'s 3-tier cache
(memory → pickle → Firestore) under real traffic. Specifically: Firestore
read counts per request on hot paths (standings, draft room state), and
whether current in-memory TTLs (`cache_service.py`) are sized correctly now
that there's real concurrent usage instead of solo dev testing.

**Frontend:** Lighthouse / Core Web Vitals (LCP, CLS, INP) on the standings
and draft room pages, at mobile viewport specifically since that's where
this app has had UX gaps before. `chrome-devtools-mcp`'s `lighthouse_audit`
tool covers this directly.

### Output
A baseline report with concrete numbers, plus recommended alert thresholds
(e.g. p95 latency, Firestore reads/request) — not a one-time audit that goes
stale. Where a threshold is breached, that becomes its own follow-up, not
fixed inline in this pass.

---

## 3. Alerts / Push Notifications

### Problem
Reported as "iPhone users don't see alerts," but investigation during
brainstorming found the underlying issue is broader:

- **The entire push send path silently swallows failures.** Three separate
  points suppress errors with no logging: the client's `catch` block in
  `initPushNotifications()` (`static/js/main.js:503-505`, only a
  `console.warn` no one will see), `_send_push_sync`'s bare
  `except: pass` (`routes/draft_routes.py:545-551`), and
  `send_push_notification`'s own `except Exception` (`services/push_service.py`,
  logged at `debug` level only). There is currently **zero observability**
  into whether a push notification ever actually lands, on any platform.
- **There is exactly one trigger today**: "you're on the clock" during a
  live draft (`draft_routes.py:534-542`). No other event sends a push. A
  player who isn't actively drafting at the moment it fires has nothing to
  recall seeing — which is consistent with an Android user (project owner)
  also not remembering seeing one, not just an iOS-specific bug.
- **iOS-specific gap**: this repo has no `manifest.json` / PWA install
  metadata. iOS Safari only exposes the Push API (`PushManager` in `window`)
  to sites added to the home screen — in a plain browser tab,
  `initPushNotifications()`'s guard at `main.js:477` is false and the
  function returns immediately, silently, with no user-facing indication
  that notifications aren't available yet.

### Approach

1. **Instrument, don't assume.** Replace the swallowed exceptions with real
   logging (success/failure counts) in `send_push_notification` and the
   subscribe flow, so the next investigation has data instead of guesses.
   This should ship first — everything else benefits from it.
2. **Fix the iOS gap**: add `manifest.json` + Apple meta tags
   (`apple-mobile-web-app-capable`, touch icons) for a proper "Add to Home
   Screen" experience; detect iOS non-standalone context client-side and
   show an in-app prompt explaining that install is required before push
   will work, instead of failing silently.
3. **Evaluate a device-agnostic fallback channel.** Web Push reliability
   varies by browser/OS/PWA-install-state in ways this app can't fully
   control. Email (via Resend, already wired in `email_service.py` for MFA
   codes and recaps) doesn't have that variance. Evaluate promoting email to
   a fallback — or primary — channel for time-sensitive alerts like
   "you're on the clock."
4. **Broader trigger coverage** (stretch goal, same pass): today the only
   alert of any kind is "on the clock." Candidates once the above is solid:
   draft-starting-soon, game day, weekly recap ready. The last one already
   exists as a separate backlog item (automate the recap email as a
   scheduled job) — worth coordinating rather than duplicating.

### Out of scope
Building a full notification-preferences UI (per-alert-type opt-in/out) —
revisit only if trigger coverage actually expands enough to need it.

---

## 4. Cost Checks

### Problem
No cost tracking or budget alerting exists on the `fishbone-wins-pool` GCP
project. The app now has real, ongoing infra spend (a web service + 4
scheduled Cloud Run Jobs, one of which — `winspool-live-scores` — runs every
5 minutes in-season) with no floor against surprise overspend.

### Approach

1. **Budget alerts first** (cheapest, highest safety-per-effort): set a GCP
   billing budget with alert thresholds on the project. This is pure safety
   net — catches gross overspend even before any breakdown work below is
   done.
2. **Cost breakdown by service:**
   - Cloud Run: web service + the 4 scheduled jobs, with particular
     attention to `winspool-predict-daily` (the only image carrying the ML
     dependency stack, `Dockerfile.predict`, `python:3.11-slim` +
     TensorFlow/XGBoost/scikit-learn) since it's the heaviest per-run cost.
   - Firestore: read/write volume, especially on `winspool-live-scores`
     (every 5 minutes in-season) — worth confirming its "narrow, last-7-days
     window" claim actually holds in practice rather than assuming it.
   - Gemini API: recap generation (`ai_service.py`) currently uses
     `gemini-1.5-flash` with no rate limiting or cost guard of any kind — low
     per-call cost, but worth confirming actual call volume matches
     expectations (e.g. not being triggered more often than intended).
3. **Output**: a cost-by-service breakdown report, mirroring the performance
   section's approach — baseline numbers plus specific thresholds worth
   alerting on, not a one-time snapshot.

---

## Sequencing note

These four sections have no dependencies on each other. Recommended pickup
order based on risk/effort:
1. **Cost checks §1 (budget alert only)** — smallest effort, pure safety net.
2. **Alerts §1 (instrumentation)** — needed before any other alerts work can
   be evidence-based rather than guesswork.
3. **UI tests** and **Performance check** — larger, can run in parallel with
   each other once picked up.
4. **Alerts §2-4** and **Cost checks §2-3** — follow-on work once their
   respective instrumentation/safety-net step has landed.

Each section becomes its own implementation plan when picked up, via
`superpowers:writing-plans`.
