# 2026 Season Forecast Section — ML Accuracy Tab

**Goal:** Add a "2026 Season Forecast" card at the top of the admin ML Accuracy tab showing preseason team win projections and per-game model predictions for the upcoming 2026 season.

**Architecture:** A new `GET /api/admin/forecast` endpoint returns team projections + metadata. Game drill-down reuses the existing `/api/admin/predictions/games?season=2026&week=N` endpoint unchanged. The forecast card renders into a new `#acc-forecast-section` div injected above the existing overall accuracy banner, via new functions added to `admin_accuracy.js`.

**Tech Stack:** FastAPI, Firestore/pickle via existing `get_preseason_predictions` / `get_game_predictions` / `get_prediction_features`, vanilla JS, Jinja2

---

## API Endpoint

### `GET /api/admin/forecast`

Location: `routes/admin_routes.py` — requires `Depends(require_admin)`.

Returns:

```json
{
  "season": 2026,
  "model_version": "nn_v10+xgb_v6+lr_v2",
  "game_count": 272,
  "weeks": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
  "team_projections": [
    {"team": "KC",  "projected_wins": 11.5, "std_dev": 2.1},
    {"team": "DET", "projected_wins": 11.0, "std_dev": 2.0}
  ]
}
```

**Derivation:**
- `team_projections` — `get_preseason_predictions(2026)`, converted to list, sorted by `projected_wins` descending
- `game_count` — `len(get_game_predictions(2026))`
- `weeks` — distinct week numbers parsed from game prediction keys (`int(key[1:3])`), sorted ascending
- `model_version` — `get_prediction_features(2026)` → `ensemble_version` field; `None` if not found
- `season` — hardcoded 2026 (endpoint is forecast-specific; generalize only if needed later)

The endpoint never raises — on any failure it returns a 500 via `server_error()`. If `get_preseason_predictions(2026)` returns empty, `team_projections` is `[]`. If `get_game_predictions(2026)` returns empty, `weeks` is `[]` and `game_count` is 0.

**Game drill-down:** The existing `GET /api/admin/predictions/games?season=2026&week=N` endpoint is reused unchanged. It already handles unplayed games by returning `actual_winner: null` and `is_correct: null`.

---

## UI

### HTML (`templates/admin.html`)

Add `<div id="acc-forecast-section"></div>` as the first child inside `#accuracy-section`, before the existing overall accuracy banner div.

```html
<div id="accuracy-section" class="tab-content card-glass hidden" style="height: auto;">
    <div id="acc-forecast-section"></div>   <!-- NEW: inserted here -->
    <!-- existing overall banner, season table, week panel, modal ... -->
```

No new script tag — `loadForecastData()` lives in the existing `admin_accuracy.js`.

### Forecast Card Layout

```
┌─ 2026 Season Forecast ──────────────── [nn_v10+xgb_v6+lr_v2] ─┐
│                                                                  │
│  Team Win Projections                                            │
│  [two-column flex grid, 16 teams per column]                    │
│  KC   ████████████░░░  11.5W  ±2.1                              │
│  DET  ████████████░░░  11.0W  ±2.0                              │
│  ...                                                             │
│                                                                  │
│  Per-Game Predictions  (272 games · 18 weeks)                   │
│  ▶ Week 1   ──────────────────────────────────────────          │
│  ▶ Week 2   ──────────────────────────────────────────          │
│  ...                                                             │
│    ↳ expanded week: Away @ Home | Model Pick | Conf |           │
│      Model Spread | Edge vs Vegas | ? feature modal             │
└──────────────────────────────────────────────────────────────────┘
[Overall accuracy banner — existing]
[Historical season table — existing]
```

### Team Projections

- Two-column flex grid (`flex-wrap: wrap`, each item `width: ~50%`)
- Bar: filled width = `(projected_wins / 17) * 100%`, color `var(--accent-green)`
- Label: `{TEAM}  {projected_wins}W  ±{std_dev}` — std dev in `var(--text-secondary)`
- If `team_projections` is empty: show muted text "No preseason projections found. Run `predict_season.py --season 2026`."

### Per-Game Predictions (Week Rows)

- Collapsible rows for each week in `weeks` array
- Click expands inline via `loadWeekGames(2026, week, containerId)` — the existing function, no changes needed
- Game rows display: Away @ Home | Model Pick + confidence % | Model Spread | Edge vs Vegas | ? feature modal button
- "Actual" column is absent — the game table already shows `—` for unplayed games

### JavaScript (`static/js/admin_accuracy.js`)

**New module-level variable:**
```js
let _forecastData = null;
```

**New `loadForecastData()` function:**
- Fetches `GET /api/admin/forecast` with auth header
- On success: stores response in `_forecastData`, calls `renderForecastCard(_forecastData)`
- On failure: renders a muted "2026 forecast unavailable" message in `#acc-forecast-section`

**New `renderForecastCard(data)` function:**
- Renders the header with season + model version badge
- Renders team projections as a two-column flex grid
- Renders week rows (collapsible), each wiring `loadWeekGames(2026, week, id)` on click
- Injects result into `#acc-forecast-section`

**Updated tab click handler:**
```js
btn.addEventListener('click', () => {
    if (!_forecastData)  loadForecastData();
    if (!_accuracyData)  loadAccuracyData();
});
```

---

## Files Changed

| File | Change |
|---|---|
| `routes/admin_routes.py` | Add `GET /api/admin/forecast` endpoint |
| `static/js/admin_accuracy.js` | Add `_forecastData`, `loadForecastData()`, `renderForecastCard()`, update tab init |
| `templates/admin.html` | Add `<div id="acc-forecast-section"></div>` inside `#accuracy-section` |

---

## Testing

- `GET /api/admin/forecast` returns correct shape with 32 teams sorted by `projected_wins` desc
- `weeks` array contains all 18 weeks present in game predictions
- `game_count` matches number of prediction entries
- `model_version` is populated when prediction_features exist, `null` otherwise
- If `get_preseason_predictions(2026)` returns empty, response has `team_projections: []`
- If `get_game_predictions(2026)` returns empty, response has `weeks: [], game_count: 0`
- Game drill-down via existing endpoint returns `actual_winner: null` for 2026 games
