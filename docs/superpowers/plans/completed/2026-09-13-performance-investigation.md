# Performance Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one markdown report with real, measured performance findings (Cloud Run latency/cold-starts, whole-system Firestore usage, frontend Core Web Vitals) and concrete recommendations for the `fishbone-wins-pool` GCP project — no fixes applied.

**Architecture:** Each task queries a real data source (Cloud Monitoring REST API, Cloud Run Jobs execution history, repo source code, or a live Lighthouse audit against production) and appends a findings section to one shared report file. The report accumulates section by section; the final task synthesizes cross-cutting recommendations and closes out the investigation.

**Tech Stack:** `gcloud` CLI (already authenticated as the project owner against `fishbone-wins-pool`), Cloud Monitoring REST API v3 (via `curl`/Python `requests` — no new Python dependency needed, `requests` is already available), `chrome-devtools-mcp`'s `lighthouse_audit` tool.

**Spec:** `docs/superpowers/specs/2026-09-13-performance-investigation-design.md`

## Global Constraints

- **Analysis window:** last 7 days, computed fresh at task-execution time (`date -u -d '7 days ago'` / `date -u`), not a fixed date range copied from this plan.
- **No fixes applied.** Every task produces findings and recommendations only. Do not change any Cloud Run service config, Firestore data, or app code as part of this plan.
- **GCP project:** `fishbone-wins-pool`. Cloud Run service: `winspool`, region `us-east1`, URL `https://winspool-62mntiyu5a-ue.a.run.app`. Scheduled jobs (same region): `winspool-sync-daily`, `winspool-predict-daily`, `winspool-live-scores`, `winspool-schedule-kickoffs`.
- **Report file:** every task appends to `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (create it in Task 1; every later task edits it, never overwrites earlier sections).
- **Frontend audit:** runs against the live production URL above, authenticated as the `e2e-test-01` fixture account (`e2e-test-01@winspool.internal`, password in this repo's `.env` as `E2E_TEST_PLAYER_PASSWORD` — first ID in `E2E_TEST_PLAYER_IDS` is this account).
- **Auth for GCP queries:** this environment's `gcloud` session is already authenticated with read access to Cloud Monitoring and Cloud Run. Every task's commands assume this — do not add service-account setup or credential handling.

---

## Task 1: Report scaffold + confirmed motivating finding

**Files:**
- Create: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md`

**Interfaces:**
- Produces: the report file every later task appends to. Section headings later tasks must use exactly: `## Backend / Cloud Run`, `## Firestore Usage`, `## Frontend Core Web Vitals`, `## Recommendations`.

This task locks in the report's structure and pre-fills the motivating finding already confirmed during brainstorming (re-verify the numbers live rather than copying them stale, since a few hours may have passed).

- [x] **Step 1: Re-confirm the Cloud Run scaling config**

