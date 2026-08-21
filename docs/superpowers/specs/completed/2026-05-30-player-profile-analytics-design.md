# Player Profile Analytics Page — Issue #30

**Date:** 2026-05-30
**Status:** Approved
**Closes:** GitHub issue #30
**Scope:** `routes/history_routes.py`, `services/analysis_service.py`, `routes/api_routes.py`, `templates/player_profile.html`, `static/js/player_profile.js`, `templates/overall_history.html`

---

## Overview

A dedicated per-player analytics page at `/history/player/{player_id}` showing multi-season performance, draft analysis, and trends. Accessible by any logged-in user; no role restriction. The existing `/history` table gains a "View" link per row to navigate here.

---

## Page Layout

Charts-first pattern matching the rest of the app (glassmorphism cards):

```
┌─────────────────────────────────────────┐
│  [Player Name]  ·  N seasons            │
│  Total wins: X  ·  Avg: Y/season        │
│  Best finish: 1st (YYYY) · Worst: Nth   │
└─────────────────────────────────────────┘

┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  Wins Trend  │ │  Draft ROI   │ │  Rank        │
│  line chart  │ │  bar chart   │ │  line chart  │
└──────────────┘ └──────────────┘ └──────────────┘

[legend row: "± proj = vs preseason projection · ± slot = vs historical average for that pick number"]

┌─────────────────────────────────────────────────┐
│  Year-by-year breakdown table                   │
│  Year | Rank | Wins | Pick 1 | Pick 2 | Pick 3  │
└─────────────────────────────────────────────────┘
```

Three chart cards sit side-by-side on desktop; stack vertically on mobile (≤480px breakpoint). Header card spans full width above the charts. Table spans full width below.

---

## Routes

### `GET /history/player/{player_id}` — `routes/history_routes.py`

Server-rendered page. Loads player record + calls `get_player_analytics(player_id)`. Passes result to `templates/player_profile.html`. Returns 404 if player not found.

### `GET /api/player/{player_id}/analytics` — `routes/api_routes.py`

JSON endpoint consumed by Chart.js on page load. Returns the same analytics dict as the template context (so charts can be initialized after DOM ready without a second server render).

Response shape:
```json
{
  "player": {"playerId": 1, "fullName": "Alice", "nickName": "Ali"},
  "career": {
    "seasons": 5,
    "totalWins": 132,
    "avgWins": 26.4,
    "bestFinish": {"rank": 1, "year": 2023},
    "worstFinish": {"rank": 6, "year": 2021}
  },
  "seasons": [
    {
      "year": 2024,
      "rank": 2,
      "totalWins": 30,
      "picks": [
        {
          "pickNum": 3,
          "team": "KC",
          "actualWins": 15,
          "projectedWins": 12,
          "slotAvgWins": 13.2,
          "vsProjected": 3,
          "vsSlot": 1.8
        }
      ]
    }
  ],
  "slotAverages": {"1": 13.5, "2": 12.8, ...}
}
```

---

## Analysis — `services/analysis_service.py`

### `get_player_analytics(player_id, draft_results, standings, preseason_predictions)`

Joins data across all seasons for one player:

1. Filter `draft_results` to rows where `playerId == player_id`.
2. For each season the player participated in, look up their final rank and total wins from standings.
3. For each pick, join `nfl_standings` to get actual wins and `preseason_predictions` to get projected wins.
4. Compute `vsProjected = actualWins − projectedWins`.
5. Compute `slotAverages`: for each pick slot 1–30, average `actualWins` across **all players, all seasons** in `draft_results`. This is the league-wide benchmark, not specific to this player. Cache this computation — it's the same for every player page.
6. Compute `vsSlot = actualWins − slotAverages[pickNum]`.
7. Build career summary: total seasons, sum of wins, avg wins/season, best/worst rank with year.

Returns the dict matching the API response shape above.

---

## Charts — `static/js/player_profile.js`

Chart.js (already loaded in `base.html`). Three charts initialized on `DOMContentLoaded` from the inline JSON passed via a `<script id="analyticsData" type="application/json">` tag in the template.

### Wins Trend (line)
- x: season years
- y: total wins
- Series 1: actual wins (solid line, gold)
- Series 2: linear trendline (dashed, muted) — computed from least-squares slope of the wins data

### Draft ROI (grouped bar)
- x: season years
- 3 bar groups per year (one per pick), color-coded: green if `vsProjected > 0`, red if negative
- y: `actualWins − projectedWins`
- Tooltip: pick #, team, actual wins, projected wins, vs-slot delta
- Card header has ⓘ icon with tooltip: *"Shows how each pick performed vs. our preseason win projection for that team."*

### Rank Over Years (line)
- x: season years
- y: final rank (y-axis inverted — lower rank number = better, so 1st place appears at top)
- Solid line (accent color) + dashed trendline
- Card header has ⓘ icon with tooltip: *"Lower rank = better finish. Y-axis is inverted so improvement trends upward."*

---

## Table — `templates/player_profile.html`

Year-by-year breakdown. Each pick cell shows:
```
[TEAM]  Pick #N
W: 15  ±proj: +3  ±slot: +1.8
```

**Legend row** above the table (muted text, one line):
> *± proj = vs preseason projection · ± slot = vs historical average for that pick number*

**Tooltip on column headers** "± proj" and "± slot":
- *± proj*: "Difference between actual wins and our ML preseason projection for this team."
- *± slot*: "Difference between actual wins and the historical average wins for teams drafted at this pick position."

---

## Navigation Changes

### `templates/overall_history.html`

Add a "View" link in each row of the all-time player stats table:
```html
<a href="/history/player/{{ player.playerId }}" class="btn-small">View</a>
```

No other navigation changes. URL is accessible to any logged-in user.

---

## What This Does NOT Include

- Admin-only data or views (all players see the same page)
- Editing or updating player data from this page
- Head-to-head breakdown per player (covered by existing `/headtohead` routes)
- Comparisons between two players side-by-side
