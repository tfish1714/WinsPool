/**
 * WinsPool Mock Draft — standalone, login-free solo draft simulator.
 * Mirrors the real draft room's layout (clock card, pick queue, teams grid,
 * running portfolio, full board toggle) minus chat and minus a real timer —
 * drives the entire pick loop client-side against /api/mock-draft/* instead
 * of a WebSocket. Deliberately independent of main.js / websocket_service.js
 * / auth_service.js.
 */

const TEAM_LOGO_OVERRIDES = { LA: 'LAR', WAS: 'WSH' };
function teamLogo(code) {
    return `https://a.espncdn.com/i/teamlogos/nfl/500/${TEAM_LOGO_OVERRIDES[code] || code}.png`;
}

function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

const BOT_PICK_DELAY_MS = 700;
const QUEUE_PAST = 3;
const QUEUE_FUTURE = 2;

// Cosmetic only — assigned per slot for the session, always shown alongside
// the slot number so it's still clear which pick is which.
const BOT_NAME_POOL = [
    'Couch Quarterback', 'Waiver Wire Wizard', 'The Armchair GM', 'Blitzkrieg Bob',
    'Sunday Scaries', 'Sleeper Pick Steve', 'Mock Draft Menace', 'Two-Minute Drill',
    'The Tailgate Legend', 'Prime Time Pete', 'Chalk Eater', 'Upset Special',
    'The Stat Nerd', 'The Homer', 'Hot Take Machine', 'Turf Monster',
    'Fantasy Phenom', 'The Underdog', 'Garbage Time Gary', 'The Analytics Guy',
];

class MockDraft {
    constructor() {
        this.setup = null;       // { pickSequence, teams, season, teamSchedules, projections? }
        this.mySlot = null;
        this.slots = [];         // distinct slot numbers, ascending
        this.rosters = {};       // slot -> [team, team, team]
        this.botNames = {};      // slot -> cosmetic name (non-human slots only)
        this.pickHistory = [];   // [{ pick, slot, team, wasWildcard }] in draft order, same length as pickSequence once done
        this.picked = new Set(); // teams already taken
        this.pickIndex = 0;      // index into pickSequence
        this.selectedTeam = null;
        this.wildcardsSoFar = 0;
        this.totalBotPicks = 0;
        this.botPicksDone = 0;
        this.boardExpanded = false;

        this.$slotSelect = document.getElementById('mock-slot-select');
        this.$slotGrid = document.getElementById('mock-slot-grid');
        this.$randomSlotBtn = document.getElementById('mock-random-slot');
        this.$seasonDisplay = document.getElementById('mock-season-display');
        this.$roundLabel = document.getElementById('mock-round-label');

        this.$board = document.getElementById('mock-draft-board');
        this.$clockCard = document.getElementById('mock-clock-card');
        this.$pickQueue = document.getElementById('mock-pick-queue');
        this.$teamGrid = document.getElementById('mock-team-grid');
        this.$selectionPreview = document.getElementById('mock-selection-preview');
        this.$selectedTeamName = document.getElementById('mock-selected-team-name');
        this.$confirmPickBtn = document.getElementById('mock-confirm-pick-btn');
        this.$portfolio = document.getElementById('mock-portfolio-content');
        this.$boardToggleBtn = document.getElementById('mock-board-toggle-btn');
        this.$fullBoardSection = document.getElementById('mock-full-board-section');
        this.$fullBoard = document.getElementById('mock-full-board');

        this.$results = document.getElementById('mock-results');
        this.$yourTeams = document.getElementById('mock-your-teams');
        this.$rankings = document.getElementById('mock-rankings');
        this.$again = document.getElementById('mock-again');
        this.$error = document.getElementById('mock-error');

        this.$randomSlotBtn.addEventListener('click', () => this.chooseSlot(null));
        this.$again.addEventListener('click', () => this.restart());
        this.$confirmPickBtn.addEventListener('click', () => this.confirmPick());
        this.$boardToggleBtn.addEventListener('click', () => this.toggleFullBoard());
    }

