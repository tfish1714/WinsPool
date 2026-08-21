# Mixed Fixes and Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implement 7 independent improvements spanning admin UX, schedule display, data hygiene, and code quality (DRY violations #22 + test quality #18).

**Architecture:** Items are grouped by scope — XS UI tweaks → route/template fixes → new scripts → frontend refactor → backend DRY → test cleanup. Each task is self-contained; the only ordering dependency is Task 6 (adds `PASSWORD_COMPLEXITY_RE` to constants) must land before Task 8 (tests import it).

**Tech Stack:** FastAPI + Jinja2 (backend), Vanilla JS ES modules (frontend), pytest (tests), Python + Firestore (scripts)

---

## File Map

| Task | Files Modified / Created |
|---|---|
| 1 | `static/js/admin_elo.js` |
| 2 | `routes/standings_routes.py` (add helper + apply), `templates/schedule.html` |
| 3 | `routes/standings_routes.py`, `templates/wins_pool.html`, `weekbyweek.html`, `playoff_race.html`, `schedule.html` |
| 4 | `scripts/cleanup_test_players.py` (create) |
| 5 | `templates/admin.html`, `static/js/admin_accuracy.js` |
| 6 | `services/constants.py`, `services/utils.py`, `routes/auth_routes.py`, `routes/admin_routes.py`, `routes/history_routes.py`, `routes/standings_routes.py` |
| 7 | `services/data_service.py` |
| 8 | `tests/test_cache_service.py`, `tests/conftest.py`, `tests/test_recap_service.py`, `tests/test_auth.py`, `tests/test_db.py` |
| 9 | Create: `tests/test_elo.py`, `tests/test_game_prediction.py`, `tests/test_portfolio.py` — Delete: `tests/test_prediction_service.py` |

---

## Task 1: Elo Tooltip Sort

**Spec:** Item 2 — XS  
**Files:**
- Modify: `static/js/admin_elo.js`

The `tooltip` config block in `_drawChart()` is at line ~255. Add `itemSort` so items are ordered highest-to-lowest Elo in the tooltip popup.

- [x] **Step 1: Add `itemSort` to tooltip config in `admin_elo.js`**

Locate the `tooltip:` block (around line 255) and add `itemSort` as the first key:

```javascript
tooltip: {
    itemSort: (a, b) => (b.parsed.y ?? 0) - (a.parsed.y ?? 0),
    backgroundColor: 'rgba(0,0,0,0.85)',
    titleColor: 'rgba(255,255,255,0.9)',
    bodyColor: 'rgba(255,255,255,0.75)',
    borderColor: 'rgba(255,255,255,0.1)',
    borderWidth: 1,
    callbacks: {
        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(1) ?? 'N/A'}`
    }
},
```

- [x] **Step 2: Verify no tests break**

```bash
pytest tests/ -q
```

Expected: all pass (no tests for this UI change).

- [x] **Step 3: Commit**

```bash
git add static/js/admin_elo.js
git commit -m "fix: sort Elo tooltip items by highest rating"
```

---

## Task 2: Schedule Status Label — Game Time for Unplayed Games

**Spec:** Item 5 (revised) — S  
**Files:**
- Modify: `routes/standings_routes.py`
- Modify: `templates/schedule.html`

Every unplayed game has `result == -1000` (sentinel). Currently the template shows "Live" for ALL of these — wrong for future scheduled games. The real live-game indicator (issue #3) will be built separately; this task just removes the false "Live" label and shows the actual kickoff time instead.

**Data available:** `gametime` comes from the nflverse CSV (e.g. `"13:00"`, `"16:25"` in 24-hour Eastern time) and is already stored in Firestore / `.local_db/nfl_games.pkl` because `daily_nfl_sync.py` uploads the full games DataFrame. After `fillna(UNDRAFTED_SENTINEL)` in `get_enriched_schedule`, unknown game times become `-1000`.

**Desired display:**
- `result != -1000` → **"Final"** (green `status-final`)
- `result == -1000` AND valid `gametime` → **"1:00 PM"** (neutral, no class, ET implied)
- `result == -1000` AND no valid `gametime` → **"—"** (neutral, no class)
- "Live" is **removed entirely** — issue #3 owns the live indicator

- [x] **Step 1: Add `_fmt_gametime()` helper to `standings_routes.py`**

Add this function after the imports and before the router definition:

```python
def _fmt_gametime(gt) -> str:
    """Convert a 24-hour Eastern time string '13:00' → '1:00 PM'.
    Returns '' if the value is missing, -1000, or unparseable.
    """
    import datetime as _dt
    if gt is None or gt in (-1000, '-1000', 'nan', '', float('nan')):
        return ''
    try:
        h, m = str(gt).strip().split(':')
        t = _dt.time(int(h), int(m))
        # %-I removes leading zero on Unix; %I keeps it on Windows — strip manually
        formatted = t.strftime('%I:%M %p').lstrip('0') or t.strftime('%I:%M %p')
        return formatted  # e.g. "1:00 PM" or "4:25 PM"
    except Exception:
        return ''
```

- [x] **Step 2: Apply `_fmt_gametime` to the schedule DataFrame in `schedule_by_year()`**

In `schedule_by_year()`, after `schedule_enriched = merge_game_predictions(schedule_enriched, year)`, add:

```python
if not schedule_enriched.empty and 'gametime' in schedule_enriched.columns:
    schedule_enriched = schedule_enriched.copy()
    schedule_enriched['gametime_display'] = schedule_enriched['gametime'].apply(_fmt_gametime)
```

- [x] **Step 3: Fix status label in `schedule.html`**

Find lines ~94-96 (the `game-status-label` span):

```html
<span
    class="game-status-label {% if row['result'] != -1000 %}status-final{% else %}status-live{% endif %}">
    {{ 'Final' if row['result'] != -1000 else 'Live' }}
</span>
```

Replace with:

```html
<span class="game-status-label {% if row['result'] is number and row['result'] != -1000 %}status-final{% endif %}">
    {%- if row['result'] is number and row['result'] != -1000 -%}
        Final
    {%- elif row.get('gametime_display') -%}
        {{ row['gametime_display'] }}
    {%- else -%}
        —
    {%- endif -%}
</span>
```

- [x] **Step 4: Run tests**

```bash
pytest tests/ -q
```

Expected: all pass.

- [x] **Step 5: Commit**

```bash
git add routes/standings_routes.py templates/schedule.html
git commit -m "fix: show kickoff time instead of 'Live' for unplayed games on schedule page"
```

---

## Task 3: Wins Pool Dropdown Year Inclusion + Descending Sort

**Spec:** Item 4 — S  
**Files:**
- Modify: `routes/standings_routes.py`
- Modify: `templates/wins_pool.html`, `templates/weekbyweek.html`, `templates/playoff_race.html`, `templates/schedule.html`

**Bug root cause:** `get_available_years()` caps at `active_season` (2025). When navigating to `/wins-pool/2026`, year 2026 is not in the list, no `<option>` has `selected`, so the browser defaults to the first DOM item (2013). Templates also render ascending, so 2013 appears first.

**Fix:** (a) Three route handlers need to add the requested year to `available_years` if missing — same pattern already present in `schedule_by_year()`. (b) Four templates need `| sort(reverse=True)` on the loop.

- [x] **Step 1: Write a test for the year-inclusion fix**

Add to `tests/test_auth.py` (or create a new minimal test file — use `tests/test_auth.py` for brevity since we already have a `client` there):

Actually, this is purely a routing fix. Skip writing a new test for this trivial guard — existing integration tests cover route behavior. We'll verify visually after implementation.

- [x] **Step 2: Fix `wins_pool_by_year()` in `standings_routes.py`**

After line `available_years = get_available_years(all_draft_results, all_games)` (around line 60), add:

```python
available_years = get_available_years(all_draft_results, all_games)
if year not in available_years:
    available_years = sorted(available_years + [year])