Run:
```bash
gcloud run services describe winspool --region=us-east1 --format="value(spec.template.metadata.annotations)"
```
Expected: output contains `autoscaling.knative.dev/maxScale=1` and does **not** contain `autoscaling.knative.dev/minScale` (confirming it's still at the default of 0). If a `minScale` annotation is now present, someone changed this since the spec was written — record the actual current values instead of assuming the spec's numbers still hold.

- [x] **Step 2: Create the report file**

```markdown
# Performance Investigation Report

**Date:** <today's date, from `date -u +%Y-%m-%d`>
**Analysis window:** 7 days (<start> to <end>, from `date -u -d '7 days ago' +%Y-%m-%d` / `date -u +%Y-%m-%d`)
**Project:** fishbone-wins-pool
**Spec:** docs/superpowers/specs/2026-09-13-performance-investigation-design.md

## Motivating Finding

The `winspool` Cloud Run service (region `us-east1`) has:
- `autoscaling.knative.dev/maxScale=1` — capped at exactly one instance, never scales out.
- No `autoscaling.knative.dev/minScale` annotation — sits at the Cloud Run default of 0, so the service scales to zero when idle and cold-starts on the next request.

Confirmed live via `gcloud run services describe winspool --region=us-east1 --format="value(spec.template.metadata.annotations)"` on <date>. Full annotation string: `<paste the actual output here>`.

## Backend / Cloud Run

*(filled in by Task 2)*

## Firestore Usage

*(filled in by Tasks 3-5)*

## Frontend Core Web Vitals

*(filled in by Task 6)*

## Recommendations

*(filled in by Task 7)*
```

Fill in the `<...>` placeholders with real values from Step 1 and real `date` output — this file must never contain literal `<...>` once saved.

- [x] **Step 3: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: scaffold performance investigation report"
```

---

## Task 2: Backend / Cloud Run performance data

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (fill in `## Backend / Cloud Run`)

**Interfaces:**
- Consumes: the report file from Task 1 (edits the `## Backend / Cloud Run` section only).

Pulls real request-latency percentiles, cold-start (startup) latency, and instance-count-over-time data for the `winspool` service via the Cloud Monitoring REST API v3. These exact queries were run and confirmed working during planning — reuse them, don't reinvent the query shape.

- [x] **Step 1: Query p50/p95 request latency for successful (2xx) requests**

```bash
TOKEN=$(gcloud auth print-access-token)
START=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

for ALIGNER in ALIGN_PERCENTILE_50 ALIGN_PERCENTILE_95; do
  echo "=== $ALIGNER ==="
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://monitoring.googleapis.com/v3/projects/fishbone-wins-pool/timeSeries?filter=metric.type%3D%22run.googleapis.com%2Frequest_latencies%22%20AND%20resource.labels.service_name%3D%22winspool%22%20AND%20metric.labels.response_code_class%3D%222xx%22&interval.startTime=$START&interval.endTime=$END&aggregation.alignmentPeriod=604800s&aggregation.perSeriesAligner=$ALIGNER&aggregation.crossSeriesReducer=REDUCE_NONE" \
    | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('timeSeries',[{}])[0].get('points',[{}])[0].get('value',{}).get('doubleValue', 'NO DATA'))"
done
```
Expected: two real millisecond numbers (p50 and p95), not "NO DATA". If "NO DATA", the service had zero 2xx traffic in the window — record that fact instead of a number.

- [x] **Step 2: Query cold-start (container startup) latency**

```bash
TOKEN=$(gcloud auth print-access-token)
START=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://monitoring.googleapis.com/v3/projects/fishbone-wins-pool/timeSeries?filter=metric.type%3D%22run.googleapis.com%2Fcontainer%2Fstartup_latencies%22%20AND%20resource.labels.service_name%3D%22winspool%22&interval.startTime=$START&interval.endTime=$END&aggregation.alignmentPeriod=604800s&aggregation.perSeriesAligner=ALIGN_PERCENTILE_95&aggregation.crossSeriesReducer=REDUCE_NONE" \
  | python -m json.tool
```
Expected: one or more `timeSeries` entries (one per revision that started up in the window) with a p95 startup latency in milliseconds. Record the highest value seen (worst cold start) and how many distinct revisions/entries appeared (each entry is one cold-start event's revision, not a total count — note that limitation in the report rather than treating "number of entries" as "number of cold starts").

- [x] **Step 3: Query instance count over time to visualize scale-to-zero pattern**

```bash
TOKEN=$(gcloud auth print-access-token)
START=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://monitoring.googleapis.com/v3/projects/fishbone-wins-pool/timeSeries?filter=metric.type%3D%22run.googleapis.com%2Fcontainer%2Finstance_count%22%20AND%20resource.labels.service_name%3D%22winspool%22&interval.startTime=$START&interval.endTime=$END&aggregation.alignmentPeriod=3600s&aggregation.perSeriesAligner=ALIGN_MEAN&aggregation.crossSeriesReducer=REDUCE_SUM" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
pts = d.get('timeSeries', [{}])[0].get('points', [])
zero_hours = sum(1 for p in pts if p['value']['doubleValue'] == 0)
print(f'{len(pts)} hourly points, {zero_hours} hours at zero instances ({100*zero_hours/len(pts):.0f}% of the window)' if pts else 'NO DATA')
"
```
Expected: a real point count and a percentage of hours spent at zero instances. A high percentage (e.g. >30%) directly corroborates frequent cold starts; a low percentage means the service rarely scales to zero and cold starts are less frequent than the config alone suggests — report whichever is actually true.

- [x] **Step 4: Check request-concurrency clustering against `maxScale=1`**

```bash
TOKEN=$(gcloud auth print-access-token)
START=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://monitoring.googleapis.com/v3/projects/fishbone-wins-pool/timeSeries?filter=metric.type%3D%22run.googleapis.com%2Fcontainer%2Fmax_request_concurrencies%22%20AND%20resource.labels.service_name%3D%22winspool%22&interval.startTime=$START&interval.endTime=$END&aggregation.alignmentPeriod=3600s&aggregation.perSeriesAligner=ALIGN_MAX&aggregation.crossSeriesReducer=REDUCE_MAX" \
  | python -c "
import json, sys
d = json.load(sys.stdin)
pts = d.get('timeSeries', [{}])[0].get('points', [])
vals = [p['value']['doubleValue'] for p in pts]
print(f'peak concurrent requests in any hour this week: {max(vals) if vals else \"NO DATA\"}')"
```
Expected: a peak concurrency number. Cloud Run's default per-instance concurrency limit is 80 — a peak nowhere near that means `maxScale=1` is not currently a real bottleneck (even though it's still a latent risk if traffic grows, e.g. a full 10-player live draft). A peak approaching 80 means it's a live, current problem, not just theoretical. Report which case actually holds.

- [x] **Step 5: Write findings into the report**

Replace the `## Backend / Cloud Run` placeholder with the real numbers from Steps 1-4, e.g.:

```markdown
## Backend / Cloud Run

- **Request latency (2xx, 7-day window):** p50 = `<value>` ms, p95 = `<value>` ms.
- **Cold-start (container startup) latency:** p95 = `<value>` ms across `<N>` revision-entries in the window.
- **Time at zero instances:** `<N>` of `<total>` hourly samples (`<pct>`%) had zero running instances.
- **Peak concurrent requests (any single hour):** `<value>` (Cloud Run's per-instance default limit is 80).

**Interpretation:** <write 2-4 sentences connecting these numbers to the motivating finding — does the p50/p95 gap and time-at-zero data actually support "cold starts are the cause," or does the data tell a different story? State plainly which it is.>
```

- [x] **Step 6: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add backend/Cloud Run performance findings"
```

---

## Task 3: Firestore code-path audit — web app

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (start `## Firestore Usage`)

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: an inventory of every Firestore-touching function reachable from the web app, for Task 5 to reference when judging cache TTL sizing.

`services/db_service.py` is this app's sole Firestore access layer for player/game/draft data (per `CLAUDE.md`'s Data Flow & Caching section); `services/cache_service.py` and `services/data_service.py` additionally read/write a few collections directly (`analytics_cache`, `elo_history`, `nn_weekly_accuracy`, `game_predictions`). This task enumerates all of it with file:line citations — no estimating, no "probably touches Firestore."

