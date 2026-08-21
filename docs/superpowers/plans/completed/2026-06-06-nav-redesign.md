# Nav Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat 11-link nav bar with a slim desktop rail + More dropdown and a mobile bottom tab bar, with a `draft_active` flag that swaps Draft Results → Live Draft in the primary nav during the draft period.

**Architecture:** New `config/settings` Firestore doc stores `draft_active`. A public `GET /api/config/settings` endpoint serves it to the frontend on login. JS renders the primary nav links and More dropdown dynamically based on role + draft_active. Desktop and mobile navs are separate HTML elements shown/hidden by CSS breakpoint. The existing drawer is reused as the More drawer on mobile.

**Tech Stack:** Python/FastAPI, Jinja2, Vanilla JS (ES6 modules), CSS custom properties, Firestore, `USE_LOCAL_DATA` local pkl/json fallback.

---

## File Map

| File | Change |
|---|---|
| `services/db_service.py` | Add `get_config_settings()` and `set_config_settings(data)` |
| `scripts/refresh_local_pkls.py` | Add `dump_config_settings()` called from `main()` |
| `routes/api_routes.py` | Add `GET /api/config/settings` and `POST /api/admin/config/settings` |
| `tests/test_config_api.py` | New test file: config API tests |
| `templates/base.html` | Replace nav: desktop rail + More dropdown + avatar; mobile header (brand only) + bottom tab bar |
| `static/style.css` | Add `.nav-rail`, `.nav-more`, `.nav-avatar-wrap`, `.nav-mobile-header`, `.bottom-tab-bar` styles; remove/update old `.hamburger-btn` and `.app-nav` tab styles |
| `static/js/main.js` | Fetch config on init; `updateNav(role, draftActive)` renders primary links + More dropdown; More dropdown open/close; avatar popover; re-wire logout |
| `static/js/responsive.js` | Wire `#btb-more-tab` (bottom tab More button) to open the drawer; remove `#drawer-toggle` reference |
| `templates/admin.html` | Add draft-active toggle card at top of draft-section tab |
| `static/js/admin_main.js` | Fetch current `draft_active` state on load; wire toggle to `POST /api/admin/config/settings` |

---

## Task 1: Backend — `get_config_settings` and `set_config_settings`

**Files:**
- Modify: `services/db_service.py`
- Modify: `scripts/refresh_local_pkls.py`

- [ ] **Step 1: Add functions to `db_service.py`**

Add these two functions at the end of `services/db_service.py` (after `get_metadata`):

```python
def get_config_settings() -> dict:
    """Returns app config settings. Defaults to {"draft_active": False}."""
    import json
    default = {"draft_active": False}
    use_local = os.environ.get("USE_LOCAL_DATA", "False").lower() == "true"

    if use_local:
        local_path = pathlib.Path(".local_db") / "config_settings.json"
        if local_path.exists():
            try:
                with open(local_path) as f:
                    return {**default, **json.load(f)}
            except Exception:
                pass
        return default

    db = get_db()
    if not db:
        return default
    doc = db.collection("config").document("settings").get()
    return {**default, **(doc.to_dict() if doc.exists else {})}


def set_config_settings(data: dict):
    """Writes config settings to Firestore and local json cache."""
    import json
    db = get_db()
    if db:
        db.collection("config").document("settings").set(data)

    local_path = pathlib.Path(".local_db") / "config_settings.json"
    try:
        with open(local_path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning("Failed to persist config_settings locally: %s", e)
```

- [ ] **Step 2: Add `dump_config_settings` to `refresh_local_pkls.py`**

Add this function after `dump_prediction_features`:

```python
def dump_config_settings():
    """Pull config/settings doc → .local_db/config_settings.json."""
    log.info("  Fetching 'config/settings' from Firestore...")
    try:
        db = get_db()
        doc = db.collection("config").document("settings").get()
        data = doc.to_dict() if doc.exists else {"draft_active": False}
        out_path = LOCAL_DB / "config_settings.json"
        import json
        with open(out_path, "w") as f:
            json.dump(data, f)
        log.info(f"    ✓ → {out_path.name}")
    except Exception as e:
        log.error(f"    ✗ Failed 'config/settings': {e}")
```

Then in `main()`, add a call after `dump_prediction_features()`:

```python
    log.info("\n-- App config --")
    dump_config_settings()
```

- [ ] **Step 3: Smoke test locally**

```bash
python -c "from services.db_service import get_config_settings; print(get_config_settings())"
```

