let ws;
let state = null;
let selectedTeam = null;
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
const signinStep1 = document.getElementById('signin-step-1');
const signinStep2 = document.getElementById('signin-step-2');
const playerGrid = document.getElementById('player-grid');
const codeInput = document.getElementById('verification-code');
const verifyBtn = document.getElementById('verify-btn');
const verifyError = document.getElementById('verify-error');
const cancelVerifyBtn = document.getElementById('cancel-verify-btn');

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
        } else if (message.type === 'verification_sent') {
            pendingVerificationId = message.playerId;

            // Show Step 2 UI directly
            signinStep1.style.display = 'none';
            signinStep2.classList.remove('hidden');
            signinStep2.style.display = 'block';
            codeInput.value = '';
            verifyError.classList.add('hidden');
            codeInput.focus();

        } else if (message.type === 'verified') {
            myPlayerId = message.playerId;
            localStorage.setItem('nfl_wins_my_player_id', myPlayerId);
            render();
        } else if (message.type === 'error') {
            showToast(message.message);
            verifyError.classList.remove('hidden');
            verifyError.textContent = message.message;
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
        // Attempt to re-authenticate if a player ID is stored
        const storedPlayerId = localStorage.getItem('nfl_wins_my_player_id');
        if (storedPlayerId && storedPlayerId !== "null") {
            ws.send(JSON.stringify({ action: 'reauthenticate', playerId: storedPlayerId }));
        }
    };
}

function processDraftData() {
    // Determine active player
    let activePlayer = null;
    let isComplete = false;

    if (!state.draft_ready && state.active_pick <= 30) {
        if (statusBanner) statusBanner.textContent = "Waiting for all players to join...";
        return;
    }

    // We already have active_pick
    if (state.active_pick > 30) {
        if (statusBanner) statusBanner.textContent = "Draft Complete!";
        isComplete = true;
    } else {
        const item = state.draft_board.find(x => x.pick === state.active_pick);
        if (item) {
            if (statusBanner) statusBanner.innerHTML = `Currently Drafting: <strong>${item.playerName}</strong> (Pick #${item.pick})`;
        }
    }
}