- [x] **Step 1: Enumerate every Firestore collection reference in the web app's own code**

```bash
grep -rn "\.collection(" services/ routes/ --include="*.py" | grep -v "^services/db_service.py" | sort
grep -n "\.collection(" services/db_service.py | sort
```
Read through the output. For each distinct collection name found, note: which file(s)/function(s) reference it, and whether that access happens on every request (uncached) or behind `data_service.load_data()`'s or `cache_service.py`'s in-memory cache.

- [x] **Step 2: Trace which routes call which cached-vs-uncached paths**

For each of the app's main user-facing surfaces — standings/leaderboard, draft room (HTTP routes + the `/ws` WebSocket), admin dashboard, mock draft, recap, auth/login, chat, push subscription — identify (via `grep -n "load_data\|get_db\|\.collection(" routes/*.py`) whether that surface's Firestore reads go through the cached `load_data()` path or hit Firestore directly and, if directly, whether that function itself does any caching (e.g. `get_game_predictions` in `cache_service.py`).

Pay particular attention to the live draft WebSocket flow (`routes/draft_routes.py`) — it is the app's highest-concurrency code path (up to 10 real players against one draft simultaneously) and per-pick behavior matters more here than on any other page.

- [x] **Step 3: Write the web-app inventory into the report**

```markdown
## Firestore Usage

### Web app code paths

| Collection | Touched by | Cached? |
|---|---|---|
| <collection> | <file:function or file:line> | <"load_data() (1hr TTL)" / "cache_service.py's own TTL" / "no cache — every request"> |
| ... | ... | ... |

**Live draft WebSocket (`routes/draft_routes.py`):** <2-4 sentences on what Firestore access happens per pick, and whether that scales acceptably to a full 10-player draft based on the code alone (this is a code-level judgment here; Task 4's aggregate metric will show whether the real numbers back it up).>
```

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add web-app Firestore code-path inventory"
```

---

## Task 4: Firestore code-path audit — scheduled jobs + aggregate usage metrics

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (extend `## Firestore Usage`)

