let ws;
let state = null;
let selectedTeam = null;
let myPlayerName = null;
let myPlayerId = localStorage.getItem('nfl_wins_my_player_id') || null; // Track local user sign in

const seasonDisplay = document.getElementById('season-display');
const statusBanner = document.getElementById('current-pick-status');
const draftList = document.getElementById('draft-list');
const teamsGrid = document.getElementById('teams-grid');
const selectionPreview = document.getElementById('selection-preview');
const selectedTeamName = document.getElementById('selected-team-name');
const confirmBtn = document.getElementById('confirm-pick-btn');
const toastInfo = document.getElementById('toast');
const dashboardMain = document.getElementById('dashboard-main');

// Sign In Elements
const signinScreen = document.getElementById('signin-screen');
const authEmail = document.getElementById('auth-email');
const authPassword = document.getElementById('auth-password');
const authConfirmPassword = document.getElementById('auth-confirm-password');
const authSubmitBtn = document.getElementById('auth-submit-btn');
const authError = document.getElementById('auth-error');
const authLoading = document.getElementById('auth-loading');
const setupRequirements = document.getElementById('setup-requirements');
const authTitle = document.getElementById('auth-title');
const authSubtitle = document.getElementById('auth-subtitle');

let isClaiming = false;

// Tabs
const appNav = document.getElementById('app-nav');

// Draft Summary State
let draftSummary = null;

async function fetchDraftSummary() {
    try {
        const res = await fetch('/api/progress/draft_summary');
        draftSummary = await res.json();
    } catch (e) {
        console.error("No summary found", e);
    }
}

function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    ws = new WebSocket(`${protocol}//${host}/ws`);

    ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        if (message.type === 'state') {
            state = message.payload;
            render();
        } else if (message.type === 'error') {
            showToast(message.message);
        }
    };

    ws.onclose = () => {
        if (statusBanner) {
            statusBanner.textContent = 'Disconnected... Reconnecting in 3s.';
            statusBanner.style.color = 'var(--accent-red)';
        }
        setTimeout(initWebSocket, 3000);
    };

    ws.onopen = () => {
        if (statusBanner) {
            statusBanner.textContent = 'Connected. Waiting for state...';
            statusBanner.style.color = '';
        }
        const storedPlayerId = localStorage.getItem('nfl_wins_my_player_id');
        if (storedPlayerId && storedPlayerId !== "null") {
            ws.send(JSON.stringify({ action: 'reauthenticate', playerId: storedPlayerId }));
        }
    };
}

let timerInterval = null;

function startTimer() {
    if (timerInterval) clearInterval(timerInterval);
    const clockEl = document.getElementById('pick-timer');
    if (!clockEl || !state || !state.pick_start_time) return;

    timerInterval = setInterval(() => {
        const now = Math.floor(Date.now() / 1000);
        const elapsed = now - state.pick_start_time;

        if (elapsed < 0) return;

        const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
        const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
        const s = (elapsed % 60).toString().padStart(2, '0');

        clockEl.textContent = `[⏱️ ${h}:${m}:${s}]`;
        clockEl.style.color = "var(--accent-gold)";
    }, 1000);
}

function processDraftData() {
    if (!state.draft_ready && state.active_pick <= 30) {
        if (statusBanner) statusBanner.innerHTML = "Waiting for all players to join...";
        return;
    }

    if (state.active_pick > 30) {
        if (statusBanner) statusBanner.innerHTML = "Draft Complete!";
    } else {
        const item = state.draft_board.find(x => x.pick === state.active_pick);
        if (item) {
            const isMe = String(item.playerId) === String(myPlayerId);
            const pName = isMe ? "Your" : `${item.playerName}'s`;
            const txt = isMe
                ? `🚨 <strong>You are ON THE CLOCK!</strong> 🚨 (Pick ${item.pick})`
                : `<strong>${pName}</strong> is on the clock (Pick ${item.pick})`;

            if (statusBanner) {
                statusBanner.innerHTML = `<span class="pulse-dot"></span>${txt} <span id="pick-timer" style="margin-left: 10px; font-family: monospace; font-size: 1.15em; font-weight: bold;"></span>`;
            }
            startTimer();
        }
    }
}