    async init() {
        try {
            const res = await fetch('/api/mock-draft/setup');
            if (!res.ok) throw new Error((await res.json()).error || 'Setup failed.');
            this.setup = await res.json();
            this.slots = [...new Set(this.setup.pickSequence.map(e => e.slot))].sort((a, b) => a - b);
            this.$seasonDisplay.textContent = this.setup.season;
            this.renderSlotGrid();
        } catch (err) {
            this.showError(err.message);
        }
    }

    showError(message) {
        this.$error.innerHTML = '';
        const msg = document.createElement('div');
        msg.textContent = message;
        this.$error.appendChild(msg);

        const restartBtn = document.createElement('button');
        restartBtn.type = 'button';
        restartBtn.className = 'btn-primary';
        restartBtn.textContent = 'Restart Draft';
        restartBtn.addEventListener('click', () => this.restart());
        this.$error.appendChild(restartBtn);

        this.$error.classList.remove('hidden');
    }

    renderSlotGrid() {
        this.$slotGrid.innerHTML = this.slots.map(slot => `
            <button class="mock-slot-btn" data-slot="${slot}">
                <span class="mock-slot-label">Slot</span>
                <span class="mock-slot-num">${slot}</span>
            </button>`
        ).join('');
        this.$slotGrid.querySelectorAll('.mock-slot-btn').forEach(btn => {
            btn.addEventListener('click', () => this.chooseSlot(parseInt(btn.dataset.slot, 10)));
        });
    }

    chooseSlot(slot) {
        this.mySlot = slot === null ? this.slots[Math.floor(Math.random() * this.slots.length)] : slot;
        this.totalBotPicks = this.setup.pickSequence.filter(e => e.slot !== this.mySlot).length;
        this.slots.forEach(s => { this.rosters[s] = []; });
        this.assignBotNames();

        this.$slotSelect.classList.add('hidden');
        this.$board.style.display = '';
        this.renderPortfolio();
        this.renderFullBoard();
        this.advance();
    }

    assignBotNames() {
        const pool = [...BOT_NAME_POOL];
        for (let i = pool.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [pool[i], pool[j]] = [pool[j], pool[i]];
        }
        this.botNames = {};
        let i = 0;
        this.slots.forEach(s => {
            if (s !== this.mySlot) {
                this.botNames[s] = pool[i % pool.length];
                i += 1;
            }
        });
    }

    slotLabel(slot) {
        return slot === this.mySlot ? 'You' : `${this.botNames[slot]} – Slot ${slot}`;
    }

    roundFor(pick) {
        return Math.ceil(pick / this.slots.length);
    }

    availableTeams() {
        return this.setup.teams.filter(t => !this.picked.has(t));
    }

    // Admin-only ranking key: model + consensus projected wins, added together
    // so teams strong on both signals float to the top. Missing either signal
    // just contributes 0 rather than excluding the team.
    projectionSortValue(d) {
        if (!d) return 0;
        return (d.model ? d.model.projected_wins : 0) + (d.consensus ? d.consensus.consensus_mean : 0);
    }

    async advance() {
        if (this.pickIndex >= this.setup.pickSequence.length) {
            return this.finish();
        }
        const entry = this.setup.pickSequence[this.pickIndex];
        const isYourTurn = entry.slot === this.mySlot;

        this.$roundLabel.textContent = ` / round ${this.roundFor(entry.pick)}`;
        this.renderClockCard(entry, isYourTurn);
        this.renderPickQueue();

        if (isYourTurn) {
            this.$teamGrid.classList.remove('hidden');
            this.renderHumanTurn();
        } else {
            this.$teamGrid.classList.add('hidden');
            this.$selectionPreview.classList.add('hidden');
            await this.runBotTurn(entry);
        }
    }