function renderBoard() {
    if (!draftList) return;
    draftList.innerHTML = '';

    // To calculate the round correctly, we need the total number of players
    const totalPlayers = state.all_players.length || 10;

    state.draft_board.forEach(item => {
        const li = document.createElement('li');
        li.className = 'draft-item';

        if (item.team) {
            li.classList.add('completed');
        } else if (item.pick === state.active_pick) {
            li.classList.add('active');
        }

        // Calculate best pick badges
        let badges = '';
        if (item.team && draftSummary) {
            const currentRound = Math.ceil(item.pick / totalPlayers);
            const isBestOverall = draftSummary.best_overall === item.team;
            const isBestInRound = draftSummary.best_by_round && draftSummary.best_by_round[currentRound] === item.team;

            if (isBestOverall) {
                badges += `<span class="best-pick-badge">🏆 BEST OVERALL WINNER</span>`;
            } else if (isBestInRound) {
                badges += `<span class="best-pick-badge">🌟 Best in Rd ${currentRound}</span>`;
            }
        }

        li.innerHTML = `
            <span class="pick-number">#${item.pick}</span>
            <span class="pick-player">${item.playerName}</span>
            ${item.team ? `<span class="pick-team">${item.team} ${badges}</span>` : ''}
        `;

        draftList.appendChild(li);

        // Auto scroll to active pick
        if (item.pick === state.active_pick) {
            li.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
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

    state.available_teams.forEach(team => {
        const div = document.createElement('div');
        div.className = 'team-card';
        if (selectedTeam === team) {
            div.classList.add('selected');
        }
        div.textContent = team;

        div.onclick = () => {
            selectedTeam = team;
            renderTeams(); // re-render to update selected class
            showSelectionPreview();
        };

        teamsGrid.appendChild(div);
    });
}

function showSelectionPreview() {
    if (selectedTeam && state.active_pick <= 30 && state.draft_ready) {
        // Also ensure it is OUR turn to pick
        const activeItem = state.draft_board.find(x => x.pick === state.active_pick);
        if (activeItem && activeItem.playerId === myPlayerId) {
            selectionPreview.classList.remove('hidden');
            selectedTeamName.textContent = selectedTeam;
            confirmBtn.disabled = false;
        } else {
            selectionPreview.classList.remove('hidden');
            selectedTeamName.textContent = `${selectedTeam} (Not Your Turn!)`;
            confirmBtn.disabled = true;
        }
    } else {
        selectionPreview.classList.add('hidden');
    }
}

function renderSignIn() {
    if (!playerGrid) return;
    playerGrid.innerHTML = '';

    // Sort logic (unchanged)
    const sortedPlayers = [...state.all_players].sort((a, b) => {
        if (a.connected && !b.connected) return 1;
        if (!a.connected && b.connected) return -1;
        return a.playerName.localeCompare(b.playerName);
    });

    sortedPlayers.forEach(p => {
        const btn = document.createElement('button');
        btn.className = 'player-btn ' + (p.connected ? 'connected' : 'available');
        btn.innerHTML = `${p.playerName} ${p.connected ? '<span class="status-indicator"></span>' : ''}`;

        if (p.connected) {
            btn.disabled = true;
        } else {
            btn.onclick = () => {
                ws.send(JSON.stringify({ action: 'request_signin', playerId: p.playerId }));
            };
        }
        playerGrid.appendChild(btn);
    });
}

function render() {
    if (!state) return;

    const contentDiv = document.getElementById('content');

    if (!myPlayerId) {
        if (signinScreen) signinScreen.style.display = 'flex';
        if (dashboardMain) dashboardMain.style.display = 'none';
        if (appNav) appNav.style.display = 'none';
        if (contentDiv) contentDiv.style.display = 'none';
        if (seasonDisplay) seasonDisplay.textContent = `(${state.season}) - Sign In`;
        renderSignIn();
        return;
    } else {
        if (signinScreen) signinScreen.style.display = 'none';
        if (appNav) appNav.style.display = 'flex';
        if (dashboardMain && window.location.pathname === '/draft') dashboardMain.style.display = 'grid';
        if (contentDiv) contentDiv.style.display = 'block';
    }

    if (seasonDisplay) seasonDisplay.textContent = `(${state.season})`;

    if (dashboardMain) {
        processDraftData();
        renderBoard();

        // Check if previously selected team was just picked remotely
        if (selectedTeam && !state.available_teams.includes(selectedTeam)) {
            selectedTeam = null;
            showSelectionPreview();
        }

        renderTeams();
    }
}

function showToast(msg) {
    if (!toastInfo) return;
    toastInfo.textContent = msg;
    toastInfo.classList.remove('hidden');
    setTimeout(() => {
        toastInfo.classList.add('hidden');
    }, 3000);
}

// Code Verification Actions
if (verifyBtn) {
    verifyBtn.addEventListener('click', () => {
        const code = codeInput.value.trim();
        if (code && pendingVerificationId) {
            if (verifyError) verifyError.classList.add('hidden');
            ws.send(JSON.stringify({
                action: 'verify_code',
                playerId: pendingVerificationId,
                code: code
            }));
        }
    });
}

if (cancelVerifyBtn) {
    cancelVerifyBtn.addEventListener('click', () => {
        pendingVerificationId = null;
        if (signinStep2) {
            signinStep2.classList.add('hidden');
            signinStep2.style.display = 'none';
        }
        if (signinStep1) signinStep1.style.display = 'block';
        if (verifyError) verifyError.classList.add('hidden');
    });
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

// Prevent Login Screen flicker before WebSocket connects
if (myPlayerId && myPlayerId !== "null") {
    if (signinScreen) signinScreen.style.display = 'none';
    if (appNav) appNav.style.display = 'flex';
    const contentDiv = document.getElementById('content');
    if (contentDiv) contentDiv.style.display = 'block';

    // Attempt default draft tab behavior if it exists
    const tabDraft = document.getElementById('tab-draft');
    if (dashboardMain && (!tabDraft || tabDraft.classList.contains('active')) && window.location.pathname === '/draft') {
        dashboardMain.style.display = 'grid';
    }
}

// Start
fetchDraftSummary().then(() => {
    initWebSocket();
});
