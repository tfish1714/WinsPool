# Betting Angle Screener — Design Spec

## Problem

The admin has no way to explore prediction signals (Elo, Vegas spread) independently
of the blended ML model. They want to build simple, composable "angles" — e.g. "home
underdogs of 3-7 points where Elo favors the home team" — check how that angle has
performed historically against the spread (ATS), and then see which games in an
upcoming week currently match it. This is a screening/backtesting tool, not a
prediction engine: it explicitly goes beyond (and doesn't touch) the existing NN+XGB+LR
ensemble.

## Prerequisite: fix the `game_predictions` clobber bug

`scripts/backfill_schedule_predictions.py --features` writes a rich per-game record to
the `game_predictions` store (Firestore collection + local JSON mirror), including an
`explanation` dict with `elo_diff`, `vegas_line`, `edge_vs_vegas`, etc. (see
`services/nn_prediction_service.py::build_ensemble_lookup` for completed games and
`scripts/backfill_schedule_predictions.py::_build_predictions_map`'s MC-simulation
branch for future games — both always populate `explanation`, regardless of the
`--features` flag, which only controls the *separate* `prediction_features` SHAP-style
audit store).

`scripts/cache_builder.py` (called by `scripts/run_cron.py`, and runnable standalone)
also writes to `game_predictions`, but only a thin 4-field record (`pred_winner`,
`pred_su_conf`, `pred_ats_pick`, `pred_prob`), via
`services/cache_service.py::write_game_predictions`, which does a full per-season
document overwrite — not a merge. Any run of this path silently destroys whatever
richer `explanation` data a prior backfill run wrote for that season. This is why the
admin's prediction-explain tooltip shows nothing useful for 2026 games right now
(confirmed: `.local_db/game_predictions_2026.json` was last written 2026-08-13 — shape
matches a `cache_builder.py` write, consistent with it having been run locally to
refresh analytics after the preseason/Elo-boost/MC-simulation model fixes made around
that date, not necessarily a scheduled trigger), and it is also why the screener can't
be built without fixing this first — every season's `elo_diff` and `vegas_line` data
lives inside `explanation`, which this path currently wipes.

**Fix:** `cache_builder.py`'s `game_predictions` write path reads the existing stored
record for each game first (via `services.cache_service.get_game_predictions(year)`)
and merges its own 4 fields into each existing per-game dict (creating a new thin
record only if none existed), instead of building a fresh season-wide map from scratch
and overwriting the whole document. This preserves `explanation`/`model_spread`/
`edge_vs_vegas`/`locked` whenever they're already present, while still keeping
`pred_winner`/`pred_su_conf`/`pred_ats_pick`/`pred_prob` fresh nightly.

This fix alone also resolves the tooltip bug reported alongside this feature request —
it is one fix serving both.

## Data flow

No new data ingestion and no new Firestore collections. The screener reads two
already-existing sources, both already reached through sanctioned paths (never
`rawdata/` directly):

1. **`services.cache_service.get_game_predictions(season)`** — per-game `elo_diff`
   (signed home-minus-away, e.g. `home_elo_pre - away_elo_pre`) and Vegas
   `spread_line`/`edge_vs_vegas`, for every game in every season 2006–2026 (spread
   data existed before 2006, but Elo data — and therefore `elo_diff` — only starts in
   2006, so that's the natural backtest floor). Covers both completed games (real
   feature-table values) and future/unplayed games (MC-simulation projections using
   current team state) uniformly — the screener does not need to know which.
2. **`services.data_service.load_data()`** (no `year` arg — all seasons) — gives
   `home_score`, `away_score`, and `result` (`home_score - away_score`) for grading
   historical covers. Confirmed via a live row: `result=-7.0` for a 13-20 home loss.

These are joined in-memory by `(season, week, home_team, away_team)` at request time —
no caching layer needed given the existing per-request `load_data()`/`get_game_predictions()`
caching already in place (1-hour TTL in-memory, pickle/Firestore below that).

## Filter model

A filter describes a bet on **one team in a game** ("side"), not the game as a whole,
so magnitude-only angles ("any team favored by 10+") don't need to special-case
home/away.

```
Filter:
  side:              "home" | "away" | "any"      (which team the bet is on; "any" = evaluate both teams in every game as separate candidate bets)
  favorite_or_dog:    "favorite" | "dog" | "any"   (is `side`'s team favored by Vegas)
  spread_min:         float | null                 (minimum |spread| magnitude, points)
  spread_max:         float | null                 (maximum |spread| magnitude, points)
  elo_diff_min:       float | null                 (minimum elo_diff, signed to `side`'s perspective: positive = side favored by Elo)
  elo_diff_max:       float | null                 (maximum elo_diff, signed to `side`'s perspective)
```

All non-null bounds are AND-combined. `elo_diff` is stored signed home-minus-away; when
`side == "away"` the screener negates it before comparing against
`elo_diff_min`/`elo_diff_max` so the bounds always read as "from the bet's perspective."

A game with `spread_line == 0` (a pick'em) matches `favorite_or_dog: "any"` only —
neither `"favorite"` nor `"dog"` matches it, since there's no favorite to be on either
side of.

`side: "any"` does not mean both teams always match — each game still has exactly one
favorite and one underdog (or neither, if a pick'em), so a filter with
`favorite_or_dog: "favorite"` and `side: "any"` still yields exactly one candidate per
game (whichever team is actually favored), not two. `side: "any"` only means "don't
restrict by home/away," not "count both teams."

## Prebuilt angles

| Name | side | favorite_or_dog | spread_min | spread_max |
|---|---|---|---|---|
| Home Dog | home | dog | — | — |
| Away Favorite | away | favorite | — | — |
| Big Favorite (10+) | any | favorite | 10 | — |
| Big Underdog (10+) | any | dog | 10 | — |

These are just filter presets — clicking one populates the same filter-builder state a
custom filter would, so "start from a preset, then tweak it" works for free.

## Grading (ATS)

For a candidate bet on team `T` in a completed game:

```
margin_for_T = (home_score - away_score) if T is home else (away_score - home_score)
line_for_T   = spread_line if T is home else -spread_line
covers  = margin_for_T > line_for_T
push    = margin_for_T == line_for_T
loses   = margin_for_T < line_for_T
```

Backtest record is `W-L-P` plus `cover_pct = W / (W + L)` (pushes excluded from the
percentage, shown separately) and `n` (total games, for judging sample size). Games
with a null `spread_line` (extremely old data) or unresolved outcome are excluded
from the backtest tally, but if it's the *target* week and unplayed, it's still
eligible to appear as a current candidate (there's just nothing to grade yet).

## API

**`GET /api/admin/betting/screen`** — admin-only (`Depends(require_admin)`).

Query params: `season` (target week's season, default current year),
`week` (target week to surface candidates for, default: next upcoming week with any
unplayed game), `side`, `favorite_or_dog`, `spread_min`, `spread_max`, `elo_diff_min`,
`elo_diff_max` (last 6 all optional; a bare request with no filter params returns the
unfiltered backtest — every graded team-side in history — plus every candidate for the
target week).

Response:
```json
{
  "backtest": { "wins": 412, "losses": 388, "pushes": 14, "n": 814, "cover_pct": 0.515 },
  "candidates": [
    {
      "season": 2026, "week": 1, "home_team": "KC", "away_team": "LAC",
      "side": "away", "spread_line": -3.5, "elo_diff": 42.1,
      "already_played": false
    }
  ]
}
```

`side` in each candidate row identifies which team the *matching* bet is on (relevant
when the filter's `side` is `"any"` — a single game can contribute 0, 1, or 2 candidate
rows).

## UI

New **Betting** tab in `templates/admin.html` (same tab-button/tab-content pattern as
Elo Ratings / Consensus), backed by a new `static/js/admin_betting.js`:

- Prebuilt angle chips (the four rows above) — clicking one loads that filter and runs
  the screen immediately.
- Custom filter builder: side selector (Home/Away/Any), favorite-or-dog selector,
  spread range (two number inputs), Elo diff range (two number inputs), a season/week
  picker for the target week (defaulting to the next upcoming week).
- Results: a backtest summary card (record, cover%, n) styled like the existing
  Consensus tab's summary row, followed by a table of this week's matching candidates
  (team, opponent, spread, Elo diff) — reusing the sortable-table pattern just added to
  the Consensus tab.

## Testing

- `services/cache_service.py` additions (if any) and the new screening logic get unit
  tests over synthetic `game_predictions` + `games` fixtures: filter matching per field
  (side, favorite/dog, spread range, Elo range, sign-flip for `side="away"`), grading
  math (cover/push/loss), and prebuilt-angle preset correctness.
- Route-level test for `/api/admin/betting/screen`: auth-required, filter params
  narrow results, `side="any"` yields per-team candidate rows.
- The `cache_builder.py` merge fix gets a regression test proving a prior rich
  `explanation` survives a subsequent thin-write call.

## Explicitly out of scope (v1)

- Bankroll/unit simulation, odds/vig handling, ROI tracking.
- Filter dimensions beyond Elo and spread (roster quality, EPA, rest, travel, etc.) —
  the API/filter model is structured so these could be added as additional optional
  bounds later without a breaking change, but v1 ships with only `elo_diff` and
  `spread`.
- Saving/naming custom filters beyond the four hardcoded presets.
