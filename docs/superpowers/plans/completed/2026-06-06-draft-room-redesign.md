# Draft Room Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current draft room layout with a shame-timer-centric design: count-up escalating timer, windowed pick queue, two-column split, collapsible full board, and a floating admin bar.

**Architecture:** Backend adds `time_taken_seconds` to draft board state and `connected_count` to the WS payload. All visual changes are HTML/CSS/JS only. The shame timer is a client-side `setInterval` driven by the existing `pick_start_time` field. The confirm-pick flow, all WS actions, and `renderDraftBoard()` are untouched.

**Tech Stack:** Python/FastAPI, Jinja2, Vanilla JS (ES6 modules), CSS custom properties, Firestore (no changes needed).

---

## File Map

| File | Change |
|---|---|
| `services/draft_service.py` | Add `time_taken_seconds` to each `draft_board` entry; add `connected_count` to state |
| `tests/test_draft_service.py` | Tests for the two new state fields |
| `templates/index.html` | Full restructure: shame timer card, room-split, board toggle, floating admin bar |
| `static/style.css` | New classes: `.clock-card`, `.room-split`, `.q-row`, `.q-foot`, `.board-toggle`, `.admin-bar`, `.numeral`, `@keyframes shakepulse` |
| `static/js/ui_renderer.js` | Add `renderPickQueue()`; update `renderTeamGrid()` to accept `draftBoard` and show drafted teams |
| `static/js/main.js` | Replace `processDraftBanners`/`startTimer` with `updateShameTimer()`; update `renderDraftState()`; wire board toggle and admin bar |

---

## Task 1: Backend — `time_taken_seconds` + `connected_count`

**Files:**
- Modify: `services/draft_service.py`
- Modify: `tests/test_draft_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_draft_service.py`:

```python
def test_draft_board_entries_include_time_taken():
    """Each draft board entry must have a time_taken_seconds key (float or None)."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    for entry in state['draft_board']:
        assert 'time_taken_seconds' in entry, f"missing time_taken_seconds on pick {entry['pick']}"
        assert entry['time_taken_seconds'] is None or isinstance(entry['time_taken_seconds'], float)


def test_state_includes_connected_count():
    """State must include connected_count equal to the size of the connected_players set."""
    from services.draft_service import load_draft_state
    state = load_draft_state({1, 2, 3})
    assert state['connected_count'] == 3

    state2 = load_draft_state(set())
    assert state2['connected_count'] == 0
```

- [ ] **Step 2: Run to confirm failure**

```
pytest tests/test_draft_service.py::test_draft_board_entries_include_time_taken tests/test_draft_service.py::test_state_includes_connected_count -v
```

Expected: both FAIL (KeyError).

- [ ] **Step 3: Implement in `draft_service.py`**

In `load_draft_state`, directly before the `draft_board = []` line (~line 100), build a time-taken lookup from `results_season`:

```python
    # Build time_taken map: pick → seconds
    time_taken_map: dict[int, float] = {}
    if 'time_taken_seconds' in results_season.columns:
        for _, r in results_season[['draftPick', 'time_taken_seconds']].dropna().iterrows():
            time_taken_map[int(r['draftPick'])] = float(r['time_taken_seconds'])
```

Then change the `draft_board.append(...)` call (currently ~line 111) to include `time_taken_seconds`:

```python
        draft_board.append({
            "pick": int(row['draftPick']),
            "playerId": pid,
            "playerName": pname,
            "team": team,
            "time_taken_seconds": time_taken_map.get(int(row['draftPick'])),
        })
```

Then, directly before the `sanitize_state(state)` call (~line 172), add:

```python
    state["connected_count"] = len(connected_players) if connected_players else 0
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_draft_service.py -v
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add services/draft_service.py tests/test_draft_service.py
git commit -m "feat: add time_taken_seconds to draft_board entries and connected_count to state"
```

---

## Task 2: HTML restructure

**Files:**
- Modify: `templates/index.html`

