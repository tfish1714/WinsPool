# Performance Investigation

**Status:** Draft — ready for `superpowers:writing-plans`
**Date:** 2026-09-13

## Origin

Split off from `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`'s
four independent workstreams (UI Tests, Performance Check, Alerts/Push, Cost
Checks). This spec covers **Workstream 2 (Performance Check)** only.

It started life as a combined performance-and-cost investigation
(`2026-09-13-performance-cost-investigation-design.md`, this file's
predecessor), but during brainstorming a concrete Cloud Run configuration gap
surfaced that made performance — specifically cold-start latency the project
owner had already noticed firsthand — the clear priority. Cost Checks
(Workstream 4) is deferred back to its own future pass; see "Explicitly out
of scope" below.

## Audit: what's actually done, re-verified against the repo (not just memory)

Re-checked the parent design doc's four workstreams against current code
before scoping this investigation, since a prior session's memory claimed
"cost+alerts §1 done 2026-09-10" and that needed verification, not blind
trust:

| Workstream | Section | Status | Evidence |
|---|---|---|---|
| 1. UI Tests | — | **Done** | All 4 sibling e2e plans merged into `main`; `tests_e2e/` covers login, standings, mock draft, full live draft, nav parity, and the full admin-flows surface. |
| 3. Alerts | §1 Instrumentation | **Done** | `services/push_service.py` now logs real success/failure instead of a debug-level swallow; `routes/draft_routes.py::_send_push_sync` logs via `logger.exception` instead of a bare `except: pass`. |
| 3. Alerts | §3 Fallback channel | **Partially done, further than the spec asked** | `routes/draft_routes.py::_send_on_the_clock_email_sync` already ships a real email fallback via `services/email_service.py::send_on_the_clock_email`, sent unconditionally alongside push for the one existing "on the clock" trigger. |
| 3. Alerts | §2 iOS PWA fix | **Not done** | No `manifest.json` anywhere in the app; no `apple-mobile-web-app-capable` meta tag in any template. |
| 3. Alerts | §4 Broader triggers | **Not done** | "On the clock" remains the only trigger. |
| 4. Cost Checks | all | **Not started; deferred, not part of this spec** | No budget-alert evidence in the repo (it's a GCP Console setting, unverifiable from code either way), no cost breakdown report, nothing in `reports/`. |
| 2. Performance Check | all | **Not started — this spec's scope** | No Cloud Monitoring dashboard config, no baseline report, no Lighthouse run anywhere in the repo. |

Alerts §2 and §4 are real, open gaps but out of scope here (UX/reliability
work, not measurement) — flagged for a separate future pass.

## Motivating finding

While confirming GCP access for this investigation, pulled the `winspool`
Cloud Run service's actual scaling config:

```
autoscaling.knative.dev/maxScale=1
run.googleapis.com/startup-cpu-boost=true
```

No `minScale` annotation at all — meaning it sits at the Cloud Run default of
**0**. Two distinct, concrete problems follow directly from this:

1. **Cold starts on every request after an idle period** — this is almost
   certainly the delay the project owner has directly observed.
2. **`maxScale=1` means the service can never scale out**, even under
   genuine concurrent load (e.g. all 10 players in a live draft hitting it
   at once). Under load this manifests as request queuing on a single
   instance rather than a clean scale-out — a distinct risk from cold
   starts, worth measuring separately.

This finding drives the investigation's priority and its first concrete
recommendation candidate (see Output), but is **not applied** as part of
this work — see "Report + recommendations only," below.

## Scope

### A. Backend / Cloud Run performance
- Pull actual cold-start frequency and p50/p95 request latency from Cloud
  Monitoring over the analysis window (below), and correlate any latency
  spikes against the `minScale=0` gap directly (are the worst latencies
  actually the cold-start requests?).
- Check request-concurrency patterns against the `maxScale=1` ceiling — does
  real traffic actually cluster in ways (e.g. live-draft windows, all 10
  players connecting within a short span) that would hit this ceiling and
  queue?
- Extend the existing scheduled-jobs alert-policy pattern
  (`run.googleapis.com/job/completed_execution_count`) as a model for what a
  request-latency alert policy on the web service could look like — a
  recommendation, not something built in this pass.

### B. Firestore usage — whole system, not just the hot user-facing pages
Broadened during brainstorming from an initial standings/draft-room-only
scope to cover every Firestore-touching code path in the app:

- Every web route/service backed by `data_service.load_data()`'s 3-tier
  cache: standings, draft, admin, mock-draft, recap, auth/session, chat,
  push.
- The live draft WebSocket flow specifically — per-pick Firestore
  read/write volume, since this is the single highest-concurrency code path
  in the app (10 real players against one draft in real time).
- All 4 scheduled Cloud Run Jobs' own Firestore access:
  `winspool-sync-daily`, `winspool-predict-daily`, `winspool-live-scores`
  (every 5 minutes in-season — the highest-frequency job by far), and
  `winspool-schedule-kickoffs`. These share the same Firestore
  quotas/performance envelope as user-facing requests even though they're
  not user-facing themselves.
- Whether `cache_service.py`'s in-memory TTLs are sized correctly for real
  concurrent usage, now that there's a full picture of what's actually
  hitting Firestore rather than assumptions from solo-dev-era tuning.

### C. Frontend performance
- Lighthouse / Core Web Vitals (LCP, CLS, INP) on the standings and draft
  room pages, at both mobile and desktop viewports.
- **Run against the live production URL**, not a local dev server — most
  representative of real user experience (real Cloud Run cold starts, real
  network), and read-only page loads are negligible load on prod.
- Standings/draft room sit behind a client-side login wall
  (`static/js/main.js`), so an authenticated session is required to measure
  real content rather than just the signin screen. **Log in as the existing
  `e2e-test-01` fixture account** (already seeded in production Firestore
  for e2e testing — no new credentials needed).
- `chrome-devtools-mcp`'s `lighthouse_audit` tool is the direct path here.

## Execution decisions (confirmed during brainstorming)

- **GCP access:** this environment's `gcloud` session is already
  authenticated as the project owner against `fishbone-wins-pool`, with
  working access to Cloud Monitoring and Cloud Billing APIs — query
  directly, no need to relay commands back and forth.
- **Analysis window:** last 7 days — recent real usage without diluting it
  with older, less-representative data.
- **Report location:** a markdown doc (this repo's existing convention for
  investigation write-ups), not a published Artifact/dashboard.

## Output

One markdown report at `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md`
(this spec itself moves to `plans/completed/`'s sibling, `specs/completed/`,
alongside it once the investigation is done) with:
- Concrete numbers for each of A/B/C above, from the confirmed 7-day window.
- Specific recommended fixes, clearly labeled as recommendations, e.g.
  "set `minScale=1`" — **stated with its cost trade-off (an always-warm
  instance is billed continuously instead of scaling to zero), not
  applied.**
- Recommended alert thresholds for the metrics gathered (matching the
  parent spec's original intent: not a one-time snapshot, something with
  thresholds worth alerting on going forward).

### Report + recommendations only — no fixes applied
This investigation produces findings and recommendations. Applying the
Cloud Run scaling fix, or any other remediation this pass surfaces, is a
deliberate, separate decision for the project owner to make afterward —
not bundled into this work, even though the root cause looks likely to be
already found.

## Explicitly out of scope
- **Cost Checks (Workstream 4)** — budget-alert confirmation, cost
  breakdown by service (Cloud Run, Firestore, Gemini API). Deferred to its
  own future pass now that this investigation is performance-only.
- **Alerts §2 (iOS PWA manifest/meta tags) and §4 (broader trigger
  coverage)** — real, confirmed gaps, but UX/reliability fixes, not
  measurement. Deserve their own follow-up spec.
- **Applying any fix** this investigation finds, including the Cloud Run
  scaling change already suspected — see above.
- Checking off/archiving the 4 now-completed UI-tests plans — unrelated
  housekeeping, mentioned only because it came up during the same audit.

## Next step
Write the implementation plan via `superpowers:writing-plans`.
