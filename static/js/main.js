import { ApiService } from './api.js';
import { AuthService } from './auth_service.js';
import { UiRenderer } from './ui_renderer.js';
import { WebSocketService } from './websocket_service.js';

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

        // 3. Page Routing & Data Bundles
        const path = window.location.pathname;
        await this.handleDataBundles(path);

        if (path === '/draft') {
            await this.initDraftPage();
        }
    }

    /**
     * Checks if the current year/season is optimized with a Data Bundle.
     * If so, loads it into the local cache proactively.
     */
    async handleDataBundles(path) {
        try {
            const config = await ApiService.fetchDropdownConfig();
            this.bundledSeasons = config.bundled_seasons || [];

            // Extract year from path (e.g., /wins-pool/2024 or /schedule/2024)
            const match = path.match(/\/(?:wins-pool|schedule|draft-results|h2h|week-by-week|playoff-race)\/(\d{4})/);
            const currentYear = match ? parseInt(match[1]) : null;

            if (currentYear && this.bundledSeasons.includes(currentYear)) {
                console.log(`[App] Current year ${currentYear} is optimized. Loading Data Bundle...`);
                await BundleService.loadSeasonBundle(currentYear);
            }
        } catch (e) {
            console.warn('[App] Failed to initialize bundles/metadata', e);
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
                adminLink.style.display = (role === 'admin') ? 'flex' : 'none';
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
        if (logoutBtn) logoutBtn.onclick = () => { AuthService.clearCredentials(); window.location.reload(); };
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
        }
    }

    onWsOpen() {
        if (this.user.playerId) {
            this.ws.send({ action: 'reauthenticate', playerId: this.user.playerId });
        }
        this.updateStatusBanner('Connected. Waiting for state...');
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

        // Update Banner
        this.processDraftBanners(state);

        // Render Board
        UiRenderer.renderDraftBoard(draft_board, active_pick, this.user.playerId, this.draftSummary);

        // Render Teams
        UiRenderer.renderTeamGrid(available_teams, this.selectedTeam, this.user.role, preseason_predictions, team_schedules);

        // Setup Admin Overrides (Cleanup old select if it exists)
        if (this.user.role === 'admin') {
            const undoBtn = document.getElementById('undo-pick-btn');
            const resetBtn = document.getElementById('reset-pick-btn');
            if (undoBtn) undoBtn.style.display = 'flex';
            if (resetBtn) resetBtn.style.display = 'flex';
        }

        // Setup individual card clicks
        this.attachTeamCardClickHandlers();

        // Refresh icons for dynamic content
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    processDraftBanners(state) {
        const banner = document.getElementById('current-pick-status');
        if (!banner) return;

        if (!state.draft_ready && state.active_pick <= 30) {
            banner.innerHTML = "Waiting for players to join...";
            return;
        }

        if (state.active_pick > 30) {
            banner.innerHTML = "Draft Complete!";
        } else {
            const item = state.draft_board.find(x => x.pick === state.active_pick);
            if (item) {
                const isMe = String(item.playerId) === String(this.user.playerId);
                const txt = isMe ? `<strong>Your Pick!</strong> (Pick ${item.pick})` : `<strong>${item.playerName}</strong> is picking...`;
                banner.innerHTML = `<span class="pulse-dot"></span>${txt} <span id="pick-timer"></span>`;
                this.startTimer(state.pick_start_time);
            }
        }
    }

    startTimer(startTime) {
        if (this.timerInterval) clearInterval(this.timerInterval);
        const timerEl = document.getElementById('pick-timer');
        if (!timerEl || !startTime) return;

        this.timerInterval = setInterval(() => {
            const elapsed = Math.floor(Date.now() / 1000) - startTime;
            if (elapsed < 0) return;
            const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
            const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
            const s = (elapsed % 60).toString().padStart(2, '0');
            timerEl.textContent = `[${h}:${m}:${s}]`;
        }, 1000);
    }

    attachTeamCardClickHandlers() {
        const cards = document.querySelectorAll('.team-card');
        cards.forEach(card => {
            card.onclick = () => {
                this.selectedTeam = card.dataset.team;
                this.updateSelectionPreview();
                // Re-render grid to show selection
                // Note: Optimization would be to just toggle classes
                cards.forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
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
            err.textContent = text;
            err.classList.remove('hidden');
        }
    }
}

// Global App Start
window.App = new App();
window.App.init();
