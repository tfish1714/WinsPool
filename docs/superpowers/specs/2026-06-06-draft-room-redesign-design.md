# Draft Room Redesign — Design Spec

**Date:** 2026-06-06
**Status:** Approved
**Scope:** `templates/index.html`, `static/js/ui_renderer.js`, `static/js/main.js`, `static/style.css`, `services/draft_service.py`, `routes/draft_routes.py`, `templates/base.html`

---

## Overview

Replaces the current draft room layout with a tighter, more focused design. The core behavior change is a **count-up shame timer** in place of the existing countdown banner, plus a windowed pick queue showing the active context (3 past + active + 2 next) instead of the full 30-pick board. The full board remains accessible via a collapsible toggle below the main content.

All existing functionality is preserved: static confirm-pick flow, admin undo/reset, admin portfolio, preseason win predictions, team selection behavior.

---

## Layout (top → bottom)

```
Header (eyebrow + h1 + LIVE pill)
─────────────────────────────────
Shame Timer Card  [full width]
─────────────────────────────────
[ Pick Queue  1fr ] [ Teams Grid  1.3fr ]
─────────────────────────────────
Show full board ▾  [toggle]
  └─ Full Draft Board (all 30 picks, collapsed by default)
     └─ Admin Portfolio (admin only, unchanged)
─────────────────────────────────
Floating Admin Bar (fixed bottom, admin only)
```

---

## 1. Header

- Eyebrow: `Live draft · {season}` (unchanged)
- H1: `Draft Room` + `/{round_label}` in JetBrains Mono `--ink-2` (e.g. `/round 3`)
- Right side: `● LIVE · {connected_count} connected` mono-pill with pulsing green dot
- Bottom `1px --line` hairline border (unchanged)

**Backend:** Add `connected_count: len(connected_players)` to the state dict in `load_draft_state()` / state broadcast in `draft_routes.py`. The connected_players set is already tracked in `websocket_endpoint`.

---

## 2. Shame Timer Card

Full-width card between the header and the two-column split.

**Layout:** CSS grid `1fr auto 1fr`, `align-items: center`, `gap: 34px`, `padding: 26px 34px`.

**Left cell:**
- Eyebrow: `On the clock` (`--ink-3`)
- Player name: 30px / 700 / `--ink`
- Subline: `Round N · Pick N of 30` — 13px / `--ink-2`

**Center cell:**
- Pick number chip: Newsreader italic numeral (26px `--ink-2`) + `/30` (13px `--ink-3`), `padding: 8px 16px`, `border: 1px solid --line`, `border-radius: 999px`

**Right cell (timer):**
- Eyebrow: `Time on the clock` (`--ink-3`; turns `--neg` at tier 4)
- Timer: `M:SS` display, 58px / 700 / tabular-nums, JetBrains Mono; color transitions with tier
- Status pill: `● {label}` mono-pill; dot and label shift with tier

**Escalation tiers** (pure function of elapsed seconds; color/border transitions over 500–600ms):

| Elapsed | Timer color | Status label | Dot / border |
|---|---|---|---|
| 0–29s | `--ink` | On the clock | green / `--line-strong` |
| 30–74s | `--ink-2` | Taking it in… | green / `--line-strong` |
| 75–149s | `--warn` | The pool is getting restless | amber / `rgba(217,168,108,0.40)` |
| 150s+ | `--neg` | You're holding up the entire draft | red pulsing / `rgba(217,112,112,0.45)` |

At tier 4 (150s+): timer and dot pulse opacity (`shakepulse` keyframe, 1.2s, `opacity: 1 → 0.62`); eyebrow turns `--neg`.

**Client implementation:** `pick_start_time` (Unix epoch seconds) is already in the WS state. Client computes `elapsed = Math.floor(Date.now()/1000 - pick_start_time)` on a `setInterval` every 1 second. `tierFor(elapsed)` is a pure lookup. No backend changes needed.

**Draft complete state:** When `active_pick > 30`, the card renders a neutral "Draft complete" state (no timer, no escalation).

**Mobile (`≤860px`):** Card collapses to single-column layout; timer and pick chip left-align.

---

## 3. Pick Queue (left column)

Windowed view showing the 6 picks closest to the active pick.

**Window:** `[active_pick - 3 … active_pick + 2]`, clamped to valid range. Produces at most 6 rows.

