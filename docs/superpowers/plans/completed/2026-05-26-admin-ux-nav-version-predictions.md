# Admin UX — Nav No-Jump, Model Version Badge, Per-Game Predictions Tab

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Three targeted admin UX improvements: prevent layout shift when the admin nav link appears after auth, show which model version the ML accuracy stats are based on, and add a per-game predictions tab showing actual winners alongside model picks.

**Architecture:** Task 1 is a pure CSS/JS tweak — swap `display:none` for `visibility:hidden` to preserve layout space. Task 2 extends the accuracy API response with per-season model version sourced from prediction_features filenames, then renders a version badge in the JS. Task 3 adds a new admin endpoint returning per-game prediction data plus actual winner, replaces the "Predictions" nav link in admin.html with a real tab panel, and creates a new JS module to drive it.

**Tech Stack:** FastAPI, Python, Jinja2, Vanilla JS ES modules, pytest, TestClient

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `templates/base.html` | Modify | Remove `style="display:none"` from admin link; use CSS class |
| `static/style.css` | Modify | Add `.admin-hidden { visibility:hidden }` |
| `static/js/main.js` | Modify | Toggle `admin-hidden` class instead of `display` style |
| `routes/api_routes.py` | Modify | Extend `/api/predictions/accuracy` to include `model_version` per season |
| `static/js/admin_accuracy.js` | Modify | Render version badge in season table and overall banner |
| `routes/admin_routes.py` | Modify | Add `GET /api/admin/predictions/games?season&week` endpoint |
| `static/js/api.js` | Modify | Add `fetchPredictionsGames(season, week)` method |
| `templates/admin.html` | Modify | Replace Predictions `<a>` nav link with real tab `<button>` + section `<div>` |
| `static/js/admin_predictions_games.js` | Create | Per-game predictions tab: selectors, fetch, render table |
| `tests/test_admin_routes.py` | Modify | Tests for new `/api/admin/predictions/games` endpoint |
| `tests/test_api_endpoints.py` | Modify | Test that accuracy response includes `model_version` field |

---

## Task 1: Header Nav No-Jump Fix

**Files:**
- Modify: `templates/base.html:109`
- Modify: `static/style.css` (add helper class)
- Modify: `static/js/main.js:65-68`

The root cause: `#admin-nav-link` starts with `style="display:none"` (zero space), so when JS detects admin role and sets `display:flex`, the flex container reflows and all preceding nav items shift left. Fix: start with `visibility:hidden` instead — the element keeps its space in layout, so switching to visible causes no reflow.

Non-admins keep seeing an invisible placeholder at the nav end; it's imperceptible because the nav scrolls horizontally and the item is visually blank.

- [x] **Step 1: Write failing test for nav link visibility**

```python
# tests/test_api_endpoints.py — add at end of file

def test_base_html_admin_link_uses_visibility_not_display():
    """admin-nav-link must not use inline display:none (causes layout shift)."""
    import pathlib
    src = pathlib.Path("templates/base.html").read_text()
    assert 'id="admin-nav-link"' in src
    # Must not have inline display:none
    assert 'display: none' not in src or 'admin-nav-link' not in src.split('display: none')[0].split('\n')[-1]
    # Must use admin-hidden class
    assert 'admin-hidden' in src
```

- [x] **Step 2: Run test to verify it fails**

```
pytest tests/test_api_endpoints.py::test_base_html_admin_link_uses_visibility_not_display -v
```
Expected: FAIL — `admin-hidden` not yet in base.html

- [x] **Step 3: Add `.admin-hidden` to `static/style.css`**

Find the `.admin-link` rule block (search for `admin-link`) and add immediately after it:

```css
/* Reserve layout space for admin nav link until auth resolves */
.admin-hidden { visibility: hidden; }
```

- [x] **Step 4: Update `templates/base.html` — remove inline `display:none`, add class**

Change line ~109 from:
```html
<a href="/admin" id="admin-nav-link" class="tab-btn admin-link" style="display: none;"><i
        data-lucide="settings"></i> Admin Portal</a>
```
To:
```html
<a href="/admin" id="admin-nav-link" class="tab-btn admin-link admin-hidden"><i
        data-lucide="settings"></i> Admin Portal</a>
```