Expected output: `{'draft_active': False}`

- [ ] **Step 4: Commit**

```bash
git add services/db_service.py scripts/refresh_local_pkls.py
git commit -m "feat: add get/set_config_settings to db_service and refresh_local_pkls"
```

---

## Task 2: API routes — config endpoints

**Files:**
- Modify: `routes/api_routes.py`
- Create: `tests/test_config_api.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_get_config_settings_public():
    """GET /api/config/settings is public — no auth required."""
    response = client.get("/api/config/settings")
    assert response.status_code == 200
    data = response.json()
    assert "draft_active" in data
    assert isinstance(data["draft_active"], bool)


def test_set_config_settings_requires_admin(auth_token):
    """POST /api/admin/config/settings rejects non-admin tokens."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": True},
        headers={"Authorization": auth_token},
    )
    assert response.status_code == 403


def test_set_config_settings_as_admin(admin_token, mock_firestore):
    """POST /api/admin/config/settings succeeds for admin and returns updated value."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": True},
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data.get("ok") is True
    assert data.get("draft_active") is True


def test_set_config_settings_ignores_unknown_keys(admin_token, mock_firestore):
    """POST /api/admin/config/settings strips unknown keys."""
    response = client.post(
        "/api/admin/config/settings",
        json={"draft_active": False, "secret_flag": True},
        headers={"Authorization": admin_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "secret_flag" not in data
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_config_api.py -v
```

