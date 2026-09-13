# Performance Investigation Report

**Date:** 2026-09-13
**Analysis window:** 7 days (2026-09-06 to 2026-09-13)
**Project:** fishbone-wins-pool
**Spec:** docs/superpowers/specs/2026-09-13-performance-investigation-design.md

## Motivating Finding

The `winspool` Cloud Run service (region `us-east1`) has:
- `autoscaling.knative.dev/maxScale=1` — capped at exactly one instance, never scales out.
- No `autoscaling.knative.dev/minScale` annotation — sits at the Cloud Run default of 0, so the service scales to zero when idle and cold-starts on the next request.

Confirmed live via `gcloud run services describe winspool --region=us-east1 --format="value(spec.template.metadata.annotations)"` on 2026-09-13. Full annotation string: `autoscaling.knative.dev/maxScale=1;run.googleapis.com/client-name=gcloud;run.googleapis.com/client-version=559.0.0;run.googleapis.com/startup-cpu-boost=true`.

## Backend / Cloud Run

- **Request latency (2xx, 7-day window):** p50 = `41.3` ms, p95 = `984.7` ms.
- **Cold-start (container startup) latency:** p95 = `20111.6` ms (worst of the window, revision `winspool-00097-8g7`) across `13` revision-entries in the window (range 7146.9-20111.6 ms; each entry is one revision's startup event, not a count of scale-from-zero cold starts experienced by users).
- **Time at zero instances:** `0` of `112` hourly samples (`0`%) had zero running instances; max concurrent instance count observed was `2` (briefly, consistent with revision rollover overlap, not sustained scale-out — `maxScale=1` still caps steady-state capacity at one instance). Note the window returned 112 hourly points rather than the expected ~168 for a 7-day span; this was not investigated further per instructions not to force a different query shape.
- **Peak concurrent requests (any single hour):** could not be determined — the exact query specified in the brief (`ALIGN_MAX` / `REDUCE_MAX` over `run.googleapis.com/container/max_request_concurrencies`) returned a Cloud Monitoring API error rather than data: `400 INVALID_ARGUMENT: "The aligner cannot be applied to metrics with kind DELTA and value type DISTRIBUTION."` This metric is a delta/distribution type in this project, which `ALIGN_MAX` cannot align; per task instructions, this was not silently retried with a different aligner. This step should be re-run with a distribution-compatible aligner (e.g. `ALIGN_PERCENTILE_99`) as a follow-up.

**Interpretation:** The data does not support "scale-to-zero cold starts are causing the p95 latency spikes" as stated. The service spent 0% of the 7-day window at zero instances — it did not actually scale to zero in this period despite having no `minScale` floor, so idle-triggered cold starts were rare-to-nonexistent here. The 13 measured startup events (7.1-20.1 seconds each) instead line up with revision deployments during the week, not traffic-driven scale-ups. Critically, those startup latencies (7-20 *seconds*) are 10-20x larger than the p95 *request* latency (985 ms), meaning a cold start — if a user request landed during one — would dominate that request's latency far beyond what shows up at p95; the fact that p95 is only ~1 second suggests either very few user requests actually hit a cold instance in this window, or Cloud Run's startup-cpu-boost plus low traffic volume kept most requests off the small number of cold windows. The 24x p50-to-p95 gap (41 ms to 985 ms) is real but likely reflects a mix of a few slow endpoints and/or the rare cold-start-affected request, not a systemic scale-to-zero problem in the current traffic pattern. The `maxScale=1` risk remains a real latent concern (worth revisiting once concurrency data is available), but the cold-start theory as the primary driver of the motivating finding is not well corroborated by this window's data.

## Firestore Usage

*(filled in by Tasks 3-5)*

## Frontend Core Web Vitals

*(filled in by Task 6)*

## Recommendations

*(filled in by Task 7)*
