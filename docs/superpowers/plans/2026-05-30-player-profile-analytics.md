# Player Profile Analytics Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-player analytics page at `/history/player/{player_id}` showing multi-season wins trend, draft ROI, rank history, and a year-by-year breakdown table.

**Architecture:** New `get_player_analytics()` function in `analysis_service.py` joins draft results, standings, and preseason projections. A new page route renders `player_profile.html`; a parallel JSON endpoint feeds the three Chart.js charts. The existing `/history` table gets a "View" link per row.

**Tech Stack:** FastAPI, Pandas, Jinja2, Chart.js (already loaded in `base.html`).

---

## File Structure

- **Create:** `tests/test_player_analytics.py` — unit tests for `get_player_analytics()` + route smoke tests
- **Modify:** `services/analysis_service.py` — add `get_player_analytics()`
- **Modify:** `routes/history_routes.py` — add `/history/player/{player_id}` page route; add `playerId` to overall-history stats dict
- **Modify:** `routes/api_routes.py` — add `GET /api/player/{player_id}/analytics`
- **Create:** `templates/player_profile.html` — chart cards + year-by-year table
- **Create:** `static/js/player_profile.js` — three Chart.js charts with trendlines
- **Modify:** `templates/overall_history.html` — add "View" link per player row

---

### Task 1: `get_player_analytics()` analysis function

**Files:**
- Modify: `services/analysis_service.py` (append after `calculate_wins_pool_standings`)
- Create: `tests/test_player_analytics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_player_analytics.py`:

```python
import pytest
import pandas as pd
from services.analysis_service import get_player_analytics


def _draft():
    return pd.DataFrame([
        {"playerId": 1, "season": 2022, "draftPick": 1,  "team": "KC"},
        {"playerId": 1, "season": 2022, "draftPick": 11, "team": "SF"},
        {"playerId": 1, "season": 2022, "draftPick": 21, "team": "DAL"},
        {"playerId": 2, "season": 2022, "draftPick": 2,  "team": "BUF"},
        {"playerId": 2, "season": 2022, "draftPick": 12, "team": "PHI"},
        {"playerId": 2, "season": 2022, "draftPick": 22, "team": "MIA"},
    ])


def _standings():
    return pd.DataFrame([
        {"season": 2022, "team": "KC",  "wins": 14, "scored": 400, "allowed": 300},
        {"season": 2022, "team": "SF",  "wins": 13, "scored": 380, "allowed": 290},
        {"season": 2022, "team": "DAL", "wins": 12, "scored": 370, "allowed": 310},
        {"season": 2022, "team": "BUF", "wins": 13, "scored": 390, "allowed": 295},
        {"season": 2022, "team": "PHI", "wins": 14, "scored": 410, "allowed": 305},
        {"season": 2022, "team": "MIA", "wins": 9,  "scored": 330, "allowed": 340},
    ])


def _players():
    return pd.DataFrame([
        {"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"},
        {"playerId": 2, "fullName": "Bob Jones",   "nickName": "Bob"},
    ])


def _preds():
    return {
        2022: {
            "KC":  {"projected_wins": 12.0},
            "SF":  {"projected_wins": 11.0},
            "DAL": {"projected_wins": 10.0},
            "BUF": {"projected_wins": 12.0},
            "PHI": {"projected_wins": 13.0},
            "MIA": {"projected_wins": 8.0},
        }
    }


def test_career_summary():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    assert result is not None
    career = result["career"]
    assert career["seasons"] == 1
    assert career["totalWins"] == 39   # 14+13+12
    assert career["avgWins"] == 39.0
    assert career["bestFinish"]["year"] == 2022
    assert career["worstFinish"]["year"] == 2022


def test_picks_with_deltas():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    season = result["seasons"][0]
    assert season["year"] == 2022
    assert season["totalWins"] == 39
    kc = next(p for p in season["picks"] if p["team"] == "KC")
    assert kc["actualWins"] == 14
    assert kc["projectedWins"] == 12.0
    assert kc["vsProjected"] == 2.0   # 14 - 12
    assert kc["pickNum"] == 1


def test_slot_averages():
    result = get_player_analytics(1, _draft(), _standings(), _players(), _preds())
    sa = result["slotAverages"]
    assert sa[1] == 14.0   # KC only pick at slot 1 across all seasons
    assert sa[11] == 13.0  # SF
    assert sa[21] == 12.0  # DAL


def test_returns_none_for_unknown_player():
    assert get_player_analytics(99, _draft(), _standings(), _players(), _preds()) is None


def test_returns_none_for_empty_draft_results():
    assert get_player_analytics(1, pd.DataFrame(), _standings(), _players(), _preds()) is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_player_analytics.py -v
```