```

- [x] **Step 3: Fix `wins_pool_weekbyweek()` in `standings_routes.py`**

Find the `return templates.TemplateResponse` call (around line 106). Change:

```python
"available_years": get_available_years(all_draft_results, all_games),
```

To:

```python
"available_years": sorted(
    yrs := get_available_years(all_draft_results, all_games),
    key=lambda y: y
) if year in (yrs := get_available_years(all_draft_results, all_games))
else sorted(yrs + [year]),
```

Wait — that walrus operator form is ugly. Use a cleaner two-liner:

```python
_yrs = get_available_years(all_draft_results, all_games)
if year not in _yrs:
    _yrs = sorted(_yrs + [year])
```

Then pass `_yrs` to the template:

```python
return templates.TemplateResponse(request, "weekbyweek.html", {
    "table": record_by_week.rename(columns=_first_name).to_html(classes="wp-data-table", index=True, border=0),
    "current_year": get_active_season(games),
    "year": year,
    "available_years": _yrs,
})
```

- [x] **Step 4: Fix `playoff_race_by_year()` in `standings_routes.py`**

Similarly, before the `return` statement, add:

```python
_yrs = get_available_years(all_draft_results, all_games)
if year not in _yrs:
    _yrs = sorted(_yrs + [year])
```

Then use `_yrs` in the template context:

```python
return templates.TemplateResponse(request, "playoff_race.html", {
    "race": race_data,
    "year": year,
    "current_year": get_active_season(games),
    "available_years": _yrs,
})
```

- [x] **Step 5: Add `| sort(reverse=True)` to four templates**

In `templates/wins_pool.html` (line ~24), change:

```html
{% for y in available_years %}
```

to:

```html
{% for y in available_years | sort(reverse=True) %}
```

In `templates/weekbyweek.html` (line ~16), same change.

In `templates/playoff_race.html` (line ~20), same change.

In `templates/schedule.html` (line ~17), same change.

(`draft_results.html` and `headtohead.html` already use `| sort(reverse=True)` — no change needed.)

- [x] **Step 6: Run tests**

```bash
pytest tests/ -q
```

Expected: all pass.

- [x] **Step 7: Commit**

```bash
git add routes/standings_routes.py templates/wins_pool.html templates/weekbyweek.html templates/playoff_race.html templates/schedule.html
git commit -m "fix: wins pool year dropdown includes current year + sorts descending"
```

---

## Task 4: StableNick Cleanup Script

**Spec:** Item 3 — S  
**Files:**
- Create: `scripts/cleanup_test_players.py`

49 test players (`playerIds` 6, 14–67) with `nickName == "StableNick"` or `fullName == "Stability Test User"` are polluting dropdown menus and standings. This script deletes them from Firestore.

- [x] **Step 1: Create `scripts/cleanup_test_players.py`**

```python
#!/usr/bin/env python3
"""
cleanup_test_players.py — Delete StableNick / Stability Test User test records
from Firestore (and optionally local pkl).

Usage:
    python scripts/cleanup_test_players.py            # Delete from Firestore
    python scripts/cleanup_test_players.py --dry-run  # Preview only
    python scripts/cleanup_test_players.py --force-local  # Allow local-only mode

After deletion, run:
    python scripts/refresh_local_pkls.py
"""

import argparse
import os
import sys
import pathlib