- [x] **Step 5: Update `static/js/main.js` — toggle class instead of display**

Change lines ~65-68 from:
```javascript
const adminLink = document.getElementById('admin-nav-link');
if (adminLink) {
    adminLink.style.display = (role === 'admin') ? 'flex' : 'none';
}
```
To:
```javascript
const adminLink = document.getElementById('admin-nav-link');
if (adminLink) {
    if (role === 'admin') {
        adminLink.classList.remove('admin-hidden');
    } else {
        adminLink.classList.add('admin-hidden');
    }
}
```

- [x] **Step 6: Run test to verify it passes**

```
pytest tests/test_api_endpoints.py::test_base_html_admin_link_uses_visibility_not_display -v
```
Expected: PASS

- [x] **Step 7: Commit**

```bash
git add templates/base.html static/style.css static/js/main.js tests/test_api_endpoints.py
git commit -m "fix: admin nav link uses visibility:hidden to prevent layout shift on auth"
```

---

## Task 2: ML Accuracy — Model Version Badge

**Files:**
- Modify: `routes/api_routes.py` (extend accuracy response)
- Modify: `static/js/admin_accuracy.js` (render badge)
- Test: `tests/test_api_endpoints.py`

The accuracy endpoint scans `game_predictions_*.json` files but never returns which model version made the predictions. The prediction_features files encode the version in their filename: `prediction_features_{season}_{ensemble_version}.json`. We scan those filenames, build a `{season: version}` map, and attach it to each season row and the overall object. Filenames are sorted ascending so the last match wins (highest version).

- [x] **Step 1: Write failing test for model_version in accuracy response**

```python
# tests/test_api_endpoints.py — add at end of file

def test_accuracy_response_includes_model_version(auth_token):
    """GET /api/predictions/accuracy must include model_version on each season row."""
    resp = client.get("/api/predictions/accuracy", headers={"Authorization": auth_token})
    assert resp.status_code == 200
    data = resp.json()
    assert "seasons" in data
    assert "overall" in data
    # Every season row must have a model_version key (value can be None if no features file)
    for row in data["seasons"]:
        assert "model_version" in row, f"Season {row.get('season')} missing model_version"
```

- [x] **Step 2: Run test to verify it fails**

```
pytest tests/test_api_endpoints.py::test_accuracy_response_includes_model_version -v
```
Expected: FAIL — `model_version` key missing from season rows

- [x] **Step 3: Extend `routes/api_routes.py` accuracy endpoint**

In `get_prediction_accuracy()`, add version-map scanning before the `return` statement. The complete final block (replacing lines ~155–175) looks like:

```python
        seasons_list = sorted(seasons_data.values(), key=lambda r: r['season'], reverse=True)
        overall = {
            'total': overall_total,
            'correct': overall_correct,
            'accuracy': round(overall_correct / overall_total * 100, 1) if overall_total else 0,
        }

        # ── Attach model version from prediction_features filenames ────────
        import re as _re
        version_map: dict = {}
        feat_files = sorted(local_db.glob('prediction_features_*.json')) if local_db.exists() else []
        for ff in feat_files:
            m = _re.match(r'prediction_features_(\d{4})_(.+)\.json', ff.name)
            if m:
                version_map[int(m.group(1))] = m.group(2)  # last (highest) wins

        for row in seasons_list:
            row['model_version'] = version_map.get(row['season'])

        return JSONResponse(content={'seasons': seasons_list, 'overall': overall})
```

- [x] **Step 4: Run test to verify it passes**

```
pytest tests/test_api_endpoints.py::test_accuracy_response_includes_model_version -v
```
Expected: PASS

- [x] **Step 5: Update `static/js/admin_accuracy.js` — version badge in season table**

Replace `renderSeasonTable()` with:

