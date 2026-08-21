# Nav Redesign — Design Spec

**Date:** 2026-06-06
**Status:** Approved
**Scope:** `templates/base.html`, `static/style.css`, `static/js/main.js`, `routes/api_routes.py`, `services/db_service.py`

---

## Overview

Replaces the current nav (11 links in a single horizontal bar, no hierarchy) with a structured two-tier system: a slim desktop rail showing the 4–5 most important pages plus a "More ▾" dropdown, and a mobile bottom tab bar. A new `draft_active` Firestore flag drives a context-sensitive swap — Live Draft replaces Draft Results in the nav during the draft period, toggled by an admin control.

---

## 1. Desktop Nav

### Structure

```
[🐟 fishbone.png  NFL Wins Pool]  [primary links]  [More ▾]  [TF avatar]
```

`position: sticky; top: 0` — replaces the current `.app-nav` bar.

### Primary links (regular season)

| Slot | Non-admin | Admin |
|---|---|---|
| 1 | Standings | Standings |
| 2 | Schedule | Schedule |
| 3 | Draft Results | Draft Results |
| 4 | Playoff Race | Playoff Race |
| 5 | — | Admin (gold `--accent-gold`) |

### Primary links (draft active)

Slot 3 swaps from **Draft Results** → **● Live Draft** (green pill, pulsing dot). All other slots unchanged.

### More dropdown

Contains all secondary pages not in the primary rail:

- Weekly Progress
- Head to Head
- Draft History
- All-Time History
- Profile
- Admin Portal *(admin only)*

Dropdown opens on click, closes on outside click or Escape. Positioned below the More button, `min-width: 180px`, glassmorphism card style matching existing dropdowns.

### Avatar

Replaces the current "TFish · Logout" username/logout text. Shows initials (first 2 chars of nickname, uppercase). Clicking opens a small dropdown card (glassmorphism, `min-width: 160px`, positioned below-right): nickname in `--ink` / role badge in `--ink-3` / divider / Logout button. Closes on outside click or Escape.

### Logo

`fishbone.png` unchanged — same `<img src="/static/fishbone.png">` as today.

---

## 2. Mobile Nav

### Header

Logo + brand name only. No hamburger button — the bottom tab bar handles all navigation.

```
[🐟  NFL Wins Pool]
```

### Bottom Tab Bar

`position: fixed; bottom: 0; left: 0; right: 0` — replaces the current `.nav-drawer` hamburger trigger.

| Tab | Regular season | Draft active |
|---|---|---|
| 1 | 🏆 Standings | 🏆 Standings |
| 2 | 📅 Schedule | 📅 Schedule |
| 3 | 📋 Drafts | 🏈 Live Draft (green) |
| 4 | 🏁 Playoff | 🏁 Playoff |
| 5 | ☰ More | ☰ More |

Active tab highlighted in `--accent-gold`. Live Draft tab highlighted in `--pos` (green) during draft period.

### More Drawer

Tapping the More tab opens the existing `.nav-drawer` slide-in panel (currently triggered by the hamburger). Drawer content and styling unchanged — all pages remain accessible here.

---

## 3. Draft Active Toggle

### Data model

New Firestore document: collection `config`, document `settings`. No existing config collection — this is net-new.

```json
{ "draft_active": false }
```

`draft_active: true` makes Live Draft appear in the primary nav for all users.

### Admin portal control

In the Admin Portal page, a toggle card:

```
Draft active          [toggle switch]
Shows Live Draft in nav for all users
```

When on: border highlights green, subtext reads "Live Draft visible in nav · all users".

### API

- `GET /api/config/settings` — returns `{ draft_active: bool }` (public, no auth required — all users need to read this on page load)
- `POST /api/admin/config/settings` — admin-only, body `{ draft_active: bool }`

### Frontend

On auth success, `main.js` fetches `/api/config/settings` once and stores `draft_active` in app state. All nav render calls read this value to decide which link set to display. No polling — the nav reflects the state at page load. (Users already on the page before the admin toggles will see the update on next refresh; this is acceptable.)

---

## 4. Files Changed

| File | Change |
|---|---|
| `templates/base.html` | Replace nav HTML: desktop rail + More dropdown + avatar popover; mobile header (brand only); bottom tab bar |
| `static/style.css` | New classes: `.nav-rail`, `.nav-link`, `.nav-link--live`, `.nav-link--admin`, `.nav-more-dropdown`, `.nav-avatar`, `.bottom-tab-bar`, `.btb-item`; remove old `.nav-drawer` trigger styles |
| `static/js/main.js` | Fetch `/api/config/settings` on auth; `renderNav(state, draftActive)` replaces old nav render logic; More dropdown open/close; avatar popover; bottom tab active state |
| `routes/api_routes.py` | Add `GET /api/config/settings`; add `POST /api/admin/config/settings` |
| `services/db_service.py` | Add `get_config_settings()` and `set_config_settings(data)` — read/write `config/settings` doc in Firestore; local pkl fallback for `USE_LOCAL_DATA=True` |
| `scripts/refresh_local_pkls.py` | Add `config/settings` to the local pkl sync |

---

## 5. What Does NOT Change

- `fishbone.png` logo and its `<img>` tag
- All existing page routes and URLs
- Nav drawer content (reused as the More drawer on mobile)
- Auth flow, user identity display logic
- Admin portal page content (toggle card is additive)
- Any page-level templates (`standings.html`, `index.html`, etc.)
