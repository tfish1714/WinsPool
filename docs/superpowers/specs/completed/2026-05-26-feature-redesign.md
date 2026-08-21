# Feature Engineering Redesign Spec

**Date:** 2026-05-26  
**Author:** Thomas Fischer  
**Status:** FINAL v2 — ready for plan + implementation

---

## 1. Design Principles

Every feature in `FEATURE_COLUMNS` must follow these rules after the redesign:

1. **Signed differential** — positive always means home-team advantage, negative means away-team advantage. No raw per-team scalars sitting next to their mirror (e.g., `tm_elo_pre` + `opp_elo_pre`).
2. **Symmetric across roles** — the feature must be meaningful whether team A is home or away. No home-only features with no away counterpart.
3. **Performance-based, not volume-based** — volume proxies (snap count) must be replaced with actual performance measures where those are available.
4. **No constant features** — a feature that takes the same value for every row contributes zero information.
5. **Explicit over implicit** — home field advantage, QB health, and game context should be explicitly encoded rather than left for the model to discover from label patterns alone.

---

## 2. Current Inventory & Issues

32-feature set in `services/nn_feature_engine.py:FEATURE_COLUMNS`.

| # | Name | Current Formula | Problem |
|---|------|----------------|---------|
| 1 | `tm_elo_pre` | `home_elo_pre` from `elo_computed.csv` | Paired scalar; model must discover the diff itself |
| 2 | `opp_elo_pre` | `away_elo_pre` from `elo_computed.csv` | See #1 |
| 3 | `off_pass_epa` | Rolling `passing_epa / attempts` for home team | Correct metric; wrong pairing with #4 |
| 4 | `def_pass_epa` | Rolling `passing_epa / attempts` for **away** team — **MISLABELED** | Named "def" but is opponent's *offense*, not the home team's defensive quality |
| 5 | `off_rush_epa` | Rolling `rushing_epa / carries` for home team | See #6 |
| 6 | `def_rush_epa` | Rolling `rushing_epa / carries` for **away** team — **MISLABELED** | Same mislabeling as #4 |
| 7 | `turnover_margin_rolling` | `(opp_to − tm_to) × 0.5` | ✅ Already signed diff; keep |
| 8 | `tm_point_diff` | Home team's rolling avg score margin | Paired scalar; model must discover the diff |
| 9 | `opp_point_diff` | Away team's rolling avg score margin | See #8 |
| 10 | `early_down_pass_epa` | `pass_epa_play × 0.8 + cpoe × 0.05` for **home team only** | Asymmetric — no away counterpart; and pass-only ignores runs on early downs |
| 11 | `net_success_rate` | `tm_first_downs_roll − opp_first_downs_roll` | ✅ Already signed diff; keep |
| 12 | `elo_confidence` | `\|tm_elo − opp_elo\| / 25` | ✅ Keep; recalculate from new `elo_diff` |
| 13 | `market_implied_team_total` | `total_line / 2` | ✅ Game context; keep |
| 14 | `passing_difficulty_index` | `wind × 1.5 + max(0, 40 − temp)` | ✅ Game context; keep |
| 15 | `travel_rest_disadvantage` | `(home_rest − away_rest) + haversine(away→home) / 1500` | **Three problems:** (a) neutral/international games — formula still computes stadium-to-stadium distance when both teams flew to a third location; (b) only measures away travel, not net difference; (c) rest (days) and distance (miles/1500) on incompatible scales, summed with equal weight |
| 16 | `trench_dominance_metric` | OL: `snap_count × age_mult` (volume); DL: `sacks×6 + qb_hits×1 + tfl×1` | OL component is **volume** (more snaps played ≠ better play). DL misses run defense entirely |
| 17 | `roster_talent_delta` | `home_grade − away_grade` | ✅ Signed diff; keep |
| 18 | `qb_pressure_rate` | Rolling `times_pressured_pct` for home QB | Paired scalar |
| 19 | `opp_qb_pressure_rate` | Rolling `times_pressured_pct` for away QB | See #18 |
| 20 | `def_pressure_gen` | Rolling `def_pressures` total for home defense | Paired scalar |
| 21 | `opp_def_pressure_gen` | Rolling `def_pressures` total for away defense | See #20 |
| 22 | `qb_injury_flag` | `away_qb_out − home_qb_out` | **Bug:** when both QBs are injured, `1−1=0` — indistinguishable from both healthy. Split into two binary flags. |
| 23 | `off_roster_value_delta` | Home − away | ✅ Keep |
| 24 | `def_roster_value_delta` | Home − away | ✅ Keep |
| 25 | `st_value_delta` | Home − away | ✅ Keep |
| 26 | `qb_resilience_delta` | Home − away | ✅ Keep |
| 27 | `home_flag` | **Always 1.0** | Constant for all REG games — drop. But home field IS real (+11 pts win rate vs neutral sites). Replace with `home_field_advantage = 1.0` for regular home games, `0.0` for neutral sites (74 neutral REG games 1999–2025, growing to 8/season). |
| 28 | `div_game_flag` | 1 if divisional game | ✅ Keep |
| 29 | `surface_type` | 1 if artificial turf | ✅ Keep |
| 30 | `is_dome_flag` | 1 if dome/closed roof | Captured by `passing_difficulty_index` (dome → wind=0, temp=72). Drop. |
| 31 | `playoff_flag` | 1 if postseason | ✅ Keep |
| 32 | `week` | Week number 1–18 | ✅ Keep |