Replace the entire file content. Keep the VAPID meta tag, chat overlay, and `{% block scripts %}` block unchanged. The key structural changes: header gains `#round-label` span and `#live-pill` replaces the old status banner; `<main>` gains shame timer card + room-split + board toggle + collapsible board section; floating admin bar moves outside `<main>`.

- [ ] **Step 1: Replace `templates/index.html`**

```html
{% extends "base.html" %}

{% block title %}NFL Wins Pool Draft{% endblock %}

{% block content %}
{% if vapid_public_key %}
<meta name="vapid-public-key" content="{{ vapid_public_key }}">
{% endif %}
<div class="container">
    <!-- Page Header -->
    <header class="wp-top">
        <div>
            <div class="eyebrow">Live Draft · <span id="season-display"></span></div>
            <h1 class="wp-h1">Draft Room<span class="room-year" id="round-label"></span></h1>
        </div>
        <div class="wp-top-right">
            <div id="admin-year-selector" class="hidden">
                <select id="season-dropdown" class="season-select"></select>
            </div>
            <span id="live-pill" class="mono-pill">
                <span id="current-pick-status">Connecting…</span>
            </span>
        </div>
    </header>

    <!-- Draft Board -->
    <main class="dashboard" id="dashboard-main" style="display: none;">

        <!-- Shame Timer Card -->
        <div class="clock-card" id="shame-timer-card"></div>

        <!-- Two-column split -->
        <div class="room-split">

            <!-- Left: Pick Queue -->
            <section id="pick-queue-section">
                <div class="eyebrow" style="margin-bottom:14px; color:var(--ink-3)">Pick queue</div>
                <div id="pick-queue"></div>
                <div id="pick-queue-footer" class="q-foot mono"></div>
            </section>

            <!-- Right: Teams Grid -->
            <section class="available-teams-section">
                <div id="selection-preview" class="selection-preview hidden">
                    <p>Selected Team: <span id="selected-team-name">None</span></p>
                    <button id="confirm-pick-btn" class="btn-primary" disabled>Confirm Pick</button>
                </div>
                <div class="teams-grid" id="teams-grid"></div>
            </section>

        </div>

        <!-- Full board toggle -->
        <div class="board-toggle-row">
            <button id="board-toggle-btn" class="board-toggle">Show full board ▾</button>
        </div>

        <!-- Full Draft Board (collapsed by default) -->
        <section id="draft-board-section" style="display:none;">
            <ul class="draft-list" id="draft-list"></ul>

            <!-- Admin Only: Running Portfolio -->
            <section id="admin-portfolio-section" style="display:none; margin-top:1rem;">
                <h2>Running Portfolio (Admin Only)</h2>
                <div class="portfolio-container" id="admin-portfolio-content" style="overflow-x:auto;"></div>
            </section>
        </section>

    </main>
</div>

<!-- Floating Admin Bar (admin only) -->
<div id="admin-bar" class="admin-bar" style="display:none;">
    <button id="undo-pick-btn" class="btn-primary"
        style="background:var(--accent-gold);border-color:var(--accent-gold);padding:0.4rem 1rem;font-size:0.85rem;">
        Undo Pick
    </button>
    <button id="reset-pick-btn" class="btn-primary"
        style="background:var(--accent-red);border-color:var(--accent-red);padding:0.4rem 1rem;font-size:0.85rem;">
        Reset Pick
    </button>
</div>

<!-- Floating Chat Overlay -->
<div id="chat-overlay" style="
    position:fixed; bottom:1.5rem; right:1.5rem; z-index:9999;
    width:320px;
    background:var(--bg-elev); border:1px solid var(--line-strong);
    border-radius:12px; box-shadow:0 8px 32px rgba(0,0,0,0.5);
    display:flex; flex-direction:column; overflow:hidden;">

    <div id="chat-header" style="
        display:flex; align-items:center; justify-content:space-between;
        padding:0.6rem 0.9rem; cursor:pointer;
        border-bottom:1px solid var(--line-strong);
        background:var(--bg-elev-2); user-select:none;">
        <span style="font-size:0.85rem; font-weight:600; color:var(--ink); display:flex; align-items:center; gap:6px;">
            💬 Draft Chat
            <span id="chat-unread-badge" style="display:none; background:var(--accent-red); color:#fff;
                border-radius:99px; font-size:0.65rem; padding:1px 6px; line-height:1.4;">0</span>
        </span>
        <button id="chat-collapse-btn" style="
            background:none; border:none; color:var(--ink-2);
            cursor:pointer; font-size:1rem; line-height:1; padding:0 2px;"
            aria-label="Toggle chat">−</button>
    </div>

    <div id="chat-body" style="display:flex; flex-direction:column; padding:0.75rem;">
        <div id="chat-messages" style="
            height:260px; overflow-y:auto; display:flex; flex-direction:column;
            gap:4px; margin-bottom:0.65rem; padding-right:2px;"></div>
        <div style="display:flex; gap:0.4rem; align-items:center;">
            <input id="chat-input" type="text" maxlength="500" placeholder="Message…"
                style="flex:1; background:rgba(255,255,255,0.07); border:1px solid var(--glass-border);
                border-radius:6px; padding:0.35rem 0.65rem; color:var(--ink); font-size:0.82rem;"/>
            <button id="chat-send-btn" class="btn-primary"
                style="padding:0.35rem 0.75rem; font-size:0.8rem; white-space:nowrap;">Send</button>
            <button id="chat-teams-btn" class="btn-primary"
                title="List available teams"
                style="padding:0.35rem 0.6rem; font-size:0.8rem;
                background:rgba(255,255,255,0.07); border-color:var(--glass-border);">🏈</button>
        </div>
    </div>
</div>
{% endblock %}

{% block scripts %}
{% endblock %}
```