```javascript
function renderSeasonTable(seasons) {
    const table = document.getElementById('acc-season-table');
    if (!seasons || seasons.length === 0) {
        table.innerHTML = '<p style="color:var(--text-secondary);">No accuracy data found. Run the backfill script to generate locked predictions.</p>';
        return;
    }

    const rows = seasons.map(s => `
        <tr class="acc-season-row" data-season="${s.season}" style="cursor:pointer; transition: background 0.15s;"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''"
        >
            <td style="padding:10px 14px; font-weight:700; color:var(--accent-gold);">${s.season}</td>
            <td style="padding:10px 14px; text-align:right;">${s.correct}/${s.total}</td>
            <td style="padding:10px 14px; min-width:160px;">${_bar(s.accuracy)}</td>
            <td style="padding:10px 14px; font-size:0.72rem; color:var(--text-secondary); font-family:monospace; white-space:nowrap;">${s.model_version || '—'}</td>
            <td style="padding:10px 14px; font-size:0.8rem; color:var(--text-secondary);">↗ weekly</td>
        </tr>
    `).join('');

    table.innerHTML = `
        <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
            <thead>
                <tr style="border-bottom:1px solid var(--glass-border); color:var(--text-secondary); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em;">
                    <th style="padding:8px 14px; text-align:left;">Season</th>
                    <th style="padding:8px 14px; text-align:right;">Correct / Total</th>
                    <th style="padding:8px 14px; text-align:left;">SU Accuracy</th>
                    <th style="padding:8px 14px; text-align:left;">Ensemble</th>
                    <th style="padding:8px 14px;"></th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    document.querySelectorAll('.acc-season-row').forEach(row => {
        row.addEventListener('click', () => {
            const season = parseInt(row.dataset.season);
            const sData = _accuracyData.seasons.find(s => s.season === season);
            if (sData) renderWeekPanel(sData);
        });
    });
}
```

- [x] **Step 6: Commit**

```bash
git add routes/api_routes.py static/js/admin_accuracy.js tests/test_api_endpoints.py
git commit -m "feat: show model version badge in ML Accuracy tab per season"
```

---

## Task 3: Per-Game Predictions Tab

**Files:**
- Create: `static/js/admin_predictions_games.js`
- Modify: `routes/admin_routes.py` (new endpoint)
- Modify: `static/js/api.js` (new method)
- Modify: `templates/admin.html` (replace link with real tab)
- Test: `tests/test_admin_routes.py`

The "Predictions" item in admin.html is currently `<a href="/admin/predictions">` — a nav link that navigates away from the admin panel. This task converts it to a real tab panel showing per-game prediction data (matchup, model pick, actual winner, ✓/✗, spreads, Vegas edge) loaded from a new admin endpoint.

The existing `/admin/predictions` feature debug page (`admin_predictions.html`) remains intact; we add a small "Feature Debug ↗" link within the new tab for deep dives.

### New endpoint shape

`GET /api/admin/predictions/games?season=YYYY&week=N` (requires admin auth)

Response:
```json
{
  "season": 2024,
  "week": 9,
  "games": [
    {
      "key": "W09_MIN_IND",
      "away_team": "MIN",
      "home_team": "IND",
      "pred_winner": "MIN",
      "pred_su_conf": 72.0,
      "model_spread": -8.1,
      "vegas_line": -6.5,
      "edge_vs_vegas": -1.6,
      "pred_ats_pick": "MIN",
      "actual_winner": "MIN",
      "is_correct": true
    }
  ]
}
```

`actual_winner` is `null` for future/unplayed games. `is_correct` is `null` when `actual_winner` is null or `pred_winner` is null.

- [x] **Step 1: Write failing tests for new endpoint**

```python
# tests/test_admin_routes.py — append to end of file