---

## 3. Proposed Feature Set (25 features)

### 3.1 Elo (2 features)

#### `elo_diff`
**Replaces:** `tm_elo_pre`, `opp_elo_pre`

```
elo_diff = home_elo_pre − away_elo_pre
```

Positive = home team has higher Elo rating (stronger recent form + history).

**Source:** `rawdata/elo_computed.csv` — columns `home_elo_pre`, `away_elo_pre`  
Computed by `scripts/compute_elo.py` from `rawdata/schedules/games.csv`.  
**Coverage:** Full history (2006+)

---

#### `elo_confidence`
**Keep; rebased on `elo_diff`.**

```
elo_confidence = |elo_diff| / 25
```

Captures the decisiveness of the Elo gap. A 25-pt gap ≈ 54% win probability; 100 pts ≈ 64%.  
**Source:** derived from `elo_diff`

---

### 3.2 EPA Matchup (3 features)

#### Background — EPA and the matchup frame

**Expected Points Added (EPA)** measures how much each play changed expected score. Positive EPA = the offense gained an advantage; negative = the defense gained one. `stats_team_week` provides per-team, per-game cumulative EPA totals; we normalize to per-play rates.

**Why the current features are wrong:**  
`def_pass_epa` is set to the away team's *offensive* pass EPA — it measures "how well did the away team's offense throw the ball" not "how well did the home team's defense stop the pass." These are different things. A team can have a great offense (high `off_pass_epa`) and a terrible defense at the same time.

**The correct matchup frame:**
```
home_advantage_in_dimension =
    (home_offense quality − away_defense quality)  ← home team drives the ball
  − (away_offense quality − home_defense quality)  ← away team drives the ball

= (home_off − away_def) − (away_off − home_def)
```

Positive = home team's combined offense+defense edge in that dimension.

**How defensive EPA is derived:**  
`stats_team_week` has no `def_passing_epa` column. Defensive EPA allowed is computed by schedule pairing:

> For each `(season, week, team)`, find the opponent from `schedules/games.csv`.  
> The team's defensive pass EPA allowed that game = the opponent's `passing_epa / attempts` that same game.

Rolling expanding-mean shifted by 1 gives `def_pass_epa_roll` per team, leak-free.

---

#### `pass_epa_matchup`
**Replaces:** `off_pass_epa`, `def_pass_epa`

```
# Per-play (source: stats_team_week.passing_epa / attempts)
home_off_pass = rolling_mean(home_team.passing_epa / attempts, prior games)
away_off_pass = rolling_mean(away_team.passing_epa / attempts, prior games)

# Defensive EPA allowed (derived via schedule pairing — see §3.2 background)
home_def_pass = rolling_mean(home_team's opponents' passing_epa / attempts, prior games)
away_def_pass = rolling_mean(away_team's opponents' passing_epa / attempts, prior games)

pass_epa_matchup = (home_off_pass − away_def_pass) − (away_off_pass − home_def_pass)
```