**Row anatomy:**
- Pick number: Newsreader italic, 22px; `--ink-3` for past/next, `--ink` for active
- Player name (14px / 600) + `R{round}·P{pick}` mono subline (11px `--ink-3`)
- Right side:
  - **Past:** team badge (small, colored) + time `M:SS` in mono 11px; `--warn` color if ≥ 120s
  - **Active:** `● picking` green mono-pill (pulsing dot)
  - **Next:** `—` in `--ink-3` 11px

Row styling: `padding: 12px 14px`, `border: 1px solid --line`, `border-radius: 8px`, `margin-bottom: 6px`. Active row: `background: rgba(255,255,255,0.025)`, border `--line-strong`, full opacity. Past rows: `opacity: 0.6`.

**Stats footer** (below rows, mono 11px `--ink-3`):
`Avg pick this round · {avg} · Slowest · {name} {time}`
Slowest name+time rendered in `--warn` if that time ≥ 120s. Footer is hidden if no past picks have timing data.

**Backend change:** `draft_service.py` — when building `draft_board` entries, join `time_taken_seconds` from `draft_results`. Each entry gains `"time_taken_seconds": float | None`.

**Round derivation:** `round = ceil(pick / players_count)` where `players_count = len(all_players)`.

---

## 4. Teams Grid (right column)

Content and selection behavior unchanged. Styling updated to match the design spec.

**Each team button:** `display: flex; align-items: center; gap: 12px; padding: 12px 14px; border: 1px solid --line; border-radius: 8px`
- Team badge (colored, existing component)
- City name: 13px / 600; struck through (`--ink-3` decoration color) if drafted
- Subline: record OR win prediction (admin); `→ Owner` if drafted

**Available teams:** hover → `border-color: --line-strong`, `background: rgba(255,255,255,0.025)`
**Drafted teams:** `opacity: 0.4`, `cursor: default`, `pointer-events: none`

**Static confirm-pick space stays exactly as-is** above the teams grid (`#selection-preview`, `.selection-preview`). No changes to pick selection logic.

**Sorting:** unchanged — alpha for regular users, win-prediction sort for admin.

---

## 5. Full Draft Board Toggle

Below the two-column split:
- A `Show full board ▾` / `Hide full board ▴` toggle button (`--ink-3`, small, no border chrome)
- The full draft board (`#draft-list`) is hidden by default, shown on click
- Existing `renderDraftBoard()` in `ui_renderer.js` is untouched
- Admin portfolio section remains below the full board (unchanged)

---

## 6. Floating Admin Bar

`position: fixed; bottom: 0; left: 0; right: 0; z-index: 100`
Only rendered when `role === 'admin'`.

- Background: `--bg-elev-2`; top border: `1px solid --line-strong`; padding: `12px 24px`
- Layout: `display: flex; gap: 0.5rem; align-items: center; justify-content: flex-end`
- Contains: **Undo Pick** (gold, existing `#undo-pick-btn`) + **Reset Pick** (red, existing `#reset-pick-btn`)

The existing in-board admin buttons are removed from their current position in the draft board section header. The button IDs (`#undo-pick-btn`, `#reset-pick-btn`) and all JS click handlers are unchanged — only the DOM location moves.

When the admin bar is visible, add `padding-bottom: 64px` to `#dashboard-main` so the bar never covers the bottom of the page content.

---

## 7. Typography Addition

Add **Newsreader** italic to the font stack in `templates/base.html`:
```
Newsreader:ital,opsz,wght@1,12,500
```
Used exclusively for pick numerals in the shame timer chip and pick queue rows.

---

## Files Changed

| File | Change |
|---|---|
| `services/draft_service.py` | Join `time_taken_seconds` into each `draft_board` entry; add `connected_count` to state |
| `routes/draft_routes.py` | Pass `connected_count` (len of connected_players set) into state broadcast |
| `templates/base.html` | Add Newsreader italic to Google Fonts link |
| `templates/index.html` | Full restructure: shame timer card, two-column split, full board toggle, floating admin bar |
| `static/js/ui_renderer.js` | Add `renderShameTimer()`, `renderPickQueue()`, update `renderTeamGrid()` styling |
| `static/js/main.js` | Wire shame timer interval; handle `connected_count` in state; full board toggle; floating admin bar visibility |
| `static/style.css` | Shame timer card styles, pick queue styles, floating admin bar styles, `shakepulse` keyframe |

---

## What Does NOT Change

- Pick selection logic (select → static confirm space → confirm button)
- `renderDraftBoard()` — reused as-is for the collapsible full board
- Admin portfolio section
- Chat overlay (separate work item)
- All WebSocket actions (pick, force_pick, undo_pick, reset_pick, chat, etc.)
- Team sorting logic
- Preseason win predictions display