class TestPredictionsGames:
    """Tests for GET /api/admin/predictions/games."""

    def test_requires_admin(self, auth_token):
        """Non-admin token is rejected."""
        resp = client.get(
            "/api/admin/predictions/games?season=2024&week=1",
            headers={"Authorization": auth_token},
        )
        assert resp.status_code in (401, 403)

    def test_requires_token(self):
        """Missing token is rejected."""
        resp = client.get("/api/admin/predictions/games?season=2024&week=1")
        assert resp.status_code in (401, 403)

    def test_happy_path_returns_games_list(self, admin_token):
        """Returns JSON with season, week, and games list for valid params."""
        from unittest.mock import patch
        mock_preds = {
            "W01_KC_BUF": {
                "pred_winner": "KC",
                "pred_su_conf": 65.0,
                "model_spread": 3.5,
                "edge_vs_vegas": 1.0,
                "pred_ats_pick": "KC",
                "explanation": {"vegas_line": 2.5},
            }
        }
        import pandas as pd
        mock_games = pd.DataFrame([{
            "week": 1, "home_team": "KC", "away_team": "BUF", "result": 7.0,
        }])
        with patch("routes.admin_routes.get_game_predictions", return_value=mock_preds), \
             patch("routes.admin_routes.load_data", return_value=(
                 None, None, mock_games, None, None, None, None
             )):
            resp = client.get(
                "/api/admin/predictions/games?season=2024&week=1",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["season"] == 2024
        assert body["week"] == 1
        assert isinstance(body["games"], list)
        assert len(body["games"]) == 1
        game = body["games"][0]
        assert game["away_team"] == "BUF"
        assert game["home_team"] == "KC"
        assert game["actual_winner"] == "KC"
        assert game["is_correct"] is True
        assert "model_spread" in game
        assert "vegas_line" in game

    def test_future_game_has_null_actual_winner(self, admin_token):
        """Unplayed game returns actual_winner=null, is_correct=null."""
        from unittest.mock import patch
        import pandas as pd
        mock_preds = {
            "W18_SF_SEA": {
                "pred_winner": "SF",
                "pred_su_conf": 60.0,
                "model_spread": 4.0,
                "edge_vs_vegas": 0.5,
                "pred_ats_pick": "SF",
                "explanation": {"vegas_line": 3.5},
            }
        }
        mock_games = pd.DataFrame()  # No results yet
        with patch("routes.admin_routes.get_game_predictions", return_value=mock_preds), \
             patch("routes.admin_routes.load_data", return_value=(
                 None, None, mock_games, None, None, None, None
             )):
            resp = client.get(
                "/api/admin/predictions/games?season=2025&week=18",
                headers={"Authorization": admin_token},
            )
        assert resp.status_code == 200
        games = resp.json()["games"]
        assert len(games) == 1
        assert games[0]["actual_winner"] is None
        assert games[0]["is_correct"] is None
```

- [x] **Step 2: Run tests to verify they fail**

```
pytest tests/test_admin_routes.py::TestPredictionsGames -v
```
Expected: FAIL — endpoint doesn't exist yet

- [x] **Step 3: Add endpoint to `routes/admin_routes.py`**

Add after the `get_predictions_vs_vegas` function (around line 386), before the `admin_predictions_page` page route:

```python
@router.get("/admin/predictions/games")
async def get_predictions_games(season: int, week: int, _: dict = Depends(require_admin)):
    """Per-game predictions with actual winner for a week (admin only).

    Returns all predictions for the given season/week with actual_winner
    from nfl_games. actual_winner and is_correct are null for future games.
    """
    try:
        from services.cache_service import get_game_predictions
        from services.nn_feature_engine import _normalize_team

        preds = get_game_predictions(season)

        # Build actual-winner lookup from nfl_games
        _, _, all_games, _, _, _, _ = load_data()
        result_lookup: dict = {}
        if all_games is not None and not all_games.empty:
            played = all_games[
                all_games['result'].notna() & (all_games['result'] != -1000)
            ]
            for _, row in played.iterrows():
                wk = row.get('week')
                ht = _normalize_team(str(row.get('home_team', '') or ''))
                at = _normalize_team(str(row.get('away_team', '') or ''))
                res = row.get('result', 0)
                if not wk or not ht or not at:
                    continue
                key = f"W{int(wk):02d}_{ht}_{at}"
                if res > 0:
                    result_lookup[key] = ht
                elif res < 0:
                    result_lookup[key] = at

        week_prefix = f"W{week:02d}_"
        games = []
        for key, pred in preds.items():
            if not key.startswith(week_prefix):
                continue
            parts = key.split("_")
            ht = parts[1] if len(parts) > 1 else "?"
            at = parts[2] if len(parts) > 2 else "?"
            ex = pred.get("explanation") or {}
            actual_winner = result_lookup.get(key)
            pw = pred.get("pred_winner")
            is_correct: bool | None = None
            if actual_winner is not None and pw is not None:
                is_correct = (_normalize_team(str(pw)) == actual_winner)
            games.append({
                "key":           key,
                "away_team":     at,
                "home_team":     ht,
                "pred_winner":   pw,
                "pred_su_conf":  pred.get("pred_su_conf"),
                "model_spread":  pred.get("model_spread"),
                "vegas_line":    ex.get("vegas_line"),
                "edge_vs_vegas": pred.get("edge_vs_vegas"),
                "pred_ats_pick": pred.get("pred_ats_pick"),
                "actual_winner": actual_winner,
                "is_correct":    is_correct,
            })

        # Sort: incorrect first, then future, then correct
        def _sort_key(g):
            ic = g["is_correct"]
            if ic is False:
                return 0
            if ic is None:
                return 1
            return 2

        games.sort(key=_sort_key)
        return JSONResponse(content={"season": season, "week": week, "games": games})
    except Exception:
        logger.exception("Unhandled error in get_predictions_games")
        return server_error()
```

- [x] **Step 4: Run tests to verify they pass**

```
pytest tests/test_admin_routes.py::TestPredictionsGames -v
```
Expected: 4 tests PASS

- [x] **Step 5: Add `fetchPredictionsGames` to `static/js/api.js`**

In the `ApiService` export object, after `fetchVsVegas`:

```javascript
async fetchPredictionsGames(season, week) {
    return fetchWithTimeout(`${API_BASE}/admin/predictions/games?season=${season}&week=${week}`);
},
```

- [x] **Step 6: Create `static/js/admin_predictions_games.js`**

```javascript
/**
 * admin_predictions_games.js — Per-game predictions tab for the Admin Panel.
 *
 * Loads /api/admin/predictions/games for a chosen season + week and renders
 * a table showing matchup, model pick, actual winner, ✓/✗, spreads, Vegas edge.
 */

import { ApiService } from './api.js';

// ── DOM refs ─────────────────────────────────────────────────────────────────

const pgSeason  = document.getElementById('pg-season');
const pgWeek    = document.getElementById('pg-week');
const pgLoad    = document.getElementById('pg-load');
const pgResults = document.getElementById('pg-results');
const pgEmpty   = document.getElementById('pg-empty');
const pgLoading = document.getElementById('pg-loading');

// ── Helpers ───────────────────────────────────────────────────────────────────

function _fmtSpread(line, home, away) {
    if (line == null) return '<span style="color:var(--text-secondary);">—</span>';
    if (line === 0)   return "Pick'em";
    const fav = line > 0 ? home : away;
    return `${fav} -${Math.abs(line).toFixed(1)}`;
}

function _correctIcon(isCorrect) {
    if (isCorrect === null || isCorrect === undefined)
        return '<span style="color:var(--text-secondary);">—</span>';
    return isCorrect
        ? '<span style="color:var(--accent-green); font-weight:700;">✓</span>'
        : '<span style="color:var(--accent-red);   font-weight:700;">✗</span>';
}

function _edgeStr(ev, home, away) {
    if (ev == null) return '<span style="color:var(--text-secondary);">—</span>';
    const abs = Math.abs(ev);
    const dir = ev > 0 ? home : away;
    const cls = abs >= 3 ? 'edge-high' : abs >= 1.5 ? 'edge-mid' : 'edge-low';
    return `<span class="${cls}">${dir} +${abs.toFixed(1)}${abs >= 3 ? ' ⚡' : ''}</span>`;
}

// ── Load & render ─────────────────────────────────────────────────────────────

async function loadGames() {
    const season = Number(pgSeason.value);
    const week   = Number(pgWeek.value);
    if (!season || !week) return;

    pgResults.style.display = 'none';
    pgEmpty.style.display   = 'none';
    pgLoading.style.display = 'block';

    try {
        const data = await ApiService.fetchPredictionsGames(season, week);
        pgLoading.style.display = 'none';

        if (!data.games || !data.games.length) {
            pgEmpty.style.display = 'block';
            return;
        }

        const total   = data.games.length;
        const correct = data.games.filter(g => g.is_correct === true).length;
        const hasResults = data.games.some(g => g.actual_winner != null);

        const summaryHtml = hasResults
            ? `<div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.75rem;">
                 Week ${week} — <span style="color:var(--text-primary); font-weight:600;">${correct}/${total}</span> correct
               </div>`
            : `<div style="font-size:0.8rem; color:var(--text-secondary); margin-bottom:0.75rem;">
                 Week ${week} — ${total} games (no results yet)
               </div>`;

        document.querySelector('#pg-table tbody').innerHTML = data.games.map(g => {
            const pickColor = g.pred_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const actualColor = g.actual_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const rowBg = g.is_correct === false ? 'rgba(239,68,68,0.05)' : '';
            return `<tr style="background:${rowBg}">
                <td style="font-weight:600;">${g.away_team} @ ${g.home_team}</td>
                <td style="color:${pickColor};">${g.pred_winner ?? '—'} ${g.pred_su_conf != null ? `<span style="color:var(--text-secondary);font-size:0.75rem;">${g.pred_su_conf}%</span>` : ''}</td>
                <td style="color:${g.actual_winner ? actualColor : 'var(--text-secondary)'};">${g.actual_winner ?? '—'}</td>
                <td style="text-align:center;">${_correctIcon(g.is_correct)}</td>
                <td>${_fmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                <td>${_fmtSpread(g.vegas_line,   g.home_team, g.away_team)}</td>
                <td>${_edgeStr(g.edge_vs_vegas, g.home_team, g.away_team)}</td>
            </tr>`;
        }).join('');

        document.getElementById('pg-summary').innerHTML = summaryHtml;
        pgResults.style.display = 'block';
    } catch (err) {
        pgLoading.style.display = 'none';
        pgEmpty.innerHTML = `Failed to load: ${err.message}`;
        pgEmpty.style.display = 'block';
    }
}

pgLoad.addEventListener('click', loadGames);

// ── Init: populate season / week dropdowns ────────────────────────────────────

(function initPg() {
    if (!pgSeason || !pgWeek) return;   // Guard: only run on pages with these elements

    const currentYear = new Date().getFullYear();
    const seasonOpts  = '<option value="">— Season —</option>' +
        Array.from({ length: currentYear - 2019 }, (_, i) => currentYear - i)
             .map(y => `<option value="${y}">${y}</option>`).join('');
    pgSeason.innerHTML = seasonOpts;

    const weekOpts = '<option value="">— Week —</option>' +
        Array.from({ length: 22 }, (_, i) => i + 1)
             .map(w => `<option value="${w}">Week ${w}</option>`).join('');
    pgWeek.innerHTML = weekOpts;
})();
```

- [x] **Step 7: Update `templates/admin.html` — replace Predictions link, add tab section**

**7a.** In the admin tab bar (lines ~25-33), replace:
```html
<a href="/admin/predictions" class="admin-tab-btn tab-btn" style="text-decoration:none;">Predictions</a>
```
With:
```html
<button class="admin-tab-btn tab-btn" data-tab="predictions-section">Predictions</button>
```

**7b.** After the `#accuracy-section` closing `</div>` (around line 307), add the new section:

```html
    <!-- Per-Game Predictions Tab -->
    <div id="predictions-section" class="tab-content card-glass hidden" style="height: auto;">
        <div style="display:flex; justify-content:space-between; align-items:baseline; margin-bottom:1.25rem; flex-wrap:wrap; gap:0.5rem;">
            <div>
                <h2 style="margin:0 0 0.25rem;">Predictions</h2>
                <p style="margin:0; color:var(--text-secondary); font-size:0.85rem;">Per-game picks vs actual results. Incorrect predictions highlighted.</p>
            </div>
            <a href="/admin/predictions" target="_blank" style="font-size:0.8rem; color:var(--text-secondary); text-decoration:none;">Feature Debug ↗</a>
        </div>

        <!-- Selectors -->
        <div class="card-glass" style="padding:1.25rem; margin-bottom:1.5rem;">
            <div style="display:flex; gap:1rem; flex-wrap:wrap; align-items:flex-end;">
                <div>
                    <label style="font-size:0.75rem; color:var(--text-secondary); display:block; margin-bottom:4px;">Season</label>
                    <select id="pg-season" class="admin-input" style="min-width:110px;"></select>
                </div>
                <div>
                    <label style="font-size:0.75rem; color:var(--text-secondary); display:block; margin-bottom:4px;">Week</label>
                    <select id="pg-week" class="admin-input" style="min-width:110px;"></select>
                </div>
                <button id="pg-load" class="btn-glass" style="padding:6px 18px; font-size:0.85rem;">Load</button>
            </div>
        </div>

        <!-- Results -->
        <div id="pg-results" style="display:none;">
            <div class="card-glass" style="padding:1.25rem;">
                <div id="pg-summary"></div>
                <table class="feat-table" id="pg-table">
                    <thead>
                        <tr>
                            <th>Matchup</th>
                            <th>Model Pick</th>
                            <th>Actual</th>
                            <th style="text-align:center;">✓/✗</th>
                            <th>Model Line</th>
                            <th>Vegas</th>
                            <th>Edge</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                </table>
            </div>
        </div>
        <div id="pg-empty"   style="color:var(--text-secondary); text-align:center; padding:2rem; display:none;">No predictions found for this week.</div>
        <div id="pg-loading" style="color:var(--text-secondary); text-align:center; padding:2rem; display:none;">Loading…</div>
    </div>
```

**7c.** Add the script tag at the bottom of `admin.html`, after the existing script tags:

```html
<script type="module" src="/static/js/admin_predictions_games.js"></script>
```

- [x] **Step 8: Run full test suite to verify nothing broken**

```
pytest tests/test_admin_routes.py tests/test_api_endpoints.py -v
```
Expected: all tests PASS (including the 4 new TestPredictionsGames tests)

- [x] **Step 9: Commit**

```bash
git add routes/admin_routes.py static/js/api.js static/js/admin_predictions_games.js templates/admin.html tests/test_admin_routes.py
git commit -m "feat: per-game predictions tab with actual winner and correctness indicator"
```

---

## Task 4: Vegas Edge Badge on Schedule Tab

**Files:**
- Modify: `services/cache_service.py` (expose `edge_vs_vegas` through `merge_game_predictions`)
- Modify: `templates/schedule.html` (add ⚡ badge when `|edge_vs_vegas| >= 3`)
- Test: `tests/test_cache_service.py`

`merge_game_predictions()` currently only propagates `pred_winner`, `pred_su_conf`, `pred_ats_pick`, `pred_prob`. The schedule template needs `edge_vs_vegas` to show the disagreement badge. We add it to the merge so it flows through the `/api/schedule` endpoint into the Jinja2 template.

The badge shows only on unplayed games with a prediction and renders as: `⚡ vs Vegas: {favored_team} +{abs_edge}` in accent-green. Threshold: `|edge_vs_vegas| >= 3.0`.

- [x] **Step 1: Write failing test for edge_vs_vegas in merged schedule**

```python
# tests/test_cache_service.py — add to existing file

def test_merge_game_predictions_includes_edge_vs_vegas():
    """merge_game_predictions must propagate edge_vs_vegas from prediction dict."""
    import pandas as pd
    from unittest.mock import patch
    from services.cache_service import merge_game_predictions

    df = pd.DataFrame([{
        'week': 3, 'home_team': 'KC', 'away_team': 'BUF', 'season': 2024,
    }])
    mock_preds = {
        'W03_KC_BUF': {
            'pred_winner': 'KC',
            'pred_su_conf': 68.0,
            'pred_ats_pick': 'KC',
            'pred_prob': 0.68,
            'edge_vs_vegas': 4.5,
            'model_spread': 7.0,
        }
    }
    with patch('services.cache_service.get_game_predictions', return_value=mock_preds):
        result = merge_game_predictions(df, 2024)

    assert 'edge_vs_vegas' in result.columns
    assert result.iloc[0]['edge_vs_vegas'] == 4.5
```

- [x] **Step 2: Run test to verify it fails**

```
pytest tests/test_cache_service.py::test_merge_game_predictions_includes_edge_vs_vegas -v
```
Expected: FAIL — `edge_vs_vegas` column missing

- [x] **Step 3: Update `services/cache_service.py` — add `edge_vs_vegas` and `model_spread` to merge**

In `merge_game_predictions()`, change the two occurrences of the columns tuple from:
```python
for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob'):
```
To:
```python
for col in ('pred_winner', 'pred_su_conf', 'pred_ats_pick', 'pred_prob',
            'edge_vs_vegas', 'model_spread'):
```

Both loops (the initialization loop and the assignment loop) use the same tuple, so one change covers both.

- [x] **Step 4: Run test to verify it passes**

```
pytest tests/test_cache_service.py::test_merge_game_predictions_includes_edge_vs_vegas -v
```
Expected: PASS

- [x] **Step 5: Add ⚡ badge to `templates/schedule.html`**

Find the prediction block (around line 105–125). Inside the `{% if row['pred_winner'] %}` block, after the ATS pick line and before the `</div>` that closes `.game-prediction`, add:

```html
{% if row['edge_vs_vegas'] is not none and row['edge_vs_vegas']|float|abs >= 3 %}
<div style="margin-top:5px; font-size:0.68rem; font-weight:700; color:var(--accent-green); letter-spacing:0.01em;">
    ⚡ vs Vegas: {{ row['home_team'] if row['edge_vs_vegas']|float > 0 else row['away_team'] }} +{{ (row['edge_vs_vegas']|float|abs)|round(1) }}
</div>
{% endif %}
```

The full prediction block (lines ~105–125) should look like:

```html
{% if row['pred_winner'] %}
<div class="game-prediction"
    style="margin-top: 8px; padding-top: 4px; border-top: 1px dashed rgba(255,255,255,0.1); color: var(--accent-gold); font-weight: 600; display: flex; justify-content: space-between; align-items: flex-start;">
    <div>
        <span><i data-lucide="sparkles"></i> Predictor:</span>
        <div style="font-size: 0.75rem; color: #fff;">
            Win: {{ row['pred_winner'] }} ({{ row['pred_su_conf'] }}%)
        </div>
        <div style="font-size: 0.7rem; color: var(--text-secondary); font-weight: normal;">
            ATS: {{ row['pred_ats_pick'] }}
        </div>
        {% if row['edge_vs_vegas'] is not none and row['edge_vs_vegas']|float|abs >= 3 %}
        <div style="margin-top:5px; font-size:0.68rem; font-weight:700; color:var(--accent-green); letter-spacing:0.01em;">
            ⚡ vs Vegas: {{ row['home_team'] if row['edge_vs_vegas']|float > 0 else row['away_team'] }} +{{ (row['edge_vs_vegas']|float|abs)|round(1) }}
        </div>
        {% endif %}
    </div>
    <button class="pred-explain-btn"
        data-season="{{ row['season'] }}"
        data-week="{{ row['week'] }}"
        data-home="{{ row['home_team'] }}"
        data-away="{{ row['away_team'] }}"
        title="Why this prediction?"
        style="display:none; background:none; border:1px solid rgba(251,191,36,0.3); border-radius:50%; width:22px; height:22px; cursor:pointer; color:var(--accent-gold); font-size:12px; padding:0; line-height:22px; text-align:center; flex-shrink:0; margin-top:2px;">?</button>
</div>
{% endif %}
```

- [x] **Step 6: Run full test suite**

```
pytest tests/test_cache_service.py -v
```
Expected: all tests PASS

- [x] **Step 7: Commit**

```bash
git add services/cache_service.py templates/schedule.html tests/test_cache_service.py
git commit -m "feat: show Vegas edge badge on schedule when model disagrees by 3+ pts"
```

---

## Self-Review

**Spec coverage:**
- ✅ Nav no-jump: `visibility:hidden` preserves space, class toggle in JS
- ✅ Model version badge: scanned from prediction_features filenames, shown in season table
- ✅ Predictions tab: converted from nav link to real tab, shows actual winner + ✓/✗
- ✅ Incorrect predictions highlighted (red row background)
- ✅ Future games show `—` for actual/result columns
- ✅ Feature Debug link preserved as `↗` link within the tab
- ✅ Vegas edge badge on schedule: ⚡ when `|edge_vs_vegas| >= 3`
- ✅ Tests for all new backend behaviour

**Placeholder scan:** No TBDs, no "similar to" references, all code blocks complete.

**Type consistency:**
- `is_correct` is `bool | None` in Python, transmitted as JSON `true/false/null`, checked as `=== true / === false` in JS ✅
- `model_version` is `str | None` in Python, displayed as `|| '—'` in JS ✅
- Endpoint key format `W{week:02d}_{home}_{away}` used consistently ✅
- `edge_vs_vegas` propagated through `merge_game_predictions` → `/api/schedule` → Jinja2 template ✅