Expected: FAIL — `ImportError: cannot import name 'get_player_analytics'`

- [ ] **Step 3: Implement `get_player_analytics()` in `services/analysis_service.py`**

Append after `calculate_wins_pool_standings` (after line ~549):

```python
def get_player_analytics(
    player_id: int,
    all_draft_results: pd.DataFrame,
    standings_master: pd.DataFrame,
    players: pd.DataFrame,
    preseason_preds: dict,
) -> dict | None:
    """Multi-season analytics for one player.

    preseason_preds: {season_int: {team_abbr: {"projected_wins": float}}}
    Returns None if the player has no draft history.
    """
    from services.utils import filter_season

    if all_draft_results.empty:
        return None
    player_draft = all_draft_results[all_draft_results["playerId"] == player_id]
    if player_draft.empty:
        return None

    # League-wide avg wins per draft slot across all players/seasons
    slot_avgs: dict[int, float] = {}
    for pick_num in range(1, 31):
        slot_picks = all_draft_results[all_draft_results["draftPick"] == pick_num]
        wins_list = []
        for _, row in slot_picks.iterrows():
            yr_st = filter_season(standings_master, row["season"])
            team_row = yr_st[yr_st["team"] == row["team"]]
            if not team_row.empty:
                wins_list.append(int(team_row.iloc[0].get("wins", 0)))
        slot_avgs[pick_num] = round(sum(wins_list) / len(wins_list), 1) if wins_list else 0.0

    available_seasons = sorted(int(s) for s in player_draft["season"].unique())
    seasons_data = []

    for season in available_seasons:
        season_picks = filter_season(player_draft, season).sort_values("draftPick")
        yr_standings = filter_season(standings_master, season)
        all_season_draft = filter_season(all_draft_results, season)
        season_preds = preseason_preds.get(season, {})

        picks = []
        total_wins = 0
        for _, pick_row in season_picks.iterrows():
            team = pick_row["team"]
            pick_num = int(pick_row["draftPick"])
            team_st = yr_standings[yr_standings["team"] == team]
            actual_wins = int(team_st.iloc[0].get("wins", 0)) if not team_st.empty else 0
            total_wins += actual_wins
            projected = float(season_preds.get(team, {}).get("projected_wins", 0))
            slot_avg = slot_avgs.get(pick_num, 0.0)
            picks.append({
                "pickNum": pick_num,
                "team": team,
                "actualWins": actual_wins,
                "projectedWins": round(projected, 1),
                "slotAvgWins": slot_avg,
                "vsProjected": round(actual_wins - projected, 1),
                "vsSlot": round(actual_wins - slot_avg, 1),
            })

        pool = calculate_wins_pool_standings(yr_standings, all_season_draft, players, season)
        pool_row = pool[pool["playerId"] == player_id] if not pool.empty else pd.DataFrame()
        rank = int(pool_row.iloc[0]["Rank"]) if not pool_row.empty else 0

        seasons_data.append({"year": season, "rank": rank, "totalWins": total_wins, "picks": picks})

    if not seasons_data:
        return None

    all_wins = [s["totalWins"] for s in seasons_data]
    ranked = [s for s in seasons_data if s["rank"] > 0]
    best_finish  = min(ranked, key=lambda s: s["rank"],  default=seasons_data[0])
    worst_finish = max(ranked, key=lambda s: s["rank"],  default=seasons_data[-1])

    p_row = players[players["playerId"] == player_id]
    p = p_row.iloc[0] if not p_row.empty else pd.Series(dtype=object)

    return {
        "player": {
            "playerId": player_id,
            "fullName": str(p.get("fullName", "")),
            "nickName": str(p.get("nickName", "")),
        },
        "career": {
            "seasons": len(seasons_data),
            "totalWins": sum(all_wins),
            "avgWins": round(sum(all_wins) / len(all_wins), 1),
            "bestFinish":  {"rank": best_finish["rank"],  "year": best_finish["year"]},
            "worstFinish": {"rank": worst_finish["rank"], "year": worst_finish["year"]},
        },
        "seasons": seasons_data,
        "slotAverages": slot_avgs,
    }
```

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_player_analytics.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Run full suite to confirm no regressions**

