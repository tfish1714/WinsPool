# WinsPool Prediction Model — How It Works

This document explains how the ensemble model scores every feature for every game, and how those scores become a win probability. It covers both paths: the **in-season path** (weeks with real game data) and the **preseason path** (week 1 projections built from prior-season profiles).

---

## High-Level Architecture

```
rawdata/ (nflverse CSVs)
    │
    ▼
nn_feature_engine.py          ← builds the 26-feature row for each game
    │
    ├── NNPredictionService   ← TensorFlow feedforward NN (45% weight)
    ├── XGBPredictionService  ← XGBoost gradient-boosted trees (20% weight)
    └── LRPredictionService   ← ElasticNet logistic regression (35% weight)
                │
                ▼
        blended probability = 0.45·NN + 0.20·XGB + 0.35·LR
                │
                ▼
        home win probability → predicted winner, model spread, edge vs Vegas
```

There are **two separate code paths** depending on whether game data exists for the week being predicted.

---

## Path 1 — In-Season (Completed or Current-Week Games)

Used by: `backfill_schedule_predictions.py`, `generate_weekly_predictions.py`, `weekly_model_eval.py`

### Step 1: Build the Master Feature Table

`build_master_feature_table()` in `nn_feature_engine.py` loads all rawdata CSVs and assembles one row per game. Every stat is computed from data available *before kickoff* — rolling/expanding means are shifted by one game to prevent leakage.

### Step 2: Run the Ensemble

```python
X = feature_table[FEATURE_COLUMNS].values  # shape (N_games, 26)

nn_prob  = NN.predict(scaler.transform(X))          # sigmoid output
xgb_prob = XGB.predict_proba(scaler.transform(X))   # calibrated via StandardScaler
lr_prob  = LR.predict_proba(scaler.transform(X))    # ElasticNet logistic

blended  = clip(0.45·nn + 0.20·xgb + 0.35·lr, 0.02, 0.98)
```

### Step 3: Derive Outputs

```
home_win_prob  = blended
model_spread   = 7.5 × log(p / (1−p))      ← logit scaled to point spread
vegas_spread   = spread_line from nflverse schedule
edge_vs_vegas  = model_spread − vegas_spread  (+ = model likes home more than Vegas)
confidence     = max(home_win_prob, 1−home_win_prob) × 100, capped at 99%
```

---

## Path 2 — Preseason / Week 1 (No Game Data Yet)

Used by: `NNProjectionEngine` in `nn_projection_engine.py`, called by `backfill_schedule_predictions.py` and the API's schedule endpoint. This builds **individual game** predictions (e.g. one row for "Week 1 KC @ LAC"). The **season win-total** projections shown on the draft board and standings (`preseason_predictions`, `mean_wins`/`std_dev`/percentiles) come from a separate, more involved mechanism — see "Season Win Projection" near the end of this doc.

