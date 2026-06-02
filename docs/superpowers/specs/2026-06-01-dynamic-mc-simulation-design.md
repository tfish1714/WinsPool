# Dynamic Monte Carlo Simulation — Design Spec
**Date:** 2026-06-01  
**Status:** Approved for implementation

---

## Problem

The 2026 preseason projected win totals span only 7–10 wins across all 32 teams (real NFL seasons span ~3–15). Two root causes:

1. **`predict_season.py` uses a broken profile builder** that averages relative/differential features (e.g. `elo_diff`) across all matchups per team, collapsing every team toward the league mean.
2. **Static profiles throughout the MC simulation** — all 17/18 games use the same prior-season team snapshot, so momentum (win streaks, slumps) has no effect on later-week predictions.

Additionally, the preseason projection (`predict_season.py`) and per-game schedule predictions (`backfill_schedule_predictions.py`) use different code paths, producing results that aren't consistent with each other.

---

## Requirements

1. Projected season win totals must have a realistic distribution (~3–15 win spread, std dev ~2.5+).
2. Predictions use the same method regardless of preseason vs. in-season context.
3. Team state (Elo + EPA) updates after each simulated game within each MC trial, so win streaks raise a team's projected strength for later weeks.
4. In-season runs use actual game results (real margins from `nfl_games`) for completed weeks; future weeks simulate forward from that real state.
5. Per-game schedule predictions for future weeks reflect the simulated team trajectory, not static prior-season profiles.
6. All existing displayed fields are preserved: `pred_prob`, `model_spread`, `edge_vs_vegas`, `ats_pick`, `pred_su_conf`, explanation features.

---

## Architecture

### Single Engine, Two Callers

`NNProjectionEngine.simulate_season()` becomes the single prediction engine for all contexts. Both callers become thin wrappers around it.

```
predict_season.py                →  NNProjectionEngine.simulate_season()
backfill_schedule_predictions.py →  NNProjectionEngine.simulate_season()
```

The unified path eliminates the `if preseason / else in_season` branching. The only difference between contexts is what goes into the starting state and `completed_results`.

### Distribution Fix

`NNProjectionEngine._build_team_profiles()` already stores **absolute** per-team values (Elo, off/def EPA, margin) extracted separately for home and away appearances, then averaged. This is correct — team-relative features are computed at matchup time as `home_value - away_value`. The fix is to route `predict_season.py` through this engine rather than its own broken `_build_team_profiles()`.

---

## Team State

Per-team mutable state tracked across weeks within each MC trial:

| Field | Description | Initial value |
|---|---|---|
| `elo` | Absolute Elo rating | Prior-season avg `elo_pre` from team profiles |
| `off_pass_epa` | Offensive passing EPA rolling avg | From team profiles |
| `off_rush_epa` | Offensive rushing EPA rolling avg | From team profiles |
| `def_pass_epa` | Defensive passing EPA rolling avg | From team profiles |
| `def_rush_epa` | Defensive rushing EPA rolling avg | From team profiles |
| `margin_roll` | Rolling point differential | From team profiles |

Shape in memory: `state[n_sims, n_teams, 6]` — all trials start identical from the initial state.

**Both preseason and in-season runs always start from the prior-season baseline.** For in-season runs, `completed_results` carries the actual margins for all played games; the simulation applies those deterministically as it processes weeks in order, naturally rebuilding the current team state before simulating any future weeks. There is no separate "current season" initialization path.

---

## Update Formulas

### After each simulated game (future games only):

**1. Sample margin**
```
implied_spread = SPREAD_TO_PROB_SCALE × log(p / (1 - p))
margin ~ Normal(implied_spread, σ=13.0)
```
- Positive margin → home team wins; negative → away team wins.
- σ=13.0 matches real NFL game-to-game variance.
- The sign of the sampled margin determines the winner for that trial (integrating win probability and margin in one draw).

**2. Elo update**
Reuse `compute_elo_shift()` from `prediction_service.py`:
```
shift = K × (1 - P(winner)) × MoV_multiplier(|margin|, elo_diff)
winner_elo += shift
loser_elo  -= shift
```
K=20, home advantage=48 Elo points (existing constants).

**3. EPA update**
Small additive nudge proportional to simulated margin:
```
epa_delta = |margin| × 0.004

winner: off_pass_epa += epa_delta
        off_rush_epa += epa_delta × 0.5   (rushing has lower weight)
        def_pass_epa += epa_delta
        def_rush_epa += epa_delta × 0.5

loser:  same fields -= respective deltas
```
Scale 0.004 keeps updates small — a 7pt win nudges EPA by ~0.028. Prior-season baseline dominates early in the simulated season; momentum accumulates gradually over multiple weeks.