# Ensure project root is importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Delete StableNick test players from Firestore.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be deleted without deleting anything.")
    parser.add_argument("--force-local", action="store_true",
                        help="Run even if USE_LOCAL_DATA=True (local pkl only mode).")
    args = parser.parse_args()

    use_local = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"
    if use_local and not args.force_local:
        print("ERROR: USE_LOCAL_DATA=True — this script targets Firestore.")
        print("  Pass --force-local to run anyway (will still delete from Firestore if credentials present).")
        sys.exit(1)

    from services.db_service import get_db
    db = get_db()
    if db is None:
        print("ERROR: Could not connect to Firestore. Check FIREBASE_CREDENTIALS.")
        sys.exit(1)

    # Query by nickName and fullName separately (Firestore doesn't support OR across fields natively)
    players_ref = db.collection("players")
    to_delete = []

    for doc in players_ref.stream():
        data = doc.to_dict()
        nick = (data.get("nickName") or "").strip()
        name = (data.get("fullName") or "").strip()
        if nick == "StableNick" or name == "Stability Test User":
            to_delete.append((doc.id, data.get("playerId"), data.get("fullName")))

    if not to_delete:
        print("No StableNick / Stability Test User records found.")
        return

    print(f"Found {len(to_delete)} test player(s) to delete:")
    for doc_id, player_id, full_name in to_delete:
        print(f"  doc_id={doc_id}  playerId={player_id}  fullName={full_name!r}")

    if args.dry_run:
        print("\n[DRY RUN] No deletions performed.")
        return

    confirm = input(f"\nDelete {len(to_delete)} records from Firestore? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        return

    # Delete in batches of 500
    BATCH_SIZE = 500
    deleted = 0
    batch = db.batch()
    batch_count = 0

    for doc_id, _, _ in to_delete:
        batch.delete(players_ref.document(doc_id))
        batch_count += 1
        deleted += 1
        if batch_count >= BATCH_SIZE:
            batch.commit()
            print(f"  Committed batch of {batch_count}...")
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()

    print(f"\nDeleted {deleted} test player(s).")
    print("\nNext step: rebuild local pkl files:")
    print("  python scripts/refresh_local_pkls.py")


if __name__ == "__main__":
    main()
```

- [x] **Step 2: Make the script executable and verify it parses without error**

```bash
python scripts/cleanup_test_players.py --dry-run
```

Expected (with `USE_LOCAL_DATA=True` in env): exits with the local-mode error message. This confirms the guard works.

- [x] **Step 3: Commit**

```bash
git add scripts/cleanup_test_players.py
git commit -m "feat: add cleanup_test_players.py script to delete StableNick test records"
```

---

## Task 5: Unified ML Accuracy + Predictions Tab

**Spec:** Item 1 — M  
**Files:**
- Modify: `templates/admin.html`
- Modify: `static/js/admin_accuracy.js`

**What changes:**  
Remove the "Predictions" tab from the admin panel nav. Fold per-game detail into the ML Accuracy tab: clicking a week row expands inline per-game rows (fetched from existing `/api/admin/predictions/games` endpoint). The `/admin/predictions` Feature Debug page and Vegas Edge tab remain intact; `admin_predictions_games.js` keeps its null guards and works only on the `/admin/predictions` page.

- [x] **Step 1: Remove Predictions tab from `admin.html`**

Remove the tab button and the entire `#predictions-section` div:

**Tab bar** — delete the Predictions button:
```html
<!-- DELETE THIS LINE: -->
<button class="admin-tab-btn tab-btn" data-tab="predictions-section">Predictions</button>
```

**Section div** — delete the entire block from:
```html
<!-- Per-Game Predictions Tab -->
<div id="predictions-section" class="tab-content card-glass hidden" style="height: auto;">
```
...through to and including its closing `</div>`.

Also remove the script tag at the bottom of the template (only if `admin_predictions_games.js` is no longer needed on admin.html — keep it if the null guards mean it's harmless, but since all element references will be gone, remove to keep the page clean):

```html
<!-- DELETE THIS LINE: -->
<script type="module" src="/static/js/admin_predictions_games.js"></script>
```

- [x] **Step 2: Add helper functions to `admin_accuracy.js`**

At the top of `admin_accuracy.js`, after the `_bar()` function, add three helper functions copied from `admin_predictions_games.js` (inline, no import needed since this file doesn't use ES module imports):

```javascript
function _pgFmtSpread(line, home, away) {
    if (line == null) return '<span style="color:var(--text-secondary);">—</span>';
    if (line === 0)   return "Pick'em";
    const fav = line > 0 ? home : away;
    return `${fav} -${Math.abs(line).toFixed(1)}`;
}

function _pgCorrectIcon(isCorrect) {
    if (isCorrect === null || isCorrect === undefined)
        return '<span style="color:var(--text-secondary);">—</span>';
    return isCorrect
        ? '<span style="color:var(--accent-green); font-weight:700;">✓</span>'
        : '<span style="color:var(--accent-red);   font-weight:700;">✗</span>';
}

function _pgEdgeStr(ev, home, away) {
    if (ev == null) return '<span style="color:var(--text-secondary);">—</span>';
    const abs = Math.abs(ev);
    const dir = ev > 0 ? home : away;
    const cls = abs >= 3 ? 'edge-high' : abs >= 1.5 ? 'edge-mid' : 'edge-low';
    return `<span class="${cls}">${dir} +${abs.toFixed(1)}${abs >= 3 ? ' ⚡' : ''}</span>`;
}
```

(Prefixed with `_pg` to avoid any future naming collision.)

- [x] **Step 3: Add `loadGameDetail()` function to `admin_accuracy.js`**

Add after the three helpers:

```javascript
async function loadGameDetail(season, week, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (container.dataset.loaded === '1') return; // already fetched

    try {
        const _token  = localStorage.getItem('nfl_wins_token');
        const _headers = _token ? { 'Authorization': `Bearer ${_token}` } : {};
        const resp = await fetch(
            `/api/admin/predictions/games?season=${season}&week=${week}`,
            { headers: _headers }
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        container.dataset.loaded = '1';

        if (!data.games || !data.games.length) {
            container.innerHTML =
                '<div style="color:var(--text-secondary);text-align:center;padding:0.5rem 0;">No predictions for this week.</div>';
            return;
        }

        const gameRows = data.games.map(g => {
            const rowBg     = g.is_correct === false ? 'background:rgba(239,68,68,0.05);' : '';
            const pickColor = g.pred_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const actColor  = g.actual_winner === g.home_team ? 'var(--accent-green)' : 'var(--accent-gold)';
            const debugUrl  = `/admin/predictions?season=${season}&week=${week}&home=${g.home_team}&away=${g.away_team}`;
            return `<tr style="${rowBg}">
                <td style="padding:4px 8px;">
                    <span style="font-weight:600;">${g.away_team} @ ${g.home_team}</span>
                    <a href="${debugUrl}" target="_blank"
                        title="Feature Debug"
                        style="margin-left:6px; color:var(--text-secondary); font-size:0.7rem; text-decoration:none;">🔍</a>
                </td>
                <td style="padding:4px 8px; color:${pickColor};">
                    ${g.pred_winner ?? '—'}
                    ${g.pred_su_conf != null ? `<span style="color:var(--text-secondary);font-size:0.72rem;">${g.pred_su_conf}%</span>` : ''}
                </td>
                <td style="padding:4px 8px; color:${g.actual_winner ? actColor : 'var(--text-secondary)'};">
                    ${g.actual_winner ?? '—'}
                </td>
                <td style="padding:4px 8px; text-align:center;">${_pgCorrectIcon(g.is_correct)}</td>
                <td style="padding:4px 8px;">${_pgFmtSpread(g.model_spread, g.home_team, g.away_team)}</td>
                <td style="padding:4px 8px;">${_pgFmtSpread(g.vegas_line, g.home_team, g.away_team)}</td>
                <td style="padding:4px 8px;">${_pgEdgeStr(g.edge_vs_vegas, g.home_team, g.away_team)}</td>
            </tr>`;
        }).join('');

        container.innerHTML = `
            <table style="width:100%; border-collapse:collapse; font-size:0.8rem; margin-top:0.25rem;">
                <thead>
                    <tr style="color:var(--text-secondary);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;">
                        <th style="padding:4px 8px;text-align:left;">Matchup</th>
                        <th style="padding:4px 8px;text-align:left;">Model Pick</th>
                        <th style="padding:4px 8px;text-align:left;">Actual</th>
                        <th style="padding:4px 8px;text-align:center;">✓/✗</th>
                        <th style="padding:4px 8px;text-align:left;">Model Line</th>
                        <th style="padding:4px 8px;text-align:left;">Vegas</th>
                        <th style="padding:4px 8px;text-align:left;">Edge</th>
                    </tr>
                </thead>
                <tbody>${gameRows}</tbody>
            </table>`;
    } catch (err) {
        container.innerHTML =
            `<div style="color:var(--accent-red);padding:0.5rem 0;font-size:0.8rem;">Error: ${err.message}</div>`;
    }
}
```

- [x] **Step 4: Replace `renderWeekPanel()` in `admin_accuracy.js`**

Replace the entire `renderWeekPanel(seasonData)` function with the new version that makes week rows clickable and inserts per-game expansion rows:

```javascript
function renderWeekPanel(seasonData) {
    const panel = document.getElementById('acc-week-panel');
    const title = document.getElementById('acc-week-title');
    const table = document.getElementById('acc-week-table');

    title.textContent = `${seasonData.season} — Week-by-Week Accuracy`;

    const rows = seasonData.by_week.map(w => `
        <tr class="acc-week-row" data-season="${seasonData.season}" data-week="${w.week}"
            style="cursor:pointer; transition:background 0.15s;"
            onmouseover="this.style.background='rgba(255,255,255,0.04)'"
            onmouseout="this.style.background=''">
            <td style="padding:8px 14px; font-weight:600;">
                Week ${w.week}
                <span style="font-size:0.65rem; color:var(--text-secondary); margin-left:4px;">▶</span>
            </td>
            <td style="padding:8px 14px; text-align:right;">${w.correct}/${w.total}</td>
            <td style="padding:8px 14px; min-width:160px;">${_bar(w.accuracy)}</td>
        </tr>
        <tr class="acc-game-expansion" data-expansion-week="${w.week}" style="display:none;">
            <td colspan="3" style="padding:0.5rem 1rem 0.75rem 2rem; background:rgba(0,0,0,0.2);">
                <div id="acc-games-${seasonData.season}-${w.week}" style="font-size:0.82rem;"></div>
            </td>
        </tr>
    `).join('');

    table.innerHTML = `
        <table style="width:100%; border-collapse:collapse; font-size:0.9rem;">
            <thead>
                <tr style="border-bottom:1px solid var(--glass-border); color:var(--text-secondary); font-size:0.75rem; text-transform:uppercase; letter-spacing:0.06em;">
                    <th style="padding:6px 14px; text-align:left;">Week</th>
                    <th style="padding:6px 14px; text-align:right;">Correct / Total</th>
                    <th style="padding:6px 14px; text-align:left;">SU Accuracy</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>
    `;

    table.querySelectorAll('.acc-week-row').forEach(row => {
        row.addEventListener('click', () => {
            const season = parseInt(row.dataset.season);
            const week   = parseInt(row.dataset.week);
            const expansion = table.querySelector(`[data-expansion-week="${week}"]`);
            if (!expansion) return;
            const isOpen = expansion.style.display !== 'none';
            expansion.style.display = isOpen ? 'none' : '';
            if (!isOpen) {
                loadGameDetail(season, week, `acc-games-${season}-${week}`);
            }
            // Toggle chevron direction
            const chevron = row.querySelector('span[style*="▶"]') ||
                            row.querySelector('span');
            if (chevron) chevron.textContent = isOpen ? '▶' : '▼';
        });
    });

    panel.style.display = 'block';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
```

- [x] **Step 5: Run tests**

```bash
pytest tests/ -q
```

Expected: all pass (no tests for admin JS).

- [x] **Step 6: Commit**

```bash
git add templates/admin.html static/js/admin_accuracy.js
git commit -m "feat: inline per-game predictions in ML Accuracy tab, remove standalone Predictions tab"
```

---

## Task 6: DRY — Shared Utilities (Password Regex + Name Abbreviation + filter_season)

**Spec:** Item 6A, 6B, 6D — M  
**Files:**
- Modify: `services/constants.py`
- Modify: `services/utils.py`
- Modify: `routes/auth_routes.py`
- Modify: `routes/admin_routes.py`
- Modify: `routes/history_routes.py`
- Modify: `routes/standings_routes.py`

⚠️ **Task 8 depends on `PASSWORD_COMPLEXITY_RE` being in `services/constants.py` — do this task before Task 8.**

### 6A: Password complexity regex

- [x] **Step 1: Write a failing test for the constant**

```python
# In tests/test_auth.py — add temporarily to verify import
def test_password_complexity_re_importable():
    from services.constants import PASSWORD_COMPLEXITY_RE
    import re
    assert re.match(PASSWORD_COMPLEXITY_RE, "Valid1!LongEnoughPwd") is not None
```

Run: `pytest tests/test_auth.py::test_password_complexity_re_importable -v`  
Expected: **FAIL** with `ImportError: cannot import name 'PASSWORD_COMPLEXITY_RE'`

- [x] **Step 2: Add `PASSWORD_COMPLEXITY_RE` to `services/constants.py`**

Append to `services/constants.py`:

```python
# Password complexity — enforced in auth_routes.py, admin_routes.py.
# 12+ chars, must include uppercase, lowercase, digit, and special character.
PASSWORD_COMPLEXITY_RE = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
```

- [x] **Step 3: Run the test — expect PASS**

```bash
pytest tests/test_auth.py::test_password_complexity_re_importable -v
```

Expected: **PASS**

- [x] **Step 4: Replace inline regex in `auth_routes.py` (2 occurrences)**

At the top of `routes/auth_routes.py`, add to the imports:

```python
from services.constants import PASSWORD_COMPLEXITY_RE
```

Find the first inline regex (around line 100):

```python
pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
if not re.match(pw_regex, password):
```

Replace with:

```python
if not re.match(PASSWORD_COMPLEXITY_RE, password):
```

Find the second inline regex (around line 265):

```python
pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
if not re.match(pw_regex, new_password):
```

Replace with:

```python
if not re.match(PASSWORD_COMPLEXITY_RE, new_password):
```

- [x] **Step 5: Replace inline regex in `admin_routes.py`**

Add import at the top:

```python
from services.constants import PASSWORD_COMPLEXITY_RE
```

Find the inline regex (around line 227):

```python
pw_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$"
if not re.match(pw_regex, body.tempPassword):
```

Replace with:

```python
if not re.match(PASSWORD_COMPLEXITY_RE, body.tempPassword):
```

- [x] **Step 6: Run tests**

```bash
pytest tests/ -q
```

Expected: all pass.

### 6B: `abbreviate_player_name` utility

- [x] **Step 7: Add `abbreviate_player_name()` to `services/utils.py`**

Append to `services/utils.py`:

```python
def abbreviate_player_name(name: str) -> str:
    """Return 'J. Smith' from 'John Smith', or the original if single-word.
    
    Special cases:
    - 'Undrafted' → 'Undrafted'
    - 'Overall Record' → 'Overall'
    - empty / None → returned as-is
    """
    if not name:
        return name
    if name == 'Undrafted':
        return 'Undrafted'
    if name == 'Overall Record':
        return 'Overall'
    parts = str(name).strip().split()
    if len(parts) > 1:
        return f"{parts[0][0]}. {' '.join(parts[1:])}"
    return parts[0] if parts else name
```

- [x] **Step 8: Replace `_first_name()` in `routes/history_routes.py`**

Add to the imports at the top:

```python
from services.utils import abbreviate_player_name as _first_name
```

Delete the local `_first_name` definition (lines 14–22):

```python
def _first_name(name: str) -> str:
    if not name or name == 'Undrafted':
        return name or 'Undrafted'
    if name == 'Overall Record':
        return 'Overall'
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return parts[0] if parts else name
```

- [x] **Step 9: Replace `_first_name()` in `routes/standings_routes.py`**

Add to the imports at the top:

```python
from services.utils import abbreviate_player_name as _first_name
```

Delete the local `_first_name` definition (lines 23–31):

```python
def _first_name(name: str) -> str:
    if not name or name == 'Undrafted':
        return name or 'Undrafted'
    if name == 'Overall Record':
        return 'Overall'
    parts = str(name).strip().split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}."
    return parts[0] if parts else name
```

### 6D: `filter_season` utility

- [x] **Step 10: Add `filter_season()` to `services/utils.py`**

Append to `services/utils.py`:

```python
def filter_season(df: "pd.DataFrame", year: int) -> "pd.DataFrame":
    """Return rows where df['season'] == year, or df unchanged if empty or no 'season' column."""
    import pandas as pd
    if not isinstance(df, pd.DataFrame) or df.empty or 'season' not in df.columns:
        return df
    return df[df['season'] == year]
```

- [x] **Step 11: Replace inline season-filter in `standings_routes.py`**

In `wins_pool_by_year()`, replace (lines ~53-56):

```python
standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
games = all_games[all_games['season'] == year] if not all_games.empty else all_games
draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results
```

With:

```python
from services.utils import filter_season
standings     = filter_season(all_st, year)
games         = filter_season(all_games, year)
draft_results = filter_season(all_draft_results, year)
```

Repeat the same replacement in `wins_pool_weekbyweek()` (lines ~95-97) and `playoff_race_by_year()` (lines ~125-128).

Add to the imports at the top of `standings_routes.py`:

```python
from services.utils import abbreviate_player_name as _first_name, filter_season
```

- [x] **Step 12: Replace inline season-filter in `history_routes.py`**

In the relevant route handler (around lines 183-185), replace:

```python
standings = all_st[all_st['season'] == year] if not all_st.empty else all_st
games = all_games[all_games['season'] == year] if not all_games.empty else all_games
draft_results = all_draft_results[all_draft_results['season'] == year] if not all_draft_results.empty else all_draft_results
```

With:

```python
from services.utils import filter_season
standings     = filter_season(all_st, year)
games         = filter_season(all_games, year)
draft_results = filter_season(all_draft_results, year)
```

Add `filter_season` to the import at top of `history_routes.py`:

```python
from services.utils import abbreviate_player_name as _first_name, filter_season
```

- [x] **Step 13: Run full test suite**

```bash
pytest tests/ -q
```

Expected: all pass.

- [x] **Step 14: Commit**

```bash
git add services/constants.py services/utils.py routes/auth_routes.py routes/admin_routes.py routes/history_routes.py routes/standings_routes.py
git commit -m "refactor: DRY — PASSWORD_COMPLEXITY_RE constant, abbreviate_player_name + filter_season utilities (#22)"
```

---

## Task 7: DRY — DataBundle NamedTuple

**Spec:** Item 6C — S  
**Files:**
- Modify: `services/data_service.py`
- Modify: `services/draft_service.py`
- Modify: `services/sandbox_service.py`

`load_data()` returns a bare 7-tuple. Converting it to a `NamedTuple` is **non-breaking** because `NamedTuple` is a tuple subclass — all 32+ existing positional unpackings (`a, b, c, ... = load_data()`) continue to work. New code can use named access (`data.games` instead of index 2).

- [x] **Step 1: Add `DataBundle` NamedTuple to `services/data_service.py`**

After the existing imports at the top of `data_service.py`, add:

```python
from typing import NamedTuple

class DataBundle(NamedTuple):
    """Named 7-tuple returned by load_data(). All fields are pandas DataFrames."""
    standings:          "pd.DataFrame"
    teams:              "pd.DataFrame"
    games:              "pd.DataFrame"
    players:            "pd.DataFrame"
    draft_order:        "pd.DataFrame"
    draft_results:      "pd.DataFrame"
    draft_order_rules:  "pd.DataFrame"
```

- [x] **Step 2: Change `load_data()` return from bare tuple to `DataBundle`**

Find (near line 211):

```python
res = (standings, teams, games, players, draft_order, draft_results, draft_order_rules)
_DATA_CACHE[key] = res
```

Replace with:

```python
res = DataBundle(standings, teams, games, players, draft_order, draft_results, draft_order_rules)
_DATA_CACHE[key] = res
```

- [x] **Step 3: Update `load_data_season()` to use named access**

Find in `load_data_season()`:

```python
standings, teams, games, players, draft_order, draft_results, rules = load_data()
```

Replace with:

```python
bundle = load_data()
```

And update the return:

```python
return (
    _filter(bundle.standings),
    bundle.teams,
    _filter(bundle.games),
    bundle.players,
    bundle.draft_order,
    _filter(bundle.draft_results),
    bundle.draft_order_rules,
)
```

- [x] **Step 4: Update `draft_service.py` internal call sites**

In `services/draft_service.py`, find (around line 58):

```python
_, _, _, _, d_order, _, _ = load_data()
```

Replace with:

```python
d_order = load_data().draft_order
```

Find (around line 78):

```python
standings, _, games_master, players_df, d_order, results, rules = load_data(year=season)
```

Replace with:

```python
_bundle = load_data(year=season)
standings, games_master, players_df = _bundle.standings, _bundle.games, _bundle.players
d_order, results, rules = _bundle.draft_order, _bundle.draft_results, _bundle.draft_order_rules
```

- [x] **Step 5: Update `sandbox_service.py` internal call site**

In `services/sandbox_service.py`, find:

```python
_, _, prior_games, _, _, _, _ = load_data(year=year - 1)
```

Replace with:

```python
prior_games = load_data(year=year - 1).games
```

- [x] **Step 6: Run full test suite**

```bash
pytest tests/ -q
```

Expected: all pass (positional unpacking in route files continues to work unchanged).

- [x] **Step 7: Commit**

```bash
git add services/data_service.py services/draft_service.py services/sandbox_service.py
git commit -m "refactor: DataBundle NamedTuple for load_data() — named access for internal callers (#22)"
```

---

## Task 8: Test Quality — Fill Empty Test + Shared Fixture + Parameterized Passwords + UUID Emails

**Spec:** Item 7A, 7B, 7C, 7D — M  
**Files:**
- Modify: `tests/test_cache_service.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_recap_service.py`
- Modify: `tests/test_auth.py`
- Modify: `tests/test_db.py`

⚠️ **Requires Task 6 to be complete** — `PASSWORD_COMPLEXITY_RE` must exist in `services/constants.py`.

### 7A: Fill in empty cache test

- [x] **Step 1: Verify the test currently passes but asserts nothing**

```bash
pytest tests/test_cache_service.py::test_local_cache_read_write -v
```

Expected: **PASS** (vacuously — has `pass` as body).

- [x] **Step 2: Replace the empty test body**

Replace the entire `test_local_cache_read_write` function:

```python
def test_local_cache_read_write(tmp_path, monkeypatch):
    """Round-trip: write analytics cache entry, read it back, assert equal."""
    monkeypatch.setattr("services.cache_service._USE_LOCAL", True)
    monkeypatch.setattr("services.cache_service._LOCAL_CACHE_DIR", tmp_path)
    from services.cache_service import write_cache, get_cached
    payload = {"test_key": 42, "nested": {"a": 1}}
    write_cache("test_metric", 2024, 1, payload)
    result = get_cached("test_metric", 2024, 1)
    assert result == payload
```

Note: The function signature changes (removes `mock_open, mock_env_vars`, adds `tmp_path, monkeypatch`). pytest injects `tmp_path` and `monkeypatch` automatically as fixtures.

- [x] **Step 3: Run the test — expect PASS with real assertion**

```bash
pytest tests/test_cache_service.py::test_local_cache_read_write -v
```

Expected: **PASS** (round-trip verified).

### 7B: Shared `sample_games_df` fixture in conftest.py

- [x] **Step 4: Add `sample_games_df` fixture to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
import pandas as pd

@pytest.fixture
def sample_games_df():
    """Minimal NFL games DataFrame (3 rows) for unit tests that need a games structure.

    Covers: 2 completed games (weeks 1) and 1 unplayed game (week 2, result=-1000).
    Shape: season, week, home_team, away_team, home_score, away_score, result, spread_line.
    """
    return pd.DataFrame([
        {"season": 2024, "week": 1, "home_team": "KC",  "away_team": "BUF",
         "home_score": 27, "away_score": 24, "result": 3.0,   "spread_line": -2.5},
        {"season": 2024, "week": 1, "home_team": "PHI", "away_team": "DAL",
         "home_score": 22, "away_score": 16, "result": 6.0,   "spread_line":  3.5},
        {"season": 2024, "week": 2, "home_team": "SF",  "away_team": "SEA",
         "home_score": 0,  "away_score": 0,  "result": -1000, "spread_line": -4.0},
    ])
```

- [x] **Step 5: Update `test_recap_service.py` to use `sample_games_df` fixture**

In `test_extract_weekly_data`, the inline `games` DataFrame is passed to `mock_load_data.return_value`. Replace it with the fixture:

Change the function signature:

```python
@patch("services.recap_service.load_data")
def test_extract_weekly_data(mock_load_data, sample_games_df):
```

Replace the inline `games = pd.DataFrame([...])` with `games = sample_games_df`. The test assertions check `data_summary` content from `mock_enriched`, not the raw games, so this is a safe swap.

- [x] **Step 6: Run tests to verify fixture wiring**

```bash
pytest tests/test_recap_service.py -v
```

Expected: all pass.

### 7C: Parameterized password complexity tests

- [x] **Step 7: Add parameterized tests to `tests/test_auth.py`**

Append to `tests/test_auth.py`:

```python
import re

@pytest.mark.parametrize("pw,should_match", [
    # Too short
    ("Short1!",                 False),
    # No uppercase
    ("alllowercase1!longpwd",   False),
    # No lowercase
    ("ALLUPPERCASE1!LONGPWD",   False),
    # No special character
    ("NoSpecialChar12345678",   False),
    # No digit
    ("NoNumbers!!LongEnough",   False),
    # Valid passwords
    ("Valid1!LongEnoughPwd",    True),
    ("Another$Valid1Password",  True),
    # Exactly 12 chars (boundary)
    ("A1!aaaaaaaaa",            True),
    # Long valid
    ("A1!aaaaaaaaaaaaaaaaaa",   True),
    # 11 chars — too short
    ("A1!aaaaaaaa",             False),
])
def test_password_complexity(pw, should_match):
    """PASSWORD_COMPLEXITY_RE must accept/reject exactly the documented cases."""
    from services.constants import PASSWORD_COMPLEXITY_RE
    assert bool(re.match(PASSWORD_COMPLEXITY_RE, pw)) == should_match
```

- [x] **Step 8: Run the parameterized tests**

```bash
pytest tests/test_auth.py::test_password_complexity -v
```

Expected: 10/10 pass.

### 7D: UUID emails in `test_db.py`

- [x] **Step 9: Replace hardcoded email in `test_db.py`**

At the top of `tests/test_db.py`, add:

```python
import uuid

def _test_email():
    """Generate a unique test email to prevent Firestore collision on re-runs."""
    return f"test_{uuid.uuid4().hex[:8]}@example.com"
```

Find:

```python
test_email = "test_stability_verify@example.com"
```

Replace with:

```python
test_email = _test_email()
```

- [x] **Step 10: Run full test suite**

```bash
pytest tests/ -q
```

Expected: all pass.

- [x] **Step 11: Commit**

```bash
git add tests/test_cache_service.py tests/conftest.py tests/test_recap_service.py tests/test_auth.py tests/test_db.py
git commit -m "test: fill empty cache test, shared fixture, parameterized passwords, UUID emails (#18)"
```

---

## Task 9: Split `test_prediction_service.py` into Three Files

**Spec:** Item 7E — M  
**Files:**
- Create: `tests/test_elo.py`
- Create: `tests/test_game_prediction.py`
- Create: `tests/test_portfolio.py`
- Delete: `tests/test_prediction_service.py`

The 556-line / 13-class test file is split by concern. The `_make_games_df()` helper is moved to `conftest.py` as a `prediction_games_df` fixture so all three new files share it.

### Add shared fixture to conftest.py

- [x] **Step 1: Add `prediction_games_df` fixture to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def prediction_games_df():
    """7-game DataFrame across 2 seasons (2023 + 2024) for Elo/prediction tests.
    
    2023: 4 games (KC, DET, BUF, NYJ — all completed).
    2024: 2 completed + 1 unplayed (result=None).
    """
    return pd.DataFrame([
        # 2023 Season
        {"game_id": "2023_01_KC_DET",  "season": 2023, "week": 1, "game_type": "REG",
         "home_team": "KC",  "away_team": "DET", "home_score": 21, "away_score": 20, "result": 1},
        {"game_id": "2023_01_BUF_NYJ", "season": 2023, "week": 1, "game_type": "REG",
         "home_team": "BUF", "away_team": "NYJ", "home_score": 22, "away_score": 16, "result": 6},
        {"game_id": "2023_02_DET_KC",  "season": 2023, "week": 2, "game_type": "REG",
         "home_team": "DET", "away_team": "KC",  "home_score": 31, "away_score": 17, "result": 14},
        {"game_id": "2023_02_NYJ_BUF", "season": 2023, "week": 2, "game_type": "REG",
         "home_team": "NYJ", "away_team": "BUF", "home_score": 10, "away_score": 24, "result": -14},
        # 2024 Season
        {"game_id": "2024_01_KC_BUF",  "season": 2024, "week": 1, "game_type": "REG",
         "home_team": "KC",  "away_team": "BUF", "home_score": 27, "away_score": 20, "result": 7},
        {"game_id": "2024_01_DET_NYJ", "season": 2024, "week": 1, "game_type": "REG",
         "home_team": "DET", "away_team": "NYJ", "home_score": 35, "away_score": 14, "result": 21},
        {"game_id": "2024_02_BUF_KC",  "season": 2024, "week": 2, "game_type": "REG",
         "home_team": "BUF", "away_team": "KC",  "home_score": None, "away_score": None, "result": None},
    ])
```

### Create test_elo.py

- [x] **Step 2: Create `tests/test_elo.py`**

```python
"""tests/test_elo.py — Elo math, travel distance, and PredictionService init tests.

Covers: TestHaversine, TestWinProbability, TestMoVMultiplier,
        TestPreseasonReversion, TestPredictionServiceInit.
"""

import pytest
import math
from services.prediction_service import (
    _haversine_miles,
    _get_travel_distance,
    win_probability,
    margin_of_victory_multiplier,
    compute_elo_shift,
    apply_preseason_reversion,
    PredictionService,
    ELO_MEAN,
    STADIUM_COORDS,
)


class TestHaversine:
    """Validates the Haversine great-circle distance calculation."""

    def test_same_point_returns_zero(self):
        assert _haversine_miles(40.0, -74.0, 40.0, -74.0) == 0.0

    def test_known_distance_nyc_to_la(self):
        """NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) is ~2,451 miles."""
        dist = _haversine_miles(40.7128, -74.0060, 34.0522, -118.2437)
        assert 2400 < dist < 2500

    def test_shared_stadium_zero_travel(self):
        """NYG and NYJ share MetLife Stadium; travel distance should be 0."""
        assert _get_travel_distance("NYG", "NYJ") == 0.0
        assert _get_travel_distance("LA", "LAC") == 0.0

    def test_cross_country_travel(self):
        """SEA to MIA should be > 2,500 miles."""
        assert _get_travel_distance("SEA", "MIA") > 2500

    def test_unknown_team_returns_zero(self):
        assert _get_travel_distance("FAKE", "KC") == 0.0

    def test_all_teams_have_coords(self):
        current_teams = [
            "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
            "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
            "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
            "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
        ]
        for team in current_teams:
            assert team in STADIUM_COORDS, f"Missing coordinates for {team}"


class TestWinProbability:
    """Validates the FiveThirtyEight logistic win probability formula."""

    def test_equal_teams_fifty_fifty(self):
        assert abs(win_probability(1500, 1500) - 0.5) < 0.001

    def test_200_point_advantage(self):
        prob = win_probability(1600, 1400)
        assert 0.75 < prob < 0.77

    def test_400_point_advantage(self):
        prob = win_probability(1700, 1300)
        assert 0.90 < prob < 0.92

    def test_adjustments_shift_probability(self):
        neutral = win_probability(1500, 1500, adjustments=0.0)
        home    = win_probability(1500, 1500, adjustments=48.0)
        assert home > neutral
        assert home > 0.55

    def test_symmetry(self):
        prob_a = win_probability(1550, 1450)
        prob_b = win_probability(1450, 1550)
        assert abs((prob_a + prob_b) - 1.0) < 0.001

    def test_probabilities_bounded(self):
        assert 0 < win_probability(2000, 1000) < 1
        assert 0 < win_probability(1000, 2000) < 1


class TestMoVMultiplier:
    """Validates the log-scaled MoV multiplier with autocorrelation correction."""

    def test_close_game_low_multiplier(self):
        mult = margin_of_victory_multiplier(1.0, 0.0)
        assert 0.5 < mult < 1.5

    def test_blowout_higher_multiplier(self):
        close   = margin_of_victory_multiplier(3.0, 0.0)
        blowout = margin_of_victory_multiplier(28.0, 0.0)
        assert blowout > close

    def test_diminishing_returns(self):
        m14 = margin_of_victory_multiplier(14.0, 0.0)
        m28 = margin_of_victory_multiplier(28.0, 0.0)
        m42 = margin_of_victory_multiplier(42.0, 0.0)
        assert (m42 - m28) < (m28 - m14)

    def test_autocorrelation_correction(self):
        fav_mult = margin_of_victory_multiplier(14.0,  200.0)
        dog_mult = margin_of_victory_multiplier(14.0, -200.0)
        assert dog_mult > fav_mult


class TestPreseasonReversion:
    """Validates the 1/3 mean-reversion to 1505."""

    def test_reversion_formula(self):
        reverted = apply_preseason_reversion(1700.0)
        expected = (1700.0 * 2 / 3) + (1505 * 1 / 3)
        assert abs(reverted - expected) < 0.01

    def test_mean_team_stays(self):
        assert abs(apply_preseason_reversion(1505.0) - 1505.0) < 0.01

    def test_bad_team_improves(self):
        reverted = apply_preseason_reversion(1300.0)
        assert reverted > 1300.0
        assert reverted < 1505.0


class TestPredictionServiceInit:
    """Tests for Elo initialization and rating computation."""

    def test_initialize_produces_ratings(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        ratings = svc.get_all_ratings()
        assert "KC" in ratings and "BUF" in ratings
        assert "DET" in ratings and "NYJ" in ratings

    def test_winners_gain_elo(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        assert svc._elo_ratings["KC"] > svc._elo_ratings["NYJ"]
        assert svc._elo_ratings["BUF"] > svc._elo_ratings["NYJ"]

    def test_preseason_reversion_applied(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        for elo in svc._elo_ratings.values():
            assert 1300 < elo < 1700

    def test_scoring_aggregates_computed(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        kc_scoring = svc._team_scoring.get("KC", {})
        assert kc_scoring["points_for"] == 27
        assert kc_scoring["games_played"] == 1
```

- [x] **Step 3: Run `test_elo.py` to verify it passes**

```bash
pytest tests/test_elo.py -v
```

Expected: all pass.

### Create test_game_prediction.py

- [x] **Step 4: Create `tests/test_game_prediction.py`**

```python
"""tests/test_game_prediction.py — Pythagorean expectation + single-game prediction tests.

Covers: TestPythagoreanExpectation, TestGamePrediction.
"""

import pytest
from services.prediction_service import (
    pythagorean_win_pct,
    pythagorean_projected_wins,
    PredictionService,
)


class TestPythagoreanExpectation:
    """Validates the Frontiers NFL Pythagorean model (exponent 2.37)."""

    def test_equal_scoring_fifty_percent(self):
        assert abs(pythagorean_win_pct(350, 350) - 0.5) < 0.001

    def test_dominant_offense(self):
        assert pythagorean_win_pct(400, 300) > 0.6

    def test_weak_team(self):
        assert pythagorean_win_pct(250, 400) < 0.4

    def test_projected_wins_reasonable(self):
        wins = pythagorean_projected_wins(400, 300, total_games=17)
        assert 10.0 < wins < 13.0

    def test_zero_scoring_fallback(self):
        assert pythagorean_win_pct(0, 0) == 0.5


class TestGamePrediction:
    """Tests for single-game win probability output."""

    def test_game_prediction_keys(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        expected_keys = {
            "home_team", "away_team", "home_win_prob", "away_win_prob",
            "elo_home_prob", "pyth_home_prob", "home_elo", "away_elo",
            "adjustments", "travel_miles", "elo_weight", "predicted_spread",
        }
        assert expected_keys.issubset(set(result.keys()))

    def test_probabilities_sum_to_one(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert abs(result["home_win_prob"] + result["away_win_prob"] - 1.0) < 0.001

    def test_home_advantage_reflected(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.game_win_probability("KC", "BUF")
        assert result["adjustments"] > 0
```

- [x] **Step 5: Run `test_game_prediction.py`**

```bash
pytest tests/test_game_prediction.py -v
```

Expected: all pass.

### Create test_portfolio.py

- [x] **Step 6: Create `tests/test_portfolio.py`**

```python
"""tests/test_portfolio.py — Portfolio projection, draft confidence, team summary,
season teams, defunct team filtering, projected wins, and schedule enrichment tests.

Covers: TestPortfolioProjection, TestDraftConfidence, TestTeamSummary,
        TestSeasonTeams, TestDefunctTeamFiltering, TestTeamProjectedWins,
        TestScheduleEnrichment.
"""

import pytest
import pandas as pd
from services.prediction_service import (
    PredictionService,
    _get_season_teams,
    enrich_schedule_with_predictions,
)


class TestPortfolioProjection:
    """Tests for Monte Carlo portfolio simulation."""

    def test_portfolio_projection_structure(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=50)
        expected_keys = {"mean_wins", "std_wins", "min_wins", "max_wins",
                         "actual_wins", "projected_additional", "simulations"}
        assert expected_keys.issubset(set(result.keys()))

    def test_actual_wins_counted(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=50)
        assert result["actual_wins"]["KC"] == 1
        assert result["actual_wins"]["DET"] == 1

    def test_completed_season_no_simulation(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2023)
        result = svc.project_portfolio_wins(["KC", "BUF"], 2023, prediction_games_df)
        assert result["simulations"] == 0
        assert result["season_complete"] is True

    def test_mean_wins_reasonable(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        result = svc.project_portfolio_wins(["KC", "DET"], 2024, prediction_games_df, n_simulations=100)
        assert result["mean_wins"] >= 2.0
        assert result["mean_wins"] <= 10.0


class TestDraftConfidence:
    """Tests for draft room confidence scoring."""

    def test_confidence_scores_sorted(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        confidences = [s["confidence"] for s in scores]
        assert confidences == sorted(confidences, reverse=True)

    def test_drafted_teams_excluded(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df, drafted_teams=["KC"])
        assert "KC" not in [s["team"] for s in scores]

    def test_ranks_sequential(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        ranks = [s["rank"] for s in scores]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_confidence_bounded(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        for s in scores:
            assert 0.0 <= s["confidence"] <= 1.0


class TestTeamSummary:
    """Tests for individual team summary output."""

    def test_summary_keys(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        summary = svc.get_team_summary("KC")
        expected = {"team", "elo", "elo_rank", "points_for", "points_against",
                    "games_played", "pythagorean_win_pct", "pythagorean_projected_wins",
                    "bye_weeks"}
        assert expected.issubset(set(summary.keys()))

    def test_summary_values_plausible(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        summary = svc.get_team_summary("KC")
        assert summary["points_for"] == 27
        assert summary["games_played"] == 1


class TestSeasonTeams:
    """Tests for _get_season_teams helper."""

    def test_returns_teams_from_season(self, prediction_games_df):
        teams = _get_season_teams(prediction_games_df, 2024)
        assert "KC" in teams and "BUF" in teams
        assert "DET" in teams and "NYJ" in teams

    def test_no_extra_seasons(self, prediction_games_df):
        teams_2024 = _get_season_teams(prediction_games_df, 2024)
        assert len(teams_2024) == 4

    def test_empty_season(self, prediction_games_df):
        teams = _get_season_teams(prediction_games_df, 2030)
        assert len(teams) == 0


class TestDefunctTeamFiltering:
    """Tests that defunct teams (SD, STL, OAK) are excluded from modern season outputs."""

    def test_confidence_excludes_defunct_teams(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        svc._elo_ratings["SD"]  = 1500.0
        svc._elo_ratings["STL"] = 1500.0
        svc._elo_ratings["OAK"] = 1500.0
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        team_list = [s["team"] for s in scores]
        assert "SD" not in team_list
        assert "STL" not in team_list
        assert "OAK" not in team_list

    def test_only_season_teams_in_confidence(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        scores = svc.generate_draft_confidence_scores(2024, prediction_games_df)
        assert len([s["team"] for s in scores]) == 4


class TestTeamProjectedWins:
    """Tests for get_team_projected_wins."""

    def test_returns_only_season_teams(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        projections = svc.get_team_projected_wins(prediction_games_df)
        assert "KC" in projections and "BUF" in projections
        assert len(projections) == 4

    def test_projections_are_floats(self, prediction_games_df):
        svc = PredictionService()
        svc.initialize(prediction_games_df, 2024)
        projections = svc.get_team_projected_wins(prediction_games_df)
        for team, wins in projections.items():
            assert isinstance(wins, float)


class TestScheduleEnrichment:
    """Tests for enrich_schedule_with_predictions."""

    def test_unplayed_games_get_predictions(self, prediction_games_df):
        schedule = pd.DataFrame([{
            "home_team": "BUF", "away_team": "KC", "result": -1000,
            "spread_line": -3.0, "week": 2,
        }])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert enriched.iloc[0]["pred_winner"] is not None
        assert 50.0 <= enriched.iloc[0]["pred_su_conf"] <= 99.0

    def test_completed_games_skipped(self, prediction_games_df):
        schedule = pd.DataFrame([{
            "home_team": "KC", "away_team": "BUF", "result": 7,
            "home_score": 27, "away_score": 20, "spread_line": -3.0, "week": 1,
        }])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert enriched.iloc[0]["pred_winner"] is None

    def test_columns_added(self, prediction_games_df):
        schedule = pd.DataFrame([{
            "home_team": "DET", "away_team": "NYJ", "result": -1000,
            "spread_line": 7.0, "week": 3,
        }])
        enriched = enrich_schedule_with_predictions(schedule, prediction_games_df, 2024)
        assert "pred_winner" in enriched.columns
        assert "pred_su_conf" in enriched.columns
        assert "pred_ats_pick" in enriched.columns
```

- [x] **Step 7: Run all three new test files**

```bash
pytest tests/test_elo.py tests/test_game_prediction.py tests/test_portfolio.py -v
```

Expected: all pass.

- [x] **Step 8: Delete `tests/test_prediction_service.py`**

```bash
# PowerShell
Remove-Item tests\test_prediction_service.py
```

Or on Unix:

```bash
rm tests/test_prediction_service.py
```

- [x] **Step 9: Run full test suite — no regressions**

```bash
pytest tests/ -q
```

Expected: all pass, same count as before deletion (new files replace the old one 1-for-1).

- [x] **Step 10: Commit**

```bash
git add tests/conftest.py tests/test_elo.py tests/test_game_prediction.py tests/test_portfolio.py
git rm tests/test_prediction_service.py
git commit -m "test: split test_prediction_service.py into test_elo/game_prediction/portfolio (#18)"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Item | Task | Status |
|---|---|---|
| 1. Unified ML Accuracy + Predictions tab | Task 5 | ✅ |
| 2. Elo tooltip sorted by highest | Task 1 | ✅ |
| 3. StableNick cleanup script | Task 4 | ✅ |
| 4. Wins pool dropdown 2013 bug | Task 3 | ✅ |
| 5. Schedule: kickoff time instead of "Live" | Task 2 | ✅ |
| 6A. Password regex DRY | Task 6 | ✅ |
| 6B. `_first_name` DRY | Task 6 | ✅ |
| 6C. DataBundle NamedTuple | Task 7 | ✅ |
| 6D. `filter_season` DRY | Task 6 | ✅ |
| 7A. Fill empty cache test | Task 8 | ✅ |
| 7B. Shared `sample_games_df` fixture | Task 8 | ✅ |
| 7C. Parameterized password tests | Task 8 | ✅ |
| 7D. UUID emails in test_db.py | Task 8 | ✅ |
| 7E. Split test_prediction_service.py | Task 9 | ✅ |

**Spec Item 3 note**: Spec mentions querying by `nickName == "StableNick"` OR `fullName == "Stability Test User"`. Firestore doesn't support OR queries across different fields natively — Task 4 handles this by streaming all documents and filtering in Python.

**Spec Item 6B note**: The spec's `abbreviate_player_name` changes format from `"Thomas F."` to `"T. Fischer"`. This is a display change in H2H matrix column headers and standings labels. Special cases for 'Undrafted' and 'Overall Record' are preserved in the plan's implementation.

### 2. Placeholder Scan

No TBD / TODO / "fill in" placeholders found.

### 3. Type Consistency

- `DataBundle` defined in Task 7 uses same field names as the 7-tuple unpacking pattern throughout the codebase (`standings`, `teams`, `games`, `players`, `draft_order`, `draft_results`, `draft_order_rules`).
- `abbreviate_player_name` imported as `_first_name` in both route files — existing call sites (`_first_name(name)`) remain identical.
- `filter_season(df, year)` signature matches all usage sites — `filter_season(all_st, year)` etc.
- `prediction_games_df` fixture in conftest returns same structure as old `_make_games_df()` — same column names, same game_id format.

### 4. Dependency Order

Task 6 must complete before Task 8 (password tests import `PASSWORD_COMPLEXITY_RE`). All other tasks are independent and can be done in any order.