**Source columns:**  
- `stats_team_week.passing_epa`, `.attempts`  
- `schedules/games.csv` — `home_team`, `away_team`, `week` (opponent pairing)  
**Coverage:** 2020+ (stats_team_week); pre-2020 rows zero-filled.

---

#### `rush_epa_matchup`
**Replaces:** `off_rush_epa`, `def_rush_epa`

```
home_off_rush = rolling_mean(home_team.rushing_epa / carries)
away_off_rush = rolling_mean(away_team.rushing_epa / carries)
home_def_rush = rolling_mean(home_team's opponents' rushing_epa / carries)
away_def_rush = rolling_mean(away_team's opponents' rushing_epa / carries)

rush_epa_matchup = (home_off_rush − away_def_rush) − (away_off_rush − home_def_rush)
```

**Source columns:** `stats_team_week.rushing_epa`, `.carries` + schedule pairing  
**Coverage:** 2020+

---

#### `early_down_matchup`
**Replaces:** `early_down_pass_epa` (was home-only, pass-only)

```
# Early-down composite: weighted blend of pass + rush EPA and completion % over expectation
early_down_epa = (passing_epa / attempts) × 0.6
               + (rushing_epa / carries)  × 0.2
               + passing_cpoe            × 0.05

home_early_off = rolling_mean(home_team.early_down_epa)
away_early_off = rolling_mean(away_team.early_down_epa)
home_early_def = rolling_mean(home_team's opponents' early_down_epa)
away_early_def = rolling_mean(away_team's opponents' early_down_epa)

early_down_matchup = (home_early_off − away_early_def) − (away_early_off − home_early_def)
```

**Source columns:** `stats_team_week.passing_epa`, `.attempts`, `.rushing_epa`, `.carries`, `.passing_cpoe` + schedule pairing  
**Coverage:** 2020+

---

### 3.3 Ball-Control (2 features — unchanged)

#### `turnover_margin_rolling`
```
# Turnovers = interceptions + rushing_fumbles_lost (+ receiving_fumbles_lost if available)
tm_to  = rolling_mean(home_team.turnovers_per_game)
opp_to = rolling_mean(away_team.turnovers_per_game)

turnover_margin_rolling = (opp_to − tm_to) × (1 − 0.50)   # regressed 50% toward zero
```
Positive = home team historically turns it over less. Regression applied because turnovers are high-variance.  
**Source:** `stats_team_week.passing_interceptions`, `.rushing_fumbles_lost`  
**Coverage:** 2020+

---

#### `net_success_rate`
```
net_success_rate = rolling_mean(home_team.first_downs_per_game)
                 − rolling_mean(away_team.first_downs_per_game)
```
Positive = home team converts first downs at a higher rate.  
**Source:** `stats_team_week.passing_first_downs + rushing_first_downs + receiving_first_downs`  
**Coverage:** 2020+

---

### 3.4 Score Margin (1 feature)

#### `point_diff_advantage`
**Replaces:** `tm_point_diff`, `opp_point_diff`

```
# Each team's rolling avg score margin from their own perspective
tm_margin  = rolling_mean(home_team's prior game margins)     # home_score − away_score when home, vice versa when away
opp_margin = rolling_mean(away_team's prior game margins)

point_diff_advantage = tm_margin − opp_margin
```

Positive = home team outscores opponents by more per game.  
**Source:** `schedules/games.csv` → `home_score`, `away_score`  
**Coverage:** Full history

---

### 3.5 Game Context (5 features — travel/rest split)

#### `market_implied_team_total` *(unchanged)*
```
market_implied_team_total = total_line / 2
```
Proxy for game pace / offensive environment.  
**Source:** `schedules/games.csv → total_line`

---