- [ ] **Step 2: Verify the server starts without errors**

```
uvicorn main:app --reload
```

Navigate to `http://localhost:8000/draft` and log in. Confirm the page loads — it will look unstyled/broken until CSS and JS are updated in later tasks. No JS errors for missing elements yet (the JS hasn't been updated to reference the new IDs).

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: restructure draft room HTML for shame timer + pick queue layout"
```

---

## Task 3: CSS — new styles

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Update `.dashboard` and add new classes**

Find the existing `.dashboard` rule (around line 585):

```css
.dashboard {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2rem;
    animation: fadeIn 1s ease-out;
}
```

Replace it with:

```css
.dashboard {
    display: flex;
    flex-direction: column;
    gap: 2rem;
    animation: fadeIn 1s ease-out;
}
```

Then append the following new rules at the end of `style.css`:

```css
/* ── Draft Room Redesign ─────────────────────────────────── */

/* Newsreader numeral (italic serif for pick numbers) */
.numeral {
    font-family: 'Newsreader', Georgia, serif;
    font-style: italic;
}

/* Shame timer card */
.clock-card {
    border: 1px solid var(--line-strong);
    border-radius: 14px;
    background: var(--bg-elev);
    padding: 26px 34px;
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: 34px;
    transition: border-color 600ms ease;
    min-height: 120px;
}
.cc-left { min-width: 0; }
.cc-name { font-size: 30px; font-weight: 700; letter-spacing: -0.01em; margin: 10px 0 5px; }
.cc-sub { color: var(--ink-2); font-size: 13px; }
.cc-pick {
    display: flex; align-items: baseline; gap: 4px;
    padding: 8px 16px; border: 1px solid var(--line);
    border-radius: 999px; justify-self: center;
}
.cc-timer { text-align: right; justify-self: end; }
.shame-num {
    font-size: 58px; font-weight: 700; letter-spacing: -0.03em; line-height: 1;
    font-variant-numeric: tabular-nums; margin: 8px 0 6px;
    transition: color 500ms ease;
}
@keyframes shakepulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.62; }
}
.pulse-shame { animation: shakepulse 1.2s ease-in-out infinite; }
.dot.warn { background: var(--warn); box-shadow: 0 0 0 3px rgba(217,168,108,0.18); }
.dot.bad  { background: var(--neg);  box-shadow: 0 0 0 3px rgba(217,112,112,0.18); }