function renderBoard() {
    if (!draftList) return;
    draftList.innerHTML = '';
    const totalPlayers = state.all_players.length || 10;

    state.draft_board.forEach(item => {
        const li = document.createElement('li');
        li.className = 'draft-item';
        if (item.team) li.classList.add('completed');
        else if (item.pick === state.active_pick) li.classList.add('active');

        let badges = '';
        if (item.team && draftSummary) {
            const currentRound = Math.ceil(item.pick / totalPlayers);
            if (draftSummary.best_overall === item.team) badges += `<span class="best-pick-badge">🏆 BEST OVERALL WINNER</span>`;
            else if (draftSummary.best_by_round && draftSummary.best_by_round[currentRound] === item.team) badges += `<span class="best-pick-badge">🌟 Best in Rd ${currentRound}</span>`;
        }

        li.innerHTML = `
            <span class="pick-number">#${item.pick}</span>
            <span class="pick-player">${item.playerName}</span>
            ${item.team ? `<span class="pick-team">${item.team} ${badges}</span>` : ''}
        `;
        draftList.appendChild(li);
        if (item.pick === state.active_pick) li.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
}

function renderTeams() {
    if (!teamsGrid) return;
    teamsGrid.innerHTML = '';
    if (state.active_pick > 30) {
        teamsGrid.innerHTML = '<p style="grid-column: 1/-1; text-align: center;">Draft is over.</p>';
        return;
    }
    if (!state.draft_ready) {
        teamsGrid.innerHTML = '<p style="grid-column: 1/-1; text-align: center;">Waiting for all players...</p>';
        return;
    }

    let teamsToRender = [...state.available_teams];
    if (myPlayerName === "TFish" && state.preseason_predictions) {
        teamsToRender.sort((a, b) => (state.preseason_predictions[b] || 0) - (state.preseason_predictions[a] || 0));
    }

    teamsToRender.forEach(team => {
        const div = document.createElement('div');
        div.className = 'team-card' + (selectedTeam === team ? ' selected' : '');
        div.textContent = (myPlayerName === "TFish" && state.preseason_predictions && state.preseason_predictions[team]) ? `${team} (${state.preseason_predictions[team]}W)` : team;
        if (state.team_schedules && state.team_schedules[team]) div.title = state.team_schedules[team].join('\n');
        div.onclick = () => { selectedTeam = team; renderTeams(); showSelectionPreview(); };
        teamsGrid.appendChild(div);
    });
}

function showSelectionPreview() {
    if (selectedTeam && state.active_pick <= 30 && state.draft_ready) {
        const activeItem = state.draft_board.find(x => x.pick === state.active_pick);
        const isMe = activeItem && String(activeItem.playerId) === String(myPlayerId);
        selectionPreview.classList.remove('hidden');
        selectedTeamName.textContent = isMe ? selectedTeam : `${selectedTeam} (Not Your Turn!)`;
        confirmBtn.disabled = !isMe;
    } else {
        selectionPreview.classList.add('hidden');
    }
}

function showToast(msg) {
    if (!toastInfo) return;
    toastInfo.textContent = msg;
    toastInfo.classList.remove('hidden');
    setTimeout(() => toastInfo.classList.add('hidden'), 3000);
}

// Updated Authentication Logic
if (authEmail) {
    authEmail.addEventListener('blur', checkAccountStatus);
}

async function checkAccountStatus() {
    const email = authEmail.value.trim();
    if (!email || !email.includes('@')) return;

    authError.classList.add('hidden');
    authLoading.classList.remove('hidden');

    try {
        const res = await fetch(`/api/check_player?email=${encodeURIComponent(email)}`);
        const data = await res.json();
        authLoading.classList.add('hidden');

        if (res.status === 404) {
            authError.textContent = "Email not found in player database.";
            authError.classList.remove('hidden');
            return;
        }

        if (data.exists) {
            if (data.has_password) {
                isClaiming = false;
                authTitle.textContent = "Sign In";
                authSubtitle.textContent = `Welcome back, ${data.playerName}`;
                authConfirmPassword.style.display = 'none';
                authConfirmPassword.classList.add('hidden');
                setupRequirements.style.display = 'none';
                setupRequirements.classList.add('hidden');
                authSubmitBtn.textContent = "Log In";
            } else {
                isClaiming = true;
                authTitle.textContent = "Account Setup";
                authSubtitle.textContent = `Hello ${data.playerName}! Set a password to claim your account.`;
                authConfirmPassword.style.display = 'block';
                authConfirmPassword.classList.remove('hidden');
                setupRequirements.style.display = 'block';
                setupRequirements.classList.remove('hidden');
                authSubmitBtn.textContent = "Set Password";
            }
        }
    } catch (e) {
        authLoading.classList.add('hidden');
        console.error("Auth check failed", e);
    }
}

if (authSubmitBtn) {
    authSubmitBtn.addEventListener('click', handleAuthSubmit);
}

async function handleAuthSubmit() {
    const email = authEmail.value.trim();
    const password = authPassword.value;
    authError.classList.add('hidden');

    if (isClaiming) {
        const confirm = authConfirmPassword.value;
        const pwRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{12,}$/;
        if (!pwRegex.test(password)) {
            authError.textContent = "Password too weak. Min 12 chars, mixed case, number, symbol.";
            authError.classList.remove('hidden');
            return;
        }
        if (password !== confirm) {
            authError.textContent = "Passwords do not match.";
            authError.classList.remove('hidden');
            return;
        }
        try {
            const res = await fetch('/api/set_password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password, confirm_password: confirm })
            });
            const data = await res.json();
            if (res.ok) processSuccessfulLogin(data);
            else { authError.textContent = data.error; authError.classList.remove('hidden'); }
        } catch (e) { authError.textContent = "Network error."; authError.classList.remove('hidden'); }
    } else {
        try {
            const res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });
            const data = await res.json();
            if (res.ok) processSuccessfulLogin(data);
            else { authError.textContent = data.error; authError.classList.remove('hidden'); }
        } catch (e) { authError.textContent = "Network error."; authError.classList.remove('hidden'); }
    }
}