When `snap_counts_{season}.csv` is empty (season hasn't started), the engine builds **team profiles** from prior-season averages instead of game rows.

### Step 1: Build Team Profiles

For each team, average every feature column across all of their games in the prior season (e.g., 2025 average for 2026 Week 1 predictions).

```python
feature_table = build_master_feature_table(min_season=2020, max_season=season-1)
# → average home + away games per team for season-1
team_profile[team][feature] = mean of that feature across all 2025 games
```

### Step 2: Preseason Trench Override

Because `trench_dominance_metric` is built from snap counts + DL stats that reflect the *old roster*, a 2026 preseason override uses actual 2026 roster files + 2025 individual player performance:

```python
preseason_roster = compute_preseason_roster_features(2026, rawdata_dir)
# → {team: {"ol_av": float, "dl_perf": float}}

# Normalize against league-wide distribution for this preseason
ol_mu, ol_sig = mean/std of ol_av across all 32 teams
dl_mu, dl_sig = mean/std of dl_perf across all 32 teams

trench[home] = z(home_ol) + z(home_dl)
trench[away] = z(away_ol) + z(away_dl)
game_trench  = trench[home] - trench[away]
```

OL score = Σ(offense_snaps × age_multiplier) per OL player on 2026 roster matched to 2025 snaps.
DL score = Σ((sacks×6 + qb_hits×1 + pressures×1.5) × age_multiplier) per DL player.
Rookies with no prior-season snap data get `position_median × 0.5`.

### Step 3: Assemble Game Features

```python
for col in FEATURE_COLUMNS:
    if col == "home_flag":
        features[col] = 1.0
    elif col == "trench_dominance_metric":
        features[col] = preseason_trench_delta   # override above
    elif col in delta_features:
        features[col] = home_profile[col] - away_profile[col]
    else:
        features[col] = home_profile[col]        # home team's average
```

### Step 4: Run the Same Ensemble

Same three models, same blend. The only difference is *where the features come from*.

---

## The 26 Features

Every feature is from the **home team's perspective** unless noted. Delta features are `home − away`.

### Group 1 — Elo Power Ratings (2 features)

| Feature | Source | What It Measures |
|---------|--------|-----------------|
| `tm_elo_pre` | `rawdata/elo_computed.csv` (computed by `scripts/compute_elo.py`) | Home team's pre-game Elo rating. League average = 1500. |
| `opp_elo_pre` | same | Away team's pre-game Elo. |

**How it's computed:** Elo is calculated from scratch using the Reddit r/nfl methodology (`scripts/compute_elo.py`), not sourced from FiveThirtyEight or nflverse. Every team starts at 1500 in their first recorded season. After each game the ratings are updated:

```
K  = 12                          # base update factor
HFA = 15 points                  # home field advantage added to home Elo before computing E
MoV multiplier = log(margin+1) / 2.5, clamped [0.5, 2.0]

E_home  = 1 / (1 + 10^(-(home_elo + HFA - away_elo) / 400))
delta   = K × MoV_mult × (actual − E_home)
home_elo_post = home_elo + delta
away_elo_post = away_elo − delta

Off-season regression: elo = elo + (1500 − elo) / 3   ← pulls toward mean each spring
```

If quarter-score data is available (`rawdata/quarter_scores.csv` from `scrape_quarter_scores.py`), the model also applies K_q=3 updates after each quarter, discounting garbage-time quarters (leading by 17+) by 0.1× to reduce noise.

The output `elo_computed.csv` is one row per regular-season game with `home_elo_pre` and `away_elo_pre` (ratings before the game). Merged on `(season, week, home_team, away_team)`. Falls back to 1500 for any game with no Elo data.

Higher `tm_elo_pre` and lower `opp_elo_pre` both push toward a home win. The difference between these two is the single strongest predictor in the ensemble.

---

### Group 2 — EPA (Expected Points Added) per Play (4 features)

Rolling expanding mean of per-play EPA from nflverse team stats, shifted by 1 game to prevent leakage.

| Feature | What It Measures |
|---------|-----------------|
| `off_pass_epa` | Home team's passing efficiency (EPA/pass play, prior games this season) |
| `def_pass_epa` | Away team's passing efficiency when they have the ball (used as home defense quality) |
| `off_rush_epa` | Home team's rushing efficiency |
| `def_rush_epa` | Away team's rushing efficiency |

**How it's scored:**
```
off_pass_epa for Week N = mean(EPA/pass play in Weeks 1..N-1)
```
Positive = above average (good for home team). Zero = league average. Raw scale is roughly −0.2 to +0.3.

`def_pass_epa` and `def_rush_epa` represent the *opponent's* offense — a high value here means the away team moves the ball well, which is bad for the home team.

---

### Group 3 — Rolling Box Stats (2 features)

From `stats_team_week_*.csv`, shifted by 1 game.

| Feature | What It Measures |
|---------|-----------------|
| `turnover_margin_rolling` | (Away team turnovers − Home team turnovers), regressed 50% toward zero |
| `net_success_rate` | (Home first downs − Away first downs) rolling average |

**Turnover regression:** Raw turnover margin is multiplied by 0.50 because turnovers are ~50% luck. This prevents the model from over-indexing on fluky turnover streaks.

**How it's scored:**
```
raw_to = opp_turnovers_roll - tm_turnovers_roll
turnover_margin_rolling = raw_to × 0.50
```
Positive = home team has been getting more takeaways than giving away.

---

### Group 4 — Rolling Point Differential (2 features)

| Feature | What It Measures |
|---------|-----------------|
| `tm_point_diff` | Home team's expanding-mean score margin this season (prior games only) |
| `opp_point_diff` | Away team's expanding-mean score margin this season |

Week 1 fallback: uses prior-season average. Zero-filled if no history.

---

### Group 5 — Synthetic / Composite Features (6 features)

| Feature | Formula | What It Measures |
|---------|---------|-----------------|
| `early_down_pass_epa` | Rolling mean of home team's 1st & 2nd down pass EPA | Scheme efficiency on early downs — a leading indicator of offensive success |
| `net_success_rate` | Home first downs − Away first downs (rolling) | Sustained drive efficiency vs. the opponent |
| `elo_confidence` | `|tm_elo_pre − opp_elo_pre| / 25` | How certain the Elo model is — larger mismatches amplify Elo's influence |
| `market_implied_team_total` | `total_line / 2` | Expected scoring pace (from Vegas over/under). Higher = faster-paced game |
| `passing_difficulty_index` | Composite of defensive pressure metrics | How hard it is to pass against this defense |
| `travel_rest_disadvantage` | `(home_rest − away_rest) + travel_miles / 1500` | Positive = home team has more rest + away team travelled farther. Both favor home. |

**Travel rest detail:** `home_rest` and `away_rest` come from nflverse schedule (days since last game). Travel distance is straight-line miles between franchise city centroids. Divided by 1500 to normalize to the same scale as a rest-day advantage.

---

### Group 6 — Trench Dominance (1 feature)

| Feature | What It Measures |
|---------|-----------------|
| `trench_dominance_metric` | Home OL + DL quality vs. Away OL + DL quality (z-scored composite) |

**In-season computation:**

```
OL score (team, season) = Σ over OL players: offense_snaps × age_multiplier
DL score (team, season) = Σ season sacks × 6 + qb_hits × 1 + tackles_for_loss × 1

Per-season z-score:
  ol_z = (team_ol - season_mean_ol) / season_std_ol
  dl_z = (team_dl - season_mean_dl) / season_std_dl
  team_trench = ol_z + dl_z

game_trench = home_team_trench - away_team_trench
```

OL uses snap counts from 2012+. DL uses team-level weekly stats from 2020+. Both are zero for seasons before their data coverage starts.

**Age multipliers applied to OL snap scores:**

| Age | Multiplier |
|-----|-----------|
| < 24 | 1.05 (growth phase) |
| 24–27 | 1.00 (prime) |
| 27–30 | 1.0 − 0.02 × (age − 27) (mild decay) |
| 30+ | 1.0 − 0.04 × (age − 27), floor 0.3 |
| 30+ skill position (RB/WR/CB/S) | 0.06/yr decay |

**Preseason computation:** Same formula but uses 2026 roster × 2025 individual player data, normalized against the 32-team league distribution for that preseason.

---

### Group 7 — Roster Talent (1 feature)

| Feature | What It Measures |
|---------|-----------------|
| `roster_talent_delta` | Home team roster grade − Away team roster grade (performance-based) |

**Computation (in-season):**
```
off_raw = passing_epa + rushing_epa + (pass_tds + rush_tds)×2 − interceptions×3
def_raw = sacks×1.5 + interceptions×2.5 + tfl×0.5

For each team at each week:
  grade = cum_off_z × 0.6 + cum_def_z × 0.4   (z-scored within week across all teams)
  
roster_talent_delta = home_grade − away_grade
```

The cumulative average is shifted by 1 week (no leakage). Week 1 grade = 0 (no prior data). Available from 2020+ (requires team stats data).

---

### Group 8 — QB Pressure & Pass Rush (4 features)

From `pfr_advstats` (2018+). Rolling mean shifted by 1 game.

| Feature | What It Measures |
|---------|-----------------|
| `qb_pressure_rate` | How often the home QB gets pressured (fraction of dropbacks) |
| `opp_qb_pressure_rate` | How often the away QB gets pressured |
| `def_pressure_gen` | Home defense's pressure generation rate |
| `opp_def_pressure_gen` | Away defense's pressure generation rate |

High `opp_qb_pressure_rate` = away QB is under pressure often = good for home. High `def_pressure_gen` = home defense is disruptive = good for home.

Zero-filled for seasons before 2018.

---

### Group 9 — QB Injury Flag (1 feature)

| Feature | What It Measures |
|---------|-----------------|
| `qb_injury_flag` | +1 if away QB starter is out, −1 if home QB starter is out, 0 otherwise |

**Computation:** Uses snap-count-based starter detection. A QB is flagged as "starter" if they took >50% of snaps in the prior week. If that QB appears on the injury report as "Out" or "Doubtful", the flag fires.

```
qb_injury_flag = away_qb_out − home_qb_out
```

Positive = home team advantage (away QB is missing). Negative = home team disadvantaged.

---

### Group 10 — Roster Value (4 features)

From `roster_value_service.py`. EPA-based WAR proxy.

| Feature | What It Measures |
|---------|-----------------|
| `off_roster_value_delta` | Home offensive roster strength − Away (alpha-blended season-to-date) |
| `def_roster_value_delta` | Home defensive roster strength − Away |
| `st_value_delta` | Home special teams value − Away |
| `qb_resilience_delta` | Home QB's ability to perform under pressure − Away QB |

These use an alpha-blending approach: early in the season, the prior-year average dominates; as games accumulate, current-season performance takes over.

---

### Group 11 — Contextual Flags (6 features)

| Feature | Values | What It Measures |
|---------|--------|-----------------|
| `home_flag` | Always 1.0 for home team row | Raw home-field advantage baseline |
| `div_game_flag` | 0 or 1 | Division game indicator — these tend to be closer |
| `surface_type` | 0 = grass, 1 = turf | Turf slightly favors speed/passing teams |
| `is_dome_flag` | 0 or 1 | Dome games remove weather as a factor |
| `playoff_flag` | 0 or 1 | Postseason indicator (different competitive dynamics) |
| `week` | 1–22 | Raw week number — captures early-season vs. late-season patterns |

---

## The Three Models

### Neural Network (45% weight) — `nn_v11.keras`

```
Architecture: Input(26) → Dense(48, ReLU, L2=1e-4) → Dropout(0.4)
                        → Dense(24, ReLU, L2=1e-4) → Dropout(0.4)
                        → Dense(1, Sigmoid)

Optimizer: Adam, lr=0.0005
Loss: Binary cross-entropy
Early stopping: patience=20, monitor=val_accuracy
```

**Training split (chronological — no random shuffling):**
- Train: seasons 2006–2024 (~4,900 games)
- Validation: 2025 weeks 1–14 (~180 games)
- Test: 2025 weeks 15–18 (~64 games)

**Why a small network with high dropout?** NFL datasets are ~5k rows. Larger networks (128+ units) memorize training data and fail to generalize. The 48-24 architecture with 0.4 dropout achieves ~69% validation accuracy while keeping test accuracy consistent.

**What the NN does well:** Captures non-linear interactions between features (e.g., Elo difference matters more when travel disadvantage is also high).

**2025 season metrics:** Train acc 66.4%, Val acc 69.2%, Test acc 56.3%, Season R² 0.56

---

### XGBoost (20% weight) — `xgb_v5.json`

```
Trees: 404 (early stopped from 1000 max)
Max depth: 3 (shallow — prevents overfitting)
Learning rate: 0.02
Subsample: 0.70 per tree
Column sample: 0.60 per tree
Min child weight: 20 (~0.4% of training data per leaf)
L1: 0.5, L2: 2.0 (strong regularization)
```

**Top features by importance (XGB v5):**
1. `roster_talent_delta` — 12.8%
2. `opp_elo_pre` — 6.5%
3. `tm_elo_pre` — 6.1%
4. `tm_point_diff` — 5.4%
5. `trench_dominance_metric` — 4.5%

**What XGB does well:** Picks up threshold effects and non-monotonic relationships. Lower weight (20%) because it tends to overfit more than LR on small samples.

**2025 season metrics:** Train acc 70.6%, Test acc 56.3%, Season R² 0.71

---

### Logistic Regression (35% weight) — `lr_v3.pkl`

```
Type: ElasticNet LogisticRegressionCV
Regularization: C=0.05 (strong), l1_ratio=0.70 (mostly L1 sparsity)
Solver: SAGA (supports ElasticNet)
Cross-validation: 5-fold on training data to select C and l1_ratio
```

**Top coefficients (LR v3):**

| Feature | Coefficient | Direction |
|---------|-------------|-----------|
| `opp_elo_pre` | −0.340 | Higher away Elo → away wins |
| `tm_elo_pre` | +0.274 | Higher home Elo → home wins |
| `trench_dominance_metric` | +0.207 | Home trench edge → home wins |
| `qb_injury_flag` | +0.136 | Away QB out → home wins |
| `tm_point_diff` | +0.108 | Home margin edge → home wins |

**What LR does well:** Maximally resistant to overfitting. Acts as a calibration anchor — when NN and XGB are overconfident on a game, LR pulls the probability toward the base rate (~55% for home teams). This is why it gets the highest weight (35%) despite lower raw accuracy.

**2025 season metrics:** Train acc 65.6%, Test acc 56.3%, Season R² 0.66

---

## Ensemble Blending

```python
blended = clip(0.45·nn + 0.20·xgb + 0.35·lr, 0.02, 0.98)
```

The floor/ceiling at 2%/98% prevents the model from ever claiming certainty. In practice, probabilities range from ~35% to ~75% for most games.

**Why these weights?** Determined empirically on 2025 season holdout data. LR gets the highest weight because it's the best-calibrated (probabilities closest to actual win rates). NN gets the second-highest because it captures non-linear interactions. XGB gets the lowest because its tree structure tends to produce overconfident probabilities on small datasets.

**2025 full-season ensemble performance:** 178/272 correct (65.4%), Brier score ~0.21, Season R² 0.56

---

## Season Win Projection (Monte Carlo Simulation)

Used by `NNProjectionEngine.simulate_season()`, called by `predict_season.py`, `cache_builder.py`, `scripts/walk_forward_validate.py`, and the draft board. This produces the `mean_wins`/`median`/`std_dev`/`p5`/`p25`/`p75`/`p95` win-distribution numbers players actually see — there is no separate season-wins model and no power-rating blend; every game inside the simulation uses the same 45/20/35 ensemble as Path 1/2 above (`_batch_predict`).

### Step 1: Seed initial team state from preseason player profiles

`initialize()` builds each team's starting Elo/EPA/margin state from **player-level** preseason profiles (`compute_preseason_player_profiles()` in `nn_feature_engine.py`) — a materially more detailed computation than Path 2's team-level `compute_preseason_roster_features()` override above. It blends up to `DL_BLEND_SEASONS` (3) prior seasons per player across **every** position group (QB/WR/TE/RB, OL, DL, LB, CB/S), weighted by recency (`DL_BLEND_RECENCY_WEIGHTS = [0.55, 0.30, 0.15]`, most-recent first) × reliability (that season's snap/attempt/target/carry share of a full role, via the `FULL_SEASON_*` constants). A season below a minimum-sample threshold (e.g. `DL_MIN_SNAPS_TRUSTED`, and equivalent per-position volume gates on the offensive side) is **excluded from the blend entirely**, not merely down-weighted — a genuinely tiny sample (a backup's handful of pass attempts, a few defensive snaps) can otherwise produce a wildly noisy rate that still pollutes the blend once every available season for that player is similarly small. An additional age-scaled "return risk" haircut applies when the *most recent* season specifically was injury-shortened, so a healthy multi-year track record isn't erased by one bad year — while a genuine current-season breakout still dominates, since it's both most recent and normal-volume.

### Step 2: Profile composite → Elo boost

Each of the 7 `PRESEASON_ELO_WEIGHTS` profile dimensions (`qb_tier`, `off_pass_epa`, `def_pass_epa`, `dl_perf`, `ol_av`, `off_rush_epa`, `def_rush_epa` — defensive EPA dims sign-flipped so a better defense scores positively) is individually z-scored across the league, combined into a weighted composite, and then **the composite itself is re-z-scored to std=1** before being clipped to ±2σ and scaled to `PRESEASON_ELO_BOOST_MAX` (±200 Elo). Averaging several partially-correlated z-scored signals mathematically compresses variance below any single input; without this second normalization the clip almost never engages, silently narrowing the league's whole preseason spread well below its intended range.

### Step 3: Walk the schedule via Monte Carlo

State (Elo + 4 EPA dims + margin) is tiled across N Monte Carlo trials, then for each week in schedule order: batch-predict every game across every trial with the ensemble, convert the resulting probability to an implied point margin, sample `Normal(implied, MC_MARGIN_STD)`, increment wins on the sampled margin's sign, and **update Elo/EPA state in place** so later weeks see the simulated record. The Elo update specifically uses `ELO_SIM_K`/`ELO_SIM_HFA`/`ELO_SIM_MOV_MIN`/`ELO_SIM_MOV_MAX` (matching `scripts/compute_elo.py`'s real-game methodology exactly) — **not** the separate `ELO_K`/`ELO_HOME_ADVANTAGE` constants used by the in-season path elsewhere in this file, which belong to an independently-calibrated Elo+Pythagorean engine. Win distributions across all trials give the final `mean_wins`/`median`/`std_dev`/`p5`/`p25`/`p75`/`p95`.