**Interfaces:**
- Consumes: the `## Firestore Usage` section started in Task 3 — appends to it, doesn't replace it.

Covers the other half of the whole-system Firestore picture: the 4 scheduled Cloud Run Jobs, plus the real aggregate read/write volume for the whole project (which the per-code-path inventory alone can't produce, since Cloud Monitoring's Firestore metrics aren't broken down by caller).

- [x] **Step 1: Enumerate each scheduled job's Firestore access**

```bash
grep -n "\.collection(\|--firestore" scripts/run_cron.py scripts/cache_builder.py scripts/sync_live_scores.py scripts/schedule_kickoffs.py scripts/sync_nflverse_data.py scripts/compute_elo.py scripts/daily_nfl_sync.py
```
For each of the 4 actual Cloud Run Jobs (`winspool-sync-daily` → `run_cron.py`'s chain, `winspool-predict-daily` → `cache_builder.py`, `winspool-live-scores` → `sync_live_scores.py`, `winspool-schedule-kickoffs` → `schedule_kickoffs.py`), note which collections it writes to (per `CLAUDE.md`'s Firestore-collection table) and how often it runs (per `CLAUDE.md`'s Scheduled Jobs table — `winspool-live-scores` every 5 minutes in-season is the standout).

- [x] **Step 2: Pull real execution frequency/duration for `winspool-live-scores`**

```bash
gcloud run jobs executions list --region=us-east1 --format="table(metadata.name,status.startTime,status.completionTime)" \
  | grep "winspool-live-scores" | head -20
```
Confirm the actual real-world cadence (should be ~every 5 minutes) and typical duration (compute from `startTime`/`completionTime` on a few rows). Each execution's own Firestore access (per Step 1's code reading) times this real frequency is this job's actual weekly Firestore load — do the arithmetic and include it.

- [x] **Step 3: Pull aggregate project-wide Firestore read/write counts for the 7-day window**

```bash
TOKEN=$(gcloud auth print-access-token)
START=$(date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

for METRIC in read_count write_count delete_count; do
  echo "=== $METRIC ==="
  curl -s -H "Authorization: Bearer $TOKEN" \
    "https://monitoring.googleapis.com/v3/projects/fishbone-wins-pool/timeSeries?filter=metric.type%3D%22firestore.googleapis.com%2Fdocument%2F$METRIC%22&interval.startTime=$START&interval.endTime=$END&aggregation.alignmentPeriod=604800s&aggregation.perSeriesAligner=ALIGN_SUM&aggregation.crossSeriesReducer=REDUCE_SUM" \
    | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('timeSeries',[{}])[0].get('points',[{}])[0].get('value',{}).get('int64Value', 'NO DATA'))"
done
```
Expected: three real integers (total document reads/writes/deletes, project-wide, for the whole week). These numbers are project-wide, not per-service — note that limitation plainly in the report rather than attributing the total to any one cause without evidence.

- [x] **Step 4: Write findings into the report**

```markdown
### Scheduled jobs

| Job | Firestore writes | Real cadence (from execution history) | Typical duration |
|---|---|---|---|
| winspool-sync-daily | <collections> | daily | <n>s |
| winspool-predict-daily | <collections> | daily | <n>s |
| winspool-live-scores | <collections> | every ~<n> min (confirmed) | <n>s |
| winspool-schedule-kickoffs | <collections> | weekly | <n>s |

### Aggregate project-wide volume (7-day window)

- Document reads: `<value>`
- Document writes: `<value>`
- Document deletes: `<value>`

**Interpretation:** <2-4 sentences: is this volume large for an app this size? Does it correlate plausibly with winspool-live-scores' 5-minute cadence plus real user traffic, based on the code-level access patterns found in Task 3 and Step 1 above? Be specific about what you can and can't attribute confidently given these metrics aren't broken down by caller.>
```

- [x] **Step 5: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add scheduled-jobs Firestore audit and aggregate usage metrics"
```

---

## Task 5: Cache TTL sizing review

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (extend `## Firestore Usage`)

**Interfaces:**
- Consumes: the web-app and scheduled-jobs inventories from Tasks 3-4.

`services/cache_service.py:118` defines `_CACHE_TTL_SECONDS = 3600` (1 hour) — the single in-memory TTL governing both `data_service.py::load_data()`'s main cache and `cache_service.py`'s own cached reads. This task judges whether that one number is still well-sized now that Tasks 3-4 have produced a real picture of what's hitting Firestore, rather than the solo-dev-era assumptions it was originally tuned against.

- [x] **Step 1: Read the TTL's current rationale**

```bash
grep -n -B2 -A2 "_CACHE_TTL_SECONDS" services/cache_service.py
```
Note the existing inline comment's stated rationale (as of planning time: "long enough to avoid Firestore spam on every request, short enough to catch same-day data changes").

- [x] **Step 2: Reason about sizing against Tasks 3-4's findings**

Answer directly, using the real numbers already gathered — no new commands needed for this step:
- Given the real aggregate Firestore read volume (Task 4, Step 3) and the web-app inventory (Task 3), does 1 hour look too short (real spam), too long (stale data risk for a user-facing page), or about right?
- Does the live-draft WebSocket path (Task 3, Step 2) bypass this TTL in a way that matters (e.g. does draft state need to be much fresher than 1 hour, and if so, does it already get that via a different mechanism)?

- [x] **Step 3: Write the recommendation into the report**

```markdown
### Cache TTL sizing (`cache_service.py:118`, `_CACHE_TTL_SECONDS`)

Current value: 3600s (1 hour), shared by `data_service.py::load_data()`'s main cache and `cache_service.py`'s own cached reads.

**Recommendation:** <"keep at 1 hour" / "raise to X" / "lower to X" / "leave as-is but note Y", with the reasoning from Step 2 stated concretely, citing the real numbers from Tasks 3-4 rather than restating the original rationale unexamined.>
```

- [x] **Step 4: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add cache TTL sizing recommendation"
```

---

## Task 6: Frontend Core Web Vitals

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (fill in `## Frontend Core Web Vitals`)

**Interfaces:**
- Consumes: `e2e-test-01@winspool.internal` credentials from this repo's `.env` (`E2E_TEST_PLAYER_PASSWORD`; the account itself is the first ID in `E2E_TEST_PLAYER_IDS`).

Runs a real Lighthouse audit against the live production URL, authenticated, at both mobile and desktop viewports, on the two highest-traffic pages: standings and the draft room.

- [x] **Step 1: Load the chrome-devtools-mcp tools**

These are deferred tools in this environment — load them before use:
```
ToolSearch query: "select:mcp__plugin_chrome-devtools-mcp_chrome-devtools__new_page,mcp__plugin_chrome-devtools-mcp_chrome-devtools__navigate_page,mcp__plugin_chrome-devtools-mcp_chrome-devtools__fill,mcp__plugin_chrome-devtools-mcp_chrome-devtools__click,mcp__plugin_chrome-devtools-mcp_chrome-devtools__wait_for,mcp__plugin_chrome-devtools-mcp_chrome-devtools__lighthouse_audit,mcp__plugin_chrome-devtools-mcp_chrome-devtools__resize_page"
```

- [x] **Step 2: Log in as `e2e-test-01` against production**

Open a new page at `https://winspool-62mntiyu5a-ue.a.run.app`, fill in the real signin form with `e2e-test-01@winspool.internal` / the password from `.env`'s `E2E_TEST_PLAYER_PASSWORD`, submit, and wait for the signin overlay to disappear — the same real-UI login flow `tests_e2e/test_standings.py::_login` uses, just driven via chrome-devtools-mcp tools instead of Playwright. Do not bypass this with a direct API call — Lighthouse needs a real authenticated browser session (cookies set the normal way) to measure what a real user's browser actually does.

- [x] **Step 3: Desktop Lighthouse audit — standings**

Navigate to `/wins-pool` (or whatever the standings page resolves to for this account — check the nav after login if unsure), then run `lighthouse_audit` at the default desktop viewport. Record LCP, CLS, and INP (or TBT if this Lighthouse version doesn't report INP directly).

- [x] **Step 4: Desktop Lighthouse audit — draft room**

Navigate to `/draft`, run `lighthouse_audit` again. Note: if no season currently has an active draft, this page may render a "no active draft" state rather than the full draft room — record which state was actually measured, don't assume it was the full interactive draft room.

- [x] **Step 5: Mobile Lighthouse audits — both pages**

Resize the page to the ~390px mobile width `CLAUDE.md` calls out (`resize_page`), then repeat Steps 3-4's `lighthouse_audit` calls for both pages at that width.

- [x] **Step 6: Write findings into the report**

```markdown
## Frontend Core Web Vitals

Audited against production (`https://winspool-62mntiyu5a-ue.a.run.app`), authenticated as `e2e-test-01`.

| Page | Viewport | LCP | CLS | INP/TBT |
|---|---|---|---|---|
| Standings (/wins-pool) | Desktop | <value> | <value> | <value> |
| Standings (/wins-pool) | Mobile (~390px) | <value> | <value> | <value> |
| Draft room (/draft) | Desktop | <value> | <value> | <value> |
| Draft room (/draft) | Mobile (~390px) | <value> | <value> | <value> |

**Draft room state at time of audit:** <"full interactive draft room" or "no-active-draft placeholder" — state which>

**Interpretation:** <2-4 sentences: do any of these numbers fall outside Google's "good" thresholds (LCP < 2.5s, CLS < 0.1, INP < 200ms)? If the draft room was measured in its placeholder state rather than the real interactive room, say so as a known gap rather than treating the number as representative.>
```

- [x] **Step 7: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add frontend Core Web Vitals findings"
```

---

## Task 7: Synthesize recommendations and close out

**Files:**
- Modify: `docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md` (fill in `## Recommendations`)
- Move: `docs/superpowers/specs/2026-09-13-performance-investigation-design.md` → `docs/superpowers/specs/completed/2026-09-13-performance-investigation-design.md`

**Interfaces:**
- Consumes: every section written by Tasks 1-6.

Pulls the whole report together into concrete, prioritized recommendations — the actual deliverable of this investigation — and archives the design spec now that its investigation is done.

- [x] **Step 1: Write the Recommendations section**

Read back through every section of the report written so far, then write:

```markdown
## Recommendations

Ordered by expected impact / effort ratio, highest first. None of these are applied as part of this investigation — see the spec's "Report + recommendations only" constraint.

1. **<Cloud Run minScale/maxScale recommendation>** — state the specific recommended values (e.g. "set minScale=1"), the concrete evidence from Task 2 that justifies it, and the cost trade-off explicitly (an always-warm instance is billed continuously rather than scaling to zero — quantify this at least roughly: 1 vCPU + 512Mi running 24/7 vs. the current scale-to-zero billing, using Cloud Run's published per-vCPU-second/per-GiB-second pricing).
2. **<Firestore/cache finding, if Task 5 recommended a change>**
3. **<Any Core Web Vitals finding that exceeded a "good" threshold>**
4. **Suggested alert thresholds going forward**, one per metric actually gathered in this report (e.g. "alert if p95 request latency > X ms," "alert if Firestore reads/day > Y"), each with the real baseline number from this report as the reference point.

If any area's findings didn't support the motivating finding as strongly as expected, say so plainly here rather than forcing a narrative — this section's job is accuracy, not confirming the hypothesis from Task 1.
```

- [x] **Step 2: Move the design spec to completed/**

```bash
git mv docs/superpowers/specs/2026-09-13-performance-investigation-design.md docs/superpowers/specs/completed/2026-09-13-performance-investigation-design.md
```

- [x] **Step 3: Commit**

```bash
git add docs/superpowers/specs/completed/2026-09-13-performance-investigation-report.md
git commit -m "docs: add performance investigation recommendations, archive design spec"
```

---

## Self-Review Notes

- **Spec coverage:** Scope A (Backend/Cloud Run) → Task 2. Scope B (Firestore, whole-system) → Tasks 3-5 (web app inventory, scheduled jobs + aggregate metrics, TTL sizing). Scope C (Frontend CWV) → Task 6. Motivating finding → Task 1 (re-confirmed live, not copied stale). Output (one report, recommendations, thresholds, cost trade-off stated) → Task 7. Explicitly-out-of-scope items (cost breakdown, Alerts §2/§4, applying fixes) are not tasked here, matching the spec.
- **Every GCP query in this plan was run for real during planning** (not guessed) against the live `fishbone-wins-pool` project — `gcloud monitoring time-series list` does not exist as a command in the installed gcloud version; the REST API v3 via `curl` + a bearer token is the working approach used throughout, confirmed against real data (e.g. p50/p95 latency, cold-start latency, and instance-count queries all returned real numbers, not empty results, when tested).
- **Known real numbers from testing** (for context only — tasks must re-query fresh, not copy these): p95 request latency ≈ 985ms vs p50 ≈ 41ms (a ~24x gap, consistent with cold starts skewing the tail); p95 cold-start latency ≈ 11.1 seconds on at least one revision; aggregate project-wide Firestore reads ≈ 2.72M over 7 days.