/* Two-column split */
.room-split {
    display: grid;
    grid-template-columns: 1fr 1.3fr;
    gap: 36px;
}

/* Pick queue rows */
.q-row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: 8px;
    margin-bottom: 6px;
    transition: background 120ms;
}
.q-foot {
    margin-top: 12px;
    font-size: 11px;
    color: var(--ink-3);
    letter-spacing: 0.03em;
    padding: 0 4px;
}
.q-foot b { color: var(--ink-2); font-weight: 700; }

/* Full-board toggle */
.board-toggle-row { display: flex; justify-content: center; }
.board-toggle {
    background: none; border: none; color: var(--ink-3);
    font-size: 0.8rem; cursor: pointer; padding: 0.25rem 0.75rem;
    border-radius: 6px; transition: color 120ms;
}
.board-toggle:hover { color: var(--ink-2); }

/* Floating admin bar */
.admin-bar {
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 100;
    background: var(--bg-elev-2);
    border-top: 1px solid var(--line-strong);
    padding: 12px 24px;
    display: flex;
    gap: 0.5rem;
    align-items: center;
    justify-content: flex-end;
}

/* New team button style (design spec) */
.team-btn {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 14px; border: 1px solid var(--line);
    border-radius: 8px; background: transparent;
    cursor: pointer; transition: border-color 120ms, background 120ms;
    font-family: inherit; width: 100%; text-align: left;
    color: var(--ink);
}
.team-btn:not(:disabled):hover {
    border-color: var(--line-strong);
    background: rgba(255,255,255,0.025);
}
.team-btn:disabled { cursor: default; opacity: 0.4; }
.team-btn.selected {
    border-color: var(--accent-gold);
    background: rgba(201,162,74,0.08);
}
.team-btn-city { font-size: 13px; font-weight: 600; }
.team-btn-sub  { font-size: 11px; color: var(--ink-3); font-family: 'JetBrains Mono', monospace; margin-top: 2px; }

