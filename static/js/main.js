import { ApiService } from './api.js';
import { AuthService } from './auth_service.js';
import { UiRenderer } from './ui_renderer.js?v=3';
import { WebSocketService } from './websocket_service.js';
import { initChat, loadHistory, appendMessage } from './chat.js';

/**
 * WinsPool Main Application Module (Refactored)
 * Orchestrates all application state and interaction.
 */

class App {
    constructor() {
        this.user = AuthService.getCredentials();
        this.selectedTeam = null;
        this.draftSummary = null;
        this.shameTimerInterval = null;
        this.draftActive = localStorage.getItem('nfl_wins_draft_active') === 'true';

        this.ws = new WebSocketService({
            onMessage: (msg) => this.handleWsMessage(msg),
            onOpen: () => this.onWsOpen(),
            onClose: () => this.updateStatusBanner('Disconnected. Reconnecting...'),
            onError: (err) => console.error('[WS] Error', err)
        });
    }

    async init() {
        console.log('[App] Initializing modular WinsPool...');

        // Render nav immediately from localStorage cache — no HTTP round trips on critical path
        this.initGlobalUI();
        if (typeof lucide !== 'undefined') lucide.createIcons();

        // Auth handlers
        this.setupAuthHandlers();

        // Page routing
        const path = window.location.pathname;
        if (path === '/draft') {
            await this.initDraftPage();
        }

        // Background sync: refresh profile + config in parallel, re-render nav only if something changed
        if (this.user.playerId) {
            this._backgroundSync();
        }
    }

    async _backgroundSync() {
        try {
            const [, cfg] = await Promise.all([
                AuthService.syncProfile(),
                fetch('/api/config/settings').then(r => r.json()).catch(() => null),
            ]);

            // Re-read credentials — syncProfile may have updated name/role in localStorage
            const fresh = AuthService.getCredentials();
            let needsNavUpdate = false;

            if (fresh.role !== this.user.role ||
                fresh.nickName !== this.user.nickName ||
                fresh.playerName !== this.user.playerName) {
                this.user = fresh;
                needsNavUpdate = true;
            }

            if (cfg) {
                const freshDraftActive = cfg.draft_active === true;
                localStorage.setItem('nfl_wins_draft_active', String(freshDraftActive));
                if (freshDraftActive !== this.draftActive) {
                    this.draftActive = freshDraftActive;
                    needsNavUpdate = true;
                }
            }

            if (needsNavUpdate) {
                this.updateNav();
                if (typeof lucide !== 'undefined') lucide.createIcons();
            }
        } catch (e) {
            console.warn('[App] Background sync failed', e);
        }
    }

    initGlobalUI() {
        const { playerId, role } = this.user;
        const root = document.documentElement;

        if (playerId) {
            root.classList.remove('show-signin');
            this.updateNav();
            this.initNavInteractions();

            // Show admin elements on draft page if admin
            if (role === 'admin') {
                const yearSelector = document.getElementById('admin-year-selector');
                if (yearSelector) yearSelector.classList.remove('hidden');

                const pickQueue = document.getElementById('pick-queue');
                if (pickQueue) {
                    pickQueue.addEventListener('click', (e) => {
                        if (e.target.classList.contains('q-undo-btn')) { e.stopPropagation(); this.undoPick(); return; }
                        if (e.target.classList.contains('q-reset-btn')) { e.stopPropagation(); this.resetPick(); return; }
                        if (e.target.classList.contains('q-timer-btn')) { e.stopPropagation(); this.resetTimer(); return; }
                        const row = e.target.closest('.q-row-admin');
                        if (!row) return;
                        const wasExpanded = row.classList.contains('expanded');
                        pickQueue.querySelectorAll('.q-row-admin.expanded').forEach(r => r.classList.remove('expanded'));
                        if (!wasExpanded) row.classList.add('expanded');
                    });
                }
                console.log('[App] Admin UI enabled.');
            }
        } else {
            root.classList.add('show-signin');
        }
    }

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
                const isDraftLink = link.paths.includes('/draft') || link.paths.includes('/draft-results');
                const id = isDraftLink ? ' id="nav-draft-link"' : '';
                return `<a href="${link.href}" class="${cls}"${id}>${dot}${link.label}</a>`;
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
            { href: '/mock-draft',    label: 'Mock Draft' },
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