#### `passing_difficulty_index` *(unchanged)*
```
passing_difficulty_index = (wind × 1.5) + max(0, 40 − temp)
```
Dome imputation: `dome/closed` → `temp = 72`, `wind = 0`.  
**Source:** `schedules/games.csv → wind`, `temp`, `roof`

---

#### `rest_advantage` *(split from old combined feature)*
```
rest_advantage = home_rest − away_rest    # in days
```
Positive = home team has more rest. Typical values: 0 (same week), +7 (home had bye), −7 (home on short week).  
**Source:** `schedules/games.csv → home_rest`, `away_rest`  
**Coverage:** Full history

---

#### `net_travel_disadvantage` *(split from old combined feature, neutral site fixed)*
```
if location == 'Neutral':
    net_travel_disadvantage = 0.0    # both teams traveled; no home-field travel edge
else:
    # away team traveled from their stadium to the home stadium; home team did not travel
    net_travel_disadvantage = haversine(away_stadium_coords, home_stadium_coords) / 1000
    # /1000 so coast-to-coast (≈3,000 mi) = 3.0 units
```

Positive = away team had to travel farther.

**Why zero for Neutral?** For international games (London, Frankfurt) and neutral site games (Super Bowl, etc.), both teams flew to a third venue. The current formula incorrectly computes e.g. `JAX_stadium → BUF_stadium` ≈ 1,250 miles for a London game where both teams flew 4,500 miles. The schedule has 102 neutral games (1999–2025, growing to 8/season in 2024+).

**Source:** `schedules/games.csv → location` + `STADIUM_COORDS` dict in `prediction_service.py`  
**Coverage:** Full history

---

#### `trench_dominance_metric` *(redesigned — all four trench components)*

**Old problem:** OL score = `snap_count × age_multiplier` — this measures how many snaps a lineman played (volume), not how well he played.

**New design — four performance components:**

```
# ── OL: pass protection ──────────────────────────────────────────────
# Sacks allowed per game; negated so fewer sacks = higher score
OL_pass = −rolling_mean(sacks_suffered)
# Source: stats_team_week.sacks_suffered

# ── OL: run blocking ─────────────────────────────────────────────────
# Rushing yards per carry; higher = better run blocking
OL_run = +rolling_mean(rushing_yards / carries)
# Source: stats_team_week.rushing_yards, .carries

# ── DL: pass rush ────────────────────────────────────────────────────
# Composite weighted toward sacks; higher = more disruptive pass rush
DL_pass = +rolling_mean(def_sacks × 6 + def_qb_hits × 1 + def_tackles_for_loss × 1)
# Source: stats_team_week.def_sacks, .def_qb_hits, .def_tackles_for_loss

# ── DL: run defense ──────────────────────────────────────────────────
# Rushing yards per carry ALLOWED to opponents; negated so fewer yards = higher score
# Derived via schedule pairing (same pass as defensive EPA)
DL_run = −rolling_mean(opponents' rushing_yards / carries)
# Source: stats_team_week.rushing_yards / carries of opponent each week (schedule pairing)

# ── Composite ────────────────────────────────────────────────────────
# Z-score each component within (season, week) so all four contribute equally
# Trench advantage = sum of home z-scores minus sum of away z-scores
trench_dominance_metric =
    (home_OL_pass_z + home_OL_run_z + home_DL_pass_z + home_DL_run_z)
  − (away_OL_pass_z + away_OL_run_z + away_DL_pass_z + away_DL_run_z)
```

**Source columns:**  
- `stats_team_week.sacks_suffered`, `.rushing_yards`, `.carries` (OL)  
- `stats_team_week.def_sacks`, `.def_qb_hits`, `.def_tackles_for_loss` (DL pass)  
- `stats_team_week.rushing_yards / carries` via schedule pairing (DL run)  
**Coverage:** 2020+  
*(Pre-2020: falls back to 0.0 since stats_team_week unavailable)*

---

### 3.6 Pressure & Injury (3 features)

#### `qb_pressure_advantage`
**Replaces:** `qb_pressure_rate`, `opp_qb_pressure_rate`

