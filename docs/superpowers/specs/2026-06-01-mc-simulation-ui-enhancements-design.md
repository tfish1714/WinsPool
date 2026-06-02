# MC Simulation UI Enhancements — Design Spec
**Date:** 2026-06-01  
**Status:** Pending (depends on `2026-06-01-dynamic-mc-simulation-design.md`)

---

## Context

The dynamic MC simulation spec produces per-game win probabilities as the mean across 10,000 trials, and those probabilities naturally carry more uncertainty for games further into the season (week 14 depends on 13 weeks of simulated outcomes; week 7 depends on only 6). The backend already emits `mean_prob` and `n_sims` per game. This spec covers surfacing that information in the UI.

---

## Requirements

1. Future game predictions in the schedule view display the MC win probability as a percentage framed as a simulation fraction (e.g. "wins in 73% of simulations") rather than a generic "confidence" label.
2. The explanation modal shows the simulation source: "MC simulation (10,000 trials)" replacing the current `"profile"` source text.
3. Future games have a visual uncertainty indicator that scales with how far into the season the game is — week 7 predictions are more certain than week 14 predictions because fewer simulated weeks precede them.
4. The uncertainty indicator does not need to show exact numbers; a subtle visual cue (e.g. reduced opacity, a small icon, or a label like "early projection") is sufficient for later-week games.

---

## Data Available from Backend (no schema changes needed)

After the dynamic MC spec is implemented, `game_predictions` for future games already contain:

| Field | What it means |
|---|---|
| `pred_prob` | Mean win probability across all MC trials |
| `pred_su_conf` | Same value as a percentage |
| `explanation.source` | `"mc_simulation (N trials)"` |
| `week` | Derivable from the game key (`W{wk:02d}_{ht}_{at}`) |

The number of future weeks before this game (= `game_week - current_week`) drives the uncertainty indicator. No new backend fields required.

---

## UI Changes

### Schedule game card / row
- Replace label "Conf:" with "Win probability:" and append "of simulations" or similar phrasing
- Current week +1 (next unplayed game): full opacity, no extra indicator
- Games 2–4 weeks out: subtle reduced opacity or muted color on the confidence value
- Games 5+ weeks out: add a small label ("long-range") or icon alongside the confidence value

### Explanation modal
- Change source display from `"profile"` to the simulation string already in `explanation.source`
- Add a one-line note: "Projected using week-by-week simulation; later weeks carry higher uncertainty"

---

## Out of Scope

- Showing the full win probability distribution (p25/p75 range) per game — the backend doesn't store per-trial game outcomes, only `mean_prob`
- Any backend changes — this spec is UI-only
- Changing the model spread or ATS pick display

---

## Dependencies

Must ship after `2026-06-01-dynamic-mc-simulation-design.md` is implemented and deployed, since the `explanation.source` field and simulation-derived `pred_prob` values require that backend work.
