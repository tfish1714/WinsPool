# Performance & Cost Investigation

**Status:** Draft — ready for brainstorming
**Date:** 2026-09-13

## Origin

Split off from `docs/superpowers/specs/2026-09-09-post-launch-hardening-design.md`'s four independent workstreams (UI Tests, Performance Check, Alerts/Push, Cost Checks). Workstream 1 (UI Tests) is now fully implemented and merged (`docs/superpowers/plans/2026-09-09-ui-tests-{playwright,player-flows,auth-lifecycle,admin-flows}.md`, none yet checked off/archived — separate housekeeping item, not blocking). This spec bundles **Workstream 2 (Performance Check)** and **Workstream 4 (Cost Checks)** together, since both are "measure real GCP usage now that the app has live traffic" investigations against the same project (`fishbone-wins-pool`) and naturally share tooling (Cloud Monitoring, Cloud Billing) and a report-shaped output.

Workstream 3 (Alerts/Push) is intentionally **not** included here — see the audit below for its actual state, which differs from what was assumed going in.

## Audit: what's actually done, re-verified against the repo (not just memory)

Before writing this spec, re-checked the parent design doc's four workstreams against current code, since a prior session's memory claimed "cost+alerts §1 done 2026-09-10" and that claim needed verification, not blind trust:

| Workstream | Section | Status | Evidence |
|---|---|---|---|
| 1. UI Tests | — | **Done** | All 4 sibling e2e plans merged into `main`; `tests_e2e/` covers login, standings, mock draft, full live draft, nav parity, and (as of today) the full admin-flows surface. |
| 3. Alerts | §1 Instrumentation | **Done** | `services/push_service.py` now logs real success/failure (`logger.info`/`logger.warning`/`logger.exception`) instead of the old debug-level swallow; `routes/draft_routes.py::_send_push_sync` also now logs via `logger.exception` instead of a bare `except: pass`. |
| 3. Alerts | §3 Fallback channel | **Partially done, further than the spec asked** | `routes/draft_routes.py::_send_on_the_clock_email_sync` already ships a real email fallback via `services/email_service.py::send_on_the_clock_email`, sent unconditionally alongside push (not gated on push failure) for the one existing "on the clock" trigger. The spec only asked to *evaluate* this; it's actually built. |
| 3. Alerts | §2 iOS PWA fix | **Not done** | No `manifest.json` anywhere in the app (only unrelated files under `graphify-out/`). No `apple-mobile-web-app-capable` meta tag in any template. iOS Safari users in a plain browser tab still silently get no push capability at all. |
| 3. Alerts | §4 Broader triggers | **Not done** | "On the clock" (now push + email) remains the only trigger. No draft-starting-soon, game-day, or recap-ready alerts exist. |
| 4. Cost Checks | §1 Budget alert | **Unverifiable from the repo** | This is a GCP Console / Cloud Billing setting, not code — nothing to grep for. Memory says it was done 2026-09-10, but that can't be confirmed from this checkout. **Must be confirmed directly against the GCP project during this investigation, not assumed.** |
| 4. Cost Checks | §2-3 Breakdown + thresholds | **Not done** | No cost report, no breakdown script, nothing in `reports/`. |
| 2. Performance Check | all | **Not started** | No Cloud Monitoring dashboard config, no baseline report, no Lighthouse run or config anywhere in the repo. Confirmed via repo-wide search — the only hits for "lighthouse"/"dashboard"/"billingbudgets" are the parent spec's own prose. |

**Net effect on scope:** this investigation is Performance Check (full) + Cost Checks §2-3 (breakdown/thresholds) + a **first confirmation** of whether Cost §1's budget alert genuinely exists on the live project (cheap to check, and the whole rest of this investigation should not proceed on an unconfirmed assumption about the one safety net already claimed done). Alerts §2 and §4 are real, open gaps but are out of scope here — flagged for a separate pass rather than silently folded in, since they're UX/reliability work, not measurement.

## Scope of this investigation

### A. Backend (Cloud Run) performance
- p50/p95 request latency and cold-start frequency for the `winspool` web service.
- Extend the existing scheduled-jobs alert-policy pattern (`run.googleapis.com/job/completed_execution_count`) to the request-serving service rather than inventing a new monitoring approach.

### B. Data-layer (Firestore) performance
- Firestore read counts per request on hot paths: standings, draft room state.
- Whether `cache_service.py`'s in-memory TTLs are sized correctly under real concurrent usage rather than solo-dev-era assumptions.

### C. Frontend performance
- Lighthouse / Core Web Vitals (LCP, CLS, INP) on the standings and draft room pages.
- Mobile viewport specifically — this app has a real history of mobile-specific gaps (see CLAUDE.md's nav-parity incident).
- `chrome-devtools-mcp`'s `lighthouse_audit` tool is the direct path here, already available in this environment.

### D. Cost breakdown by service
- Cloud Run: web service + the 4 scheduled jobs, particular attention to `winspool-predict-daily` (only image carrying the ML dependency stack — TensorFlow/XGBoost/scikit-learn — via `Dockerfile.predict`).
- Firestore read/write volume, especially `winspool-live-scores` (runs every 5 minutes in-season) — worth confirming its "narrow, last-7-days window" claim actually holds under real billing data, not just code inspection.
- Gemini API (recap generation, `ai_service.py`, `gemini-1.5-flash`) — confirm actual call volume matches expectations, no runaway triggering.

### E. Budget-alert confirmation (cheap, do first)
- Directly confirm in the GCP Console whether a billing budget + alert threshold actually exists on `fishbone-wins-pool` today. If it does, note its threshold for reference. If it doesn't (memory was wrong), that becomes this investigation's first, highest-priority finding — the parent spec called this "smallest effort, pure safety net," and it should not still be missing.

### Output
One baseline report combining performance and cost findings — concrete numbers plus recommended alert thresholds (e.g. p95 latency, Firestore reads/request, $/day by service), matching the parent spec's stated intent for both workstreams: not a one-time snapshot, something with thresholds worth alerting on going forward. Where a threshold is breached or a real problem surfaces, that becomes its own follow-up rather than being fixed inline during this investigation pass.

## Explicitly out of scope here
- Alerts §2 (iOS PWA manifest/meta tags) and §4 (broader push/email trigger coverage) — real, confirmed gaps, but UX/reliability fixes, not measurement. Deserve their own follow-up spec.
- Fixing any performance or cost problem this investigation finds — surfacing it with a number and a recommended threshold is this pass's job; remediation is separate follow-on work, consistent with how the parent spec scoped both workstreams' "Output" sections.
- Checking off/archiving the 4 now-completed UI-tests plans — unrelated housekeeping, mentioned only because it came up during the same audit.

## Next step
Brainstorm the actual approach (tooling choices, what "done" looks like for the report, whether any of this touches prod in a way that needs care) via `superpowers:brainstorming` before writing an implementation plan.