```
# times_pressured_pct = fraction of dropbacks where QB was pressured (0–1)
# Starting QB identified as the QB with most pressures faced that game
home_pressure = rolling_mean(home_QB.times_pressured_pct)
away_pressure = rolling_mean(away_QB.times_pressured_pct)

# Positive = home QB faces less pressure (better home OL or worse away pass rush)
qb_pressure_advantage = away_pressure − home_pressure
```

**Source:** `pfr_advstats/advstats_week_pass_*.csv → times_pressured_pct`, `times_pressured`  
**Coverage:** 2018+; fallback 0.0 for pre-2018

---

#### `def_pressure_diff`
**Replaces:** `def_pressure_gen`, `opp_def_pressure_gen`

```
# Total pressures generated per game (sum across all defenders on the team)
home_press_gen = rolling_mean(home_defense.def_pressures_per_game)
away_press_gen = rolling_mean(away_defense.def_pressures_per_game)

# Positive = home defense generates more pressure
def_pressure_diff = home_press_gen − away_press_gen
```

**Source:** `pfr_advstats/advstats_week_def_*.csv → def_pressures` (summed per team per game)  
**Coverage:** 2018+; fallback 0.0

---

#### `home_qb_injury_flag` + `away_qb_injury_flag`
**Replaces:** `qb_injury_flag` (single signed diff)

**Why split:** The combined `away_qb_out − home_qb_out` collapses both-injured into `1−1=0`, indistinguishable from both-healthy. Two separate binary features let the model learn each scenario independently, including the unusual both-QBs-out case.

```
home_qb_injury_flag = 1.0 if home QB listed Out or Doubtful, else 0.0
away_qb_injury_flag = 1.0 if away QB listed Out or Doubtful, else 0.0
```

Four states the model can now distinguish:

| State | `home_qb_injury_flag` | `away_qb_injury_flag` |
|---|---|---|
| Both healthy | 0 | 0 |
| Home QB out | 1 | 0 |
| Away QB out | 0 | 1 |
| Both out (bug was here) | 1 | 1 |

**Source:** `injuries/injuries_*.csv → position == "QB"`, `report_status ∈ {Out, Doubtful}`  
**Coverage:** 2009+; earlier seasons default 0.0 (healthy assumed)

---

### 3.7 Roster Value (5 features — unchanged)

All already signed home-minus-away differentials from `roster_value_service.py`.

| Feature | Formula |
|---------|---------|
| `roster_talent_delta` | home perf_grade − away perf_grade (perf-based composite from stats_team_week) |
| `off_roster_value_delta` | home − away offensive roster EPA value |
| `def_roster_value_delta` | home − away defensive roster EPA value |
| `st_value_delta` | home − away special teams value |
| `qb_resilience_delta` | home − away QB resilience |

**Coverage:** 2006+

---

### 3.8 Contextual (5 features)

#### `home_field_advantage` *(new — replaces the constant `home_flag`)*
```
home_field_advantage = 0.0 if location == 'Neutral' else 1.0
```

**Why not `home_flag = 1.0` for everything:** The original constant provided zero information to the model. But home field advantage is real: NFL regular-season home teams win **56.2%** of games; at neutral sites that drops to **45.5%** (measured across all NFL games 1999–2025). Making this `0.0` for neutral sites gives the model an explicit signal for the ~6–8 international/neutral games per season (growing) where neither team has crowd noise, familiarity, or avoided travel.

**Source:** `schedules/games.csv → location` (`'Neutral'` or `'Home'`)

---

#### Other contextual features *(unchanged)*

| Feature | Formula | Notes |
|---------|---------|-------|
| `div_game_flag` | 1 if divisional matchup | `schedules/games.csv → div_game` |
| `surface_type` | 1 if artificial turf | `schedules/games.csv → surface` |
| `playoff_flag` | 1 if postseason | from `game_type != REG` |
| `week` | Week number 1–18 | `schedules/games.csv → week` |

**Dropped:**
- `home_flag` — replaced by `home_field_advantage` (now has real variance)
- `is_dome_flag` — captured by `passing_difficulty_index` (dome imputes `wind=0, temp=72`)