    renderClockCard(entry, isYourTurn) {
        const total = this.setup.pickSequence.length;
        this.$clockCard.innerHTML = `
            <div class="cc-left">
                <div class="eyebrow" style="color:var(--ink-3)">On the clock</div>
                <div class="cc-name"${isYourTurn ? ' style="color:var(--link)"' : ''}>${esc(this.slotLabel(entry.slot))}</div>
                <div class="cc-sub">Round ${this.roundFor(entry.pick)} &middot; Pick ${entry.pick} of ${total}</div>
            </div>
            <div class="cc-pick">
                <span class="numeral" style="font-size:26px;color:var(--ink-2)">${entry.pick}</span>
                <span style="color:var(--ink-3);font-size:13px">/${total}</span>
            </div>
            <div class="cc-timer">
                <div class="eyebrow" style="color:var(--ink-3)">Status</div>
                <div style="display:flex;justify-content:flex-end;margin-top:30px">
                    <span class="mono-pill">
                        <span class="dot${isYourTurn ? ' warn' : ''} pulse"></span>${isYourTurn ? 'Your pick' : 'Picking…'}
                    </span>
                </div>
            </div>`;
    }

    renderPickQueue() {
        const start = Math.max(0, this.pickIndex - QUEUE_PAST);
        const end = Math.min(this.setup.pickSequence.length - 1, this.pickIndex + QUEUE_FUTURE);
        const rows = [];
        for (let idx = start; idx <= end; idx++) {
            const entry = this.setup.pickSequence[idx];
            const done = this.pickHistory[idx];
            const isActive = idx === this.pickIndex;
            const isYou = entry.slot === this.mySlot;

            let right;
            if (done) {
                right = `
                    <div style="display:flex;align-items:center;gap:8px">
                        <img src="${teamLogo(done.team)}" alt="${done.team}" style="width:16px;height:16px;object-fit:contain;">
                        <span style="background:#444;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;font-family:'JetBrains Mono',monospace">${done.team}${done.wasWildcard ? ' 🎲' : ''}</span>
                    </div>`;
            } else if (isActive) {
                right = '<span class="mono-pill"><span class="dot pulse"></span>picking</span>';
            } else {
                right = '<span class="mono" style="color:var(--ink-3);font-size:11px">—</span>';
            }

            rows.push(`
                <div class="q-row" style="
                    opacity:${done ? 0.6 : 1};
                    background:${isActive ? 'rgba(255,255,255,0.025)' : 'transparent'};
                    border-color:${isActive ? 'var(--line-strong)' : 'var(--line)'}">
                    <span class="numeral" style="font-size:22px;color:${isActive ? 'var(--ink)' : 'var(--ink-3)'};min-width:28px">${entry.pick}</span>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:14px;font-weight:600;${isYou ? 'color:var(--link)' : ''}">${esc(this.slotLabel(entry.slot))}</div>
                        <div class="mono" style="font-size:11px;color:var(--ink-3)">R${this.roundFor(entry.pick)}&middot;P${entry.pick}</div>
                    </div>
                    ${right}
                </div>`);
        }
        this.$pickQueue.innerHTML = rows.join('');
    }

