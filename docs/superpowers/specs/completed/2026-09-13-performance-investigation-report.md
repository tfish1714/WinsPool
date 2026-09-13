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

*(filled in by Task 2)*

## Firestore Usage

*(filled in by Tasks 3-5)*

## Frontend Core Web Vitals

*(filled in by Task 6)*

## Recommendations

*(filled in by Task 7)*