---

### 3.9 Future / Phase 2 (not in this PR)

#### Playoffs-clinched / resting starters
**Concept:** Late in the season (week ≥ 16), teams that have clinched their playoff seeding may rest starters, degrading their apparent quality without any injury report signal.

**Why not now:**  
- Requires computing clinching status from rolling standings per week, per team, per conference — complex but feasible from Firestore `nfl_standings` data  
- `roster_talent_delta` partially captures it *after the fact* (rested backups produce lower EPA grades) but can't predict it pre-game

**Suggested Phase 2 approach:**  
`clinched_flag = 1.0` if team has clinched their division or secured their seeding AND `week >= 16` AND they lost their previous game (heuristic for when coaches actually rest starters). A signed diff (`away_clinched − home_clinched`) would fit the model's convention.

---

## 4. Summary Table — Before & After

| Old Feature | Status | New Feature |
|------------|--------|------------|
| `tm_elo_pre` | → collapse | `elo_diff` |
| `opp_elo_pre` | → collapse | *(in elo_diff)* |
| `off_pass_epa` | → matchup formula | `pass_epa_matchup` |
| `def_pass_epa` (mislabeled) | → matchup formula | *(in pass_epa_matchup)* |
| `off_rush_epa` | → matchup formula | `rush_epa_matchup` |
| `def_rush_epa` (mislabeled) | → matchup formula | *(in rush_epa_matchup)* |
| `early_down_pass_epa` | → matchup formula + rush | `early_down_matchup` |
| `tm_point_diff` | → collapse | `point_diff_advantage` |
| `opp_point_diff` | → collapse | *(in point_diff_advantage)* |
| `turnover_margin_rolling` | keep ✅ | `turnover_margin_rolling` |
| `net_success_rate` | keep ✅ | `net_success_rate` |
| `elo_confidence` | keep (rebase) | `elo_confidence` |
| `market_implied_team_total` | keep ✅ | `market_implied_team_total` |
| `passing_difficulty_index` | keep ✅ | `passing_difficulty_index` |
| `travel_rest_disadvantage` | → split + fix neutral | `rest_advantage` |
| *(same)* | → split + fix neutral | `net_travel_disadvantage` |
| `trench_dominance_metric` | redesign: 4-component OL+DL | `trench_dominance_metric` |
| `roster_talent_delta` | keep ✅ | `roster_talent_delta` |
| `qb_pressure_rate` | → collapse | `qb_pressure_advantage` |
| `opp_qb_pressure_rate` | → collapse | *(in qb_pressure_advantage)* |
| `def_pressure_gen` | → collapse | `def_pressure_diff` |
| `opp_def_pressure_gen` | → collapse | *(in def_pressure_diff)* |
| `qb_injury_flag` | → **split** (bug fix) | `home_qb_injury_flag` |
| *(same)* | → **split** (bug fix) | `away_qb_injury_flag` |
| `off_roster_value_delta` | keep ✅ | `off_roster_value_delta` |
| `def_roster_value_delta` | keep ✅ | `def_roster_value_delta` |
| `st_value_delta` | keep ✅ | `st_value_delta` |
| `qb_resilience_delta` | keep ✅ | `qb_resilience_delta` |
| `home_flag` | → **replace** (was constant) | `home_field_advantage` (1.0/0.0) |
| `div_game_flag` | keep ✅ | `div_game_flag` |
| `surface_type` | keep ✅ | `surface_type` |
| `is_dome_flag` | **DROP** ❌ | *(redundant with PDI)* |
| `playoff_flag` | keep ✅ | `playoff_flag` |
| `week` | keep ✅ | `week` |

**Count: 32 → 27** (removed 14 old features, added 9 new)

