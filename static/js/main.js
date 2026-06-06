import { ApiService } from './api.js';
import { AuthService } from './auth_service.js';
import { UiRenderer } from './ui_renderer.js?v=2';
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
        this.timerInterval = null;
        this.shameTimerInterval = null;

        this.ws = new WebSocketService({
            onMessage: (msg) => this.handleWsMessage(msg),
            onOpen: () => this.onWsOpen(),
            onClose: () => this.updateStatusBanner('Disconnected. Reconnecting...'),
            onError: (err) => console.error('[WS] Error', err)
        });
    }

    async init() {
        console.log('[App] Initializing modular WinsPool...');

        // 1. Sync Role/Profile if logged in
        if (this.user.playerId) {
            const profile = await AuthService.syncProfile(this.user.playerId);
            if (profile) this.user.role = profile.role;
        }

        // 2. Setup Navigation & Global UI
        this.initGlobalUI();
        if (typeof lucide !== 'undefined') lucide.createIcons();

        // 2. Auth Logic
        this.setupAuthHandlers();

        // 3. Page Routing
        const path = window.location.pathname;
        if (path === '/draft') {
            await this.initDraftPage();
        }
    }

    initGlobalUI() {
        const { playerId, playerName, nickName, role } = this.user;
        const root = document.documentElement;

        if (playerId) {
            root.classList.remove('show-signin');

            // Show the user identity box in the top right
            const identityBox = document.getElementById('user-identity');
            const nicknameEl = document.getElementById('user-nickname-display');
            if (identityBox) {
                identityBox.style.display = 'flex';
                identityBox.classList.remove('hidden');
                if (nicknameEl) nicknameEl.textContent = nickName || playerName;
            }

            const adminLink = document.getElementById('admin-nav-link');
            if (adminLink) {
                if (role === 'admin') {
                    adminLink.classList.remove('admin-hidden');
                } else {
                    adminLink.classList.add('admin-hidden');
                }
            }

            const adminLinkDrawer = document.getElementById('admin-nav-link-drawer');
            if (adminLinkDrawer) {
                if (role === 'admin') {
                    adminLinkDrawer.classList.remove('admin-hidden');
                } else {
                    adminLinkDrawer.classList.add('admin-hidden');
                }
            }

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
            root.classList.add('show-signin');
        }

        const logoutBtn = document.getElementById('logout-btn');
        if (logoutBtn) logoutBtn.onclick = async () => {
            await fetch('/api/logout', { method: 'POST' }).catch(() => {});
            AuthService.clearCredentials();
            window.location.reload();
        };
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


    // --- WebSocket Handlers ---

    handleWsMessage(msg) {
        if (msg.type === 'state') {
            this.lastDraftState = msg.payload; // Store for preview checks
            this.renderDraftState(msg.payload);
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
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules);

        // Setup individual card clicks
        this.attachTeamCardClickHandlers();

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

    attachTeamCardClickHandlers() {
        const cards = document.querySelectorAll('.team-card');
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