```
pytest tests/ -v
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add services/analysis_service.py tests/test_player_analytics.py
git commit -m "feat: add get_player_analytics() with multi-season picks, rank, and slot averages (#30)"
```

---

### Task 2: Page route, API endpoint, and playerId fix

**Files:**
- Modify: `routes/history_routes.py`
- Modify: `routes/api_routes.py`

- [ ] **Step 1: Write failing route tests**

Add to `tests/test_player_analytics.py`:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from main import app

client = TestClient(app)


def _mock_load_data():
    standings = pd.DataFrame([
        {"season": 2022, "team": "KC", "wins": 14, "scored": 400, "allowed": 300},
    ])
    players = pd.DataFrame([{"playerId": 1, "fullName": "Alice Smith", "nickName": "Alice"}])
    draft = pd.DataFrame([
        {"playerId": 1, "season": 2022, "draftPick": 1, "team": "KC"},
    ])
    games = pd.DataFrame([{"season": 2022}])
    return standings, pd.DataFrame(), games, players, pd.DataFrame(), draft, pd.DataFrame()


@patch("routes.history_routes.load_data", return_value=_mock_load_data())
@patch("routes.history_routes.get_preseason_predictions", return_value={"KC": {"projected_wins": 12.0}})
def test_player_profile_page_returns_200(mock_preds, mock_load):
    resp = client.get("/history/player/1")
    assert resp.status_code == 200