### Validation caveat

`scripts/walk_forward_validate.py` (an out-of-sample MAE diagnostic, not a production path — scores the ensemble against seasons it never trained on) can't exercise this preseason-profile branch for most historical folds: `initialize()` only takes it when the target season's `snap_counts_{season}.csv` is missing, which is never true for a completed historical season, and pre-2025 depth-chart files use an incompatible schema this pipeline can't read. `scripts/walk_forward_diagnose_preseason_path.py` forces this specific path for the 2025 fold (the one year both conditions can be worked around) as a one-off check; there is no standing automated regression coverage for this code path otherwise.

### Known model/consensus divergences

Some model-vs-analyst-consensus disagreement is expected and not itself a bug —
the whole point of the separate `consensus_projections` Firestore collection
(analyst consensus, distinct from `preseason_predictions`'s model output — see
CLAUDE.md's Data Flow & Caching table) is to let the two disagree and be
compared. A few 2026 cases were investigated by hand after the position-group
blending fix (`e5e48db`) rather than left unexamined. `scripts/rank_position_groups.py`
(added 2026-08-19) automates the trace used below — it reproduces, per team,
the exact per-dimension z-scores/ranks and composite that Step 2 computes, as
CSV, so a surprising projection can be checked with one command instead of
re-deriving the math by hand each time:

- **MIA** — the starting QB's `qb_tier`/`off_pass_epa` contribution was being
  pulled from a prior season with an unrepresentatively small pass-attempt
  sample, inflating the rate. Fixed by the `min_volume=100` floor on the QB's
  `_blended_rate(...)` call (`nn_feature_engine.py`, the same per-season
  inclusion-threshold mechanism described in Step 1 above, applied here to
  pass attempts specifically) — a season with fewer than 100 attempts is
  excluded from the QB blend entirely rather than diluting it. MIA's QB
  dimensions sit near league average post-fix; any remaining MIA-vs-consensus
  gap is driven by other inputs (rushing offense, pass defense), not the QB.
- **ARI, IND** — investigated, no specific cause identified. Treated as
  legitimate model/consensus disagreement rather than a bug.
- **ATL** (2026-08-19) — model projected ~11.9 mean wins vs. consensus ~6.9,
  a ~5-win gap. Traced with `rank_position_groups.py --season 2026 --team ATL`:
  ATL ranks #1/32 on `dl_perf` and #3/32 on `def_pass_epa`, both driven by
  real, verified 2025 box-score production (Brandon Dorlus 8.5 sacks, James
  Pearce Jr. 10.5 sacks as a promoted SLB edge rusher, LaCale London 5.0 sacks
  — not a data or join bug; `LaCale London` is a real DL, distinct from
  Drake London the WR, confirmed against `rosters_2026.csv`/`depth_charts_2026.csv`).
  `off_pass_epa` is #29/32 (weak passing offense), but `PRESEASON_ELO_WEIGHTS`'
  combined defense/trench weight (`dl_perf` 0.15 + `ol_av` 0.10 + `def_pass_epa`
  0.20 + `def_rush_epa` 0.02 = 0.47) is enough to produce a #3/32 composite and
  a near-max Elo boost anyway. This is the composite re-normalization from
  Step 2 (`e5e48db`, 2026-08-15) working as designed, not a bug — but it's an
  open modeling-judgment question whether trench/defense dims are weighted too
  heavily relative to a strong passing offense/QB tier. Not changed as part of
  this investigation; a candidate follow-up is backtesting `PRESEASON_ELO_WEIGHTS`
  against historical win outcomes to check if the current split is well-calibrated.

---

## Data Sources by Feature Group

| Feature Group | Source File(s) | Coverage |
|---------------|---------------|----------|
| Elo | `rawdata/elo_computed.csv` (computed by `scripts/compute_elo.py`) | All seasons with schedule data |
| EPA (pass/rush) | `rawdata/stats_team/stats_team_week_*.csv` | 1999–present |
| Box stats (turnovers, first downs) | same | 1999–present |
| Point differential | computed from schedule scores | 1999–present |
| QB pressure | `rawdata/pfr_advstats/advstats_week_pass_*.csv` | 2018–present |
| OL snap counts | `rawdata/snap_counts/snap_counts_*.csv` | 2012–present |
| DL performance | `rawdata/stats_team/stats_team_week_*.csv` | 2020–present |
| Roster value | `rawdata/rosters/`, `rawdata/snap_counts/` | 2000–present |
| Travel/rest | `rawdata/schedules/games.csv` + city coordinates | all years |
| QB injury | `rawdata/injuries/injuries_*.csv` | 2009–present |
| Contextual flags | `rawdata/schedules/games.csv` | all years |

Features with limited historical coverage default to 0 for out-of-range seasons. This means predictions for early seasons (pre-2012) rely more heavily on Elo and EPA.

---

## Adding or Changing a Feature

1. Add the computation to `nn_feature_engine.py` → `build_master_feature_table()`
2. Add the column name to `FEATURE_COLUMNS` in the same file
3. Re-run `python scripts/train_nn_model.py` (auto-increments version)
4. Re-run `python scripts/train_xgb_model.py` and `python scripts/train_lr_model.py`
5. Evaluate: `python scripts/weekly_model_eval.py --season 2025 --week 1 18 --no-save`
6. If metrics improve: `python scripts/backfill_schedule_predictions.py --force --firestore`

Removing a feature follows the same steps. The models are always retrained from scratch on the full feature set — there is no fine-tuning.
