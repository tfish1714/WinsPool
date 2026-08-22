# Model Quality Drift Monitoring

**Date:** 2026-08-22
**Status:** Not designed — backlog item, split out of the injury-aware-roster-value plan's final MLOps-lens review so it doesn't get lost. Needs a proper brainstorming pass before implementation.

## Origin

Surfaced while reviewing `docs/superpowers/plans/2026-08-22-injury-aware-roster-value.md` (already implemented and merged) through an MLOps-pipeline-quality lens: the scheduled jobs have real failure alerting, but nothing watches whether the *predictions themselves* are still good.

## What's already verified

- `services/email_service.py::send_alert_email()` + the Cloud Monitoring alert policy on `completed_execution_count` cover **job failures** (crashed, didn't run, non-zero exit) — this is solid and already documented in CLAUDE.md's Scheduled Jobs section.
- `scripts/weekly_model_eval.py` already computes real accuracy metrics (per-week and season-range ensemble accuracy) and writes them to `reports/nn_weekly_accuracy.csv` — but it's a manual command, never scheduled. Nothing runs it automatically, and nothing reads that CSV to decide whether anything needs attention.
- There is no signal anywhere in the system for "the job ran fine, but its predictions have quietly gotten worse." A model that degrades — from a bad retrain, a silent data-source schema change upstream (nflverse), or a bug in a feature-computation script — would currently be caught only by a human noticing wrong-looking predictions on the schedule page, not by any automated check.

## What's genuinely needed here

A scheduled check that runs the existing `weekly_model_eval.py`-style accuracy computation on a cadence (e.g. after each week's games complete), compares it against a rolling baseline or the pre-retrain reference already used in the Task 7 promotion gate, and alerts (via the existing `send_alert_email()` path, so it reuses established infra rather than inventing a new channel) if quality has dropped beyond some threshold.

## Open questions to resolve when this is picked up

These need an actual brainstorming pass (clarifying questions → approaches → design), not decided here:

1. **What metric actually signals "degraded"?** Weekly accuracy vs. a fixed baseline? ATS performance vs. Vegas (the model is already compared to the market via `edge_vs_vegas`)? Some rolling average that's robust to normal week-to-week variance (NFL outcomes are noisy by nature — a bad week doesn't mean a bad model)?
2. **What threshold triggers an alert, and over what window?** A single bad week will happen periodically even for a good model; the real signal is a sustained shift. Needs a rule that doesn't cry wolf every few weeks but also doesn't wait so long that a real regression sits unnoticed all season.
3. **Does this need a new scheduled job, or can it piggyback on an existing one?** `winspool-predict-daily` already runs daily and could plausibly run an evaluation step after a week completes, rather than provisioning a fifth Cloud Run Job.
4. **Should this also cover the `RESIMULATE_LEAD_MINUTES` runtime risk** flagged during the injury-aware-roster-value review (the `--resimulate` mode's real runtime was never measured, and nothing would notice if it started running long over time)? That's a related but distinct kind of drift (operational timing, not prediction quality) — worth deciding whether it belongs in the same monitoring pass or its own.
5. **Where do results live?** Just email alerts, or also a queryable history (e.g. writing to Firestore/the existing `reports/` CSV) so a trend can be seen, not just a single threshold-crossing event?

## Non-goals

- Not a general observability platform — scoped specifically to prediction-quality signal, not infrastructure metrics (those are already covered).
- Not deciding the alerting mechanism from scratch — reuse `send_alert_email()` unless a real reason emerges not to.