### Final FEATURE_COLUMNS (27)
```python
FEATURE_COLUMNS = [
    # Elo (2)
    "elo_diff", "elo_confidence",
    # EPA matchup (3)
    "pass_epa_matchup", "rush_epa_matchup", "early_down_matchup",
    # Ball-control (2)
    "turnover_margin_rolling", "net_success_rate",
    # Score margin (1)
    "point_diff_advantage",
    # Game context (5)
    "market_implied_team_total", "passing_difficulty_index",
    "rest_advantage", "net_travel_disadvantage",
    "trench_dominance_metric",
    # Pressure (2)
    "qb_pressure_advantage", "def_pressure_diff",
    # QB health (2) — split to fix both-injured=0 bug
    "home_qb_injury_flag", "away_qb_injury_flag",
    # Roster Value (5)
    "roster_talent_delta",
    "off_roster_value_delta", "def_roster_value_delta",
    "st_value_delta", "qb_resilience_delta",
    # Contextual (5)
    "home_field_advantage",   # 1.0 regular home, 0.0 neutral site
    "div_game_flag", "surface_type", "playoff_flag", "week",
]
```

---

## 5. Decisions Log

| Question | Decision |
|----------|----------|
| Collapse Elo pair to diff? | Yes → `elo_diff` |
| Use real defensive EPA (schedule pairing)? | Yes |
| Include rushing EPA in early-down composite? | Yes (0.6 pass + 0.2 rush + 0.05 cpoe) |
| Fix trench OL from snap-count to performance? | Yes → sacks_allowed + rush_ypc |
| Include DL run defense (schedule pairing)? | Yes → opponents' rush_ypc |
| Split travel_rest into two features? | Yes → `rest_advantage` + `net_travel_disadvantage` |
| Zero travel for neutral/international? | Yes |
| Min training season | Keep 2006; zero-fill EPA pre-2020 |
| Drop `home_flag`? | Yes — constant |
| Drop `is_dome_flag`? | Yes — captured by PDI |

---

## 6. Files Requiring Changes

### `services/nn_feature_engine.py` (primary)
1. **`FEATURE_COLUMNS`** — replace (32 → 25)
2. **`_load_rolling_epa()`** — add defensive EPA + defensive rush_ypc derivation via schedule pairing; return 8 rolling columns (3 off + 3 def EPA, plus off_rush_ypc + def_rush_ypc for trench)
3. **`compute_roster_features()`** — remove OL snap-count logic; replaced by trench computation in `build_master_feature_table()` using stats_team_week
4. **`build_master_feature_table()`**:
   - EPA join: compute 3 matchup features from 12 intermediate columns
   - Trench: compute 4-component trench using stats_team_week + schedule pairing
   - Elo: keep `home_elo_pre`/`away_elo_pre` as metadata; expose `elo_diff` as feature
   - Point diff: add `point_diff_advantage = tm_point_diff − opp_point_diff`
   - Pressure: add `qb_pressure_advantage`, `def_pressure_diff`
   - Travel/rest: split into `rest_advantage` + `net_travel_disadvantage` (zero for Neutral)
   - Output: expose `home_elo_pre`, `away_elo_pre`, per-team raw EPA as extra metadata cols for projection engine

### `services/nn_projection_engine.py`
- `_build_team_profiles()` — include new aux columns (elo, raw EPA) in profile build
- `game_win_probability()`:
  - Remove `home_flag` handler
  - Remove `def_*` and `opp_*` prefix patterns (these features no longer exist)
  - Add `elo_diff` handler (home_elo − away_elo from aux columns)
  - Add `elo_confidence` handler (from `elo_diff`)
  - Add matchup EPA handlers (from per-team off/def EPA aux columns)
  - Add handlers for `point_diff_advantage`, `qb_pressure_advantage`, `def_pressure_diff`, `rest_advantage`, `net_travel_disadvantage`

### `services/nn_prediction_service.py`
- Explanation block — replace old feature references (`off_pass_epa`, `def_pass_epa`, `tm_elo_pre`) with new names

### Scripts — manual steps after implementation
1. `pytest tests/` — verify no breakage
2. `python scripts/train_nn_model.py` — NN v11
3. `python scripts/train_xgb_model.py` — XGB v5  
4. `python scripts/train_lr_model.py` — LR v3
5. `python scripts/backfill_schedule_predictions.py --firestore`
6. `python scripts/refresh_local_pkls.py`