/* Mobile */
@media (max-width: 860px) {
    .clock-card {
        grid-template-columns: 1fr;
        gap: 20px;
        text-align: left;
    }
    .cc-pick, .cc-timer { justify-self: start; }
    .cc-timer { text-align: left; }
    .room-split { grid-template-columns: 1fr; }
    .dashboard { padding-bottom: 80px; }
}
```

- [ ] **Step 2: Commit**

```bash
git add static/style.css
git commit -m "feat: add shame timer, pick queue, room-split, and admin bar CSS"
```

---

## Task 4: Shame timer JS

**Files:**
- Modify: `static/js/main.js`

Replace `processDraftBanners` and `startTimer` with a new `updateShameTimer` method, and update `renderDraftState` to call it.

- [ ] **Step 1: Add `updateShameTimer` method and remove old methods**

In `main.js`, find and delete the `processDraftBanners` method (lines ~247–267) and the `startTimer` method (lines ~269–282).

Replace them with:

```javascript
    updateShameTimer(state) {
        const card = document.getElementById('shame-timer-card');
        if (!card) return;

        if (this.shameTimerInterval) {
            clearInterval(this.shameTimerInterval);
            this.shameTimerInterval = null;
        }

        const { active_pick, draft_board, pick_start_time, all_players, connected_count, season } = state;

        // Update LIVE pill
        const livePill = document.getElementById('current-pick-status');
        if (livePill && connected_count != null) {
            livePill.innerHTML = `<span class="dot pulse"></span>LIVE &middot; ${connected_count} connected`;
        }

        // Update round label in h1
        const roundLabel = document.getElementById('round-label');
        if (roundLabel && all_players) {
            const totalPlayers = all_players.length || 10;
            const round = active_pick <= draft_board.length
                ? Math.ceil(active_pick / totalPlayers)
                : Math.ceil(draft_board.length / totalPlayers);
            roundLabel.textContent = `/round ${round}`;
        }

        // Draft complete
        if (active_pick > 30 || active_pick > (draft_board.length || 30)) {
            card.style.borderColor = 'var(--line-strong)';
            card.innerHTML = `
                <div style="grid-column:1/-1;text-align:center;padding:8px 0;color:var(--ink-2);font-size:15px;font-weight:600;">
                    Draft complete 🎉
                </div>`;
            return;
        }

        const item = draft_board.find(x => x.pick === active_pick);
        if (!item) return;

        const totalPicks = draft_board.length;
        const totalPlayers = (all_players || []).length || 10;
        const round = Math.ceil(active_pick / totalPlayers);

        const TIERS = [
            { at: 0,   color: 'var(--ink)',   ring: 'var(--line-strong)',        dot: 'ok',   label: 'On the clock' },
            { at: 30,  color: 'var(--ink-2)', ring: 'var(--line-strong)',        dot: 'ok',   label: 'Taking it in…' },
            { at: 75,  color: 'var(--warn)',  ring: 'rgba(217,168,108,0.40)',    dot: 'warn', label: 'The pool is getting restless' },
            { at: 150, color: 'var(--neg)',   ring: 'rgba(217,112,112,0.45)',    dot: 'bad',  label: 'You’re holding up the entire draft' },
        ];
        const tierFor = (secs) => TIERS.reduce((t, x) => secs >= x.at ? x : t, TIERS[0]);
        const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

        const render = () => {
            const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - pick_start_time);
            const tier = tierFor(elapsed);
            const hot = elapsed >= 150;

            card.style.borderColor = tier.ring;
            card.innerHTML = `
                <div class="cc-left">
                    <div class="eyebrow" style="color:var(--ink-3)">On the clock</div>
                    <div class="cc-name">${item.playerName}</div>
                    <div class="cc-sub">Round ${round} &middot; Pick ${active_pick} of ${totalPicks}</div>
                </div>
                <div class="cc-pick">
                    <span class="numeral" style="font-size:26px;color:var(--ink-2)">${active_pick}</span>
                    <span style="color:var(--ink-3);font-size:13px">/${totalPicks}</span>
                </div>
                <div class="cc-timer">
                    <div class="eyebrow" style="color:${hot ? 'var(--neg)' : 'var(--ink-3)'}">Time on the clock</div>
                    <div class="shame-num mono${hot ? ' pulse-shame' : ''}" style="color:${tier.color}">${mmss(elapsed)}</div>
                    <div style="display:flex;justify-content:flex-end;margin-top:6px">
                        <span class="mono-pill">
                            <span class="dot ${tier.dot}${hot ? ' pulse' : ''}"></span>${tier.label}
                        </span>
                    </div>
                </div>`;
        };

        render();
        this.shameTimerInterval = setInterval(render, 1000);
    }
```

- [ ] **Step 2: Update `renderDraftState` to call `updateShameTimer` instead of `processDraftBanners`**

In `renderDraftState`, find:
```javascript
        // Update Banner
        this.processDraftBanners(state);
```

Replace with:
```javascript
        // Shame timer + LIVE pill + round label
        this.updateShameTimer(state);
```

Also find and remove these lines (they reference the old admin button show/hide logic in renderDraftState):
```javascript
        // Setup Admin Overrides (Cleanup old select if it exists)
        if (this.user.role === 'admin') {
            const undoBtn = document.getElementById('undo-pick-btn');
            const resetBtn = document.getElementById('reset-pick-btn');
            if (undoBtn) undoBtn.style.display = 'flex';
            if (resetBtn) resetBtn.style.display = 'flex';
        }
