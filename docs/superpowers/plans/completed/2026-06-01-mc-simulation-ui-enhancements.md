# MC Simulation UI Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface MC simulation framing and per-week uncertainty in the schedule card and explanation modal for future game predictions.

**Architecture:** Two-file UI change only — `templates/schedule.html` gets updated confidence label text and a Jinja2 uncertainty indicator based on `row['week'] - current_week`; `static/js/schedule_explain.js` gets an `isMcSimulation` flag (derived from `data-current-week` passed by the template button) to swap in the simulation source note and update the pick card label.

**Tech Stack:** Jinja2 templates, vanilla JS ES6 modules. No backend or schema changes.

---

## File Map

| File | Change |
|---|---|
| `templates/schedule.html` | Change confidence label; add uncertainty opacity/label; add `data-current-week` on explain button |
| `static/js/schedule_explain.js` | Detect MC simulation via `data.currentWeek`; update pick card label; replace source note |

---

## Key Data Facts

- `current_week` is available in the Jinja2 template context (passed by `standings_routes.py`).
- `row['week']` is the game's week number; `row['result']` is `None`/NaN for future games and a non-zero number for completed games.
- `pred_su_conf` is already a percentage (e.g. `73` for "73%").
- The explain button currently has `data-season`, `data-week`, `data-home`, `data-away`. We'll add `data-current-week`.
- `renderExplanation(data)` is called from `handleExplainClick(btn)`; pass `currentWeek` into `data` before calling.
- `explanation.source` is `null` for backfilled MC simulation predictions and `'profile'` for preseason profile predictions.

---

## Task 1: Schedule card — simulation label + uncertainty indicator

**Files:**
- Modify: `templates/schedule.html` (lines 110–134)

- [ ] **Step 1: Replace confidence label and add uncertainty indicator**

Find this block (lines 110–134 in `schedule.html`):

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

Replace with:

```html
            {% if row['pred_winner'] %}
            {% set is_future = row['result'] is not number or row['result'] == -1000 %}
            {% set weeks_out = [row['week']|int - current_week|int, 0]|max %}
            <div class="game-prediction"
                style="margin-top: 8px; padding-top: 4px; border-top: 1px dashed rgba(255,255,255,0.1); color: var(--accent-gold); font-weight: 600; display: flex; justify-content: space-between; align-items: flex-start;">
                <div>
                    <span><i data-lucide="sparkles"></i> Predictor:</span>
                    <div style="font-size: 0.75rem; color: #fff;">
                        {% if is_future %}
                        <span style="opacity:{{ '0.6' if weeks_out >= 5 else ('0.8' if weeks_out >= 2 else '1.0') }};">
                            {{ row['pred_winner'] }} wins {{ row['pred_su_conf'] }}% of simulations
                        </span>
                        {% if weeks_out >= 5 %}
                        <span style="font-size:0.65rem; color:var(--text-secondary); margin-left:3px; font-weight:normal;">(long-range)</span>
                        {% endif %}
                        {% else %}
                        Win: {{ row['pred_winner'] }} ({{ row['pred_su_conf'] }}%)
                        {% endif %}
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
                    data-current-week="{{ current_week }}"
                    title="Why this prediction?"
                    style="display:none; background:none; border:1px solid rgba(251,191,36,0.3); border-radius:50%; width:22px; height:22px; cursor:pointer; color:var(--accent-gold); font-size:12px; padding:0; line-height:22px; text-align:center; flex-shrink:0; margin-top:2px;">?</button>
            </div>
            {% endif %}
```

- [ ] **Step 2: Commit**

```bash
git add templates/schedule.html
git commit -m "feat: show simulation framing and uncertainty indicator on future game cards"
```

---

## Task 2: Explanation modal — simulation source note + pick card label

**Files:**
- Modify: `static/js/schedule_explain.js`

- [ ] **Step 1: Pass `currentWeek` into `renderExplanation` via augmented data**

In `handleExplainClick`, find the call site (around line 368–369):

```js
        const auditHtml = renderFeatureAuditSection(featureData, home, away);
        content.innerHTML = auditHtml + renderExplanation(data);
```

Replace with:

```js
        const auditHtml = renderFeatureAuditSection(featureData, home, away);
        const currentWeek = parseInt(btn.dataset.currentWeek ?? '0', 10);
        content.innerHTML = auditHtml + renderExplanation({ ...data, currentWeek });
```