    renderPortfolio() {
        const rows = this.slots.map(slot => {
            const isYou = slot === this.mySlot;
            const teamsHtml = (this.rosters[slot] || []).map(t => `
                <span style="display:inline-block;padding:2px 6px;background:rgba(255,255,255,0.1);border-radius:4px;margin:2px;font-size:0.8rem;">
                    <img src="${teamLogo(t)}" alt="${t}" style="width:14px;height:14px;vertical-align:middle;margin-right:4px;">${t}
                </span>`).join('') || '<span style="color:var(--ink-3);">No teams yet</span>';
            return `
                <tr>
                    <td style="padding:0.5rem;border-bottom:1px solid var(--line);font-weight:bold;${isYou ? 'color:var(--link);' : ''}">${esc(this.slotLabel(slot))}</td>
                    <td style="padding:0.5rem;border-bottom:1px solid var(--line);">${teamsHtml}</td>
                </tr>`;
        }).join('');
        this.$portfolio.innerHTML = `
            <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead><tr>
                    <th style="padding:0.5rem;border-bottom:1px solid var(--glass-border);">Player</th>
                    <th style="padding:0.5rem;border-bottom:1px solid var(--glass-border);">Teams</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    renderFullBoard() {
        this.$fullBoard.innerHTML = this.setup.pickSequence.map((entry, idx) => {
            const done = this.pickHistory[idx];
            const isActive = idx === this.pickIndex;
            const isYou = entry.slot === this.mySlot;
            return `
                <li class="draft-item${isActive ? ' active' : ''}${done ? ' completed' : ''}">
                    <div class="pick-main">
                        <span class="pick-number">#${entry.pick}</span>
                        <span class="pick-player"${isYou ? ' style="color:var(--link)"' : ''}>${esc(this.slotLabel(entry.slot))}</span>
                    </div>
                    ${done ? `
                        <div class="pick-result">
                            <img src="${teamLogo(done.team)}" class="tiny-logo" alt="">
                            <span class="pick-team">${done.team}${done.wasWildcard ? ' 🎲' : ''}</span>
                        </div>` : ''}
                </li>`;
        }).join('');
    }

    toggleFullBoard() {
        this.boardExpanded = !this.boardExpanded;
        this.$fullBoardSection.style.display = this.boardExpanded ? 'block' : 'none';
        this.$boardToggleBtn.textContent = this.boardExpanded ? 'Hide full board ▴' : 'Show full board ▾';
    }

    renderHumanTurn() {
        const detail = this.setup.projectionsDetail;
        const schedules = this.setup.teamSchedules;

        let teams = this.availableTeams();
        if (detail) {
            // Admin only: best teams left first, ranked by model + consensus combined.
            teams = [...teams].sort((a, b) => this.projectionSortValue(detail[b]) - this.projectionSortValue(detail[a]));
        } else {
            teams = [...teams].sort((a, b) => a.localeCompare(b));
        }

        this.$teamGrid.innerHTML = teams.map(team => {
            const d = detail && detail[team];
            let sub = '';
            if (d) {
                const modelLine = d.model ? `Model: ${d.model.projected_wins}W &plusmn;${d.model.std_dev}` : '';
                const consensusLine = d.consensus ? `Consensus: ${d.consensus.consensus_mean.toFixed(1)}W` : '';
                sub = [modelLine, consensusLine].filter(Boolean)
                    .map(line => `<div class="team-btn-sub">${line}</div>`).join('');
            }
            const schedule = schedules && schedules[team] ? schedules[team].join('\n') : '';
            return `
                <button class="team-btn" data-team="${team}" title="${esc(schedule)}">
                    <img src="${teamLogo(team)}" alt="${team}" style="width:32px;height:32px;object-fit:contain;">
                    <div style="flex:1;min-width:0"><div class="team-btn-city">${team}</div>${sub}</div>
                </button>`;
        }).join('');
        this.selectedTeam = null;
        this.$selectionPreview.classList.add('hidden');
        this.$teamGrid.querySelectorAll('.team-btn').forEach(btn => {
            btn.addEventListener('click', () => this.selectTeam(btn));
        });
    }

    selectTeam(btn) {
        if (this.selectedTeam === btn.dataset.team) {
            this.selectedTeam = null;
            btn.classList.remove('selected');
            this.$selectionPreview.classList.add('hidden');
            return;
        }
        this.selectedTeam = btn.dataset.team;
        this.$selectedTeamName.textContent = this.selectedTeam;
        this.$selectionPreview.classList.remove('hidden');
        this.$teamGrid.querySelectorAll('.team-btn').forEach(b => b.classList.remove('selected'));
        btn.classList.add('selected');
    }

    confirmPick() {
        if (!this.selectedTeam) return;
        this.$selectionPreview.classList.add('hidden');
        this.applyPick(this.mySlot, this.selectedTeam, false);
    }

    async runBotTurn(entry) {
        const remaining = this.totalBotPicks - this.botPicksDone;
        let team, wasWildcard;
        try {
            const res = await fetch('/api/mock-draft/pick', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    season: this.setup.season,
                    availableTeams: this.availableTeams(),
                    wildcardsSoFar: this.wildcardsSoFar,
                    botPicksRemaining: remaining,
                }),
            });
            if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.error || 'Bot pick failed.');
            }
            ({ team, wasWildcard } = await res.json());
        } catch (err) {
            return this.showError(err.message || 'Bot pick failed. Please restart the draft.');
        }
        await new Promise(resolve => setTimeout(resolve, BOT_PICK_DELAY_MS));
        if (wasWildcard) this.wildcardsSoFar += 1;
        this.botPicksDone += 1;
        this.applyPick(entry.slot, team, wasWildcard);
    }

    applyPick(slot, team, wasWildcard) {
        const entry = this.setup.pickSequence[this.pickIndex];
        this.pickHistory.push({ pick: entry.pick, slot, team, wasWildcard });
        this.rosters[slot].push(team);
        this.picked.add(team);
        this.pickIndex += 1;
        this.renderPortfolio();
        this.renderFullBoard();
        this.advance();
    }

    async finish() {
        this.$board.style.display = 'none';
        this.$results.classList.remove('hidden');

        this.$yourTeams.innerHTML = `<div class="eyebrow" style="margin-bottom:8px;">Your teams</div>` +
            this.rosters[this.mySlot].map(team =>
                `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:10px;">
                    <img src="${teamLogo(team)}" style="width:20px;height:20px;">${team}
                </span>`
            ).join('');

        try {
            const res = await fetch('/api/mock-draft/results', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ season: this.setup.season, rosters: this.rosters }),
            });
            if (!res.ok) throw new Error((await res.json()).error || 'Ranking failed.');
            const { rankings } = await res.json();
            const graded = rankings.length > 0 && rankings.every(r => r.graded);
            const heading = `<div class="eyebrow" style="margin-bottom:8px;">Standings</div>`;
            if (!graded) {
                this.$rankings.innerHTML = `${heading}<p style="color:var(--ink-2);">Rankings unavailable — no projection data for this season yet.</p>`;
            } else {
                rankings.sort((a, b) => a.rank - b.rank);
                this.$rankings.innerHTML = heading + rankings.map(r => {
                    const isYou = r.slot === this.mySlot;
                    const isFirst = r.rank === 1;
                    const totalText = 'totalProjectedWins' in r ? `<span class="mock-rank-total">${r.totalProjectedWins}W</span>` : '';
                    const teams = (this.rosters[r.slot] || []).map(t =>
                        `<img src="${teamLogo(t)}" alt="${t}" title="${t}" style="width:22px;height:22px;object-fit:contain;">`
                    ).join('');
                    return `
                        <div class="mock-rank-row${isFirst ? ' is-rank-1' : ''}${isYou ? ' is-you' : ''}">
                            <span class="mock-rank-num">#${r.rank}</span>
                            <span class="mock-rank-label">${esc(this.slotLabel(r.slot))}</span>
                            <span class="mock-rank-teams">${teams}</span>
                            ${totalText}
                        </div>`;
                }).join('');
            }
        } catch (err) {
            this.showError(err.message);
        }
    }

    restart() {
        this.$board.style.display = 'none';
        this.$results.classList.add('hidden');
        this.$error.classList.add('hidden');
        this.mySlot = null;
        this.rosters = {};
        this.botNames = {};
        this.pickHistory = [];
        this.picked = new Set();
        this.pickIndex = 0;
        this.selectedTeam = null;
        this.wildcardsSoFar = 0;
        this.botPicksDone = 0;
        this.boardExpanded = false;
        this.$fullBoardSection.style.display = 'none';
        this.$boardToggleBtn.textContent = 'Show full board ▾';
        this.$slotSelect.classList.remove('hidden');
        this.init();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new MockDraft().init();
});