### Completed games (actual results):
Real margins from `nfl_games` (`home_score - away_score`) are applied **deterministically** across all trials — every trial gets the same Elo/EPA update, since the actual result is known.

---

## `simulate_season()` Method

```python
def simulate_season(
    self,
    schedule_df: pd.DataFrame,
    n_sims: int = 10_000,
    completed_results: dict = None,  # {game_key: margin}  game_key = "W{wk:02d}_{ht}_{at}"
) -> dict:
    """
    Returns:
        {
          "team_stats": {
              team: {median_wins, mean_wins, std_dev, p5, p25, p75, p95}
          },
          "game_probs": {
              game_key: {mean_prob, model_spread, home_team, away_team, week}
          }
        }
    """
```

### Algorithm

1. Extract initial state from `self._team_profiles` → `state[n_sims, n_teams, 6]`
2. Group games by week, sort weeks ascending
3. **For each week:**
   - **Completed games** (keys in `completed_results`): apply real margin deterministically to all trials; update `state`; no probability recording needed.
   - **Future games** (remaining games in the week, processed as a batch):
     - Collect all future games this week → `G` games
     - Build feature matrix `(G × n_sims, 26)`: dynamic fields (elo_diff, pass/rush/early_down epa matchups, point_diff_advantage) from current `state`; static fields (travel, trench, market_total, rest=0, qb_injury=0) from prior-season profiles
     - Batch-infer NN, XGB, LR on the full matrix → reshape to `(G, n_sims)` → blend → `probs[G, n_sims]`
     - For each game `g` in the batch:
       - `mean_prob = mean(probs[g])`, `model_spread = 7.5 × log(mean_prob / (1 - mean_prob))`
       - Store in `game_probs[key]`
       - Per-trial implied spreads: `implied[n_sims] = 7.5 × log(probs[g] / (1 - probs[g]))`
       - Sample margins: `margins[n_sims] ~ Normal(implied[n_sims], 13.0)`
       - Update `win_matrix` and `state` per trial based on margin sign
4. Aggregate `win_matrix` → `team_stats`

### Performance

All future games within a week are batched into a single model call `(n_games_this_week × n_sims, 26)`. This yields `n_weeks` batch calls per model rather than `n_games × n_sims` individual calls. Estimated runtime: 8–12 seconds for 18 weeks × 10,000 simulations on CPU.

---

## Changes to `predict_season.py`

**Remove:** `_build_team_profiles()`, `_compute_game_probs()`, `_run_monte_carlo()`

**Replace main() with:**
```python
engine = NNProjectionEngine()
engine.initialize(season)
results = engine.simulate_season(schedule_df, n_sims=args.simulations)
# format and upload results["team_stats"] to preseason_predictions (same schema as today)
```

The Firestore upload schema is unchanged: `projected_wins`, `mean_wins`, `std_dev`, `floor`, `p25`, `p75`, `ceiling`.

---

## Changes to `backfill_schedule_predictions.py`

**Remove:** `_profile_predictions_for_year()`

**Replace with:** call to `simulate_season()` with `completed_results` built from `nfl_games` actual results for the target season:

```python
# completed_results: {game_key: margin} from actual nfl_games scores
completed = {
    f"W{int(row.week):02d}_{row.home_team}_{row.away_team}": float(row.result)
    for _, row in games_df.iterrows()
    if pd.notna(row.result) and row.season == year
}
results = engine.simulate_season(schedule_df, n_sims=10_000, completed_results=completed)
```

For each future game in `results["game_probs"]`, derive stored fields:
```
pred_prob      = mean_prob
pred_winner    = home_team if mean_prob >= 0.5 else away_team
pred_su_conf   = round(max(mean_prob, 1-mean_prob) * 100, 1)
model_spread   = game_probs[key]["model_spread"]
edge_vs_vegas  = model_spread - vegas_line  (if available)
ats_pick       = home if model_spread > vegas_line else away
explanation.source = f"mc_simulation ({n_sims} trials)"
```

Locked/played game logic unchanged — completed games continue to use actual feature-table predictions.

---

---

## Files Changed

| File | Change |
|---|---|
| `services/nn_projection_engine.py` | Add `simulate_season()`, update `initialize()` |
| `scripts/predict_season.py` | Remove internal profile/MC logic; call engine |
| `scripts/backfill_schedule_predictions.py` | Remove `_profile_predictions_for_year()`; call engine |

No changes to routes, templates, Firestore schema, or frontend — existing fields are preserved with more accurate values.

---

## Out of Scope (separate spec)

- UI changes to show win probability distributions or uncertainty bands for future weeks
- Retraining models with new features
- Changing ensemble blend weights