Expected: all 4 FAIL (404 — routes don't exist yet).

- [ ] **Step 3: Add routes to `api_routes.py`**

Add these two endpoints at the end of `routes/api_routes.py`, before the last line. Also add `require_admin` to the existing import:

First, update the import line at line 14:
```python
from services.session_service import require_auth, require_admin
```

Then append the two new endpoints:

```python
@router.get("/config/settings")
def get_config():
    """Returns app config. Public — all users need draft_active on page load."""
    from services.db_service import get_config_settings
    try:
        return JSONResponse(content=get_config_settings())
    except Exception:
        logger.exception("get_config error")
        return JSONResponse(content={"draft_active": False})


@router.post("/admin/config/settings")
async def set_config(request: Request, _auth: dict = Depends(require_admin)):
    """Updates app config. Admin only."""
    from services.db_service import set_config_settings
    try:
        body = await request.json()
        allowed = {k: v for k, v in body.items() if k in {"draft_active"}}
        set_config_settings(allowed)
        return JSONResponse(content={"ok": True, **allowed})
    except Exception:
        logger.exception("set_config error")
        return server_error()
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_config_api.py -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add routes/api_routes.py tests/test_config_api.py
git commit -m "feat: add GET /api/config/settings and POST /api/admin/config/settings"
```

---

## Task 3: HTML — base.html restructure

**Files:**
- Modify: `templates/base.html`

Replace the current nav block (lines 88–147 — from `<!-- Unified Global Navigation -->` through the closing `</aside>`) with the new structure below. Everything outside that range is unchanged.

- [ ] **Step 1: Replace the nav block in `templates/base.html`**

Find and replace this entire section:
```html
        <!-- Unified Global Navigation (Hidden until Authenticated) -->
        <div class="app-nav" id="app-nav">
```
...through...
```html
        </aside>
```

Replace with:

```html
        <!-- Desktop Nav Rail (hidden until authenticated, hidden on mobile) -->
        <div class="nav-rail" id="app-nav">
            <div class="nav-brand">
                <img src="/static/fishbone.png" alt="Logo" class="nav-logo">
                <h1>NFL Wins Pool</h1>
            </div>
            <nav class="nav-rail__links" id="nav-primary-links"></nav>
            <div class="nav-more">
                <button class="nav-more-btn" id="nav-more-btn">More <span class="nav-more-arrow">▾</span></button>
                <div class="nav-more-dropdown hidden" id="nav-more-dropdown"></div>
            </div>
            <div class="nav-avatar-wrap">
                <button class="nav-avatar" id="nav-avatar-btn" aria-label="Account menu">
                    <span id="nav-avatar-initials"></span>
                </button>
                <div class="nav-avatar-popover hidden" id="nav-avatar-popover">
                    <div class="nav-ap-name" id="nav-ap-name"></div>
                    <div class="nav-ap-role" id="nav-ap-role"></div>
                    <div class="nav-drop-divider"></div>
                    <button class="nav-ap-logout" id="nav-ap-logout-btn">Logout</button>
                </div>
            </div>
        </div>

        <!-- Mobile Header (brand only, hidden on desktop) -->
        <div class="nav-mobile-header" id="app-nav-mobile">
            <div class="nav-brand">
                <img src="/static/fishbone.png" alt="Logo" class="nav-logo">
                <h1>NFL Wins Pool</h1>
            </div>
        </div>

        <!-- Mobile Navigation Drawer (triggered by bottom tab More) -->
        <div class="nav-drawer-overlay" id="nav-drawer-overlay"></div>
        <aside class="nav-drawer" id="nav-drawer">
            <div class="nav-drawer__header">
                <img src="/static/fishbone.png" alt="Logo" class="nav-logo">
                <span class="nav-drawer__title">NFL Wins Pool</span>
                <button class="nav-drawer__close" id="drawer-close" aria-label="Close navigation"><i
                        data-lucide="x"></i></button>
            </div>
            <nav class="nav-drawer__links">
                <a href="/draft" id="drawer-live-draft-link" class="hidden"><i data-lucide="radio"></i> Live Draft</a>
                <a href="/wins-pool"><i data-lucide="trophy"></i> Standings</a>
                <a href="/schedule"><i data-lucide="calendar"></i> Schedule</a>
                <a href="/draft-results"><i data-lucide="clipboard-list"></i> Draft Results</a>
                <a href="/playoff-race"><i data-lucide="flag"></i> Playoff Race</a>
                <a href="/wins-pool/{{ current_year|default(2024) }}/weekbyweek"><i data-lucide="bar-chart-3"></i> Weekly Progress</a>
                <a href="/headtohead"><i data-lucide="users"></i> Head to Head</a>
                <a href="/draft/history"><i data-lucide="book-open"></i> Draft History</a>
                <a href="/history"><i data-lucide="crown"></i> All-Time History</a>
                <a href="/profile"><i data-lucide="user"></i> Profile</a>
                <a href="/admin" id="admin-nav-link-drawer" class="admin-link admin-hidden"><i
                        data-lucide="settings"></i> Admin Portal</a>
            </nav>
            <div class="nav-drawer__footer" id="drawer-user-identity"></div>
        </aside>

        <!-- Bottom Tab Bar (mobile only, hidden on desktop) -->
        <nav class="bottom-tab-bar" id="bottom-tab-bar">
            <a href="/wins-pool/{{ current_year|default(2024) }}" class="btb-item" data-path="/wins-pool">
                <i data-lucide="trophy"></i><span>Standings</span>
            </a>
            <a href="/schedule" class="btb-item" data-path="/schedule">
                <i data-lucide="calendar"></i><span>Schedule</span>
            </a>
            <a href="/draft-results" class="btb-item" id="btb-draft-tab" data-path="/draft-results">
                <i data-lucide="clipboard-list" id="btb-draft-icon"></i><span id="btb-draft-label">Drafts</span>
            </a>
            <a href="/playoff-race" class="btb-item" data-path="/playoff-race">
                <i data-lucide="flag"></i><span>Playoff</span>
            </a>
            <button class="btb-item" id="btb-more-tab">
                <i data-lucide="menu"></i><span>More</span>
            </button>
        </nav>
```

- [ ] **Step 2: Verify the server starts without errors**

```bash
uvicorn main:app --reload
```

Navigate to `http://localhost:8000/wins-pool/2025` and log in. The page will look broken — that's expected until CSS and JS are updated.

- [ ] **Step 3: Commit**

```bash
git add templates/base.html
git commit -m "feat: restructure nav HTML — desktop rail, mobile header, bottom tab bar"
```

---

## Task 4: CSS — new nav styles

**Files:**
- Modify: `static/style.css`

- [ ] **Step 1: Update the `.app-nav` visibility rules**

Find these lines near the top of `style.css` (around line 517):

```css
.app-nav,
```

This is part of a rule that hides the nav until authenticated. Replace the selector to cover the new elements. Find the full rule block:

```css
.app-nav,
```

and wherever `.app-nav` appears in auth-gating rules (search for `.app-nav` in `style.css`). Replace all references to `.app-nav` in visibility/auth rules with `.nav-rail, .nav-mobile-header, .bottom-tab-bar`.

Specifically, find:
```css
html:not(.show-signin) .app-nav {
```
Replace with:
```css
html:not(.show-signin) .nav-rail,
html:not(.show-signin) .nav-mobile-header,
html:not(.show-signin) .bottom-tab-bar {
```

- [ ] **Step 2: Update the old `.app-nav` base rule**

Find the `.app-nav { ... }` rule (around line 978) and replace it with:

```css
.nav-rail {
    position: sticky;
    top: 0;
    z-index: 200;
    display: none; /* shown by auth rule above */
    align-items: center;
    gap: 4px;
    padding: 8px 20px;
    background: var(--bg-elev);
    border-bottom: 1px solid var(--line);
}
```

- [ ] **Step 3: Replace `.hamburger-btn` rule**

Find `.hamburger-btn { ... }` (around line 1035) and replace the full rule with an empty tombstone comment so the selector no longer exists:

```css
/* hamburger-btn removed — replaced by bottom tab bar */
```

- [ ] **Step 4: Append all new nav styles at the end of `style.css`**

```css
/* ── Nav Redesign ──────────────────────────────────────── */

/* Desktop rail */
.nav-brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.nav-brand h1 { font-size: 15px; font-weight: 700; margin: 0; }
.nav-rail__links { display: flex; gap: 2px; flex: 1; padding: 0 8px; }
.nav-rail-link {
    padding: 6px 12px; border-radius: 7px; font-size: 13px; color: var(--ink-2);
    white-space: nowrap; text-decoration: none; display: flex; align-items: center; gap: 6px;
    transition: background 100ms, color 100ms;
}
.nav-rail-link:hover { background: rgba(255,255,255,0.05); color: var(--ink); }
.nav-rail-link.active { background: rgba(255,255,255,0.07); color: var(--ink); font-weight: 600; }
.nav-rail-link--live {
    color: var(--pos);
    background: rgba(111,191,115,0.08);
    border: 1px solid rgba(111,191,115,0.2);
}
.nav-rail-link--live:hover { background: rgba(111,191,115,0.14); }
.nav-rail-link--admin { color: var(--accent-gold); }
.nav-live-dot {
    width: 7px; height: 7px; border-radius: 50%; background: var(--pos); flex-shrink: 0;
    animation: pulse-live 1.4s ease-in-out infinite;
}
@keyframes pulse-live { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

/* More dropdown */
.nav-more { position: relative; flex-shrink: 0; }
.nav-more-btn {
    padding: 6px 12px; border-radius: 7px; font-size: 13px; color: var(--ink-2);
    background: transparent; border: 1px solid var(--line); cursor: pointer;
    display: flex; align-items: center; gap: 4px; transition: background 100ms;
}
.nav-more-btn:hover { background: rgba(255,255,255,0.05); color: var(--ink); }
.nav-more-dropdown {
    position: absolute; top: calc(100% + 6px); right: 0;
    background: var(--bg-elev-2); border: 1px solid var(--line-strong);
    border-radius: 10px; padding: 6px; min-width: 190px;
    box-shadow: 0 12px 32px rgba(0,0,0,0.5); z-index: 300;
}
.nav-more-dropdown.hidden { display: none; }
.nav-drop-item {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 12px; border-radius: 7px; font-size: 13px; color: var(--ink-2);
    text-decoration: none; cursor: pointer; transition: background 80ms;
    background: none; border: none; width: 100%; text-align: left;
}
.nav-drop-item:hover { background: rgba(255,255,255,0.05); color: var(--ink); }
.nav-drop-divider { height: 1px; background: var(--line); margin: 4px 0; }

/* Avatar */
.nav-avatar-wrap { position: relative; flex-shrink: 0; }
.nav-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    background: var(--accent-gold); border: none; cursor: pointer;
    font-size: 12px; font-weight: 700; color: #0c0e12;
    display: grid; place-items: center;
}
.nav-avatar-popover {
    position: absolute; top: calc(100% + 8px); right: 0;
    background: var(--bg-elev-2); border: 1px solid var(--line-strong);
    border-radius: 10px; padding: 12px 14px; min-width: 160px;
    box-shadow: 0 12px 32px rgba(0,0,0,0.5); z-index: 300;
}
.nav-avatar-popover.hidden { display: none; }
.nav-ap-name { font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 2px; }
.nav-ap-role { font-size: 11px; color: var(--ink-3); margin-bottom: 8px; }
.nav-ap-logout {
    width: 100%; padding: 6px 0; border-radius: 6px; font-size: 13px;
    background: rgba(255,255,255,0.06); border: 1px solid var(--line);
    color: var(--ink-2); cursor: pointer; margin-top: 4px;
    transition: background 100ms;
}
.nav-ap-logout:hover { background: rgba(255,255,255,0.1); color: var(--ink); }

/* Mobile header */
.nav-mobile-header {
    display: none;
    align-items: center;
    padding: 10px 16px;
    background: var(--bg-elev);
    border-bottom: 1px solid var(--line);
    position: sticky; top: 0; z-index: 200;
}

/* Bottom tab bar */
.bottom-tab-bar {
    display: none;
    position: fixed; bottom: 0; left: 0; right: 0; z-index: 200;
    background: var(--bg-elev-2);
    border-top: 1px solid var(--line-strong);
    padding: 6px 4px 10px;
}
.btb-item {
    flex: 1; display: flex; flex-direction: column; align-items: center; gap: 3px;
    padding: 4px; text-decoration: none; color: var(--ink-3);
    background: none; border: none; cursor: pointer; font-size: inherit;
    font-family: inherit; transition: color 100ms;
}
.btb-item svg { width: 20px; height: 20px; }
.btb-item span { font-size: 10px; }
.btb-item.active { color: var(--accent-gold); }
.btb-item.live { color: var(--pos); }

/* Auth-gate new nav elements (hidden until .show-signin is removed) */
.nav-rail { display: none; }
.nav-mobile-header { display: none; }
.bottom-tab-bar { display: none; }

html:not(.show-signin) .nav-rail { display: flex; }

/* Breakpoint: switch between desktop rail and mobile layout */
@media (max-width: 860px) {
    html:not(.show-signin) .nav-rail { display: none; }
    html:not(.show-signin) .nav-mobile-header { display: flex; }
    html:not(.show-signin) .bottom-tab-bar { display: flex; }
    /* Pad page content so bottom bar doesn't cover it */
    #content { padding-bottom: 72px; }
}
```

- [ ] **Step 5: Remove the old responsive `.app-nav` rule**

Find this rule (around line 1625):
```css
    .app-nav {
```
and the block it belongs to. Replace the `.app-nav { ... }` block inside the mobile media query with a comment:

```css
    /* .app-nav handled by new .nav-rail / .nav-mobile-header rules above */
```

- [ ] **Step 6: Verify visually**

```bash
uvicorn main:app --reload
```

Log in. Confirm the desktop rail appears at the top (empty primary links for now — JS not wired yet). Resize to mobile: confirm the bottom tab bar appears. No console errors.

- [ ] **Step 7: Commit**

```bash
git add static/style.css
git commit -m "feat: add nav rail, bottom tab bar, More dropdown, and avatar CSS"
```

---

## Task 5: JS — `updateNav`, More dropdown, avatar popover, bottom tab active state

**Files:**
- Modify: `static/js/main.js`
- Modify: `static/js/responsive.js`

- [ ] **Step 1: Fetch config in `init()` and store on `this`**

In `main.js`, find the `init()` method. After the `syncProfile` call (line ~32), add:

```javascript
        // Fetch draft_active config
        this.draftActive = false;
        try {
            const cfg = await fetch('/api/config/settings').then(r => r.json());
            this.draftActive = cfg.draft_active === true;
        } catch (e) {
            console.warn('[App] Could not load config/settings', e);
        }
```

Also add `this.draftActive = false;` in the constructor alongside the other property declarations.

- [ ] **Step 2: Add `updateNav` method**

Add this method to the `App` class, after `initGlobalUI`:

```javascript
    updateNav() {
        const { playerId, nickName, playerName, role } = this.user;
        if (!playerId) return;

        const initials = (nickName || playerName || '?').slice(0, 2).toUpperCase();
        const path = window.location.pathname;

        // ── Primary links ──
        const primaryLinks = [
            { href: `/wins-pool/${new Date().getFullYear()}`, label: 'Standings', paths: ['/wins-pool'] },
            { href: '/schedule',      label: 'Schedule',     paths: ['/schedule'] },
            this.draftActive
                ? { href: '/draft',   label: 'Live Draft',   paths: ['/draft'], live: true }
                : { href: '/draft-results', label: 'Draft Results', paths: ['/draft-results'] },
            { href: '/playoff-race',  label: 'Playoff Race', paths: ['/playoff-race'] },
        ];
        if (role === 'admin') {
            primaryLinks.push({ href: '/admin', label: 'Admin', paths: ['/admin'], admin: true });
        }

        const primaryContainer = document.getElementById('nav-primary-links');
        if (primaryContainer) {
            primaryContainer.innerHTML = primaryLinks.map(link => {
                const isActive = link.paths.some(p => path.startsWith(p));
                let cls = 'nav-rail-link';
                if (isActive) cls += ' active';
                if (link.live) cls += ' nav-rail-link--live';
                if (link.admin) cls += ' nav-rail-link--admin';
                const dot = link.live ? '<span class="nav-live-dot"></span>' : '';
                return `<a href="${link.href}" class="${cls}">${dot}${link.label}</a>`;
            }).join('');
        }

        // ── More dropdown ──
        const moreLinks = [
            { href: `/wins-pool/${new Date().getFullYear()}/weekbyweek`, label: 'Weekly Progress' },
            { href: '/headtohead', label: 'Head to Head' },
            null, // divider
        ];
        if (this.draftActive) {
            moreLinks.push({ href: '/draft-results', label: 'Draft Results' });
        }
        moreLinks.push(
            { href: '/draft/history', label: 'Draft History' },
            { href: '/history',       label: 'All-Time History' },
            null,
            { href: '/profile',       label: 'Profile' },
        );
        if (role === 'admin') {
            moreLinks.push({ href: '/admin', label: 'Admin Portal' });
        }

        const moreDropdown = document.getElementById('nav-more-dropdown');
        if (moreDropdown) {
            moreDropdown.innerHTML = moreLinks.map(link =>
                link === null
                    ? '<div class="nav-drop-divider"></div>'
                    : `<a href="${link.href}" class="nav-drop-item">${link.label}</a>`
            ).join('');
        }

        // ── Avatar ──
        const avatarBtn = document.getElementById('nav-avatar-btn');
        const avatarInitials = document.getElementById('nav-avatar-initials');
        const apName = document.getElementById('nav-ap-name');
        const apRole = document.getElementById('nav-ap-role');
        if (avatarInitials) avatarInitials.textContent = initials;
        if (apName) apName.textContent = nickName || playerName || '';
        if (apRole) apRole.textContent = role === 'admin' ? 'Admin' : 'Player';

        // ── Drawer admin link ──
        const drawerAdmin = document.getElementById('admin-nav-link-drawer');
        if (drawerAdmin) {
            if (role === 'admin') drawerAdmin.classList.remove('admin-hidden');
            else drawerAdmin.classList.add('admin-hidden');
        }

        // ── Drawer Live Draft link ──
        const drawerLiveDraft = document.getElementById('drawer-live-draft-link');
        if (drawerLiveDraft) {
            drawerLiveDraft.classList.toggle('hidden', !this.draftActive);
        }

        // ── Bottom tab active state ──
        document.querySelectorAll('.btb-item').forEach(item => {
            const tabPath = item.dataset.path;
            if (tabPath && path.startsWith(tabPath)) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        });

        // ── Bottom tab: swap Drafts ↔ Live Draft ──
        const btbDraftTab = document.getElementById('btb-draft-tab');
        const btbDraftLabel = document.getElementById('btb-draft-label');
        if (btbDraftTab && btbDraftLabel) {
            if (this.draftActive) {
                btbDraftTab.href = '/draft';
                btbDraftTab.dataset.path = '/draft';
                btbDraftTab.classList.add('live');
                btbDraftLabel.textContent = 'Live Draft';
            } else {
                btbDraftTab.href = '/draft-results';
                btbDraftTab.dataset.path = '/draft-results';
                btbDraftTab.classList.remove('live');
                btbDraftLabel.textContent = 'Drafts';
            }
        }
    }
```

- [ ] **Step 3: Wire More dropdown and avatar popover**

Add this method to the `App` class, after `updateNav`:

```javascript
    initNavInteractions() {
        // More dropdown toggle
        const moreBtn = document.getElementById('nav-more-btn');
        const moreDropdown = document.getElementById('nav-more-dropdown');
        if (moreBtn && moreDropdown) {
            moreBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                moreDropdown.classList.toggle('hidden');
                avatarPopover?.classList.add('hidden');
            });
        }

        // Avatar popover toggle
        const avatarBtn = document.getElementById('nav-avatar-btn');
        const avatarPopover = document.getElementById('nav-avatar-popover');
        if (avatarBtn && avatarPopover) {
            avatarBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                avatarPopover.classList.toggle('hidden');
                moreDropdown?.classList.add('hidden');
            });
        }

        // Close both on outside click or Escape
        document.addEventListener('click', () => {
            moreDropdown?.classList.add('hidden');
            avatarPopover?.classList.add('hidden');
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                moreDropdown?.classList.add('hidden');
                avatarPopover?.classList.add('hidden');
            }
        });

        // Avatar logout button
        const apLogout = document.getElementById('nav-ap-logout-btn');
        if (apLogout) {
            apLogout.onclick = async () => {
                await fetch('/api/logout', { method: 'POST' }).catch(() => {});
                AuthService.clearCredentials();
                window.location.reload();
            };
        }
    }
```

- [ ] **Step 4: Call `updateNav` and `initNavInteractions` from `initGlobalUI`**

In `initGlobalUI`, find the block that sets up the `user-identity` box and admin link visibility. Replace the entire body of the `if (playerId) { ... }` block with:

```javascript
        if (playerId) {
            root.classList.remove('show-signin');
            this.updateNav();
            this.initNavInteractions();

            // Show admin elements on draft page if admin
            if (role === 'admin') {
                const yearSelector = document.getElementById('admin-year-selector');
                const undoBtn = document.getElementById('undo-pick-btn');
                const resetBtn = document.getElementById('reset-pick-btn');
                if (yearSelector) yearSelector.classList.remove('hidden');
                if (undoBtn) {
                    undoBtn.style.display = 'flex';
                    undoBtn.onclick = () => this.undoPick();
                }
                if (resetBtn) {
                    resetBtn.style.display = 'flex';
                    resetBtn.onclick = () => this.resetPick();
                }
                console.log('[App] Admin UI enabled.');
            }
        } else {
```

Also remove the old logout button wiring below (it's now handled in `initNavInteractions`). Find and delete these lines from `initGlobalUI`:

```javascript
        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) logoutBtn.onclick = async () => {
            await fetch('/api/logout', { method: 'POST' }).catch(() => {});
            AuthService.clearCredentials();
            window.location.reload();
        };
```

- [ ] **Step 5: Update `responsive.js` — wire `#btb-more-tab` to open the drawer**

In `responsive.js`, find:

```javascript
        var toggle = document.getElementById('drawer-toggle');
```

Replace the entire `toggle` reference and its click handler. Find:

```javascript
        if (!toggle || !drawer || !overlay) return;

        toggle.addEventListener('click', function() {
            drawer.classList.add('open');
            overlay.classList.add('open');
```

Replace with:

```javascript
        var moreTab = document.getElementById('btb-more-tab');

        if (!moreTab || !drawer || !overlay) return;

        moreTab.addEventListener('click', function() {
            drawer.classList.add('open');
            overlay.classList.add('open');
```

- [ ] **Step 6: Smoke test**

```bash
uvicorn main:app --reload
```

Log in. Confirm:
- Desktop: primary links render (Standings, Schedule, Draft Results, Playoff Race; Admin if admin)
- Desktop: More ▾ opens dropdown, closes on outside click
- Desktop: avatar shows initials, popover shows name/role/logout
- Mobile (resize to < 860px): bottom tab bar visible, More tab opens the drawer

- [ ] **Step 7: Commit**

```bash
git add static/js/main.js static/js/responsive.js
git commit -m "feat: wire nav updateNav, More dropdown, avatar popover, bottom tab bar"
```

---

## Task 6: Admin portal — draft active toggle

**Files:**
- Modify: `templates/admin.html`
- Modify: `static/js/admin_main.js`

- [ ] **Step 1: Add toggle card to `admin.html` draft-section**

In `templates/admin.html`, find the draft-section `<div>`:

```html
    <div id="draft-section" class="tab-content card-glass hidden" style="height: auto;">
        <h2 style="border-bottom: 1px solid var(--glass-border); padding-bottom: 0.5rem;">Season & Draft Management</h2>
        <p>Generate a new season with specific players or wipe existing seasonal data.</p>

        <div class="admin-form" style="display: flex; flex-direction: column; gap: 1.5rem; margin-top: 1rem;">
```

Insert the toggle card directly after the `<div class="admin-form"...>` opening tag:

```html
            <!-- Draft Active Toggle -->
            <div id="draft-active-row" style="
                display: flex; align-items: center; justify-content: space-between;
                padding: 14px 16px; border: 1px solid var(--line-strong); border-radius: 10px;
                background: var(--bg-elev-2);">
                <div>
                    <div style="font-size: 14px; font-weight: 600; color: var(--ink);">Draft active</div>
                    <div id="draft-active-sub" style="font-size: 12px; color: var(--ink-3); margin-top: 2px;">
                        Shows Live Draft in nav for all users
                    </div>
                </div>
                <button id="draft-active-toggle" aria-pressed="false" style="
                    width: 42px; height: 24px; border-radius: 999px; border: none; cursor: pointer;
                    background: rgba(255,255,255,0.12); position: relative; transition: background 200ms;"
                    title="Toggle draft active">
                    <span style="
                        position: absolute; top: 3px; left: 3px;
                        width: 18px; height: 18px; border-radius: 50%; background: #fff;
                        transition: left 200ms; display: block;" id="draft-active-knob"></span>
                </button>
            </div>
```

- [ ] **Step 2: Wire toggle in `admin_main.js`**

In `static/js/admin_main.js`, find the initialization area (where other admin controls are wired up). Add:

```javascript
// ── Draft Active Toggle ──────────────────────────────────
async function initDraftActiveToggle() {
    const toggle = document.getElementById('draft-active-toggle');
    const knob = document.getElementById('draft-active-knob');
    const sub = document.getElementById('draft-active-sub');
    if (!toggle || !knob || !sub) return;

    function applyState(active) {
        toggle.setAttribute('aria-pressed', active ? 'true' : 'false');
        toggle.style.background = active ? 'var(--pos)' : 'rgba(255,255,255,0.12)';
        knob.style.left = active ? '21px' : '3px';
        sub.textContent = active
            ? 'Live Draft visible in nav · all users'
            : 'Shows Live Draft in nav for all users';
        sub.style.color = active ? 'var(--pos)' : 'var(--ink-3)';
    }

    // Load current state
    try {
        const cfg = await fetch('/api/config/settings').then(r => r.json());
        applyState(cfg.draft_active === true);
    } catch (e) {
        console.warn('[Admin] Could not load config/settings', e);
    }

    // Toggle on click
    toggle.addEventListener('click', async () => {
        const current = toggle.getAttribute('aria-pressed') === 'true';
        const next = !current;
        applyState(next);
        try {
            await fetch('/api/admin/config/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ draft_active: next }),
            });
        } catch (e) {
            console.error('[Admin] Failed to save draft_active', e);
            applyState(current); // revert on error
        }
    });
}

initDraftActiveToggle();
```

- [ ] **Step 3: Smoke test admin toggle**

```bash
uvicorn main:app --reload
```

Log in as admin, navigate to `/admin`, click the Draft tab. Confirm the toggle renders. Click it — confirm it turns green. Refresh the page — confirm the state persists (it reads from `/api/config/settings`). Refresh `/wins-pool` — confirm Live Draft appears in the nav primary links.

- [ ] **Step 4: Commit**

```bash
git add templates/admin.html static/js/admin_main.js
git commit -m "feat: add draft-active toggle to admin portal draft tab"
```

---

## Task 7: End-to-end smoke test

No code changes — manual verification only.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -q --ignore=tests/test_firebase_schema.py --ignore=tests/test_data_alignment.py
```

Expected: all passing.

- [ ] **Step 2: Desktop — regular season**

Start the server, log in as a non-admin user. Confirm:
- Nav rail: Standings, Schedule, Draft Results, Playoff Race, More ▾, avatar
- More ▾ dropdown: Weekly Progress, H2H, Draft History, All-Time History, Profile
- Avatar popover: nickname, role, Logout button
- Active link highlighted on the current page

- [ ] **Step 3: Desktop — admin user**

Log in as admin. Confirm:
- Admin link appears in the primary rail (gold)
- Admin Portal appears in More dropdown

- [ ] **Step 4: Activate draft mode and verify Live Draft swap**

Go to Admin → Draft tab. Toggle "Draft active" on. Navigate to any page. Confirm:
- Desktop primary: Draft Results replaced by ● Live Draft (green pill)
- More dropdown: Draft Results appears in the list
- Mobile bottom tab: Drafts tab becomes Live Draft (green)
- Drawer: Live Draft link appears at the top

Toggle off. Confirm everything reverts.

- [ ] **Step 5: Mobile**

Resize browser to < 860px. Confirm:
- Desktop rail hidden, mobile header visible (brand only, no hamburger)
- Bottom tab bar visible with 5 tabs
- Tapping More opens the drawer; X closes it
- Active tab highlighted gold

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: nav redesign complete"
```