@patch("routes.history_routes.load_data", return_value=_mock_load_data())
@patch("routes.history_routes.get_preseason_predictions", return_value={})
def test_player_profile_page_returns_404_for_unknown_player(mock_preds, mock_load):
    resp = client.get("/history/player/999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_player_analytics.py::test_player_profile_page_returns_200 -v
```

Expected: FAIL — `404 Not Found` (route doesn't exist yet)

- [ ] **Step 3: Add page route to `routes/history_routes.py`**

Add imports at top of file:

```python
from fastapi import HTTPException
import json as _json
from services.data_service import get_preseason_predictions
```

Append at the bottom of `routes/history_routes.py`:

```python
# ─── Player Profile ───────────────────────────────────────────────────────────

@router.get("/history/player/{player_id}")
async def player_profile(request: Request, player_id: int):
    standings_master, _, all_games, players, _, all_draft_results, _ = load_data()

    player_row = players[players["playerId"] == player_id] if not players.empty else pd.DataFrame()
    if player_row.empty:
        raise HTTPException(status_code=404, detail="Player not found")

    player_seasons = (
        all_draft_results[all_draft_results["playerId"] == player_id]["season"].unique()
        if not all_draft_results.empty else []
    )
    preseason_preds = {int(s): get_preseason_predictions(int(s)) for s in player_seasons}

    analytics = analysis.get_player_analytics(
        player_id, all_draft_results, standings_master, players, preseason_preds
    )
    if analytics is None:
        raise HTTPException(status_code=404, detail="No analytics data for player")

    return templates.TemplateResponse(request, "player_profile.html", {
        "analytics": analytics,
        "analytics_json": _json.dumps(analytics),
        "current_year": get_active_season(all_games),
    })
```

- [ ] **Step 4: Add API endpoint to `routes/api_routes.py`**

Add after the last existing endpoint in `api_routes.py`:

```python
@router.get("/player/{player_id}/analytics")
def get_player_analytics_endpoint(
    player_id: int,
    _auth: dict = Depends(require_auth),
):
    """Multi-season analytics payload for Chart.js on the player profile page."""
    try:
        standings_master, _, _, players, _, all_draft_results, _ = load_data()
        player_row = players[players["playerId"] == player_id] if not players.empty else pd.DataFrame()
        if player_row.empty:
            return not_found()

        from services.data_service import get_preseason_predictions
        player_seasons = (
            all_draft_results[all_draft_results["playerId"] == player_id]["season"].unique()
            if not all_draft_results.empty else []
        )
        preseason_preds = {int(s): get_preseason_predictions(int(s)) for s in player_seasons}

        result = analysis.get_player_analytics(
            player_id, all_draft_results, standings_master, players, preseason_preds
        )
        if result is None:
            return not_found()
        return JSONResponse(content=result)
    except Exception:
        logger.exception("Error in /api/player/%d/analytics", player_id)
        return server_error()
```

- [ ] **Step 5: Fix `playerId` in `overall_history` stats dict (`routes/history_routes.py`)**

In `overall_history()`, find the line (around line 69):

```python
for _, row in yr_standings.iterrows():
    p_name = row["fullName"]
    wins = int(row["TotalWins"])
    rank = int(row["Rank"])

    season_records.append(...)

    if p_name not in player_stats:
        player_stats[p_name] = {
            "name": p_name, "total_wins": 0, "seasons_played": 0,
```

Replace `if p_name not in player_stats:` block with:

```python
    if p_name not in player_stats:
        player_stats[p_name] = {
            "name": p_name,
            "playerId": int(row.get("playerId", 0)),
            "total_wins": 0, "seasons_played": 0,
            "1st": 0, "2nd": 0, "3rd": 0, "10th": 0,
            "best": {"year": None, "wins": -1, "rank": 999},
            "worst": {"year": None, "wins": 999, "rank": -1},
        }
```

- [ ] **Step 6: Run tests**

```
pytest tests/test_player_analytics.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 7: Commit**

```bash
git add routes/history_routes.py routes/api_routes.py tests/test_player_analytics.py
git commit -m "feat: add /history/player/{id} page route and /api/player/{id}/analytics endpoint (#30)"
```

---

### Task 3: Player profile template

**Files:**
- Create: `templates/player_profile.html`

- [ ] **Step 1: Create `templates/player_profile.html`**

```html
{% extends "base.html" %}
{% block title %}{{ analytics.player.fullName }} — Player Profile{% endblock %}
{% block content %}

{# Inline analytics data for player_profile.js #}
<script id="analyticsData" type="application/json">{{ analytics_json | safe }}</script>

<main class="dashboard-main" style="display:block;">
  <div class="card-glass page-card" style="height:auto; overflow:visible;">

    {# ── Back link ── #}
    <div style="margin-bottom:1rem;">
      <a href="/history" style="color:var(--text-secondary); font-size:0.85rem; text-decoration:none;">
        ← All-Time History
      </a>
    </div>

    {# ── Career summary header ── #}
    <header class="wp-top" style="margin-bottom:1.5rem;">
      <div>
        <div class="eyebrow">Player Profile</div>
        <h1 class="wp-h1">{{ analytics.player.fullName }}</h1>
      </div>
    </header>

    <div style="display:flex; gap:2rem; flex-wrap:wrap; margin-bottom:2rem; font-size:0.9rem; color:var(--text-secondary);">
      <span><strong style="color:var(--text-primary);">{{ analytics.career.seasons }}</strong> seasons</span>
      <span><strong style="color:var(--primary-color);">{{ analytics.career.totalWins }}</strong> total wins</span>
      <span><strong style="color:var(--text-primary);">{{ analytics.career.avgWins }}</strong> avg wins/season</span>
      <span>Best finish: <strong style="color:var(--primary-color);">#{{ analytics.career.bestFinish.rank }}</strong> ({{ analytics.career.bestFinish.year }})</span>
      <span>Worst finish: <strong style="color:var(--neg);">#{{ analytics.career.worstFinish.rank }}</strong> ({{ analytics.career.worstFinish.year }})</span>
    </div>

    {# ── Chart cards ── #}
    <style>
      .profile-charts { display:grid; grid-template-columns:repeat(3,1fr); gap:1rem; margin-bottom:2rem; }
      @media (max-width:480px) { .profile-charts { grid-template-columns:1fr; } }
    </style>
    <div class="profile-charts">

      <div class="card-glass" style="padding:1rem;">
        <h3 style="font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:1px; margin-bottom:0.75rem;">
          Wins Per Season
        </h3>
        <div style="position:relative; height:180px;">
          <canvas id="winsTrendChart"></canvas>
        </div>
      </div>

      <div class="card-glass" style="padding:1rem;">
        <h3 style="font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:1px; margin-bottom:0.75rem;">
          Draft ROI vs Projection
          <span title="Shows how each pick performed vs. our preseason win projection for that team."
                style="cursor:help; margin-left:4px; opacity:0.6;">ⓘ</span>
        </h3>
        <div style="position:relative; height:180px;">
          <canvas id="draftRoiChart"></canvas>
        </div>
      </div>

      <div class="card-glass" style="padding:1rem;">
        <h3 style="font-size:0.85rem; color:var(--text-secondary); text-transform:uppercase; letter-spacing:1px; margin-bottom:0.75rem;">
          Final Rank by Season
          <span title="Lower rank = better finish. Y-axis is inverted so improvement trends upward."
                style="cursor:help; margin-left:4px; opacity:0.6;">ⓘ</span>
        </h3>
        <div style="position:relative; height:180px;">
          <canvas id="rankChart"></canvas>
        </div>
      </div>

    </div>

    {# ── Table legend ── #}
    <p style="font-size:0.78rem; color:var(--text-secondary); margin-bottom:0.75rem;">
      ± proj = vs preseason projection &nbsp;·&nbsp; ± slot = vs historical average for that pick number
    </p>

    {# ── Year-by-year table ── #}
    <div class="table-container">
      <table class="stacked-table">
        <thead>
          <tr>
            <th>Year</th>
            <th>Rank</th>
            <th>Wins</th>
            <th>
              Pick 1
              <span title="Difference between actual wins and preseason projection / historical pick-slot average."
                    style="cursor:help; opacity:0.6;">ⓘ</span>
            </th>
            <th>Pick 2</th>
            <th>Pick 3</th>
          </tr>
        </thead>
        <tbody>
          {% for s in analytics.seasons | sort(attribute='year', reverse=True) %}
          <tr>
            <td style="font-weight:700;">{{ s.year }}</td>
            <td>#{{ s.rank }}</td>
            <td style="color:var(--primary-color); font-weight:700;">{{ s.totalWins }}</td>
            {% for pick in s.picks %}
            <td style="font-size:0.82rem; line-height:1.6;">
              <strong>{{ pick.team }}</strong> &nbsp;<span style="color:var(--text-secondary);">Pick #{{ pick.pickNum }}</span><br>
              {{ pick.actualWins }}W
              <span style="color:{{ 'var(--primary-color)' if pick.vsProjected >= 0 else 'var(--neg)' }};">
                {{ '%+.1f' % pick.vsProjected }} proj
              </span>
              <span style="color:{{ 'var(--primary-color)' if pick.vsSlot >= 0 else 'var(--neg)' }};">
                {{ '%+.1f' % pick.vsSlot }} slot
              </span>
            </td>
            {% endfor %}
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

  </div>
</main>

<script src="/static/js/player_profile.js"></script>
{% endblock %}
```

- [ ] **Step 2: Verify page renders**

Start dev server:
```
uvicorn main:app --reload
```

Navigate to `/history/player/1` (use a real player ID from your local data). Confirm:
- Career summary header shows player name and stats
- Three empty canvas elements render (charts won't work yet — JS not written)
- Year-by-year table shows rows
- "± proj" / "± slot" legend is visible
- Back link goes to `/history`

- [ ] **Step 3: Commit**

```bash
git add templates/player_profile.html
git commit -m "feat: add player_profile.html template with chart cards and year-by-year table (#30)"
```

---

### Task 4: Chart.js initialization

**Files:**
- Create: `static/js/player_profile.js`

- [ ] **Step 1: Create `static/js/player_profile.js`**

```javascript
(function () {
    'use strict';

    const CHART_DEFAULTS = {
        tickColor: 'rgba(255,255,255,0.7)',
        gridColor: 'rgba(255,255,255,0.1)',
        tooltipBg: 'rgba(0,0,0,0.85)',
    };

    function linearTrendline(values) {
        const n = values.length;
        if (n < 2) return values.slice();
        const sumX  = values.reduce((s, _, i) => s + i, 0);
        const sumY  = values.reduce((s, y) => s + y, 0);
        const sumXY = values.reduce((s, y, i) => s + i * y, 0);
        const sumX2 = values.reduce((s, _, i) => s + i * i, 0);
        const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX) || 0;
        const intercept = (sumY - slope * sumX) / n;
        return values.map((_, i) => Math.round((slope * i + intercept) * 10) / 10);
    }

    function axisConfig() {
        return {
            ticks: { color: CHART_DEFAULTS.tickColor },
            grid:  { color: CHART_DEFAULTS.gridColor },
        };
    }

    document.addEventListener('DOMContentLoaded', function () {
        const raw = document.getElementById('analyticsData');
        if (!raw) return;
        const data = JSON.parse(raw.textContent);
        const seasons = data.seasons.slice().sort((a, b) => a.year - b.year);
        const years   = seasons.map(s => String(s.year));

        // ── Chart 1: Wins Trend ───────────────────────────────────────────────
        const winsData = seasons.map(s => s.totalWins);
        new Chart(document.getElementById('winsTrendChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: years,
                datasets: [
                    {
                        label: 'Wins',
                        data: winsData,
                        borderColor: '#c9a24a',
                        backgroundColor: '#c9a24a',
                        borderWidth: 2,
                        tension: 0.1,
                        pointRadius: 4,
                    },
                    {
                        label: 'Trend',
                        data: linearTrendline(winsData),
                        borderColor: 'rgba(201,162,74,0.4)',
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: CHART_DEFAULTS.tooltipBg },
                },
                scales: { x: axisConfig(), y: axisConfig() },
            },
        });

        // ── Chart 2: Draft ROI ────────────────────────────────────────────────
        // 3 datasets: player's 1st / 2nd / 3rd pick per season (sorted by draftPick)
        const pickColors = values => values.map(v => v >= 0 ? '#4ade80' : '#f87171');
        const pickLabel  = (seasonObj, idx) => {
            const pick = seasonObj.picks[idx];
            return pick ? `Pick #${pick.pickNum} (${pick.team})` : '';
        };

        const roiDatasets = [0, 1, 2].map(idx => {
            const values = seasons.map(s => s.picks[idx] ? s.picks[idx].vsProjected : 0);
            return {
                label: `Pick ${idx + 1}`,
                data: values,
                backgroundColor: pickColors(values),
                borderWidth: 0,
            };
        });

        new Chart(document.getElementById('draftRoiChart').getContext('2d'), {
            type: 'bar',
            data: { labels: years, datasets: roiDatasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: CHART_DEFAULTS.tooltipBg,
                        callbacks: {
                            label: function (ctx) {
                                const s = seasons[ctx.dataIndex];
                                const pick = s.picks[ctx.datasetIndex];
                                if (!pick) return '';
                                return `Pick #${pick.pickNum} ${pick.team}: ${pick.actualWins}W (proj ${pick.projectedWins}, slot avg ${pick.slotAvgWins})`;
                            },
                        },
                    },
                },
                scales: {
                    x: axisConfig(),
                    y: { ...axisConfig(), title: { display: true, text: 'Wins vs Projected', color: CHART_DEFAULTS.tickColor } },
                },
            },
        });

        // ── Chart 3: Rank Over Years ──────────────────────────────────────────
        const rankData = seasons.map(s => s.rank);
        new Chart(document.getElementById('rankChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: years,
                datasets: [
                    {
                        label: 'Rank',
                        data: rankData,
                        borderColor: '#5b9cf6',
                        backgroundColor: '#5b9cf6',
                        borderWidth: 2,
                        tension: 0.1,
                        pointRadius: 4,
                    },
                    {
                        label: 'Trend',
                        data: linearTrendline(rankData),
                        borderColor: 'rgba(91,156,246,0.4)',
                        borderDash: [5, 5],
                        borderWidth: 1,
                        pointRadius: 0,
                        fill: false,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: { backgroundColor: CHART_DEFAULTS.tooltipBg },
                },
                scales: {
                    x: axisConfig(),
                    y: {
                        ...axisConfig(),
                        reverse: true,
                        ticks: {
                            color: CHART_DEFAULTS.tickColor,
                            stepSize: 1,
                            callback: v => `#${v}`,
                        },
                    },
                },
            },
        });
    });
}());
```

- [ ] **Step 2: Verify charts render**

With dev server running, navigate to `/history/player/1`. Confirm:
- Wins Trend line chart renders with a dashed trendline overlay
- Draft ROI bar chart shows green/red bars per pick per season
- Rank chart renders with inverted y-axis (1st place at top)
- Tooltips work on hover

- [ ] **Step 3: Commit**

```bash
git add static/js/player_profile.js
git commit -m "feat: add player_profile.js Chart.js charts (wins trend, draft ROI, rank) (#30)"
```

---

### Task 5: "View" link in overall_history table

**Files:**
- Modify: `templates/overall_history.html`

- [ ] **Step 1: Add "View" link column to `templates/overall_history.html`**

In the `<thead>` row (around line 19), add a header after `Worst Season`:

```html
<th></th>
```

In each `<tr>` in `<tbody>` (after the `Worst Season` `<td>`), add:

```html
<td>
  <a href="/history/player/{{ stat.playerId }}"
     style="font-size:0.8rem; color:var(--primary-color); text-decoration:none; white-space:nowrap;">
    View →
  </a>
</td>
```

- [ ] **Step 2: Verify the links appear and navigate correctly**

Navigate to `/history`. Confirm:
- Each player row has a "View →" link
- Clicking it navigates to `/history/player/{playerId}` and loads their profile page

- [ ] **Step 3: Run full test suite**

```
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add templates/overall_history.html
git commit -m "feat: add View profile links to all-time history table (#30)"
```