```

- [ ] **Step 3: Add `shameTimerInterval` initialisation in the constructor/init**

Find where `this.timerInterval` is declared (search for `this.timerInterval`). Add alongside it:

```javascript
        this.shameTimerInterval = null;
```

- [ ] **Step 4: Smoke test shame timer**

```
uvicorn main:app --reload
```

Log in, open `/draft`. Confirm the shame timer card renders with the active player's name, the count-up timer ticking, and the LIVE pill showing a connected count. The rest of the page will still be incomplete — that's expected.

- [ ] **Step 5: Commit**

```bash
git add static/js/main.js
git commit -m "feat: replace countdown banner with shame timer count-up card"
```

---

## Task 5: Pick queue renderer

**Files:**
- Modify: `static/js/ui_renderer.js`

- [ ] **Step 1: Add `renderPickQueue` method**

Inside the `UiRenderer` object (after the `renderTeamGrid` method), add:

```javascript
    renderPickQueue(board, activePick, allPlayers) {
        const container = document.getElementById('pick-queue');
        const footer    = document.getElementById('pick-queue-footer');
        if (!container) return;

        const totalPlayers = (allPlayers || []).length || 10;
        const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, '0')}`;
        const SLOW_SECS = 120;

        // Windowed range: 3 past + active + 2 next
        const start  = Math.max(1, activePick - 3);
        const end    = Math.min(board.length, activePick + 2);
        const window = board.filter(x => x.pick >= start && x.pick <= end);

        container.innerHTML = window.map(item => {
            const isPast   = item.pick < activePick;
            const isActive = item.pick === activePick;
            const round    = Math.ceil(item.pick / totalPlayers);
            const took     = item.time_taken_seconds;
            const isSlow   = took != null && took >= SLOW_SECS;

            let rightHtml;
            if (isPast && item.team) {
                const tookFmt = took != null ? mmss(took) : '';
                rightHtml = `
                    <div style="display:flex;align-items:center;gap:10px">
                        ${tookFmt ? `<span class="mono" style="font-size:11px;color:${isSlow ? 'var(--warn)' : 'var(--ink-3)'}">${tookFmt}</span>` : ''}
                        <span style="background:#444;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace">${item.team}</span>
                    </div>`;
            } else if (isActive) {
                rightHtml = `<span class="mono-pill"><span class="dot pulse"></span>picking</span>`;
            } else {
                rightHtml = `<span class="mono" style="color:var(--ink-3);font-size:11px">—</span>`;
            }

            return `
                <div class="q-row" style="
                    opacity:${isPast ? 0.6 : 1};
                    background:${isActive ? 'rgba(255,255,255,0.025)' : 'transparent'};
                    border-color:${isActive ? 'var(--line-strong)' : 'var(--line)'}">
                    <span class="numeral" style="font-size:22px;color:${isActive ? 'var(--ink)' : 'var(--ink-3)'};min-width:28px">${item.pick}</span>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:14px;font-weight:600">${item.playerName}</div>
                        <div class="mono" style="font-size:11px;color:var(--ink-3)">R${round}&middot;P${item.pick}</div>
                    </div>
                    ${rightHtml}
                </div>`;
        }).join('');

        if (!footer) return;

        // Stats footer: avg + slowest for the current round
        const currentRound    = Math.ceil(activePick / totalPlayers);
        const roundPastPicks  = board.filter(x =>
            x.pick < activePick &&
            Math.ceil(x.pick / totalPlayers) === currentRound &&
            x.time_taken_seconds != null
        );

        if (roundPastPicks.length === 0) { footer.innerHTML = ''; return; }

        const avg     = roundPastPicks.reduce((s, x) => s + x.time_taken_seconds, 0) / roundPastPicks.length;
        const slowest = roundPastPicks.reduce((a, b) => b.time_taken_seconds > a.time_taken_seconds ? b : a);
        const slowColor = slowest.time_taken_seconds >= SLOW_SECS ? 'var(--warn)' : 'var(--ink-2)';

        footer.innerHTML = `Avg pick this round &middot; <b>${mmss(avg)}</b> &nbsp;&middot;&nbsp; Slowest &middot; <b style="color:${slowColor}">${slowest.playerName} ${mmss(slowest.time_taken_seconds)}</b>`;
    },
```