        // ── Drawer footer ──
        const drawerFooter = document.getElementById('drawer-user-identity');
        if (drawerFooter) {
            drawerFooter.innerHTML = '<div class="drawer-user-name"></div><button class="drawer-logout-btn" id="drawer-logout-btn">Logout</button>';
            drawerFooter.querySelector('.drawer-user-name').textContent = nickName || playerName || '';
            const drawerLogoutBtn = document.getElementById('drawer-logout-btn');
            if (drawerLogoutBtn) {
                drawerLogoutBtn.addEventListener('click', async () => {
                    await fetch('/api/logout', { method: 'POST' }).catch(() => {});
                    AuthService.clearCredentials();
                    window.location.reload();
                });
            }
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

    initNavInteractions() {
        if (this._navInteractionsWired) return;
        this._navInteractionsWired = true;
        const moreBtn = document.getElementById('nav-more-btn');
        const moreDropdown = document.getElementById('nav-more-dropdown');
        const avatarBtn = document.getElementById('nav-avatar-btn');
        const avatarPopover = document.getElementById('nav-avatar-popover');

        if (moreBtn && moreDropdown) {
            moreBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                moreDropdown.classList.toggle('hidden');
                avatarPopover?.classList.add('hidden');
            });
        }

        if (avatarBtn && avatarPopover) {
            avatarBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                avatarPopover.classList.toggle('hidden');
                moreDropdown?.classList.add('hidden');
            });
        }

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

        const apLogout = document.getElementById('nav-ap-logout-btn');
        if (apLogout) {
            apLogout.onclick = async () => {
                await fetch('/api/logout', { method: 'POST' }).catch(() => {});
                AuthService.clearCredentials();
                window.location.reload();
            };
        }
    }

    async initDraftPage() {
        // Show the dashboard container
        const dash = document.getElementById('dashboard-main');
        if (dash) dash.style.display = 'grid';

        this.ws.connect();
        this.setupDraftListeners();

        try {
            this.draftSummary = await ApiService.generateRecap('latest', 'latest');
        } catch (e) {
            console.warn('[App] Draft summary unavailable', e);
        }
    }


    // Surgically updates only the draft-related nav elements without rebuilding the full nav.
    // Used by config_changed WS messages and the admin toggle to avoid a visible nav flash.
    _patchDraftNav() {
        const active = this.draftActive;

        // Desktop rail link
        const draftLink = document.getElementById('nav-draft-link');
        if (draftLink) {
            draftLink.href = active ? '/draft' : '/draft-results';
            draftLink.innerHTML = active ? '<span class="nav-live-dot"></span>Live Draft' : 'Draft Results';
            draftLink.classList.toggle('nav-rail-link--live', active);
        }

        // Drawer live-draft link
        document.getElementById('drawer-live-draft-link')?.classList.toggle('hidden', !active);

        // Bottom tab bar
        const btbTab = document.getElementById('btb-draft-tab');
        const btbLabel = document.getElementById('btb-draft-label');
        if (btbTab && btbLabel) {
            btbTab.href = active ? '/draft' : '/draft-results';
            btbTab.dataset.path = active ? '/draft' : '/draft-results';
            btbTab.classList.toggle('live', active);
            btbLabel.textContent = active ? 'Live Draft' : 'Drafts';
        }

        // More dropdown: add/remove Draft Results entry
        const moreDropdown = document.getElementById('nav-more-dropdown');
        if (moreDropdown) {
            const existing = moreDropdown.querySelector('a[href="/draft-results"]');
            if (active && !existing) {
                const draftHistoryLink = moreDropdown.querySelector('a[href="/draft/history"]');
                const el = document.createElement('a');
                el.href = '/draft-results';
                el.className = 'nav-drop-item';
                el.textContent = 'Draft Results';
                if (draftHistoryLink) moreDropdown.insertBefore(el, draftHistoryLink);
                else moreDropdown.appendChild(el);
            } else if (!active && existing) {
                existing.remove();
            }
        }

        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    // --- WebSocket Handlers ---

    handleWsMessage(msg) {
        if (msg.type === 'state') {
            this.lastDraftState = msg.payload; // Store for preview checks
            this.renderDraftState(msg.payload);
        } else if (msg.type === 'config_changed') {
            if (typeof msg.draft_active === 'boolean' && msg.draft_active !== this.draftActive) {
                this.draftActive = msg.draft_active;
                localStorage.setItem('nfl_wins_draft_active', String(msg.draft_active));
                this._patchDraftNav();
            }
        } else if (msg.type === 'error') {
            alert(msg.message);
        } else if (msg.type === 'chat_history') {
            loadHistory(msg.messages);
        } else if (msg.type === 'chat_message') {
            appendMessage(msg.msgType, msg.playerName, msg.text, msg.timestamp);
        }
    }

    onWsOpen() {
        if (this.user.playerId) {
            this.ws.send({ action: 'reauthenticate', playerId: this.user.playerId });
        }
        this.updateStatusBanner('Connected. Waiting for state...');
        initChat(this.ws);
        this.initPushNotifications();
    }

    async initPushNotifications() {
        if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
        const vapidKey = document.querySelector('meta[name="vapid-public-key"]')?.content;
        if (!vapidKey) return;

        try {
            const reg = await navigator.serviceWorker.register('/sw.js');
            const permission = await Notification.requestPermission();
            if (permission !== 'granted') return;

            const sub = await reg.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: _urlBase64ToUint8Array(vapidKey),
            });

            const token = AuthService.getToken();
            await fetch('/api/draft/push-subscribe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
                },
                body: JSON.stringify({
                    playerId: this.user.playerId,
                    subscription: sub.toJSON(),
                }),
            });
        } catch (e) {
            console.warn('[Push] Subscription failed:', e);
        }
    }

    updateStatusBanner(text) {
        const banner = document.getElementById('current-pick-status');
        if (banner) banner.innerHTML = text;
    }

    // --- Draft Logic ---

    renderDraftState(state) {
        const { active_pick, draft_board, available_teams, draft_ready, preseason_predictions, team_schedules, season } = state;

        // Update Year Context
        const yearDisplay = document.getElementById('season-display');
        if (yearDisplay && season) yearDisplay.textContent = `(${season})`;

        // Sync Dropdown
        const dropdown = document.getElementById('season-dropdown');
        if (dropdown && state.available_seasons) {
            const current = String(season);
            // Only rebuild if options changed or empty to avoid flickering
            if (dropdown.options.length !== state.available_seasons.length) {
                dropdown.innerHTML = state.available_seasons.map(s =>
                    `<option value="${s}" ${String(s) === current ? 'selected' : ''}>${s}</option>`
                ).join('');
            } else {
                dropdown.value = current;
            }
        }

        // Shame timer + LIVE pill + round label
        this.updateShameTimer(state);

        // Render Pick Queue
        UiRenderer.renderPickQueue(draft_board, active_pick, state.all_players, this.user.role);

        // Render Board
        UiRenderer.renderDraftBoard(draft_board, active_pick, this.user.playerId, this.draftSummary, state.all_players ? state.all_players.length : 10, this.user.role, preseason_predictions);

        // Render Admin Portfolio
        if (this.user.role === 'admin') {
            const adminPanel = document.getElementById('admin-portfolio-section');
            if (adminPanel) adminPanel.style.display = 'block';
            UiRenderer.renderAdminPortfolio(draft_board, state.all_players, preseason_predictions);
        } else {
            const adminPanel = document.getElementById('admin-portfolio-section');
            if (adminPanel) adminPanel.style.display = 'none';
        }

        // Render Teams
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules, draft_board);

        // Setup individual card clicks
        this.attachTeamCardClickHandlers();

        // Wire board toggle (idempotent)
        this.initBoardToggle();

        // Refresh icons for dynamic content
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    updateShameTimer(state) {
        const card = document.getElementById('shame-timer-card');
        if (!card) return;

        if (this.shameTimerInterval) {
            clearInterval(this.shameTimerInterval);
            this.shameTimerInterval = null;
        }

        const { active_pick, draft_board, pick_start_time, all_players, connected_count } = state;

        // Update LIVE pill
        const livePill = document.getElementById('current-pick-status');
        if (livePill && connected_count != null) {
            livePill.innerHTML = `<span class="dot pulse"></span>LIVE &middot; ${connected_count} connected`;
        }

        // Update round label in h1
        const roundLabel = document.getElementById('round-label');
        if (roundLabel && all_players) {
            const totalPlayers = all_players.length || 10;
            const round = active_pick <= (draft_board.length || 30)
                ? Math.ceil(active_pick / totalPlayers)
                : Math.ceil((draft_board.length || 30) / totalPlayers);
            roundLabel.textContent = `/round ${round}`;
        }

        // Not yet ready
        if (!state.draft_ready) {
            card.style.borderColor = 'var(--line-strong)';
            card.innerHTML = `
                <div style="grid-column:1/-1;text-align:center;padding:8px 0;color:var(--ink-2);font-size:15px;">
                    Waiting for players to join…
                </div>`;
            return;
        }

        // Draft complete
        if (!draft_board || active_pick > (draft_board.length || 30)) {
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
            { at: 0,   color: 'var(--ink)',   ring: 'var(--line-strong)',     dot: 'ok',   label: 'On the clock' },
            { at: 30,  color: 'var(--ink-2)', ring: 'var(--line-strong)',     dot: 'ok',   label: 'Taking it in…' },
            { at: 75,  color: 'var(--warn)',  ring: 'rgba(217,168,108,0.40)', dot: 'warn', label: 'The pool is getting restless' },
            { at: 150, color: 'var(--neg)',   ring: 'rgba(217,112,112,0.45)', dot: 'bad',  label: 'You\'re holding up the entire draft' },
        ];
        const tierFor = (secs) => TIERS.reduce((t, x) => secs >= x.at ? x : t, TIERS[0]);
        const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;

        const _esc = (s) => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

        const render = () => {
            const elapsed = Math.max(0, Math.floor(Date.now() / 1000) - pick_start_time);
            const tier = tierFor(elapsed);
            const hot = elapsed >= 150;

            card.style.borderColor = tier.ring;
            card.innerHTML = `
                <div class="cc-left">
                    <div class="eyebrow" style="color:var(--ink-3)">On the clock</div>
                    <div class="cc-name">${_esc(item.playerName)}</div>
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

    attachTeamCardClickHandlers() {
        const cards = document.querySelectorAll('.team-btn');
        cards.forEach(card => {
            card.onclick = () => {
                if (this.selectedTeam === card.dataset.team) {
                    this.selectedTeam = null;
                    card.classList.remove('selected');
                    document.getElementById('selection-preview')?.classList.add('hidden');
                } else {
                    this.selectedTeam = card.dataset.team;
                    this.updateSelectionPreview();
                    cards.forEach(c => c.classList.remove('selected'));
                    card.classList.add('selected');
                }
            };
        });
    }

    updateSelectionPreview() {
        const preview = document.getElementById('selection-preview');
        const teamName = document.getElementById('selected-team-name');
        const confirmBtn = document.getElementById('confirm-pick-btn');

        if (this.selectedTeam && preview) {
            preview.classList.remove('hidden');

            // Allow admin to confirm for anyone
            const activeItem = this.lastDraftState?.draft_board?.find(x => x.pick === this.lastDraftState.active_pick);
            const isMe = activeItem && String(activeItem.playerId) === String(this.user.playerId);
            const isAdmin = this.user.role === 'admin';

            if (isMe) {
                teamName.textContent = this.selectedTeam;
                confirmBtn.disabled = false;
            } else if (isAdmin) {
                teamName.textContent = `${this.selectedTeam} (ADMIN OVERRIDE)`;
                confirmBtn.disabled = false;
            } else {
                teamName.textContent = `${this.selectedTeam} (Not Your Turn!)`;
                confirmBtn.disabled = true;
            }
        }
    }

    undoPick() {
        if (!confirm('Permanently undo the last pick? (Timer will NOT be reset)')) return;

        let pick = this.lastDraftState?.active_pick;
        if (!pick || pick <= 1) return;
        if (pick > 30) pick = 30;
        else pick = pick - 1;

        this.ws.send({
            action: 'undo_pick',
            playerId: this.user.playerId,
            pick: pick
        });
    }

    resetPick() {
        if (!confirm('Undo the last pick and RESTART the timer?')) return;

        let pick = this.lastDraftState?.active_pick;
        if (!pick || pick <= 1) return;
        if (pick > 30) pick = 30;
        else pick = pick - 1;

        this.ws.send({
            action: 'reset_pick',
            playerId: this.user.playerId,
            pick: pick
        });
    }

    resetTimer() {
        if (!confirm('Reset the timer for the current picker? (Pick is NOT undone)')) return;

        const pick = this.lastDraftState?.active_pick;
        if (!pick) return;

        this.ws.send({
            action: 'reset_pick',
            playerId: this.user.playerId,
            pick: pick
        });
    }

    setupDraftListeners() {
        const confirmBtn = document.getElementById('confirm-pick-btn');
        if (confirmBtn) {
            confirmBtn.onclick = () => {
                if (!this.selectedTeam) return;
                this.ws.send({ action: 'pick', playerId: this.user.playerId, team: this.selectedTeam });
                this.selectedTeam = null;
                document.getElementById('selection-preview')?.classList.add('hidden');
            };
        }

        // Season Switcher
        const seasonDropdown = document.getElementById('season-dropdown');
        if (seasonDropdown) {
            seasonDropdown.onchange = () => {
                const year = seasonDropdown.value;
                this.ws.send({ action: 'switch_season', year: year });
                this.selectedTeam = null;
                document.getElementById('selection-preview')?.classList.add('hidden');
            };
        }

        // Admin Master Controls - handled in initGlobalUI for reset-pick-btn
    }

    // --- Auth Logic ---

    setupAuthHandlers() {
        const emailInput = document.getElementById('auth-email');
        if (emailInput) emailInput.onblur = () => this.handleEmailBlur(emailInput.value);

        const authBtn = document.getElementById('auth-submit-btn');
        if (authBtn) authBtn.onclick = () => this.handleLogin();
    }

    async handleEmailBlur(email) {
        if (!email.includes('@')) return;
        const data = await AuthService.checkAccount(email);
        if (data.exists) {
            document.getElementById('auth-title').textContent = data.has_password ? "Sign In" : "Setup Account";
            document.getElementById('auth-subtitle').textContent = `Welcome, ${data.playerName}`;
            if (data.has_password) {
                document.getElementById('auth-confirm-password').classList.add('hidden');
                document.getElementById('setup-requirements').classList.add('hidden');
                document.getElementById('auth-submit-btn').textContent = "Log In";
            } else {
                document.getElementById('auth-confirm-password').classList.remove('hidden');
                document.getElementById('auth-confirm-password').style.display = 'block';
                document.getElementById('setup-requirements').classList.remove('hidden');
                document.getElementById('setup-requirements').style.display = 'block';
                document.getElementById('auth-submit-btn').textContent = "Setup Account";
            }
        }
    }

    async handleLogin() {
        const email = document.getElementById('auth-email').value;
        const pass = document.getElementById('auth-password').value;
        const confirmPass = document.getElementById('auth-confirm-password').value;
        const btn = document.getElementById('auth-submit-btn');

        try {
            let data;
            if (btn.textContent === "Setup Account") {
                const resp = await fetch('/api/set_password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password: pass, confirm_password: confirmPass })
                });
                data = await resp.json();
                if (resp.status !== 200) throw new Error(data.error || 'Setup failed');
                data.status = 'success'; // Treat successful setup as immediate login
            } else {
                data = await AuthService.login(email, pass);
            }

            if (data.status === 'success') {
                AuthService.setCredentials(data);
                window.location.reload();
            } else if (data.status === 'mfa_required') {
                this.handleMfaRequired(data.playerId);
            } else {
                this.showAuthError(data.error || 'Login failed');
            }
        } catch (e) {
            console.error('[App] Login failed', e);
            this.showAuthError('Connection error. Try again.');
        }
    }

    handleMfaRequired(playerId) {
        this.mfaPlayerId = playerId;
        document.getElementById('auth-title').textContent = "Verify 2FA";
        document.getElementById('auth-subtitle').textContent = "Enter the 6-digit code sent to your email";
        document.getElementById('auth-password').classList.add('hidden');
        document.getElementById('auth-mfa-code').classList.remove('hidden');
        document.getElementById('auth-mfa-code').style.display = 'block';

        const btn = document.getElementById('auth-submit-btn');
        btn.textContent = "Verify Code";
        btn.onclick = () => this.handleMfaVerify();
    }

    async handleMfaVerify() {
        const code = document.getElementById('auth-mfa-code').value;
        if (code.length !== 6) return this.showAuthError('Enter 6 digits');

        try {
            const data = await AuthService.verifyMfa(this.mfaPlayerId, code);
            if (data.status === 'success') {
                AuthService.setCredentials(data);
                window.location.reload();
            } else {
                this.showAuthError(data.error || 'Invalid code');
            }
        } catch (e) {
            this.showAuthError('MFA Verification failed');
        }
    }

    showAuthError(text) {
        const err = document.getElementById('auth-error');
        if (err) {
            err.innerHTML = `<div class="err-banner__dot"></div><div style="font-size:13px;color:var(--ink);">${text}</div>`;
            err.style.display = 'grid';
            err.classList.remove('hidden');
        }
    }
}

// Global App Start
window.App = new App();

// ── Sortable tables ────────────────────────────────────────────
function _parseSortVal(text) {
    // "2-1 (4-3)" → sort by total wins (first number in parens)
    const parens = text.match(/\((\d+)-/);
    if (parens) return parseInt(parens[1], 10);
    // "2-1" or plain number → first integer
    const num = text.match(/^(\d+)/);
    if (num) return parseInt(num[1], 10);
    return text.toLowerCase();
}

document.querySelectorAll('table.wp-data-table').forEach(table => {
    const headers = table.querySelectorAll('thead th');
    headers.forEach((th, colIdx) => {
        th.addEventListener('click', () => {
            const asc = th.dataset.sortDir !== 'asc';
            headers.forEach(h => {
                h.dataset.sortDir = '';
                h.classList.remove('sort-asc', 'sort-desc');
            });
            th.dataset.sortDir = asc ? 'asc' : 'desc';
            th.classList.add(asc ? 'sort-asc' : 'sort-desc');

            const tbody = table.querySelector('tbody');
            Array.from(tbody.querySelectorAll('tr'))
                .sort((a, b) => {
                    const cells = (r) => r.querySelectorAll('th, td');
                    const aVal = _parseSortVal((cells(a)[colIdx]?.textContent ?? '').trim());
                    const bVal = _parseSortVal((cells(b)[colIdx]?.textContent ?? '').trim());
                    const cmp = typeof aVal === 'number' && typeof bVal === 'number'
                        ? aVal - bVal
                        : String(aVal).localeCompare(String(bVal));
                    return asc ? cmp : -cmp;
                })
                .forEach(row => tbody.appendChild(row));
        });
    });
});
window.App.init();

function _urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