function render() {
    if (!state) return;
    if (myPlayerId && myPlayerId !== "null") {
        const me = state.all_players.find(p => String(p.playerId) === String(myPlayerId));
        if (me) myPlayerName = me.playerName;
    }

    const adminPanel = document.getElementById('tfish-admin-panel');
    const forceTeamSelect = document.getElementById('force-team-select');

    if (myPlayerName === "TFish") {
        if (adminPanel) { adminPanel.classList.remove('hidden'); adminPanel.style.display = 'block'; }
        if (forceTeamSelect && state.available_teams) {
            const currentVal = forceTeamSelect.value;
            forceTeamSelect.innerHTML = '<option value="">Force Pick...</option>';
            state.available_teams.forEach(team => {
                const opt = document.createElement('option');
                opt.value = team; opt.textContent = team;
                forceTeamSelect.appendChild(opt);
            });
            forceTeamSelect.value = currentVal;
        }
    } else {
        if (adminPanel) { adminPanel.classList.add('hidden'); adminPanel.style.display = 'none'; }
    }

    const contentDiv = document.getElementById('content');
    if (!myPlayerId || myPlayerId === "null") {
        document.documentElement.classList.add('show-signin');
        if (dashboardMain) dashboardMain.style.display = 'none';
        if (appNav) appNav.style.display = 'none';
        if (contentDiv) contentDiv.style.display = 'none';
        if (seasonDisplay) seasonDisplay.textContent = `(${state.season}) - Sign In`;
        return;
    } else {
        document.documentElement.classList.remove('show-signin');
        if (appNav) appNav.style.display = 'flex';
        if (contentDiv) contentDiv.style.display = 'block';
        const role = localStorage.getItem('nfl_wins_role') || 'user';
        const adminBtn = document.getElementById('admin-nav-link');
        if (adminBtn) adminBtn.style.display = (role === 'admin') ? 'inline-block' : 'none';
        if (dashboardMain && window.location.pathname === '/draft') dashboardMain.style.display = 'grid';
    }

    if (seasonDisplay) seasonDisplay.textContent = `(${state.season})`;
    if (dashboardMain) {
        processDraftData();
        renderBoard();
        if (selectedTeam && !state.available_teams.includes(selectedTeam)) { selectedTeam = null; showSelectionPreview(); }
        renderTeams();
    }
}

function processSuccessfulLogin(data) {
    myPlayerId = data.playerId;
    myPlayerName = data.playerName;
    localStorage.setItem('nfl_wins_my_player_id', myPlayerId);
    localStorage.setItem('nfl_wins_role', data.role);
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'reauthenticate', playerId: myPlayerId }));
    render();
}

if (confirmBtn) {
    confirmBtn.addEventListener('click', () => {
        if (!selectedTeam) return;

        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Confirming...';

        ws.send(JSON.stringify({
            action: 'pick',
            team: selectedTeam
        }));

        // Optimistic clear
        setTimeout(() => {
            selectedTeam = null;
            showSelectionPreview();
            confirmBtn.textContent = 'Confirm Pick';
        }, 300);
    });
}

// Prevent Login Screen flicker fallback
if (myPlayerId && myPlayerId !== "null") {
    document.documentElement.classList.remove('show-signin');
    if (appNav) appNav.style.display = 'flex';
    const contentDiv = document.getElementById('content');
    if (contentDiv) contentDiv.style.display = 'block';

    // Attempt default draft tab behavior if it exists
    const tabDraft = document.getElementById('tab-draft');
    if (dashboardMain && (!tabDraft || tabDraft.classList.contains('active')) && window.location.pathname === '/draft') {
        dashboardMain.style.display = 'grid';
    }
}

// Master Override Listeners
const tfishUndoBtn = document.getElementById('undo-pick-btn');
const tfishForceBtn = document.getElementById('force-pick-btn');
const tfishForceSelect = document.getElementById('force-team-select');

if (tfishUndoBtn) {
    tfishUndoBtn.addEventListener('click', () => {
        if (!confirm("Are you sure you want to completely erase the last pick from the database?")) return;
        ws.send(JSON.stringify({ action: "undo_pick", playerId: myPlayerId }));
    });
}

if (tfishForceBtn) {
    tfishForceBtn.addEventListener('click', () => {
        const team = tfishForceSelect ? tfishForceSelect.value : null;
        if (!team) {
            showToast("Please select a team to force pick.");
            return;
        }
        if (!confirm(`Are you sure you want to FORCE log ${team} for the current drafter?`)) return;
        ws.send(JSON.stringify({ action: "force_pick", playerId: myPlayerId, team: team }));
        if (tfishForceSelect) tfishForceSelect.value = "";
    });
}

// Start
fetchDraftSummary().then(() => {
    initWebSocket();
});