- [ ] **Step 2: Wire `renderPickQueue` into `renderDraftState` in `main.js`**

In `renderDraftState`, after `this.updateShameTimer(state)`, add:

```javascript
        // Render Pick Queue
        UiRenderer.renderPickQueue(draft_board, active_pick, state.all_players);
```

- [ ] **Step 3: Smoke test**

Reload the draft room. Confirm the pick queue appears in the left column with the windowed rows and stats footer.

- [ ] **Step 4: Commit**

```bash
git add static/js/ui_renderer.js static/js/main.js
git commit -m "feat: add pick queue renderer with windowed rows and round stats footer"
```

---

## Task 6: Teams grid restyle + board toggle + admin bar + LIVE pill wiring

**Files:**
- Modify: `static/js/ui_renderer.js`
- Modify: `static/js/main.js`

### Part A — Teams grid restyle (show drafted teams)

- [ ] **Step 1: Update `renderTeamGrid` signature and implementation**

Find `renderTeamGrid(teams, selectedTeam, role, predictions, schedules)` in `ui_renderer.js` and replace the entire method:

```javascript
    renderTeamGrid(availableTeams, selectedTeam, role, predictions, schedules, draftBoard) {
        const grid = document.getElementById('teams-grid');
        if (!grid) return;

        // Build drafted map: team → ownerName
        const draftedMap = {};
        if (draftBoard) {
            draftBoard.forEach(entry => {
                if (entry.team) draftedMap[entry.team] = entry.playerName;
            });
        }

        // Combine and sort: available (alpha or prediction) + drafted (alpha)
        let available = [...availableTeams];
        if (role === 'admin' && predictions) {
            available.sort((a, b) => {
                const pA = predictions[a] && typeof predictions[a] === 'object' ? predictions[a].projected_wins : (predictions[a] || 0);
                const pB = predictions[b] && typeof predictions[b] === 'object' ? predictions[b].projected_wins : (predictions[b] || 0);
                return pB - pA;
            });
        }

        const drafted = Object.keys(draftedMap).sort();
        const allTeams = [...available, ...drafted];

        grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:8px;';
        grid.innerHTML = allTeams.map(team => {
            const isDrafted  = team in draftedMap;
            const isSelected = team === selectedTeam && !isDrafted;
            const pred       = (role === 'admin' && predictions && !isDrafted) ? predictions[team] : null;
            const schedule   = schedules && schedules[team] ? schedules[team].join('\n') : '';

            let subHtml;
            if (isDrafted) {
                subHtml = `<div class="team-btn-sub">&rarr; ${draftedMap[team]}</div>`;
            } else if (pred) {
                if (typeof pred === 'object') {
                    subHtml = `<div class="team-btn-sub">${pred.projected_wins}W &plusmn;${pred.std_dev}</div>`;
                } else {
                    subHtml = `<div class="team-btn-sub">${pred}W</div>`;
                }
            } else {
                subHtml = '';
            }

            return `
                <button class="team-btn${isSelected ? ' selected' : ''}"
                    data-team="${team}"
                    title="${schedule}"
                    ${isDrafted ? 'disabled' : ''}>
                    <img src="${this.getTeamLogo(team)}" alt="${team}"
                        style="width:32px;height:32px;object-fit:contain;flex-shrink:0;">
                    <div style="flex:1;min-width:0">
                        <div class="team-btn-city" style="${isDrafted ? 'text-decoration:line-through;text-decoration-color:var(--ink-3)' : ''}">${team}</div>
                        ${subHtml}
                    </div>
                </button>`;
        }).join('');
    },
```