- [ ] **Step 2: Add `isMcSimulation` detection and update pick card label**

In `renderExplanation`, find the destructure + `isProfileOnly` lines (lines 61–67):

```js
    const { home_team, away_team, pred_winner, pred_su_conf, pred_prob,
            pred_ats_pick, model_spread, edge_vs_vegas, explanation: ex } = data;

    const isProfileOnly = ex?.source === 'profile';
    const homeFavored   = pred_winner === home_team;
    const predColor     = homeFavored ? 'var(--accent-green)' : 'var(--accent-gold)';
    const confBar       = _bar(pred_su_conf, predColor);
```

Replace with:

```js
    const { home_team, away_team, pred_winner, pred_su_conf, pred_prob,
            pred_ats_pick, model_spread, edge_vs_vegas, explanation: ex,
            currentWeek = 0 } = data;

    const isProfileOnly   = ex?.source === 'profile';
    const isMcSimulation  = !isProfileOnly && data.week > currentWeek;
    const homeFavored     = pred_winner === home_team;
    const predColor       = homeFavored ? 'var(--accent-green)' : 'var(--accent-gold)';
    const confBar         = _bar(pred_su_conf, predColor);
```

- [ ] **Step 3: Update the pick card confidence label**

In `renderExplanation`, find the pick card HTML (around line 79):

```js
            <div style="font-size:0.75rem;">${pred_su_conf}% confidence</div>
```

Replace with:

```js
            <div style="font-size:0.75rem;">${isMcSimulation ? `wins ${pred_su_conf}% of simulations` : `${pred_su_conf}% confidence`}</div>
```

- [ ] **Step 4: Replace `sourceNote` to cover both profile and MC simulation cases**

Find the `sourceNote` block (lines 217–221):

```js
    const sourceNote = isProfileOnly
        ? `<div style="margin-top:0.75rem; padding:6px 10px; border-radius:6px; background:rgba(255,255,255,0.04); font-size:0.72rem; color:var(--text-secondary);">
            ℹ Pre-season projection — factors reflect prior-season averages. Values update as ${data.season} game data becomes available.
           </div>`
        : '';
```

Replace with:

```js
    const sourceNote = isProfileOnly
        ? `<div style="margin-top:0.75rem; padding:6px 10px; border-radius:6px; background:rgba(255,255,255,0.04); font-size:0.72rem; color:var(--text-secondary);">
            ℹ Pre-season projection — factors reflect prior-season averages. Values update as ${data.season} game data becomes available.
           </div>`
        : isMcSimulation
        ? `<div style="margin-top:0.75rem; padding:6px 10px; border-radius:6px; background:rgba(255,255,255,0.04); font-size:0.72rem; color:var(--text-secondary);">
            ℹ ${ex?.source || 'MC simulation (10,000 trials)'} — projected using week-by-week simulation. Later-season games carry higher uncertainty as each simulated week compounds variance from prior simulated outcomes.
           </div>`
        : '';
```

- [ ] **Step 5: Commit**

```bash
git add static/js/schedule_explain.js
git commit -m "feat: show MC simulation source note and simulation-framed label in explain modal"
```

---

## Task 3: Manual verification

No automated tests — template/JS changes require visual inspection.

- [ ] **Step 1: Start dev server**

```bash
uvicorn main:app --reload
```

- [ ] **Step 2: Check schedule card (future game)**

Load `http://localhost:8000/wins-pool/2026`. Confirm:
- Week 1 game shows `"DET wins 85% of simulations"` (or similar), full opacity.
- Week 6+ game shows reduced opacity on the percentage.
- Week 10+ game shows `(long-range)` label.
- A past-season schedule (e.g. `/wins-pool/2025`) still shows `"Win: KC (72%)"` (old format — these are completed games so `is_future` is false).

- [ ] **Step 3: Check explain modal (admin)**

Click `?` on a 2026 future game. Confirm:
- Pick card shows `"wins 85% of simulations"` instead of `"85% confidence"`.
- Bottom source note reads `"MC simulation (10,000 trials) — projected using week-by-week simulation. Later-season games carry higher uncertainty…"`.

- [ ] **Step 4: Check explain modal on a completed 2025 game**

Click `?` on any 2025 game. Confirm:
- Pick card still shows `"72% confidence"` (not "simulations").
- No MC simulation note at the bottom.

- [ ] **Step 5: Commit verification note, push**

```bash
git push origin main
```