- [ ] **Step 2: Update the `renderTeamGrid` call in `main.js`**

Find the existing call:
```javascript
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules);
```

Replace with:
```javascript
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules, draft_board);
```

- [ ] **Step 3: Update `attachTeamCardClickHandlers` in `main.js` to use `.team-btn`**

Find:
```javascript
        const cards = document.querySelectorAll('.team-card');
```

Replace with:
```javascript
        const cards = document.querySelectorAll('.team-btn');
```

### Part B — Board toggle, admin bar, admin padding

- [ ] **Step 4: Wire board toggle in `main.js`**

Add a new method to DraftApp (after `updateShameTimer`):

```javascript
    initBoardToggle() {
        const btn     = document.getElementById('board-toggle-btn');
        const section = document.getElementById('draft-board-section');
        if (!btn || !section) return;
        if (btn._wired) return;
        btn._wired = true;

        btn.addEventListener('click', () => {
            const open = section.style.display !== 'none';
            section.style.display = open ? 'none' : 'block';
            btn.textContent = open ? 'Show full board ▾' : 'Hide full board ▴';
        });
    }
```

Call it once in `renderDraftState`, at the end:

```javascript
        this.initBoardToggle();
```

- [ ] **Step 5: Wire admin bar visibility in `main.js`**

In `renderDraftState`, find where admin panel visibility is set:

```javascript
        // Render Admin Portfolio
        if (this.user.role === 'admin') {
            const adminPanel = document.getElementById('admin-portfolio-section');
            if (adminPanel) adminPanel.style.display = 'block';
            UiRenderer.renderAdminPortfolio(draft_board, state.all_players, preseason_predictions);
        } else {
            const adminPanel = document.getElementById('admin-portfolio-section');
            if (adminPanel) adminPanel.style.display = 'none';
        }
```

Add directly after:

```javascript
        // Floating admin bar + body padding
        const adminBar = document.getElementById('admin-bar');
        const main = document.getElementById('dashboard-main');
        if (this.user.role === 'admin') {
            if (adminBar) adminBar.style.display = 'flex';
            if (main) main.style.paddingBottom = '64px';
        } else {
            if (adminBar) adminBar.style.display = 'none';
            if (main) main.style.paddingBottom = '';
        }
```

- [ ] **Step 6: Run full test suite**

```
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add static/js/ui_renderer.js static/js/main.js
git commit -m "feat: restyle teams grid, wire board toggle and floating admin bar"
```

---

## Task 7: End-to-end smoke test

No code changes — manual validation only.

- [ ] **Step 1: Start server and open draft room**

```
uvicorn main:app --reload
```

Log in at `http://localhost:8000/draft`.

- [ ] **Step 2: Shame timer**

Confirm: player name visible, timer counting up from correct start time, LIVE pill shows connected count, round label in h1 updates.

Wait 30s and confirm the tier shifts to "Taking it in…" with the timer color change. (You can test higher tiers by temporarily editing `TIERS` thresholds.)

- [ ] **Step 3: Pick queue**

Confirm the windowed 6 rows appear — past picks with team badges + times, active row with pulsing pill, next rows with `—`. Stats footer visible if past picks exist.

- [ ] **Step 4: Teams grid**

Confirm available teams render as `.team-btn` cards. Drafted teams appear dimmed with strikethrough. Click a team, confirm the static confirm-pick space appears above the grid.

- [ ] **Step 5: Full board toggle**

Click "Show full board ▾" — confirm the draft board expands with all picks. Click "Hide full board ▴" — confirm it collapses.

- [ ] **Step 6: Admin bar (admin only)**

Log in as admin. Confirm the floating bar appears at the bottom with Undo Pick and Reset Pick. Confirm non-admin users don't see the bar.

- [ ] **Step 7: Commit and push**

```bash
git add -A
git commit -m "chore: draft room redesign complete"
git push origin main
```
